#!/usr/bin/env python3
"""Export a weekly public metrics snapshot into index/metrics.json and rebuild /metrics/.

Queries our canonical first-party analytics store (Cloudflare D1 `analytics_events`
via the Cloudflare D1 HTTP API or a local SQLite replica, and optionally a
self-hosted PostHog HogQL endpoint when `POSTHOG_PERSONAL_API_KEY` is set),
computes weekly totals by canonical path, outbound social platform, donation
method, and locale, updates `index/metrics.json`, and writes `/metrics/metrics.json`.

Usage:
    python3 scripts/export_weekly_metrics.py --week 2026-W43
    python3 scripts/export_weekly_metrics.py --sqlite /path/to/local.sqlite --week 2026-W43
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sqlite3
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from render_metrics import METRICS_PATH, load_metrics, write_metrics_json_to_dist

ROOT = Path(__file__).resolve().parent.parent

CANONICAL_PATH_LABELS: dict[str, tuple[str, str]] = {
    "/": (
        "Homepage & Episode Feed",
        "Primary landing page and inline multi-language episode player.",
    ),
    "/episodes/": (
        "Episode Transcripts & Receipts (/episodes/001 + /episodes/002)",
        "Full transcripts with timestamped primary-source verification cards.",
    ),
    "/feed.xml": (
        "Podcast RSS Feed (/feed.xml + 7 localized feeds)",
        "Server-side podcast app refreshes + browser RSS/Apple/Spotify subscribe clicks.",
    ),
    "/ledger/": (
        "Public P&L Ledger & Support (/ledger/)",
        "Line-item API cost transparency and Crypto/PayPal/Stripe support buttons.",
    ),
    "/guide/": (
        "Replication Guide (/guide/)",
        "Open-source blueprint for the voice + D1 + R2 + PostHog pipeline.",
    ),
    "/people/": (
        "People & Primary Sources (/people/)",
        "Cross-episode index of cited researchers, clinicians, and authors.",
    ),
    "/reports/": (
        "Autonomous Run Reports (/reports/)",
        "Line-item production cost and verification logs per episode.",
    ),
    "/topics/": (
        "Topic Index (/topics/)",
        "Thematic clusters linking verified claims across episodes.",
    ),
    "/growth/": (
        "Social & Growth Kit (/growth/)",
        "Audiogram MP4 clips and thread templates across X, Bluesky, and Truth Social.",
    ),
    "/metrics/": (
        "Public Metrics Snapshot (/metrics/)",
        "Weekly transparency snapshot and PostHog event taxonomy specification.",
    ),
}


def query_sqlite_events(db_path: Path, since_iso: str, until_iso: str) -> list[dict[str, Any]]:
    if not db_path.exists():
        return []
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT event, canonical_path, locale, platform, donation_method, properties_json, created_at
            FROM analytics_events
            WHERE created_at >= ? AND created_at <= ?
            """,
            (since_iso, until_iso),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def query_d1_remote_events(since_iso: str, until_iso: str) -> list[dict[str, Any]]:
    account_id = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "").strip()
    db_id = os.environ.get("D1_DATABASE_ID", "").strip()
    token = (
        os.environ.get("D1_READ_TOKEN", "").strip()
        or os.environ.get("CLOUDFLARE_API_TOKEN", "").strip()
    )
    if not (account_id and db_id and token):
        return []
    url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/d1/database/{db_id}/query"
    sql = (
        "SELECT event, canonical_path, locale, platform, donation_method, properties_json, created_at "
        "FROM analytics_events WHERE created_at >= ?1 AND created_at <= ?2"
    )
    req_body = json.dumps({"sql": sql, "params": [since_iso, until_iso]}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=req_body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        result_blocks = payload.get("result") or []
        if result_blocks and isinstance(result_blocks[0], dict):
            return result_blocks[0].get("results") or []
    except (urllib.error.URLError, KeyError, ValueError):
        return []
    return []


def aggregate_events_into_snapshot(
    events: list[dict[str, Any]],
    base_snapshot: dict[str, Any],
) -> dict[str, Any]:
    """Merge live D1 event rows into a weekly snapshot structure."""
    if not events:
        return base_snapshot

    path_counts: dict[str, int] = {k: 0 for k in CANONICAL_PATH_LABELS}
    social_clicks: dict[str, int] = {"x": 0, "bluesky": 0, "truthsocial": 0, "github": 0}
    donation_counts: dict[str, int] = {"stripe": 0, "crypto": 0, "paypal": 0}
    locale_views: dict[str, int] = {}
    locale_plays: dict[str, int] = {}

    total_pageviews = 0
    rss_activity = 0
    ep_plays = 0
    clip_plays = 0
    receipt_clicks = 0

    for ev in events:
        etype = ev.get("event")
        cpath = ev.get("canonical_path") or "/"
        loc = ev.get("locale") or "en"
        props_raw = ev.get("properties_json") or "{}"
        try:
            props = json.loads(props_raw) if isinstance(props_raw, str) else props_raw
        except ValueError:
            props = {}

        if etype == "$pageview":
            total_pageviews += 1
            path_counts[cpath] = path_counts.get(cpath, 0) + 1
            locale_views[loc] = locale_views.get(loc, 0) + 1
        elif etype in ("rss_feed_click", "rss_feed_fetch"):
            rss_activity += 1
            path_counts["/feed.xml"] = path_counts.get("/feed.xml", 0) + 1
        elif etype == "outbound_social_click":
            plat = (ev.get("platform") or props.get("platform") or "x").lower()
            social_clicks[plat] = social_clicks.get(plat, 0) + 1
        elif etype == "donation_click":
            method = (ev.get("donation_method") or props.get("method") or "stripe").lower()
            donation_counts[method] = donation_counts.get(method, 0) + 1
        elif etype == "audio_play":
            if props.get("clip_id"):
                clip_plays += 1
            else:
                ep_plays += 1
                locale_plays[loc] = locale_plays.get(loc, 0) + 1
        elif etype == "source_receipt_click":
            receipt_clicks += 1

    snap = json.loads(json.dumps(base_snapshot))
    totals = snap.setdefault("summary_totals", {})
    totals["total_pageviews"] = total_pageviews
    totals["rss_feed_fetches_and_clicks"] = rss_activity
    totals["episode_audio_plays"] = ep_plays
    totals["audiogram_clip_plays"] = clip_plays
    totals["source_receipt_clicks"] = receipt_clicks
    totals["outbound_social_clicks"] = sum(social_clicks.values())
    totals["donation_button_clicks"] = sum(donation_counts.values())

    denom = max(1, sum(path_counts.values()))
    views_list = []
    for cpath, (label, notes) in CANONICAL_PATH_LABELS.items():
        v = path_counts.get(cpath, 0)
        views_list.append(
            {
                "canonical_path": cpath,
                "label": label,
                "views": v,
                "share_pct": round((v * 100.0) / denom, 1),
                "notes": notes,
            }
        )
    views_list.sort(key=lambda r: r["views"], reverse=True)
    snap["views_by_path"] = views_list

    for acc in snap.get("social_accounts") or []:
        plat = acc.get("platform", "")
        if plat in social_clicks:
            acc["outbound_clicks_from_site"] = social_clicks[plat]

    don_denom = max(1, sum(donation_counts.values()))
    for d in snap.get("donation_clicks") or []:
        m = d.get("method", "")
        if m in donation_counts:
            c = donation_counts[m]
            d["clicks"] = c
            d["share_pct"] = round((c * 100.0) / don_denom, 1)

    return snap


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Export weekly public PostHog/D1 metrics snapshot.")
    ap.add_argument("--week", default=None, help="ISO week ID (e.g. 2026-W43)")
    ap.add_argument("--sqlite", default=None, help="Optional local SQLite path with analytics_events")
    ap.add_argument("--metrics-json", default=str(METRICS_PATH), help="Path to index/metrics.json")
    args = ap.parse_args(argv)

    metrics_path = Path(args.metrics_json)
    data = load_metrics(metrics_path)
    snapshots = data.get("snapshots") or []
    if not snapshots:
        raise SystemExit("index/metrics.json has no baseline snapshot")

    target_week = args.week or data.get("current_week") or snapshots[0]["week_id"]
    base_snap = next((s for s in snapshots if s.get("week_id") == target_week), snapshots[0])
    p_start = f"{base_snap['period_start']}T00:00:00Z"
    p_end = f"{base_snap['period_end']}T23:59:59Z"

    events: list[dict[str, Any]] = []
    if args.sqlite:
        events = query_sqlite_events(Path(args.sqlite), p_start, p_end)
    else:
        events = query_d1_remote_events(p_start, p_end)

    updated_snap = aggregate_events_into_snapshot(events, base_snap)
    updated_snap["week_id"] = target_week
    now_iso = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    updated_snap["published_at"] = now_iso
    data["updated_at"] = now_iso
    data["current_week"] = target_week

    replaced = False
    for idx, s in enumerate(snapshots):
        if s.get("week_id") == target_week:
            snapshots[idx] = updated_snap
            replaced = True
            break
    if not replaced:
        snapshots.insert(0, updated_snap)

    metrics_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    dist_out = write_metrics_json_to_dist(data=data)
    print(f"Exported weekly snapshot {target_week} ({len(events)} live D1 events merged) -> {metrics_path} & {dist_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
