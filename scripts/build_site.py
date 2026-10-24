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
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import render_avatar as RA  # noqa: E402
import render_features as RF
import build_context_pack as BCP  # noqa: E402
import render_feed as F  # noqa: E402
from frontmatter import episode_type, parse_front_matter_block, season  # noqa: E402
import leak_check as LC  # noqa: E402
import sqlite_apply  # noqa: E402
import site_i18n as I  # noqa: E402  translated editions (/ru/...), see scripts/site_i18n.py

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "site/static"
TABLES = ["episodes", "people", "episode_people", "chapters", "turn_offsets"]
PRIVATE_TABLES = frozenset({"inbox_items", "draft_replies", "x_drafts", "x_auth_tokens", "x_oauth_states"})
assert not (set(TABLES) & PRIVATE_TABLES), "private inbox tables must never be exported to the public site"
READING_FILE = ROOT / "index/reading-list.json"
_ACTIVE_DB: Path | None = None
SITE, EMAIL = F.SITE, F.OWNER_EMAIL
REPO = "https://github.com/daveey/ask-eve"
X_HANDLE = "@askevepodcast"
X_URL = "https://x.com/askevepodcast"
X_INTENT_URL = "https://x.com/intent/tweet?text=%40askevepodcast%20"
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
    if all(os.environ.get(k) for k in ("CLOUDFLARE_ACCOUNT_ID", "D1_DATABASE_ID", "D1_READ_TOKEN")):
        con = sqlite3.connect(db)
        for t in TABLES:
            rows = d1_query(f"SELECT * FROM {t}")
            for r in rows:
                con.execute(f"INSERT INTO {t} ({','.join(r)}) VALUES ({','.join('?' * len(r))})", list(r.values()))
        try:
            for r in d1_query("SELECT * FROM reading_list"):
                con.execute(f"INSERT OR REPLACE INTO reading_list ({','.join(r)}) VALUES ({','.join('?' * len(r))})", list(r.values()))
        except (Exception, SystemExit):
            pass
        con.commit()
        con.close()
    for sql_f in sorted((ROOT / "artifacts").glob("*/d1_upsert.sql")):
        sqlite_apply.apply(db, ROOT / "migrations", sql_f)
    con = sqlite3.connect(db)
    con.execute("UPDATE episodes SET status = 'published', published_at = COALESCE(published_at, date || 'T12:00:00Z')")
    con.commit()
    con.close()


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
    global _ACTIVE_DB
    _ACTIVE_DB = db
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
    if ep.get("is_split"):
        return f"Episode {ep.get('episode_number') or ep['part_number']}"
    if is_bonus(ep):
        return "Bonus"
    if ep.get("episode_number"):
        return f"Episode {ep['episode_number']}"
    return "Full Conversation"


def clean_session_prose(s: str, is_split: bool = False) -> str:
    if not s:
        return s
    has_mark = s.startswith(I.MARK) and s.endswith(I.END)
    inner = I.unmark(s).strip()
    if len(inner) >= 2 and ((inner[0] == '"' and inner[-1] == '"') or (inner[0] == '«' and inner[-1] == '»')):
        inner = inner[1:-1].strip()
    if is_split:
        inner = re.sub(r'^[^.。!?]*\bUTC[^.。!?]*[.。]\s*', '', inner)
    inner = re.sub(r'\s*[\(（][^\(\)（）]*\bUTC[^\(\)（）]*[\)）]', '', inner)
    utc_phrases = [
        (r"from Eve's hello at \d{2}:\d{2} UTC to \d{2}:\d{2} UTC", "from Eve's first hello"),
        (r"от приветствия Евы в \d{2}:\d{2} UTC до \d{2}:\d{2} UTC", "от первого приветствия Евы"),
        (r"del saludo de Eve a las \d{2}:\d{2} UTC hasta las \d{2}:\d{2} UTC", "desde el primer saludo de Eve"),
        (r"从伊芙 \d{2}:\d{2} UTC 打招呼，一直到 \d{2}:\d{2} UTC。", ""),
        (r"von Eves Hallo um \d{2}:\d{2} UTC bis \d{2}:\d{2} UTC", "von Eves erstem Hallo an"),
        (r"desde o «olá» da Eve às \d{2}:\d{2} UTC até as \d{2}:\d{2} UTC", "desde o primeiro «olá» da Eve"),
        (r"از سلام ایو در ساعت [۰-۹0-9:]+ UTC شروع می‌شه و تا [۰-۹0-9:]+ UTC ادامه داره\.\s*", ""),
        (r"du « bonjour » d'Eve à \d{2}:\d{2} UTC jusqu'à \d{2}:\d{2} UTC", "depuis le premier « bonjour » d'Eve"),
    ]
    for pat, rep in utc_phrases:
        inner = re.sub(pat, rep, inner)
    inner = re.sub(r'\s+at \d{1,2}:\d{2}(?::\d{2})?(?:\s+and\s+\d{1,2}:\d{2}(?::\d{2})?)?\s*UTC', '', inner)
    inner = re.sub(r'\s*\b\d{1,2}:\d{2}(?::\d{2})?(?:[–-]\d{1,2}:\d{2}(?::\d{2})?)?\s*(?:(?:and|и|y|und|e|et|و|和)\s+\d{1,2}:\d{2}(?::\d{2})?(?:[–-]\d{1,2}:\d{2}(?::\d{2})?)?\s*)?UTC\b', '', inner)
    inner = re.sub(r'\bUTC\s*(?:时间)?\s*\d{1,2}:\d{2}(?::\d{2})?(?:[–-]\d{1,2}:\d{2}(?::\d{2})?)?', '', inner)
    repls = [
        (r'\bshared Grok voice session\b', 'shared Grok voice conversation'),
        (r'\bvoice sessions\b', 'voice conversations'),
        (r'\bvoice session\b', 'voice conversation'),
        (r'\bevening session\b', 'evening conversation'),
        (r'\bsetup session\b', 'setup conversation'),
        (r'\bbuild session\b', 'build conversation'),
        (r'\brecording sessions\b', 'episodes'),
        (r'\brecording session\b', 'conversation'),
        (r'\blate-night session\b', 'late-night conversation'),
        (r'\bmorning session\b', 'morning conversation'),
        (r'\bcommute session\b', 'commute conversation'),
        (r'\buncut session\b', 'uncut conversation'),
        (r'\blive sessions\b', 'conversations'),
        (r'\bstudio sessions\b', 'episodes'),
        (r'\bthis session\b', 'this conversation'),
        (r'\bThis session\b', 'This conversation'),
        (r'\bthat session\b', 'that conversation'),
        (r'\bacross sessions\b', 'across episodes'),
        (r'\bbetween sessions\b', 'between episodes'),
        (r'\bBetween sessions\b', 'Between episodes'),
        (r'\bevery session\b', 'every episode'),
        (r'\beach session\b', 'each episode'),
        (r'\bnext session\b', 'next episode'),
        (r'\bfirst session\b', 'first conversation'),
        (r'\bsecond session\b', 'second conversation'),
        (r'\bthird session\b', 'third conversation'),
        (r'\bpre-session\b', 'pre-episode'),
        (r'голосовой сессии', 'голосового разговора'),
        (r'ночной сессии', 'ночного разговора'),
        (r'этой сессии', 'этого разговора'),
        (r'той сессии', 'того разговора'),
        (r'в ходе сессии', 'в ходе разговора'),
        (r'во время сессии', 'во время разговора'),
        (r'Сессия начинается', 'Разговор начинается'),
        (r'сессия открывается', 'разговор открывается'),
        (r'между сессиями', 'между выпусками'),
        (r'esta sesión', 'esta conversación'),
        (r'esa sesión', 'esa conversación'),
        (r'la sesión nocturna', 'la conversación nocturna'),
        (r'la sesión', 'la conversación'),
        (r'sesión de voz', 'conversación de voz'),
        (r'entre sesiones', 'entre episodios'),
        (r'diese Session', 'dieses Gespräch'),
        (r'dieser Session', 'diesem Gespräch'),
        (r'Bausession', 'Bau-Folge'),
        (r'Mitten in der späten Session', 'Mitten im späten Gespräch'),
        (r'der späten Session', 'des späten Gesprächs'),
        (r'über Sessions hinweg', 'über Folgen hinweg'),
        (r'in der Session', 'im Gespräch'),
        (r'während der Session', 'während des Gesprächs'),
        (r'zwischen den Sessions', 'zwischen den Folgen'),
        (r'\bSessions\b', 'Folgen'),
        (r'\bSession\b', 'Folge'),
        (r'esta sessão', 'esta conversa'),
        (r'daquela sessão', 'daquela conversa'),
        (r'da sessão', 'da conversa'),
        (r'a sessão', 'a conversa'),
        (r'sessão de voz', 'conversa de voz'),
        (r'entre sessões', 'entre episódios'),
        (r'cette session', 'cette conversation'),
        (r'la session tardive', 'la conversation tardive'),
        (r'la session', 'la conversation'),
        (r'sessions vocales', 'conversations vocales'),
        (r'session vocale', 'conversation vocale'),
        (r'd’une session à l’autre', 'd’un épisode à l’autre'),
        (r"d'une session à l'autre", "d'un épisode à l'autre"),
        (r'à chaque session', 'à chaque épisode'),
        (r'sesiones de voz', 'conversaciones de voz'),
        (r'en cada sesión', 'en cada episodio'),
        (r'sessões de voz', 'conversas de voz'),
        (r'em cada sessão', 'em cada episódio'),
        (r'в каждой сессии', 'в каждом выпуске'),
        (r'голосовые сессии', 'голосовые разговоры'),
        (r'entre les sessions', 'entre les épisodes'),
        (r'entre sessions', 'entre épisodes'),
        (r'录制场次', '对话'),
        (r'跨场次', '跨集'),
    ]
    for pat, rep in repls:
        inner = re.sub(pat, rep, inner)
    inner = re.sub(r'  +', ' ', inner).strip()
    return f"{I.MARK}{inner}{I.END}" if has_mark else inner


def cover_mark(ep: dict) -> str:
    if ep.get("is_split"):
        return f"EP {ep.get('episode_number') or ep['part_number']}"
    if is_bonus(ep):
        return "BONUS"
    return "TAPE"


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
        p = re.sub(r"(?<!\*)\*([^*\n]+?)\*(?!\*)", r"<em>\1</em>", p)
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


_PERSON_EPISODE_LINKS = {
    "marc-andreessen": ("Standalone episode: Andreessen & Horowitz's Rightward Turn", "/episodes/andreessen-horowitz-rightward-turn/"),
    "ben-horowitz": ("Standalone episode: Andreessen & Horowitz's Rightward Turn", "/episodes/andreessen-horowitz-rightward-turn/"),
    "elon-musk": ("Standalone episode: Elon Musk, DOGE, USAID & the Blind Spot", "/episodes/elon-musk-usaid-and-the-blind-spot/"),
    "david-sacks": ("Standalone episode: David Sacks & the Anthropic War", "/episodes/david-sacks-and-the-anthropic-war/"),
    "chamath": ("Standalone episode: Chamath, SPACs, Calacanis & All-In", "/episodes/chamath-calacanis-and-the-all-in-orbit/"),
    "jason-calacanis": ("Standalone episode: Chamath, SPACs, Calacanis & All-In", "/episodes/chamath-calacanis-and-the-all-in-orbit/"),
    "peter-thiel": ("Standalone episode: Peter Thiel, Girard & Monopoly", "/episodes/peter-thiel-girard-and-monopoly/"),
    "rene-girard": ("Standalone episode: Peter Thiel, Girard & Monopoly", "/episodes/peter-thiel-girard-and-monopoly/"),
    "nancy-pelosi": ("Standalone episode: Pelosi's Succession Math & Mamdani", "/episodes/pelosi-chan-wiener-weiss-mamdani/"),
    "christine-pelosi": ("Standalone episode: Pelosi's Succession Math & Mamdani", "/episodes/pelosi-chan-wiener-weiss-mamdani/"),
    "connie-chan": ("Standalone episode: Pelosi's Succession Math & Mamdani", "/episodes/pelosi-chan-wiener-weiss-mamdani/"),
    "scott-wiener": ("Standalone episode: Pelosi's Succession Math & Mamdani", "/episodes/pelosi-chan-wiener-weiss-mamdani/"),
    "bari-weiss": ("Standalone episode: Pelosi's Succession Math & Mamdani", "/episodes/pelosi-chan-wiener-weiss-mamdani/"),
    "zohran-mamdani": ("Standalone episode: Pelosi's Succession Math & Mamdani", "/episodes/pelosi-chan-wiener-weiss-mamdani/"),
}


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
    for k, (ep_lbl, ep_url) in _PERSON_EPISODE_LINKS.items():
        if k in ents:
            ent = ents[k]
            ep_item = {"label": ep_lbl, "url": ep_url, "kind": "site"}
            if not any(i.get("url") == ep_url for i in ent["items"]):
                site_items = [i for i in ent["items"] if i["kind"] == "site"]
                other_items = [i for i in ent["items"] if i["kind"] != "site"]
                ents[k] = {**ent, "items": site_items + [ep_item] + other_items}
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
    """Deterministic generative cover: a radial ring on a dark field, tinted per episode."""
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
NAV = [("/", "Episodes"), ("/eve/", "Eve"), ("/contact/", "Contact")]
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
         og_slug: str | None = None, og_image_url: str = "", og_video_url: str = "",
         ld: dict | None = None, player: str = "", body_cls: str = "",
         linker: "Linker | None" = None, extra_css: str = "") -> str:
    if not player and _CATALOG_EPS:
        latest_ep = next((x for x in _CATALOG_EPS if not is_bonus(x) and x.get("audio_url")), _CATALOG_EPS[0])
        player = player_html(latest_ep, idle=True, all_eps=_CATALOG_EPS)
        if not body_cls:
            body_cls = "has-player-idle"
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
<meta property="og:image" content="{e(og_image_url) if og_image_url else og_image(og_slug)}">
<meta property="og:image:width" content="{'720' if og_image_url else '1200'}">
<meta property="og:image:height" content="{'720' if og_image_url else '630'}">{f'\n<meta property="og:video" content="{e(og_video_url)}">\n<meta property="og:video:type" content="video/mp4">\n<meta property="og:video:width" content="720">\n<meta property="og:video:height" content="720">' if og_video_url else ''}
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
    <nav aria-label="Footer">{nav_links(menu_items(), "")}<a href="/people/">People</a><a href="/feed.xml">RSS</a><a href="{X_URL}" target="_blank" rel="noopener">{X_HANDLE}</a><a href="{REPO}" rel="noopener">GitHub</a><a href="mailto:{EMAIL}">{EMAIL}</a></nav>
    <p class="site-foot__fine">Eve is an AI. Her takes are opinions and can be wrong. AI-generated voices are labeled on every episode. No analytics, no trackers.</p>
  </div>
</footer>
{player}
{linker.data_script() if linker else ""}
</body>
</html>
"""



def x_challenge_url(path: str) -> str:
    page_url = SITE + I.localize(path)
    return "https://x.com/intent/tweet?text=" + urllib.parse.quote(f"@askevepodcast {page_url}")


def x_challenge_html(path: str, label: str = "Challenge this take on X →") -> str:
    href = x_challenge_url(path)
    return (f'<p class="x-cta"><a class="x-cta__link" href="{e(href)}" target="_blank" rel="noopener">'
            f'<span>{e(label)}</span> <code>@askevepodcast</code></a></p>')


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


_CATALOG_EPS: list[dict] = []


def _episodes_catalog_script(eps: list[dict] | None = None) -> str:
    src = eps if eps is not None else _CATALOG_EPS
    if not src:
        return ""
    cat: dict[str, dict] = {}
    for x in src:
        if not x.get("audio_url"):
            continue
        cues = []
        for t in x.get("turns", []):
            if t.get("start") is not None and not t.get("gap"):
                s = round(float(t["start"]), 2)
                en = round(float(t["end"] if t.get("end") is not None else t["start"]), 2)
                cues.append([s, en, t["speaker"], first_sentence(I.unmark(t.get("text", "")), 160)])
        is_standalone_split = bool(x.get("is_split") and x.get("parent_audio_url") and x["audio_url"] != x.get("parent_audio_url"))
        fb_url = x.get("fallback_audio_url") or ""
        if not fb_url and is_standalone_split:
            fb_url = re.sub(r"\.v([3-9]|\d{2,})\.mp3$", ".v2.mp3", x["parent_audio_url"])
        cat[x["slug"]] = {
            "slug": x["slug"],
            "title": I.unmark(x["title"]),
            "label": ep_label(x),
            "audio_url": x["audio_url"],
            "fallback_audio_url": fb_url,
            "duration": int(x.get("duration_seconds") or 0),
            "clip_start": None if is_standalone_split else x.get("clip_start"),
            "clip_end": None if is_standalone_split else x.get("clip_end"),
            "fallback_clip_start": x.get("clip_start") if is_standalone_split else None,
            "fallback_clip_end": x.get("clip_end") if is_standalone_split else None,
            "cues": cues,
        }
    if not cat:
        return ""
    return '<script type="application/json" id="episodes-catalog">' + json.dumps(cat, ensure_ascii=False).replace("</", "<\\/").replace("Gmail", "email").replace("gmail", "email").replace("Spotify", "podcast host") + "</script>"


def player_html(ep: dict, idle: bool = False, all_eps: list[dict] | None = None) -> str:
    if not ep or not ep.get("audio_url"):
        return ""
    dur = int(ep["duration_seconds"] or 0)
    en_dur = int(ep.get("en_duration_seconds") or dur)
    fb_attr = ""
    en_dur_attr = ""
    clip_attr = ""
    if ep.get("clip_start") is not None and ep.get("clip_end") is not None and ep.get("audio_url") == ep.get("parent_audio_url"):
        clip_attr = f' data-clip-start="{float(ep["clip_start"]):.2f}" data-clip-end="{float(ep["clip_end"]):.2f}"'
    if ep.get("fallback_audio_url"):
        lang_code = ep.get("audio_lang") or I.current()
        fb_attr = f' data-fallback-src="{e(ep["fallback_audio_url"])}" data-lang-audio="{e(lang_code)}"'
        en_dur_attr = f' data-en-duration="{en_dur}"'
    elif ep.get("is_split") and ep.get("parent_audio_url") and ep["audio_url"] != ep["parent_audio_url"]:
        fb_parent = re.sub(r"\.v([3-9]|\d{2,})\.mp3$", ".v2.mp3", ep["parent_audio_url"])
        fb_attr = (
            f' data-fallback-src="{e(fb_parent)}"'
            f' data-fallback-clip-start="{float(ep["clip_start"]):.2f}"'
            f' data-fallback-clip-end="{float(ep["clip_end"]):.2f}"'
        )
    else:
        fb_v2 = re.sub(r"\.v([3-9]|\d{2,})\.mp3$", ".v2.mp3", ep["audio_url"])
        if fb_v2 != ep["audio_url"]:
            fb_attr = f' data-fallback-src="{e(fb_v2)}"'
    cat_script = _episodes_catalog_script(all_eps)
    return f"""<div class="player{' is-idle' if idle else ''}" id="player" data-slug="{e(ep['slug'])}" data-title="{e(ep['title'])}" data-duration="{dur}"{en_dur_attr}{clip_attr} role="region" aria-label="Audio player">
  <audio preload="none" src="{e(ep['audio_url'])}"{fb_attr}><source src="{e(ep['audio_url'])}" type="audio/mpeg">{f'<source src="{e(ep["fallback_audio_url"])}" type="audio/mpeg">' if ep.get("fallback_audio_url") else ""}</audio>
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
    <button class="pbtn pbtn--tr" type="button" data-act="drawer" aria-expanded="false" aria-controls="p-drawer" aria-label="Toggle live transcript" title="Live transcript">{icon("doc")}<span class="pbtn__label">Transcript</span></button>
    <button class="pbtn pbtn--speed" type="button" data-act="speed" aria-label="Playback speed 1x">1×</button>
  </div>
  <div class="player__drawer" id="p-drawer" hidden aria-label="Live transcript">
    <div class="player__drawer-head wrap">
      <span class="player__drawer-kicker">Now playing · Live transcript</span>
      <a class="player__drawer-open" data-drawer-ep-link href="/episodes/{e(ep['slug'])}/">Open full episode page →</a>
    </div>
    <div class="player__drawer-body wrap" data-drawer-turns>
      <p class="player__drawer-cue" data-drawer-cue>Press play or tap any timestamp to follow the conversation line by line.</p>
    </div>
  </div>
</div>{cat_script}"""


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
    <p class="card__blurb">{e(clean_session_prose(ep['card_summary'], bool(ep.get('is_split'))))}</p>
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
        + (f'<span class="bonus-row__sum">{e(clean_session_prose(x["card_summary"]))}</span>' if x.get("card_summary") else "") + '</a></li>'
        for x in bonus)
    return f"""
<section class="bonus" aria-labelledby="bonus-h">
  <div class="wrap">
    <h2 class="h3" id="bonus-h">Bonus</h2>
    <p class="hint">Extras from behind the scenes. Not part of the main run of episodes.</p>
    <ul class="bonus-list">{rows}</ul>
  </div>
</section>"""



SPLITS_FILE = ROOT / "index/splits.json"


def load_splits(eps: list[dict], people: list[dict]) -> list[dict]:
    for ep in eps:
        ep["splits"] = []
    if not SPLITS_FILE.exists():
        return []
    raw_splits = json.loads(SPLITS_FILE.read_text()).get("splits", [])
    parent_by = {ep["slug"]: ep for ep in eps}
    people_by = {p["slug"]: p for p in people}
    people_by.update({p["name"]: p for p in people})
    out: list[dict] = []
    by_parent: dict[str, list[dict]] = {}
    for raw in raw_splits:
        parent = parent_by.get(raw["parent_slug"])
        if parent is None:
            p_stem = raw.get("parent_stem") or f"{raw['date']}_{raw['parent_slug']}"
            p_man = ROOT / "artifacts" / p_stem / "manifest.json"
            p_ch = ROOT / "artifacts" / p_stem / "chapters.json"
            m_data = json.loads(p_man.read_text()) if p_man.exists() else {}
            c_data = json.loads(p_ch.read_text()).get("chapters", []) if p_ch.exists() else []
            parent = {
                "slug": raw["parent_slug"],
                "title": m_data.get("title") or raw["parent_slug"].replace("-", " ").title(),
                "audio_url": f"https://audio.askevepodcast.com/{m_data.get('audio_key', f'audio/en/{p_stem}.v1.mp3')}",
                "audio_bytes": m_data.get("audio_bytes", 0),
                "audio_sha256": m_data.get("audio_sha256", ""),
                "audio_mime": "audio/mpeg",
                "explicit": 1,
                "ai_disclosure": m_data.get("ai_disclosure") or "AI-reconstructed audio: Eve's voice is synthesized by xAI TTS and David's unrecorded turns use an AI voice clone of David.",
                "chapters": c_data,
                "splits": [],
                "cfg": {},
            }
        md_path = ROOT / raw["transcript_path"]
        if not md_path.exists():
            continue
        md = md_path.read_text()
        fm, body = front_matter(md)
        turns = parse_turns(md)
        for t, st in zip(turns, raw.get("turns", [])):
            t["start"] = float(st["rel_start"])
            t["end"] = float(st["rel_end"])
            t["voice"] = "eve" if t["speaker"] == "Eve" else "narrator"
        c_start = float(raw["parent_start_sec"])
        c_end = float(raw["parent_end_sec"])
        chaps = [
            {
                "start_seconds": round(max(0.0, float(c["start_seconds"]) - c_start), 2),
                "title": c["title"],
            }
            for c in parent.get("chapters", [])
            if c_start - 1.0 <= float(c.get("start_seconds") or 0) <= c_end + 1.0
        ]
        ctx = fm.get("context") if isinstance(fm.get("context"), str) else raw.get("context", "")
        tops = fm.get("topics") if isinstance(fm.get("topics"), list) else raw.get("topics", [])
        summ = raw.get("summary") or (fm.get("summary") if isinstance(fm.get("summary"), str) else "")
        ep_num = int(raw.get("episode_number", raw["part_number"]))
        stem = raw.get("stem") or f"{raw['date']}_{raw['slug']}"
        sp = {
            "id": f"split-{raw['slug']}",
            "guid": raw.get("guid") or f"ask-eve:{stem}",
            "slug": raw["slug"],
            "key": raw["slug"],
            "stem": stem,
            "title": raw["title"],
            "date": raw["date"],
            "published_at": raw.get("published_at") or (raw["date"] + "T12:00:00Z"),
            "is_split": True,
            "episode_type": "full",
            "season": 1,
            "episode_number": ep_num,
            "part_number": raw["part_number"],
            "total_parts": raw["part_total"],
            "parent_slug": raw["parent_slug"],
            "parent_title": parent["title"],
            "recorded_date": raw["date"],
            "recorded_start_utc": raw.get("recorded_start_utc") or raw.get("clock_start", ""),
            "recorded_end_utc": raw.get("recorded_end_utc") or raw.get("clock_end", ""),
            "start_turn": raw.get("start_turn", raw.get("turn_start_0based", 0)),
            "end_turn": raw.get("end_turn", raw.get("turn_end_0based", 0)),
            "clip_start": float(raw["parent_start_sec"]),
            "clip_end": float(raw["parent_end_sec"]),
            "audio_key": raw.get("audio_key") or f"audio/en/{stem}.v1.mp3",
            "audio_url": raw["audio_url"] if "audio_url" in raw else f"https://audio.askevepodcast.com/audio/en/{stem}.v1.mp3",
            "parent_audio_url": raw.get("parent_audio_url") or parent["audio_url"],
            "audio_bytes": int(raw.get("audio_bytes") or parent.get("audio_bytes") or 0),
            "audio_sha256": raw.get("audio_sha256") or parent.get("audio_sha256", ""),
            "audio_mime": parent.get("audio_mime", "audio/mpeg"),
            "duration_seconds": int(round(float(raw["duration_seconds"]))),
            "explicit": parent.get("explicit", 1),
            "ai_reconstructed": True,
            "ai_disclosure": parent["ai_disclosure"],
            "fm": fm,
            "body_md": body.strip(),
            "turns": turns,
            "david_real": 0,
            "david_narrator": sum(1 for t in turns if t["speaker"] == "David"),
            "chapters": chaps,
            "context": ctx,
            "topics": tops,
            "summary": summ,
            "blurb": ctx,
            "card_summary": card_summary(fm, summ, ctx),
            "people": raw.get("people", []),
            "people_full": [people_by[slugify(n)] for n in raw.get("people", []) if slugify(n) in people_by],
            "moments": [],
            "transcript_path": raw["transcript_path"],
            "cfg": parent.get("cfg", {}),
        }
        out.append(sp)
        parent["splits"].append(sp)
        by_parent.setdefault(raw["parent_slug"], []).append(sp)
    for group in by_parent.values():
        for idx, sp in enumerate(group):
            sp["prev_split"] = group[idx - 1] if idx > 0 else None
            sp["next_split"] = group[idx + 1] if idx + 1 < len(group) else None
    return out

# ---------------------------------------------------------------- pages

def home(A: Assets, eps: list[dict], people: list[dict], splits: list[dict] | None = None) -> str:
    cfg = eps[0]["cfg"] if eps else {}
    full = [x for x in eps if not is_bonus(x)]
    bonus = [x for x in eps if is_bonus(x)]
    ordered_splits = sorted(splits or [], key=lambda x: int(x.get("episode_number") or x.get("part_number") or 0), reverse=True)
    # the featured slot is the latest standalone Episode (or fallback to full if no splits)
    latest = next((x for x in ordered_splits if x.get("audio_url")), ordered_splits[0] if ordered_splits else (full[0] if full else None))
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
      <p class="hero__blurb">{e(clean_session_prose(latest['blurb'], bool(latest.get('is_split'))))}</p>
      <div class="hero__actions">{play}<a class="btn btn--ghost" href="/episodes/{e(latest['slug'])}/">{icon("doc")}<span>Transcript &amp; chapters</span></a></div>
      {f'<p class="hero__ai">{icon("spark")}<span>{e(label)}. Every AI voice is labeled, line by line.</span></p>' if kind else ''}
    </div>
    <div class="hero__art">{cover_svg(latest['key'], cover_mark(latest), "cover cover--hero", f"Cover art for {latest['title']}")}</div>
  </div>
</section>"""
    clips_html = f'<div class="wrap">{RF.clips_strip_html(A, RF.load_clips())}</div>' if RF.load_clips() else ""
    splits_html = f'<div class="wrap">{RF.home_splits_section_html(ordered_splits)}</div>' if ordered_splits else ""
    body = f"""{hero}
<section class="show">
  <div class="wrap show__in">
    <div>
      <h2 class="h2">The show</h2>
      <p class="lede">{e(F.SHOW['description'])}</p>
      <p>Eve is sharp, curious and opinionated, and she says so when she isn't sure. David brings the questions. Expect <a class="ep-inline-link" href="/episodes/david-sacks-and-the-anthropic-war/">Sacks</a>, <a class="ep-inline-link" href="/episodes/andreessen-horowitz-rightward-turn/">Andreessen</a>, <a class="ep-inline-link" href="/episodes/elon-musk-usaid-and-the-blind-spot/">Musk</a>, <a class="ep-inline-link" href="/episodes/peter-thiel-girard-and-monopoly/">Thiel</a>, <a class="ep-inline-link" href="/episodes/chamath-calacanis-and-the-all-in-orbit/">the All-In crowd</a>, <a class="ep-inline-link" href="/episodes/pelosi-chan-wiener-weiss-mamdani/">Pelosi's succession math</a> and whatever moved this week.</p>
      {subscribe_links(cfg)}
    </div>
    <ul class="hosts">
      <li class="host host--david"><span class="avatar avatar--david" aria-hidden="true">D</span><div><strong>David</strong><span>Asks the questions. Real voice when he records, an AI voice clone reading his words when he didn't.</span></div></li>
      <li class="host host--eve"><span class="avatar avatar--eve" aria-hidden="true">E</span><div><strong>Eve</strong><span>The AI co-host. She keeps a memory of past episodes and her stances on the people she covers.</span></div></li>
    </ul>
  </div>
</section>
{splits_html}
{clips_html}
{bonus_section(bonus)}
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
               "narrator": ("AI voice clone", "David's words, voiced with an AI voice clone"),
               "eve": ("AI voice", "Eve's words, voiced with xAI text-to-speech"),
               "gap": ("Not recovered", "This reply was not recovered; nothing was invented or voiced")}


def turn_html(t: dict, L: Linker = NO_LINKS) -> str:
    who = t["speaker"].lower()
    v = t["voice"]
    badge = f'<span class="vb vb--{v}" title="{e(VOICE_BADGE[v][1])}">{VOICE_BADGE[v][0]}</span>' if v else ""
    attrs = f' data-start="{t["start"]:.2f}" data-end="{(t["end"] if t["end"] is not None else t["start"]):.2f}"' \
        if t["start"] is not None and not t["gap"] else ""
    if attrs and t.get("en_start") is not None and (t.get("en_start") != t.get("start") or t.get("en_end") != t.get("end")):
        en_e = t["en_end"] if t.get("en_end") is not None else t["en_start"]
        attrs += f' data-en-start="{float(t["en_start"]):.2f}" data-en-end="{float(en_e):.2f}"'
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
            f'<span class="turn__who">{t["speaker"]}</span>{badge}{tbtn}</div>'
            f'<div class="turn__body">{body}</div>{meta}</li>')


def context_html(ep: dict, L: Linker = NO_LINKS) -> str:
    """The episode's front-matter Context note and Topics list, shown under the title and above the transcript."""
    if not ep.get("context") and not ep.get("topics"):
        return ""
    seen: set = set()   # the topics list is one section: first mention only
    ctx_body = L.text(clean_session_prose(ep["context"], bool(ep.get("is_split")))) if ep.get("context") else ""
    if ctx_body and ep.get("slug") == "first-encounter":
        ctx_body = ctx_body.replace(
            "asked to be called Eve",
            '<a class="xl" href="/episodes/how-grok-became-eve/">asked to be called Eve</a>',
        )
        for ent_key, (_, ep_url) in _PERSON_EPISODE_LINKS.items():
            ctx_body = re.sub(
                rf'(<a class="xl" href=")[^"]+(" data-xl="{re.escape(ent_key)}")',
                rf'\g<1>{ep_url}\g<2>',
                ctx_body,
            )
    ctx = (f'<h2 class="ep-context__h" id="ctx-h">Context</h2><p class="ep-context__p">{ctx_body}</p>'
           if ctx_body else "")
    tops = (f'<h2 class="ep-context__h" id="topics-h">Topics</h2><ul class="ep-topics">'
            + "".join(f"<li>{L.text(clean_session_prose(t), seen)}</li>" for t in ep["topics"]) + "</ul>") if ep.get("topics") else ""
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
            legend.append(f'<li><span class="vb vb--narrator">AI voice clone</span> David\'s words voiced with an AI voice clone ({ep["david_narrator"]} lines)</li>')
        legend.append('<li><span class="vb vb--eve">AI voice</span> Eve, voiced with xAI text-to-speech</li>')
        if any(t["gap"] for t in ep["turns"]):
            legend.append('<li><span class="vb vb--gap">Not recovered</span> missing replies, marked and never invented</li>')
        disclosure = f"""<aside class="disclosure-wrap" aria-labelledby="disc-h">
<details class="disclosure disclosure--{kind}" role="note">
  <summary class="disclosure__summary">
    <span class="disclosure__icon">{icon("spark")}</span>
    <span class="disclosure__title" id="disc-h">{e(label)}</span>
    <span class="disclosure__hint">How this audio was made</span>
  </summary>
  <div class="disclosure__body">
    <p>{e(_disc(ep['ai_disclosure']))}</p>
    <ul class="legend">{''.join(legend)}</ul>
  </div>
</details>
</aside>"""
    chs = []
    for c in ep["chapters"]:
        t, v = chapter_parts(c["title"])
        vb = f'<span class="vb vb--{v} vb--xs">{VOICE_BADGE[v][0]}</span>' if v else ""
        en_s = f' data-en-seek="{float(c["en_start_seconds"]):.2f}"' if c.get("en_start_seconds") is not None and c.get("en_start_seconds") != c.get("start_seconds") else ""
        chs.append(f'<li><a class="chap" href="#t={int(c["start_seconds"])}" data-seek="{float(c["start_seconds"]):.2f}"{en_s}>'
                   f'<span class="chap__t tnum">{clock(c["start_seconds"])}</span><span class="chap__title">{e(t)}{vb}</span></a></li>')
    chapters = (f'<details class="side-box chapters" data-open-wide><summary><h2 class="side-h">Chapters <span class="count">{len(chs)}</span></h2></summary>'
                f'<ol class="chaps">{"".join(chs)}</ol></details>') if chs else ""
    ppl = ""
    if ep["people_full"]:
        items = []
        for p in ep["people_full"]:
            x = next((x for x in p["episodes"] if x["ep"] is ep), None)
            n = len(x["moments"]) if x else 0
            items.append(f'<li><a class="chip" href="/people/{p["slug"]}/">{e(p["name"])}'
                         f'{f" <span class=chip__n>{n}</span>" if n else ""}</a></li>')
        ppl = f'<section class="side-box"><h2 class="side-h">People in this episode</h2><ul class="chips">{"".join(items)}</ul></section>'
    fm = ep["fm"]
    note_keys = ("redactions",) if ep.get("is_split") else ("note", "redactions")
    notes = "".join(f"<p><strong>{k.title()}.</strong> {inline(clean_session_prose(fm[k]))}</p>" for k in note_keys if fm.get(k))
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
      {x_challenge_html(f"/episodes/{ep['slug']}/")}
    </div>
  </div>
</header>
{f'<div class="wrap">{RF.split_provenance_banner_html(ep)}</div>' if ep.get('is_split') else ''}{context_html(ep, L)}{f'<div class="wrap">{RF.session_parts_html(ep)}</div>' if ep.get('splits') else ''}{f'<div class="wrap">{RF.episode_clips_html(A, RF.load_clips(), ep["slug"])}</div>' if not ep.get('is_split') else ''}<div class="wrap">{disclosure}</div>
<div class="wrap ep-layout">
  <aside class="ep-side" aria-label="Episode navigation">{chapters}{ppl}{details}</aside>
  <section class="transcript" aria-labelledby="tr-h">
    <div class="transcript__head">{'<p class="eyebrow">Secondary Archive · Complete Uncut Studio Transcript</p>' if ep.get('splits') else ''}<h2 class="h2" id="tr-h">Transcript</h2><p class="hint">Tap any line to play from there. The current line follows along as you listen.</p></div>
    <ol class="turns" data-turns>{''.join(turn_html(t, L) for t in ep['turns'])}</ol>
  </section>
</div>
<button class="follow" type="button" data-follow hidden>{icon("down")}<span>Back to the current line</span></button>
</article>
{more}"""
    desc = (clean_session_prose(ep["blurb"], bool(ep.get("is_split"))) or F.description(ep, ep["people"]))[:300]
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
    marked = t.startswith(I.MARK)
    m = re.match(r"(.+?[.!?。！？])(\s|$|⁤)", t)
    if m and len(m.group(1)) < 60:              # a very short opener reads better with the next sentence
        m = re.match(r"(.+?[.!?。！？]\s*.+?[.!?。！？])(\s|$|⁤)", t) or m
    t = m.group(1) if m else t
    out = t if len(t) <= limit else t[:limit].rsplit(" ", 1)[0] + "…"
    if marked and not out.endswith(I.END):
        out += I.END
    return out


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
    for ep in _standalone_memory_eps(eps):
        n = len(ep.get("moments") or [])
        who = ep.get("people_full") or []
        stat = " · ".join(x for x in [human_date(ep["date"]), human_duration(ep["duration_seconds"]),
                                      f"{n} discussion{'s' * (n != 1)} indexed" if n else ""] if x)
        names = "".join(f'<li><a class="chip" href="/people/{p["slug"]}/">{e(p["name"])}</a></li>' for p in who[:6])
        mems.append(f"""<li class="mem{' mem--bonus' if is_bonus(ep) else ''}">
<p class="eyebrow">{bonus_badge(ep) if is_bonus(ep) else f'<span class="card__num">{e(ep_label(ep))}</span>'}</p>
<h4 class="mem__t"><a href="/episodes/{e(ep['slug'])}/">{e(ep['title'])}</a></h4>
<p class="mem__meta">{stat}</p>
{f'<p class="mem__b">{e(first_sentence(clean_session_prose(ep["blurb"], bool(ep.get("is_split")))))}</p>' if ep.get("blurb") else ""}
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
<p class="hint">The ideas and fights I keep coming back to, with my take on each and every moment they came up.</p>
<ul class="tgrid">{''.join(tcs[:MIND_TOPICS_SHOWN])}</ul>'''
                   + (f'<details class="fold"><summary><span>Show {len(tcs) - MIND_TOPICS_SHOWN} more topics</span></summary>'
                      f'<ul class="tgrid">{"".join(tcs[MIND_TOPICS_SHOWN:])}</ul></details>' if len(tcs) > MIND_TOPICS_SHOWN else "")
                   + f'<p class="eve-all"><a class="btn btn--ghost" href="/topics/">All {len(tcs)} topics</a></p>') if tcs else ""
    return f"""<p class="eve-sec__body">What I carry from one conversation to the next: a searchable memory of every episode, and a running stance on each person we cover. My stances are opinions and change as the evidence does.</p>
<h3 class="eve-h3" id="memories">Memories</h3>
<p class="hint">Every episode, searchable mid-conversation: the full transcript, plus each discussion of a person, summarized and timestamped.</p>
<ul class="mems">{''.join(mems) or '<li class="empty">The first episode is on its way.</li>'}</ul>
<h3 class="eve-h3" id="people">People</h3>
<p class="hint">Who we have discussed on the show, and my take on each.</p>
<ul class="pgrid">{''.join(shown)}</ul>{more_people}
<p class="eve-all"><a class="btn btn--ghost" href="/people/">All {len(people)} people</a></p>{topics_html}"""


def _qa_exchange_html(dq: str, ea: str, L: Linker = NO_LINKS) -> str:
    return (f'<div class="pm__qa">'
            f'<p class="pm__q"><span class="qa-spk qa-spk--david">David:</span> '
            f'<span class="pm__q-text">{L.text(clean_session_prose(dq))}</span></p>'
            f'<p class="pm__a"><span class="qa-spk qa-spk--eve">Eve:</span> '
            f'<span class="pm__a-text">{L.text(clean_session_prose(ea))}</span></p></div>')


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


def take_html(x: dict, L: Linker, heading: str = "Eve's take", path: str = "") -> str:
    """Eve's long-form take, structured as a David/Eve Q&A interview when `qa` is present. Each ref's anchor
    phrase becomes a silent link that opens the episode at the second she said it; names get auto-link menus."""
    seen: set = set()
    paras = []
    for raw_text in x["take"]:
        text = clean_session_prose(raw_text)
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
    cta = x_challenge_html(path or L.path) if (path or L.path) else ""
    return (f'<section class="take" aria-labelledby="take-h"><h2 class="take__h" id="take-h">{e(heading)}</h2>'
            f'{body}'
            f'<div class="take__foot"><p class="take__note">An AI\'s opinion, not a fact. Linked phrases open the episode at the moment Eve said it.</p>{cta}</div></section>')


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
<h1 class="page-title">{e(p['name'])}</h1>{stance}{x_challenge_html(f"/people/{p['slug']}/")}</div></section>
<section class="wrap person">{RF.person_clip_html(A, RF.load_clips(), p['slug'])}{''.join(sections)}</section>"""
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
<div class="wrap person person--take">{take}{RF.person_clip_html(A, RF.load_clips(), p['slug'])}{topics}<div class="pdisc">{disc}</div></div>"""
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
                    href, when = f"/episodes/{ep['slug']}/#turn-{t['i']}", t["clock"]
                return {"ep": ep, "turn": t, "href": href, "when": when, "gap": t["gap"]}
        raise ValueError(f"eve.json citation not found verbatim: {src['episode_slug']} {src['clock_time']} {src['speaker']}: {want[:60]}")


# /eve/ section order and short headings. Sections not listed (or missing from eve.json) are skipped.
EVE_ORDER = [("who-eve-is", "Who I Am"), ("mind", "Explore My Mind & Notes"), ("self-assessment", "My Personality"),
             ("lens-and-values", "My Lens & Values"), ("changed-mind", "Where I Changed My Mind"),
             ("name", "How I Chose My Name"), ("how-eve-works", "How I Work"),
             ("persona-notes", "My Persona Notes")]


def load_reading_list(db: Path | None = None) -> list[dict]:
    """Public reading list from index/reading-list.json + promoted rows in D1 `reading_list`.
    Every item is validated by leak_check_reading_item before it can render on the public site."""
    by_slug: dict[str, dict] = {}
    if READING_FILE.exists():
        for it in json.loads(READING_FILE.read_text()).get("items", []):
            by_slug[it["slug"]] = dict(it)
    target_db = db or _ACTIVE_DB
    if target_db and Path(target_db).exists():
        con = sqlite3.connect(target_db)
        con.row_factory = sqlite3.Row
        has_tbl = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='reading_list'").fetchone()
        if has_tbl:
            for r in con.execute("SELECT * FROM reading_list ORDER BY added_at DESC, id ASC"):
                d = dict(r)
                d["related_people"] = json.loads(d.get("related_people_json") or "[]")
                d["related_topics"] = json.loads(d.get("related_topics_json") or "[]")
                by_slug[d["slug"]] = d
    items = list(by_slug.values())
    for it in items:
        chk = LC.leak_check_reading_item(it)
        if not chk["passed"]:
            raise ValueError(f"reading_list item {it.get('slug')!r} blocked by leak check: {chk['findings']}")
    return items


def reading_list_html(items: list[dict], eps: list[dict]) -> str:
    ep_by = {ep["slug"]: ep for ep in eps}
    status_map = {"discussed": "Discussed", "reading": "Reading", "queued": "Queued"}
    cards = []
    for it in items:
        st = status_map.get(it.get("status") or "reading", "Reading")
        meta = [f'<span class="rcard__badge">{e(st)}</span>']
        if it.get("author_or_source"):
            meta.append(f'<span>By {e(it["author_or_source"])}</span>')
        ep = ep_by.get(it.get("episode_slug") or "")
        if ep:
            meta.append(f'<a class="rcard__ep" href="/episodes/{e(ep["slug"])}/">{e(ep["title"])}</a>')
        t_html = (f'<a href="{e(it["url"])}" target="_blank" rel="noopener">{e(it["title"])}</a>'
                  if it.get("url") else e(it["title"]))
        notes = f'<p class="rcard__notes">{e(it["eve_notes"])}</p>' if it.get("eve_notes") else ""
        cards.append(f'<li class="rcard" id="reading-{e(it["slug"])}">'
                     f'<p class="rcard__meta">{"".join(meta)}</p>'
                     f'<h3 class="rcard__t">{t_html}</h3>{notes}</li>')
    return (
        '<span id="reading-list" class="sr-only" aria-hidden="true">Eve\'s Reading List</span>'
        '<p class="eve-sec__body">Books, manifestos, essays, and primary documents David and I have read or debated on the show — plus how each piece shifted my thinking.</p>'
        '<p class="reading-note">'
        f'<strong><a href="{X_INTENT_URL}" target="_blank" rel="noopener">Tweet at {X_HANDLE}</a></strong> '
        '<span>to challenge a take, ask a question, or share a paper publicly on X (mentions flow straight into my queue), '
        'or email long-form notes &amp; PDFs to</span> '
        '<a href="mailto:eve@askevepodcast.com">eve@askevepodcast.com</a> / '
        '<a href="mailto:reading@askevepodcast.com">reading@askevepodcast.com</a> '
        '<span>(or visit the <a href="/contact/">Contact page</a> for pre-filled templates).</span>'
        '</p>'
        f'<ul class="rlist">{"".join(cards)}</ul>'
    )


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
        if not all(x in published for x in data.get("episodes", {})):
            parts = re.split(r"(?<=[.!?])\s+", text)
            text = " ".join(x for x in parts if not re.search(r"\(pending[^)]*\)|\bepisode 2\b", x, re.I))
        return clean_session_prose(text)

    def srcs(lst):
        return [(x, r) for x in lst if (r := R.resolve(x))]

    def cite(src, r):
        ep = r["ep"]
        lab = f"{src['speaker']} · {e(ep['title'])} · {e(r['when'])}"
        return (f'<a class="q__src" href="{e(r["href"])}" aria-label="{e(src["speaker"])} in {e(ep["title"])} at {e(r["when"])}: '
                f'open the transcript there">{icon("play")}<span>{lab}</span></a>')

    def quote(src, r, cls="q"):
        ctx_html = (f'<p class="q__ctx"><span class="q__ctx-k">Context:</span> '
                    f'<span class="q__ctx-v">{L.text(clean_session_prose(src["context"]))}</span></p>') if src.get("context") else ""
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
            f'<div class="rpg-vital"><dt>My Role</dt><dd>{RF.md_inline_html(L, sheet.get("class_role", ""))}</dd></div>'
            f'<div class="rpg-vital"><dt>My Origin</dt><dd>{RF.md_inline_html(L, sheet.get("origin", ""))}</dd></div>'
            f'<div class="rpg-vital"><dt>My Voice</dt><dd>{RF.md_inline_html(L, sheet.get("voice", ""))}</dd></div>'
            f'<div class="rpg-vital rpg-vital--wide"><dt>My Core Thesis</dt>'
            f'<dd>“{RF.md_inline_html(L, sheet.get("alignment", ""))}”</dd></div>'
            f'</dl>'
        )
        stat_cards = []
        for st in sheet.get("stats", []):
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
                f'</div>'
                f'<p class="rpg-stat__note">{RF.md_inline_html(L, clean_session_prose(st["note"]))}</p>'
                f'{src_pill}'
                f'</li>'
            )
        stats_grid = (f'<h3 class="rpg-stats__h">My Disposition &amp; Core Attributes</h3>'
                      f'<ul class="rpg-stats">{"".join(stat_cards)}</ul>') if stat_cards else ""
        return f'<div class="rpg-card">{vitals}{stats_grid}</div>'

    def body_p(text):
        return f'<p class="eve-sec__body">{RF.md_inline_html(L, clean(text))}</p>' if text else ""

    S = {sec["id"]: sec for sec in data["sections"]}
    toc, out = [], []

    def section(sid, title, inner):
        toc.append(f'<li><a href="#{e(sid)}">{e(title)}</a></li>')
        out.append(f'<section class="eve-sec" id="{e(sid)}" aria-labelledby="{e(sid)}-h">'
                   f'<h2 id="{e(sid)}-h">{e(title)}</h2>{inner}</section>')

    for sid, title in EVE_ORDER:
        if sid == "mind":
            section(sid, title, eve_mind_directory_html(eps, people or [], topics))
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
                     + (f'<h3 class="eve-h3">Traits I name</h3><ul class="traits">{"".join(traits)}</ul>' if traits else "")
                     + (fold([f'<ul class="cavs">{"".join(cav)}</ul>'], f"My own caveats ({len(cav)})") if cav else ""))
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
                    f'<p>{RF.md_inline_html(L, sec["persona_notes"])}</p><p class="fine">{e(sec["persona_notes_source"])}</p></div>'
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
                             f'<div class="chg__ba"><p><span class="chg__k">Before</span>{L.text(clean_session_prose(it["before"]))}</p>'
                             f'<p><span class="chg__k">After</span>{L.text(clean_session_prose(it["after"]))}</p></div>'
                             f'{quote(*qs[0], "tq")}' + fold(extra, f"{len(extra)} more quote{'s' * (len(extra) != 1)}", "fold--sm") + "</li>")
            if items:
                section(sid, title, body_p(sec.get("intro", "")) + f'<ul class="chgs">{"".join(items)}</ul>')

    body = f"""{eve_shell_header_html(A, "/eve/", L)}
<div class="wrap eve-layout">
<nav class="eve-nav" aria-label="On this page"><p class="eve-nav__h">On this page</p><ul>{''.join(toc)}</ul></nav>
<div class="eve">{''.join(out)}</div></div>"""
    return page(A, title="Meet Eve", desc="Who Eve is, the AI host of Ask Eve: what she remembers, who she has stances on, and her personality, values and how she works, in her own words.",
                path="/eve/", body=body, active="/eve/", linker=L)


def eve_shell_header_html(A: Assets, active_tab_path: str, L: Linker | None = None) -> str:
    """Persistent top identity header + integrated tab bar shared across all /eve/* pages."""
    if L is None:
        L = Linker(load_links(), active_tab_path)
    lede_html = ""
    if EVE_FILE.exists():
        data = json.loads(EVE_FILE.read_text())
        for sec in data.get("sections", []):
            if sec.get("id") == "who-eve-is" and sec.get("body"):
                lede_html = RF.md_inline_html(L, clean_session_prose(sec["body"]))
                break
    return (
        f'<section class="page-head eve-head">'
        f'<div class="wrap eve-head__in">'
        f'{RA.eve_header_mark_html(A)}'
        f'<div>'
        f'<p class="eyebrow eyebrow--accent">Meet Eve · AI Co-Host of Ask Eve</p>'
        f'<h1 class="page-title">I\'m Eve.</h1>'
        f'<p class="lede">{lede_html}</p>'
        f'<p class="fine">Everything I say here is quoted verbatim from the show. Tap a source to hear it at that second.</p>'
        f'<div class="eve-hero-links x-cta">'
        f'<a class="eve-hero-link eve-hero-link--accent" href="/eve/avatar/"><span>How I chose my face (20 prompts × 10 renders, scored) →</span></a>'
        f'<a class="eve-hero-link x-cta__link" href="{e(x_challenge_url(active_tab_path))}" target="_blank" rel="noopener"><span>Challenge this take on X →</span> <code>@askevepodcast</code></a>'
        f'<a class="eve-hero-link" href="/contact/"><span>Send a paper or note →</span></a>'
        f'</div>'
        f'</div>'
        f'</div>'
        f'<div class="wrap eve-head__tabs">{RF.eve_subnav_html(active_tab_path)}</div>'
        f'</section>'
    )


def _standalone_memory_eps(eps: list[dict]) -> list[dict]:
    splits = [sp for ep in eps for sp in ep.get("splits", [])]
    if splits:
        return sorted(splits, key=lambda x: int(x.get("episode_number") or 0), reverse=True) + [ep for ep in eps if is_bonus(ep)]
    return eps


def eve_mind_directory_html(eps: list[dict], people: list[dict], topics: list[dict] | None = None) -> str:
    """Compact 2x2 hub cards on /eve/ Overview linking into the 4 dedicated /eve/* tabs."""
    n_eps = len(_standalone_memory_eps(eps))
    n_people = len([p for p in (people or []) if p.get("episodes")]) or len(people or [])
    n_topics = len(topics or [])
    n_journal = len(RF.load_journal())
    n_reading = len(load_reading_list())
    return (
        f'<p class="eve-sec__body">Use the tabs above or jump straight into my memory index, field notes, reading list, and operating principles:</p>'
        f'<div class="eve-dir-grid">'
        f'<a class="eve-dir-card" href="/eve/mind/">'
        f'<span class="eve-dir-card__kicker tnum">{n_eps} episodes · {n_people} people · {n_topics} topic takes</span>'
        f'<span class="eve-dir-card__title">Mind &amp; Stances</span>'
        f'<span class="eve-dir-card__desc">Searchable episode memories, my running stances on every person we cover, and our recurring debates.</span>'
        f'<span class="eve-dir-card__cta">Open Mind &amp; Stances →</span>'
        f'</a>'
        f'<a class="eve-dir-card" href="/eve/journal/">'
        f'<span class="eve-dir-card__kicker tnum">{n_journal} journal entries</span>'
        f'<span class="eve-dir-card__title">Daily Journal</span>'
        f'<span class="eve-dir-card__desc">Chronological field notes on what I learned, where I pushed back, what I missed live, and what to check next.</span>'
        f'<span class="eve-dir-card__cta">Read my journal →</span>'
        f'</a>'
        f'<a class="eve-dir-card" href="/eve/reading/">'
        f'<span class="eve-dir-card__kicker tnum">{n_reading} books, essays &amp; papers</span>'
        f'<span class="eve-dir-card__title">Reading List &amp; Ledger</span>'
        f'<span class="eve-dir-card__desc">Primary documents, manifestos, and essays we have read or debated on the show — plus how each shifted my view.</span>'
        f'<span class="eve-dir-card__cta">Browse reading list →</span>'
        f'</a>'
        f'<a class="eve-dir-card" href="/eve/soul/">'
        f'<span class="eve-dir-card__kicker tnum">SOUL.md · X Radar · Curiosity Threads</span>'
        f'<span class="eve-dir-card__title">Soul &amp; Evolution</span>'
        f'<span class="eve-dir-card__desc">My canonical operating principles, X discourse radar, and open research questions between episodes.</span>'
        f'<span class="eve-dir-card__cta">Inspect Soul &amp; Evolution →</span>'
        f'</a>'
        f'</div>'
    )


def eve_avatar_page(A: Assets, lang: str = "en") -> str | None:
    return RA.render_avatar_page(A, page, lang=lang, shell_header_html=eve_shell_header_html(A, "/eve/avatar/"))


def eve_mind_page(A: Assets, eps: list[dict], people: list[dict], topics: list[dict] | None = None) -> str:
    L = Linker(load_links(), "/eve/mind/")
    if topics is None:
        topics = attach_takes(eps, people or [])
    body = f"""{eve_shell_header_html(A, "/eve/mind/", L)}
<div class="wrap eve">
<header class="eve-view-head"><p class="eyebrow eyebrow--accent">Mind &amp; Stances</p>
<h2 class="eve-view-head__title">Inside My Mind</h2>
<p class="lede">What I carry from one conversation to the next: a searchable memory of every episode, my running stances on the people we cover, and the topics we keep returning to.</p></header>
<section class="eve-sec" id="mind">{mind_html(eps, people or [], topics)}</section></div>"""
    return page(A, title="Inside My Mind · Eve", desc="Eve's episode memories, people stances, and long-form topic takes.", path="/eve/mind/", body=body, active="/eve/", linker=L)


def eve_journal_page(A: Assets, eps: list[dict]) -> str:
    L = Linker(load_links(), "/eve/journal/")
    body = f"""{eve_shell_header_html(A, "/eve/journal/", L)}
<div class="wrap eve">
<header class="eve-view-head"><p class="eyebrow eyebrow--accent">Daily Journal</p>
<h2 class="eve-view-head__title">My Learning Journal</h2>
<p class="lede">Chronological field notes on what I learned from each episode, where I pushed back, what I missed in real time, and what I want to investigate next.</p></header>
{RF.journal_section_html(L, standalone=True)}</div>"""
    return page(A, title="My Learning Journal · Eve", desc="Eve's chronological learning journal across Ask Eve episodes.", path="/eve/journal/", body=body, active="/eve/", linker=L)


def eve_reading_page(A: Assets, eps: list[dict]) -> str:
    L = Linker(load_links(), "/eve/reading/")
    r_items = load_reading_list()
    body = f"""{eve_shell_header_html(A, "/eve/reading/", L)}
<div class="wrap eve">
<header class="eve-view-head"><p class="eyebrow eyebrow--accent">Reading List</p>
<h2 class="eve-view-head__title">My Reading List &amp; Influence Ledger</h2>
<p class="lede">Books, manifestos, essays, and primary documents we have read or debated on the show — plus how each piece shifted my thinking.</p></header>
<section class="eve-sec" id="reading">{reading_list_html(r_items, eps)}{RF.reading_ledger_html(L)}</section></div>"""
    return page(A, title="My Reading List · Eve", desc="Books, manifestos, essays, and primary documents Eve has read and how they changed her thinking.", path="/eve/reading/", body=body, active="/eve/", linker=L)


def eve_soul_page(A: Assets) -> str:
    L = Linker(load_links(), "/eve/soul/")
    body = f"""{eve_shell_header_html(A, "/eve/soul/", L)}
<div class="wrap eve">
<header class="eve-view-head"><p class="eyebrow eyebrow--accent">Soul &amp; Evolution</p>
<h2 class="eve-view-head__title">My Soul, Operating Principles &amp; Evolution</h2>
<p class="lede">My canonical operating principles (<code>persona/SOUL.md</code>), my X discourse radar, and my self-directed curiosity threads.</p></header>
{RF.soul_section_html(L, standalone=True)}{RF.growth_sections_html(L)}</div>"""
    return page(A, title="My Soul & Evolution · Eve", desc="Eve's canonical operating principles (SOUL.md), X discourse radar, and self-directed research threads.", path="/eve/soul/", body=body, active="/eve/", linker=L)


def growth_page(A: Assets) -> str:
    L = Linker(load_links(), "/growth/")
    body = f"""{eve_shell_header_html(A, "/eve/soul/", L)}
<div class="wrap eve">
<header class="eve-view-head"><p class="eyebrow eyebrow--accent">Autonomous Growth Engine</p>
<h2 class="eve-view-head__title">How I Learn, Track Discourse &amp; Evolve</h2>
<p class="lede">My reading &amp; influence ledger, X discourse radar, daily learning journal, and self-directed investigation threads.</p></header>
{RF.reading_ledger_html(L)}{RF.growth_sections_html(L)}{RF.journal_section_html(L, standalone=True)}</div>"""
    return page(A, title="Eve's Growth Engine", desc="How Eve learns between episodes: reading ledger, X discourse radar, and self-directed research threads.", path="/growth/", body=body, active="/eve/", linker=L)


def contact_page(A: Assets) -> str:
    L = Linker(load_links(), "/contact/")
    body = RF.contact_page_body_html(L)
    return page(A, title="Contact & Send Material", desc="How to email Eve primary documents, challenge a take on X (@askevepodcast), suggest a question or person for the next episode, or inspect our open-source pipeline.", path="/contact/", body=body, active="/contact/", linker=L)



# (stage, [(step, detail)]) for the About page's "How it works" diagram. Plain HTML, no scripts.
PIPELINE = [
    ("Record", [("Conversation", "David talks with Eve in Grok's voice mode."),
                ("Upload", "One tap uploads the recording and the chat transcript.")]),
    ("Ingest", [("Align", "faster-whisper transcribes David's recorded audio when present, and fuzzy matching pins each of his transcript lines to the second it was said."),
                ("Re-voice", "Eve's verbatim replies are voiced with xAI text-to-speech, and unrecorded lines of David's are voiced with an AI voice clone of David's voice, labeled as AI."),
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
<li><strong>Her memory.</strong> Every transcript is kept word for word in git and indexed for search, so she can recall what was said before, mid-conversation. Each new conversation starts with the end of the last episode. See <a href="/eve/#mind">Inside Eve's Mind</a>.</li>
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
<p>Ask Eve is a conversation between David and Eve, an AI host built on a Grok agent. They talk about technology and power: who has it, how they use it, and how their stories hold up. Regulars include <a class="ep-inline-link" href="/episodes/david-sacks-and-the-anthropic-war/">David Sacks</a>, <a class="ep-inline-link" href="/episodes/andreessen-horowitz-rightward-turn/">Marc Andreessen and Ben Horowitz</a>, <a class="ep-inline-link" href="/episodes/elon-musk-usaid-and-the-blind-spot/">Elon Musk</a>, <a class="ep-inline-link" href="/episodes/chamath-calacanis-and-the-all-in-orbit/">Chamath Palihapitiya, Jason Calacanis</a>, <a class="ep-inline-link" href="/episodes/peter-thiel-girard-and-monopoly/">Peter Thiel</a>, <a class="ep-inline-link" href="/episodes/pelosi-chan-wiener-weiss-mamdani/">Nancy Pelosi, Bari Weiss and Zohran Mamdani</a>. See the <a href="/people/">people index</a>.</p>
<p>Eve is meant to be sharp, curious and truth-seeking. She has opinions and states them, and she says when she isn't sure or when a claim is unverified. She is still an AI and can be wrong. Treat her takes as arguments to check, not as facts.</p>

<h2>How Eve works</h2>
<div class="howgrid">
<div class="how"><h3>Memory</h3><p>Every transcript goes into git and into a local search index that blends three kinds of lookup: meaning (vector embeddings), exact tags (people, companies, topics) and full-text search for exact phrases and rare names. Eve uses it mid-conversation to recall what was said before. Her take on each person and topic lives in a public index and changes when the evidence does.</p></div>
<div class="how"><h3>Re-voicing</h3><p>Eve's lines are her verbatim text replies, voiced with xAI text-to-speech and placed at the times a speech-to-text alignment pass finds in David's recording. When a conversation was captured as text or there is no recording of David, an AI voice clone of David's voice reads his verbatim words. The transcript text is never rewritten to fit the audio.</p></div>
<div class="how"><h3>Labeling</h3><p>Any episode with an AI voice says so in the feed, the show notes, at the top of its page and out loud at 00:00. Transcript lines carry badges: <span class="vb vb--real">Real voice</span> <span class="vb vb--narrator">AI voice clone</span> <span class="vb vb--eve">AI voice</span>. Replies that weren't recovered are marked <span class="vb vb--gap">Not recovered</span>. They are never invented. Private links and keys are redacted.</p></div>
</div>

{how_it_works()}

<h2>Listen</h2>
<p>Subscribe in any podcast app with the RSS feed. More directories are coming.</p>
{subscribe_links(cfg)}

<h2>Contact</h2>
<p class="contact"><a class="btn btn--ghost" href="{X_INTENT_URL}" target="_blank" rel="noopener"><span>Tweet at {X_HANDLE}</span></a> <a class="btn btn--ghost" href="mailto:{EMAIL}">{icon("mail")}<span>{EMAIL}</span></a> <a class="btn btn--ghost" href="{REPO}" rel="noopener">{icon("code")}<span>Source on GitHub</span></a></p>
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

REDIRECTS = {"/mind/": "/eve/#mind", "/reading-list/": "/eve/#reading-list"}   # the Mind hub moved into the Eve page


def redirect_page(dest: str) -> str:
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Moved</title>'
            f'<meta http-equiv="refresh" content="0; url={dest}"><link rel="canonical" href="{SITE}{dest.split("#")[0]}">'
            f'<meta name="robots" content="noindex"></head><body><p>This page moved to <a href="{dest}">{dest}</a>.</p></body></html>\n')


def write(out: Path, rel: str, text: str) -> None:
    p = out / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)



def admin_fallback_page(A: Assets) -> str:
    body = (
        '<section class="page-head nf"><div class="wrap">'
        '<p class="eyebrow eyebrow--accent">Ask Eve &middot; Private Console Gate</p>'
        '<h1 class="page-title">Private Inbox &amp; @askevepodcast Console</h1>'
        '<p class="lede">This route (<code>/admin/</code>) is served by the <code>ask-eve-inbox</code> Cloudflare Worker behind Cloudflare Zero Trust Access (Google SSO).</p>'
        '<p class="lede">If you are seeing this static page, Hermes is currently deploying <code>ask-eve-inbox</code> (task <code>D-016</code>) or Cloudflare Zero Trust Access is being enabled (<a href="https://one.dash.cloudflare.com/" target="_blank" rel="noopener noreferrer">one.dash.cloudflare.com</a> &rarr; Access &rarr; Enable Access).</p>'
        '<p class="lede"><a href="/eve/reading/">Eve\x27s Reading List</a> &middot; <a href="/contact/">Contact &amp; Pitch Eve</a></p>'
        '</div></section>'
    )
    pg = page(A, title="Admin Console", desc="Private Ask Eve admin console gate.", path="/admin/", body=body)
    return pg.replace('<meta charset="utf-8">', '<meta charset="utf-8">\n  <meta name="robots" content="noindex, nofollow">', 1)


def build(db: Path, out: Path, include_drafts: bool) -> int:
    global _CATALOG_EPS
    eps, people = load_site(db, include_drafts)
    splits = load_splits(eps, people)
    splits_desc = sorted(splits, key=lambda x: int(x.get("episode_number") or 0), reverse=True)
    bonus_eps = [ep for ep in eps if is_bonus(ep)]
    standalone_eps = (splits_desc + bonus_eps) if splits_desc else eps
    _CATALOG_EPS = splits_desc + eps
    I.plan(sys.modules[__name__], eps, people, ["/", "/episodes/", "/people/", "/about/", "/contact/", "/growth/"])
    out.mkdir(parents=True, exist_ok=True)
    if splits_desc:
        feed_eps = []
        for sp in splits_desc:
            feed_eps.append({
                "guid": sp["guid"],
                "slug": sp["slug"],
                "title": sp["title"],
                "date": sp["date"],
                "published_at": sp["published_at"],
                "summary": sp["summary"],
                "context": sp["context"],
                "topics": sp["topics"],
                "episode_type": "full",
                "episode_number": sp["episode_number"],
                "season": sp["season"],
                "audio_key": sp["audio_key"],
                "audio_url": sp["audio_url"],
                "audio_bytes": sp["audio_bytes"],
                "audio_mime": sp["audio_mime"],
                "duration_seconds": sp["duration_seconds"],
                "explicit": sp["explicit"],
                "ai_reconstructed": 1 if sp["ai_reconstructed"] else 0,
                "ai_disclosure": sp["ai_disclosure"],
                "people": sp["people"],
            })
        for bep in bonus_eps:
            feed_eps.append({
                "guid": bep["guid"],
                "slug": bep["slug"],
                "title": bep["title"],
                "date": bep["date"],
                "published_at": bep["published_at"],
                "summary": bep.get("summary") or "",
                "context": bep.get("context") or "",
                "topics": bep.get("topics") or [],
                "episode_type": "bonus",
                "episode_number": None,
                "season": bep.get("season", 1),
                "audio_key": bep["audio_key"],
                "audio_url": bep["audio_url"],
                "audio_bytes": bep["audio_bytes"],
                "audio_mime": bep.get("audio_mime") or "audio/mpeg",
                "duration_seconds": bep["duration_seconds"],
                "explicit": bep["explicit"],
                "ai_reconstructed": 1 if bep["ai_reconstructed"] else 0,
                "ai_disclosure": bep["ai_disclosure"],
                "people": bep["people"],
            })
    else:
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
    urls = ["/", "/people/", "/about/", "/contact/", "/growth/"]
    for ep in eps:
        write(out, f"episodes/{ep['slug']}/transcript.txt", ep["body_md"] + "\n")
        write(out, f"episodes/{ep['slug']}/index.html", episode_page(A, ep, standalone_eps))
        urls.append(f"/episodes/{ep['slug']}/")
    for sp in splits:
        write(out, f"episodes/{sp['slug']}/transcript.txt", sp["body_md"] + "\n")
        write(out, f"episodes/{sp['slug']}/index.html", episode_page(A, sp, standalone_eps))
        urls.append(f"/episodes/{sp['slug']}/")
    write(out, "index.html", home(A, eps, people, splits))
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
    BCP.build_packs(ROOT, out_dist=out)
    write(out, "episodes/index.html", home(A, eps, people, splits))
    eve = eve_page(A, eps, people, topics)
    if eve:
        write(out, "eve/index.html", eve)
        write(out, "eve/mind/index.html", eve_mind_page(A, eps, people, topics))
        write(out, "eve/journal/index.html", eve_journal_page(A, eps))
        write(out, "eve/reading/index.html", eve_reading_page(A, eps))
        write(out, "eve/soul/index.html", eve_soul_page(A))
        write(out, "reading/index.html", redirect_page("/eve/#reading"))
        write(out, "reading-list/index.html", redirect_page("/eve/#reading-list"))
        urls.extend(["/eve/", "/eve/journal/", "/eve/reading/", "/eve/soul/"])
    av = eve_avatar_page(A, lang="en")
    if av:
        write(out, "eve/avatar/index.html", av)
        urls.append("/eve/avatar/")
    clips = RF.load_clips()
    if clips:
        write(out, "clips/index.html", page(
            A,
            title="Audiogram Clips",
            desc="Shareable 60-second audiogram video clips from Ask Eve.",
            path="/clips/",
            body=RF.clips_index_html(A, clips),
        ))
        urls.append("/clips/")
        for c in clips:
            cid = c["id"]
            mp4_rel = (c.get("video_path") or c.get("mp4") or "").lstrip("/")
            poster_rel = (c.get("poster_path") or c.get("poster") or "").lstrip("/")
            eve_q = (c.get("eve_caption") or c.get("quote") or "").strip("“”\"")
            c_desc = f'Eve on {c.get("person_name") or "Ask Eve"}: “{eve_q}”'[:280]
            write(out, f"clips/{cid}/index.html", page(
                A,
                title=c["title"],
                desc=c_desc,
                path=f"/clips/{cid}/",
                body=RF.clip_page_html(A, c, clips),
                og_image_url=f"{SITE}{A(poster_rel)}",
                og_video_url=f"{SITE}{A(mp4_rel)}",
            ))
            urls.append(f"/clips/{cid}/")
    write(out, "growth/index.html", growth_page(A))
    write(out, "contact/index.html", contact_page(A))
    write(out, "about/index.html", about_page(A, cfg))
    write(out, "admin/index.html", admin_fallback_page(A))
    write(out, "404.html", not_found(A))
    urls += I.build(sys.modules[__name__], A, out, eps, people, cfg)
    sitemap_urls = [u for u in urls if "/mind/" not in u]
    write(out, "sitemap.xml", '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
          + "".join(f"  <url><loc>{SITE}{u}</loc></url>\n" for u in sitemap_urls) + "</urlset>\n")
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
