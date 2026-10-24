"""Translated editions (/ru/): structure mirrors the English pages 1:1 and everything comes from the committed cache."""
import json
import re
from html.parser import HTMLParser

import pytest

from pipeline_helpers import ROOT
import build_site as B
import site_i18n as I
import sqlite_apply

EP0, EP1 = "first-encounter", "the-making-of-eve"
LANG = "ru"
CYR = re.compile(r"[\u0400-\u04ff]")


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("site_i18n")
    db = tmp / "d.sqlite"
    for sql in sorted(ROOT.glob("artifacts/*/d1_upsert.sql")):
        sqlite_apply.apply(db, ROOT / "migrations", sql)
    out = tmp / "dist"
    assert B.main(["--db", str(db), "--out", str(out), "--include-drafts"]) == 0
    eps, people = B.load_site(db, include_drafts=True)
    return out, {ep["slug"]: ep for ep in eps}, people


def cached_slugs():
    return sorted(p.stem for p in (ROOT / "translations" / LANG).glob("*.json") if not p.stem.startswith("_"))


class Turns(HTMLParser):
    """Collect (id, data-start, data-end, seek href, speaker class) for every transcript turn."""
    def __init__(self):
        super().__init__()
        self.turns, self.seeks = [], []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "li" and "turn" in (a.get("class") or "").split():
            self.turns.append((a.get("id"), a.get("data-start"), a.get("data-end"), a.get("class")))
        if tag == "a" and "data-seek" in a:
            self.seeks.append(a.get("href"))


def parse(html):
    p = Turns()
    p.feed(html)
    return p


def test_cache_files_are_well_formed():
    assert cached_slugs(), "no translations committed"
    for path in (ROOT / "translations" / LANG).glob("*.json"):
        doc = json.loads(path.read_text())
        assert doc["lang"] == LANG and doc["strings"], path
        for k, v in doc["strings"].items():
            assert k == I.key(v["en"]) or "[redacted" in v["en"], (path.name, k)   # `en` is scrubbed of private bits
            assert v["tr"].strip(), (path.name, k)


@pytest.mark.parametrize("slug", [EP0, EP1])
def test_episode_translation_complete(built, slug):
    _, eps, _ = built
    if slug not in cached_slugs():
        pytest.skip(f"{slug} not translated")
    assert I.missing(LANG, I.episode_strings(eps[slug])) == []


@pytest.mark.parametrize("slug", [EP0, EP1])
def test_transcript_structure_matches_english(built, slug):
    out, eps, _ = built
    if slug not in cached_slugs():
        pytest.skip(f"{slug} not translated")
    en = parse((out / f"episodes/{slug}/index.html").read_text())
    ru = parse((out / f"{LANG}/episodes/{slug}/index.html").read_text())
    assert len(en.turns) == len(eps[slug]["turns"]) > 0
    assert ru.turns == en.turns            # same segment count, ids, start/end timestamps, speakers, in order
    assert ru.seeks == en.seeks            # same seek links (#t=N)
    # plain-text transcript: same turn headers ([hh:mm:ss] speaker) in the same order
    hdr = re.compile(r"^\[(\d{2}:\d{2}:\d{2})\] ", re.M)
    assert hdr.findall((out / f"{LANG}/episodes/{slug}/transcript.txt").read_text()) == \
        hdr.findall((out / f"episodes/{slug}/transcript.txt").read_text())


def test_ru_pages_lang_hreflang_note(built):
    out, eps, people = built
    paths = ["/", "/people/", "/about/"] + [x for x in ("/eve/", "/topics/") if (out / LANG / x.strip("/") / "index.html").exists()] + [f"/episodes/{s}/" for s in cached_slugs() if s in eps]
    for path in paths:
        h = (out / f"{LANG}{path}index.html").read_text()
        en = (out / f"{path.lstrip('/')}index.html").read_text()
        assert h.startswith("<!doctype html>") or "<html" in h
        assert f'<html lang="{LANG}"' in h and 'data-i18n="' in h, path
        assert f'<link rel="canonical" href="{B.SITE}/{LANG}{path}">' in h, path
        for doc in (h, en):
            assert f'hreflang="en" href="{B.SITE}{path}"' in doc and f'hreflang="{LANG}" href="{B.SITE}/{LANG}{path}"' in doc
            assert 'hreflang="x-default"' in doc and 'class="lang-switch"' in doc
        assert '<html lang="en"' in en
        assert 'class="wrap i18n-note"' in h and f'href="{path}" hreflang="en"' in h, path
        assert CYR.search(h)
        assert "\u2063" not in h and "\u2064" not in h, path     # render-time content markers are stripped


def test_ru_internal_links_stay_in_edition(built):
    out, eps, _ = built
    h = (out / f"{LANG}/index.html").read_text()
    for s in cached_slugs():
        assert f'href="/{LANG}/episodes/{s}/"' in h
    assert f'href="/{LANG}/people/"' in h and f'href="/{LANG}/about/"' in h
    assert 'href="/feed.xml"' in h                    # feed stays the English feed


def test_no_untranslated_ui_strings(built):
    assert I.misses(LANG) == [], I.misses(LANG)[:20]


def test_audio_and_feed_unchanged(built):
    out, eps, _ = built
    feed = (out / "feed.xml").read_text()
    assert not CYR.search(feed) and "/ru/" not in feed
    for s in cached_slugs():
        en = re.findall(r'https://audio\.askevepodcast\.com/[^"\s]+', (out / f"episodes/{s}/index.html").read_text())
        ru = re.findall(r'https://audio\.askevepodcast\.com/[^"\s]+', (out / f"{LANG}/episodes/{s}/index.html").read_text())
        assert en and sorted(set(ru)) == sorted(set(en))


def test_sitemap_lists_ru_pages(built):
    out, _, _ = built
    sm = (out / "sitemap.xml").read_text()
    assert f"{B.SITE}/{LANG}/</loc>" in sm
    for s in cached_slugs():
        assert f"{B.SITE}/{LANG}/episodes/{s}/</loc>" in sm


def test_ui_plural_and_patterns():
    assert I.ui(LANG, "1 discussion", record=False) == "1 обсуждение"
    assert I.ui(LANG, "3 discussions", record=False) == "3 обсуждения"
    assert I.ui(LANG, "11 discussions", record=False) == "11 обсуждений"
    assert I.ui(LANG, "Oct 23, 2026", record=False) == "23 октября 2026"
    assert I.ui(LANG, "1 hr 5 min", record=False) == "1 ч 5 мин"


def test_no_private_strings_in_translations():
    blob = "".join(p.read_text() for p in (ROOT / "translations").rglob("*.json")) + (ROOT / "i18n" / f"{LANG}.json").read_text()
    assert not re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", blob, re.I)   # agent/bot IDs
    assert not re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", blob)                       # emails
    assert "grok.com/share" not in blob and not re.search(r"https?://\S*canvas", blob, re.I)
    assert "xai-" not in blob


def test_takes_keep_their_episode_links(built):
    """Every ref anchor in Eve's takes is still a link to the moment on the translated page."""
    out, _, _ = built
    pages = sorted((out / LANG / "topics").glob("*/index.html")) + sorted((out / LANG / "people").glob("*/index.html"))
    checked = 0
    for ru in pages:
        en = out / ru.relative_to(out / LANG)
        a, b = en.read_text(), ru.read_text()
        if 'class="take"' not in a:
            continue
        cites = lambda h: re.findall(r'<a class="cite" href="([^"]+)"', h)  # noqa: E731
        assert [x.removeprefix(f"/{LANG}") for x in cites(b)] == cites(a), ru
        checked += 1
    assert checked or not (out / LANG / "topics").exists()
