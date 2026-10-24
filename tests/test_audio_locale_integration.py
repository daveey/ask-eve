"""Integration tests for multilingual audio dubs, localized splits, and language-isolated RSS feeds."""
import json
import re
from pathlib import Path

import pytest

from pipeline_helpers import ROOT
import build_site as B
import site_i18n as I
import sqlite_apply


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("audio_locale")
    db = tmp / "d.sqlite"
    for sql in sorted(ROOT.glob("artifacts/*/d1_upsert.sql")):
        sqlite_apply.apply(db, ROOT / "migrations", sql)
    out = tmp / "dist"
    assert B.main(["--db", str(db), "--out", str(out), "--include-drafts"]) == 0
    eps, people = B.load_site(db, include_drafts=True)
    return out, {ep["slug"]: ep for ep in eps}, people


def test_making_of_eve_is_guide_not_audio_episode(built):
    out, eps, _ = built
    assert B.is_guide_ep(eps["the-making-of-eve"])
    assert eps["the-making-of-eve"]["audio_url"] == ""
    for lang in ["en"] + list(I.LANGS):
        prefix = "" if lang == "en" else f"{lang}/"
        feed = (out / f"{prefix}feed.xml").read_text()
        assert "the-making-of-eve" not in feed
        stub = (out / f"{prefix}episodes/the-making-of-eve/index.html").read_text()
        target = "/guide/" if lang == "en" else f"/{lang}/guide/"
        assert f'<meta http-equiv="refresh" content="0; url={target}">' in stub
        guide = (out / f"{prefix}guide/index.html").read_text()
        assert 'id="origin-story"' in guide
        assert 'id="cloning-guide"' in guide
        assert 'id="transcript"' in guide


@pytest.mark.parametrize("lang", I.LANGS)
def test_locale_audio_isolation_and_split_urls(built, lang):
    out, eps, _ = built
    feed = (out / lang / "feed.xml").read_text()
    assert f"<language>{lang}</language>" in feed
    enclosures = re.findall(r'<enclosure url="([^"]+)"', feed)
    assert enclosures, f"No enclosures in /{lang}/feed.xml"
    for url in enclosures:
        assert "#" not in url, (lang, url)

    # Check localized episodes that have dub metadata in translations/<lang>/*.json
    for tr_path in sorted((ROOT / "translations" / lang).glob("*.json")):
        slug = tr_path.stem
        if slug.startswith("_") or slug == "the-making-of-eve" or slug not in eps:
            continue
        doc = json.loads(tr_path.read_text())
        dub_url = doc.get(f"{lang}_audio_url") or doc.get("ru_audio_url")
        if not dub_url:
            continue
        ep_html = (out / lang / "episodes" / slug / "index.html").read_text()
        assert f'src="{dub_url}"' in ep_html, (lang, slug)
        assert f'data-lang-audio="{lang}"' in ep_html, (lang, slug)
        assert f"/audio/{lang}/" in dub_url

        # Verify standalone splits inherit localized split audio URLs and turn offsets
        for sp in eps[slug].get("splits", []):
            sp_slug = sp["slug"]
            sp_html_path = out / lang / "episodes" / sp_slug / "index.html"
            if not sp_html_path.exists():
                continue
            sp_html = sp_html_path.read_text()
            expected_sp_url = f"https://audio.askevepodcast.com/audio/{lang}/splits/{sp['stem']}.v1.mp3"
            assert f'src="{expected_sp_url}"' in sp_html, (lang, sp_slug)
            assert f'data-lang-audio="{lang}"' in sp_html, (lang, sp_slug)
            assert expected_sp_url in feed, (lang, sp_slug)
