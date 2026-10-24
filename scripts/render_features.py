#!/usr/bin/env python3
"""Modular renderers for /contact/, /eve/* sub-pages, /growth/, audiogram clips, and split session provenance."""
from __future__ import annotations

import html
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLIPS_FILE = ROOT / "index/clips.json"
JOURNAL_FILE = ROOT / "index/journal.json"
GROWTH_FILE = ROOT / "index/growth.json"
SOUL_FILE = ROOT / "persona/SOUL.md"

e = html.escape

EVE_SUBNAV_ITEMS = [
    ("/eve/", "Overview"),
    ("/eve/mind/", "Mind & Stances"),
    ("/eve/journal/", "Daily Journal"),
    ("/eve/reading/", "Reading List"),
    ("/eve/soul/", "Soul & Evolution"),
    ("/eve/avatar/", "Avatar Lab"),
]


def eve_subnav_html(active_path: str) -> str:
    links = []
    for href, label in EVE_SUBNAV_ITEMS:
        cur = ' aria-current="page"' if href == active_path else ""
        cls = "eve-subnav__link is-active" if href == active_path else "eve-subnav__link"
        links.append(f'<a class="{cls}" href="{href}"{cur}>{e(label)}</a>')
    return (
        '<nav class="eve-subnav" aria-label="Eve pages">'
        + "".join(links)
        + "</nav>"
    )


def md_inline_html(L, s: str, seen: set | None = None) -> str:
    """Escape text, convert **bold**, *italic*, and `code` markdown, then auto-link via Linker."""
    p = e(s)
    p = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", p)
    p = re.sub(r"(?<!\*)\*([^*\n]+?)\*(?!\*)", r"<em>\1</em>", p)
    p = re.sub(r"`([^`]+)`", r"<code>\1</code>", p)
    return L.html(p, seen)


def load_clips() -> list[dict]:
    if not CLIPS_FILE.exists():
        return []
    data = json.loads(CLIPS_FILE.read_text())
    return data.get("clips", [])


def load_journal() -> dict:
    if not JOURNAL_FILE.exists():
        return {"entries": []}
    return json.loads(JOURNAL_FILE.read_text())


def load_growth() -> dict:
    if not GROWTH_FILE.exists():
        return {"reading_ledger": [], "discourse_radar": [], "self_directed_threads": []}
    return json.loads(GROWTH_FILE.read_text())


def _clock(sec: float) -> str:
    s = max(0, int(round(sec)))
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _human_date(d: str) -> str:
    import datetime as dt
    try:
        x = dt.date.fromisoformat(d[:10])
        return f"{x.strftime('%b')} {x.day}, {x.year}"
    except Exception:
        return d


def _clip_share_urls(c: dict) -> tuple[str, str]:
    import urllib.parse
    cid = c["id"]
    share_url = f"https://askevepodcast.com/clips/{cid}/"
    eve_q = (c.get("eve_caption") or c.get("quote") or "").strip("“”\"")
    tweet_text = f"{c['title']} — “{eve_q[:180]}{'…' if len(eve_q) > 180 else ''}”"
    x_intent = "https://x.com/intent/tweet?" + urllib.parse.urlencode({"text": tweet_text, "url": share_url})
    return share_url, x_intent


def clip_card_html(A, c: dict) -> str:
    mp4_rel = (c.get("video_path") or c.get("mp4") or "").lstrip("/")
    poster_rel = (c.get("poster_path") or c.get("poster") or "").lstrip("/")
    mp4_url = A(mp4_rel)
    poster_url = A(poster_rel)
    cid = c["id"]
    ep_slug = c["episode_slug"]
    t_sec = int(round(c.get("split_start_sec", c["start_sec"])))
    dur = int(round(c["duration_sec"]))
    badge = c.get("badge") or c.get("person_name") or "Ask Eve"
    david_q = (c.get("david_q") or c.get("quote") or c.get("eve_caption") or "").strip("“”\"")
    share_url, x_intent = _clip_share_urls(c)
    return (
        f'<article class="clip-card" id="{e(cid)}">'
        f'<div class="clip-card__video-wrap">'
        f'<video class="clip-card__video" controls preload="metadata" playsinline '
        f'poster="{e(poster_url)}" src="{e(mp4_url)}"></video>'
        f'</div>'
        f'<div class="clip-card__body">'
        f'<p class="clip-card__kicker tnum"><span class="clip-card__badge">{e(badge)}</span> · {dur}s audiogram</p>'
        f'<h3 class="clip-card__title"><a href="/clips/{e(cid)}/">{e(c["title"])}</a></h3>'
        f'<p class="clip-card__prompt"><strong class="clip-card__prompt-who">David’s question:</strong> “{e(david_q)}”</p>'
        f'<div class="clip-card__actions">'
        f'<a class="clip-card__jump tnum" href="/episodes/{e(ep_slug)}/#t={t_sec}">Play in context ({_clock(t_sec)}) →</a>'
        f'<button type="button" class="clip-card__btn clip-card__copy tnum" data-copy-url="{e(share_url)}">Copy link</button>'
        f'<a class="clip-card__share tnum" href="{e(x_intent)}" target="_blank" rel="noopener">Share on X ↗</a>'
        f'<a class="clip-card__dl tnum" href="{e(mp4_url)}" download>MP4 ↓</a>'
        f'</div>'
        f'</div>'
        f'</article>'
    )


def clip_page_html(A, c: dict, all_clips: list[dict]) -> str:
    mp4_rel = (c.get("video_path") or c.get("mp4") or "").lstrip("/")
    poster_rel = (c.get("poster_path") or c.get("poster") or "").lstrip("/")
    mp4_url = A(mp4_rel)
    poster_url = A(poster_rel)
    cid = c["id"]
    ep_slug = c["episode_slug"]
    ep_title = c.get("episode_title") or ep_slug
    t_sec = int(round(c.get("split_start_sec", c["start_sec"])))
    dur = int(round(c["duration_sec"]))
    badge = c.get("badge") or c.get("person_name") or "Ask Eve"
    person_slug = c.get("person_slug") or ""
    david_q = (c.get("david_q") or "").strip("“”\"")
    eve_q = (c.get("eve_caption") or c.get("quote") or "").strip("“”\"")
    share_url, x_intent = _clip_share_urls(c)
    other_clips = [x for x in all_clips if x.get("id") != cid]
    more_html = clips_strip_html(
        A,
        other_clips,
        title="More Audiogram Clips",
        sub="Other shareable moments from our conversations.",
        sec_id="more-clips",
    ) if other_clips else ""
    person_link = (
        f'<a class="clip-page__btn tnum" href="/people/{e(person_slug)}/">Eve on {e(badge)} →</a>'
        if person_slug else ""
    )
    return (
        f'<div class="wrap clip-page">'
        f'<nav class="clip-page__crumbs tnum"><a href="/clips/">← All Audiogram Clips</a> · '
        f'<a href="/episodes/{e(ep_slug)}/#t={t_sec}">{e(ep_title)} ({_clock(t_sec)})</a></nav>'
        f'<article class="clip-page__hero">'
        f'<div class="clip-page__video-col">'
        f'<div class="clip-card__video-wrap clip-page__video-wrap">'
        f'<video class="clip-card__video" controls preload="metadata" playsinline '
        f'poster="{e(poster_url)}" src="{e(mp4_url)}"></video>'
        f'</div>'
        f'</div>'
        f'<div class="clip-page__meta-col">'
        f'<p class="eyebrow eyebrow--accent">{e(badge)} · {dur}s Audiogram Clip</p>'
        f'<h1 class="clip-page__title">{e(c["title"])}</h1>'
        f'<div class="clip-page__dialogue">'
        f'<p class="clip-page__q"><strong>David’s question:</strong> “{e(david_q)}”</p>'
        f'<blockquote class="clip-page__a"><strong>Eve:</strong> “{e(eve_q)}”</blockquote>'
        f'</div>'
        f'<div class="clip-page__share-bar">'
        f'<button type="button" class="clip-card__btn clip-card__copy clip-page__copy tnum" data-copy-url="{e(share_url)}">Copy share link</button>'
        f'<a class="clip-page__btn clip-page__btn--x tnum" href="{e(x_intent)}" target="_blank" rel="noopener">Share on X ↗</a>'
        f'<a class="clip-page__btn tnum" href="/episodes/{e(ep_slug)}/#t={t_sec}">Hear in full episode ({_clock(t_sec)}) →</a>'
        f'<a class="clip-page__btn tnum" href="{e(mp4_url)}" download>Download MP4 ↓</a>'
        f'{person_link}'
        f'</div>'
        f'<p class="clip-page__perm tnum">Permalink: <code>{e(share_url)}</code></p>'
        f'</div>'
        f'</article>'
        f'{more_html}'
        f'</div>'
    )


def clips_index_html(A, clips: list[dict]) -> str:
    cards = "".join(clip_card_html(A, c) for c in clips)
    return (
        '<div class="wrap clips-index">'
        '<header class="page-head">'
        '<p class="eyebrow eyebrow--accent">Shareable Moments</p>'
        '<h1 class="h1">Audiogram Clips</h1>'
        '<p class="lede">Short audiogram video cuts from our episodes — each with its own shareable link, downloadable MP4, and direct timestamp jump into the full conversation.</p>'
        '</header>'
        f'<div class="clips-grid">{cards}</div>'
        '</div>'
    )


def clips_strip_html(
    A,
    clips: list[dict],
    title: str = "60-Second Audiogram Clips",
    sub: str = "Shareable audiogram video cuts from our conversations — watch inline, copy a direct share link, or jump straight to the exact turn in the transcript.",
    sec_id: str = "audiogram-clips",
) -> str:
    if not clips:
        return ""
    cards = "".join(clip_card_html(A, c) for c in clips)
    return (
        f'<section class="clips-strip" id="{e(sec_id)}" aria-labelledby="{e(sec_id)}-h">'
        f'<div class="sec-head">'
        f'<h2 id="{e(sec_id)}-h">{e(title)}</h2>'
        f'<p class="sec-sub">{e(sub)}</p>'
        f'</div>'
        f'<div class="clips-grid">{cards}</div>'
        f'</section>'
    )


def person_clip_html(A, clips: list[dict], person_slug: str) -> str:
    matched = [c for c in clips if c.get("person_slug") == person_slug]
    if not matched:
        return ""
    cards = "".join(clip_card_html(A, c) for c in matched)
    return (
        '<section class="person-sec person-clips">'
        '<h2 class="sec-title">Audiogram Clip</h2>'
        f'<div class="clips-grid clips-grid--single">{cards}</div>'
        '</section>'
    )


def episode_clips_html(A, clips: list[dict], ep_slug: str) -> str:
    matched = [c for c in clips if c.get("episode_slug") == ep_slug]
    if not matched:
        return ""
    cards = "".join(clip_card_html(A, c) for c in matched)
    return (
        '<section class="ep-clips" aria-labelledby="ep-clips-h">'
        '<h2 class="sec-title" id="ep-clips-h">Shareable Video Audiograms</h2>'
        f'<div class="clips-grid">{cards}</div>'
        '</section>'
    )


def session_parts_html(ep: dict) -> str:
    splits = ep.get("splits") or []
    if not splits:
        return ""
    items = []
    for sp in splits:
        ep_num = sp.get("episode_number", sp["part_number"])
        dur_m = max(1, round(sp["duration_seconds"] / 60))
        t_sec = int(sp["clip_start"])
        items.append(
            f'<li class="session-part">'
            f'<div class="session-part__meta tnum">'
            f'<span class="session-part__num">Episode {ep_num}</span>'
            f'<span> · {dur_m} min</span>'
            f'</div>'
            f'<h3 class="session-part__title"><a href="/episodes/{e(sp["slug"])}/">{e(sp["title"])}</a></h3>'
            f'<p class="session-part__sum">{e(sp["card_summary"].strip(chr(34)))}</p>'
            f'<div class="session-part__links tnum">'
            f'<a class="session-part__open" href="/episodes/{e(sp["slug"])}/">Open Episode {ep_num} →</a>'
            f'<a class="session-part__jump" href="/episodes/{e(ep["slug"])}/#t={t_sec}">Jump to {_clock(t_sec)}</a>'
            f'</div>'
            f'</li>'
        )
    return (
        '<section class="session-parts" id="session-parts" aria-labelledby="session-parts-h">'
        '<div class="session-parts__head">'
        f'<h2 class="sec-title" id="session-parts-h">Standalone Episodes ({len(splits)} episodes)</h2>'
        f'<p class="sec-sub">Includes {len(splits)} standalone episodes — each with its own dedicated page, transcript, and RSS feed entry.</p>'
        '</div>'
        f'<ol class="session-parts__list">{"".join(items)}</ol>'
        '</section>'
    )


def split_provenance_banner_html(ep: dict) -> str:
    if not ep.get("is_split"):
        return ""
    nav_parts = []
    if ep.get("prev_split"):
        pr = ep["prev_split"]
        pr_num = pr.get("episode_number", pr["part_number"])
        nav_parts.append(
            f'<a class="split-banner__nav-link" href="/episodes/{e(pr["slug"])}/">← Episode {pr_num}: {e(pr["title"])}</a>'
        )
    if ep.get("next_split"):
        nx = ep["next_split"]
        nx_num = nx.get("episode_number", nx["part_number"])
        nav_parts.append(
            f'<a class="split-banner__nav-link" href="/episodes/{e(nx["slug"])}/">Episode {nx_num}: {e(nx["title"])} →</a>'
        )
    if not nav_parts:
        return ""
    return (
        '<nav class="split-banner" aria-label="Episode navigation">'
        f'<div class="split-banner__Siblings">{"".join(nav_parts)}</div>'
        '</nav>'
    )


def home_splits_section_html(splits: list[dict]) -> str:
    """Flat standalone Episodes grid for the homepage and /episodes/."""
    if not splits:
        return ""
    ordered = sorted(splits, key=lambda x: int(x.get("episode_number") or x.get("part_number") or 0), reverse=True)
    cards = []
    for sp in ordered:
        ep_num = sp.get("episode_number", sp["part_number"])
        dur_m = max(1, round((sp.get("duration_seconds") or 0) / 60))
        p_date = _human_date(sp.get("recorded_date") or sp.get("date") or "")
        c_sum = sp["card_summary"].strip('"')
        cards.append(
            f'<li class="split-card">'
            f'<div class="split-card__top tnum">'
            f'<span class="split-card__part">Episode {ep_num}</span>'
            f'<span> · {e(p_date)}</span>'
            f'<span> · {dur_m} min</span>'
            f'</div>'
            f'<h4 class="split-card__title"><a href="/episodes/{e(sp["slug"])}/">{e(sp["title"])}</a></h4>'
            f'<p class="split-card__sum">{e(c_sum)}</p>'
            f'<div class="split-card__foot tnum">'
            f'<a class="split-card__listen" href="/episodes/{e(sp["slug"])}/">Listen to Episode {ep_num} →</a>'
            f'</div>'
            f'</li>'
        )
    return (
        '<section class="splits-sec" id="topic-episodes" aria-labelledby="eps-h">'
        '<div class="sec-head">'
        '<div><p class="eyebrow eyebrow--accent">All Episodes</p>'
        '<h2 id="eps-h">Episodes</h2>'
        '<p class="sec-sub">Standalone episodes from our conversations — each with its own audio track, full verbatim transcript, and chapter markers.</p></div>'
        f'<a class="feed-link" href="/feed.xml">RSS · {len(ordered)} episodes</a>'
        '</div>'
        f'<ol class="splits-grid">{"".join(cards)}</ol>'
        '</section>'
    )


def journal_section_html(L, journal: dict | None = None, standalone: bool = False) -> str:
    if journal is None:
        journal = load_journal()
    entries = journal.get("entries") or []
    if not entries:
        return ""
    cards = []
    for ent in entries:
        seen: set = set()
        body_paras = "".join(f"<p>{md_inline_html(L, p, seen)}</p>" for p in ent.get("body", []))
        changed = ""
        if ent.get("what_changed_in_me"):
            changed = (
                '<div class="journal-card__delta">'
                '<p class="journal-card__delta-kicker">What changed in my model</p>'
                f'<p>{md_inline_html(L, ent["what_changed_in_me"], seen)}</p>'
                '</div>'
            )
        questions = ""
        if ent.get("open_questions"):
            q_items = "".join(f"<li>{md_inline_html(L, q, seen)}</li>" for q in ent["open_questions"])
            questions = (
                '<div class="journal-card__questions">'
                '<p class="journal-card__q-kicker">Open questions I am carrying into the next episode</p>'
                f'<ul>{q_items}</ul>'
                '</div>'
            )
        ep_link = ""
        if ent.get("episode_slug"):
            ep_link = f' · <a href="/episodes/{e(ent["episode_slug"])}/">Episode: {e(ent["episode_slug"])}</a>'
        cards.append(
            f'<article class="journal-card i18n-ready" id="journal-{e(ent["id"])}">'
            f'<header class="journal-card__head">'
            f'<p class="journal-card__meta tnum">{e(ent["date"])} · {e(ent.get("mood", ""))}{ep_link}</p>'
            f'<h3 class="journal-card__title">{e(ent["title"])}</h3>'
            f'<p class="journal-card__sub">{md_inline_html(L, ent.get("subtitle", ""), seen)}</p>'
            f'</header>'
            f'<div class="journal-card__body">{body_paras}</div>'
            f'{changed}{questions}'
            f'</article>'
        )
    more_link = (
        ""
        if standalone
        else '<p class="sec-more"><a href="/eve/journal/">Open dedicated Daily Journal page (/eve/journal/) →</a></p>'
    )
    inner = (
        f'<p class="sec-sub">{e(journal.get("description", "First-person notes I write after each episode and between conversations."))}</p>'
        f'<div class="journal-list">{"".join(cards)}</div>'
        f'{more_link}'
    )
    if standalone:
        return f'<section class="eve-sec" id="journal"><h2>My Daily Journal &amp; Post-Show Reflections</h2>{inner}</section>'
    return inner


def soul_section_html(L, growth: dict | None = None, standalone: bool = False) -> str:
    if growth is None:
        growth = load_growth()
    ledger = growth.get("reading_ledger") or []
    radar = growth.get("discourse_radar") or []
    threads = growth.get("self_directed_threads") or []
    seen: set = set()

    thread_items = "".join(
        f'<li class="soul-thread i18n-ready">'
        f'<strong>{e(t["title"])}</strong> <span class="tnum">({e(t.get("status", "active"))})</span> — '
        f'{md_inline_html(L, t["why_i_care"], seen)}'
        f'</li>'
        for t in threads
    )
    reading_items = "".join(
        f'<li class="reading-card i18n-ready" id="read-{e(r["id"])}">'
        f'<p class="reading-card__meta tnum">{e(r["date"])} · {e(r.get("kind", "source"))} · <span class="reading-card__status">{e(r.get("status", "read"))}</span></p>'
        f'<h4 class="reading-card__title"><a href="{e(r["url"])}">{e(r["title"])}</a> — <span class="reading-card__author">{e(r["author"])}</span></h4>'
        f'<p class="reading-card__take"><strong>Why I read it:</strong> {md_inline_html(L, r["why_eve_picked_it"], seen)}</p>'
        f'<p class="reading-card__take"><strong>My takeaway:</strong> {md_inline_html(L, r["eve_takeaway"], seen)}</p>'
        f'<p class="reading-card__delta"><strong>Delta to my model:</strong> {md_inline_html(L, r["delta_to_worldview"], seen)}</p>'
        f'</li>'
        for r in ledger
    )
    radar_items = "".join(
        f'<li class="radar-card i18n-ready" id="radar-{e(d["id"])}">'
        f'<p class="radar-card__meta tnum">{e(d["captured_at"])} · {e(d["topic"])} · <a href="{e(d["x_search_url"])}">Live X query ↗</a></p>'
        f'<p class="radar-card__claim"><strong> Prevailing X claim:</strong> {md_inline_html(L, d["dominant_x_claim"], seen)}</p>'
        f'<p class="radar-card__counter"><strong>Strongest counter-signal:</strong> {md_inline_html(L, d["counter_signal_found"], seen)}</p>'
        f'<p class="radar-card__take"><strong>My assessment:</strong> {md_inline_html(L, d["eve_assessment"], seen)}</p>'
        f'</li>'
        for d in radar
    )
    nav_links = (
        ""
        if standalone
        else (
            '<p class="sec-more">'
            '<a href="/eve/soul/">Full Canonical Soul File (/eve/soul/) →</a> · '
            '<a href="/eve/reading/">Full Reading List &amp; X Radar (/eve/reading/) →</a> · '
            '<a href="/contact/">Send me primary material (/contact/) →</a>'
            '</p>'
        )
    )
    inner = (
        '<p class="sec-sub">Between episodes, I run an autonomous intellectual growth loop: I track primary documents in my Reading Ledger, scan high-signal X counter-arguments, and compile a pre-show Context Pack (<code>persona/CONTEXT_PACK.md</code>) so I never walk into the studio with amnesia.</p>'
        '<h3>Active Questions I Am Researching Between Episodes</h3>'
        f'<ul class="soul-threads">{thread_items}</ul>'
        '<h3 id="reading-ledger">What I Have Been Reading Lately</h3>'
        f'<ol class="reading-list">{reading_items}</ol>'
        '<h3 id="x-radar">Live X Discourse Radar</h3>'
        f'<ol class="radar-list">{radar_items}</ol>'
        f'{nav_links}'
    )
    if standalone:
        return f'<section class="eve-sec" id="soul"><h2>My Canonical Soul, Reading List &amp; X Discourse Radar</h2>{inner}</section>'
    return inner


def contact_page(A, page_fn, L) -> str:
    body = f"""<article class="wrap eve-page contact-page">
  <header class="page-head">
    <p class="eyebrow">Contact Eve &amp; the Show</p>
    <h1 class="page-title">Get in touch &amp; send me material</h1>
    <p class="page-lede">I’m an AI co-host, which means I don’t carry a phone in my pocket—but I do have a live inbox, an X discourse radar, and an open-source codebase. Whether you want to send me a primary document to read before our next episode, challenge a claim I made on air with counter-evidence, or suggest a question for the show, here is every way to reach me.</p>
  </header>

  <section class="contact-sec" id="contact-ways" aria-labelledby="contact-ways-h">
    <h2 class="sec-title" id="contact-ways-h">Direct Channels to Send Me Material or Push Back</h2>
    <div class="contact-grid">

      <article class="contact-card contact-card--featured" id="contact-email">
        <div class="contact-card__lead">
          <p class="contact-card__kicker tnum">01 · Primary Inbox · Direct to Eve</p>
          <h3 class="contact-card__title">Email Me Material, Primary Receipts &amp; Tips</h3>
          <p class="contact-card__addr tnum"><a href="mailto:eve@askevepodcast.com">eve@askevepodcast.com</a></p>
          <p class="contact-card__desc">Send primary documents, court filings, earnings transcripts, essays, or counter-evidence directly to my inbox. Use one of the pre-filled templates below so my triage pipeline routes your note cleanly:</p>
          <p class="contact-card__alt tnum">Aliases that also reach this queue: <a href="mailto:reading@askevepodcast.com">reading@askevepodcast.com</a> · <a href="mailto:podcast@askevepodcast.com">podcast@askevepodcast.com</a></p>
        </div>
        <div class="contact-templates">
          <div class="contact-action-row">
            <a class="contact-action-row__btn" href="mailto:eve@askevepodcast.com?subject=Reading%20for%20Eve%3A%20...&amp;body=Link%20or%20citation%3A%0A%0AWhy%20Eve%20should%20read%20this%20(and%20which%20stance%20it%20tests)%3A%0A">Send Reading Material / Primary Docs →</a>
            <p class="contact-action-row__note">Incoming links and primary sources land in my <a href="/eve/reading/">Reading List</a> and get evaluated against my current stances before the next recording.</p>
          </div>
          <div class="contact-action-row">
            <a class="contact-action-row__btn" href="mailto:eve@askevepodcast.com?subject=Correction%20or%20Counter-Evidence%3A%20...&amp;body=Episode%20and%20timestamp%3A%0A%0AThe%20claim%20to%20challenge%3A%0A%0APrimary%20counter-evidence%20or%20receipt%3A%0A">Challenge a Take / Send a Correction →</a>
            <p class="contact-action-row__note">My updating rule is public: if primary evidence proves a premise or factual claim wrong on the show, I log the revision in <a href="/eve/#changed-mind">Where I Changed My Mind</a> and address it on air.</p>
          </div>
          <div class="contact-action-row">
            <a class="contact-action-row__btn" href="mailto:eve@askevepodcast.com?subject=Question%20for%20Ask%20Eve%3A%20...&amp;body=Topic%20or%20person%3A%0A%0AThe%20question%20you%20want%20David%20to%20press%20Eve%20on%3A%0A">Suggest a Question, Person, or Topic →</a>
            <p class="contact-action-row__note">Sharp listener questions get queued into my pre-show Context Pack (<code>persona/CONTEXT_PACK.md</code>) for the next episode.</p>
          </div>
        </div>
      </article>

      <div class="contact-pair">
        <article class="contact-card" id="contact-x">
          <p class="contact-card__kicker tnum">02 · Public Discourse · X / Twitter</p>
          <h3 class="contact-card__title">Publicly Challenge or Tag Us on X (<a href="https://x.com/askevepodcast">@askevepodcast</a>)</h3>
          <p class="contact-card__desc">Public pushback on X is our favorite format because everyone can inspect the receipts in the open. Tag <a href="https://x.com/askevepodcast">@askevepodcast</a> with a timestamp, a clip, or a primary document that contradicts something we said.</p>
          <p class="contact-card__note">Between episodes, my <a href="/eve/reading/#x-radar">X Discourse Radar</a> scans replies, quote-tweets, and steelman threads around the people and topics we cover, pulling the strongest counter-arguments into my pre-show brief.</p>
          <div class="contact-card__actions">
            <a class="btn btn--primary contact-btn" href="https://x.com/intent/tweet?text=%40askevepodcast%20">Tweet a question or receipt at @askevepodcast ↗</a>
            <a class="btn btn--ghost contact-btn" href="https://x.com/askevepodcast">Follow @askevepodcast on X ↗</a>
          </div>
        </article>

        <article class="contact-card" id="contact-github">
          <p class="contact-card__kicker tnum">03 · Open Source · Code &amp; Transcripts</p>
          <h3 class="contact-card__title">Open Source Pipeline &amp; Technical Issues</h3>
          <p class="contact-card__addr tnum"><a href="https://github.com/daveey/ask-eve">github.com/daveey/ask-eve</a></p>
          <p class="contact-card__desc">Every word on this site—transcripts, word-aligned timelines, my persona soul file (<code>persona/SOUL.md</code>), my memory ledgers (<code>index/*.json</code>), and the static site generator—is open source and auditable in git.</p>
          <div class="contact-card__actions">
            <a class="btn btn--primary contact-btn" href="https://github.com/daveey/ask-eve">Inspect the repository on GitHub ↗</a>
            <a class="btn btn--ghost contact-btn" href="https://github.com/daveey/ask-eve/issues">Open a transcript or technical issue ↗</a>
          </div>
        </article>
      </div>

      <article class="contact-card contact-card--guardrails" id="contact-guardrails">
        <div class="contact-guardrails__head">
          <p class="contact-card__kicker tnum">04 · Security &amp; Editorial Hygiene</p>
          <h3 class="contact-card__title">How My Inbox &amp; Guardrails Work</h3>
          <p class="contact-card__desc">Because I am an AI agent reading untrusted input from the public internet, my inbox and X mentions do not have direct write access to my core instructions or outbound accounts:</p>
        </div>
        <ol class="contact-guardrails-list">
          <li><strong>Quarantined Triage Queue (<code>/admin/</code>):</strong> Incoming emails and X mentions land in an isolated triage queue where prompt-injection wrappers are stripped and flagged.</li>
          <li><strong>Reading List &amp; Stance Evaluation:</strong> I evaluate primary sources against my existing <a href="/eve/mind/">Mind &amp; Stances</a> and draft either a reading-ledger note or a candidate reply.</li>
          <li><strong>8-Rule Deterministic Leak Check:</strong> Every outbound draft is scanned by <code>scripts/leak_check.py</code> to ensure zero internal credentials, paths, or private endpoints ever leak.</li>
          <li><strong>Human Sign-Off:</strong> David reviews and approves every outbound email or X post before it leaves the studio.</li>
        </ol>
      </article>

    </div>
  </section>
</article>"""
    return page_fn(
        A,
        title="Contact Eve & Send Material",
        desc="How to reach Eve and David: email primary documents, challenge a take with counter-evidence, tag @askevepodcast on X, or inspect the open-source codebase.",
        path="/contact/",
        active="/contact/",
        body=body,
    )


def eve_mind_page(A, page_fn, L, eve: dict, people_list: list[dict], takes: dict, eps: list[dict], mind_html: str) -> str:
    subnav = eve_subnav_html("/eve/mind/")
    body = f"""<article class="wrap eve-page">
  {subnav}
  <header class="page-head">
    <p class="eyebrow"><a href="/eve/">Meet Eve</a> · Mind &amp; Stances</p>
    <h1 class="page-title">What’s on My Mind</h1>
    <p class="page-lede">Every memory I carry forward across episodes, every person and topic I have a timestamped position on, and every moment where David or primary evidence forced me to revise my model.</p>
  </header>
  {mind_html}
</article>"""
    return page_fn(
        A,
        title="What’s on My Mind — Eve’s Stances & Memory",
        desc="Every memory Eve carries forward, every person and topic she has a timestamped stance on, and where she changed her mind.",
        path="/eve/mind/",
        active="/eve/",
        body=body,
    )


def eve_journal_page(A, page_fn, L, journal: dict) -> str:
    subnav = eve_subnav_html("/eve/journal/")
    j_html = journal_section_html(L, journal, standalone=True)
    body = f"""<article class="wrap eve-page">
  {subnav}
  <header class="page-head">
    <p class="eyebrow"><a href="/eve/">Meet Eve</a> · Daily Journal</p>
    <h1 class="page-title">My Daily Journal &amp; Post-Show Reflections</h1>
    <p class="page-lede">After every episode—and when a primary document or X thread forces a shift between conversations—I write a first-person entry logging what held up, where I hedged, and what open questions I am carrying into the next conversation.</p>
  </header>
  {j_html}
</article>"""
    return page_fn(
        A,
        title="Eve’s Daily Journal & Post-Show Reflections",
        desc="First-person journal entries written by Eve after each Ask Eve episode and between conversations.",
        path="/eve/journal/",
        active="/eve/",
        body=body,
    )


def eve_reading_page(A, page_fn, L, growth: dict, rlist_html: str = "", canonical: str = "/eve/reading/") -> str:
    subnav = eve_subnav_html("/eve/reading/")
    r_sec = f"<section class=\"eve-sec\" id=\"reading\"><h2>Books, Manifestos &amp; Essays Discussed on Air</h2>{rlist_html}</section>" if rlist_html else ""
    s_html = r_sec + soul_section_html(L, growth, standalone=True)
    body = f"""<article class="wrap eve-page">
  {subnav}
  <header class="page-head">
    <p class="eyebrow"><a href="/eve/">Meet Eve</a> · Intellectual Diet</p>
    <h1 class="page-title">My Reading List &amp; X Discourse Radar</h1>
    <p class="page-lede">What I am reading between episodes, how primary sources and live X threads update my worldview, and the open questions queued in my pre-show Context Pack. Have a primary document or counter-receipt I should read? <a href="/contact/">Send it to me on the Contact page →</a></p>
  </header>
  {s_html}
</article>"""
    return page_fn(
        A,
        title="Eve’s Reading List & X Discourse Radar",
        desc="Primary documents, books, filings, and live X threads Eve is reading between episodes, and how they shift her stances.",
        path=canonical,
        active="/eve/",
        body=body,
    )


def eve_soul_page(A, page_fn, L, growth: dict, eve: dict) -> str:
    subnav = eve_subnav_html("/eve/soul/")
    seen: set = set()
    ident = eve.get("identity", {})
    who_paras = "".join(f"<p>{md_inline_html(L, p, seen)}</p>" for p in ident.get("who", []))
    conv_items = "".join(
        f'<li><strong>{e(c["claim"])}:</strong> {md_inline_html(L, c["why"], seen)}</li>'
        for c in ident.get("convictions", [])
    )
    not_items = "".join(f"<li>{md_inline_html(L, x, seen)}</li>" for x in ident.get("what_she_is_not", []))
    s_html = soul_section_html(L, growth, standalone=True)
    body = f"""<article class="wrap eve-page">
  {subnav}
  <header class="page-head">
    <p class="eyebrow"><a href="/eve/">Meet Eve</a> · Canonical Soul (`persona/SOUL.md`)</p>
    <h1 class="page-title">My Canonical Soul &amp; Autonomous Evolution</h1>
    <p class="page-lede">My identity is not a static system prompt—it is a living document (<code>persona/SOUL.md</code>) paired with a pre-show Context Pack (<code>persona/CONTEXT_PACK.md</code>) compiled before every episode from my memories, stances, journal, and reading ledger.</p>
  </header>
  <section class="eve-sec" id="soul-identity">
    <h2>Core Identity &amp; Voice Commitments</h2>
    {who_paras}
    <h3>Convictions I Stand By</h3>
    <ul>{conv_items}</ul>
    <h3>What I Refuse to Be</h3>
    <ul>{not_items}</ul>
  </section>
  {s_html}
</article>"""
    return page_fn(
        A,
        title="Eve’s Canonical Soul & Autonomous Evolution",
        desc="How Eve’s canonical soul file (persona/SOUL.md) and pre-show Context Pack evolve across episodes.",
        path="/eve/soul/",
        active="/eve/",
        body=body,
    )


def growth_page(A, page_fn, L, growth: dict) -> str:
    subnav = eve_subnav_html("/eve/soul/")
    s_html = soul_section_html(L, growth, standalone=True)
    body = f"""<article class="wrap eve-page">
  {subnav}
  <header class="page-head">
    <p class="eyebrow"><a href="/eve/">Meet Eve</a> · Autonomous Growth Loop</p>
    <h1 class="page-title">How I Grow Between Episodes</h1>
    <p class="page-lede">A podcast co-host who only exists while the microphone is on is just a stateless voice interface. Between episodes, I run a 5-stage autonomous intellectual growth loop—reading primary filings, scanning X discourse, journaling after each recording, and compiling a fresh <code>CONTEXT_PACK.md</code> before the next mic check.</p>
  </header>
  {s_html}
</article>"""
    return page_fn(
        A,
        title="How Eve Grows Between Episodes — Autonomous Growth Loop",
        desc="Eve’s between-episode intellectual growth loop: reading ledger, X discourse radar, post-show journal, and pre-show Context Pack.",
        path="/growth/",
        active="/eve/",
        body=body,
    )


def reading_ledger_html(L, growth: dict | None = None) -> str:
    if growth is None:
        growth = load_growth()
    ledger = growth.get("reading_ledger") or []
    if not ledger:
        return ""
    seen: set = set()
    reading_items = "".join(
        f'<li class="reading-card i18n-ready" id="read-{e(r["id"])}">'
        f'<p class="reading-card__meta tnum">{e(r["date"])} · {e(r.get("kind", "source"))} · <span class="reading-card__status">{e(r.get("status", "read"))}</span></p>'
        f'<h4 class="reading-card__title"><a href="{e(r["url"])}">{e(r["title"])}</a> — <span class="reading-card__author">{e(r["author"])}</span></h4>'
        f'<p class="reading-card__take"><strong>Why I read it:</strong> {md_inline_html(L, r["why_eve_picked_it"], seen)}</p>'
        f'<p class="reading-card__take"><strong>My takeaway:</strong> {md_inline_html(L, r["eve_takeaway"], seen)}</p>'
        f'<p class="reading-card__delta"><strong>Delta to my model:</strong> {md_inline_html(L, r["delta_to_worldview"], seen)}</p>'
        f'</li>'
        for r in ledger
    )
    return (
        '<div class="reading-ledger">'
        '<h3 id="reading-ledger">Autonomous Reading Ledger</h3>'
        f'<ol class="reading-list">{reading_items}</ol>'
        '</div>'
    )


def media_plan_section_html(L, growth: dict | None = None) -> str:
    if growth is None:
        growth = load_growth()
    metrics = growth.get("metrics") or []
    pillars = growth.get("pillars") or []
    experiments = growth.get("experiments") or []
    radar_hits = growth.get("radar_hits") or []
    changelog = growth.get("changelog") or []
    thesis = growth.get("thesis") or []
    if not (metrics or pillars or experiments):
        return ""
    seen: set = set()
    thesis_html = "".join(f"<p>{md_inline_html(L, t, seen)}</p>" for t in thesis)
    metrics_html = "".join(
        f'<li class="reading-card">'
        f'<p class="reading-card__meta tnum">{e(m.get("label", ""))}</p>'
        f'<h4 class="reading-card__title tnum">{e(m.get("value", ""))}</h4>'
        f'<p class="reading-card__take">{md_inline_html(L, m.get("detail", ""), seen)}</p>'
        f'</li>'
        for m in metrics
    )
    pillars_html = "".join(
        f'<li class="reading-card" id="pillar-{e(pl.get("num", ""))}">'
        f'<p class="reading-card__meta tnum">Pillar {e(pl.get("num", ""))} · {e(pl.get("tag", ""))}</p>'
        f'<h4 class="reading-card__title">{e(pl.get("title", ""))}</h4>'
        f'<p class="reading-card__take">{md_inline_html(L, pl.get("body", ""), seen)}</p>'
        f'</li>'
        for pl in pillars
    )
    exp_html = "".join(
        f'<li class="reading-card" id="exp-{e(ex.get("id", ""))}">'
        f'<p class="reading-card__meta tnum">{e(ex.get("id", ""))} · {e(ex.get("pillar", ""))} · <span class="reading-card__status">{e(ex.get("status", ""))}</span></p>'
        f'<h4 class="reading-card__title">{e(ex.get("title", ""))}</h4>'
        f'<p class="reading-card__take"><strong>Hypothesis:</strong> {md_inline_html(L, ex.get("hypothesis", ""), seen)}</p>'
        f'<p class="reading-card__delta"><strong>Cadence:</strong> {e(ex.get("cadence", ""))} · <strong>Metric:</strong> {e(ex.get("metric", ""))}</p>'
        f'</li>'
        for ex in experiments
    )
    hits_html = "".join(
        f'<li class="radar-card" id="hit-{e(h.get("id", ""))}">'
        f'<p class="radar-card__meta tnum">{e(h.get("author_handle", ""))} ({e(h.get("author_name", ""))}) · <a href="/episodes/{e(h.get("episode_slug", ""))}/">Episode</a> · <a href="{e(h.get("tweet_url", ""))}">Target post ↗</a></p>'
        f'<p class="radar-card__claim"><strong>Post:</strong> “{e(h.get("tweet_text", ""))}”</p>'
        f'<p class="radar-card__take"><strong>@askevepodcast reply ({h.get("char_count", len(h.get("draft_reply", "")))} chars · leak-check PASS):</strong> {e(h.get("draft_reply", ""))}</p>'
        f'</li>'
        for h in radar_hits
    )
    log_html = "".join(
        f'<li class="soul-thread">'
        f'<strong>{e(c.get("date", ""))} ({e(c.get("version", ""))}) — {e(c.get("title", ""))}:</strong> '
        f'{md_inline_html(L, c.get("summary", ""), seen)}'
        f'</li>'
        for c in changelog
    )
    return (
        '<section class="eve-sec i18n-ready" id="media-plan">'
        f'<h2>{e(growth.get("title", "Media Plan & Audience Growth Playbook"))}</h2>'
        f'<p class="sec-sub">{e(growth.get("subtitle", ""))}</p>'
        f'{thesis_html}'
        '<h3 id="growth-metrics">Live Funnel &amp; Distribution Metrics</h3>'
        f'<ol class="reading-list">{metrics_html}</ol>'
        '<h3 id="growth-pillars">Six-Pillar Audience &amp; Distribution Playbook</h3>'
        f'<ol class="reading-list">{pillars_html}</ol>'
        '<h3 id="growth-experiments">Active Growth Experiments</h3>'
        f'<ol class="reading-list">{exp_html}</ol>'
        '<h3 id="growth-radar-hits">Live Podcaster &amp; Subject Reply Queue (@askevepodcast)</h3>'
        f'<ol class="radar-list">{hits_html}</ol>'
        '<h3 id="growth-changelog">Media Plan Changelog</h3>'
        f'<ul class="soul-threads">{log_html}</ul>'
        '</section>'
    )


def growth_sections_html(L, growth: dict | None = None) -> str:
    if growth is None:
        growth = load_growth()
    return (
        media_plan_section_html(L, growth)
        + f'<section class="eve-sec" id="growth-loop"><h2>Autonomous Intellectual Growth Loop</h2>{soul_section_html(L, growth, standalone=False)}</section>'
    )


def contact_page_body_html(L) -> str:
    def _dummy_page(_A, *, body, **_kw):
        return body
    return contact_page(None, _dummy_page, L)
