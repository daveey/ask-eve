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

sys.path.insert(0, str(Path(__file__).resolve().parent))
from frontmatter import public_slug  # noqa: E402

SITE = "https://askevepodcast.com"
FEED = f"{SITE}/feed.xml"
OWNER_EMAIL = "podcast@askevepodcast.com"
SHOW = {
    "title": "Ask Eve", "author": "David Bloomin", "language": "en", "category": "Technology",
    "description": "David talks with Eve, a Grok agent, about technology, power and the people shaping both.",
    "image": "https://audio.askevepodcast.com/art/cover-3000.v1.jpg",
}


def rfc2822(iso: str) -> str:
    return email.utils.format_datetime(dt.datetime.fromisoformat(iso.replace("Z", "+00:00")))


def hhmmss(sec: int) -> str:
    return f"{sec // 3600:02d}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


def description(ep: dict, people: list[str]) -> str:
    """Plain text: AI label (required for reconstructed audio), the Context note, the Topics list, people, transcript."""
    parts = []
    if ep["ai_reconstructed"]:
        parts.append(ep["ai_disclosure"])
    if ep.get("context") or ep["summary"]:
        parts.append(ep.get("context") or ep["summary"])
    if ep.get("topics"):
        parts.append("Topics:\n" + "\n".join(f"- {t}" for t in ep["topics"]))
    if people:
        parts.append("People discussed: " + ", ".join(people) + ".")
    parts.append(f"Full verbatim transcript: {SITE}/episodes/{ep['slug']}/")
    return "\n\n".join(parts)


def content_html(ep: dict, people: list[str]) -> str:
    """<content:encoded> body: the same information as description(), as HTML."""
    e = html.escape
    out = []
    if ep["ai_reconstructed"]:
        out.append(f"<p><em>{e(ep['ai_disclosure'])}</em></p>")
    if ep.get("context") or ep["summary"]:
        out.append(f"<p>{e(ep.get('context') or ep['summary'])}</p>")
    if ep.get("topics"):
        out.append("<p><strong>Topics</strong></p><ul>" + "".join(f"<li>{e(t)}</li>" for t in ep["topics"]) + "</ul>")
    if people:
        out.append(f"<p>People discussed: {e(', '.join(people))}.</p>")
    url = f"{SITE}/episodes/{ep['slug']}/"
    out.append(f'<p>Full verbatim transcript: <a href="{e(url)}">{e(url)}</a></p>')
    return "".join(out).replace("]]>", "]]&gt;")


def show_notes(ep: dict, people: list[str], chapters: list[dict]) -> str:
    out = [f"# {ep['title']}", ""]
    if ep["ai_reconstructed"]:
        disc = ep["ai_disclosure"]
        if disc.lower().startswith("ai-reconstructed audio:"):
            disc = disc.split(":", 1)[1].strip()
            disc = disc[:1].upper() + disc[1:]
        out += [f"> **AI-reconstructed audio.** {disc}", ""]
    out += [f"- Date: {ep['date']}", f"- Duration: {hhmmss(ep['duration_seconds'] or 0)}",
            f"- Transcript: {SITE}/episodes/{ep['slug']}/"]
    out += ([f"- Source: {ep['source_url']}"] if ep.get("source_url") else []) + [""]
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
        stem = Path(ep["transcript_path"]).stem
        ep["key"] = stem.split("_", 1)[1]        # filename slug: stable cover-art seed
        ep["slug"] = public_slug(stem)           # public URL slug (index/slugs.json); D1 may still hold the old one
        ep["people"] = [r[0] for r in con.execute(
            "SELECT p.name FROM episode_people ep JOIN people p ON p.id = ep.person_id WHERE ep.episode_id = ? ORDER BY p.name", (ep["id"],))]
        ep["chapters"] = [dict(r) for r in con.execute(
            "SELECT start_seconds, title FROM chapters WHERE episode_id = ? AND language = 'en' ORDER BY start_seconds", (ep["id"],))]
    return eps


def itunes_episode(ep: dict) -> int | None:
    """<itunes:episode> value: positive numbers on full episodes only. Bonus/trailer items and episode 0
    (Apple wants a positive integer) get no number at all rather than 0."""
    n = ep.get("episode_number")
    if (ep.get("episode_type") or "full") != "full" or n is None:
        return None
    return int(n) if int(n) >= 1 else None


def itunes_season(ep: dict) -> int:
    """<itunes:season> on every item, full and bonus alike (Apple shows "Unknown Season" without it). Default 1."""
    n = ep.get("season")
    return int(n) if n and int(n) >= 1 else 1


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
    <content:encoded><![CDATA[{content_html(ep, ep["people"])}]]></content:encoded>
    <enclosure url="{e(ep['audio_url'])}" length="{ep['audio_bytes']}" type="{e(ep['audio_mime'] or 'audio/mpeg')}"/>
    <itunes:duration>{ep['duration_seconds']}</itunes:duration>
    <itunes:explicit>{'true' if ep['explicit'] else 'false'}</itunes:explicit>
    <itunes:episodeType>{e(ep.get('episode_type') or 'full')}</itunes:episodeType>
    <itunes:season>{itunes_season(ep)}</itunes:season>{f'''
    <itunes:episode>{itunes_episode(ep)}</itunes:episode>''' if itunes_episode(ep) is not None else ''}
    <podcast:transcript url="{SITE}/episodes/{e(ep['slug'])}/transcript.txt" type="text/plain"/>
    <podcast:chapters url="https://audio.askevepodcast.com/chapters/en/{e(Path(ep['audio_key'] or ep['slug']).stem)}.json" type="application/json+chapters"/>
  </item>""")
    built = rfc2822(now or dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd" xmlns:podcast="https://podcastindex.org/namespace/1.0" xmlns:content="http://purl.org/rss/1.0/modules/content/" xmlns:atom="http://www.w3.org/2005/Atom">
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
