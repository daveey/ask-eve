"""Tests for the Three-Layer Public Transparency Architecture on askevepodcast.com:
- Layer 1: Raw Transcripts (/transcripts/, /transcripts/<slug>/, /transcripts/redaction-log.json)
- Layer 2: Technical Changelog (/changelog/, /changelog/feed.xml, /changelog/changelog.json)
- Layer 3: First-Person Growth Log (/growth/#growth-log, /growth/feed.xml, /growth/growth-log.json)
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import leak_check as LC
import redact_transcripts as RT
import render_changelog as RC
import site_i18n as I

DIST = ROOT / "site/dist"


def test_redaction_pass_and_leak_check():
    """Every published conversation turn passes both redact_transcripts and leak_check."""
    summary = RT.verify_redacted_conversations()
    assert summary["passed"] is True
    assert summary["conversations"] == 6
    assert summary["total_turns"] >= 600
    assert summary["redaction_entries"] == 14
    assert summary["total_items_redacted"] >= 50

    rlog = RT.load_redaction_log()
    cats = {e["category"] for e in rlog["entries"]}
    assert cats == {
        "personal_finance",
        "family_personal",
        "health_medical",
        "credentials_pii",
        "unrelated_private_ops",
    }
    for e in rlog["entries"]:
        for req in ("id", "date", "conversation_slug", "turn_range", "category", "items_redacted_count", "reason"):
            assert req in e and e[req], f"Missing {req} in {e}"
        assert LC.scan_text(e["reason"], field_name=f"redaction:{e['id']}") == []


def test_layer1_transcripts_index_and_conversations():
    """Layer 1 (/transcripts/ and /transcripts/<slug>/) renders all 6 dated sessions, redaction log, and upward links."""
    idx_html = (DIST / "transcripts/index.html").read_text(encoding="utf-8")
    assert 'class="transparency-bar i18n-ready"' in idx_html
    assert 'id="redaction-log"' in idx_html
    assert 'href="/transcripts/redaction-log.json"' in idx_html

    rlog_dist = json.loads((DIST / "transcripts/redaction-log.json").read_text(encoding="utf-8"))
    assert len(rlog_dist["entries"]) == 14

    expected_slugs = [
        "2026-10-23-podcast-planning",
        "2026-10-23-the-making-of-eve",
        "2026-10-23-iran-hormuz-polymarket",
        "2026-10-24-starshield-iran-anthropic",
        "2026-10-24-california-hsr-prop-40",
        "2026-10-23-to-2026-10-24-site-and-platform-build",
    ]
    for slug in expected_slugs:
        assert f'href="/transcripts/{slug}/"' in idx_html
        conv_page = DIST / "transcripts" / slug / "index.html"
        assert conv_page.exists(), f"Missing transcript page {conv_page}"
        chtml = conv_page.read_text(encoding="utf-8")
        assert 'class="transparency-bar i18n-ready"' in chtml
        assert 'id="turn-1"' in chtml
        assert "/changelog/#" in chtml
        assert "/growth/#" in chtml
        cleaned = re.sub(r"\b[0-9a-f]{40}\b|\b[0-9a-f]{64}\b", "<HASH>", chtml)
        assert LC.scan_text(cleaned, field_name=f"transcripts/{slug}") == []

    # Check inline redaction markers on conversations that had redactions
    planning_html = (DIST / "transcripts/2026-10-23-podcast-planning/index.html").read_text(encoding="utf-8")
    assert "Redaction ID: RED-001" in planning_html
    assert 'id="redaction-log-red-001"' in planning_html


def test_layer2_changelog_and_rss_feed():
    """Layer 2 (/changelog/ and /changelog/feed.xml) is fed from git log, grouped by UTC date, and cross-linked."""
    cl_html = (DIST / "changelog/index.html").read_text(encoding="utf-8")
    assert 'class="transparency-bar i18n-ready"' in cl_html
    assert 'id="date-2026-10-24"' in cl_html
    assert 'id="date-2026-10-23"' in cl_html
    assert cl_html.index('id="date-2026-10-24"') < cl_html.index('id="date-2026-10-23"')
    assert 'data-rss-feed="/changelog/feed.xml"' in cl_html
    assert 'href="/changelog/feed.xml"' in cl_html
    assert 'id="raw-git-commits"' in cl_html

    # Check upward links to Layer 3 and downward links to Layer 1
    assert 'href="/growth/#growth-' in cl_html
    assert 'href="/transcripts/2026-10-23-to-2026-10-24-site-and-platform-build/#turn-' in cl_html

    # Every git commit in changelog.json has a #commit-<short_hash> row
    cl_json = json.loads((DIST / "changelog/changelog.json").read_text(encoding="utf-8"))
    assert cl_json["total_git_commits"] >= 50
    assert len(cl_json["entries"]) >= 15
    for c in cl_json["git_commits"][:20]:
        assert f'id="commit-{c["short"]}"' in cl_html

    # Validate RSS 2.0 XML
    rss_xml = (DIST / "changelog/feed.xml").read_text(encoding="utf-8")
    root = ET.fromstring(rss_xml)
    assert root.tag == "rss" and root.attrib.get("version") == "2.0"
    channel = root.find("channel")
    assert channel is not None
    items = channel.findall("item")
    assert len(items) == len(cl_json["entries"])
    for item in items:
        assert item.findtext("title")
        assert item.findtext("link", "").startswith("https://askevepodcast.com/changelog/#")
        desc = item.findtext("description", "")
        assert "https://askevepodcast.com/growth/#" in desc
        assert "https://askevepodcast.com/transcripts/" in desc


def test_layer3_growth_log_and_rss_feed():
    """Layer 3 (/growth/#growth-log and /growth/feed.xml) is written in first-person Eve voice and links down to L2 & L1."""
    g_html = (DIST / "growth/index.html").read_text(encoding="utf-8")
    assert 'class="transparency-bar i18n-ready"' in g_html
    assert 'id="growth-log"' in g_html
    assert 'data-rss-feed="/growth/feed.xml"' in g_html
    assert 'href="/growth/feed.xml"' in g_html

    g_json = json.loads((DIST / "growth/growth-log.json").read_text(encoding="utf-8"))
    assert len(g_json["entries"]) >= 12
    for e in g_json["entries"]:
        assert e["headline"].startswith("Today I ")
        assert f'id="{e["id"]}"' in g_html
        for cl_link in e["changelog_links"]:
            assert f'/changelog/#{cl_link["id"]}' in g_html
            assert f'/changelog/#commit-{cl_link["commit"]}' in g_html
        for t in e["transcript_links"]:
            assert f'/transcripts/{t["slug"]}/#{t["anchor"]}' in g_html

    # Validate Growth RSS 2.0 XML
    rss_xml = (DIST / "growth/feed.xml").read_text(encoding="utf-8")
    root = ET.fromstring(rss_xml)
    assert root.tag == "rss" and root.attrib.get("version") == "2.0"
    items = root.findall("./channel/item")
    assert len(items) == len(g_json["entries"])
    for item in items:
        assert "Today I " in item.findtext("title", "")
        desc = item.findtext("description", "")
        assert "https://askevepodcast.com/changelog/#" in desc
        assert "https://askevepodcast.com/transcripts/" in desc


def test_cross_linking_nav_footer_and_locales():
    """All 3 layers are linked from nav/footer and built across all 8 locales with zero missing UI strings."""
    home_html = (DIST / "index.html").read_text(encoding="utf-8")
    for path in ("/changelog/", "/transcripts/", "/growth/", "/changelog/feed.xml", "/growth/feed.xml"):
        assert f'href="{path}"' in home_html

    for lang in I.LANGS:
        assert (DIST / lang / "changelog/index.html").exists()
        assert (DIST / lang / "transcripts/index.html").exists()
        assert (DIST / lang / "growth/index.html").exists()
        assert (DIST / lang / "transcripts/2026-10-23-podcast-planning/index.html").exists()

    proc = subprocess.run([sys.executable, "scripts/leak_check.py", "--check-repo"], cwd=ROOT, capture_output=True, text=True, check=True)
    res = json.loads(proc.stdout)
    assert res["passed"] is True and res["findings"] == []
