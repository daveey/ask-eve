"""Site build: the redesigned pages render from a local D1 copy and carry the elements the site depends on."""
import json
import re
import xml.etree.ElementTree as ET

import pytest

from pipeline_helpers import ROOT
import build_site as B
import sqlite_apply

EP0, EP1 = "eve-david-podcast-planning", "the-making-of-eve"


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("site")
    db = tmp / "d.sqlite"
    sqls = sorted(ROOT.glob("artifacts/*/d1_upsert.sql"))
    assert len(sqls) >= 2
    for sql in sqls:
        sqlite_apply.apply(db, ROOT / "migrations", sql)
    out = tmp / "dist"
    assert B.main(["--db", str(db), "--out", str(out), "--include-drafts"]) == 0
    return out


def read(out, rel):
    return (out / rel).read_text()


def test_pages_exist(site):
    for rel in ["index.html", "about/index.html", "people/index.html", "404.html", "feed.xml", "sitemap.xml",
                "robots.txt", "favicon.svg", "_headers", f"episodes/{EP0}/index.html", f"episodes/{EP0}/transcript.txt",
                f"episodes/{EP1}/index.html", "people/david-sacks/index.html", "fonts/fraunces-var.woff2"]:
        assert (site / rel).is_file(), rel
    ET.parse(site / "feed.xml")
    assert len(list((site / "assets").glob("site.*.css"))) == 1


def test_head_seo(site):
    for rel, canon in [("index.html", "https://askevepodcast.com/"), (f"episodes/{EP1}/index.html", f"https://askevepodcast.com/episodes/{EP1}/"),
                       ("people/index.html", "https://askevepodcast.com/people/"), ("about/index.html", "https://askevepodcast.com/about/")]:
        h = read(site, rel)
        assert f'<link rel="canonical" href="{canon}">' in h, rel
        assert '<link rel="alternate" type="application/rss+xml"' in h and 'href="https://askevepodcast.com/feed.xml"' in h
        assert re.search(r'<meta name="description" content="[^"]{40,}">', h), rel
        assert 'property="og:image"' in h and "<title>" in h
        assert 'name="viewport"' in h and 'name="color-scheme" content="light dark"' in h


def test_home(site):
    h = read(site, "index.html")
    assert 'class="hero"' in h and "data-play" in h and 'id="player"' in h and "is-idle" in h
    assert h.count('class="card"') == 2
    assert f'href="/episodes/{EP0}/"' in h and f'href="/episodes/{EP1}/"' in h
    assert 'href="/feed.xml"' in h and "Spotify" not in h          # Spotify slot hidden until configured
    assert 'href="/people/david-sacks/"' in h


def test_episode_page(site):
    h = read(site, f"episodes/{EP0}/index.html")
    assert 'id="player"' in h and 'data-act="toggle"' in h and 'data-act="back"' in h and 'data-act="fwd"' in h
    assert 'data-act="speed"' in h and 'class="range"' in h and "audio.askevepodcast.com" in h
    turns = re.findall(r'<li class="turn turn--(david|eve)[^"]*" id="turn-\d+" data-start="[\d.]+" data-end="[\d.]+"', h)
    from align_audio import parse_transcript
    assert len(turns) == len(parse_transcript((ROOT / f"episodes/2026-10-23_{EP0}.md").read_text()))
    assert {"david", "eve"} <= set(turns)
    assert 'class="disclosure' in h and "Fully AI-reconstructed audio" in h
    assert "vb--narrator" in h and "vb--eve" in h and "vb--real" not in h
    assert h.count('class="chap"') >= 5 and "data-seek" in h
    assert 'href="/people/david-sacks/"' in h
    assert '"@type": "PodcastEpisode"' in h


def test_hybrid_episode_badges(site):
    h = read(site, f"episodes/{EP1}/index.html")
    assert "Hybrid: real and AI voices" in h
    assert h.count('<span class="vb vb--real"') >= 10 and "vb--narrator" in h and "vb--gap" in h
    assert "(AI-reconstructed, narrator voice)" not in h       # chapter titles are prettified, label kept as a badge


def test_people_pages(site):
    idx = read(site, "people/index.html")
    assert idx.count('class="pcard"') >= 10
    p = read(site, "people/david-sacks/index.html")
    assert "Eve&#x27;s current take" in p or "Eve's current take" in p
    assert re.search(rf'href="/episodes/{EP0}/#t=\d+"', p) and "<mark>" in p


def test_about_and_privacy(site):
    a = read(site, "about/index.html")
    assert "mailto:podcast@askevepodcast.com" in a and "Memory" in a and "Re-voicing" in a and "Labeling" in a
    for f in site.rglob("*.html"):
        h = f.read_text()
        emails = set(re.findall(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", h))
        assert emails <= {"podcast@askevepodcast.com"}, (f, emails)    # never a personal address
        for src in re.findall(r'<script[^>]*\bsrc="([^"]+)"', h):
            assert src.startswith("/assets/"), (f, src)               # no third-party scripts
        assert not re.search(r'<link[^>]+href="https?://(?!askevepodcast\.com)', h), f
        assert not re.search(r"<script(?![^>]*\b(src=|type=\"application/ld\+json\"))", h), f  # no inline JS
        assert ' style="' not in h, f                                # CSP: style-src 'self'
    hdr = read(site, "_headers")
    assert "Content-Security-Policy" in hdr and "/feed.xml" in hdr


def test_spotify_shown_only_when_configured():
    cfg = json.loads((ROOT / "site/site.json").read_text())
    assert "spotify" not in B.subscribe_links(cfg).lower()
    cfg["subscribe"]["spotify"] = "https://open.spotify.com/show/example"
    assert 'href="https://open.spotify.com/show/example"' in B.subscribe_links(cfg)


def test_parse_turns_matches_align_audio():
    from align_audio import parse_transcript
    for md in ROOT.glob("episodes/2026-*.md"):
        text = md.read_text()
        ours, theirs = B.parse_turns(text), parse_transcript(text)
        assert [(t["i"], t["speaker"]) for t in ours] == [(t.index, t.speaker) for t in theirs], md.name
