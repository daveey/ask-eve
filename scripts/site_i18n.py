"""Translated editions of the site (/ru/... today), kept out of build_site.py's page templates.

How it works:
- Content (episode titles, Context notes, Topics, transcript turns, chapters, moment summaries, people stances) is
  translated ahead of time by scripts/translate.py and cached in translations/<lang>/<slug>.json, keyed by a hash of
  the English source text. The build only reads the cache: no API in CI, deterministic output.
- UI strings (nav, headings, buttons, labels) live in i18n/<lang>.json, keyed by the English string itself, plus a
  few patterns (dates, durations, counts) and the names glossary.
- build_site.py renders a translated page with the SAME template functions on a translated copy of the data, then
  translate_html() swaps UI text nodes/attributes for their translation, points internal links at /<lang>/..., adds
  the "AI translation" note and the language switcher. build_site.py itself only calls the hooks below.
- An episode gets a translated page only when every one of its strings is in the cache, so a transcript edit on main
  never ships a half-translated page; it falls back to the English page until translate.py is re-run.
Audio, the feed and outside links stay English/unchanged.
"""
from __future__ import annotations

import copy
import hashlib
import html
import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
I18N_DIR = ROOT / "i18n"
CACHE_DIR = ROOT / "translations"
SOURCE = "en"
LANGS = ["ru"]                       # translated editions; each needs i18n/<lang>.json

_cur = SOURCE                        # language of the page being rendered
_paths: dict[str, set[str]] = {}     # lang -> English paths that have a translated page
_misses: dict[str, set[str]] = {}    # lang -> UI strings with no translation (build warning)


# ---------------------------------------------------------------- data files

def key(text: str) -> str:
    """Cache key: sha256 of the exact English source text (first 16 hex chars)."""
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def strings(lang: str) -> dict:
    p = I18N_DIR / f"{lang}.json"
    return json.loads(p.read_text()) if p.exists() else {}


_S: dict[str, dict] = {}
_C: dict[str, dict[str, str]] = {}


def S(lang: str) -> dict:
    if lang not in _S:
        _S[lang] = strings(lang)
    return _S[lang]


def cache(lang: str) -> dict[str, str]:
    """All cached content translations for a language: hash -> translated text."""
    if lang not in _C:
        out: dict[str, str] = {}
        for f in sorted((CACHE_DIR / lang).glob("*.json")) if (CACHE_DIR / lang).exists() else []:
            for k, v in json.loads(f.read_text()).get("strings", {}).items():
                out[k] = v["tr"]
        _C[lang] = out
    return _C[lang]


def reset() -> None:
    _S.clear(); _C.clear(); _paths.clear(); _misses.clear()


# ---------------------------------------------------------------- what gets translated

def chapter_text(title: str) -> tuple[str, str]:
    """('07:29 David (AI-reconstructed, narrator voice): ', 'What happened…') - prefix kept, text translated."""
    m = re.match(r"^(\d{2}:\d{2} (?:David|Eve) \([^)]+\): )(.*)$", title)
    return (m.group(1), m.group(2).strip()) if m else ("", title)


def episode_strings(ep: dict) -> list[tuple[str, str]]:
    """(kind, English text) for every published string of an episode page, in page order."""
    out = [("title", ep["title"])]
    if ep.get("context"):
        out.append(("context", ep["context"]))
    out += [("topic", t) for t in ep.get("topics", [])]
    if ep.get("blurb") and ep["blurb"] != ep.get("context"):
        out.append(("blurb", ep["blurb"]))
    if ep.get("card_summary") and ep["card_summary"] not in (ep.get("context"), ep.get("blurb")):
        out.append(("card_summary", ep["card_summary"]))
    if ep.get("ai_disclosure"):
        out.append(("disclosure", ep["ai_disclosure"]))
    for k in ("note", "redactions"):
        if isinstance(ep["fm"].get(k), str) and ep["fm"][k]:
            out.append((k, ep["fm"][k]))
    for c in ep.get("chapters", []):
        out.append(("chapter", chapter_text(c["title"])[1]))
    for t in ep["turns"]:
        if not t["gap"]:
            out.append((f"turn {t['i']}", t["text"]))
        out += [(f"turn {t['i']} meta", m) for m in t["meta"]]
    for m in ep.get("moments", []):
        out.append(("moment", m["summary"]))
        out += [("moment topic", x) for x in m.get("topics", [])]
    seen, uniq = set(), []
    for kind, text in out:
        if text and text not in seen:
            seen.add(text); uniq.append((kind, text))
    return uniq


def people_strings(people: list[dict]) -> list[tuple[str, str]]:
    return [("stance", p["stance"]) for p in people if p.get("stance")]


def missing(lang: str, items: list[tuple[str, str]]) -> list[str]:
    c = cache(lang)
    return [t for _, t in items if key(t) not in c]


def name(lang: str, n: str) -> str:
    return S(lang).get("names", {}).get(n, n)


MARK, END = "\u2063", "\u2064"   # invisible brackets around translated content while a page renders (stripped after)


def trm(lang: str, text: str) -> str:
    """tr() for page data: the translation wrapped in MARK..END so the HTML pass knows it is content, not UI."""
    t = tr(lang, text)
    return f"{MARK}{t}{END}" if t and t != text else t


def unmark(s: str) -> str:
    return s.replace(MARK, "").replace(END, "")


def tr(lang: str, text: str) -> str:
    return cache(lang).get(key(text), text) if text else text


def localize_data(lang: str, eps: list[dict], people: list[dict]) -> tuple[list[dict], list[dict]]:
    """Deep copy of the site data with content swapped for cached translations (identity links kept)."""
    eps2, people2 = copy.deepcopy((eps, people))
    for ep in eps2:
        ep["title"] = trm(lang, ep["title"])
        ep["context"] = trm(lang, ep.get("context") or "")
        ep["topics"] = [trm(lang, t) for t in ep.get("topics", [])]
        ep["blurb"] = trm(lang, ep.get("blurb") or "")
        if ep.get("card_summary"):
            ep["card_summary"] = trm(lang, ep["card_summary"])
        ep["summary"] = ep["context"] or trm(lang, ep.get("summary") or "")
        if ep.get("ai_disclosure"):
            ep["ai_disclosure"] = trm(lang, ep["ai_disclosure"])
        for k in ("note", "redactions"):
            if isinstance(ep["fm"].get(k), str):
                ep["fm"][k] = trm(lang, ep["fm"][k])
        for c in ep.get("chapters", []):
            pre, txt = chapter_text(c["title"])
            c["title"] = pre + trm(lang, txt)
        for t in ep["turns"]:
            if not t["gap"]:
                t["text"] = tr(lang, t["text"])
            t["meta"] = [trm(lang, m) for m in t["meta"]]
        for m in ep.get("moments", []):
            m["summary"] = trm(lang, m["summary"])
            m["topics"] = [trm(lang, x) for x in m.get("topics", [])]
        ep["people"] = [name(lang, n) for n in ep.get("people", [])]
        ep["body_md"] = transcript_txt(lang, ep)
    for p in people2:
        p["name"] = name(lang, p["name"])
        p["stance"] = trm(lang, p.get("stance") or "")
    return eps2, people2


# ---------------------------------------------------------------- Meet Eve (/eve/), from index/eve.json

EVE_TEXT = {"body", "intro", "note", "trait", "caveat", "value", "topic", "before", "after", "persona_notes_label",
            "persona_notes", "persona_notes_source", "grok_on_instructions_intro", "label"}
EVE_CLEAN = {"body", "intro", "grok_on_instructions_intro"}      # build_site.eve_page() runs these through clean()
_PENDING = re.compile(r"\(pending[^)]*\)|\bepisode 2\b", re.I)


def eve_data(B) -> dict | None:
    """index/eve.json if build_site would publish /eve/ (approved), else None."""
    if not B.EVE_FILE.exists():
        return None
    data = json.loads(B.EVE_FILE.read_text())
    return data if data.get("approved") else None


def _eve_clean(data: dict, published: set[str], text: str) -> str:
    """Same rule as build_site.eve_page(): drop sentences about episodes that aren't published yet."""
    if all(x in published for x in data.get("episodes", {})):
        return text
    return " ".join(x for x in re.split(r"(?<=[.!?])\s+", text) if not _PENDING.search(x))


def _eve_walk(data: dict, published: set[str], on_text, on_quote) -> None:
    """Visit every published string of /eve/: on_text(dict, field, english) and on_quote(source dict).
    Unpublished material (pending citations, items about unpublished episodes) is never visited."""
    def walk(x):
        if isinstance(x, list):
            for v in x:
                walk(v)
        elif isinstance(x, dict):
            if "quote_excerpt" in x:
                if not x.get("pending") and x.get("episode_slug") in published and not str(x["quote_excerpt"]).startswith("[transcript gap"):
                    on_quote(x)
                return
            if isinstance(x.get("topic"), str) and "pending" in x["topic"].lower():
                return
            for k, v in x.items():
                if k in EVE_TEXT and isinstance(v, str) and v.strip():
                    t = _eve_clean(data, published, v) if k in EVE_CLEAN else v
                    if t:
                        on_text(x, k, t)
                elif isinstance(v, (list, dict)):
                    walk(v)
    walk(data.get("sections", []))


def eve_strings(data: dict | None, published: set[str]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    if data:
        _eve_walk(data, published, lambda d, k, t: out.append((f"eve {k}", t)),
                  lambda src: out.append(("eve quote", src["quote_excerpt"])))
    seen = set()
    return [x for x in out if not (x[1] in seen or seen.add(x[1]))]


def eve_quotes(data: dict | None, published: set[str]) -> list[dict]:
    out: list[dict] = []
    if data:
        _eve_walk(data, published, lambda d, k, t: None, out.append)
    return out


def localize_eve(lang: str, data: dict, published: set[str]) -> dict:
    """Translated copy of eve.json; each quote keeps its English excerpt in `_en` for verification."""
    d2 = copy.deepcopy(data)

    def on_text(d, k, t):
        d[k] = trm(lang, t)

    def on_quote(src):
        src["_en"] = src["quote_excerpt"]
        src["quote_excerpt"] = trm(lang, src["quote_excerpt"])
    _eve_walk(d2, published, on_text, on_quote)
    return d2


class _Mem:
    """Stands in for a Path: build_site.eve_page() only calls exists() and read_text()."""
    def __init__(self, text: str):
        self.text = text

    def exists(self) -> bool:
        return True

    def read_text(self) -> str:
        return self.text


def _sources_class(B, eps_en: list[dict]):
    """EveSources for a translated build: every citation is still verified verbatim against the ENGLISH
    transcript (same rule, same error), then pointed at the translated episode and turn."""
    base = B.EveSources

    class Sources(base):
        def __init__(self, eps):
            super().__init__(eps)
            self.en = base(eps_en)

        def resolve(self, src):
            r = self.en.resolve({**src, "quote_excerpt": src.get("_en", src["quote_excerpt"])})
            if r is None:
                return None
            ep = self.eps[r["ep"]["slug"]]
            return {**r, "ep": ep, "turn": ep["turns"][r["turn"]["i"]]}
    return Sources


# ---------------------------------------------------------------- Eve's takes + topic pages (index/takes.json)

A_OPEN, A_CLOSE = "⟦", "⟧"           # marks a ref's anchor phrase inside a take paragraph for the translator


def takes_data(B) -> dict | None:
    """index/takes.json if build_site would publish takes and /topics/ (SHOW_TOPICS), else None."""
    return B.load_takes() if getattr(B, "SHOW_TOPICS", False) and B.TAKES_FILE.exists() else None


def _marked(x: dict) -> list[str]:
    """Take paragraphs with each ref anchor wrapped in ⟦…⟧ (first occurrence, as build_site.take_html links it)."""
    out = []
    for text in x["take"]:
        for r in x.get("refs", []):
            a = r.get("anchor")
            if a and a in text and A_OPEN + a not in text:
                text = text.replace(a, A_OPEN + a + A_CLOSE, 1)
        out.append(text)
    return out


def _take_items(x: dict, published: set[str], owner: str) -> list[tuple[str, str]]:
    out = [(f"take {owner} para", t) for t in _marked(x)]
    out += [(f"take {owner} ref", r["label"]) for r in x.get("refs", []) if r.get("label") and r["episode"] in published]
    return out


def take_strings(data: dict | None, published: set[str], people_slugs: set[str]) -> dict[str, list[tuple[str, str]]]:
    """Owner ("person:<slug>" / "topic:<slug>") -> its strings. Lede/role/title/description/terms included."""
    out: dict[str, list[tuple[str, str]]] = {}
    if not data:
        return out
    for slug, x in data["people"].items():
        if slug in people_slugs:
            out["person:" + slug] = ([("take role", x["role"])] if x.get("role") else []) + \
                ([("take lede", x["lede"])] if x.get("lede") else []) + _take_items(x, published, slug)
    for t in data["topics"]:
        out["topic:" + t["slug"]] = [("topic title", t["title"]), ("topic description", t["description"])] + \
            _take_items(t, published, t["slug"]) + [("topic term", x) for x in t.get("link_terms", [])]
    return out


def localize_takes(lang: str, data: dict, published: set[str]) -> dict:
    """Translated copy of takes.json. Each ref's anchor becomes the ⟦…⟧ span the translator marked in the
    translated paragraph; an anchor that can't be placed exactly once stays unlinked (plain text)."""
    d2 = copy.deepcopy(data)

    def one(x):
        paras, anchors = [], {}
        for en in _marked(x):
            ru = tr(lang, en)
            src = re.findall(re.escape(A_OPEN) + r"(.+?)" + re.escape(A_CLOSE), en)
            dst = re.findall(re.escape(A_OPEN) + r"(.+?)" + re.escape(A_CLOSE), ru)
            if len(src) == len(dst):
                anchors.update(zip(src, dst))
            plain = ru.replace(A_OPEN, "").replace(A_CLOSE, "")
            paras.append(plain if ru != en else en.replace(A_OPEN, "").replace(A_CLOSE, ""))
        full = "\n".join(paras)
        for r in x.get("refs", []):
            a = anchors.get(r.get("anchor") or "")
            r["anchor"] = a if a and full.count(a) == 1 else None
            if r.get("label"):
                r["label"] = trm(lang, r["label"])
        x["take"] = [f"{MARK}{p}{END}" for p in paras]
        for k in ("role", "lede", "title", "description"):
            if x.get(k):
                x[k] = trm(lang, x[k])
        if x.get("link_terms"):
            x["link_terms"] = [tr(lang, t) for t in x["link_terms"]]
    for x in d2["people"].values():
        one(x)
    for t in d2["topics"]:
        one(t)
    return d2


# ---------------------------------------------------------------- auto-links (index/links.json) in translations

_CASES = ("а", "у", "ом", "е")       # genitive, dative, instrumental, prepositional of consonant-final names


def _forms(word: str) -> list[str]:
    if re.search(r"[бвгджзклмнпрстфхцчшщ]$", word):
        return [word] + [word + c for c in _CASES]
    return [word]


def links(lang: str, ents: dict) -> dict:
    """links.json entities for a translated page: localized person names (plus common case forms, so
    «Илона Маска» links like «Илон Маск»), localized menu labels. Link targets are unchanged."""
    s = S(lang)
    lab = s.get("links", {})
    out = copy.deepcopy(ents)
    for ent in out.values():
        en_name = ent["name"]
        loc = name(lang, en_name)
        if loc != en_name:
            ent["name"] = loc
            words = loc.split()
            terms = [loc] + [" ".join(f) for f in zip(*[_forms(w) for w in words])]   # «Илона Маска»
            terms += _forms(words[-1])                                             # «Маска»
            ent["terms"] = list(dict.fromkeys(ent["terms"] + terms))
        for it in ent["items"]:
            m = re.match(r"^Eve on (.+)$", it["label"])
            if (t := re.match(r"^Eve's take: (.+)$", it["label"])) and "eve_take" in lab:
                it["label"] = lab["eve_take"].format(title=t.group(1))
            elif m and "eve_on" in lab:
                it["label"] = lab["eve_on"].format(name=name(lang, m.group(1)))
            elif (m := re.match(r"^(@\w+) on X$", it["label"])) and "on_x" in lab:
                it["label"] = lab["on_x"].format(handle=m.group(1))
            else:
                it["label"] = s.get("strings", {}).get(it["label"], it["label"])
    return out


TURN_LINE = re.compile(r"^(\[\d{2}:\d{2}:\d{2}\]) (David|Eve): ?")


def transcript_txt(lang: str, ep: dict) -> str:
    """Translated plain-text transcript, line-for-line on the English one: same [hh:mm:ss] turn headers in order.
    `ep` is the localized copy: its body_md is still English, its turns already carry the translations."""
    s = S(lang)
    head = s.get("transcript_header", "").format(url=f"/episodes/{ep['slug']}/transcript.txt")
    out, i, in_turn = ([head, ""] if head else []), -1, False
    for line in ep["body_md"].split("\n"):
        m = TURN_LINE.match(line)
        if m:
            i += 1
            t = ep["turns"][i]
            out.append(f"{m.group(1)} {s['strings'].get(m.group(2), m.group(2))}: {unmark(t['text'])}")
            metas, in_turn = iter(t["meta"]), True
        elif in_turn and line.startswith(">"):
            out.append(f"> {unmark(next(metas))}")
        elif in_turn:
            if not line.strip() and (not out[-1].strip() if out else True):
                continue
            if not line.strip():
                out.append("")       # blank separators between turns; turn text was emitted whole above
        else:
            out.append(line)         # anything before the first turn (none today)
    return "\n".join(out).strip()


# ---------------------------------------------------------------- planning + hooks used by build_site.page()

def enabled(lang: str) -> bool:
    return (I18N_DIR / f"{lang}.json").exists()


def plan(B, eps: list[dict], people: list[dict], extra: list[str]) -> None:
    """Decide which English paths get a translated page (called once per build, before any page renders)."""
    data, published = eve_data(B), {ep["slug"] for ep in eps}
    for lang in LANGS:
        if not enabled(lang):
            continue
        tk = take_strings(takes_data(B), published, {p["slug"] for p in people})
        tgaps = {o for o, items in tk.items() if missing(lang, items)}
        if tgaps:
            print(f"warning: {lang}: {len(tgaps)} takes/topics have untranslated strings; those pages stay English "
                  f"(run scripts/translate.py): {', '.join(sorted(tgaps)[:6])}", file=sys.stderr)
        paths = set(extra) | {f"/people/{p['slug']}/" for p in people if "person:" + p["slug"] not in tgaps}
        topics = [o.split(":", 1)[1] for o in tk if o.startswith("topic:")]
        paths |= {f"/topics/{t}/" for t in topics if "topic:" + t not in tgaps}
        if topics and not tgaps:
            paths.add("/topics/")
        for ep in eps:
            gaps = missing(lang, episode_strings(ep))
            if gaps:
                print(f"warning: {lang}: {ep['slug']} has {len(gaps)} untranslated strings; no /{lang}/ page "
                      f"(run scripts/translate.py)", file=sys.stderr)
            else:
                paths.add(f"/episodes/{ep['slug']}/")
        if data and published & {ep_path.split("/")[2] for ep_path in paths if ep_path.startswith("/episodes/")}:
            gaps = missing(lang, eve_strings(data, published))
            if gaps:
                print(f"warning: {lang}: /eve/ has {len(gaps)} untranslated strings; no /{lang}/eve/ page", file=sys.stderr)
            elif all(f"/episodes/{x}/" in paths for x in published) and not tgaps:   # quotes link into every episode
                paths.add("/eve/")
        if not any(x.startswith("/episodes/") for x in paths):
            paths = set()                # no translated episode yet: no edition, no switcher
        _paths[lang] = paths
        _misses.pop(lang, None)


def current() -> str:
    return _cur


def localize(path: str) -> str:
    return path if _cur == SOURCE else f"/{_cur}{path}"


def _alts(path: str) -> list[tuple[str, str]]:
    return [(SOURCE, path)] + [(l, f"/{l}{path}") for l in LANGS if path in _paths.get(l, ())]


def html_attrs() -> str:
    a = f' lang="{_cur}"'
    if _cur != SOURCE:
        a += f' data-i18n="{html.escape(json.dumps(S(_cur).get("js", {}), ensure_ascii=False))}"'
    return a


def head_links(A, site: str, path: str) -> str:
    alts = _alts(path)
    out = []
    if len(alts) > 1:
        out += [f'<link rel="alternate" hreflang="{l}" href="{site}{p}">' for l, p in alts]
        out.append(f'<link rel="alternate" hreflang="x-default" href="{site}{path}">')
    if any(_paths.values()):
        out.append(f'<link rel="stylesheet" href="{A("assets/i18n.css")}">')
    return "\n".join(out)


def switcher(path: str) -> str:
    """EN / RU switch for the header. Compact: on phones only the other language shows."""
    if not any(_paths.values()):
        return ""
    links = []
    for l in [SOURCE] + [x for x in LANGS if _paths.get(x)]:
        href = path if l == SOURCE else (f"/{l}{path}" if path in _paths[l] else f"/{l}/")
        lab = S(l).get("language_name", "English") if l != SOURCE else "English"
        if l == _cur:
            links.append(f'<span class="lang-switch__a" lang="{l}" title="{lab}" aria-current="true">{l.upper()}</span>')
        else:
            links.append(f'<a class="lang-switch__a" href="{href}" hreflang="{l}" lang="{l}" title="{lab}">{l.upper()}</a>')
    return f'<nav class="lang-switch" aria-label="Language">{"".join(links)}</nav>'


# ---------------------------------------------------------------- UI translation of a rendered page

LATIN = re.compile(r"[A-Za-z]")
CYRILLIC = re.compile(r"[\u0400-\u04ff]")
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
SKIP_TAGS = {"code", "pre", "style"}
SKIP_CLASSES = {"turn__body", "xl"}               # translated content and auto-linked names, never UI
ATTRS = {"aria-label", "title", "alt", "placeholder", "aria-valuetext"}
META_CONTENT = {"description", "og:title", "og:description", "twitter:title", "twitter:description"}


def _plural(lang: str, n: int, forms: list[str]) -> str:
    if lang == "ru" and len(forms) == 3:
        n = abs(n) % 100
        if 10 < n < 20:
            return forms[2]
        n %= 10
        return forms[0] if n == 1 else forms[1] if 2 <= n <= 4 else forms[2]
    return forms[0] if n == 1 else forms[-1]


def _fill(lang: str, tpl: str, m: re.Match) -> str:
    def sub(x: re.Match) -> str:
        g = m.group(int(x.group(1)))
        if x.group(2):
            return _plural(lang, int(g), x.group(2)[1:].split("|"))
        if x.group(3):                                 # {1:t} - translate the captured group too
            return ui(lang, g)
        return g
    return re.sub(r"\{(\d+)(\|[^}]*)?(:t)?\}", sub, tpl)


def ui(lang: str, text: str, record: bool = True) -> str:
    """Translate one UI string (whitespace-normalized, surrounding whitespace kept); returns the input when
    nothing matches. Text that already contains Cyrillic (translated content) is never recorded as missing."""
    core = " ".join(text.split())
    if not LATIN.search(core) or re.fullmatch(r"\S+@\S+\.\w+", core):   # nothing to translate / an address
        return text
    lead, trail = text[:len(text) - len(text.lstrip())], text[len(text.rstrip()):]
    s = S(lang)
    table = s.get("strings", {})
    if core in table:
        return lead + table[core] + trail
    for pat, tpl in s.get("patterns", []):
        m = re.fullmatch(pat, core)
        if m:
            return lead + _fill(lang, tpl, m) + trail
    if "·" in core:      # "Bonus · <time> · 41 min": translate each piece
        return lead + "".join(x if not x.strip(" ·") else ui(lang, x, record) for x in re.split(r"( ?· ?)", core)) + trail
    if record and not CYRILLIC.search(core):
        _misses.setdefault(lang, set()).add(core)
    return text


BLOCKS = {"p", "li", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "figcaption", "div", "section", "td", "th",
          "dd", "dt", "title", "summary", "article", "header", "footer", "nav", "ul", "ol", "main", "aside"}


class _Rewriter(HTMLParser):
    def __init__(self, lang: str, path: str, note: str):
        super().__init__(convert_charrefs=True)
        self.lang, self.path, self.note = lang, path, note
        self.out: list[str] = []
        self.stack: list[tuple[str, bool, str]] = []    # (tag, skip, type attr for script)
        self.depth = 0                                  # inside MARK..END (translated content), maybe across tags

    def skipping(self) -> bool:
        return any(s for _, s, _ in self.stack)

    def _attrs(self, tag: str, attrs: list[tuple[str, str | None]]) -> tuple[list, bool]:
        changed, new = False, []
        d = dict(attrs)
        for k, v in attrs:
            nv = v = unmark(v) if v is not None else v
            if v is not None:
                if self.skipping() and k != "href":
                    pass
                elif k in ATTRS or (k == "content" and tag == "meta" and (d.get("name") in META_CONTENT or d.get("property") in META_CONTENT)):
                    nv = ui(self.lang, v)
                elif k == "href" and tag == "a" and "hreflang" not in d and v.startswith("/") and not v.startswith("//"):
                    p, _, frag = v.partition("#")
                    if p in _paths.get(self.lang, ()):
                        nv = f"/{self.lang}{p}" + (f"#{frag}" if frag else "")
            changed |= nv != dict(attrs)[k]
            new.append((k, nv))
        return new, changed

    @staticmethod
    def _tag(tag: str, attrs: list, close: bool = False) -> str:
        a = "".join(f" {k}" if v is None else f' {k}="{html.escape(v)}"' for k, v in attrs)
        return f"<{tag}{a}{' /' if close else ''}>"

    def handle_starttag(self, tag, attrs):
        new, changed = self._attrs(tag, attrs)
        self.out.append(self._tag(tag, new) if changed else self.get_starttag_text())
        if tag in VOID:
            return
        cls = set((dict(attrs).get("class") or "").split())
        self.stack.append((tag, tag in SKIP_TAGS or bool(cls & SKIP_CLASSES), dict(attrs).get("type") or ""))
        if tag == "main" and self.note:
            self.out.append(self.note)

    def handle_startendtag(self, tag, attrs):
        new, changed = self._attrs(tag, attrs)
        self.out.append(self._tag(tag, new, True) if changed else self.get_starttag_text())

    def handle_endtag(self, tag):
        for k in range(len(self.stack) - 1, -1, -1):
            if self.stack[k][0] == tag:
                del self.stack[k:]
                break
        if tag in BLOCKS:
            self.depth = 0                              # a cut-short translation (first_sentence…) can't leak further
        self.out.append(f"</{tag}>")

    def handle_data(self, data):
        top = self.stack[-1] if self.stack else ("", False, "")
        if top[0] == "script":
            if top[2] == "application/ld+json":
                self.out.append(self._ld(data))
            elif top[2] == "application/json":        # auto-link menus (build_site.Linker.data_script)
                self.out.append(self._links_json(data))
            else:
                self.out.append(data)
        elif top[0] == "style" or self.skipping():
            self.out.append(html.escape(data, quote=False) if top[0] != "style" else data)
        elif MARK not in data and END not in data and not self.depth:
            self.out.append(html.escape(ui(self.lang, data), quote=False))
        else:                                           # UI text around translated content: only the UI parts
            buf, parts = "", []
            for ch in data + MARK:
                if ch in (MARK, END):
                    if buf:
                        parts.append(buf if self.depth else ui(self.lang, buf))
                    buf = ""
                    self.depth = max(0, self.depth + (1 if ch == MARK else -1))
                else:
                    buf += ch
            self.depth = max(0, self.depth - 1)        # undo the sentinel
            self.out.append(html.escape("".join(parts), quote=False))

    def _links_json(self, raw: str) -> str:
        try:
            j = json.loads(raw.replace("<\\/", "</"))
        except ValueError:
            return raw
        for menu in j.values() if isinstance(j, dict) else []:
            for it in menu.get("items", []) if isinstance(menu, dict) else []:
                u = it.get("url", "")
                if u.startswith("/") and u.partition("#")[0] in _paths.get(self.lang, ()):
                    it["url"] = f"/{self.lang}{u}"
        return json.dumps(j, ensure_ascii=False).replace("</", "<\\/")

    def _ld(self, raw: str) -> str:
        try:
            j = json.loads(raw.replace("<\\/", "</"))
        except ValueError:
            return raw

        def walk(o):
            if isinstance(o, dict):
                for k, v in o.items():
                    if isinstance(v, str) and k in ("description", "name"):
                        o[k] = ui(self.lang, unmark(v), record=False)
                    elif isinstance(v, str):
                        o[k] = unmark(v)
                    elif isinstance(v, str) and k == "url" and v.startswith("http"):
                        pass
                    else:
                        walk(v)
            elif isinstance(o, list):
                for i, v in enumerate(o):
                    if isinstance(v, str):
                        o[i] = unmark(v)
                    else:
                        walk(v)
        walk(j)
        if isinstance(j, dict):
            j["inLanguage"] = self.lang
        return json.dumps(j, ensure_ascii=False).replace("</", "<\\/")

    def handle_comment(self, data):
        self.out.append(f"<!--{data}-->")

    def handle_decl(self, decl):
        self.out.append(f"<!{decl}>")

    def handle_pi(self, data):
        self.out.append(f"<?{data}>")


def note_html(lang: str, path: str) -> str:
    s = S(lang)
    return (f'<div class="wrap i18n-note" role="note" lang="{lang}">{s["note"]["text"]} '
            f'<a href="{path}" hreflang="{SOURCE}" lang="{SOURCE}">{s["note"]["link"]}</a></div>')


def translate_html(lang: str, page_html: str, path: str, note: bool = True) -> str:
    rw = _Rewriter(lang, path, note_html(lang, path) if note else "")
    rw.feed(page_html)
    rw.close()
    return unmark("".join(rw.out))


def misses(lang: str) -> list[str]:
    return sorted(_misses.get(lang, ()))


# ---------------------------------------------------------------- build entry point

def build(B, A, out: Path, eps: list[dict], people: list[dict], cfg: dict) -> list[str]:
    """Write every translated page under out/<lang>/; returns the sitemap paths written."""
    global _cur
    urls: list[str] = []
    for lang in LANGS:
        if not _paths.get(lang):
            continue
        eps2, people2 = localize_data(lang, eps, people)
        by = {x["slug"]: x for x in eps2}
        pages = [("/", lambda: B.home(A, eps2, people2)), ("/people/", lambda: B.people_index(A, people2)),
                 ("/about/", lambda: B.about_page(A, cfg))]
        pages += [(f"/people/{p['slug']}/", (lambda p=p: B.person_page(A, p))) for p in people2]
        pages += [(f"/episodes/{s}/", (lambda s=s: B.episode_page(A, by[s], eps2))) for s in by]
        data, tdata = eve_data(B), takes_data(B)
        saved = B.EVE_FILE, B.EveSources, B.load_links, B.TAKES_FILE
        topics2: list[dict] = []
        pages += [("/topics/", lambda: B.topics_index(A, topics2))]
        pages += [("/eve/", lambda: B.eve_page(A, eps2, people2, topics2))]
        _cur = lang
        try:
            if tdata:
                B.TAKES_FILE = _Mem(json.dumps(localize_takes(lang, tdata, set(by))))
                topics2 += B.attach_takes(eps2, people2)        # translated takes, ledes and topics
                pages += [(f"/topics/{t['slug']}/", (lambda t=t: B.topic_page(A, t))) for t in topics2]
            ents = links(lang, B.load_links())
            if data:
                B.EVE_FILE = _Mem(json.dumps(localize_eve(lang, data, set(by))))
            B.EveSources, B.load_links = _sources_class(B, eps), (lambda: ents)
            for path, fn in pages:
                if path not in _paths[lang]:
                    continue
                B.write(out, f"{lang}{path}index.html", translate_html(lang, fn(), path))
                urls.append(f"/{lang}{path}")
                if path.startswith("/episodes/"):
                    B.write(out, f"{lang}{path}transcript.txt", by[path.split('/')[2]]["body_md"] + "\n")
            B.write(out, f"{lang}/404.html", translate_html(lang, B.not_found(A), "/", note=False))
        finally:
            _cur = SOURCE
            B.EVE_FILE, B.EveSources, B.load_links, B.TAKES_FILE = saved
        if misses(lang):
            print(f"warning: {lang}: {len(misses(lang))} UI strings untranslated (run scripts/translate.py --ui): "
                  + "; ".join(misses(lang)[:12]), file=sys.stderr)
    return urls
