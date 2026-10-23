#!/usr/bin/env python3
"""Render feed.xml (RSS 2.0 + iTunes + Podcasting 2.0) and per-episode show notes from the D1 data.

Reads a local SQLite file (CI: `wrangler d1 export ask-eve-meta --remote --output d1.sql` then sqlite3) so the
build never needs network beyond that export. AI-reconstructed episodes get the disclosure at the top of the
description and the show notes. Placeholder audio (manifest placeholder_audio) is never rendered unless
--allow-placeholder (local dry runs only).
"""
from __future__ import annotations

import argparse
import email.utils
import datetime as dt
import html
import json
import sqlite3
import sys
from pathlib import Path

SITE = "https://askevepodcast.com"
FEED = f"{SITE}/feed.xml"
OWNER_EMAIL = "podcast@askevepodcast.com"
SHOW = {
    "title": "Ask Eve", "author": "David Bloomin", "language": "en", "category": "Technology",
    "description": "David talks with Eve, a Grok voice companion, about technology, power and the people shaping both.",
    "image": "https://audio.askevepodcast.com/art/cover-3000.v1.jpg",
}


def rfc2822(iso: str) -> str:
    return email.utils.format_datetime(dt.datetime.fromisoformat(iso.replace("Z", "+00:00")))


def hhmmss(sec: int) -> str:
    return f"{sec // 3600:02d}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


def description(ep: dict, people: list[str]) -> str:
    parts = []
    if ep["ai_reconstructed"]:
        parts.append(ep["ai_disclosure"])
    if ep["summary"]:
        parts.append(ep["summary"])
    if people:
        parts.append("People discussed: " + ", ".join(people) + ".")
    parts.append(f"Full verbatim transcript: {SITE}/episodes/{ep['slug']}/")
    return "\n\n".join(parts)


def show_notes(ep: dict, people: list[str], chapters: list[dict]) -> str:
    out = [f"# {ep['title']}", ""]
    if ep["ai_reconstructed"]:
        out += [f"> **AI-reconstructed audio.** {ep['ai_disclosure']}", ""]
    out += [f"- Date: {ep['date']}", f"- Duration: {hhmmss(ep['duration_seconds'] or 0)}",
            f"- Transcript: {SITE}/episodes/{ep['slug']}/", f"- Source: {ep['source_url']}", ""]
    if people:
        out += ["## People", "", *[f"- {p}" for p in people], ""]
    if chapters:
        out += ["## Chapters", "", *[f"- {hhmmss(int(c['start_seconds']))} {c['title']}" for c in chapters], ""]
    return "\n".join(out)


def load(db: Path, include_drafts: bool) -> list[dict]:
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    where = "" if include_drafts else "WHERE status = 'published'"
    eps = [dict(r) for r in con.execute(f"SELECT * FROM episodes {where} ORDER BY COALESCE(published_at, date) DESC")]
    for ep in eps:
        ep["people"] = [r[0] for r in con.execute(
            "SELECT p.name FROM episode_people ep JOIN people p ON p.id = ep.person_id WHERE ep.episode_id = ? ORDER BY p.name", (ep["id"],))]
        ep["chapters"] = [dict(r) for r in con.execute(
            "SELECT start_seconds, title FROM chapters WHERE episode_id = ? AND language = 'en' ORDER BY start_seconds", (ep["id"],))]
    return eps


def render(eps: list[dict], now: str | None = None) -> str:
    e = html.escape
    items = []
    for ep in eps:
        if not ep["audio_url"]:
            continue
        pub = ep["published_at"] or (ep["date"] + "T12:00:00Z")
        desc = description(ep, ep["people"])
        items.append(f"""  <item>
    <title>{e(ep['title'])}</title>
    <guid isPermaLink="false">{e(ep['guid'])}</guid>
    <link>{SITE}/episodes/{e(ep['slug'])}/</link>
    <pubDate>{rfc2822(pub)}</pubDate>
    <description>{e(desc)}</description>
    <itunes:summary>{e(desc)}</itunes:summary>
    <enclosure url="{e(ep['audio_url'])}" length="{ep['audio_bytes']}" type="{e(ep['audio_mime'] or 'audio/mpeg')}"/>
    <itunes:duration>{ep['duration_seconds']}</itunes:duration>
    <itunes:explicit>{'true' if ep['explicit'] else 'false'}</itunes:explicit>
    <itunes:episodeType>{e(ep['episode_type'])}</itunes:episodeType>{f'''
    <itunes:episode>{ep['episode_number']}</itunes:episode>''' if ep['episode_number'] is not None and ep['episode_number'] > 0 else ''}
    <podcast:transcript url="{SITE}/episodes/{e(ep['slug'])}/transcript.txt" type="text/plain"/>
    <podcast:chapters url="https://audio.askevepodcast.com/chapters/en/{e(Path(ep['audio_key'] or ep['slug']).stem)}.json" type="application/json+chapters"/>
  </item>""")
    built = rfc2822(now or dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd" xmlns:podcast="https://podcastindex.org/namespace/1.0" xmlns:atom="http://www.w3.org/2005/Atom">
<channel>
  <title>{SHOW['title']}</title>
  <link>{SITE}</link>
  <atom:link href="{FEED}" rel="self" type="application/rss+xml"/>
  <language>{SHOW['language']}</language>
  <description>{e(SHOW['description'])}</description>
  <lastBuildDate>{built}</lastBuildDate>
  <itunes:author>{SHOW['author']}</itunes:author>
  <itunes:owner><itunes:name>{SHOW['author']}</itunes:name><itunes:email>{OWNER_EMAIL}</itunes:email></itunes:owner>
  <itunes:image href="{SHOW['image']}"/>
  <itunes:category text="{SHOW['category']}"/>
  <itunes:explicit>true</itunes:explicit>
  <itunes:type>episodic</itunes:type>
{chr(10).join(items)}
</channel>
</rss>
"""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path, help="feed.xml path")
    ap.add_argument("--show-notes-dir", type=Path)
    ap.add_argument("--include-drafts", action="store_true", help="local dry runs only")
    ap.add_argument("--manifests", type=Path, nargs="*", default=[], help="manifest.json files; placeholder audio blocks rendering")
    ap.add_argument("--allow-placeholder", action="store_true", help="local dry runs only; never in publish.yml")
    a = ap.parse_args(argv)
    for m in a.manifests:
        if json.loads(m.read_text()).get("placeholder_audio") and not a.allow_placeholder:
            print(f"error: {m} is PLACEHOLDER audio; refusing to render a feed with it", file=sys.stderr)
            return 4
    eps = load(a.db, a.include_drafts)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(render(eps))
    if a.show_notes_dir:
        a.show_notes_dir.mkdir(parents=True, exist_ok=True)
        for ep in eps:
            stem = Path(ep["transcript_path"]).stem
            (a.show_notes_dir / f"{stem}.md").write_text(show_notes(ep, ep["people"], ep["chapters"]))
    print(json.dumps({"items": sum(1 for x in eps if x["audio_url"]), "out": str(a.out)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
