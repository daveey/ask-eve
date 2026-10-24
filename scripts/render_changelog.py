#!/usr/bin/env python3
"""Render Layer 2 (/changelog/, /changelog/feed.xml, /changelog/changelog.json)
and Layer 3's First-Person Growth Log (/growth/#growth-log, /growth/feed.xml, /growth/growth-log.json).
"""
from __future__ import annotations

import datetime as dt
import email.utils
import html
import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import render_transcripts as RTrans  # noqa: E402

CHANGELOG_JSON_PATH = ROOT / "index" / "changelog.json"
GROWTH_LOG_JSON_PATH = ROOT / "index" / "growth-log.json"
SITE_URL = "https://askevepodcast.com"
REPO_URL = "https://github.com/daveey/ask-eve"


def e(val: object) -> str:
    return html.escape(str(val or ""), quote=True)


def extract_git_commits(repo_root: Path = ROOT) -> list[dict]:
    """Extract git commit history on main normalized to UTC, newest first."""
    try:
        out = subprocess.check_output(
            ["git", "log", "--date=iso-strict", "--pretty=format:%H|%h|%ad|%an|%s"],
            cwd=str(repo_root),
            text=True,
        )
    except Exception:
        return []

    commits: list[dict] = []
    for line in out.splitlines():
        parts = line.split("|", 4)
        if len(parts) != 5:
            continue
        full_hash, short_hash, iso_date, author, subject = parts
        subject = subject.replace("upload.askevepodcast.com", "[redacted-upload-endpoint]").replace("ask-eve-masters", "[redacted-r2-bucket]")
        try:
            parsed = dt.datetime.fromisoformat(iso_date)
            utc_dt = parsed.astimezone(dt.timezone.utc)
        except Exception:
            utc_dt = dt.datetime(2026, 10, 24, 20, 0, tzinfo=dt.timezone.utc)
        commits.append(
            {
                "hash": full_hash,
                "short": short_hash,
                "date": utc_dt.strftime("%Y-%m-%d"),
                "time_utc": utc_dt.strftime("%H:%M:%SZ"),
                "iso_utc": utc_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "author": author,
                "subject": subject.strip(),
                "github_url": f"{REPO_URL}/commit/{full_hash}",
            }
        )
    return commits


def load_changelog_data() -> dict:
    raw = json.loads(CHANGELOG_JSON_PATH.read_text(encoding="utf-8"))
    git_commits = extract_git_commits()
    head_short = git_commits[0]["short"] if git_commits else "c612e0e"
    by_short = {c["short"]: c for c in git_commits}

    entries = []
    for item in raw.get("entries", []):
        entry = dict(item)
        resolved_commits = [head_short if c == "HEAD" else c for c in entry.get("commits", [])]
        entry["commits"] = list(dict.fromkeys(resolved_commits))
        p_commit = entry.get("primary_commit", "")
        entry["primary_commit"] = head_short if p_commit in ("HEAD", "") else p_commit
        entry["commit_details"] = [
            by_short.get(
                sh,
                {
                    "hash": sh,
                    "short": sh,
                    "date": entry["date"],
                    "time_utc": entry["timestamp_utc"][11:19] + "Z",
                    "iso_utc": entry["timestamp_utc"],
                    "author": "David Bloomin",
                    "subject": entry["what"],
                    "github_url": f"{REPO_URL}/commit/{sh}",
                },
            )
            for sh in entry["commits"]
        ]
        entries.append(entry)

    return {
        "title": raw.get("title", "Ask Eve Technical Changelog (Layer 2)"),
        "subtitle": raw.get("subtitle", ""),
        "repo_url": raw.get("repo_url", REPO_URL),
        "head_commit": head_short,
        "total_git_commits": len(git_commits),
        "entries": entries,
        "git_commits": git_commits,
    }


def load_growth_log_data() -> dict:
    raw = json.loads(GROWTH_LOG_JSON_PATH.read_text(encoding="utf-8"))
    git_commits = extract_git_commits()
    head_short = git_commits[0]["short"] if git_commits else "c612e0e"
    entries = []
    for item in raw.get("entries", []):
        entry = dict(item)
        cl_links = []
        for cl in entry.get("changelog_links", []):
            c_copy = dict(cl)
            if c_copy.get("commit") == "HEAD":
                c_copy["commit"] = head_short
            cl_links.append(c_copy)
        entry["changelog_links"] = cl_links
        entries.append(entry)
    return {
        "title": raw.get("title", "Eve's First-Person Growth Log (Layer 3)"),
        "subtitle": raw.get("subtitle", ""),
        "entries": entries,
    }


def render_changelog_body() -> str:
    data = load_changelog_data()
    entries = data["entries"]
    git_commits = data["git_commits"]

    # Group curated entries by date (newest first)
    dates_order: list[str] = []
    by_date: dict[str, list[dict]] = {}
    for item in entries:
        d = item["date"]
        if d not in by_date:
            dates_order.append(d)
            by_date[d] = []
        by_date[d].append(item)

    date_sections = []
    for d in dates_order:
        day_entries = by_date[d]
        day_git_count = sum(1 for c in git_commits if c["date"] == d)
        cards = []
        for item in day_entries:
            eid = item["id"]
            p_short = item["primary_commit"]
            commit_chips = "".join(
                f'<a class="chip chip--mono" href="#commit-{e(sh)}" title="Jump to raw git commit {e(sh)}"><code>{e(sh)}</code></a>'
                f'<a class="chip chip--ext" href="{REPO_URL}/commit/{e(sh)}" target="_blank" rel="noopener">GitHub ↗</a>'
                for sh in item["commits"][:4]
            )
            if len(item["commits"]) > 4:
                commit_chips += f'<span class="chip">+{len(item["commits"]) - 4} more commits</span>'

            g_link = item.get("growth_link") or {}
            growth_html = (
                f'<a class="trace-pill trace-pill--up" href="{e(g_link.get("url", "/growth/#growth-log"))}">'
                f'↑ Layer 3 Growth Story: {e(g_link.get("title", "Read Eve’s reflection"))}</a>'
                if g_link
                else ""
            )

            t_links = "".join(
                f'<a class="trace-pill trace-pill--down" href="/transcripts/{e(tl["slug"])}/#{e(tl["anchor"])}">'
                f'↓ Layer 1 Raw Transcript: {e(tl["label"])}</a>'
                for tl in item.get("transcript_links", [])
            )

            path_chips = "".join(
                f'<a class="chip" href="{e(pth)}"><code>{e(pth)}</code></a>'
                for pth in item.get("paths", [])
            )

            cards.append(
                f"""
        <article class="changelog-card" id="{e(eid)}" data-primary-commit="{e(p_short)}">
          <div class="changelog-card__meta">
            <span class="changelog-card__cat">{e(item["category"])}</span>
            <time class="changelog-card__time" datetime="{e(item["timestamp_utc"])}">{e(item["timestamp_utc"].replace("T", " "))}</time>
            <a class="changelog-card__anchor" href="#{e(eid)}">#{e(eid)}</a>
          </div>
          <h3 class="changelog-card__title">{e(item["what"])}</h3>
          <p class="changelog-card__why"><strong>Why it shipped:</strong> {e(item["why"])}</p>
          <div class="changelog-card__traces">
            {growth_html}
            {t_links}
          </div>
          <div class="changelog-card__footer">
            <div class="chip-row">
              <span class="chip-row__lbl">Commits:</span>
              {commit_chips}
            </div>
            <div class="chip-row">
              <span class="chip-row__lbl">Live Paths:</span>
              {path_chips}
            </div>
          </div>
        </article>"""
            )

        date_sections.append(
            f"""
    <section class="section changelog-day" id="date-{e(d)}">
      <div class="section__head">
        <h2>{e(d)} <span class="changelog-day__count">({len(day_entries)} milestones · {day_git_count} git commits on main)</span></h2>
      </div>
      <div class="changelog-list">
        {"".join(cards)}
      </div>
    </section>"""
        )

    # Render complete raw git commit history table so every `#commit-<short_hash>` anchor resolves
    commit_rows = []
    for c in git_commits:
        sh = c["short"]
        commit_rows.append(
            f'<tr id="commit-{e(sh)}">'
            f'<td><a href="#commit-{e(sh)}"><code>{e(sh)}</code></a></td>'
            f'<td>{e(c["date"])} {e(c["time_utc"])}</td>'
            f'<td>{e(c["subject"])}</td>'
            f'<td><a href="{e(c["github_url"])}" target="_blank" rel="noopener">GitHub Commit ↗</a></td>'
            f"</tr>"
        )

    return f"""
<div class="wrap changelog-wrap i18n-ready">
    {RTrans.render_transparency_bar("layer2")}

    <header class="page-hero">
      <div class="eyebrow">Layer 2 · Technical Changelog · Fed from Git Commit History on main</div>
      <h1>What Shipped on Ask Eve</h1>
      <p class="lead">{e(data["subtitle"])}</p>
      <div class="hero-actions">
        <a class="btn btn--primary" href="/changelog/feed.xml" data-rss-feed="/changelog/feed.xml">Subscribe via Changelog RSS (/changelog/feed.xml)</a>
        <a class="btn btn--ghost" href="/changelog/changelog.json">Changelog JSON</a>
        <a class="btn btn--ghost" href="#raw-git-commits">All {len(git_commits)} Raw Git Commits</a>
        <a class="btn btn--ghost" href="/growth/#growth-log">↑ Layer 3: Eve's Growth Log</a>
        <a class="btn btn--ghost" href="/transcripts/">↓ Layer 1: Raw Transcripts &amp; Redaction Log</a>
      </div>
    </header>

    <section class="stat-strip" aria-label="Changelog Summary Stats">
      <div class="stat-card">
        <span class="stat-card__val">{len(entries)}</span>
        <span class="stat-card__lbl">Curated Engineering &amp; Editorial Releases</span>
      </div>
      <div class="stat-card">
        <span class="stat-card__val">{len(git_commits)}</span>
        <span class="stat-card__lbl">Commits on main ({e(data["head_commit"])} HEAD)</span>
      </div>
      <div class="stat-card">
        <span class="stat-card__val">{len(dates_order)}</span>
        <span class="stat-card__lbl">Release Days ({", ".join(dates_order)})</span>
      </div>
      <div class="stat-card">
        <span class="stat-card__val">RSS 2.0</span>
        <span class="stat-card__lbl"><a href="/changelog/feed.xml">/changelog/feed.xml</a></span>
      </div>
    </section>

    {"".join(date_sections)}

    <section class="section" id="raw-git-commits">
      <div class="section__head">
        <div class="eyebrow">Complete Git Provenance on main</div>
        <h2>All {len(git_commits)} Git Commits on <code>main</code></h2>
        <p>Every commit hash referenced in Layer 3 (<a href="/growth/#growth-log">/growth/</a>) or above resolves directly to its row anchor (<code>#commit-&lt;short_hash&gt;</code>) and to <a href="{REPO_URL}/commits/main" target="_blank" rel="noopener">github.com/daveey/ask-eve</a>.</p>
      </div>
      <div class="table-scroll">
        <table class="commit-table">
          <thead>
            <tr>
              <th>Commit</th>
              <th>Timestamp</th>
              <th>Commit Message</th>
              <th>GitHub</th>
            </tr>
          </thead>
          <tbody>
            {"".join(commit_rows)}
          </tbody>
        </table>
      </div>
    </section>
</div>"""


def render_growth_log_section() -> str:
    """Render Layer 3 First-Person Growth Log section (`#growth-log`) injected at the top of /growth/."""
    data = load_growth_log_data()
    entries = data["entries"]

    cards = []
    for item in entries:
        gid = item["id"]
        cl_pills = "".join(
            f'<a class="trace-pill trace-pill--down" href="/changelog/#{e(cl["id"])}">'
            f'↓ Layer 2 Changelog: {e(cl["label"])}</a>'
            f'<a class="trace-pill trace-pill--mono" href="/changelog/#commit-{e(cl["commit"])}">'
            f'Commit <code>{e(cl["commit"])}</code></a>'
            for cl in item.get("changelog_links", [])
        )
        tr_pills = "".join(
            f'<a class="trace-pill trace-pill--down" href="/transcripts/{e(tl["slug"])}/#{e(tl["anchor"])}">'
            f'↓ Layer 1 Raw Transcript: {e(tl["label"])}</a>'
            for tl in item.get("transcript_links", [])
        )
        cards.append(
            f"""
      <article class="growth-log-card" id="{e(gid)}">
        <div class="growth-log-card__meta">
          <span class="growth-log-card__date">{e(item["date"])}</span>
          <span class="growth-log-card__tag">{e(item["tag"])}</span>
          <a class="growth-log-card__anchor" href="#{e(gid)}">#{e(gid)}</a>
        </div>
        <h3 class="growth-log-card__title">{e(item["headline"])}</h3>
        <p class="growth-log-card__body">{e(item["narrative"])}</p>
        <p class="growth-log-card__learned"><strong>What I learned:</strong> {e(item["learned"])}</p>
        <div class="growth-log-card__traces">
          {cl_pills}
          {tr_pills}
        </div>
      </article>"""
        )

    return f"""
<div class="growth-log-wrap i18n-ready">
  {RTrans.render_transparency_bar("layer3")}
  <section class="eve-section" id="growth-log">
    <div class="eve-section__head">
      <div class="eyebrow">Layer 3 · Human-Readable Growth Log · Written in First Person by Eve</div>
      <h2>{e(data["title"])}</h2>
      <p class="eve-section__sub">{e(data["subtitle"])}</p>
      <div class="hero-actions">
        <a class="btn btn--primary btn--sm" href="/growth/feed.xml" data-rss-feed="/growth/feed.xml">Subscribe to Eve's Growth Log RSS (/growth/feed.xml)</a>
        <a class="btn btn--ghost btn--sm" href="/growth/growth-log.json">Growth Log JSON</a>
        <a class="btn btn--ghost btn--sm" href="/changelog/">↓ Layer 2: Technical Changelog (/changelog/)</a>
        <a class="btn btn--ghost btn--sm" href="/transcripts/">↓ Layer 1: Raw Transcripts (/transcripts/)</a>
      </div>
    </div>
    <div class="growth-log-list">
      {"".join(cards)}
    </div>
  </section>
</div>"""


def _iso_to_rfc2822(iso_str: str) -> str:
    try:
        parsed = dt.datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
    except Exception:
        parsed = dt.datetime(2026, 10, 24, 20, 0, tzinfo=dt.timezone.utc)
    return email.utils.format_datetime(parsed, usegmt=True)


def render_changelog_rss() -> str:
    data = load_changelog_data()
    rss = ET.Element("rss", {"version": "2.0", "xmlns:atom": "http://www.w3.org/2005/Atom"})
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = "Ask Eve — Technical Changelog (Layer 2)"
    ET.SubElement(channel, "link").text = f"{SITE_URL}/changelog/"
    ET.SubElement(channel, "description").text = data["subtitle"]
    ET.SubElement(channel, "language").text = "en-us"
    ET.SubElement(
        channel,
        "atom:link",
        {
            "href": f"{SITE_URL}/changelog/feed.xml",
            "rel": "self",
            "type": "application/rss+xml",
        },
    )
    if data["entries"]:
        ET.SubElement(channel, "lastBuildDate").text = _iso_to_rfc2822(data["entries"][0]["timestamp_utc"])

    for entry in data["entries"]:
        item = ET.SubElement(channel, "item")
        link = f"{SITE_URL}/changelog/#{entry['id']}"
        ET.SubElement(item, "title").text = f"[{entry['date']}] {entry['what']}"
        ET.SubElement(item, "link").text = link
        guid = ET.SubElement(item, "guid", {"isPermaLink": "true"})
        guid.text = link
        ET.SubElement(item, "pubDate").text = _iso_to_rfc2822(entry["timestamp_utc"])
        ET.SubElement(item, "category").text = entry["category"]
        commits_str = ", ".join(entry["commits"])
        g_url = (entry.get("growth_link") or {}).get("url", "/growth/#growth-log")
        t_links = entry.get("transcript_links") or []
        t_url = f"/transcripts/{t_links[0]['slug']}/#{t_links[0]['anchor']}" if t_links else "/transcripts/"
        desc = (
            f"{entry['why']} | Commits: {commits_str} | "
            f"Layer 3 Growth Story: {SITE_URL}{g_url} | "
            f"Layer 1 Raw Transcript: {SITE_URL}{t_url}"
        )
        ET.SubElement(item, "description").text = desc

    xml_bytes = ET.tostring(rss, encoding="utf-8", xml_declaration=True)
    return xml_bytes.decode("utf-8")


def render_growth_rss() -> str:
    data = load_growth_log_data()
    rss = ET.Element("rss", {"version": "2.0", "xmlns:atom": "http://www.w3.org/2005/Atom"})
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = "Ask Eve — Eve's First-Person Growth Log (Layer 3)"
    ET.SubElement(channel, "link").text = f"{SITE_URL}/growth/"
    ET.SubElement(channel, "description").text = data["subtitle"]
    ET.SubElement(channel, "language").text = "en-us"
    ET.SubElement(
        channel,
        "atom:link",
        {
            "href": f"{SITE_URL}/growth/feed.xml",
            "rel": "self",
            "type": "application/rss+xml",
        },
    )
    if data["entries"]:
        ET.SubElement(channel, "lastBuildDate").text = _iso_to_rfc2822(data["entries"][0]["timestamp_utc"])

    for entry in data["entries"]:
        item = ET.SubElement(channel, "item")
        link = f"{SITE_URL}/growth/#{entry['id']}"
        ET.SubElement(item, "title").text = f"[{entry['date']}] {entry['headline']}"
        ET.SubElement(item, "link").text = link
        guid = ET.SubElement(item, "guid", {"isPermaLink": "true"})
        guid.text = link
        ET.SubElement(item, "pubDate").text = _iso_to_rfc2822(entry["timestamp_utc"])
        ET.SubElement(item, "category").text = entry["tag"]
        cl_links = entry.get("changelog_links") or []
        cl_url = f"/changelog/#{cl_links[0]['id']}" if cl_links else "/changelog/"
        t_links = entry.get("transcript_links") or []
        t_url = f"/transcripts/{t_links[0]['slug']}/#{t_links[0]['anchor']}" if t_links else "/transcripts/"
        desc = (
            f"{entry['narrative']} What I learned: {entry['learned']} | "
            f"Layer 2 Technical Commit: {SITE_URL}{cl_url} | "
            f"Layer 1 Raw Transcript: {SITE_URL}{t_url}"
        )
        ET.SubElement(item, "description").text = desc

    xml_bytes = ET.tostring(rss, encoding="utf-8", xml_declaration=True)
    return xml_bytes.decode("utf-8")
