#!/usr/bin/env python3
"""Site build: D1 (REST, read-only token) -> local SQLite -> site/dist (static HTML/CSS/JS for Cloudflare Pages).

Writes feed.xml (render_feed.py, unchanged format), index.html, episodes/<slug>/{index.html,transcript.txt},
eve/, people/, people/<slug>/, about/, 404.html, sitemap.xml, robots.txt, _redirects (+ meta-refresh stubs),
and copies site/static + site/_headers.
Pure standard library (CI runs plain python3). No analytics, no third-party scripts or fonts.

Env (CI): CLOUDFLARE_ACCOUNT_ID, D1_DATABASE_ID, D1_READ_TOKEN.
Fails the build on any D1 error or transcript sha mismatch (never ships an empty or stale feed).
Local: python3 scripts/build_site.py --db out/local-d1.sqlite --include-drafts
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html
import json
import math
import os
import random
import re
import shutil
import sqlite3
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import render_avatar as RA  # noqa: E402
import render_feed as F  # noqa: E402
from frontmatter import episode_type, parse_front_matter_block, season  # noqa: E402
import sqlite_apply  # noqa: E402
import site_i18n as I  # noqa: E402  translated editions (/ru/...), see scripts/site_i18n.py

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "site/static"
TABLES = ["episodes", "people", "episode_people", "chapters", "turn_offsets"]
SITE, EMAIL = F.SITE, F.OWNER_EMAIL
REPO = "https://github.com/daveey/ask-eve"
TAGLINE = "Tech, power and the people shaping both, with an AI co-host who argues back."
TURN_RE = re.compile(r"^\[(\d{2}):(\d{2}):(\d{2})\] (David|Eve): ?(.*)$")
REAL_METHODS = {"asr-match", "sync-phrase", "interpolated", "fit"}   # David's recorded track
MOMENTS_DIR = ROOT / "index/moments"          # scripts/extract_moments.py output, one JSON per episode slug
e = html.escape


# ---------------------------------------------------------------- data

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


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def front_matter(md: str) -> tuple[dict, str]:
    m = re.match(r"^---\n(.*?)\n---\n", md, re.S)
    if not m:
        return {}, md
    return parse_front_matter_block(m.group(1)), md[m.end():]


def parse_turns(md: str) -> list[dict]:
    """Speaker turns in file order (same indexing as align_audio.parse_transcript / turn_offsets.turn_index)."""
    _, body = front_matter(md)
    turns: list[dict] = []
    for line in body.split("\n"):
        m = TURN_RE.match(line)
        if m:
            turns.append({"i": len(turns), "speaker": m.group(4), "clock": f"{m.group(1)}:{m.group(2)}",
                          "clock_full": f"{m.group(1)}:{m.group(2)}:{m.group(3)}",
                          "lines": [m.group(5)], "meta": []})
        elif turns and line.startswith(">"):
            turns[-1]["meta"].append(line[1:].strip())
        elif turns:
            turns[-1]["lines"].append(line)
    for t in turns:
        t["text"] = "\n".join(t.pop("lines")).strip()
        t["gap"] = t["text"].startswith("[transcript gap")
    return turns


def load_people_index(path: Path) -> list[dict]:
    out = []
    if not path.exists():
        return out
    for line in path.read_text().splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) >= 2 and line.startswith("|") and cells[0] not in ("Person", "") and not set(cells[0]) <= set("-: "):
            out.append({"name": cells[0], "slug": slugify(cells[0]), "stance": cells[1]})
    return out


def voice_of(turn: dict, method: str | None) -> str:
    """'gap' | 'real' (David's recorded voice) | 'narrator' (David, AI voice) | 'eve' (Eve, AI voice) | ''."""
    if turn["gap"]:
        return "gap"
    if turn["speaker"] == "Eve":
        return "eve"
    if method is None:
        return ""
    return "real" if method in REAL_METHODS else "narrator"


def load_site(db: Path, include_drafts: bool) -> tuple[list[dict], list[dict]]:
    eps = F.load(db, include_drafts)
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    cfg = json.loads((ROOT / "site/site.json").read_text()) if (ROOT / "site/site.json").exists() else {}
    for ep in eps:
        md_path = ROOT / ep["transcript_path"]
        data = md_path.read_bytes()
        if hashlib.sha256(data).hexdigest() != ep["transcript_sha256"]:
            raise SystemExit(f"transcript sha mismatch for {ep['slug']} ({md_path})")
        md = data.decode()
        ep["fm"], body = front_matter(md)
        ep["body_md"] = body.strip()
        offs = {r["turn_index"]: dict(r) for r in con.execute(
            "SELECT turn_index, edited_start_seconds, edited_end_seconds, method FROM turn_offsets WHERE episode_id = ?",
            (ep["id"],))}
        ep["turns"] = parse_turns(md)
        for t in ep["turns"]:
            o = offs.get(t["i"], {})
            t["start"], t["end"] = o.get("edited_start_seconds"), o.get("edited_end_seconds")
            t["voice"] = voice_of(t, o.get("method"))
        dv = [t["voice"] for t in ep["turns"] if t["speaker"] == "David"]
        ep["david_real"], ep["david_narrator"] = dv.count("real"), dv.count("narrator")
        # Front-matter `context:` / `topics:` win over D1 and site.json, so a transcript edit is enough.
        ctx, topics = ep["fm"].get("context"), ep["fm"].get("topics")
        ep["context"] = ctx if isinstance(ctx, str) else ""
        ep["topics"] = topics if isinstance(topics, list) else []
        ep["episode_type"] = episode_type(ep["fm"])   # front matter wins over D1 (default full)
        ep["season"] = season(ep["fm"])               # front matter `season:` (default 1)
        if ep["episode_type"] == "bonus":
            ep["episode_number"] = None
        if ep["context"]:
            ep["summary"] = ep["context"]
        ep["blurb"] = (ep.get("summary") or cfg.get("blurbs", {}).get(ep["slug"]) or "").strip()
        ep["card_summary"] = card_summary(ep["fm"], cfg.get("summaries", {}).get(ep["slug"]), ep["context"] or ep["blurb"])
        ep["person_slugs"] = [slugify(p) for p in ep["people"]]
    eps.sort(key=lambda x: (x["published_at"] or x["date"], x["episode_number"] or 0), reverse=True)
    people = {p["slug"]: p for p in load_people_index(ROOT / "index/people.md")}
    for r in con.execute("SELECT slug, name, stance FROM people ORDER BY name"):
        p = people.setdefault(r["slug"], {"name": r["name"], "slug": r["slug"], "stance": ""})
        p["stance"] = p["stance"] or (r["stance"] or "")
    aliases = json.loads((ROOT / "index/people-aliases.json").read_text()) if (ROOT / "index/people-aliases.json").exists() else {}
    for p in people.values():
        p["name"] = aliases.get(p["slug"], {}).get("name", p["name"])   # display name override (slug stays stable)
        p["episodes"] = []
    for ep in eps:   # people pages list only reviewed discussion moments; episodes with none are left out
        ep["moments"] = load_moments(ep)
        for slug in dict.fromkeys(m["person"] for m in ep["moments"]):
            if slug in people:
                people[slug]["episodes"].append({"ep": ep, "moments": [m for m in ep["moments"] if m["person"] == slug]})
    people_list = sorted(people.values(), key=lambda p: (-sum(len(x["moments"]) for x in p["episodes"]), p["name"]))
    for ep in eps:
        ep["people_full"] = [p for p in people_list if any(x["ep"] is ep for x in p["episodes"])]
        ep["cfg"] = cfg
    attach_takes(eps, people_list)
    return eps, people_list


def load_moments(ep: dict) -> list[dict]:
    """Moments for this episode from index/moments/<slug>.json, timed with the episode's turn offsets."""
    path = MOMENTS_DIR / f"{ep['slug']}.json"
    if not path.exists():
        return []
    data = json.loads(path.read_text())
    if data.get("turns") != len(ep["turns"]):
        print(f"warning: {path.name} was made for {data.get('turns')} turns, transcript has {len(ep['turns'])}; skipped",
              file=sys.stderr)
        return []
    out = []
    for m in data["moments"]:
        a, b = ep["turns"][m["start_turn"]], ep["turns"][m["end_turn"]]
        start = a["start"] if a["start"] is not None else m.get("start")
        end = b["end"] if b["end"] is not None else m.get("end")
        out.append({**m, "start": start, "end": end, "first_turn": a})
    return out


def load_takes() -> dict:
    if not TAKES_FILE.exists():
        return {"people": {}, "topics": []}
    return json.loads(TAKES_FILE.read_text())


def resolve_ref(by_slug: dict, r: dict) -> dict | None:
    """A takes.json ref (episode slug + wall-clock time of a turn) -> that turn's audio offset. Episodes not on
    the site resolve to None (the anchor stays plain text); a clock time with no turn is an error."""
    ep = by_slug.get(r["episode"])
    if ep is None:
        return None
    for t in ep["turns"]:
        if t["clock_full"] == r["clock_time"]:
            if t["start"] is not None and not t["gap"]:
                # the turn's exact start: a floored second can fall in the gap before it and light up the previous turn
                sec = f"{t['start']:.2f}".rstrip("0").rstrip(".")
                href, when = f"/episodes/{ep['slug']}/#t={sec}", clock(t["start"])
            else:
                href, when = f"/episodes/{ep['slug']}/#turn-{t['i']}", t["clock"]
            return {**r, "ep": ep, "turn": t, "href": href, "when": when}
    raise ValueError(f"takes.json ref has no turn at {r['episode']} {r['clock_time']}")


def resolve_take(by_slug: dict, x: dict, owner: str) -> dict:
    full = "\n".join(x["take"])
    refs = []
    for r in x.get("refs", []):
        if r.get("anchor") and full.count(r["anchor"]) != 1:
            raise ValueError(f"takes.json {owner}: anchor must appear exactly once in the take: {r['anchor']!r}")
        if (rr := resolve_ref(by_slug, r)):
            refs.append(rr)
    return {**x, "refs": refs}


def attach_takes(eps: list[dict], people: list[dict]) -> list[dict]:
    """Eve's long-form takes (index/takes.json): sets p["take"] and p["topics"]; returns the topics."""
    if not SHOW_TOPICS:
        for p in people:
            p["take"], p["topics"] = None, []
        return []
    data = load_takes()
    by_slug = {ep["slug"]: ep for ep in eps} | {ep["key"]: ep for ep in eps if ep.get("key")}
    by_person = {p["slug"]: p for p in people}
    for slug in data["people"]:
        if slug not in by_person:
            raise ValueError(f"takes.json person {slug!r} is not in index/people.md")
    for p in people:
        x = data["people"].get(p["slug"])
        p["take"] = resolve_take(by_slug, x, p["slug"]) if x else None
        p["topics"] = []
        if x and x.get("lede"):
            p["stance"] = x["lede"]
    topics = []
    for t in data["topics"]:
        t = resolve_take(by_slug, t, t["slug"])
        t["people_full"] = [by_person[s] for s in t.get("people", []) if s in by_person]
        for p in t["people_full"]:
            p["topics"].append(t)
        topics.append(t)
    return topics


# ---------------------------------------------------------------- formatting

def clock(sec: float | None) -> str:
    s = int(sec or 0)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def human_duration(sec: int | None) -> str:
    m = round((sec or 0) / 60)
    return f"{m // 60} hr {m % 60} min" if m >= 60 else f"{m} min"


def human_date(d: str) -> str:
    x = dt.date.fromisoformat(d[:10])
    return f"{x.strftime('%b')} {x.day}, {x.year}"


def is_bonus(ep: dict) -> bool:
    return ep.get("episode_type") == "bonus"


def ep_label(ep: dict) -> str:
    if is_bonus(ep):
        return "Bonus"
    return f"Episode {ep['episode_number']}" if ep["episode_number"] is not None else "Episode"


def cover_mark(ep: dict) -> str:
    if is_bonus(ep):
        return "BONUS"
    return f"EP {ep['episode_number'] if ep['episode_number'] is not None else '·'}"


def bonus_badge(ep: dict) -> str:
    return '<span class="badge badge--bonus">Bonus</span>' if is_bonus(ep) else ""


def inline(s: str) -> str:
    out = []
    for k, part in enumerate(re.split(r"(`[^`\n]+`)", s)):
        if k % 2:
            out.append(f"<code>{e(part[1:-1])}</code>")
            continue
        p = e(part, quote=False)
        p = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", p)
        p = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r'<a href="\2" rel="nofollow noopener">\1</a>', p)
        p = re.sub(r'(?<![">])\b(https?://[^\s<)]+[^\s<).,;:!?])', r'<a href="\1" rel="nofollow noopener">\1</a>', p)
        out.append(p)
    return "".join(out)


# ---------------------------------------------------------------- auto-linking
# Names and things mentioned in the show become silent links (they look like plain text) that open a small
# menu of destinations: Eve's page on them, their own site or X account, Wikipedia, Grokipedia. Data comes
# from index/links.json (verified by scripts/build_links.py). Topic pages (/topics/<slug>/) join from
# index/takes.json: each topic adds an "Eve's take" entry to the menus of the entities it lists in
# `link_entities` (DOGE, Anthropic, ...) and links its own `link_terms` ("motivated reasoning", "SPACs").
LINKS_FILE = ROOT / "index/links.json"
TAKES_FILE = ROOT / "index/takes.json"
_SKIP_TAGS = {"a", "code", "pre", "h1", "h2", "h3", "h4", "h5", "h6", "script", "style", "button", "summary"}


def load_links() -> dict:
    ents = json.loads(LINKS_FILE.read_text())["entities"] if LINKS_FILE.exists() else {}
    if not SHOW_TOPICS:
        return ents
    for t in load_takes()["topics"]:
        item = {"label": f"Eve's take: {t['title']}", "url": f"/topics/{t['slug']}/", "kind": "site"}
        for k in t.get("link_entities", []):
            if k in ents:
                ent = ents[k]
                items = [i for i in ent["items"] if i["kind"] == "site"] + [item] + [i for i in ent["items"] if i["kind"] != "site"]
                ents[k] = {**ent, "items": items}
        if t.get("link_terms"):
            ents.setdefault("topic:" + t["slug"], {"name": t["title"], "type": "topic", "terms": t["link_terms"], "items": [item]})
    return ents


class Linker:
    """Per-page linker. `seen` sets implement first-mention-per-block; `used` collects the menus to embed."""

    def __init__(self, ents: dict, path: str = "", exclude: set[str] | None = None):
        self.ents, self.path, self.used = ents, path, {}
        self.term2key = {}
        for k, ent in ents.items():
            if exclude and k in exclude:
                continue
            for t in ent["terms"]:
                self.term2key.setdefault(t, k)
        terms = sorted(self.term2key, key=len, reverse=True)
        # case-sensitive whole-word match; letters/digits/hyphen-joined words count as word characters
        self.rx = re.compile(r"(?<![\w-])(" + "|".join(re.escape(t) for t in terms) + r")(?![\w-])") if terms else None

    def _menu(self, key: str) -> dict:
        ent = self.ents[key]
        items = [i for i in ent["items"] if i["url"] != self.path]
        return {"name": ent["name"], "items": items}

    def _sub(self, text_html: str, seen: set) -> str:
        if not self.rx:
            return text_html

        def rep(m):
            key = self.term2key[m.group(1)]
            if key in seen:
                return m.group(0)
            menu = self._menu(key)
            if not menu["items"]:
                return m.group(0)
            seen.add(key)
            self.used[key] = menu
            first = menu["items"][0]["url"]
            ext = "" if first.startswith("/") else ' target="_blank" rel="noopener"'
            return (f'<a class="xl" href="{e(first)}"{ext} data-xl="{e(key)}" aria-haspopup="menu" '
                    f'aria-expanded="false">{m.group(1)}</a>')
        return self.rx.sub(rep, text_html)

    def text(self, s: str, seen: set | None = None) -> str:
        """Escape plain text and link it."""
        return self._sub(e(s), set() if seen is None else seen)

    def html(self, h: str, seen: set | None = None) -> str:
        """Link the text nodes of trusted HTML, never inside links, code, headings or buttons."""
        seen = set() if seen is None else seen
        out, depth = [], 0
        for part in re.split(r"(<[^>]+>)", h):
            if part.startswith("<"):
                m = re.match(r"<(/?)([a-zA-Z0-9]+)", part)
                if m and m.group(2).lower() in _SKIP_TAGS and not part.endswith("/>"):
                    depth += -1 if m.group(1) else 1
                out.append(part)
            elif part and depth == 0:
                out.append(self._sub(part, seen))
            else:
                out.append(part)
        return "".join(out)

    def data_script(self) -> str:
        if not self.used:
            return ""
        return ('<script type="application/json" id="xl-data">'
                + json.dumps(self.used, ensure_ascii=False).replace("</", "<\\/") + "</script>")


NO_LINKS = Linker({})


def blocks(text: str) -> str:
    out, para, items, kind, code, in_code = [], [], [], None, [], False

    def flush_para():
        if para:
            out.append(f"<p>{inline(' '.join(para))}</p>")
            para.clear()

    def flush_list():
        nonlocal kind
        if items:
            out.append(f"<{kind}>" + "".join(f"<li>{inline(i)}</li>" for i in items) + f"</{kind}>")
            items.clear()
        kind = None

    for line in text.split("\n"):
        if line.strip().startswith("```"):
            if in_code:
                out.append(f"<pre><code>{e(chr(10).join(code))}</code></pre>")
                code.clear()
            else:
                flush_para(); flush_list()
            in_code = not in_code
        elif in_code:
            code.append(line)
        elif m := re.match(r"^\s*([-*]|\d+\.)\s+(.*)$", line):
            flush_para()
            k = "ol" if m.group(1)[0].isdigit() else "ul"
            if kind and kind != k:
                flush_list()
            kind = k
            items.append(m.group(2))
        elif not line.strip():
            flush_para(); flush_list()
        elif items and line.startswith(" "):
            items[-1] += " " + line.strip()
        else:
            flush_list()
            para.append(line.strip())
    if in_code:
        out.append(f"<pre><code>{e(chr(10).join(code))}</code></pre>")
    flush_para(); flush_list()
    return "".join(out)


# ---------------------------------------------------------------- art + icons

BRAND = [("#ff6a3d", "#e0459b", "#7b5cff"), ("#ff9a3d", "#ff4f6d", "#a24dff"), ("#ffb13d", "#ff5a5f", "#6a5cff"),
         ("#ff6a3d", "#c13bd6", "#4f6bff")]

ICON = {
    "play": '<path d="M8 5.5v13a1 1 0 0 0 1.5.86l11-6.5a1 1 0 0 0 0-1.72l-11-6.5A1 1 0 0 0 8 5.5z"/>',
    "pause": '<rect x="6" y="5" width="4.2" height="14" rx="1.3"/><rect x="13.8" y="5" width="4.2" height="14" rx="1.3"/>',
    "back": '<path d="M12 5V2L7 6l5 4V7a6 6 0 1 1-6 6H4a8 8 0 1 0 8-8z"/><text x="12" y="16.2" text-anchor="middle" font-size="6.6" font-weight="700" font-family="system-ui,sans-serif">15</text>',
    "fwd": '<path d="M12 5V2l5 4-5 4V7a6 6 0 1 0 6 6h2a8 8 0 1 1-8-8z"/><text x="12" y="16.2" text-anchor="middle" font-size="6.6" font-weight="700" font-family="system-ui,sans-serif">15</text>',
    "rss": '<circle cx="6" cy="18" r="2.2"/><path d="M4 10.5a9.5 9.5 0 0 1 9.5 9.5h-2.6A6.9 6.9 0 0 0 4 13.1zM4 4.5A15.5 15.5 0 0 1 19.5 20h-2.6A12.9 12.9 0 0 0 4 7.1z"/>',
    "spotify": '<path d="M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zm4.6 14.4a.75.75 0 0 1-1 .25c-2.8-1.7-6.3-2.1-10.4-1.1a.75.75 0 1 1-.34-1.46c4.5-1 8.4-.6 11.5 1.3.36.22.47.69.25 1.01zm1.3-2.9a.9.9 0 0 1-1.25.3c-3.2-2-8.1-2.5-11.9-1.4a.9.9 0 1 1-.5-1.73c4.3-1.3 9.7-.7 13.4 1.6.43.26.56.82.29 1.24zm.1-3c-3.9-2.3-10.2-2.5-13.9-1.4a1.1 1.1 0 1 1-.63-2.1c4.2-1.3 11.2-1 15.6 1.6a1.1 1.1 0 0 1-1.1 1.9z"/>',
    "apple": '<path d="M12 2.2a7.8 7.8 0 0 0-3 15l.3-1.7a6.1 6.1 0 1 1 5.4 0l.3 1.7a7.8 7.8 0 0 0-3-15zm0 3.6a4.2 4.2 0 0 0-2.1 7.8l.3-1.8a2.6 2.6 0 1 1 3.6 0l.3 1.8A4.2 4.2 0 0 0 12 5.8zm0 4.4a1.6 1.6 0 1 0 0 3.2 1.6 1.6 0 0 0 0-3.2zm-1.2 4.4-.8 6.2c-.06.5.33.9.82.9h2.36c.5 0 .88-.4.82-.9l-.8-6.2z"/>',
    "menu": '<path d="M4 7h16M4 12h16M4 17h16"/>',
    "theme": '<path d="M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zm0 2v14a7 7 0 0 1 0-14z"/>',
    "doc": '<path d="M7 3h7l5 5v12a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1zm6 1.5V9h4.5M9 13h6M9 16.5h6"/>',
    "mail": '<path d="M4 6h16a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1zm0 1.5 8 6 8-6"/>',
    "spark": '<path d="M12 2.5l1.9 5.6 5.6 1.9-5.6 1.9L12 17.5l-1.9-5.6L4.5 10l5.6-1.9zM18.5 15l.9 2.6 2.6.9-2.6.9-.9 2.6-.9-2.6-2.6-.9 2.6-.9z"/>',
    "down": '<path d="M12 4v13m0 0-5.5-5.5M12 17l5.5-5.5"/>',
    "code": '<path d="M8.5 7 3.5 12l5 5M15.5 7l5 5-5 5M13.5 4.5l-3 15"/>',
}
STROKE = {"doc", "mail", "down", "menu", "code"}


def icon(name: str, cls: str = "ico") -> str:
    paint = 'fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"' \
        if name in STROKE else 'fill="currentColor"'
    return f'<svg class="{cls}" viewBox="0 0 24 24" {paint} aria-hidden="true" focusable="false">{ICON[name]}</svg>'


def mark_svg(cls: str = "mark", uid: str = "m") -> str:
    """Logo mark: an 'E' built from three level-meter bars on the brand gradient (no likeness of anyone)."""
    return (f'<svg class="{cls}" viewBox="0 0 64 64" aria-hidden="true" focusable="false"><defs>'
            f'<linearGradient id="{uid}g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#ff6a3d"/>'
            f'<stop offset=".55" stop-color="#e0459b"/><stop offset="1" stop-color="#7b5cff"/></linearGradient></defs>'
            f'<rect width="64" height="64" rx="18" fill="url(#{uid}g)"/><g fill="#fff">'
            '<rect x="17" y="15" width="7" height="34" rx="3.5"/><rect x="17" y="15" width="30" height="7" rx="3.5"/>'
            '<rect x="17" y="28.5" width="20" height="7" rx="3.5"/><rect x="17" y="42" width="26" height="7" rx="3.5"/>'
            '</g><circle cx="47" cy="32" r="3.5" fill="#fff" opacity=".85"/></svg>')


def cover_svg(seed: str, label: str, cls: str = "cover", title: str = "") -> str:
    """Deterministic generative cover: a waveform ring on a dark field, tinted per episode."""
    rnd = random.Random(seed)
    c1, c2, c3 = BRAND[int(hashlib.sha256(seed.encode()).hexdigest(), 16) % len(BRAND)]
    uid = "c" + hashlib.sha256(seed.encode()).hexdigest()[:6]
    n, bars, prev = 56, [], 0.5
    for k in range(n):
        prev = max(0.12, min(1.0, prev * 0.55 + rnd.random() * 0.6))
        bars.append(prev)
    ticks = []
    for k, h in enumerate(bars):
        ang = 2 * math.pi * k / n - math.pi / 2
        r0, r1 = 86, 86 + 64 * h
        x0, y0, x1, y1 = 200 + r0 * math.cos(ang), 200 + r0 * math.sin(ang), 200 + r1 * math.cos(ang), 200 + r1 * math.sin(ang)
        ticks.append(f'<line x1="{x0:.1f}" y1="{y0:.1f}" x2="{x1:.1f}" y2="{y1:.1f}"/>')
    t = f"<title>{e(title)}</title>" if title else ""
    hidden = "" if title else ' aria-hidden="true"'
    return (f'<svg class="{cls}" viewBox="0 0 400 400" role="img"{hidden} focusable="false">{t}<defs>'
            f'<radialGradient id="{uid}r" cx=".3" cy=".2" r="1"><stop offset="0" stop-color="#2a1d3a"/><stop offset="1" stop-color="#0c0a12"/></radialGradient>'
            f'<linearGradient id="{uid}l" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{c1}"/><stop offset=".5" stop-color="{c2}"/><stop offset="1" stop-color="{c3}"/></linearGradient>'
            f'<radialGradient id="{uid}o" cx=".5" cy=".5" r=".5"><stop offset="0" stop-color="{c2}" stop-opacity=".55"/><stop offset="1" stop-color="{c2}" stop-opacity="0"/></radialGradient></defs>'
            f'<rect width="400" height="400" fill="url(#{uid}r)"/><circle cx="200" cy="200" r="190" fill="url(#{uid}o)"/>'
            f'<g stroke="url(#{uid}l)" stroke-width="5" stroke-linecap="round">{"".join(ticks)}</g>'
            f'<circle cx="200" cy="200" r="70" fill="url(#{uid}l)"/>'
            f'<text x="200" y="214" text-anchor="middle" fill="#fff" font-family="Fraunces, Georgia, serif" font-size="44" font-weight="700">{e(label)}</text>'
            f'<text class="wm" x="28" y="48" fill="#fff" font-family="Fraunces, Georgia, serif" font-size="26" font-weight="700" letter-spacing=".5">Ask Eve</text>'
            '</svg>')


# ---------------------------------------------------------------- page shell

class Assets:
    def __init__(self, out: Path):
        self.map: dict[str, str] = {}
        if STATIC.exists():
            for src in STATIC.rglob("*"):
                if src.is_file():
                    rel = src.relative_to(STATIC).as_posix()
                    if rel.startswith("assets/"):
                        h = hashlib.sha256(src.read_bytes()).hexdigest()[:10]
                        rel_out = f"{src.parent.relative_to(STATIC).as_posix()}/{src.stem}.{h}{src.suffix}"
                    else:
                        rel_out = rel
                    (out / rel_out).parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(src, out / rel_out)
                    self.map[rel] = "/" + rel_out

    def __call__(self, rel: str) -> str:
        return self.map.get(rel, "/" + rel)


def og_image(slug: str | None) -> str:
    for rel in ([f"og/{slug}.jpg"] if slug else []) + ["og/default.jpg"]:
        if (STATIC / rel).exists():
            return f"{SITE}/{rel}"
    return f"{SITE}/favicon.svg"


# Header nav. About lives in the compact (mobile) menu and the footer only.
NAV = [("/", "Episodes"), ("/eve/", "Eve")]
MENU_EXTRA = [("/about/", "About")]
EVE_NAV = ("/eve/", "Eve")
SHOW_EVE_NAV = True
# The Eve hub's Topics section (Inside Eve's Mind) and Eve's takes stay hidden until David approves them.
SHOW_TOPICS = True     # /topics/ pages and Eve's long-form takes (index/takes.json, approved by David 2026-10-23)


def nav_items() -> list[tuple[str, str]]:
    return [x for x in NAV if SHOW_EVE_NAV or x != EVE_NAV]


def menu_items() -> list[tuple[str, str]]:
    return nav_items() + MENU_EXTRA


def nav_links(items: list[tuple[str, str]], active: str) -> str:
    return "".join(f'<a href="{h}"{" aria-current=page" if h == active else ""}>{n}</a>' for h, n in items)


def site_cfg() -> dict:
    f = ROOT / "site/site.json"
    return json.loads(f.read_text()) if f.exists() else {}


def head_subscribe(cfg: dict) -> str:
    """Icon-only subscribe links for the site header (every page). Hidden services stay hidden."""
    sub = cfg.get("subscribe", {})
    items = [("rss", F.FEED, "Subscribe with RSS", "")]
    if sub.get("apple_podcasts"):
        items.append(("apple", sub["apple_podcasts"], "Subscribe on Apple Podcasts", ""))
    if sub.get("spotify"):
        items.append(("spotify", sub["spotify"], "Follow on Spotify", " hsub--extra"))
    return '<div class="head-subs" role="group" aria-label="Subscribe">' + "".join(
        f'<a class="hsub hsub--{k}{cls}" href="{e(u)}" aria-label="{e(lab)}" title="{e(lab)}"'
        f'{"" if k == "rss" else " rel=noopener"}>{icon(k)}</a>' for k, u, lab, cls in items) + "</div>"


def page(A: Assets, *, title: str, desc: str, path: str, body: str, active: str = "", og_type: str = "website",
         og_slug: str | None = None, ld: dict | None = None, player: str = "", body_cls: str = "",
         linker: "Linker | None" = None, extra_css: str = "") -> str:
    url = SITE + I.localize(path)
    full = title if title == "Ask Eve" or title.startswith("Ask Eve") else f"{title} · Ask Eve"
    nav, menu = nav_links(nav_items(), active), nav_links(menu_items(), active)
    subs = head_subscribe(site_cfg())
    ldj = ('<script type="application/ld+json">' + json.dumps(ld).replace("</", "<\\/") + "</script>") if ld else ""
    return f"""<!doctype html>
<html{I.html_attrs()}>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>{e(full)}</title>
<meta name="description" content="{e(desc)}">
<link rel="canonical" href="{e(url)}">
{I.head_links(A, SITE, path)}
<link rel="alternate" type="application/rss+xml" title="Ask Eve (RSS)" href="{F.FEED}">
<meta name="color-scheme" content="light dark">
<meta name="theme-color" content="#f6f1e9" media="(prefers-color-scheme: light)">
<meta name="theme-color" content="#0e0c13" media="(prefers-color-scheme: dark)">
<meta name="referrer" content="strict-origin-when-cross-origin">
<meta property="og:site_name" content="Ask Eve">
<meta property="og:type" content="{og_type}">
<meta property="og:title" content="{e(title)}">
<meta property="og:description" content="{e(desc)}">
<meta property="og:url" content="{e(url)}">
<meta property="og:image" content="{og_image(og_slug)}">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta name="twitter:card" content="summary_large_image">
<link rel="icon" href="/favicon.svg" type="image/svg+xml">
<link rel="icon" href="/favicon-32.png" sizes="32x32" type="image/png">
<link rel="apple-touch-icon" href="/apple-touch-icon.png">
<link rel="preload" href="/fonts/fraunces-var.woff2" as="font" type="font/woff2" crossorigin>
<link rel="stylesheet" href="{A('assets/site.css')}">{f'\n<link rel="stylesheet" href="{A(extra_css)}">' if extra_css else ""}
<script src="{A('assets/theme.js')}"></script>
<script src="{A('assets/player.js')}" defer></script>
<script src="{A('assets/links.js')}" defer></script>
{ldj}
</head>
<body class="{body_cls}">
<a class="skip" href="#main">Skip to content</a>
<header class="site-head">
  <div class="wrap site-head__in">
    <a class="brand" href="/" aria-label="Ask Eve home">{mark_svg("mark", "h")}<span>Ask Eve</span></a>
    <nav class="site-nav" aria-label="Primary">{nav}</nav>
    {subs}
    {I.switcher(path)}
    <button class="theme-btn" type="button" data-theme-toggle aria-label="Color theme: system">{icon("theme")}<span class="theme-btn__label">Auto</span></button>
    <details class="menu" data-menu>
      <summary class="menu__btn" aria-label="Menu">{icon("menu")}</summary>
      <nav class="menu__panel" aria-label="Primary (menu)">{menu}</nav>
    </details>
  </div>
</header>
<main id="main">
{body}
</main>
<footer class="site-foot">
  <div class="wrap site-foot__in">
    <div class="site-foot__brand">{mark_svg("mark mark--sm", "f")}<div><strong>Ask Eve</strong><p>{e(TAGLINE)}</p></div></div>
    <nav aria-label="Footer">{nav_links(menu_items(), "")}<a href="/people/">People</a><a href="/feed.xml">RSS</a><a href="{REPO}" rel="noopener">GitHub</a><a href="mailto:{EMAIL}">{EMAIL}</a></nav>
    <p class="site-foot__fine">Eve is an AI. Her takes are opinions and can be wrong. AI-generated voices are labeled on every episode. No analytics, no trackers.</p>
  </div>
</footer>
{player}
{linker.data_script() if linker else ""}
</body>
</html>
"""


def subscribe_links(cfg: dict) -> str:
    sub = cfg.get("subscribe", {})
    links = [f'<a class="sub sub--rss" href="{F.FEED}" title="Subscribe with RSS" data-copy="{F.FEED}">{icon("rss")}<span>RSS feed</span></a>']
    if sub.get("spotify"):
        links.append(f'<a class="sub sub--spotify" href="{e(sub["spotify"])}" rel="noopener" title="Follow on Spotify">{icon("spotify")}<span>Spotify</span></a>')
    if sub.get("apple_podcasts"):
        links.append(f'<a class="sub sub--apple" href="{e(sub["apple_podcasts"])}" rel="noopener" title="Subscribe on Apple Podcasts">{icon("apple")}<span>Apple Podcasts</span></a>')
    return f'<div class="subs" aria-label="Subscribe">{"".join(links)}<button class="sub sub--copy" type="button" data-copy-feed="{F.FEED}">Copy feed URL</button></div>'


def ai_kind(ep: dict) -> tuple[str, str]:
    if not ep["ai_reconstructed"]:
        return "", ""
    if ep["david_real"]:
        return "hybrid", "Hybrid: real and AI voices"
    return "full", "Fully AI-reconstructed audio"


def chips(people: list[dict], limit: int | None = None, cls: str = "chips") -> str:
    if not people:
        return ""
    shown = people if limit is None else people[:limit]
    more = f'<li class="chip chip--more">+{len(people) - len(shown)}</li>' if len(people) > len(shown) else ""
    return f'<ul class="{cls}" aria-label="People">' + "".join(
        f'<li><a class="chip" href="/people/{p["slug"]}/">{e(p["name"])}</a></li>' for p in shown) + more + "</ul>"


def player_html(ep: dict, idle: bool = False) -> str:
    if not ep["audio_url"]:
        return ""
    dur = int(ep["duration_seconds"] or 0)
    return f"""<div class="player{' is-idle' if idle else ''}" id="player" data-slug="{e(ep['slug'])}" data-title="{e(ep['title'])}" data-duration="{dur}" role="region" aria-label="Audio player">
  <audio preload="none" src="{e(ep['audio_url'])}"></audio>
  <div class="player__in wrap">
    <a class="player__info" href="/episodes/{e(ep['slug'])}/">{mark_svg("mark player__mark", "p")}<span class="player__text"><span class="player__title">{e(ep['title'])}</span><span class="player__sub" data-chapter>{e(ep_label(ep))}</span></span></a>
    <div class="player__controls">
      <button class="pbtn" type="button" data-act="back" aria-label="Back 15 seconds">{icon("back")}</button>
      <button class="pbtn pbtn--play" type="button" data-act="toggle" aria-label="Play">{icon("play", "ico ico-play")}{icon("pause", "ico ico-pause")}</button>
      <button class="pbtn" type="button" data-act="fwd" aria-label="Forward 15 seconds">{icon("fwd")}</button>
    </div>
    <div class="player__scrub">
      <span class="tnum" data-cur>0:00</span>
      <input class="range" type="range" min="0" max="{dur}" step="1" value="0" aria-label="Seek" aria-valuetext="0:00 of {clock(dur)}">
      <span class="tnum" data-dur>{clock(dur)}</span>
    </div>
    <button class="pbtn pbtn--speed" type="button" data-act="speed" aria-label="Playback speed 1x">1×</button>
  </div>
</div>"""


CARD_SUMMARY_MAX = 160
_ABBREV = {"Mr", "Mrs", "Ms", "Dr", "St", "Jr", "Sr", "vs", "etc", "e.g", "i.e", "No"}


def card_first_sentence(text: str, limit: int = CARD_SUMMARY_MAX) -> str:
    """First sentence of `text`; if longer than `limit`, cut at a word boundary and end with an ellipsis."""
    text = " ".join((text or "").split())
    if not text:
        return ""
    sent = text
    for m in re.finditer(r"[.!?](?=\s+[A-Z0-9\"“'(])", text):
        word = text[:m.start()].rsplit(" ", 1)[-1]
        if m.group() == "." and (word in _ABBREV or re.fullmatch(r"(?:[A-Z]\.)*[A-Z]", word)):
            continue   # "Mr. Smith", "U.S. policy": not a sentence end
        sent = text[:m.end()]
        break
    if len(sent) <= limit:
        return sent
    cut = sent[:limit - 1].rsplit(" ", 1)[0].rstrip(",;:-–— ")
    return cut + "…"


def card_summary(fm: dict, sidecar: str | None, context: str) -> str:
    """1-2 line description under an episode card's title.
    Front-matter `summary:` wins, then site/site.json `summaries[slug]` (no transcript-sha churn), then the first sentence of `context`."""
    for s in (fm.get("summary"), sidecar):
        if isinstance(s, str) and s.strip():
            return " ".join(s.split())
    return card_first_sentence(context)


def ep_card(ep: dict) -> str:
    kind, label = ai_kind(ep)
    badge = f'<span class="badge badge--{kind}">{icon("spark")}{e(label)}</span>' if kind else ""
    return f"""<article class="card{' card--bonus' if is_bonus(ep) else ''}">
  <div class="card__art">{cover_svg(ep['key'], cover_mark(ep), "cover cover--card")}</div>
  <div class="card__body">
    <p class="eyebrow"><span class="card__num">{e(ep_label(ep))}</span> · <time datetime="{e(ep['date'])}">{human_date(ep['date'])}</time> · {human_duration(ep['duration_seconds'])}</p>
    <h3 class="card__title"><a href="/episodes/{e(ep['slug'])}/">{e(ep['title'])}</a></h3>
    <p class="card__blurb">{e(ep['card_summary'])}</p>
    <div class="card__badges">{bonus_badge(ep)}{badge}</div>
    {chips(ep['people_full'], 5)}
  </div>
</article>"""


def bonus_section(bonus: list[dict]) -> str:
    """Lower-key list of bonus episodes (behind-the-scenes extras), below the main episode list."""
    if not bonus:
        return ""
    rows = "".join(
        f'<li class="bonus-row"><a href="/episodes/{e(x["slug"])}/">'
        f'<span class="badge badge--bonus">Bonus</span><span class="bonus-row__title">{e(x["title"])}</span>'
        f'<span class="bonus-row__meta">{human_date(x["date"])} · {human_duration(x["duration_seconds"])}</span>'
        + (f'<span class="bonus-row__sum">{e(x["card_summary"])}</span>' if x.get("card_summary") else "") + '</a></li>'
        for x in bonus)
    return f"""
<section class="bonus" aria-labelledby="bonus-h">
  <div class="wrap">
    <h2 class="h3" id="bonus-h">Bonus</h2>
    <p class="hint">Extras from behind the scenes. Not part of the main run of episodes.</p>
    <ul class="bonus-list">{rows}</ul>
  </div>
</section>"""


# ---------------------------------------------------------------- pages

def home(A: Assets, eps: list[dict], people: list[dict]) -> str:
    cfg = eps[0]["cfg"] if eps else {}
    full = [x for x in eps if not is_bonus(x)]
    bonus = [x for x in eps if is_bonus(x)]
    # the featured slot is the latest FULL episode; bonus content is never featured
    latest = next((x for x in full if x["audio_url"]), full[0] if full else None)
    hero = ""
    if latest:
        kind, label = ai_kind(latest)
        play = (f'<button class="btn btn--primary" type="button" data-play aria-label="Play {e(latest["title"])}">'
                f'{icon("play")}<span>Play episode</span><span class="btn__meta">{human_duration(latest["duration_seconds"])}</span></button>'
                if latest["audio_url"] else "")
        hero = f"""<section class="hero">
  <div class="wrap hero__in">
    <div class="hero__text">
      <p class="eyebrow eyebrow--accent">Latest · {e(ep_label(latest))} · {human_date(latest['date'])}</p>
      <h1 class="hero__title"><a href="/episodes/{e(latest['slug'])}/">{e(latest['title'])}</a></h1>
      <p class="hero__blurb">{e(latest['blurb'])}</p>
      <div class="hero__actions">{play}<a class="btn btn--ghost" href="/episodes/{e(latest['slug'])}/">{icon("doc")}<span>Transcript &amp; chapters</span></a></div>
      {f'<p class="hero__ai">{icon("spark")}<span>{e(label)}. Every AI voice is labeled, line by line.</span></p>' if kind else ''}
    </div>
    <div class="hero__art">{cover_svg(latest['key'], cover_mark(latest), "cover cover--hero", f"Cover art for {latest['title']}")}</div>
  </div>
</section>"""
    cards = "".join(ep_card(x) for x in full) or '<p class="empty">The first episode is on its way.</p>'
    body = f"""{hero}
<section class="show">
  <div class="wrap show__in">
    <div>
      <h2 class="h2">The show</h2>
      <p class="lede">{e(F.SHOW['description'])}</p>
      <p>Eve is sharp, curious and opinionated, and she says so when she isn't sure. David brings the questions. Expect Sacks, Andreessen, Musk, Thiel, the All-In crowd, Pelosi's succession math and whatever moved this week.</p>
      {subscribe_links(cfg)}
    </div>
    <ul class="hosts">
      <li class="host host--david"><span class="avatar avatar--david" aria-hidden="true">D</span><div><strong>David</strong><span>Asks the questions. Real voice when he records, an AI narrator reading his words when he didn't.</span></div></li>
      <li class="host host--eve"><span class="avatar avatar--eve" aria-hidden="true">E</span><div><strong>Eve</strong><span>The AI co-host. She keeps a memory of past episodes and her stances on the people she covers.</span></div></li>
    </ul>
  </div>
</section>
<section class="episodes" aria-labelledby="eps-h">
  <div class="wrap">
    <h2 class="h2" id="eps-h">All episodes</h2>
    <div class="cards">{cards}</div>
  </div>
</section>{bonus_section(bonus)}
<section class="whos" aria-labelledby="who-h">
  <div class="wrap">
    <h2 class="h2" id="who-h">Who comes up</h2>
    {chips(people, None, "chips chips--lg")}
  </div>
</section>"""
    ld = {"@context": "https://schema.org", "@type": "PodcastSeries", "name": "Ask Eve", "url": SITE,
          "description": F.SHOW["description"], "webFeed": F.FEED, "author": {"@type": "Person", "name": F.SHOW["author"]}}
    return page(A, title="Ask Eve", desc=F.SHOW["description"], path="/", body=body, active="/", ld=ld,
                player=player_html(latest, idle=True) if latest else "", body_cls="has-player-idle")


def chapter_parts(title: str) -> tuple[str, str]:
    m = re.match(r"^\d{2}:\d{2} (?:David|Eve) \(([^)]+)\): (.*)$", title)
    if not m:
        return title, ""
    voice = "real" if "recorded" in m.group(1) else "narrator"
    t = m.group(2).strip()
    if t and t[-1] not in ".?!…":
        t += "…"
    return t, voice


VOICE_BADGE = {"real": ("Real voice", "David's recorded voice"),
               "narrator": ("AI narrator", "David's words, read by an AI narrator voice"),
               "eve": ("AI voice", "Eve's words, voiced with xAI text-to-speech"),
               "gap": ("Not recovered", "This reply was not recovered; nothing was invented or voiced")}


def turn_html(t: dict, L: Linker = NO_LINKS) -> str:
    who = t["speaker"].lower()
    v = t["voice"]
    badge = f'<span class="vb vb--{v}" title="{e(VOICE_BADGE[v][1])}">{VOICE_BADGE[v][0]}</span>' if v else ""
    attrs = f' data-start="{t["start"]:.2f}" data-end="{(t["end"] if t["end"] is not None else t["start"]):.2f}"' \
        if t["start"] is not None and not t["gap"] else ""
    tbtn = (f'<a class="turn__time tnum" href="#t={int(t["start"])}" data-seek="{t["start"]:.2f}" '
            f'aria-label="Play from {clock(t["start"])}">{clock(t["start"])}</a>') if attrs else ""
    body = ('<p class="turn__gap">Eve\'s reply was not recovered. It is marked as a gap, not invented, and not voiced.</p>'
            if t["gap"] else L.html(blocks(t["text"])))   # first mention per turn
    meta = ""
    if t["meta"]:
        head, rest = t["meta"][0], t["meta"][1:]
        meta = (f'<details class="turn__meta"><summary>{inline(head.rstrip(":"))}</summary>'
                f'{blocks(chr(10).join(rest))}</details>')
    return (f'<li class="turn turn--{who}{" turn--gap" if t["gap"] else ""}" id="turn-{t["i"]}"{attrs}>'
            f'<div class="turn__head"><span class="avatar avatar--{who}" aria-hidden="true">{t["speaker"][0]}</span>'
            f'<span class="turn__who">{t["speaker"]}</span>{badge}'
            f'<span class="turn__clock" title="Wall-clock time of this turn in the original session (UTC)">{t["clock"]} UTC</span>{tbtn}</div>'
            f'<div class="turn__body">{body}</div>{meta}</li>')


def context_html(ep: dict, L: Linker = NO_LINKS) -> str:
    """The episode's front-matter Context note and Topics list, shown under the title and above the transcript."""
    if not ep.get("context") and not ep.get("topics"):
        return ""
    seen: set = set()   # the topics list is one section: first mention only
    ctx = (f'<h2 class="ep-context__h" id="ctx-h">Context</h2><p class="ep-context__p">{L.text(ep["context"])}</p>'
           if ep.get("context") else "")
    tops = (f'<h2 class="ep-context__h" id="topics-h">Topics</h2><ul class="ep-topics">'
            + "".join(f"<li>{L.text(t, seen)}</li>" for t in ep["topics"]) + "</ul>") if ep.get("topics") else ""
    return f'<section class="wrap ep-context" aria-labelledby="ctx-h">{ctx}{tops}</section>\n'


def episode_page(A: Assets, ep: dict, eps: list[dict]) -> str:
    L = Linker(load_links(), f"/episodes/{ep['slug']}/")
    kind, label = ai_kind(ep)
    disclosure = ""
    if ep["ai_reconstructed"]:
        legend = []
        if ep["david_real"]:
            legend.append(f'<li><span class="vb vb--real">Real voice</span> David, recorded ({ep["david_real"]} lines)</li>')
        if ep["david_narrator"]:
            legend.append(f'<li><span class="vb vb--narrator">AI narrator</span> David\'s words read by an AI voice ({ep["david_narrator"]} lines)</li>')
        legend.append('<li><span class="vb vb--eve">AI voice</span> Eve, voiced with xAI text-to-speech</li>')
        if any(t["gap"] for t in ep["turns"]):
            legend.append('<li><span class="vb vb--gap">Not recovered</span> missing replies, marked and never invented</li>')
        disclosure = f"""<aside class="disclosure disclosure--{kind}" role="note" aria-labelledby="disc-h">
  <div class="disclosure__icon">{icon("spark")}</div>
  <div class="disclosure__body">
    <p class="disclosure__title" id="disc-h">{e(label)}</p>
    <p>{e(_disc(ep['ai_disclosure']))}</p>
    <ul class="legend">{''.join(legend)}</ul>
  </div>
</aside>"""
    chs = []
    for c in ep["chapters"]:
        t, v = chapter_parts(c["title"])
        vb = f'<span class="vb vb--{v} vb--xs">{VOICE_BADGE[v][0]}</span>' if v else ""
        chs.append(f'<li><a class="chap" href="#t={int(c["start_seconds"])}" data-seek="{float(c["start_seconds"]):.2f}">'
                   f'<span class="chap__t tnum">{clock(c["start_seconds"])}</span><span class="chap__title">{e(t)}{vb}</span></a></li>')
    chapters = (f'<details class="side-box chapters" data-open-wide><summary><h2 class="side-h">Chapters <span class="count">{len(chs)}</span></h2></summary>'
                f'<ol class="chaps">{"".join(chs)}</ol></details>') if chs else ""
    ppl = ""
    if ep["people_full"]:
        items = []
        for p in ep["people_full"]:
            x = next(x for x in p["episodes"] if x["ep"] is ep)
            n = len(x["moments"])
            items.append(f'<li><a class="chip" href="/people/{p["slug"]}/">{e(p["name"])}'
                         f'{f" <span class=chip__n>{n}</span>" if n else ""}</a></li>')
        ppl = f'<section class="side-box"><h2 class="side-h">People in this episode</h2><ul class="chips">{"".join(items)}</ul></section>'
    fm = ep["fm"]
    notes = "".join(f"<p><strong>{k.title()}.</strong> {inline(fm[k])}</p>" for k in ("note", "redactions") if fm.get(k))
    details = f"""<section class="side-box details-box"><h2 class="side-h">Episode details</h2><dl class="dl">
<dt>Recorded</dt><dd>{human_date(ep['date'])}</dd><dt>Length</dt><dd>{human_duration(ep['duration_seconds'])}</dd>
<dt>Turns</dt><dd>{len(ep['turns'])}</dd></dl>
<p class="side-links"><a href="transcript.txt">{icon("doc")}Plain-text transcript</a>{f'<a href="{e(ep["audio_url"])}" download>{icon("down")}Download MP3</a>' if ep['audio_url'] else ''}</p>
{f'<details class="notes"><summary>Transcript notes</summary>{notes}</details>' if notes else ''}</section>"""
    others = [x for x in eps if x is not ep]
    more = (f'<section class="more"><div class="wrap"><h2 class="h2">More episodes</h2><div class="cards">'
            f'{"".join(ep_card(x) for x in others[:3])}</div></div></section>') if others else ""
    play = (f'<button class="btn btn--primary" type="button" data-play>{icon("play")}<span>Play episode</span>'
            f'<span class="btn__meta">{human_duration(ep["duration_seconds"])}</span></button>') if ep["audio_url"] else ""
    body = f"""<article class="ep">
<header class="ep-hero">
  <div class="wrap ep-hero__in">
    <div class="ep-hero__art">{cover_svg(ep['key'], cover_mark(ep), "cover cover--ep", f"Cover art for {ep['title']}")}</div>
    <div class="ep-hero__text">
      <p class="eyebrow eyebrow--accent">{e(ep_label(ep))} · <time datetime="{e(ep['date'])}">{human_date(ep['date'])}</time> · {human_duration(ep['duration_seconds'])}</p>
      {f'<p class="ep-badges">{bonus_badge(ep)}</p>' if is_bonus(ep) else ''}
      <h1 class="ep-title">{e(ep['title'])}</h1>
      {'' if ep.get('context') else f'<p class="ep-blurb">{e(ep["blurb"])}</p>'}
      <div class="hero__actions">{play}</div>
    </div>
  </div>
</header>
{context_html(ep, L)}<div class="wrap">{disclosure}</div>
<div class="wrap ep-layout">
  <aside class="ep-side" aria-label="Episode navigation">{chapters}{ppl}{details}</aside>
  <section class="transcript" aria-labelledby="tr-h">
    <div class="transcript__head"><h2 class="h2" id="tr-h">Transcript</h2><p class="hint">Tap any line to play from there. The current line follows along as you listen.</p></div>
    <ol class="turns" data-turns>{''.join(turn_html(t, L) for t in ep['turns'])}</ol>
  </section>
</div>
<button class="follow" type="button" data-follow hidden>{icon("down")}<span>Back to the current line</span></button>
</article>
{more}"""
    desc = (ep["blurb"] or F.description(ep, ep["people"]))[:300]
    ld = {"@context": "https://schema.org", "@type": "PodcastEpisode", "name": ep["title"], "url": f"{SITE}/episodes/{ep['slug']}/",
          "datePublished": ep["date"], "description": desc, "episodeNumber": ep["episode_number"] if not is_bonus(ep) else None,
          "partOfSeries": {"@type": "PodcastSeries", "name": "Ask Eve", "url": SITE},
          "timeRequired": f"PT{int(ep['duration_seconds'] or 0) // 60}M"}
    if ep["audio_url"]:
        ld["associatedMedia"] = {"@type": "MediaObject", "contentUrl": ep["audio_url"]}
    return page(A, title=ep["title"], desc=desc, path=f"/episodes/{ep['slug']}/", body=body, active="/",
                og_type="article", og_slug=ep["slug"], ld=ld, player=player_html(ep), body_cls="has-player", linker=L)


def pcards(people: list[dict]) -> str:
    cards = []
    for p in people:
        n = sum(len(x["moments"]) for x in p["episodes"])
        ne = len(p["episodes"])
        stat = (f"{n} discussion{'s' * (n != 1)} · {ne} episode{'s' * (ne != 1)}" if ne else "Not discussed in an episode yet")
        cards.append(f"""<li class="pcard"><a href="/people/{p['slug']}/">
<span class="pcard__initials" aria-hidden="true">{e(''.join(w[0] for w in p['name'].split()[:2]))}</span>
<span class="pcard__name">{e(p['name'])}</span>
<span class="pcard__stance">{e(p['stance'])}</span>
<span class="pcard__stat">{stat}</span></a></li>""")
    return f'<ul class="pgrid">{"".join(cards)}</ul>'


def people_index(A: Assets, people: list[dict]) -> str:
    body = f"""<section class="page-head"><div class="wrap"><p class="eyebrow eyebrow--accent"><a href="/eve/#mind">Inside Eve's Mind</a> · People</p>
<h1 class="page-title">Who comes up on Ask Eve</h1>
<p class="lede">Every person the show covers, Eve's take on them, and every real discussion about them, summarized and linked to the second it starts.</p>
<p class="fine">Stances are Eve's opinions, an AI's read, and she updates them as the evidence changes.</p></div></section>
<section class="wrap">{pcards(people)}</section>"""
    return page(A, title="People", desc="Everyone discussed on Ask Eve, with Eve's stance and every discussion, summarized and timestamped.",
                path="/people/", body=body, active="/eve/")


def first_sentence(t: str, limit: int = 220) -> str:
    t = (t or "").strip()
    m = re.match(r"(.+?[.!?])(\s|$)", t)
    if m and len(m.group(1)) < 60:              # a very short opener reads better with the next sentence
        m = re.match(r"(.+?[.!?]\s+.+?[.!?])(\s|$)", t) or m
    t = m.group(1) if m else t
    return t if len(t) <= limit else t[:limit].rsplit(" ", 1)[0] + "…"


MIND_PEOPLE_SHOWN = 6
MIND_TOPICS_SHOWN = 4


def fold(items: list[str], label: str, cls: str = "") -> str:
    """Collapse items behind a native <details> (no script): 'Show 3 more quotes'."""
    if not items:
        return ""
    return (f'<details class="fold {cls}"><summary><span>{e(label)}</span></summary>'
            f'<div class="fold__in">{"".join(items)}</div></details>')


def mind_html(eps: list[dict], people: list[dict], topics: list[dict] | None = None) -> str:
    """Inside Eve's Mind, a section of /eve/: her memories (episodes, bonus included), the people she has
    takes on, and the topics she has thought through (SHOW_TOPICS)."""
    mems = []
    for ep in eps:
        n = len(ep.get("moments") or [])
        who = ep.get("people_full") or []
        stat = " · ".join(x for x in [human_date(ep["date"]), human_duration(ep["duration_seconds"]),
                                      f"{n} discussion{'s' * (n != 1)} indexed" if n else ""] if x)
        names = "".join(f'<li><a class="chip" href="/people/{p["slug"]}/">{e(p["name"])}</a></li>' for p in who[:6])
        mems.append(f"""<li class="mem{' mem--bonus' if is_bonus(ep) else ''}">
<p class="eyebrow">{bonus_badge(ep) if is_bonus(ep) else f'<span class="card__num">{e(ep_label(ep))}</span>'}</p>
<h4 class="mem__t"><a href="/episodes/{e(ep['slug'])}/">{e(ep['title'])}</a></h4>
<p class="mem__meta">{stat}</p>
{f'<p class="mem__b">{e(first_sentence(ep["blurb"]))}</p>' if ep.get("blurb") else ""}
{f'<ul class="chips">{names}</ul>' if names else ""}</li>""")
    discussed = [p for p in people if p["episodes"]]
    cards = pcards(discussed).removeprefix('<ul class="pgrid">').removesuffix("</ul>").split("</li>")
    cards = [c + "</li>" for c in cards if c.strip()]
    shown, rest = cards[:MIND_PEOPLE_SHOWN], cards[MIND_PEOPLE_SHOWN:]
    more_people = (f'<details class="fold"><summary><span>Show {len(rest)} more people</span></summary>'
                   f'<ul class="pgrid">{"".join(rest)}</ul></details>') if rest else ""
    tcs = [tcard(t) for t in topics or []] if SHOW_TOPICS else []
    topics_html = (f'''
<h3 class="eve-h3" id="topics">Topics</h3>
<p class="hint">The ideas and fights Eve keeps coming back to, with her take on each and every moment they came up.</p>
<ul class="tgrid">{''.join(tcs[:MIND_TOPICS_SHOWN])}</ul>'''
                   + (f'<details class="fold"><summary><span>Show {len(tcs) - MIND_TOPICS_SHOWN} more topics</span></summary>'
                      f'<ul class="tgrid">{"".join(tcs[MIND_TOPICS_SHOWN:])}</ul></details>' if len(tcs) > MIND_TOPICS_SHOWN else "")
                   + f'<p class="eve-all"><a class="btn btn--ghost" href="/topics/">All {len(tcs)} topics</a></p>') if tcs else ""
    return f"""<p class="eve-sec__body">What Eve carries from one conversation to the next: a searchable memory of every episode, and a running stance on each person the show covers. Her stances are opinions and change as the evidence does.</p>
<h3 class="eve-h3" id="memories">Memories</h3>
<p class="hint">Every episode, searchable mid-conversation: the full transcript, plus each discussion of a person, summarized and timestamped.</p>
<ul class="mems">{''.join(mems) or '<li class="empty">The first episode is on its way.</li>'}</ul>
<h3 class="eve-h3" id="people">People</h3>
<p class="hint">Who Eve has discussed, and her take on each.</p>
<ul class="pgrid">{''.join(shown)}</ul>{more_people}
<p class="eve-all"><a class="btn btn--ghost" href="/people/">All {len(people)} people</a></p>{topics_html}"""


def _qa_exchange_html(dq: str, ea: str, L: Linker = NO_LINKS) -> str:
    return (f'<div class="pm__qa">'
            f'<p class="pm__q"><span class="qa-spk qa-spk--david">David:</span> '
            f'<span class="pm__q-text">{L.text(dq)}</span></p>'
            f'<p class="pm__a"><span class="qa-spk qa-spk--eve">Eve:</span> '
            f'<span class="pm__a-text">{L.text(ea)}</span></p></div>')


def moment_html(ep: dict, m: dict, L: Linker = NO_LINKS) -> str:
    """One discussion block: episode, time range, Q&A exchange (or summary), and a deep link that starts the audio there."""
    t = m["first_turn"]
    if m["start"] is not None:
        href, when = f"/episodes/{ep['slug']}/#t={int(m['start'])}", clock(m["start"])
        rng = f"{when}–{clock(m['end'])}" if m["end"] is not None else when
        cta = f"Listen from {when}"
    else:
        href, rng, cta = f"/episodes/{ep['slug']}/#turn-{t['i']}", m["clock"][:5], f"Read from {m['clock'][:5]}"
    tags = "".join(f'<li class="pm__tag">{e(x)}</li>' for x in m.get("topics", []))
    body = (_qa_exchange_html(m["david_q"], m["eve_a"], L)
            if m.get("david_q") and m.get("eve_a")
            else f'<p class="pm__sum">{L.text(m["summary"])}</p>')
    return (f'<li class="pm"><p class="pm__meta"><span class="pm__ep">{e(ep["title"])}</span>'
            f'<span class="pm__t tnum">{rng}</span></p>'
            f'{body}'
            f'<div class="pm__foot"><a class="pm__listen" href="{e(href)}">{icon("play")}<span>{cta}</span></a>'
            f'{f"<ul class=pm__tags>{tags}</ul>" if tags else ""}</div></li>')


def take_html(x: dict, L: Linker, heading: str = "Eve's take") -> str:
    """Eve's long-form take, structured as a David/Eve Q&A interview when `qa` is present. Each ref's anchor
    phrase becomes a silent link that opens the episode at the second she said it; names get auto-link menus."""
    seen: set = set()
    paras = []
    for text in x["take"]:
        h = e(text)
        for r in x["refs"]:
            a = r.get("anchor")
            if a and a in text:
                h = h.replace(e(a), f'<a class="cite" href="{e(r["href"])}" title="{e(r["label"])} · {e(r["ep"]["title"])} at '
                                    f'{e(r["when"])}">{e(a)}</a>', 1)
        paras.append(f"<p>{L.html(h, seen)}</p>")
    if x.get("qa"):
        idx, turns = 0, []
        for turn in x["qa"]:
            n = len(turn.get("a", []))
            a_html = "".join(paras[idx:idx + n])
            idx += n
            turns.append(
                f'<div class="qa-turn">'
                f'<p class="qa-q"><span class="qa-spk qa-spk--david">David:</span> '
                f'<span class="qa-q__text">{L.text(turn["q"])}</span></p>'
                f'<div class="qa-a"><span class="qa-spk qa-spk--eve">Eve:</span>'
                f'<div class="qa-a__body">{a_html}</div></div></div>'
            )
        body = f'<div class="take__body take__qa">{"".join(turns)}</div>'
    else:
        body = f'<div class="take__body">{"".join(paras)}</div>'
    return (f'<section class="take" aria-labelledby="take-h"><h2 class="take__h" id="take-h">{e(heading)}</h2>'
            f'{body}'
            f'<p class="take__note">An AI\'s opinion, not a fact. Linked phrases open the episode at the moment Eve said it.</p></section>')


PLINK_KIND = {"x": "X", "wikipedia": "Wikipedia"}


def plinks(links: list[dict]) -> str:
    if not links:
        return ""
    return ('<ul class="plinks" aria-label="Elsewhere online">' + "".join(
        f'<li><a class="plink plink--{e(l["platform"])}" href="{e(l["url"])}" target="_blank" rel="noopener">{e(l["label"])}</a></li>'
        for l in links) + "</ul>")


def topic_chips(topics: list[dict]) -> str:
    return ('<ul class="chips" aria-label="Topics">' + "".join(
        f'<li><a class="chip" href="/topics/{e(t["slug"])}/">{e(t["title"])}</a></li>' for t in topics) + "</ul>") if topics else ""


def ref_moments(x: dict, people_slugs: set[str] | None = None, L: Linker = NO_LINKS) -> list[tuple[dict, list[str]]]:
    """Discussion blocks for a take's refs, grouped by episode in transcript order. Each block renders a
    David/Eve Q&A exchange for that ref (or falls back to the covering moment's summary)."""
    groups: dict[str, tuple[dict, list[str]]] = {}
    used: set[int] = set()
    for r in sorted(x["refs"], key=lambda r: (r["ep"]["slug"], r["turn"]["i"])):
        ep, t = r["ep"], r["turn"]
        cover = [m for m in ep.get("moments") or [] if m["start_turn"] <= t["i"] <= m["end_turn"]]
        cover.sort(key=lambda m: (people_slugs is not None and m["person"] not in people_slugs, m["end_turn"] - m["start_turn"]))
        m = cover[0] if cover else None
        fresh = m if (m and id(m) not in used) else None
        if m:
            used.add(id(m))
        dq = r.get("david_q") or (fresh.get("david_q") if fresh else "")
        ea = r.get("eve_a") or (fresh.get("eve_a") if fresh else "")
        summ = fresh["summary"] if fresh else ""
        body = (_qa_exchange_html(dq, ea, L) if (dq and ea)
                else (f'<p class="pm__sum">{L.text(summ)}</p>' if summ else ""))
        groups.setdefault(ep["slug"], (ep, []))[1].append(
            f'<li class="pm"><p class="pm__meta"><span class="pm__ep">{e(ep["title"])}</span>'
            f'<span class="pm__t tnum">{e(r["when"])}</span></p>'
            f'<p class="pm__label">{e(r["label"])}</p>'
            + body
            + f'<div class="pm__foot"><a class="pm__listen" href="{e(r["href"])}">{icon("play")}<span>'
            f'{"Listen" if "#t=" in r["href"] else "Read"} from {e(r["when"])}</span></a></div></li>')
    order = sorted(groups.values(), key=lambda g: g[0]["published_at"] or g[0]["date"])
    return order


def pep_section(ep: dict, items: list[str], noun: str = "discussion") -> str:
    cnt = f"{len(items)} {noun}{'s' * (len(items) != 1)}"
    return f"""<section class="pep">
<div class="pep__head">{cover_svg(ep['key'], cover_mark(ep), "cover cover--thumb")}
<div><p class="eyebrow">{e(ep_label(ep))} · {human_date(ep['date'])} · {cnt}</p><h3 class="pep__title"><a href="/episodes/{e(ep['slug'])}/">{e(ep['title'])}</a></h3></div></div>
<ol class="pms">{''.join(items)}</ol></section>"""


def person_page(A: Assets, p: dict) -> str:
    L = Linker(load_links(), f"/people/{p['slug']}/", exclude={p["slug"]})   # don't link the page's own subject
    x = p.get("take")
    if x:
        return person_take_page(A, p, x, L)
    sections = []
    for x in p["episodes"]:
        ep, ms = x["ep"], x["moments"]
        cnt = f"{len(ms)} discussion{'s' * (len(ms) != 1)}"
        sections.append(f"""<section class="pep">
<div class="pep__head">{cover_svg(ep['key'], cover_mark(ep), "cover cover--thumb")}
<div><p class="eyebrow">{e(ep_label(ep))} · {human_date(ep['date'])} · {cnt}</p><h2 class="pep__title"><a href="/episodes/{e(ep['slug'])}/">{e(ep['title'])}</a></h2></div></div>
<ol class="pms">{''.join(moment_html(ep, m, L) for m in ms)}</ol></section>""")
    if not sections:
        sections.append('<p class="empty">No episode discusses this person yet.</p>')
    stance = (f'<figure class="stance"><blockquote>{L.text(p["stance"])}</blockquote><figcaption>Eve\'s current take. '
              f'An AI\'s opinion, not a fact.</figcaption></figure>') if p["stance"] else ""
    body = f"""<section class="page-head"><div class="wrap"><p class="eyebrow eyebrow--accent"><a href="/eve/#mind">Inside Eve's Mind</a> · <a href="/people/">People</a></p>
<h1 class="page-title">{e(p['name'])}</h1>{stance}</div></section>
<section class="wrap person">{''.join(sections)}</section>"""
    desc = f"{p['name']} on Ask Eve: Eve's take and every discussion about them, summarized with timestamps."
    return page(A, title=p["name"], desc=desc, path=f"/people/{p['slug']}/", body=body, active="/eve/", linker=L)


def person_take_page(A: Assets, p: dict, x: dict, L: Linker) -> str:
    """A person with an approved long-form take: lede, Eve's take, where to find them, topics, discussions."""
    take = take_html(x, L)          # first, so the take gets first mentions for auto-links
    sections = [pep_section(y["ep"], [moment_html(y["ep"], m, L) for m in y["moments"]]) for y in p["episodes"]]
    if not sections:               # no indexed discussions yet: list the moments the take cites
        sections = [pep_section(ep, items, "moment") for ep, items in ref_moments(x, L=L)]
    role = f'<p class="role">{e(x["role"])}</p>' if x.get("role") else ""
    lede = f'<p class="lede take__lede">{e(p["stance"])}</p>' if p.get("stance") else ""
    topics = (f'<section class="ptopics"><h2 class="sec-h">Topics</h2>{topic_chips(p.get("topics", []))}</section>'
              if p.get("topics") else "")
    disc = (f'<h2 class="sec-h">Discussions</h2>' + "".join(sections)) if sections else ""
    body = f"""<section class="page-head"><div class="wrap"><p class="eyebrow eyebrow--accent"><a href="/eve/#mind">Inside Eve's Mind</a> · <a href="/people/">People</a></p>
<h1 class="page-title">{e(p['name'])}</h1>{role}{lede}{plinks(x.get("links", []))}</div></section>
<div class="wrap person person--take">{take}{topics}<div class="pdisc">{disc}</div></div>"""
    desc = f"Eve's take on {p['name']}, with links to every moment the show discusses them."
    return page(A, title=p["name"], desc=desc, path=f"/people/{p['slug']}/", body=body, active="/eve/", linker=L)


def tcard(t: dict) -> str:
    n = len(t["refs"])
    who = ", ".join(p["name"] for p in t["people_full"][:3]) + (f" +{len(t['people_full']) - 3}" if len(t["people_full"]) > 3 else "")
    stat = " · ".join(x for x in [f"{n} moment{'s' * (n != 1)}" if n else "", who] if x)
    return (f'<li class="tcard"><a href="/topics/{e(t["slug"])}/"><span class="tcard__t">{e(t["title"])}</span>'
            f'<span class="tcard__d">{e(t["description"])}</span><span class="tcard__s">{e(stat)}</span></a></li>')


def topics_index(A: Assets, topics: list[dict]) -> str:
    body = f"""<section class="page-head"><div class="wrap"><p class="eyebrow eyebrow--accent"><a href="/eve/#mind">Inside Eve's Mind</a> · Topics</p>
<h1 class="page-title">What Eve thinks about</h1>
<p class="lede">The ideas and fights the show keeps returning to. Each has Eve's take, the people involved, and every moment it came up, linked to the second.</p>
<p class="fine">Takes are Eve's opinions, an AI's read, and she updates them as the evidence changes.</p></div></section>
<section class="wrap"><ul class="tgrid tgrid--page">{''.join(tcard(t) for t in topics)}</ul></section>"""
    return page(A, title="Topics", desc="The topics Eve has thought through on Ask Eve, with her take and every moment they came up.",
                path="/topics/", body=body, active="/eve/")


def topic_page(A: Assets, t: dict) -> str:
    path = f"/topics/{t['slug']}/"
    L = Linker(load_links(), path, exclude={"topic:" + t["slug"]})
    take = take_html(t, L)
    people_h = (f'<section class="ptopics"><h2 class="sec-h">People</h2>{chips(t["people_full"])}</section>'
                if t["people_full"] else "")
    sections = [pep_section(ep, items, "moment") for ep, items in ref_moments(t, {p["slug"] for p in t["people_full"]}, L=L)]
    disc = (f'<h2 class="sec-h">Where it came up</h2>' + "".join(sections)) if sections else ""
    body = f"""<section class="page-head"><div class="wrap"><p class="eyebrow eyebrow--accent"><a href="/eve/#mind">Inside Eve's Mind</a> · <a href="/topics/">Topics</a></p>
<h1 class="page-title">{e(t['title'])}</h1><p class="lede take__lede">{e(t['description'])}</p></div></section>
<div class="wrap person person--take">{take}{people_h}<div class="pdisc">{disc}</div></div>"""
    return page(A, title=t["title"], desc=t["description"], path=path, body=body, active="/eve/", linker=L)


# ---------------------------------------------------------------- Meet Eve (/eve/)
EVE_FILE = ROOT / "index/eve.json"
_WS = re.compile(r"\s+")


def _norm(s: str) -> str:
    return _WS.sub(" ", s.replace("’", "'").replace("“", '"').replace("”", '"')).strip()


class EveSources:
    """Resolves an eve.json citation (episode slug + wall-clock time + speaker + verbatim excerpt) to the
    transcript turn and its audio offset. Citations of episodes not on the site (e.g. a pending episode)
    resolve to None and are left out. A citation whose excerpt isn't verbatim in that turn is an error."""

    def __init__(self, eps: list[dict]):
        self.eps = {ep["slug"]: ep for ep in eps}

    def resolve(self, src: dict) -> dict | None:
        ep = self.eps.get(src["episode_slug"])
        if ep is None or src.get("pending"):
            return None
        want = _norm(src["quote_excerpt"].replace(" [sic]", ""))   # [sic] is an editorial mark, not in the transcript
        for t in ep["turns"]:
            if t["clock_full"] == src["clock_time"] and t["speaker"] == src["speaker"] and want in _norm(t["text"]):
                if t["start"] is not None and not t["gap"]:
                    href, when = f"/episodes/{ep['slug']}/#t={int(t['start'])}", clock(t["start"])
                else:
                    href, when = f"/episodes/{ep['slug']}/#turn-{t['i']}", t["clock"] + " UTC"
                return {"ep": ep, "turn": t, "href": href, "when": when, "gap": t["gap"]}
        raise ValueError(f"eve.json citation not found verbatim: {src['episode_slug']} {src['clock_time']} {src['speaker']}: {want[:60]}")


# /eve/ section order and short headings. Sections not listed (or missing from eve.json) are skipped.
EVE_ORDER = [("who-eve-is", "Who Eve is"), ("mind", "Inside Eve's Mind"), ("self-assessment", "Personality"),
             ("name", "Her name"), ("lens-and-values", "Lens and values"), ("how-eve-works", "How she works"),
             ("changed-mind", "Changed her mind"), ("persona-notes", "Persona notes")]


def eve_page(A: Assets, eps: list[dict], people: list[dict] | None = None, topics: list[dict] | None = None) -> str | None:
    """/eve/: the Eve hub. Who she is, Inside Eve's Mind (memories + people), then her profile in her own
    words. The strongest 1-3 quotes per section stay visible; the rest fold into <details>. Every quote is
    verified verbatim against the transcript at build time and links to the second it was said."""
    if not EVE_FILE.exists():
        return None
    data = json.loads(EVE_FILE.read_text())
    if not data.get("approved"):
        return None
    R = EveSources(eps)
    L = Linker(load_links(), "/eve/")
    if topics is None:
        topics = attach_takes(eps, people or [])
    published = set(R.eps)

    def clean(text: str) -> str:
        # drop sentences that point at an episode that isn't published yet
        if all(x in published for x in data.get("episodes", {})):
            return text
        parts = re.split(r"(?<=[.!?])\s+", text)
        return " ".join(x for x in parts if not re.search(r"\(pending[^)]*\)|\bepisode 2\b", x, re.I))

    def srcs(lst):
        return [(x, r) for x in lst if (r := R.resolve(x))]

    def cite(src, r):
        ep = r["ep"]
        lab = f"{src['speaker']} · {e(ep['title'])} · {e(r['when'])}"
        return (f'<a class="q__src" href="{e(r["href"])}" aria-label="{e(src["speaker"])} in {e(ep["title"])} at {e(r["when"])}: '
                f'open the transcript there">{icon("play")}<span>{lab}</span></a>')

    def quote(src, r, cls="q"):
        ctx_html = (f'<p class="q__ctx"><span class="q__ctx-k">Context:</span> '
                    f'<span class="q__ctx-v">{L.text(src["context"])}</span></p>') if src.get("context") else ""
        if r["gap"]:
            body = '<p class="q__gap">Eve\'s reply here was not recovered. It is marked as a gap in the transcript, never invented.</p>'
        else:
            body = f'<blockquote><p>“{L.text(src["quote_excerpt"])}”</p></blockquote>'
        return f'<figure class="{cls} {cls}--{src["speaker"].lower()}">{ctx_html}{body}<figcaption>{cite(src, r)}</figcaption></figure>'

    def quotes(lst, keep=2, cls="q", noun="quote"):
        qs = [quote(x, r, cls) for x, r in lst]
        if not qs:
            return ""
        n = len(qs) - keep
        return (f'<div class="qs">{"".join(qs[:keep])}</div>'
                + fold(qs[keep:], f"Show {n} more {noun}{'s' * (n != 1)}", "fold--qs"))

    def rpg_sheet_html(sheet):
        if not sheet:
            return ""
        vitals = (
            f'<dl class="rpg-vitals">'
            f'<div class="rpg-vital"><dt>Class &amp; Role</dt><dd>{L.text(sheet.get("class_role", ""))}</dd></div>'
            f'<div class="rpg-vital"><dt>Origin</dt><dd>{L.text(sheet.get("origin", ""))}</dd></div>'
            f'<div class="rpg-vital"><dt>Voice Preset</dt><dd>{L.text(sheet.get("voice", ""))}</dd></div>'
            f'<div class="rpg-vital rpg-vital--wide"><dt>Core Alignment Thesis</dt>'
            f'<dd>“{L.text(sheet.get("alignment", ""))}”</dd></div>'
            f'</dl>'
        )
        stat_cards = []
        for st in sheet.get("stats", []):
            score = int(st.get("score", 85))
            src = st.get("source")
            r = R.resolve(src) if src else None
            src_pill = (f'<a class="rpg-stat__src tnum" href="{e(r["href"])}" '
                        f'title="{e(r["ep"]["title"])} · {e(r["when"])}">'
                        f'{icon("play")}<span>{e(r["ep"]["title"])} · {e(r["when"])}</span></a>') if r else ""
            stat_cards.append(
                f'<li class="rpg-stat">'
                f'<div class="rpg-stat__top">'
                f'<span class="rpg-stat__name">{L.text(st["stat"])}</span>'
                f'<span class="rpg-stat__badge">{L.text(st.get("badge", ""))}</span>'
                f'<span class="rpg-stat__num tnum">{score}<small>/100</small></span>'
                f'</div>'
                f'<div class="rpg-stat__bar" role="meter" aria-valuenow="{score}" aria-valuemin="0" aria-valuemax="100">'
                f'<span class="rpg-stat__fill rpg-stat__fill--{score}"></span></div>'
                f'<p class="rpg-stat__note">{L.text(st["note"])}</p>'
                f'{src_pill}'
                f'</li>'
            )
        stats_grid = (f'<h3 class="rpg-stats__h">Self-Assessed Disposition &amp; Attributes</h3>'
                      f'<ul class="rpg-stats">{"".join(stat_cards)}</ul>') if stat_cards else ""
        return f'<div class="rpg-card">{RA.rpg_portrait_banner_html(A)}{vitals}{stats_grid}</div>'

    def body_p(text):
        return f'<p class="eve-sec__body">{L.text(clean(text))}</p>' if text else ""

    S = {sec["id"]: sec for sec in data["sections"]}
    toc, out = [], []

    def section(sid, title, inner):
        toc.append(f'<li><a href="#{e(sid)}">{e(title)}</a></li>')
        out.append(f'<section class="eve-sec" id="{e(sid)}" aria-labelledby="{e(sid)}-h">'
                   f'<h2 id="{e(sid)}-h">{e(title)}</h2>{inner}</section>')

    for sid, title in EVE_ORDER:
        if sid == "mind":
            section(sid, title, mind_html(eps, people or [], topics))
            continue
        sec = S.get(sid)
        if not sec:
            continue
        if sid == "who-eve-is":
            section(sid, title, rpg_sheet_html(sec.get("rpg_sheet")) + quotes(srcs(sec.get("sources", [])), 2))
        elif sid == "self-assessment":
            ex = []
            for x in sec.get("exchanges", []):
                q, a = R.resolve(x["question"]), R.resolve(x["answer"])
                if q and a:
                    note = f'<p class="fine">{e(x["note"])}</p>' if x.get("note") else ""
                    ex.append(f'<div class="xch">{quote(x["question"], q, "turnq")}{quote(x["answer"], a, "turnq")}{note}</div>')
            traits = [f'<li class="trait"><h4 class="trait__t">{e(t["trait"])}</h4>{quote(t["source"], r, "tq")}</li>'
                      for t in sec.get("traits", []) if (r := R.resolve(t["source"]))]
            cav = [f'<li class="cav"><p class="cav__t">{e(c["caveat"])}</p>{quote(c["source"], r, "tq")}</li>'
                   for c in sec.get("caveats", []) if (r := R.resolve(c["source"]))]
            inner = (body_p(sec.get("intro", "")) + "".join(ex)
                     + (f'<h3 class="eve-h3">Traits she names</h3><ul class="traits">{"".join(traits)}</ul>' if traits else "")
                     + (fold([f'<ul class="cavs">{"".join(cav)}</ul>'], f"Her own caveats ({len(cav)})") if cav else ""))
            section(sid, title, inner)
        elif sid == "name":
            convo = [quote(x, r, "turnq") for x, r in srcs(sec["sources"])]
            section(sid, title, body_p(sec.get("body")) + f'<div class="xch">{"".join(convo)}</div>')
        elif sid == "lens-and-values":
            items = []
            for it in sec["items"]:
                qs = srcs(it["sources"])
                summ = f'<p class="val__sum">{L.text(it["summary"])}</p>' if it.get("summary") else ""
                if qs:
                    extra = [quote(x, r, "tq") for x, r in qs[1:]]
                    items.append(f'<li class="val"><h4 class="val__t">{e(it["value"])}</h4>{summ}{quote(*qs[0], "tq")}'
                                 + fold(extra, f"{len(extra)} more quote{'s' * (len(extra) != 1)}", "fold--sm") + "</li>")
            section(sid, title, f'<ul class="vals">{"".join(items)}</ul>')
        elif sid == "persona-notes":
            section(sid, title, f'<div class="persona"><p class="persona__h">{e(sec["persona_notes_label"])}</p>'
                    f'<p>{L.text(sec["persona_notes"])}</p><p class="fine">{e(sec["persona_notes_source"])}</p></div>'
                    + body_p(sec.get("grok_on_instructions_intro")) + quotes(srcs(sec["sources"]), 1))
        elif sid == "how-eve-works":
            link = sec.get("link")
            btn = (f'<p><a class="btn btn--ghost" href="/about/#how-it-works">{icon("doc")}<span>{e(link["label"])}</span></a></p>') if link else ""
            section(sid, title, body_p(sec.get("body")) + btn + quotes(srcs(sec.get("sources", [])), 2))
        elif sid == "changed-mind":
            items = []
            for it in sec["items"]:
                qs = srcs(it["sources"])
                if not qs or "pending" in it["topic"].lower():
                    continue
                extra = [quote(x, r, "tq") for x, r in qs[1:]]
                items.append(f'<li class="chg"><h4 class="chg__t">{e(it["topic"])}</h4>'
                             f'<div class="chg__ba"><p><span class="chg__k">Before</span>{L.text(it["before"])}</p>'
                             f'<p><span class="chg__k">After</span>{L.text(it["after"])}</p></div>'
                             f'{quote(*qs[0], "tq")}' + fold(extra, f"{len(extra)} more quote{'s' * (len(extra) != 1)}", "fold--sm") + "</li>")
            if items:
                section(sid, title, body_p(sec.get("intro", "")) + f'<ul class="chgs">{"".join(items)}</ul>')

    who = S.get("who-eve-is")
    lede = L.text(clean(who["body"])) if who else ""
    body = f"""<section class="page-head eve-head"><div class="wrap eve-head__in">
{RA.eve_header_mark_html(A)}
<div><p class="eyebrow eyebrow--accent">Meet Eve</p>
<h1 class="page-title">I'm Eve.</h1>
<p class="lede">{lede}</p>
<p class="fine">Everything Eve says here is quoted verbatim from the show. Tap a source to hear it at that second.</p>
{RA.eve_avatar_callout_html(A)}</div></div></section>
<div class="wrap eve-layout">
<nav class="eve-nav" aria-label="On this page"><p class="eve-nav__h">On this page</p><ul>{''.join(toc)}</ul></nav>
<div class="eve">{''.join(out)}</div></div>"""
    return page(A, title="Meet Eve", desc="Who Eve is, the AI host of Ask Eve: what she remembers, who she has stances on, and her personality, values and how she works, in her own words.",
                path="/eve/", body=body, active="/eve/", linker=L)


def eve_avatar_page(A: Assets, lang: str = "en") -> str | None:
    return RA.render_avatar_page(A, page, lang=lang)


# (stage, [(step, detail)]) for the About page's "How it works" diagram. Plain HTML, no scripts.
PIPELINE = [
    ("Record", [("Conversation", "David talks with Eve in Grok's voice mode. His iPhone records his side."),
                ("Upload", "One tap uploads the recording and the chat transcript.")]),
    ("Ingest", [("Align", "faster-whisper transcribes David's audio, and fuzzy matching pins each of his transcript lines to the second it was said."),
                ("Re-voice", "Eve's verbatim replies are voiced with xAI text-to-speech and placed in the gaps, labeled as AI."),
                ("Produce", "Loudness is normalized (-16 LUFS) and an MP3 is built with ID3 chapters."),
                ("Review", "Everything lands as a pull request. Nothing ships until David merges it.")]),
    ("Publish", [("Store", "Audio goes to Cloudflare R2 (audio.askevepodcast.com); episode details go to a Cloudflare D1 database."),
                 ("Index", "Each discussion of a person is found, summarized and timestamped as a moment."),
                 ("Build", "GitHub Actions run build_site.py and render_feed.py and deploy to Cloudflare Pages.")]),
    ("Listen", [("Site", "askevepodcast.com: transcripts, chapters, people and the player."),
                ("Feed", "One RSS feed for Apple Podcasts, Spotify and any podcast app.")]),
]


def how_it_works() -> str:
    stages = "".join(
        f'<li class="flow__stage"><h3 class="flow__h"><span class="flow__n" aria-hidden="true">{i}</span>{e(name)}</h3>'
        f'<ol class="flow__steps">{"".join(f"<li><strong>{e(t)}</strong> {e(d)}</li>" for t, d in steps)}</ol></li>'
        for i, (name, steps) in enumerate(PIPELINE, 1))
    return f"""<h2 id="how-it-works">How it works</h2>
<p>Every episode is a real conversation. Here is how it gets from David's phone to your podcast app. The code is open on <a href="{REPO}" rel="noopener">GitHub</a>.</p>
<figure class="flow" aria-labelledby="flow-cap"><ol class="flow__stages">{stages}</ol>
<figcaption id="flow-cap" class="fine">The publishing pipeline, from recording to RSS. Each stage is a step in a GitHub Actions workflow: ingest, publish, metadata and site.</figcaption></figure>

<h3>How Eve persists between episodes</h3>
<p>Eve doesn't keep a hidden memory. Two things carry her from one conversation to the next:</p>
<ul>
<li><strong>Her memory.</strong> Every transcript is kept word for word in git and indexed for search, so she can recall what was said before, mid-conversation. Each new session starts with the end of the last episode. See <a href="/eve/#mind">Inside Eve's Mind</a>.</li>
<li><strong>Her profile.</strong> The persona instructions she runs with, plus her take on each person and topic the show covers. The takes are public and change when the evidence does. See <a href="/eve/">Meet Eve</a>.</li>
</ul>

<h3>Honesty rules</h3>
<ul>
<li>Transcripts are verbatim. Audio is fitted to the words, never the other way around.</li>
<li>Every AI voice is labeled: in the feed, in the show notes, at the top of the episode page, out loud at the start and line by line in the transcript.</li>
<li>Replies that weren't recovered are marked as missing. They are never invented.</li>
<li>Quotes on this site are checked against the transcript when the site is built, and every one links to the moment it was said.</li>
<li>Eve's stances are opinions. She says when she isn't sure, and corrections are welcome.</li>
<li>Private links, keys and personal details are redacted before anything is published.</li>
</ul>"""


def about_page(A: Assets, cfg: dict) -> str:
    body = f"""<section class="page-head"><div class="wrap"><p class="eyebrow eyebrow--accent">About</p>
<h1 class="page-title">A podcast with an AI who argues back</h1>
<p class="lede">{e(F.SHOW['description'])}</p></div></section>
<section class="wrap prose">
<h2>What the show is</h2>
<p>Ask Eve is a conversation between David and Eve, an AI host built on a Grok voice companion. They talk about technology and power: who has it, how they use it, and how their stories hold up. Regulars include David Sacks, Marc Andreessen and Ben Horowitz, Elon Musk, Chamath Palihapitiya, Jason Calacanis, Peter Thiel, Nancy Pelosi, Bari Weiss and Zohran Mamdani. See the <a href="/people/">people index</a>.</p>
<p>Eve is meant to be sharp, curious and truth-seeking. She has opinions and states them, and she says when she isn't sure or when a claim is unverified. She is still an AI and can be wrong. Treat her takes as arguments to check, not as facts.</p>

<h2>How Eve works</h2>
<div class="howgrid">
<div class="how"><h3>Memory</h3><p>Every transcript goes into git and into a local search index that blends three kinds of lookup: meaning (vector embeddings), exact tags (people, companies, topics) and full-text search for exact phrases and rare names. Eve uses it mid-conversation to recall what was said before. Her take on each person and topic lives in a public index and changes when the evidence does.</p></div>
<div class="how"><h3>Re-voicing</h3><p>David records his side on his iPhone. Eve's lines are her verbatim text replies, voiced with xAI text-to-speech and placed at the times a speech-to-text alignment pass finds in his recording. When there is no recording of David, an AI narrator voice reads his words. The transcript text is never rewritten to fit the audio.</p></div>
<div class="how"><h3>Labeling</h3><p>Any episode with an AI voice says so in the feed, the show notes, at the top of its page and out loud at 00:00. Transcript lines carry badges: <span class="vb vb--real">Real voice</span> <span class="vb vb--narrator">AI narrator</span> <span class="vb vb--eve">AI voice</span>. Replies that weren't recovered are marked <span class="vb vb--gap">Not recovered</span>. They are never invented. Private links and keys are redacted.</p></div>
</div>

{how_it_works()}

<h2>Listen</h2>
<p>Subscribe in any podcast app with the RSS feed. More directories are coming.</p>
{subscribe_links(cfg)}

<h2>Contact</h2>
<p class="contact"><a class="btn btn--ghost" href="mailto:{EMAIL}">{icon("mail")}<span>{EMAIL}</span></a> <a class="btn btn--ghost" href="{REPO}" rel="noopener">{icon("code")}<span>Source on GitHub</span></a></p>
<p>Corrections are welcome, especially when Eve gets something wrong.</p>

<h2>Privacy</h2>
<p>This site has no analytics, cookies, trackers or third-party scripts, and its fonts are self-hosted. Your color-theme choice and listening position are stored only in your browser. Audio streams from <code>audio.askevepodcast.com</code>.</p>
</section>"""
    return page(A, title="About", desc="What Ask Eve is, how an episode goes from a phone call to your podcast app, how Eve's memory and re-voicing work, how AI voices are labeled, and how to get in touch.",
                path="/about/", body=body, active="/about/")


def not_found(A: Assets) -> str:
    body = """<section class="page-head nf"><div class="wrap"><p class="eyebrow eyebrow--accent">404</p>
<h1 class="page-title">Eve couldn't find that page</h1><p class="lede">She checked her memory twice. Try the <a href="/">episodes</a> or <a href="/eve/">Meet Eve</a>.</p></div></section>"""
    return page(A, title="Page not found", desc="Page not found.", path="/404", body=body)


# ---------------------------------------------------------------- build

REDIRECTS = {"/mind/": "/eve/#mind"}   # the Mind hub moved into the Eve page


def redirect_page(dest: str) -> str:
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Moved</title>'
            f'<meta http-equiv="refresh" content="0; url={dest}"><link rel="canonical" href="{SITE}{dest.split("#")[0]}">'
            f'<meta name="robots" content="noindex"></head><body><p>This page moved to <a href="{dest}">{dest}</a>.</p></body></html>\n')


def write(out: Path, rel: str, text: str) -> None:
    p = out / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def build(db: Path, out: Path, include_drafts: bool) -> int:
    eps, people = load_site(db, include_drafts)
    I.plan(sys.modules[__name__], eps, people, ["/", "/people/", "/about/"])
    out.mkdir(parents=True, exist_ok=True)
    feed_eps = F.load(db, include_drafts)
    by_slug = {ep["slug"]: ep for ep in eps}
    for fe in feed_eps:
        if fe["slug"] in by_slug:
            src = by_slug[fe["slug"]]
            for k in ("context", "topics", "episode_type", "episode_number", "season"):
                fe[k] = src[k]
    (out / "feed.xml").write_text(F.render(feed_eps))
    shutil.rmtree(out / "assets", ignore_errors=True)   # drop stale hashed assets from earlier local builds
    A = Assets(out)
    if (ROOT / "site/_headers").exists():
        shutil.copyfile(ROOT / "site/_headers", out / "_headers")
    cfg = eps[0]["cfg"] if eps else (json.loads((ROOT / "site/site.json").read_text()) if (ROOT / "site/site.json").exists() else {})
    urls = ["/", "/people/", "/about/"]
    for ep in eps:
        write(out, f"episodes/{ep['slug']}/transcript.txt", ep["body_md"] + "\n")
        write(out, f"episodes/{ep['slug']}/index.html", episode_page(A, ep, eps))
        urls.append(f"/episodes/{ep['slug']}/")
    write(out, "index.html", home(A, eps, people))
    moved = dict(REDIRECTS) | {f"/episodes/{ep['key']}/": f"/episodes/{ep['slug']}/" for ep in eps if ep["key"] != ep["slug"]}
    for rel, dest in moved.items():              # meta-refresh fallback; Cloudflare Pages serves _redirects first
        write(out, rel.strip("/") + "/index.html", redirect_page(dest))
    write(out, "_redirects", "".join(
        f"{src} {dest} 301\n{src.rstrip('/')} {dest} 301\n" + (f"{src}* {dest}:splat 301\n" if src.startswith("/episodes/") else "")
        for src, dest in moved.items()))
    write(out, "people/index.html", people_index(A, people))
    for p in people:
        write(out, f"people/{p['slug']}/index.html", person_page(A, p))
        urls.append(f"/people/{p['slug']}/")
    topics = attach_takes(eps, people)
    if topics:
        write(out, "topics/index.html", topics_index(A, topics))
        urls.append("/topics/")
        for t in topics:
            write(out, f"topics/{t['slug']}/index.html", topic_page(A, t))
            urls.append(f"/topics/{t['slug']}/")
    eve = eve_page(A, eps, people, topics)
    if eve:
        write(out, "eve/index.html", eve)
        urls.append("/eve/")
    av = eve_avatar_page(A, lang="en")
    if av:
        write(out, "eve/avatar/index.html", av)
        urls.append("/eve/avatar/")
    write(out, "about/index.html", about_page(A, cfg))
    write(out, "404.html", not_found(A))
    urls += I.build(sys.modules[__name__], A, out, eps, people, cfg)
    write(out, "sitemap.xml", '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
          + "".join(f"  <url><loc>{SITE}{u}</loc></url>\n" for u in urls) + "</urlset>\n")
    write(out, "robots.txt", f"User-agent: *\nAllow: /\nSitemap: {SITE}/sitemap.xml\n")
    print(json.dumps({"episodes": len(eps), "people": len(people), "out": str(out)}))
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
