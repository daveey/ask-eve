"""Site build: the redesigned pages render from a local D1 copy and carry the elements the site depends on."""
import json
import re
import xml.etree.ElementTree as ET

import pytest

from pipeline_helpers import ROOT
import build_site as B


def unlink(h: str) -> str:
    """Page HTML with the silent auto-links removed (their text kept)."""
    return re.sub(r'<a class="xl"[^>]*>(.*?)</a>', r"\1", h)


import sqlite_apply  # noqa: E402

EP0, EP1 = "first-encounter", "the-making-of-eve"


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
    assert h.count('class="card"') == 1 and h.count('class="card card--bonus"') == 0   # main list: full episodes only
    assert f'href="/episodes/{EP0}/"' in h and f'href="/episodes/{EP1}/"' in h
    assert 'href="/feed.xml"' in h and "Spotify" not in h          # Spotify slot hidden until configured
    assert 'href="/people/david-sacks/"' in h


def test_episode_page(site):
    h = read(site, f"episodes/{EP0}/index.html")
    assert 'id="player"' in h and 'data-act="toggle"' in h and 'data-act="back"' in h and 'data-act="fwd"' in h
    assert 'data-act="speed"' in h and 'class="range"' in h and "audio.askevepodcast.com" in h
    turns = re.findall(r'<li class="turn turn--(david|eve)[^"]*" id="turn-\d+" data-start="[\d.]+" data-end="[\d.]+"', h)
    from align_audio import parse_transcript
    assert len(turns) == len(parse_transcript((ROOT / "episodes/2026-10-23_eve-david-podcast-planning.md").read_text()))
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
    assert 'class="take"' in p and "An AI's opinion, not a fact." in p
    assert re.search(rf'href="/episodes/{EP0}/#t=\d+"', p)
    assert "<mark>" not in p and "moment__q" not in p          # the old text-match snippets are gone
    assert "discussions ·" in idx or "discussion ·" in idx


def _moments(slug):
    return json.loads((ROOT / f"index/moments/{slug}.json").read_text())["moments"]


def test_person_page_one_block_per_moment(site):
    ms = [m for m in _moments(EP0) if m["person"] == "david-sacks"]
    assert ms
    p = read(site, "people/david-sacks/index.html")
    assert p.count('<li class="pm">') == sum(1 for slug in (EP0, EP1) for m in _moments(slug) if m["person"] == "david-sacks")
    import html as _h
    u = unlink(p)
    assert 'class="take__body take__qa"' in p and 'class="pm__qa"' in p
    assert '<span class="qa-spk qa-spk--david">David:</span>' in p and '<span class="qa-spk qa-spk--eve">Eve:</span>' in p
    for m in ms:
        start = int(m["start"])
        assert f'href="/episodes/{EP0}/#t={start}"' in p
        assert f"Listen from {B.clock(m['start'])}" in p and f"{B.clock(m['start'])}–{B.clock(m['end'])}" in p
        assert _h.escape(m["david_q"]) in u and _h.escape(m["eve_a"]) in u
    assert '<span class="pm__ep">First Encounter</span>' in p


def test_people_only_link_episodes_with_moments(site):
    by_ep = {slug: {m["person"] for m in _moments(slug)} for slug in (EP0, EP1)}
    for f in (site / "people").glob("*/index.html"):
        slug, h = f.parent.name, f.read_text()
        if not any(slug in s for s in by_ep.values()) and "person--take" in h:
            continue      # no indexed discussions yet: the page lists the moments Eve's take cites instead
        for ep in (EP0, EP1):
            assert (f'class="pep__title"><a href="/episodes/{ep}/"' in h) == (slug in by_ep[ep]), (slug, ep)
    for ep in (EP0, EP1):
        h = read(site, f"episodes/{ep}/index.html")
        side = h[h.index("People in this episode"):] if "People in this episode" in h else ""
        side = side[:side.index("</section>")] if side else ""
        assert set(re.findall(r'href="/people/([a-z0-9-]+)/"', side)) == by_ep[ep], ep


def test_moment_html_without_offsets():
    ep = {"slug": "x", "title": "T & U"}
    m = {"start": None, "end": None, "clock": "07:59:21", "summary": "David asks <x>; Eve explains.", "topics": ["ai-policy"],
         "first_turn": {"i": 38}}
    h = B.moment_html(ep, m)
    assert 'href="/episodes/x/#turn-38"' in h and "Read from 07:59" in h and "T &amp; U" in h and "&lt;x&gt;" in h
    assert '<li class="pm__tag">ai-policy</li>' in h


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
        assert not re.search(r"<script(?![^>]*\b(src=|type=\"application/(ld\+json|json)\"))", h), f  # no inline JS
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


def test_episode0_title_first_encounter(site):
    old = "Eve-David podcast planning session"
    for rel in ["index.html", f"episodes/{EP0}/index.html", "people/david-sacks/index.html"]:
        h = read(site, rel)
        assert "First Encounter" in h and f">{old}<" not in h and f"\"{old}\"" not in h, rel   # verbatim quotes in transcripts may mention it
    item = [i for i in ET.parse(site / "feed.xml").getroot().iter("item") if i.findtext("link").endswith(f"/{EP0}/")][0]
    assert item.findtext("title") == "First Encounter"
    assert item.findtext("guid") == "ask-eve:2026-10-23_eve-david-podcast-planning"
    assert item.find("enclosure").get("url") == "https://audio.askevepodcast.com/audio/en/2026-10-23_eve-david-podcast-planning.v2.mp3"


CTX0 = ("This show wasn't planned. Late one night David was listening to the All-In podcast and opened Grok's voice mode "
        "with one question: what happened to Marc Andreessen and Ben Horowitz?")
TOPIC0_FIRST = "Marc Andreessen and Ben Horowitz's turn to the right, and what's driving it"
TOPIC0_LAST = "Turning the conversation into a show: the name, the format, translation, guests, memory, and Grok becoming Eve"


def test_episode0_context_and_topics_on_page(site):
    h = read(site, f"episodes/{EP0}/index.html")
    import html as _h
    ctx, t1, t10 = _h.escape(CTX0), _h.escape(TOPIC0_FIRST), _h.escape(TOPIC0_LAST)
    h = unlink(h)
    assert ctx in h and t1 in h and t10 in h
    pos, at = [], h.index('class="ep-title"')       # (the context is also the <meta> description in <head>)
    for needle in [">Context<", ctx, ">Topics<", t1, t10, 'class="transcript"']:
        at = h.index(needle, at)
        pos.append(at)
    assert pos == sorted(pos)
    assert h.count("<li>", h.index('class="ep-topics"'), h.index("</ul>", h.index('class="ep-topics"'))) == 10
    assert 'class="ep-blurb"' not in h      # the context replaces the hero blurb (no duplicate)


def test_episode_context_is_home_blurb(site):
    import html as _h
    home = read(site, "index.html")
    assert _h.escape(CTX0) in home and "The conversation that started the show." not in home   # beats site.json
    assert "This bonus episode picks up" not in home       # bonus context is never the featured blurb


def test_feed_context_then_topics(site):
    root = ET.parse(site / "feed.xml").getroot()
    item = [i for i in root.iter("item") if i.findtext("link").endswith(f"/{EP0}/")][0]
    ns = "{http://www.itunes.com/dtds/podcast-1.0.dtd}"
    desc = item.findtext("description")
    assert desc == item.findtext(f"{ns}summary")
    assert desc.index("AI") < desc.index(CTX0) < desc.index("Topics:\n- " + TOPIC0_FIRST) < desc.index("- " + TOPIC0_LAST)
    enc = item.findtext("{http://purl.org/rss/1.0/modules/content/}encoded")
    assert enc and "<ul><li>Marc Andreessen and Ben Horowitz&#x27;s turn" in enc and enc.count("<li>") == 10
    assert "This show wasn&#x27;t planned." in enc


def test_episode_without_context_keeps_blurb():
    ep = {"context": "", "topics": []}
    assert B.context_html(ep) == ""
    out = B.context_html({"context": "C & D", "topics": ["a<b"]})
    assert "C &amp; D" in out and "<li>a&lt;b</li>" in out


def test_bonus_episode_never_featured(site):
    h = read(site, "index.html")
    hero = h[h.index('class="hero"'):h.index('class="show"')]
    assert "First Encounter" in hero and "Making of Eve" not in hero and f"/episodes/{EP0}/" in hero
    main = h[h.index('id="eps-h"'):h.index('aria-labelledby="bonus-h"')]
    assert f"/episodes/{EP0}/" in main and f"/episodes/{EP1}/" not in main
    bonus = h[h.index('aria-labelledby="bonus-h"'):]
    bonus = bonus[:bonus.index("</section>")]
    assert f'href="/episodes/{EP1}/"' in bonus and '<span class="badge badge--bonus">Bonus</span>' in bonus
    assert "Bonus: The Making of Eve" in bonus and f"/episodes/{EP0}/" not in bonus


def test_bonus_episode_page_badge_and_people_refs(site):
    h = read(site, f"episodes/{EP1}/index.html")
    assert '<p class="ep-badges"><span class="badge badge--bonus">Bonus</span></p>' in h
    assert '<h1 class="ep-title">Bonus: The Making of Eve</h1>' in h and ">BONUS<" in h
    assert '"episodeNumber": null' in h
    assert 'badge--bonus' not in read(site, f"episodes/{EP0}/index.html").split('class="more"')[0]
    # person pages and moments still resolve for full episodes
    assert f'href="/episodes/{EP0}/#t=' in read(site, "people/david-sacks/index.html")


def test_feed_bonus_item(site):
    root = ET.fromstring(read(site, "feed.xml"))
    ns = {"itunes": "http://www.itunes.com/dtds/podcast-1.0.dtd"}
    items = {i.findtext("guid"): i for i in root.iter("item")}
    assert len(items) == 2
    b = items["ask-eve:2026-10-23_the-making-of-eve"]
    assert b.findtext("title") == "Bonus: The Making of Eve"
    assert b.findtext("itunes:episodeType", namespaces=ns) == "bonus" and b.find("itunes:episode", ns) is None
    assert b.find("enclosure").get("url") == "https://audio.askevepodcast.com/audio/en/2026-10-23_the-making-of-eve.v2.mp3"
    f = items["ask-eve:2026-10-23_eve-david-podcast-planning"]
    assert f.findtext("title") == "First Encounter" and f.findtext("itunes:episodeType", namespaces=ns) == "full"
    assert f.find("itunes:episode", ns) is None      # episode 0: no number rather than 0
    for i in items.values():                         # every item, full and bonus, is in season 1
        assert i.findtext("itunes:season", namespaces=ns) == "1"


def test_itunes_season_rules():
    import render_feed as F
    from frontmatter import season
    assert season({}) == 1 and season({"season": "2"}) == 2 and season({"season": ""}) == 1
    for bad in ("0", "two", "-1"):
        with pytest.raises(ValueError):
            season({"season": bad})
    assert F.itunes_season({}) == 1 and F.itunes_season({"season": 3}) == 3 and F.itunes_season({"season": None}) == 1


def test_itunes_episode_number_rules():
    import render_feed as F
    assert F.itunes_episode({"episode_type": "full", "episode_number": 3}) == 3
    assert F.itunes_episode({"episode_type": "full", "episode_number": 1}) == 1
    assert F.itunes_episode({"episode_type": "full", "episode_number": 0}) is None      # Apple wants positive integers
    assert F.itunes_episode({"episode_type": "full", "episode_number": None}) is None
    assert F.itunes_episode({"episode_type": "bonus", "episode_number": 1}) is None     # bonus items are unnumbered
    assert F.itunes_episode({"episode_type": "trailer", "episode_number": 2}) is None
    assert F.itunes_episode({"episode_number": 2}) == 2                                 # missing type = full


def test_front_matter_episode_type():
    from frontmatter import episode_number, episode_type
    assert episode_type({}) == "full" and episode_type({"episode_type": "Bonus"}) == "bonus"
    assert episode_number({"episode": "2"}) == 2 and episode_number({"episode": "2", "episode_type": "bonus"}) is None
    with pytest.raises(ValueError):
        episode_type({"episode_type": "special"})


def test_header_subscribe_icons_on_every_page(site):
    pages = ["index.html", "about/index.html", "people/index.html", "people/david-sacks/index.html",
             f"episodes/{EP0}/index.html", f"episodes/{EP1}/index.html", "404.html"]
    for rel in pages:
        h = read(site, rel)
        head = h[h.index('<header class="site-head">'):h.index("</header>")]
        assert ('<a class="hsub hsub--rss" href="https://askevepodcast.com/feed.xml" aria-label="Subscribe with RSS" '
                'title="Subscribe with RSS">') in head, rel
        assert ('<a class="hsub hsub--apple" href="podcast://askevepodcast.com/feed.xml" '
                'aria-label="Subscribe on Apple Podcasts" title="Subscribe on Apple Podcasts" rel=noopener>') in head, rel
        assert "spotify" not in head.lower(), rel                 # hidden until site.json has a URL
        assert '<details class="menu" data-menu>' in head and 'aria-label="Menu"' in head
        assert head.count('href="/eve/"') == 2 and "/mind/" not in head   # inline nav + compact menu
        nav = head[head.index('class="site-nav"'):head.index("</nav>", head.index('class="site-nav"'))]
        assert "/about/" not in nav and head.count('href="/about/"') == 1   # About: compact menu only (and footer)
    home = read(site, "index.html")
    subs = home[home.index('<div class="subs"'):]
    assert 'href="podcast://askevepodcast.com/feed.xml"' in subs.split("</div>")[0]   # home subscribe area matches


def test_head_subscribe_spotify_when_configured():
    import build_site as B
    h = B.head_subscribe({"subscribe": {"spotify": "https://open.spotify.com/show/x", "apple_podcasts": ""}})
    assert 'aria-label="Follow on Spotify"' in h and "hsub--apple" not in h and "hsub--rss" in h
    assert [x for x, _ in B.nav_items()] == ["/", "/eve/"]
    assert [x for x, _ in B.menu_items()] == ["/", "/eve/", "/about/"]


# ---------------------------------------------------------------- auto-linking (silent links + menus)
def _xl_data(h):
    m = re.search(r'<script type="application/json" id="xl-data">(.*?)</script>', h, re.S)
    return json.loads(m.group(1)) if m else {}


def test_context_links_all_in_out(site):
    h = read(site, f"episodes/{EP0}/index.html")
    ctx = h[h.index('class="ep-context__p"'):]
    ctx = ctx[:ctx.index("</p>")]
    assert re.search(r'<a class="xl" href="/topics/all-in-power-center/" data-xl="all-in" '
                     r'aria-haspopup="menu" aria-expanded="false">All-In podcast</a>', ctx)   # Eve's topic page first
    menu = _xl_data(h)["all-in"]["items"]
    assert menu[0]["kind"] == "site" and "https://allin.com" in [i["url"] for i in menu]
    assert [i["kind"] for i in menu][:3] == ["site", "web", "wikipedia"] and menu[1]["url"] == "https://allin.com"


def test_transcript_sacks_links_to_person_page(site):
    h = read(site, f"episodes/{EP0}/index.html")
    turns = re.findall(r'<li class="turn[^"]*" id="turn-\d+".*?</li>', h, re.S)
    hit = [t for t in turns if 'data-xl="david-sacks"' in t]
    assert hit and re.search(r'<a class="xl" href="/people/david-sacks/" data-xl="david-sacks"[^>]*>(?:David )?Sa[ck]h?s</a>', hit[0])
    items = _xl_data(h)["david-sacks"]["items"]
    assert items[0] == {"label": "Eve on David Sacks", "url": "/people/david-sacks/", "kind": "site"}
    kinds = [i["kind"] for i in items]
    assert kinds.index("site") < kinds.index("x") < kinds.index("wikipedia") < kinds.index("grokipedia")
    for t in turns:   # first mention per turn only
        body = t[t.index('class="turn__body"'):]
        for key in set(re.findall(r'data-xl="([^"]+)"', body)):
            assert body.count(f'data-xl="{key}"') == 1


def test_links_never_inside_code_links_headings_or_disclosure(site):
    for rel in [f"episodes/{EP0}/index.html", f"episodes/{EP1}/index.html"]:
        h = read(site, rel)
        assert not re.search(r"<code>[^<]*<a class=\"xl\"", h)
        assert not re.search(r"<h[1-6][^>]*>[^<]*<a class=\"xl\"", h)
        assert not re.search(r'<a [^>]*href="[^"]*"[^>]*>[^<]*<a class="xl"', h)
        disc = h[h.index('class="disclosure'):] if 'class="disclosure' in h else ""
        assert 'class="xl"' not in disc[:disc.find("</aside>") if "</aside>" in disc else 2000]
        assert '<script src="/assets/links' in h or "assets/links." in h


def test_person_page_moment_links_skip_self(site):
    h = read(site, "people/david-sacks/index.html")
    assert 'data-xl="david-sacks"' not in h
    assert "xl-data" in h and 'class="xl"' in h


def test_no_topic_links_until_topic_pages_exist(site):
    for rel in [f"episodes/{EP0}/index.html", f"episodes/{EP1}/index.html", "people/david-sacks/index.html"]:
        assert "/topic/" not in read(site, rel)


def test_linker_rules():
    import build_site as B
    ents = {"david-sacks": {"name": "David Sacks", "terms": ["David Sacks", "Sacks", "Sachs"],
                            "items": [{"label": "Eve on David Sacks", "url": "/people/david-sacks/", "kind": "site"}]},
            "a16z": {"name": "a16z", "terms": ["Andreessen Horowitz", "a16z"],
                     "items": [{"label": "a16z.com", "url": "https://a16z.com", "kind": "web"}]},
            "marc-andreessen": {"name": "Marc Andreessen", "terms": ["Andreessen"],
                                "items": [{"label": "Eve on Marc Andreessen", "url": "/people/marc-andreessen/", "kind": "site"}]}}
    L = B.Linker(ents, "/x/")
    out = L.html("<p>Sachs met Sacks at Andreessen Horowitz; sacks of mail; Sacksville; <code>Sacks</code></p>")
    assert out.count('data-xl="david-sacks"') == 1 and ">Sachs</a> met Sacks" in out          # first mention only
    assert '>Andreessen Horowitz</a>' in out and 'data-xl="marc-andreessen"' not in out       # longest match wins
    assert "sacks of mail" in out and "Sacksville" in out and "<code>Sacks</code>" in out      # case + whole word + code
    assert B.Linker(ents, "/people/david-sacks/").text("Sacks") == "Sacks"                     # self link dropped -> plain
    assert B.Linker(ents, "/", exclude={"david-sacks"}).text("Sacks") == "Sacks"
    assert L.text("<b>Sacks</b>").startswith("&lt;b&gt;<a class=\"xl\"")


# ---------------------------------------------------------------- Meet Eve
def test_eve_page_quotes_link_to_their_moment(site):
    h = read(site, "eve/index.html")
    assert "<h1 class=\"page-title\">I'm Eve.</h1>" in h
    assert 'class="rpg-card"' in h and 'class="rpg-stats"' in h and h.count('class="rpg-stat"') == 6
    assert h.count('class="q__ctx"') >= 10
    assert 'href="/eve/"' in read(site, "index.html")          # in the header nav
    data = json.loads((ROOT / "index/eve.json").read_text())
    srcs = re.findall(r'<a class="q__src" href="/episodes/([^/]+)/#(t=\d+|turn-\d+)"', h)
    assert 10 <= len(srcs) <= 16
    ep0 = read(site, f"episodes/{EP0}/index.html")
    for slug, frag in srcs:
        assert slug in (EP0, EP1)
        if frag.startswith("t="):
            sec = int(frag[2:])
            assert sec >= 0
    # the exchange answer at 09:14:08 points at the turn that starts at that offset
    m = re.search(r'id="turn-(\d+)" data-start="([\d.]+)"[^>]*>(?:(?!</li>).)*?09:14 UTC(?:(?!</li>).)*?Mostly context', ep0, re.S)
    assert m and f'/episodes/{EP0}/#t={int(float(m.group(2)))}' in h


def test_eve_page_hides_pending_episode(site):
    h = read(site, "eve/index.html")
    assert "eve-cant-find-andreessen" not in h and "(pending" not in h.lower() and "pending publication" not in h.lower()
    assert "episode 2" not in h.lower()
    assert "Why her memory missed" not in h
    assert "SpaceXSI [sic]" in h                                  # editorial mark kept on display
    assert "no weapons, no sexual content involving minors" in h   # hard-limits line kept


def test_eve_citations_are_verbatim(site):
    import build_site as B
    db = site.parent / "d.sqlite"
    eps, _ = B.load_site(db, True)
    R = B.EveSources(eps)
    data = json.loads((ROOT / "index/eve.json").read_text())
    n = 0

    def walk(o):
        nonlocal n
        if isinstance(o, dict):
            if "quote_excerpt" in o and "episode_slug" in o:
                r = R.resolve(o)                 # raises if not verbatim
                assert (r is None) == (o["episode_slug"] not in R.eps or bool(o.get("pending")))
                n += r is not None
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(data["sections"])
    assert 15 <= n <= 24


def test_eve_hub_holds_mind(site):
    h = read(site, "eve/index.html")
    ids = re.findall(r'<section class="eve-sec" id="([\w-]+)"', h)
    assert ids[:3] == ["who-eve-is", "mind", "self-assessment"] and ids[-1] == "persona-notes"
    assert 'id="memories"' in h and 'id="people"' in h and 'id="topics"' in h   # topics approved 2026-10-23
    assert 'href="/episodes/first-encounter/"' in h and 'href="/episodes/the-making-of-eve/"' in h
    assert 'href="/people/david-sacks/"' in h and 'href="/people/"' in h
    assert '<nav class="eve-nav" aria-label="On this page">' in h and 'href="#mind"' in h
    assert '<details class="fold' in h and "<script" not in h.split("<footer")[0].split("</header>")[1]
    assert '<a href="/eve/" aria-current=page>Eve</a>' in h
    for rel in ("people/index.html", "people/david-sacks/index.html"):        # /people/ still works, under Eve
        p = read(site, rel)
        assert '<a href="/eve/" aria-current=page>Eve</a>' in p and 'href="/eve/#mind"' in p and "/mind/" not in p
    assert "/mind/" not in read(site, "sitemap.xml")
    foot = h[h.index('<footer'):]
    assert 'href="/about/"' in foot and 'href="/people/"' in foot and "/mind/" not in foot


def test_mind_redirects_to_eve(site):
    assert read(site, "_redirects").startswith("/mind/ /eve/#mind 301\n/mind /eve/#mind 301\n")
    stub = read(site, "mind/index.html")                       # fallback if _redirects isn't honoured
    assert '<meta http-equiv="refresh" content="0; url=/eve/#mind">' in stub and "<script" not in stub
    assert 'rel="canonical" href="https://askevepodcast.com/eve/"' in stub and 'name="robots" content="noindex"' in stub


def test_about_how_it_works(site):
    h = read(site, "about/index.html")
    assert 'id="how-it-works"' in h and '<figure class="flow"' in h and "mermaid" not in h
    for w in ("faster-whisper", "xAI text-to-speech", "ID3 chapters", "Cloudflare R2", "D1", "build_site.py",
              "render_feed.py", "Cloudflare Pages", "Apple Podcasts", "Spotify", "Honesty rules", "Her profile"):
        assert w in h, w
    assert 'href="https://github.com/daveey/ask-eve"' in h
    for rel in ("index.html", "people/index.html", "eve/index.html"):          # GitHub in the footer sitewide
        foot = read(site, rel).split("<footer", 1)[1]
        assert 'href="https://github.com/daveey/ask-eve" rel="noopener">GitHub</a>' in foot, rel
    assert "gmail" not in h.lower()


def test_renamed_episode_slug_and_redirects(site, tmp_path):
    """index/slugs.json renames ep 0 to first-encounter: the same build from a D1 still holding the old slug gives the
    same public URL; the old URL 301s (fragment-free dest so #t= survives); guid and enclosure keep the stem."""
    red = read(site, "_redirects")
    for line in ["/episodes/eve-david-podcast-planning/ /episodes/first-encounter/ 301",
                 "/episodes/eve-david-podcast-planning /episodes/first-encounter/ 301",
                 "/episodes/eve-david-podcast-planning/* /episodes/first-encounter/:splat 301"]:
        assert line in red.splitlines()
    feed = read(site, "feed.xml")
    assert "<link>https://askevepodcast.com/episodes/first-encounter/</link>" in feed
    assert "/episodes/eve-david-podcast-planning" not in feed
    assert "<guid isPermaLink=\"false\">ask-eve:2026-10-23_eve-david-podcast-planning</guid>" in feed
    db, out = tmp_path / "old.sqlite", tmp_path / "dist"
    for sql in sorted(ROOT.glob("artifacts/*/d1_upsert.sql")):
        txt = sql.read_text().replace("'first-encounter'", "'eve-david-podcast-planning'")
        (tmp_path / sql.parent.name).with_suffix(".sql").write_text(txt)
        sqlite_apply.apply(db, ROOT / "migrations", (tmp_path / sql.parent.name).with_suffix(".sql"))
    assert B.main(["--db", str(db), "--out", str(out), "--include-drafts"]) == 0
    assert (out / "episodes/first-encounter/index.html").is_file()
    assert read(out, "feed.xml") == feed or "<link>https://askevepodcast.com/episodes/first-encounter/</link>" in read(out, "feed.xml")
    assert read(out, "_redirects") == red


def test_person_take_and_citations(site):
    import html as _h
    takes = json.loads((ROOT / "index/takes.json").read_text())
    sacks = takes["people"]["david-sacks"]
    p = read(site, "people/david-sacks/index.html")
    body = unlink(p)
    for para in sacks["take"]:                       # every paragraph, verbatim (cite links stripped)
        assert _h.escape(para) in re.sub(r'<a class="cite"[^>]*>(.*?)</a>', r"\1", body)
    cites = re.findall(r'<a class="cite" href="/episodes/([^/]+)/#t=([\d.]+)"', p)
    assert len(cites) == len([r for r in sacks["refs"] if r.get("anchor")]) and all(s == EP0 for s, _ in cites)
    assert 'href="/episodes/first-encounter/#t=2519.36"' in p     # "three tests" = 08:06:53 -> the turn's exact start
    assert 'class="plinks"' in p and 'href="https://x.com/DavidSacks"' in p
    assert 'href="/topics/sacks-vs-anthropic/"' in p
    assert "Linked phrases open the episode" in p


def test_takes_resolve_and_drop_pending(site):
    import build_site as B
    eps, people = B.load_site(site.parent / "d.sqlite", True)
    topics = B.attach_takes(eps, people)
    assert len(topics) == 14 and sum(1 for p in people if p.get("take")) == 13
    assert next(p for p in people if p["slug"] == "chan")["take"]
    for x in [p["take"] for p in people if p.get("take")] + topics:
        assert all(r["href"].startswith(("/episodes/first-encounter/#t=", "/episodes/the-making-of-eve/#t=", "/episodes/the-making-of-eve/#turn-")) for r in x["refs"])
    raw = (ROOT / "index/takes.json").read_text()
    assert "eve-cant-find-andreessen" not in raw and "pending publication" not in raw and "Episode 2" not in raw
    assert not re.search(r"grok\.com/share|@gmail|canvas", raw)


def test_topic_pages(site):
    idx = read(site, "topics/index.html")
    assert idx.count('class="tcard"') == 14
    t = read(site, "topics/sacks-vs-anthropic/index.html")
    assert 'class="take"' in t and 'href="/people/david-sacks/"' in t
    assert re.search(r'<a class="pm__listen" href="/episodes/first-encounter/#t=[\d.]+"', t)
    assert t.count('<a class="cite"') == 4
    assert "/topics/" in read(site, "sitemap.xml") and "/topics/building-ask-eve/" in read(site, "sitemap.xml")
    ep = read(site, f"episodes/{EP0}/index.html")
    assert '"url": "/topics/musk-doge-usaid/"' in ep            # transcript auto-links reach topic pages
    assert 'href="/topics/' in read(site, "eve/index.html")


def test_take_bad_anchor_raises(site):
    import build_site as B
    eps, _ = B.load_site(site.parent / "d.sqlite", True)
    by = {ep["slug"]: ep for ep in eps}
    ref = {"episode": EP0, "clock_time": "08:06:53", "label": "x", "anchor": "not in the text"}
    with pytest.raises(ValueError):
        B.resolve_take(by, {"take": ["Some take."], "refs": [ref]}, "test")
    with pytest.raises(ValueError):
        B.resolve_ref(by, {**ref, "clock_time": "03:33:33"})


def test_card_summaries_under_titles(site):
    """Every episode card (main list + Bonus rows) carries a 1-2 line summary from site.json `summaries`."""
    import html as _h
    sums = json.loads((ROOT / "site/site.json").read_text())["summaries"]
    assert all(0 < len(s) <= B.CARD_SUMMARY_MAX for s in sums.values())
    h = read(site, "index.html")
    main = h[h.index('id="eps-h"'):h.index('aria-labelledby="bonus-h"')]
    assert f'<p class="card__blurb">{_h.escape(sums[EP0])}</p>' in main
    assert main.index('class="card__title"') < main.index('class="card__blurb"')
    bonus = h[h.index('aria-labelledby="bonus-h"'):]
    bonus = bonus[:bonus.index("</section>")]
    assert f'<span class="bonus-row__sum">{_h.escape(sums[EP1])}</span>' in bonus
    assert bonus.index("bonus-row__title") < bonus.index("bonus-row__sum")


def test_card_summary_precedence_and_fallback():
    long = "word " * 60
    assert B.card_summary({"summary": " Front  matter. "}, "Sidecar.", "Ctx.") == "Front matter."
    assert B.card_summary({}, "Sidecar.", "Ctx.") == "Sidecar."
    assert B.card_summary({}, None, "First one. Second one.") == "First one."
    assert B.card_summary({}, "", "Mr. Smith left the U.S. Senate. Then") == "Mr. Smith left the U.S. Senate."
    assert B.card_summary({}, "", "v1.2 is out. Next") == "v1.2 is out."
    out = B.card_summary({}, None, long.strip() + ".")
    assert out.endswith("…") and len(out) <= B.CARD_SUMMARY_MAX and not out[:-1].endswith(" ")
    assert B.card_summary({}, None, "") == ""


def test_card_summary_css_clamp():
    css = (ROOT / "site/static/assets/site.css").read_text()
    assert re.search(r"@media \(max-width:819px\)\{\.card__blurb,\.bonus-row__sum\{-webkit-line-clamp:2\}\}", css)
    m = re.search(r"\.bonus-row__sum\{[^}]*font-size:([\d.]+)rem", css)
    assert m and float(m.group(1)) * 16 >= 14 and "color:var(--muted)" in m.group(0)


def test_eve_avatar_experiment_page(site):
    eve_h = read(site, "eve/index.html")
    assert 'href="/eve/avatar/"' in eve_h
    assert "How I chose my face (20 prompts × 10 renders, scored) →" in eve_h
    assert "eve-winner-512" in eve_h
    assert 'class="rpg-portrait"' in eve_h
    av_h = read(site, "eve/avatar/index.html")
    assert "How I Chose My Face" in av_h
    assert "97.40" in av_h or "97.4" in av_h
    assert av_h.count('class="av-pblock"') == 20
    assert av_h.count('<article class="av-rcell') == 200
    assert av_h.count('<article class="av-fcard') == 10
    assert "/eve/avatar/" in read(site, "sitemap.xml")
