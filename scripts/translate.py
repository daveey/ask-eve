#!/usr/bin/env python3
"""Translate the site's published content into another language and cache it in the repo (default: Russian).

  python3 scripts/translate.py                # content: titles, Context, Topics, transcripts, chapters, moments, stances
  python3 scripts/translate.py --ui           # then: UI strings missing from i18n/<lang>.json (review them by hand)
  python3 scripts/translate.py --check        # no API: list what is missing, exit 1 if anything is

Output (committed, reviewable): translations/<lang>/<slug>.json, _people.json and _eve.json (the Meet Eve page), each
{"strings": {<sha256(en)[:16]>: {"kind", "en", "tr"}}} in page order. Only text whose hash is not cached yet is sent
to the API, entries whose English source is gone are dropped, so re-runs are cheap and the build stays deterministic
(scripts/build_site.py only reads the cache; CI needs no API key).

Transcripts are translated turn by turn (one cache entry per speaker turn, so the translated page keeps the same
turns, timestamps and seek points), in small batches with the neighbouring English turns as context.
Publish gate: only episodes live on the site (listed in https://askevepodcast.com/sitemap.xml) are translated,
unless --slug names one explicitly. Needs XAI_API_KEY in the environment (never logged). Model grok-4.7, temperature 0.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import http.client
import json
import os
import re
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_site as B  # noqa: E402
import site_i18n as I  # noqa: E402
import sqlite_apply  # noqa: E402

ROOT = B.ROOT
MODEL = os.environ.get("TRANSLATE_MODEL", "grok-4.7")
EFFORT = os.environ.get("TRANSLATE_EFFORT", "low")
API = "https://api.x.ai/v1/chat/completions"
LANG_NAMES = {"ru": "Russian", "es": "Spanish", "zh": "Mandarin Chinese (Simplified)", "de": "German", "pt": "Portuguese", "fa": "Persian (Farsi)", "fr": "French"}
BATCH_CHARS = 8500
BATCH_ITEMS = 22
CTX_CHARS = 700
_usage: dict[str, float] = {}
_lock = threading.Lock()


# ---------------------------------------------------------------- API

def read_stream(r) -> dict:
    """Server-sent events -> {"choices": [{"message": {"content": ...}}], "usage": {...}} like a plain response."""
    text, usage, done, last, t0 = [], {}, False, "", time.time()
    for raw in r:
        line = raw.decode().strip()
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload == "[DONE]":
            done = True
            break
        ev = json.loads(payload)
        last = payload[-160:]
        usage = ev.get("usage") or usage
        for ch in ev.get("choices") or []:
            text.append((ch.get("delta") or {}).get("content") or "")
    if not done:
        raise ValueError(f"stream ended early after {time.time() - t0:.0f}s, {len(text)} chunks; last={last!r}")
    return {"choices": [{"message": {"content": "".join(text)}}], "usage": usage}


def chat(messages: list[dict], key: str, retries: int = 8) -> dict:
    schema = {"type": "object", "additionalProperties": False, "required": ["items"], "properties": {"items": {
        "type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["id", "text"],
                                   "properties": {"id": {"type": "string"}, "text": {"type": "string"}}}}}}
    body = {"model": MODEL, "temperature": 0, "messages": messages, "stream": True, **({"reasoning_effort": EFFORT} if EFFORT else {}),
            "stream_options": {"include_usage": True},   # streamed: long calls keep the connection alive
            "response_format": {"type": "json_schema", "json_schema": {"name": "out", "schema": schema, "strict": True}}}
    req = urllib.request.Request(API, data=json.dumps(body).encode(), method="POST",
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    for k in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                data = read_stream(r)
            with _lock:
                for n, v in (data.get("usage") or {}).items():
                    if isinstance(v, (int, float)):
                        _usage[n] = _usage.get(n, 0) + v
                    elif isinstance(v, dict):
                        for n2, v2 in v.items():
                            if isinstance(v2, (int, float)):
                                _usage[f"{n}.{n2}"] = _usage.get(f"{n}.{n2}", 0) + v2
            return json.loads(data["choices"][0]["message"]["content"])
        except (OSError, http.client.HTTPException, ValueError, KeyError, IndexError) as exc:   # URLError/HTTPError/timeouts are OSError
            msg = f"HTTP {exc.code}" if isinstance(exc, urllib.error.HTTPError) else f"{type(exc).__name__}: {str(exc)[:80]}"
            print(f"  xAI call failed ({msg}); retry {k + 1}/{retries}", file=sys.stderr)
            time.sleep(min(90, 10 * (k + 1)))
    raise SystemExit("xAI API failed repeatedly")


def system_prompt(lang: str) -> str:
    s = I.S(lang)
    names = "\n".join(f"- {k} = {v}" for k, v in {**s.get("names", {}), **s.get("glossary", {})}.items())
    return f"""You are a professional English-to-{LANG_NAMES[lang]} translator for "Ask Eve", a conversational podcast
about tech, power and politics. David (a human host) talks with Eve (an AI co-host). Most text is SPOKEN conversation
(voice-mode transcripts) or chat messages; translate it into natural, native-quality spoken {LANG_NAMES[lang]}, the way an
articulate native speaker would actually say it on a podcast. Not stiff, not literal, not bureaucratic.

Rules:
- Translate the meaning completely and faithfully. Do not add, drop, soften, summarize or "fix" claims, opinions,
  hedges, jokes, numbers or names. Keep the speaker's tone (Eve: sharp, confident, opinionated; David: casual).
- David and Eve address each other informally (на «ты» in Russian, de «tú» in Spanish, 用「你」 in Chinese).
- Names: use the standard {LANG_NAMES[lang]} spellings below; for other people use the established {LANG_NAMES[lang]}
  transliteration. If a name is an obvious speech-recognition misspelling of someone below (e.g. "DeMuth" for Chamath,
  "Madani" for Mamdani, "Peter Teal" for Thiel), write the correct name.
- Numbers spelled out for text-to-speech ("twenty twenty-four", "four and a half million", "a sixteen z") become normal
  written {LANG_NAMES[lang]} (2024, 4,5 млн, a16z).
- Keep Markdown exactly: paragraph breaks (blank lines), list markers, numbering, **bold**, `code`, [text](url). Never
  change a URL, file path, command, code, repo/branch/file name, env var, or anything in backticks.
- Product, company and media names stay in Latin script (Grok, xAI, GitHub, SQLite, Cloudflare, iPhone, AirPods, The
  Free Press, All-In, Anthropic, DOGE, USAID, SPAC) unless the glossary says otherwise. "Ask Eve" is never translated.
- Bracketed editorial notes such as "[link to a private notes page removed]" or "[redacted]" are translated, keeping
  the brackets. ASR glitches and fillers may be rendered naturally; do not invent content to fill them.
- Use typographic {LANG_NAMES[lang]} quotes «…» and the em dash where natural.
- Return JSON {{"items": [{{"id": ..., "text": ...}}]}} with exactly one item for every id in TRANSLATE, in order, and
  nothing for CONTEXT (context is only there to help you understand the conversation).

Names and terms:
{names}"""


# ---------------------------------------------------------------- jobs

def check(src: str, out: str) -> list[str]:
    """Structural sanity checks on one translation; returns problems (empty = ok)."""
    probs = []
    if not out.strip():
        return ["empty"]
    for u in re.findall(r"https?://[^\s)\]>]+", src):
        if u.rstrip(".,;:!?") not in out:
            probs.append(f"url changed: {u}")
    if len(re.findall(r"`[^`\n]+`", src)) != len(re.findall(r"`[^`\n]+`", out)):
        probs.append("code spans differ")
    if re.search(r"[A-Za-z]{3}", src) and len(src) > 40:
        if not (re.search(r"[\u0400-\u04ff\u4e00-\u9fff]", out) or out != src):
            probs.append("untranslated")
    if src.count(I.A_OPEN) != out.count(I.A_OPEN) or src.count(I.A_CLOSE) != out.count(I.A_CLOSE):
        probs.append("⟦anchor⟧ marks differ")
    pa, pb = len([p for p in src.split("\n\n") if p.strip()]), len([p for p in out.split("\n\n") if p.strip()])
    if pa != pb:
        probs.append(f"paragraphs {pa}->{pb}")
    r = len(out) / max(1, len(src))
    min_r = 0.15 if re.search(r"[\u4e00-\u9fff]", out) else 0.50
    if len(src) > 200 and not min_r <= r <= 2.5:
        probs.append(f"length ratio {r:.2f}")
    return probs


def run_batch(lang: str, key: str, header: str, items: list[dict], ctx_before: list[str], ctx_after: list[str]) -> dict[str, str]:
    parts = [header]
    if ctx_before:
        parts.append("CONTEXT (before, do not translate):\n" + "\n\n".join(ctx_before))
    parts.append("TRANSLATE:\n" + "\n\n".join(f'<<{x["id"]}>> ({x["label"]})\n{x["en"]}' for x in items))
    if ctx_after:
        parts.append("CONTEXT (after, do not translate):\n" + "\n\n".join(ctx_after))
    msgs = [{"role": "system", "content": system_prompt(lang)}, {"role": "user", "content": "\n\n".join(parts)}]
    got = {x["id"]: x["text"] for x in chat(msgs, key).get("items", [])}
    out, redo = {}, []
    for x in items:
        t = got.get(x["id"], "")
        probs = check(x["en"], t)
        if probs and len(items) > 1:
            redo.append(x)
        else:
            if probs:
                print(f"  warning {x['id']}: {'; '.join(probs)}", file=sys.stderr)
            out[x["id"]] = t.strip()
    for x in redo:   # retry problem items one at a time, with the batch's context
        out.update(run_batch(lang, key, header, [x], ctx_before, ctx_after))
    return out


def clip(s: str) -> str:
    return s if len(s) <= CTX_CHARS else s[:CTX_CHARS] + " […]"


def episode_jobs(ep: dict, todo: set[str]) -> list[tuple[str, list[dict], list[str], list[str]]]:
    """Batches for one episode: page strings first, then turns in order with neighbouring turns as context."""
    header = f'Episode: "{ep["title"]}". About this episode: {ep.get("context") or ep.get("blurb") or ""}'
    misc = [{"id": I.key(t), "label": k, "en": t} for k, t in I.episode_strings(ep)
            if not k.startswith("turn") and I.key(t) in todo]
    jobs = []
    for k in range(0, len(misc), 25):
        jobs.append((header + "\nThese are page elements (title, notes, topic list items, chapter titles, discussion "
                     "summaries); translate each on its own, concise like the English.", misc[k:k + 25], [], []))
    turns = ep["turns"]
    line = lambda t: f"{t['speaker']} [{t['clock']}]: {t['text']}"  # noqa: E731
    batch: list[int] = []

    def flush():
        if not batch:
            return
        a, b = batch[0], batch[-1]
        items = []
        for i in batch:
            t = turns[i]
            if not t["gap"] and I.key(t["text"]) in todo:
                items.append({"id": I.key(t["text"]), "label": f"{t['speaker']}, turn {i}", "en": t["text"]})
            for m in t["meta"]:
                if I.key(m) in todo:
                    items.append({"id": I.key(m), "label": f"metadata line of turn {i}", "en": m})
        seen, uniq = set(), []
        for x in items:
            if x["id"] not in seen:
                seen.add(x["id"]); uniq.append(x)
        if uniq:
            jobs.append((header, uniq, [clip(line(turns[j])) for j in range(max(0, a - 2), a)],
                         [clip(line(turns[j])) for j in range(b + 1, min(len(turns), b + 3))]))
        batch.clear()

    size = 0
    for t in turns:
        n = len(t["text"]) + sum(len(m) for m in t["meta"])
        if batch and (size + n > BATCH_CHARS or len(batch) >= BATCH_ITEMS):
            flush(); size = 0
        batch.append(t["i"]); size += n
    flush()
    return jobs


def eve_text_jobs(texts: list[tuple[str, str]], todo: set[str]) -> list[tuple[str, list[dict], list[str], list[str]]]:
    items = [{"id": I.key(t), "label": k, "en": t} for k, t in texts if k != "eve quote" and I.key(t) in todo]
    header = ("These strings are from the show's \"Meet Eve\" page: a profile of Eve, the AI co-host, mostly in her own "
              "words (\"I\" = Eve). Headings stay short; sentences stay natural spoken-style Russian.")
    return [(header, items[k:k + 20], [], []) for k in range(0, len(items), 20)]


def take_jobs(tk: dict[str, list[tuple[str, str]]], names: dict[str, str], todo: set[str]) -> list:
    """One batch per person/topic take: role, lede, paragraphs (with ⟦anchor⟧ marks), source labels, terms."""
    header = ("These strings are from Eve's long-form \"take\" on {who} on the show's website: Eve (the AI co-host) "
              "writing in the first person, opinionated and direct; translate into natural, articulate Russian. "
              "Some phrases are wrapped in ⟦…⟧: they link to the moment in the episode where Eve said it. Keep exactly "
              "one ⟦…⟧ around the Russian words that render that phrase (same number of marks, never nested). "
              "'take … ref' items are short labels of those moments; 'topic term' items are phrases that will be "
              "auto-linked in Russian text: give the most common Russian form (nominative), or keep the term as is "
              "if it is a name or acronym that stays in Latin script.")
    jobs = []
    for owner, items in tk.items():
        xs = [{"id": I.key(t), "label": k, "en": t} for k, t in items if I.key(t) in todo]
        seen = set()
        xs = [x for x in xs if not (x["id"] in seen or seen.add(x["id"]))]
        if xs:
            kind, slug = owner.split(":", 1)
            who = f"the person {names.get(slug, slug)}" if kind == "person" else "a topic"
            jobs.append((header.format(who=who), xs, [], []))
    return jobs


def _norm(s: str) -> str:
    return B._norm(s.replace(" [sic]", ""))


def run_quotes(lang: str, key: str, quotes: list[dict], eps: list[dict], todo: set[str]) -> dict[str, str]:
    """Eve-page quotes: each must be the matching span of the CACHED translation of its transcript turn, so the
    Russian quote reads exactly like the Russian transcript it links to. Spans that don't match are retried once
    singly, then kept as a free translation with a warning."""
    R = B.EveSources(eps)
    work = []
    for src in quotes:
        k = I.key(src["quote_excerpt"])
        if k not in todo or any(w["id"] == k for w in work):
            continue
        r = R.resolve(src)
        if r is None or r["gap"]:
            continue
        work.append({"id": k, "en": src["quote_excerpt"], "turn_en": r["turn"]["text"], "turn_tr": I.tr(lang, r["turn"]["text"])})
    out: dict[str, str] = {}

    def ask(batch):
        body = "\n\n".join(f'<<{w["id"]}>>\nENGLISH TURN:\n{w["turn_en"]}\n\n{LANG_NAMES[lang].upper()} TURN:\n{w["turn_tr"]}'
                            f'\n\nENGLISH EXCERPT:\n{w["en"]}' for w in batch)
        msgs = [{"role": "system", "content": system_prompt(lang)},
                {"role": "user", "content": "Each item below is an English excerpt quoted from a transcript turn, with that turn "
                 f"in English and in its existing {LANG_NAMES[lang]} translation. For each, return the span of the "
                 f"{LANG_NAMES[lang]} turn that corresponds to the English excerpt, copied character for character "
                 "(same words, punctuation and quotes; start and end at the matching words; no ellipses, nothing added). "
                 "If the excerpt ends in [sic], keep [sic] at the end.\n\n" + body}]
        got = {x["id"]: x["text"].strip() for x in chat(msgs, key).get("items", [])}
        bad = []
        for w in batch:
            t = got.get(w["id"], "")
            if t and _norm(t) in _norm(w["turn_tr"]):
                out[w["id"]] = t
            else:
                bad.append(w)
        return bad

    pending = []
    batches = [work[k:k + 15] for k in range(0, len(work), 15)]
    with cf.ThreadPoolExecutor(8) as pool:
        for bad in pool.map(ask, batches):
            pending += bad
    for w in pending:
        if ask([w]):
            got = run_batch(lang, key, "A quote from Eve's profile page; context is the full turn it comes from.",
                            [{"id": w["id"], "label": "quote", "en": w["en"]}], [clip(w["turn_en"])], [])
            out.update(got)
            print(f"  warning {w['id']}: quote is not a verbatim span of the translated turn; free translation kept",
                  file=sys.stderr)
    return out


# ---------------------------------------------------------------- cache files

PRIVATE = [   # never stored in the cache (neither `en` nor `tr`), whatever the English source says
    (re.compile(r"https?://(?:www\.)?grok\.com/share/\S+?(?=[)\]\s>`\"']|[.,;:!?](?:\s|$)|$)"), "[redacted link]"),
    (re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I), "[redacted agent ID]"),
    (re.compile(r"\b(?:bc|agent)-[0-9a-f]{8,}\b", re.I), "[redacted agent ID]"),
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "[redacted email]"),
    (re.compile(r"https?://\S*canvas\S*", re.I), "[redacted link]"),
]


def scrub(text: str) -> str:
    for rx, rep in PRIVATE:
        text = rx.sub(rep, text)
    return text


def write_cache(lang: str, name: str, items: list[tuple[str, str]], new: dict[str, str]) -> int:
    path = I.CACHE_DIR / lang / f"{name}.json"
    raw = json.loads(path.read_text()) if path.exists() else {}
    old = raw.get("strings", {})
    extra = {k: v for k, v in raw.items() if k not in {"_doc", "lang", "source_lang", "model", "temperature", "strings"}}
    strings = {}
    for kind, en in items:
        k = I.key(en)
        if k in new:
            strings[k] = {"kind": kind, "en": scrub(en), "tr": scrub(new[k])}
        elif k in old:
            strings[k] = {**old[k], "kind": kind, "en": scrub(en), "tr": scrub(old[k]["tr"])}
        elif k in I._C.get(lang, {}):
            strings[k] = {"kind": kind, "en": scrub(en), "tr": scrub(I._C[lang][k])}
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = {"_doc": f"Machine translation cache ({LANG_NAMES[lang]}) written by scripts/translate.py; key = sha256(en)[:16]. "
                   "Hand edits to `tr` are kept as long as `en` is unchanged.",
           "lang": lang, "source_lang": "en", "model": MODEL, "temperature": 0, **extra, "strings": strings}
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n")
    return len(strings)


def live_slugs() -> set[str]:
    req = urllib.request.Request(f"{B.SITE}/sitemap.xml", headers={"User-Agent": "ask-eve-translate/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310
        return set(re.findall(r"/episodes/([a-z0-9-]+)/", r.read().decode()))


def load(db: Path | None) -> tuple[list[dict], list[dict]]:
    if db is None:
        db = Path(tempfile.mkdtemp()) / "local.sqlite"
        for sql in sorted(ROOT.glob("artifacts/*/d1_upsert.sql")):
            sqlite_apply.apply(db, ROOT / "migrations", sql)
    eps, people = B.load_site(db, include_drafts=True)
    B.load_splits(eps, people)
    return eps, people


# ---------------------------------------------------------------- UI strings

def ui_pass(lang: str, key: str, eps: list[dict], people: list[dict], splits: list[dict] | None = None) -> int:
    """Render the translated site in memory, collect untranslated UI strings, translate and merge them."""
    with tempfile.TemporaryDirectory() as tmp:
        I.reset()
        I.plan(B, eps, people, ["/", "/episodes/", "/people/", "/about/", "/contact/", "/growth/"])
        A = B.Assets(Path(tmp))
        cfg = eps[0]["cfg"] if eps else B.site_cfg()
        I.build(B, A, Path(tmp), eps, people, cfg)
        miss = I.misses(lang)
    if not miss:
        print("UI: nothing missing")
        return 0
    items = [{"id": f"u{k}", "label": "UI string", "en": m} for k, m in enumerate(miss)]
    header = ("These are UI strings of the podcast website (navigation, headings, buttons, labels, short notes, and "
              "pieces of About-page paragraphs that are split around links: translate each piece so the pieces read "
              "naturally when joined in order). Keep proper nouns, 'Ask Eve', RSS, MP3, email addresses and domains "
              "as is. Use a neutral, friendly website register (обращение на «вы» к читателю).")
    out = {}
    ui_batches = [items[k:k + 30] for k in range(0, len(items), 30)]
    with cf.ThreadPoolExecutor(min(12, max(1, len(ui_batches)))) as pool:
        for res in pool.map(lambda b: run_batch(lang, key, header, b, [], []), ui_batches):
            out.update(res)
    p = I.I18N_DIR / f"{lang}.json"
    doc = json.loads(p.read_text())
    for x in items:
        if x["id"] in out and out[x["id"]]:
            doc["strings"][x["en"]] = scrub(out[x["id"]])
    p.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n")
    print(f"UI: added {len(items)} strings to {p.relative_to(ROOT)}; review them")
    return len(items)


# ---------------------------------------------------------------- main

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lang", default="ru", choices=sorted(LANG_NAMES))
    ap.add_argument("--slug", action="append", help="translate this episode even if it is not on the live sitemap")
    ap.add_argument("--db", type=Path, help="local SQLite (default: built from artifacts/*/d1_upsert.sql)")
    ap.add_argument("--ui", action="store_true", help="also translate missing UI strings")
    ap.add_argument("--check", action="store_true", help="no API calls: report missing translations")
    ap.add_argument("--workers", type=int, default=3)
    a = ap.parse_args(argv)
    lang = a.lang
    eps, people = load(a.db)
    allowed = set(a.slug or []) or live_slugs()
    eps = [ep for ep in eps if ep["slug"] in allowed]
    published_people = [p for p in people]   # the people index is public; stances are published there
    plan = {ep["slug"]: I.episode_strings(ep) for ep in eps}
    split_items: list[tuple[str, str]] = []
    seen_sp: set[str] = set()
    for ep in eps:
        for sp in ep.get("splits", []):
            for k, t in I.episode_strings(sp):
                if t not in seen_sp:
                    seen_sp.add(t)
                    split_items.append((k, t))
    if split_items:
        plan["_splits"] = split_items
    plan["_people"] = I.people_strings(published_people)
    eve = I.eve_data(B)
    published = {ep["slug"] for ep in eps}
    if eve and published:
        plan["_eve"] = I.eve_strings(eve, published)
    tk = I.take_strings(I.takes_data(B), published, {p["slug"] for p in published_people}) if published else {}
    if tk:
        plan["_takes"] = [x for items in tk.values() for x in items]
    todo = {n: {I.key(t) for t in I.missing(lang, items)} for n, items in plan.items()}
    total = sum(len(v) for v in todo.values())
    print(f"{lang}: {', '.join(f'{n}: {len(todo[n])}/{len(plan[n])} to translate' for n in plan)}")
    if a.check:
        return 1 if total else 0
    key = os.environ.get("XAI_API_KEY", "").strip()
    if total or a.ui:
        if not key:
            raise SystemExit("XAI_API_KEY is not set")
    t0 = time.time()
    new: dict[str, str] = {}
    jobs = []
    for ep in eps:
        jobs += episode_jobs(ep, todo[ep["slug"]])
    if "_splits" in plan and todo["_splits"]:
        parent_todo = set().union(*(todo[ep["slug"]] for ep in eps)) if eps else set()
        sp_only = todo["_splits"] - parent_todo
        sp_xs = [{"id": I.key(t), "label": f"split {k}", "en": t} for k, t in plan["_splits"] if I.key(t) in sp_only]
        for k in range(0, len(sp_xs), 25):
            jobs.append(("Standalone topic split episode metadata (titles, context, topic pills, chapter titles). Translate concisely.",
                         sp_xs[k:k + 25], [], []))
    ppl = [{"id": I.key(t), "label": "Eve's one-line stance on " + p["name"], "en": t}
           for p in published_people if p.get("stance") and I.key(p["stance"]) in todo["_people"] for t in [p["stance"]]]
    if "_eve" in plan:
        jobs += eve_text_jobs(plan["_eve"], todo["_eve"])
    if tk:
        jobs += take_jobs(tk, {p["slug"]: p["name"] for p in people}, todo["_takes"])
    if ppl:
        jobs.append(("These are Eve's one-line opinions about people on the show's People page. Keep them terse.",
                     ppl, [], []))
    with cf.ThreadPoolExecutor(a.workers) as pool:
        futs = [pool.submit(run_batch, lang, key, *j) for j in jobs]
        for n, f in enumerate(cf.as_completed(futs), 1):
            new.update(f.result())
            print(f"  batch {n}/{len(jobs)} done ({len(new)} strings; {int(_usage.get('total_tokens', 0))} tokens, "
                  f"${_usage.get('cost_in_usd_ticks', 0) / 1e10:.2f} so far)", file=sys.stderr)
            for name, items in plan.items():   # save as we go, so a crash never loses finished batches
                write_cache(lang, name, items, new)
    if "_eve" in plan:     # quotes last: they are cut from the (now cached) translated turns
        for name, items in plan.items():
            write_cache(lang, name, items, new)
        I.reset()
        new.update(run_quotes(lang, key, I.eve_quotes(eve, published), eps, todo["_eve"]))
    for n, items in plan.items():
        cnt = write_cache(lang, n, items, new)
        print(f"  translations/{lang}/{n}.json: {cnt} strings")
    I.reset()
    if a.ui:
        ui_pass(lang, key, eps, people, B.load_splits(eps, people))
    print(json.dumps({"translated": len(new), "seconds": round(time.time() - t0), "model": MODEL, "usage": _usage}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
