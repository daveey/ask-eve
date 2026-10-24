"""Translated editions (/ru/): structure mirrors the English pages 1:1 and everything comes from the committed cache."""
import copy
import json
import re
from html.parser import HTMLParser
from pathlib import Path

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
    return sorted(
        p.stem for p in (ROOT / "translations" / LANG).glob("*.json")
        if not p.stem.startswith("_") and p.stem != EP1
    )


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
    assert I.missing(LANG, I.episode_strings(eps[slug])) == []


@pytest.mark.parametrize("slug", [EP0])
def test_transcript_structure_matches_english(built, slug):
    out, eps, _ = built
    if slug not in cached_slugs():
        pytest.skip(f"{slug} not translated")
    en = parse((out / f"episodes/{slug}/index.html").read_text())
    ru = parse((out / f"{LANG}/episodes/{slug}/index.html").read_text())
    assert len(en.turns) == len(eps[slug]["turns"]) > 0
    assert [t[0] for t in ru.turns] == [t[0] for t in en.turns]
    assert [t[3] for t in ru.turns] == [t[3] for t in en.turns]
    assert len(ru.seeks) == len(en.seeks)
    if not I.ep_meta(LANG, slug).get("ru_turn_offsets"):
        assert ru.turns == en.turns
        assert ru.seeks == en.seeks
    # plain-text transcript: same turn headers ([hh:mm:ss] speaker) in the same order
    hdr = re.compile(r"^\[(\d{2}:\d{2}:\d{2})\] ", re.M)
    assert hdr.findall((out / f"{LANG}/episodes/{slug}/transcript.txt").read_text()) == \
        hdr.findall((out / f"episodes/{slug}/transcript.txt").read_text())


def test_ru_pages_lang_hreflang_note(built):
    out, eps, people = built
    paths = ["/", "/guide/", "/ledger/", "/reports/", "/people/", "/about/"] + [x for x in ("/eve/", "/topics/") if (out / LANG / x.strip("/") / "index.html").exists()] + [f"/episodes/{s}/" for s in cached_slugs() if s in eps]
    for path in paths:
        h = (out / f"{LANG}{path}index.html").read_text()
        en = (out / f"{path.lstrip('/')}index.html").read_text()
        assert h.startswith("<!doctype html>") or "<html" in h
        assert f'<html lang="{LANG}"' in h and 'data-i18n="' in h, path
        assert f'<link rel="canonical" href="{B.SITE}/{LANG}{path}">' in h, path
        for doc in (h, en):
            assert f'hreflang="en" href="{B.SITE}{path}"' in doc and f'hreflang="{LANG}" href="{B.SITE}/{LANG}{path}"' in doc
            assert 'hreflang="x-default"' in doc and 'class="lang-menu"' in doc
        assert '<html lang="en"' in en
        assert 'class="wrap i18n-note"' in h and f'href="{path}" hreflang="en"' in h, path
        assert "Русская озвучка (ИИ): голос Евы и клонированный голос Дэвида" in h, path
        assert CYR.search(h)
        assert "\u2063" not in h and "\u2064" not in h, path     # render-time content markers are stripped


def test_lang_menu_dropdown(built, monkeypatch):
    out, eps, _ = built
    for s in cached_slugs():
        path = f"/episodes/{s}/"
        en = (out / f"episodes/{s}/index.html").read_text()
        ru = (out / f"{LANG}/episodes/{s}/index.html").read_text()
        assert '<details class="lang-menu" data-menu>' in en
        assert '<details class="lang-menu" data-menu>' in ru
        assert '<span class="lang-menu__cur">EN</span>' in en
        assert '<span class="lang-menu__cur">RU</span>' in ru
        assert '<span class="lang-menu__item is-active" lang="en" aria-current="true"><span class="lang-menu__name">English</span><span class="lang-menu__code">EN</span>' in en
        assert f'<a class="lang-menu__item" href="/{LANG}{path}" hreflang="{LANG}" lang="{LANG}" data-lang-option="{LANG}"><span class="lang-menu__name">Русский</span><span class="lang-menu__code">RU</span></a>' in en
        assert f'<a class="lang-menu__item" href="{path}" hreflang="en" lang="en" data-lang-option="en"><span class="lang-menu__name">English</span><span class="lang-menu__code">EN</span></a>' in ru
        assert '<span class="lang-menu__item is-active" lang="ru" aria-current="true"><span class="lang-menu__name">Русский</span><span class="lang-menu__code">RU</span>' in ru
    # Verify menu scales cleanly as additional languages are added
    monkeypatch.setattr(I, "LANGS", ("ru", "es"))
    monkeypatch.setitem(I._paths, "es", {"/", "/about/"})
    monkeypatch.setitem(I._S, "es", {"language_name": "Español"})
    sw = I.switcher("/about/")
    assert 'href="/ru/about/" hreflang="ru" lang="ru"' in sw
    assert 'href="/es/about/" hreflang="es" lang="es"' in sw
    assert "Español" in sw


def test_ru_internal_links_stay_in_edition(built):
    out, eps, _ = built
    h = (out / f"{LANG}/index.html").read_text()
    assert f'href="/{LANG}/episodes/andreessen-horowitz-rightward-turn/"' in h
    assert f'href="/{LANG}/guide/"' in h and f'href="/{LANG}/ledger/"' in h
    assert f'href="/{LANG}/people/"' in h and f'href="/{LANG}/about/"' in h
    assert f'href="/{LANG}/feed.xml"' in h
    stub = (out / f"{LANG}/episodes/the-making-of-eve/index.html").read_text()
    assert f'<meta http-equiv="refresh" content="0; url=/{LANG}/guide/">' in stub


def test_no_untranslated_ui_strings(built):
    assert I.misses(LANG) == [], I.misses(LANG)[:20]


def test_ru_audio_and_feed(built):
    out, eps, _ = built
    feed = (out / "feed.xml").read_text()
    assert not CYR.search(feed) and "/ru/" not in feed
    ru_feed = (out / f"{LANG}/feed.xml").read_text()
    assert "<language>ru</language>" in ru_feed
    assert f'href="{B.SITE}/{LANG}/feed.xml"' in ru_feed
    assert CYR.search(ru_feed)
    for s in cached_slugs():
        stem = Path(eps[s]["transcript_path"]).stem
        expected_ru_url = I.ep_meta(LANG, s).get("ru_audio_url") or f"https://audio.askevepodcast.com/audio/ru/{stem}.v1.mp3"
        en_html = (out / f"episodes/{s}/index.html").read_text()
        ru_html = (out / f"{LANG}/episodes/{s}/index.html").read_text()
        en_urls = re.findall(r'https://audio\.askevepodcast\.com/[^"\s]+', en_html)
        assert en_urls and all("/audio/ru/" not in u for u in en_urls)
        assert f'src="{expected_ru_url}"' in ru_html
        assert f'data-fallback-src="{eps[s]["audio_url"]}"' in ru_html
        assert 'data-lang-audio="ru"' in ru_html
        if s == "the-making-of-eve":
            assert f'<enclosure url="{expected_ru_url}"' in ru_feed
    assert '<enclosure url="https://audio.askevepodcast.com/audio/ru/splits/2026-10-23_andreessen-horowitz-rightward-turn.v1.mp3"' in ru_feed


def test_ru_turn_offsets_and_audio_meta(built, monkeypatch):
    out, eps, people = built
    slug = EP0
    ep = copy.deepcopy(eps[slug])
    n = len(ep["turns"])
    offsets = [[round(i * 12.5, 2), round((i + 1) * 12.5, 2)] for i in range(n)]
    fake_meta = {
        "ru_audio_url": "https://audio.askevepodcast.com/audio/ru/custom-test.v1.mp3",
        "ru_duration_s": 1234.4,
        "ru_audio_bytes": 49376000,
        "ru_audio_sha256": "deadbeef" * 8,
        "ru_turn_offsets": offsets,
    }
    monkeypatch.setitem(I._M, LANG, {slug: fake_meta})
    eps2, _ = I.localize_data(LANG, [ep], people)
    loc_ep = eps2[0]
    assert loc_ep["audio_url"] == "https://audio.askevepodcast.com/audio/ru/custom-test.v1.mp3"
    assert loc_ep["duration_seconds"] == 1234
    assert loc_ep["audio_bytes"] == 49376000
    assert loc_ep["audio_sha256"] == "deadbeef" * 8
    first_spoken = next(t for t in loc_ep["turns"] if not t["gap"])
    assert first_spoken["start"] == round(first_spoken["i"] * 12.5, 2)
    assert first_spoken["end"] == round((first_spoken["i"] + 1) * 12.5, 2)
    html = I.translate_html(LANG, B.episode_page(B.Assets(out), loc_ep, eps2), f"/episodes/{slug}/")
    assert 'src="https://audio.askevepodcast.com/audio/ru/custom-test.v1.mp3"' in html
    assert f'id="turn-{first_spoken["i"]}" data-start="{first_spoken["start"]:.2f}" data-end="{first_spoken["end"]:.2f}"' in html
    assert f'href="#t={int(first_spoken["start"])}" data-seek="{first_spoken["start"]:.2f}"' in html
    feed_xml = I.render_feed(LANG, B, [loc_ep])
    assert '<enclosure url="https://audio.askevepodcast.com/audio/ru/custom-test.v1.mp3" length="49376000" type="audio/mpeg"/>' in feed_xml
    assert "<itunes:duration>1234</itunes:duration>" in feed_xml


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
    assert I.ui(LANG, "Language: English", record=False) == "Язык: English"


def test_no_private_strings_in_translations():
    blob = "".join(p.read_text() for p in (ROOT / "translations").rglob("*.json")) + (ROOT / "i18n" / f"{LANG}.json").read_text()
    assert not re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", blob, re.I)   # agent/bot IDs
    assert not re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", blob)                       # emails
    assert "grok.com/share" not in blob and not re.search(r"https?://\S*canvas", blob, re.I)
    assert "xai-" not in blob


def test_takes_keep_their_episode_links(built):
    """Every ref anchor in Eve's takes is still a link to the corresponding turn on the translated page."""
    out, eps, people = built
    eps_ru, _ = I.localize_data(LANG, list(eps.values()), people)
    by_ru = {ep["slug"]: ep for ep in eps_ru}
    en_to_ru_href = {}
    for slug, ep_en in eps.items():
        ep_ru = by_ru[slug]
        def _href(ep_slug, t):
            if ep_slug == EP1:
                return f"/guide/#turn-{t['i']}"
            if t["start"] is not None and not t["gap"]:
                sec = f"{t['start']:.2f}".rstrip("0").rstrip(".")
                return f"/episodes/{ep_slug}/#t={sec}"
            return f"/episodes/{ep_slug}/#turn-{t['i']}"
        for t_en, t_ru in zip(ep_en["turns"], ep_ru["turns"]):
            en_to_ru_href[_href(slug, t_en)] = f"/{LANG}{_href(slug, t_ru)}"
        for sp_en, sp_ru in zip(ep_en.get("splits", []), ep_ru.get("splits", [])):
            for t_en, t_ru in zip(sp_en["turns"], sp_ru["turns"]):
                en_to_ru_href[_href(sp_en["slug"], t_en)] = f"/{LANG}{_href(sp_ru['slug'], t_ru)}"
    pages = sorted((out / LANG / "topics").glob("*/index.html")) + sorted((out / LANG / "people").glob("*/index.html"))
    checked = 0
    for ru in pages:
        en = out / ru.relative_to(out / LANG)
        a, b = en.read_text(), ru.read_text()
        if 'class="take"' not in a:
            continue
        cites = lambda h: re.findall(r'<a class="cite" href="([^"]+)"', h)  # noqa: E731
        assert cites(b) == [en_to_ru_href.get(x, f"/{LANG}{x}") for x in cites(a)], ru
        checked += 1
    assert checked or not (out / LANG / "topics").exists()


def test_ru_eve_avatar_page(built):
    out, _, _ = built
    ru_eve = (out / LANG / "eve/index.html").read_text()
    assert f'href="/{LANG}/eve/avatar/"' in ru_eve
    assert "Как я выбрала своё лицо" in ru_eve
    ru_av = (out / LANG / "eve/avatar/index.html").read_text()
    assert "Как я выбирала себе лицо" in ru_av
    assert f'href="/{LANG}/eve/"' in ru_av
    assert ru_av.count('class="av-pblock"') == 20
    assert ru_av.count('<article class="av-rcell') == 200


def test_localized_rss_feeds_apple_podcasts_ready(built):
    """t168u: Every /<lang>/feed.xml has a distinct localized <title>, language-scoped GUIDs, and fragment-free <enclosure> URLs."""
    out, _, _ = built
    for lang, label in I.FEED_LANG_LABELS.items():
        if not (out / lang / "feed.xml").exists():
            continue
        feed = (out / lang / "feed.xml").read_text()
        assert f"<title>Ask Eve ({label})</title>" in feed
        assert f"<language>{lang}</language>" in feed
        assert f'href="{B.SITE}/{lang}/feed.xml"' in feed
        guids = re.findall(r'<guid isPermaLink="false">([^<]+)</guid>', feed)
        assert guids and all(g.endswith(f"-{lang}") for g in guids), (lang, guids)
        enclosures = re.findall(r'<enclosure url="([^"]+)"', feed)
        assert enclosures and all("#" not in u for u in enclosures), (lang, enclosures)
        idx = (out / lang / "index.html").read_text()
        assert f'data-copy="{B.SITE}/{lang}/feed.xml"' in idx
        assert f'href="{B.SITE}/{lang}/feed.xml"' in idx
