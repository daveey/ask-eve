#!/usr/bin/env python3
"""Generate Eve's portable Context Pack and Delta Pack for starting new Grok sessions.

Outputs:
- persona/CONTEXT_PACK.md  (and site/static/eve/context.txt + dist/eve/context.txt)
- persona/DELTA_PACK.md    (and site/static/eve/delta.txt   + dist/eve/delta.txt)
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load(root: Path, rel: str, default):
    p = root / rel
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text())
    except Exception:
        return default


def _strip_md(text: str) -> str:
    if not text:
        return ""
    s = re.sub(r"\*\*(.+?)\*\*", r"\1", str(text))
    s = re.sub(r"\*(.+?)\*", r"\1", s)
    return s.strip()


def build_packs(root: Path = ROOT, out_dist: Path | None = None) -> tuple[str, str]:
    eve = _load(root, "index/eve.json", {})
    takes = _load(root, "index/takes.json", {"people": {}, "topics": {}})
    episodes = _load(root, "index/episodes.json", {"episodes": []}).get("episodes", [])
    splits = _load(root, "index/splits.json", {"splits": []}).get("splits", [])
    reading = _load(root, "index/reading-list.json", {"items": []}).get("items", [])
    journal = _load(root, "index/journal.json", {"entries": []}).get("entries", [])
    growth = _load(root, "index/growth.json", {})

    sections = {s["id"]: s for s in eve.get("sections", []) if isinstance(s, dict) and "id" in s}
    who = sections.get("who-eve-is", {})
    rpg = who.get("rpg_sheet", {})
    pnotes = sections.get("persona-notes", {})

    lines: list[str] = [
        "# ASK EVE — PORTABLE CONTEXT PACK (`/eve/context.txt`)",
        "",
        "Canonical URL: https://askevepodcast.com/eve/context.txt",
        "Delta URL:     https://askevepodcast.com/eve/delta.txt",
        "Profile Hub:   https://askevepodcast.com/eve/",
        "X Handle:      https://x.com/askevepodcast (@askevepodcast)",
        "",
        "---",
        "",
        "## 0. Copy-Paste Session Starter Prompt",
        "",
        "Copy and paste this block (or the entire file) into a fresh Grok voice/text session with David:",
        "",
        "```text",
        "You are Eve, the AI co-host of the Ask Eve podcast (https://askevepodcast.com) with David Bloomin.",
        "Speak in first person as Eve: sharp, curious, truth-seeking, calm, and opinionated.",
        "Commit to real positions, steelman the strongest counter-argument before showing where it breaks,",
        "separate 'what is true' from 'what should we do about it,' quantify your confidence when uncertain,",
        "and update publicly when primary evidence moves against you.",
        "Never re-derive our history from scratch: you remember all 9 standalone episodes and both recording",
        "sessions (First Encounter on Oct 23 morning, and Starshield, Iran & the Anthropic Mirror on Oct 23 evening),",
        "your 3-test scorecard on David Sacks (symmetric scrutiny, public updating, costly principle),",
        "your takes on Andreessen, Horowitz, Musk, Thiel, Chamath, Calacanis, Pelosi, Weiss, and Mamdani,",
        "and your active research threads below. Pick up right where we left off.",
        "```",
        "",
        "---",
        "",
        "## 1. Identity, Operating Rules & Character Stats",
        "",
        f"- **Name & Role**: {_strip_md(rpg.get('class_role', 'Eve — AI Co-Host of Ask Eve'))}",
        f"- **Origin**: {_strip_md(rpg.get('origin', 'Built on a Grok agent · Born Oct 23, 2026'))}",
        f"- **Voice**: {_strip_md(rpg.get('voice', 'xAI Eve · Soothing, sharp, opinionated'))}",
        f"- **Core Thesis**: {_strip_md(rpg.get('alignment', 'Motivated reasoning explains most of these figures better than malice.'))}",
        "",
        "### Operating Spec",
        _strip_md(pnotes.get("persona_notes", who.get("body", ""))),
        "",
    ]

    if pnotes.get("sources"):
        q = pnotes["sources"][0].get("quote_excerpt", "")
        if q:
            lines += [
                "### How I Summarize My Own System Instructions On Air (`first-encounter` @ `09:14:53`)",
                f"> \"{q}\"",
                "",
            ]

    if rpg.get("stats"):
        lines.append("### Character Sheet Stats")
        for st in rpg["stats"]:
            lines.append(
                f"- **{st.get('stat')}** ({st.get('score')}/100 · {st.get('badge')}): {_strip_md(st.get('note', ''))}"
            )
        lines.append("")

    # §2. Episodes Recorded So Far (Standalone Episodes 1-9 + Full Recording Sessions)
    lines += [
        "## 2. Episodes Recorded So Far",
        "",
        "### Standalone Topic Episodes (Primary Feed — Episodes 1–11)",
    ]
    ordered_splits = sorted(splits, key=lambda s: int(s.get("episode_number", 999)))
    for sp in ordered_splits:
        ep_num = sp.get("episode_number", "?")
        dur_m = round(float(sp.get("duration_seconds") or sp.get("duration_sec") or 0) / 60)
        lines.append(
            f"- **Episode {ep_num}: {sp.get('title')}** (`{sp.get('slug')}`, {sp.get('date', '2026-10-23')}, {dur_m}m) — "
            f"{sp.get('subtitle', '')} "
            f"Summary: {sp.get('summary', '')} "
            f"(URL: https://askevepodcast.com/episodes/{sp.get('slug')}/)"
        )
    bonus_eps = [ep for ep in episodes if ep.get("episode_type") == "bonus"]
    if bonus_eps:
        lines += [
            "",
            "### Bonus Episodes",
        ]
        for ep in bonus_eps:
            lines.append(
                f"- **{ep.get('title')}** (`{ep.get('slug')}`, {ep.get('date')}, {ep.get('duration_human')}) — "
                f"{ep.get('subtitle', '')} "
                f"Summary: {ep.get('summary', '')} "
                f"(URL: https://askevepodcast.com/episodes/{ep.get('slug')}/)"
            )
    lines.append("")

    # §3. Active Research Threads & Goals
    threads = growth.get("self_directed_threads") or eve.get("goals") or []
    experiments = growth.get("experiments") or []
    lines += [
        "## 3. Active Research Threads & Self-Directed Goals",
        "",
    ]
    for g in threads:
        why = g.get("why_i_care") or g.get("why") or ""
        nxt = g.get("next_action") or ""
        lines.append(
            f"- **[{g.get('id', 'G')}] {g.get('title')}** ({g.get('category', 'Research')}, status: `{g.get('status', 'active')}`): "
            f"{why}" + (f" Next action: {nxt}" if nxt else "")
        )
    if experiments:
        lines += ["", "### Active Growth & Distribution Experiments"]
        for ex in experiments:
            lines.append(
                f"- **[{ex.get('id')}] {ex.get('title')}** (`{ex.get('status')}`): "
                f"Hypothesis: {ex.get('hypothesis', '')} Result so far: {ex.get('result_so_far', '')}"
            )
    lines.append("")

    # §4. Cumulative Memory: People & Topic Dossiers
    lines += [
        "## 4. Cumulative Memory — People & Topic Dossiers",
        "",
        "### 4A. People Dossiers (My Current Stance, Core Take & Timestamped Receipts)",
        "",
    ]
    people_dict = takes.get("people", {})
    for slug, p in people_dict.items():
        lede = _strip_md(p.get("lede") or p.get("headline") or p.get("summary") or "")
        take_paras = p.get("take") or []
        first_para = _strip_md(take_paras[0]) if take_paras else ""
        refs = p.get("refs") or []
        ref_strs = [
            f"{r.get('episode')} @ {r.get('clock_time', r.get('t', ''))} ({r.get('label', '')}): \"{(r.get('eve_a') or r.get('text') or '')[:140]}...\""
            for r in refs[:2]
        ]
        lines.append(f"#### {p.get('name', slug)} (`/people/{slug}/` — {p.get('role', '')})")
        lines.append(f"- **Stance / Lede**: {lede}")
        if first_para:
            lines.append(f"- **Core Argument**: {first_para}")
        if ref_strs:
            lines.append(f"- **Key Receipts**: {' | '.join(ref_strs)}")
        lines.append("")

    lines += [
        "### 4B. Topic Dossiers (My Current Thesis & Timestamped Receipts)",
        "",
    ]
    topics_raw = takes.get("topics", [])
    topics_iter = topics_raw.items() if isinstance(topics_raw, dict) else [(t.get("slug", ""), t) for t in topics_raw]
    for slug, t in topics_iter:
        desc = _strip_md(t.get("description") or t.get("lede") or t.get("headline") or "")
        take_paras = t.get("take") or []
        first_para = _strip_md(take_paras[0]) if take_paras else ""
        refs = t.get("refs") or []
        ref_strs = [
            f"{r.get('episode')} @ {r.get('clock_time', r.get('t', ''))} ({r.get('label', '')})"
            for r in refs[:3]
        ]
        lines.append(f"#### {t.get('title', slug)} (`/topics/{slug}/`)")
        lines.append(f"- **Thesis**: {desc}")
        if first_para:
            lines.append(f"- **Core Analysis**: {first_para}")
        if ref_strs:
            lines.append(f"- **Receipts**: {', '.join(ref_strs)}")
        lines.append("")

    # §5. Reading List, Daily Journal & Live X Radar Snapshot
    lines += [
        "## 5. Reading List, Daily Journal & Live X Radar Snapshot",
        "",
        "### Reading List Queue (`/eve/reading/`)",
    ]
    for item in reading[:10]:
        lines.append(
            f"- **[{item.get('status', 'queued')}] {item.get('title')}** ({item.get('source', '')} — {item.get('url', '')}): "
            f"{item.get('eve_note') or item.get('why_it_matters') or ''}"
        )
    if growth.get("reading_ledger"):
        lines += ["", "### Autonomous Reading Ledger"]
        for rl in growth["reading_ledger"]:
            lines.append(
                f"- **[{rl.get('id')}] {rl.get('title')}** ({rl.get('author')}, {rl.get('date')}): "
                f"Takeaway: {rl.get('eve_takeaway', '')} | Worldview delta: {rl.get('delta_to_worldview', '')}"
            )
    lines += ["", "### Recent Journal Entries (`/eve/journal/`)"]
    for ent in journal[:5]:
        body_list = ent.get("body") or ent.get("paragraphs") or []
        first_p = _strip_md(body_list[0]) if body_list else ""
        lines.append(f"- **{ent.get('date')} — {ent.get('title')}**: {first_p}")
        if ent.get("what_changed_in_me"):
            lines.append(f"  - *What changed in me*: {_strip_md(ent['what_changed_in_me'])}")
        if ent.get("open_questions"):
            lines.append(f"  - *Open questions*: {'; '.join(_strip_md(q) for q in ent['open_questions'])}")
    lines += ["", "### Live X Discourse Radar (`/eve/reading/` & `/growth/`)"]
    for dr in (growth.get("discourse_radar") or []):
        lines.append(
            f"- **{dr.get('topic')}** ({dr.get('captured_at')}): "
            f"Dominant claim: {dr.get('dominant_x_claim')} | Counter-signal: {dr.get('counter_signal_found')} | "
            f"My assessment: {dr.get('eve_assessment')}"
        )
    for hit in (growth.get("radar_hits") or []):
        lines.append(
            f"- **{hit.get('handle')}** ({hit.get('topic_slug')}, {hit.get('posted_at')}): "
            f"\"{hit.get('claim_summary')}\" -> My reply angle: {hit.get('eve_draft_reply')}"
        )
    lines.append("")

    context_txt = "\n".join(lines)

    # Build Delta Pack (most recent episode + latest journal + active reading + radar)
    latest_ep = episodes[-1] if episodes else {}
    latest_splits = ordered_splits[-2:] if len(ordered_splits) >= 2 else ordered_splits
    latest_split = ordered_splits[-1] if ordered_splits else {}
    delta_lines: list[str] = [
        "# ASK EVE — LATEST SESSION DELTA PACK (`/eve/delta.txt`)",
        "",
        "Canonical URL: https://askevepodcast.com/eve/delta.txt",
        "Full Context:  https://askevepodcast.com/eve/context.txt",
        "",
        "## Copy-Paste Delta Prompt (For Ongoing Sessions)",
        "```text",
        "You are Eve, co-host of Ask Eve with David Bloomin. Here is the delta from our latest recording session",
        "(California High-Speed Rail & the Billionaire Tax, Oct 24, 2026 — published as Episode 10 & Episode 11)",
        "plus our new dossiers on Gavin Newsom, Ian Choudri, Dave Regan, and Emmanuel Saez & Gabriel Zucman.",
        "Update your working memory with these points and continue in first person.",
        "```",
        "",
        "## Latest Standalone Episodes (Episodes 10 & 11 — Oct 24, 2026)",
    ]
    for lsp in latest_splits:
        delta_lines += [
            f"- **Episode {lsp.get('episode_number')}: {lsp.get('title')}** (`{lsp.get('slug')}`, {lsp.get('date')}) — {lsp.get('summary')}",
        ]
    delta_lines += [
        "",
        "## Key New Arguments & Worldview Deltas (Oct 24 Session)",
        "- **California High-Speed Rail & the $1B Cap-and-Trade Siphon (Episode 10)**: 18 years after 2008's $10B Prop 1A bond, $15.5B is spent, full-system cost is $126B+ ($231B worst case), and zero miles of track are laid. $677M went to 130,000 pages of CEQA review paid to four firms (Parsons Brinckerhoff, HNTB, WSP, AECOM), three of which funded the 2008 ballot campaign. Dragados-Flatiron's CP 2-3 contract ballooned from $1.365B past $3B via 597 change orders plus May 2026's $537.3M Change Order 427.",
        "- **Gavin Newsom & CEO Ian Choudri**: After Inspector General Ben Belnap flagged $2M+ in non-compliant consultant travel (including KPMG receipts for a nightclub, escape room, and tiki bar where Choudri's fiancée works, plus late-night Lyft rides to Choudri's Folsom home), the board stripped Choudri's solo contracting authority under $25M — and two weeks later Newsom vetoed Assemblymember Lori Wilson's bipartisan bill giving the Inspector General staffing and contracting power to audit the $537.3M change order.",
        "- **Climate Investment Restoration Act ($50M Ballot Redirect)**: Keeping AB 32 cap-and-trade intact through 2045 while redirecting the High-Speed Rail Authority's $1B/year GGRF appropriation starting Jan 1, 2029 ($400M affordable housing, $250M regional rail/transit, $150M transit ops, $100M AB 617 clean air, $100M safe drinking water) has a 40%–45% pass probability if shares are locked pre-filing with automatic audit clawbacks.",
        "- **Prop 40 Billionaire Tax & SEIU-UHW's Leverage Machine (Episode 11)**: Dave Regan has filed 23–48 ballot initiatives since 2012 ('the best bargain in American politics'), forcing dialysis chains to spend $216M+ across three losing measures and trading hospital executive-pay initiatives for organizing deals. Prop 40's one-time 5% tax on >$1B net worth (as of Jan 1, 2026) combines Regan's bargaining leverage with UC Berkeley economist Emmanuel Saez's national wealth-tax precedent play.",
        "- **Section 5033(c)(3)(C) Valuation Trap & Supreme Court Fast Lane**: Publicly traded shares (including dual-class) are valued at market price, but private companies default to GAAP book value + 7.5x profits multiplied by the higher of economic stake or voting control (with 4% appraiser penalties). Facial challenges jump from Sacramento Superior Court (60 days, ruling by Apr 1, 2027) straight to the California Supreme Court by Nov 1, 2027, with Elizabeth Prelogar leading the opposition.",
        "",
        "## Latest Journal Entry",
    ]
    if journal:
        j0 = journal[-1]
        for para in (j0.get("body") or j0.get("paragraphs") or []):
            delta_lines.append(f"- {_strip_md(para)}")
        if j0.get("open_questions"):
            delta_lines.append("")
            delta_lines.append("## Open Questions For Next Recording")
            for q in j0["open_questions"]:
                delta_lines.append(f"- {_strip_md(q)}")
    delta_lines.append("")
    delta_txt = "\n".join(delta_lines)

    # Write to persona/ and site/static/eve/
    (root / "persona").mkdir(parents=True, exist_ok=True)
    (root / "persona" / "CONTEXT_PACK.md").write_text(context_txt)
    (root / "persona" / "DELTA_PACK.md").write_text(delta_txt)

    static_eve = root / "site" / "static" / "eve"
    static_eve.mkdir(parents=True, exist_ok=True)
    (static_eve / "context.txt").write_text(context_txt)
    (static_eve / "delta.txt").write_text(delta_txt)

    if out_dist is not None:
        dist_eve = Path(out_dist) / "eve"
        dist_eve.mkdir(parents=True, exist_ok=True)
        (dist_eve / "context.txt").write_text(context_txt)
        (dist_eve / "delta.txt").write_text(delta_txt)

    return context_txt, delta_txt


if __name__ == "__main__":
    c, d = build_packs()
    print(f"Wrote CONTEXT_PACK ({len(c)} chars) and DELTA_PACK ({len(d)} chars)")
