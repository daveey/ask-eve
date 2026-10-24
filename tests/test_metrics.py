import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import build_site as B
import export_weekly_metrics as EWM
import render_metrics as RM
import sqlite_apply


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("metrics_site")
    db = tmp / "d.sqlite"
    sqls = sorted(ROOT.glob("artifacts/*/d1_upsert.sql"))
    for sql in sqls:
        sqlite_apply.apply(db, ROOT / "migrations", sql)
    out = tmp / "dist"
    assert B.main(["--db", str(db), "--out", str(out), "--include-drafts"]) == 0
    return out


def test_metrics_json_schema_and_taxonomy():
    data = RM.load_metrics()
    assert data["schema_version"] == "1.0.0"
    assert "PostHog" in data["analytics_stack"]["engine"]
    assert data["analytics_stack"]["cookieless"] is True
    assert data["analytics_stack"]["privacy_guarantees"]["ip_addresses_stored"] is False

    events = {row["event"]: row for row in data["event_taxonomy"]}
    for required in (
        "$pageview",
        "outbound_social_click",
        "donation_click",
        "rss_feed_click",
        "rss_feed_fetch",
        "audio_play",
        "source_receipt_click",
    ):
        assert required in events, f"Missing required event in taxonomy: {required}"

    snap = data["snapshots"][0]
    by_path = {row["canonical_path"]: row for row in snap["views_by_path"]}
    for required_path in ("/", "/ledger/", "/reports/", "/guide/", "/episodes/", "/feed.xml", "/metrics/"):
        assert required_path in by_path, f"Missing required path in snapshot: {required_path}"

    platforms = {row["platform"] for row in snap["social_accounts"]}
    assert {"x", "bluesky", "truthsocial", "github"} <= platforms

    methods = {row["method"] for row in snap["donation_clicks"]}
    assert {"crypto", "paypal", "stripe"} <= methods


def test_analytics_snippet_wired_across_site(built):
    assets = list((built / "assets").glob("analytics.*.js"))
    assert len(assets) == 1, "Hashed analytics.<hash>.js asset should be emitted"

    for rel in (
        "index.html",
        "episodes/index.html",
        "ledger/index.html",
        "reports/index.html",
        "guide/index.html",
        "metrics/index.html",
        "growth/index.html",
        "ru/metrics/index.html",
        "zh/metrics/index.html",
    ):
        page = (built / rel).read_text(encoding="utf-8")
        assert 'id="posthog-config"' in page, f"Missing #posthog-config on {rel}"
        assert assets[0].name in page, f"Missing analytics.js script on {rel}"
        assert 'data-social-platform="x"' in page
        assert 'data-social-platform="bluesky"' in page
        assert 'data-social-platform="truthsocial"' in page


def test_ledger_donation_buttons_tagged(built):
    ledger_html = (built / "ledger/index.html").read_text(encoding="utf-8")
    assert 'data-donation-method="crypto"' in ledger_html
    assert 'data-donation-method="paypal"' in ledger_html
    assert 'data-donation-method="stripe"' in ledger_html


def test_metrics_page_and_json_published(built):
    html_text = (built / "metrics/index.html").read_text(encoding="utf-8")
    assert "Public Metrics Snapshot" in html_text
    assert "Page Views by Canonical Path" in html_text
    assert "Social Distribution" in html_text
    assert "What Worked" in html_text
    assert "Event Taxonomy" in html_text
    assert "/metrics/metrics.json" in html_text

    raw_json = json.loads((built / "metrics/metrics.json").read_text(encoding="utf-8"))
    assert raw_json["current_week"] == "2026-W43"


def test_export_weekly_metrics_sqlite_aggregation(tmp_path):
    db_path = tmp_path / "analytics.sqlite"
    conn = sqlite3.connect(str(db_path))
    schema = (ROOT / "migrations/0004_analytics_events.sql").read_text(encoding="utf-8")
    conn.executescript(schema)

    sample_rows = [
        ("ev1", "$pageview", "v1", "/", "/", "en", "home", None, None, None, None, "x.com", "{}", "2026-10-20T12:00:00Z"),
        ("ev2", "$pageview", "v2", "/ledger/", "/ledger/", "en", "ledger", None, None, None, None, "direct", "{}", "2026-10-20T12:01:00Z"),
        ("ev3", "rss_feed_fetch", "v3", "/feed.xml", "/feed.xml", "en", "rss", None, None, None, "/feed.xml", "AppleCoreMedia", "{}", "2026-10-20T12:02:00Z"),
        ("ev4", "outbound_social_click", "v1", "/", "/", "en", "home", None, "x", None, "https://x.com/askevepodcast", "direct", "{}", "2026-10-20T12:03:00Z"),
        ("ev5", "donation_click", "v2", "/ledger/", "/ledger/", "en", "ledger", None, None, "crypto", "copy:eth", "direct", "{}", "2026-10-20T12:04:00Z"),
        ("ev6", "audio_play", "v1", "/episodes/", "/episodes/", "en", "episodes", "001-pilot", None, None, None, "direct", '{"clip_id":""}', "2026-10-20T12:05:00Z"),
    ]
    conn.executemany(
        """INSERT INTO analytics_events (
             id, event, distinct_id, canonical_path, raw_path, locale, section,
             episode_slug, platform, donation_method, target_url, referrer_domain,
             properties_json, created_at
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        sample_rows,
    )
    conn.commit()
    conn.close()

    rows = EWM.query_sqlite_events(db_path, "2026-10-18T00:00:00Z", "2026-10-24T23:59:59Z")
    assert len(rows) == 6
    base_snap = RM.load_metrics()["snapshots"][0]
    snap = EWM.aggregate_events_into_snapshot(rows, base_snap)
    assert snap["summary_totals"]["total_pageviews"] == 2
    assert snap["summary_totals"]["rss_feed_fetches_and_clicks"] == 1
    assert snap["summary_totals"]["outbound_social_clicks"] == 1
    assert snap["summary_totals"]["donation_button_clicks"] == 1
    assert snap["summary_totals"]["episode_audio_plays"] == 1
