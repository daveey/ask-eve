"""Permanent guardrail tests for Ask Eve site build, localized audio tracks, language menu autonyms, clips, and Episode 0 removal."""
import re
from pathlib import Path
import pytest

from pipeline_helpers import ROOT
import build_site as B
import sqlite_apply

LANGS = ("en", "zh", "ru", "es", "de", "pt", "fa", "fr")
NATIVE_AUTONYMS = ("English", "Русский", "Español", "中文", "Deutsch", "Português", "فارسی", "Français")
FULL_SYNTH_LANGS = ("zh", "ru", "es")
PARTIAL_SYNTH_LANGS = ("de", "pt", "fa", "fr")
PARTIAL_SYNTH_SLUGS = ("iran-hormuz-and-the-art-of-the-deal", "starshield-musk-and-the-anthropic-mirror")


@pytest.fixture(scope="module")
def built_site(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("site_guardrail")
    db = tmp / "d.sqlite"
    sqls = sorted(ROOT.glob("artifacts/*/d1_upsert.sql"))
    assert len(sqls) >= 2
    for sql in sqls:
        sqlite_apply.apply(db, ROOT / "migrations", sql)
    out = tmp / "dist"
    assert B.main(["--db", str(db), "--out", str(out), "--include-drafts"]) == 0
    return out


def test_every_language_has_localized_episode_pages_with_correct_primary_audio(built_site):
    """Every built language must have localized episode pages under /<lang>/episodes/<slug>/index.html
    where the <audio> tag's primary <source> URL is the language-specific track (especially /audio/zh/... for zh!)."""
    en_eps_dir = built_site / "episodes"
    slugs = sorted(p.name for p in en_eps_dir.iterdir() if p.is_dir() and (p / "index.html").is_file() and 'http-equiv="refresh"' not in (p / "index.html").read_text())
    assert len(slugs) >= 10

    for lang in LANGS:
        prefix = built_site if lang == "en" else (built_site / lang)
        for slug in slugs:
            ep_html_path = prefix / "episodes" / slug / "index.html"
            assert ep_html_path.is_file(), f"Missing episode page for lang={lang}, slug={slug}"
            html = ep_html_path.read_text()

            # Find the <audio ...> tag and its first <source src="...">
            m_source = re.search(r'<audio[^>]*>\s*<source src="([^"]+)"', html)
            assert m_source, f"Missing <audio><source> on {ep_html_path}"
            primary_src = m_source.group(1)

            m_audio_src = re.search(r'<audio[^>]*\ssrc="([^"]+)"', html)
            assert m_audio_src, f"Missing <audio src=...> on {ep_html_path}"
            assert m_audio_src.group(1) == primary_src, f"<audio src> != primary <source src> on {ep_html_path}"

            if lang == "en":
                assert primary_src.startswith("https://audio.askevepodcast.com/audio/en/"), (
                    f"English episode {slug} has non-English primary audio: {primary_src}"
                )
            else:
                # Require ALL 7 non-English languages (zh, ru, es, de, pt, fa, fr) to use /audio/<lang>/... for EVERY episode, NEVER /audio/en/!
                assert primary_src.startswith(f"https://audio.askevepodcast.com/audio/{lang}/"), (
                    f"{lang} episode {slug} MUST have primary <source src='https://audio.askevepodcast.com/audio/{lang}/...'>, got: {primary_src}"
                )
                assert "/audio/en/" not in primary_src, f"{lang} episode {slug} primary source points to English audio: {primary_src}"
                assert 'data-fallback-src="https://audio.askevepodcast.com/audio/en/' in html, (
                    f"{lang} episode {slug} must retain English fallback track in data-fallback-src"
                )


def test_language_switcher_contains_exact_native_autonyms_across_all_editions(built_site):
    """Every language switcher .lang-menu__name across all 8 language editions must contain exact native autonyms."""
    for lang in LANGS:
        prefix = built_site if lang == "en" else (built_site / lang)
        for rel in ("index.html", "episodes/index.html", "people/index.html", "eve/index.html", "about/index.html"):
            p = prefix / rel
            assert p.is_file(), f"Missing {p}"
            html = p.read_text()
            names = re.findall(r'<span class="lang-menu__name">([^<]+)</span>', html)
            assert tuple(names) == NATIVE_AUTONYMS, (
                f"Language switcher on /{lang}/{rel} corrupted autonyms: got {names}, expected {list(NATIVE_AUTONYMS)}"
            )


def test_zero_episode_0_or_uncut_recording_sessions_on_home_and_episodes(built_site):
    """Zero occurrences of 'Episode 0' or 'Uncut Recording Sessions' on /index.html and /episodes/index.html."""
    for rel in ("index.html", "episodes/index.html"):
        html = (built_site / rel).read_text()
        assert "Episode 0" not in html, f"Found forbidden 'Episode 0' in {rel}"
        assert "EP 0" not in html, f"Found forbidden 'EP 0' in {rel}"
        assert "Uncut Recording Sessions" not in html, f"Found forbidden 'Uncut Recording Sessions' in {rel}"
        assert 'id="uncut-sessions"' not in html, f"Found forbidden #uncut-sessions in {rel}"


def test_clip_videos_and_posters_use_content_hashed_urls(built_site):
    """Every clip video/poster on /index.html and /clips/index.html must use a content-hashed /assets/clips/*.<hash>.mp4/.webp URL."""
    for rel in ("index.html", "clips/index.html"):
        html = (built_site / rel).read_text()
        videos = re.findall(r'<video[^>]+>', html)
        assert len(videos) >= 4, f"Expected clip <video> tags in {rel}"
        for v in videos:
            m_src = re.search(r'src="([^"]+)"', v)
            m_poster = re.search(r'poster="([^"]+)"', v)
            assert m_src and re.match(r'^/assets/clips/[a-z0-9-]+\.[0-9a-f]{10}\.mp4$', m_src.group(1)), (
                f"Unhashed clip video src in {rel}: {v}"
            )
            assert m_poster and re.match(r'^/assets/clips/[a-z0-9-]+\.[0-9a-f]{10}\.webp$', m_poster.group(1)), (
                f"Unhashed clip poster in {rel}: {v}"
            )


def test_no_cyrillic_in_non_russian_translation_files(built_site):
    """Non-Russian translation JSON files and rendered person/topic pages must never contain Cyrillic text."""
    import json
    cyr = re.compile(r"[\u0400-\u04FF]")
    for lang in ("zh", "es", "de", "pt", "fa", "fr"):
        for fp in sorted((ROOT / "translations" / lang).glob("*.json")):
            data = json.loads(fp.read_text())
            for k, v in data.get("strings", data).items():
                if isinstance(v, dict):
                    tr = v.get("tr", "")
                    en = v.get("en", "")
                    assert not (cyr.search(tr) and not cyr.search(en)), (
                        f"Cyrillic leak in {fp.relative_to(ROOT)} key={k}: {tr[:80]!r}"
                    )
        # Also verify rendered /<lang>/people/david-sacks/index.html has zero Cyrillic outside the language switcher autonym "Русский"
        sacks_html = (built_site / lang / "people/david-sacks/index.html").read_text()
        stripped = sacks_html.replace("Русский", "")
        assert not cyr.search(stripped), f"Cyrillic text leaked onto /{lang}/people/david-sacks/index.html"


def test_clip_jump_links_use_standalone_split_offsets_within_episode_duration():
    """Every clip in index/clips.json must have split_start_sec strictly inside its standalone split duration."""
    import json
    splits = {s["slug"]: s for s in json.loads((ROOT / "index/splits.json").read_text())["splits"]}
    clips = json.loads((ROOT / "index/clips.json").read_text())["clips"]
    for c in clips:
        sp = splits[c["episode_slug"]]
        t_sec = c.get("split_start_sec", c["start_sec"])
        assert 0 <= t_sec < sp["duration_seconds"], (
            f"Clip {c['id']} jump offset {t_sec}s is outside {c['episode_slug']} duration {sp['duration_seconds']}s"
        )


def test_every_non_english_rss_feed_uses_localized_audio(built_site):
    """Every non-English RSS feed /<lang>/feed.xml must use /audio/<lang>/ enclosure URLs for every item."""
    import xml.etree.ElementTree as ET
    for lang in ("zh", "ru", "es", "de", "pt", "fa", "fr"):
        feed_path = built_site / lang / "feed.xml"
        assert feed_path.is_file(), f"Missing RSS feed {feed_path}"
        root_el = ET.fromstring(feed_path.read_text())
        items = list(root_el.iter("item"))
        assert len(items) >= 12
        for item in items:
            enc = item.find("enclosure")
            assert enc is not None
            url = enc.get("url") or ""
            assert url.startswith(f"https://audio.askevepodcast.com/audio/{lang}/"), (
                f"/{lang}/feed.xml item {item.findtext('guid')} has non-{lang} audio URL: {url}"
            )


def test_rtl_scrubber_and_standalone_split_person_citations(built_site):
    """/fa/people/david-sacks/ must link to standalone split episodes (never first-encounter) and i18n.css must keep .player__scrub LTR."""
    fa_sacks = (built_site / "fa/people/david-sacks/index.html").read_text()
    assert "/fa/episodes/david-sacks-and-the-anthropic-war/#t=" in fa_sacks
    assert "/fa/episodes/first-encounter/#t=" not in fa_sacks
    i18n_css = (ROOT / "site/static/assets/i18n.css").read_text()
    assert '[dir="rtl"] .player__scrub' in i18n_css and "direction:ltr" in i18n_css
