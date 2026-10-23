#!/usr/bin/env python3
"""Netlify build: D1 (REST, read-only token) -> local SQLite -> site/dist/{feed.xml, index.html, episodes/<slug>/...}.

Env (Netlify site settings): CLOUDFLARE_ACCOUNT_ID, D1_DATABASE_ID, D1_READ_TOKEN.
Fails the build on any D1 error or transcript sha mismatch (never ships an empty or stale feed).
Local: python3 scripts/build_site.py --db out/local-d1.sqlite --include-drafts
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import sqlite3
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import render_feed as F  # noqa: E402
import sqlite_apply  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
TABLES = ["episodes", "people", "episode_people", "chapters"]


def _disc(d: str) -> str:
    if not d.lower().startswith("ai-reconstructed audio:"):
        return d
    d = d.split(":", 1)[1].strip()
    return d[:1].upper() + d[1:]


def d1_query(sql: str) -> list[dict]:
    a, d, t = (os.environ[k] for k in ("CLOUDFLARE_ACCOUNT_ID", "D1_DATABASE_ID", "D1_READ_TOKEN"))
    req = urllib.request.Request(f"https://api.cloudflare.com/client/v4/accounts/{a}/d1/database/{d}/query",
                                 data=json.dumps({"sql": sql}).encode(), method="POST",
                                 headers={"Authorization": f"Bearer {t}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:  # noqa: S310
        j = json.load(r)
    if not j.get("success"):
        raise SystemExit(f"D1 query failed: {j.get('errors')}")
    return j["result"][0]["results"]


def pull(db: Path) -> None:
    if db.exists():
        db.unlink()
    sqlite_apply.apply(db, ROOT / "migrations")
    con = sqlite3.connect(db)
    for t in TABLES:
        rows = d1_query(f"SELECT * FROM {t}")
        for r in rows:
            con.execute(f"INSERT INTO {t} ({','.join(r)}) VALUES ({','.join('?' * len(r))})", list(r.values()))
    con.commit()


def page(title: str, body: str) -> str:
    return (f"<!doctype html><html lang=en><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>{html.escape(title)}</title><link rel=alternate type=application/rss+xml href=/feed.xml>"
            f"<style>body{{font:16px/1.5 system-ui;max-width:46rem;margin:2rem auto;padding:0 1rem}}.ai{{background:#fff3cd;padding:.5rem 1rem}}"
            f".t{{margin:.6rem 0}}.s{{font-weight:600}}</style><body>{body}</body></html>")


def build(db: Path, out: Path, include_drafts: bool) -> int:
    eps = F.load(db, include_drafts)
    out.mkdir(parents=True, exist_ok=True)
    (out / "feed.xml").write_text(F.render(eps))
    items = []
    for ep in eps:
        md_path = ROOT / ep["transcript_path"]
        data = md_path.read_bytes()
        if hashlib.sha256(data).hexdigest() != ep["transcript_sha256"]:
            raise SystemExit(f"transcript sha mismatch for {ep['slug']} ({md_path})")
        md = data.decode()
        body_md = md.split("\n---\n", 1)[1] if md.startswith("---\n") else md
        turns = "".join(f"<p class=t>{html.escape(p)}</p>" for p in body_md.strip().split("\n\n"))
        d = out / "episodes" / ep["slug"]
        d.mkdir(parents=True, exist_ok=True)
        (d / "transcript.txt").write_text(body_md.strip() + "\n")
        ai = f"<p class=ai><b>AI-reconstructed audio.</b> {html.escape(_disc(ep['ai_disclosure']))}</p>" if ep["ai_reconstructed"] else ""
        player = f"<audio controls preload=none src='{html.escape(ep['audio_url'])}'></audio>" if ep["audio_url"] else ""
        chapters = "".join(f"<li>{F.hhmmss(int(c['start_seconds']))} {html.escape(c['title'])}</li>" for c in ep["chapters"])
        (d / "index.html").write_text(page(ep["title"], f"<p><a href=/>Ask Eve</a></p><h1>{html.escape(ep['title'])}</h1>"
                                          f"<p>{ep['date']}</p>{ai}{player}<ol>{chapters}</ol><h2>Transcript</h2>{turns}"))
        items.append(f"<li><a href=/episodes/{ep['slug']}/>{html.escape(ep['title'])}</a> ({ep['date']})</li>")
    (out / "index.html").write_text(page("Ask Eve", f"<h1>Ask Eve</h1><p>{html.escape(F.SHOW['description'])}</p>"
                                               f"<p><a href=/feed.xml>RSS feed</a></p><ul>{''.join(items)}</ul>"))
    print(json.dumps({"episodes": len(eps), "out": str(out)}))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", type=Path, help="local SQLite (skip the D1 pull)")
    ap.add_argument("--out", type=Path, default=ROOT / "site/dist")
    ap.add_argument("--include-drafts", action="store_true")
    a = ap.parse_args(argv)
    db = a.db
    if db is None:
        db = ROOT / "site/.d1.sqlite"
        pull(db)
    return build(db, a.out, a.include_drafts)


if __name__ == "__main__":
    raise SystemExit(main())
