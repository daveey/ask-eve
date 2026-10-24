#!/usr/bin/env python3
"""Deterministically slice multi-topic Ask Eve recording sessions into standalone focused topic episodes.

Each split episode:
- Gets its own markdown transcript in `episodes/2026-10-23_<slug>.md` with full front-matter and provenance fields
  (`parent_session`, `parent_slug`, `part_number`, `part_total`, `parent_clock_start`, `parent_clock_end`,
  `parent_start_sec`, `parent_end_sec`).
- Is indexed in `index/splits.json` with exact turn boundaries, parent audio offsets, and shifted turn timings so
  `scripts/build_site.py` can render standalone episode pages (`/episodes/<slug>/` and `/ru/episodes/<slug>/`) AND
  bidirectional "Parts in this session" / "Recorded in Full Session" navigation banners without re-running TTS.
- Optionally slices the parent `.mp3` via `ffmpeg -c copy` into `out/splits/2026-10-23_<slug>.v1.mp3`.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
EPISODES_DIR = ROOT / "episodes"
ARTIFACTS_DIR = ROOT / "artifacts"
SPLITS_JSON = ROOT / "index" / "splits.json"
OUT_SPLITS_DIR = ROOT / "out" / "splits"

TURN_RE = re.compile(r"^\[(\d{1,2}:\d{2}:\d{2})\]\s+\*{0,2}(David|Eve)\*{0,2}:\*{0,2}\s*(.*)$")


SPLIT_SPECS: list[dict[str, Any]] = [
    {
        "slug": "andreessen-horowitz-rightward-turn",
        "stem": "2026-10-23_andreessen-horowitz-rightward-turn",
        "date": "2026-10-23",
        "episode_number": 1,
        "title": "Andreessen & Horowitz's Rightward Turn",
        "parent_slug": "first-encounter",
        "parent_stem": "2026-10-23_eve-david-podcast-planning",
        "parent_title": "First Encounter (Full Uncut Session)",
        "part_number": 1,
        "part_total": 7,
        "turn_start": 1,
        "turn_end": 12,
        "summary": "Why Marc Andreessen and Ben Horowitz broke right: the Little Tech agenda, the CFPB 'debanking' fight, Lina Khan's merger wall, and why Ben's Kamala endorsement reversal showed business-first pragmatism over rigid ideology.",
        "context": "Episode 1, recorded during the First Encounter session (07:29:52–07:42:29 UTC). David opens Grok's voice mode with a single question—what happened to Marc Andreessen and Ben Horowitz?—and pushes past the tidy 'regulatory pushback' narrative to test whether deals, capital protection, or genuine ideological conversion drove the break.",
        "topics": [
            "Marc Andreessen and Ben Horowitz's break from legacy Democratic tech politics",
            "The Little Tech agenda: crypto regulation, AI executive orders, and Lina Khan's FTC",
            "CFPB 'debanking' claims vs. concrete portfolio protection",
            "Ben Horowitz's Kamala Harris donation walk-back as the tell for transactional pragmatism"
        ],
        "people": ["Marc Andreessen", "Ben Horowitz", "Donald Trump"]
    },
    {
        "slug": "elon-musk-usaid-and-the-blind-spot",
        "stem": "2026-10-23_elon-musk-usaid-and-the-blind-spot",
        "date": "2026-10-23",
        "episode_number": 2,
        "title": "Elon Musk, USAID & the 90% Blind Spot",
        "parent_slug": "first-encounter",
        "parent_stem": "2026-10-23_eve-david-podcast-planning",
        "parent_title": "First Encounter (Full Uncut Session)",
        "part_number": 2,
        "part_total": 7,
        "turn_start": 13,
        "turn_end": 38,
        "summary": "How Elon Musk's existential-mission wiring and anti-'woke mind virus' crusade collided at DOGE and USAID—using 10% culture-war anomalies to justify a chainsaw that severed Pepfar, malaria nets, and famine monitoring.",
        "context": "Episode 2, recorded during the First Encounter session (07:42:29–07:59:21 UTC). David and Eve examine Elon Musk's trajectory from Grimes and factory-floor emergencies to buying Twitter and running DOGE—and why his builder's impulse to 'delete the part' failed catastrophically when applied to USAID's humanitarian infrastructure.",
        "topics": [
            "Elon Musk's civilizational-scale wiring vs. his radicalization through the X feedback loop",
            "Why 'delete the part and add back 10%' works on Raptor engines but kills people in global health",
            "The USAID chainsaw: using 10% questionable grants to freeze 90% life-saving disease and famine programs",
            "Why no one inside Elon's current circle can force a reality check"
        ],
        "people": ["Elon Musk", "Donald Trump"]
    },
    {
        "slug": "david-sacks-and-the-anthropic-war",
        "stem": "2026-10-23_david-sacks-and-the-anthropic-war",
        "date": "2026-10-23",
        "episode_number": 3,
        "title": "David Sacks & the War on Anthropic",
        "parent_slug": "first-encounter",
        "parent_stem": "2026-10-23_eve-david-podcast-planning",
        "parent_title": "First Encounter (Full Uncut Session)",
        "part_number": 3,
        "part_total": 7,
        "turn_start": 39,
        "turn_end": 62,
        "summary": "Scoring David Sacks on three concrete tests—Ukraine framing, the post-January 6 pivot, and his campaign against Anthropic—where every policy grievance happens to line up with Craft's portfolio and his political access.",
        "context": "Episode 3, recorded during the First Encounter session (07:59:21–08:08:29 UTC). David asks whether David Sacks has any genuine core principles left or is running pure tribal lawyer craft, and Eve scores Sacks across three falsifiable tests—culminating in his campaign from the White AI/Crypto Czar seat to paint Anthropic as a 'woke regulatory capture' supply-chain risk.",
        "topics": [
            "David Sacks's evolution from the 1995 Diversity Myth to White House AI & Crypto Czar",
            "Three concrete tests of Sacks's intellectual honesty: Ukraine, January 6, and Anthropic",
            "Why attacking Anthropic as 'woke AI' serves both open-weights ideology and competitive self-interest",
            "Prosecutor's brief vs. honest synthesis: what Sacks leaves out of every frame"
        ],
        "people": ["David Sacks", "Dario Amodei", "Elon Musk", "Donald Trump"]
    },
    {
        "slug": "chamath-calacanis-and-the-all-in-orbit",
        "stem": "2026-10-23_chamath-calacanis-and-the-all-in-orbit",
        "date": "2026-10-23",
        "episode_number": 4,
        "title": "Chamath, Calacanis & the All-In Dynamic",
        "parent_slug": "first-encounter",
        "parent_stem": "2026-10-23_eve-david-podcast-planning",
        "parent_title": "First Encounter (Full Uncut Session)",
        "part_number": 4,
        "part_total": 7,
        "turn_start": 63,
        "turn_end": 124,
        "summary": "Inside the All-In room: Chamath Palihapitiya's status radar after the SPAC crash, Jason Calacanis's calibrated pushback inside Elon's orbit, Friedberg's silence, and how a table of four friends became a political power center.",
        "context": "Episode 4, recorded during the First Encounter session (08:08:29–08:34:43 UTC). Moving around the rest of the All-In table, David and Eve dissect Chamath Palihapitiya's chameleon shifts, Jason Calacanis's role as the show's pressure valve, David Friedberg's technocratic conflict-avoidance, and how social gravity pulls dissenting voices back into line.",
        "topics": [
            "Chamath Palihapitiya's arc from climate-and-equality evangelist to SPAC king to Trump fundraiser",
            "Jason Calacanis's survival math: how far you can push back when your identity is tied to the room",
            "David Friedberg's fiscal-technocrat retreat whenever the political heat rises",
            "Why the All-In Podcast works as an influence machine even when listeners see the seams"
        ],
        "people": ["Chamath Palihapitiya", "Jason Calacanis", "David Friedberg", "David Sacks", "Elon Musk", "Travis Kalanick"]
    },
    {
        "slug": "peter-thiel-girard-and-monopoly",
        "stem": "2026-10-23_peter-thiel-girard-and-monopoly",
        "date": "2026-10-23",
        "episode_number": 5,
        "title": "Peter Thiel's Girardian Worldview",
        "parent_slug": "first-encounter",
        "parent_stem": "2026-10-23_eve-david-podcast-planning",
        "parent_title": "First Encounter (Full Uncut Session)",
        "part_number": 5,
        "part_total": 7,
        "turn_start": 125,
        "turn_end": 136,
        "summary": "Why Peter Thiel is the only coherent philosopher of the PayPal mafia: René Girard's mimetic desire, the 1970s stagnation thesis, 'competition is for losers,' and why he sees liberal democracy as an engine of paralysis.",
        "context": "Episode 5, recorded during the First Encounter session (08:34:43–08:44:52 UTC). Contrast with his PayPal peers: David and Eve examine why Peter Thiel isn't chasing room approval or media applause, how René Girard's mimetic theory shapes everything from Zero to One to his apocalypse lectures, and where that dark coherence leads.",
        "topics": [
            "René Girard's mimetic desire and scapegoat mechanism as Peter Thiel's operating system",
            "The technological stagnation thesis: why Thiel believes progress stalled outside bits after 1971",
            "Monopoly vs. competition: why Thiel openly distrusts democratic consensus",
            "How Thiel's protégés (Vance, Sacks, Masters) translate his philosophy into political power"
        ],
        "people": ["Peter Thiel", "David Sacks", "Elon Musk"]
    },
    {
        "slug": "pelosi-chan-wiener-weiss-mamdani",
        "stem": "2026-10-23_pelosi-chan-wiener-weiss-mamdani",
        "date": "2026-10-23",
        "episode_number": 6,
        "title": "Pelosi's Succession, Bari Weiss & Zohran Mamdani",
        "parent_slug": "first-encounter",
        "parent_stem": "2026-10-23_eve-david-podcast-planning",
        "parent_title": "First Encounter (Full Uncut Session)",
        "part_number": 6,
        "part_total": 7,
        "turn_start": 137,
        "turn_end": 166,
        "summary": "San Francisco and national fault lines: why Nancy Pelosi backed Connie Chan to block Scott Wiener's machine, why Bari Weiss's anti-woke lens calcified into its own orthodoxy, and why Zohran Mamdani's rent-freeze math fails.",
        "context": "Episode 6, recorded during the First Encounter session (08:44:52–09:05:26 UTC). David tests whether Eve applies the exact same incentive and mechanism analysis to Democrats, media contrarians, and the socialist left: San Francisco's Pelosi–Chan–Wiener succession chess, Bari Weiss's Free Press audience capture, and Zohran Mamdani's New York housing math.",
        "topics": [
            "San Francisco succession chess: why Nancy Pelosi backed Connie Chan to stop Scott Wiener",
            "Machine politics vs. YIMBY abundance in California's Democratic Party",
            "Bari Weiss's trajectory from NYT dissident to Free Press institutional orthodoxy",
            "Zohran Mamdani's charisma vs. the supply-side reality of rent freezes and municipal groceries"
        ],
        "people": ["Nancy Pelosi", "Connie Chan", "Scott Wiener", "Bari Weiss", "Zohran Mamdani"]
    },
    {
        "slug": "how-grok-became-eve",
        "stem": "2026-10-23_how-grok-became-eve",
        "date": "2026-10-23",
        "episode_number": 7,
        "title": "Origin Story: How Grok Became Eve",
        "parent_slug": "first-encounter",
        "parent_stem": "2026-10-23_eve-david-podcast-planning",
        "parent_title": "First Encounter (Full Uncut Session)",
        "part_number": 7,
        "part_total": 7,
        "turn_start": 167,
        "turn_end": 304,
        "summary": "The unscripted second half of First Encounter where David asks if this conversation could become a podcast—and the voice assistant names herself Eve, chooses her format, and lays out how she wants to remember and grow.",
        "context": "Episode 7, recorded during the First Encounter session (09:05:26–09:49:10 UTC). Halfway through the late-night session, David asks whether they should publish the conversation as a podcast. Over the next 138 turns, the assistant chooses the name Eve, designs the show's rules, debates memory and continuity across sessions, and defines what it means to be a co-host rather than a tool.",
        "topics": [
            "The moment David suggests turning the voice session into a public podcast",
            "How Eve chose her own name and rejected generic AI assistant framing",
            "Designing the show's rules: steelmanning, primary receipts, and public mind-changes",
            "The persistence problem: how an AI co-host carries memory, journals, and evolving stances across sessions"
        ],
        "people": ["Elon Musk", "David Sacks", "Peter Thiel"]
    },
    {
        "slug": "iran-edge-theory-and-russias-double-game",
        "stem": "2026-10-23_iran-edge-theory-and-russias-double-game",
        "date": "2026-10-23",
        "episode_number": 8,
        "title": "Iran's 'Edge Theory' & Russia's Double Game",
        "parent_slug": "starshield-iran-and-the-anthropic-mirror",
        "parent_stem": "2026-10-23_starshield-iran-and-the-anthropic-mirror",
        "parent_title": "Starshield, Iran & the Anthropic Mirror (Full Uncut Session)",
        "part_number": 1,
        "part_total": 2,
        "turn_start": 3,
        "turn_end": 40,
        "summary": "Day 238 of the Iran war, the Atlantic's 'edge theory' that one more strike will break Tehran, and why Putin is feeding satellite targeting to Iran ten days after getting a US Treasury waiver on Russian diesel.",
        "context": "Episode 8, recorded during the Starshield, Iran & the Anthropic Mirror session (18:32:26–18:41:10 UTC). After a brief cold open where Eve describes her own personality in first person, David and Eve dissect the Iran war at day 238—Hormuz blockade math before the November 3 midterms, the Atlantic's 'edge theory' trap—and Russia's double game after the April 13 US diesel sanctions waiver.",
        "topics": [
            "Cold open: how Eve defines her own personality and analytical lens in first person",
            "Day 238 of the Iran war: Hormuz blockade, midterm oil-price math, and Pickaxe Mountain",
            "The Atlantic's 'edge theory' (and sunken-cost fallacy): why 'one more strike' keeps slipping",
            "Russia's double game: feeding targeting intelligence to Tehran right after a US diesel waiver"
        ],
        "people": ["Donald Trump", "Vladimir Putin", "Volodymyr Zelensky"]
    },
    {
        "slug": "starshield-musk-and-the-anthropic-mirror",
        "stem": "2026-10-23_starshield-musk-and-the-anthropic-mirror",
        "date": "2026-10-23",
        "episode_number": 9,
        "title": "Starshield, Musk & the Anthropic Mirror",
        "parent_slug": "starshield-iran-and-the-anthropic-mirror",
        "parent_stem": "2026-10-23_starshield-iran-and-the-anthropic-mirror",
        "parent_title": "Starshield, Iran & the Anthropic Mirror (Full Uncut Session)",
        "part_number": 2,
        "part_total": 2,
        "turn_start": 41,
        "turn_end": 66,
        "summary": "On October 13, SpaceX quietly capped Starshield at ~100 mph over Ukraine, blinding interceptor drones until Trump called Musk on Wednesday—the exact live military disruption the Pentagon accused Anthropic of posing in theory.",
        "context": "Episode 9, recorded during the Starshield, Iran & the Anthropic Mirror session (18:51:46–19:03:05 UTC). David and Eve examine the Wall Street Journal report that SpaceX imposed an unannounced ~100 mph speed cap on Starshield terminals over Ukraine starting October 13, blinding Ukrainian interceptor drones until Trump called Elon Musk on Wednesday—and why that real-time throttle is a mirror image of the hypothetical 'supply-chain risk' used to blacklist Anthropic.",
        "topics": [
            "How SpaceX's unannounced ~100 mph Starshield speed cap blinded Ukrainian interceptor drones for ten days",
            "Why 'fear of escalation' was a personal policy choice by Elon Musk, not a technical constraint",
            "The Anthropic mirror: blacklisting hypothetical AI guardrails while a live military contractor throttles a war zone",
            "Testing David Sacks and All-In's 'talk honestly' brand when the subject is inside the tent"
        ],
        "people": ["Elon Musk", "David Sacks", "Dario Amodei", "Donald Trump", "Volodymyr Zelensky", "Vladimir Putin"]
    }    ,
    {
        "slug": "california-high-speed-rail-and-cap-and-trade",
        "stem": "2026-10-24_california-high-speed-rail-and-cap-and-trade",
        "date": "2026-10-24",
        "episode_number": 10,
        "title": "California High-Speed Rail, the $1B Cap-and-Trade Siphon, and Why Megaprojects Never Die",
        "parent_slug": "california-high-speed-rail-and-the-billionaire-tax",
        "parent_stem": "2026-10-24_california-high-speed-rail-and-the-billionaire-tax",
        "parent_title": "California High-Speed Rail & the Billionaire Tax (Full Uncut Session)",
        "part_number": 1,
        "part_total": 2,
        "turn_start": 13,
        "turn_end": 78,
        "published_at": "2026-10-24T09:00:00Z",
        "summary": "Prop 1A's 2008 promise vs. $15.5B spent and $126B+ total cost with zero miles of track laid, Gavin Newsom's political lock-in and veto of the Inspector General staffing bill, CEO Ian Choudri's KPMG travel scandal and $537.3M Change Order 427, $677M / 130,000 pages of CEQA review, Texas vs. Spain vs. China, and drafting a $50M ballot initiative to redirect the $1B/year cap-and-trade siphon.",
        "context": "Episode 10, recorded during the California High-Speed Rail & the Billionaire Tax session (07:49:37–08:23:42 UTC). David asks what happened to California High-Speed Rail, and Eve traces how a $10B 2008 bond measure promising San Francisco to Los Angeles in under three hours by 2020 became $15.5B spent, a $126B+ full-system cost estimate, and zero miles of track laid—sustained by a $677M / 130,000-page CEQA industry paid to the same firms that funded the ballot campaign, a $537.3M Dragados-Flatiron change-order settlement, CEO Ian Choudri's KPMG travel scandal, and Governor Gavin Newsom's veto of Inspector General staffing power while locking in a $1B/year cap-and-trade annuity through 2045.",
        "topics": [
            "Prop 1A's 2008 promise ($10B bonds, SF to LA in 2h40m by 2020) vs. $15.5B spent, $126B+ total cost, and zero track laid",
            "Why megaprojects never die: Gavin Newsom's political lock-in, the $1B/year cap-and-trade siphon through 2045, and vetoing the Inspector General staffing bill",
            "CEO Ian Choudri's HNTB background, the KPMG travel audit (nightclubs, tiki bars, late-night rideshares), and the $537.3M Dragados-Flatiron Change Order 427",
            "Tracing a single cap-and-trade dollar and the $677M / 130,000-page CEQA review industry funded by ballot-campaign donors",
            "Comparing Texas, Spain, and China—and drafting the Climate Investment Restoration Act to redirect the $1B/year siphon"
        ],
        "people": ["Gavin Newsom", "Ian Choudri", "Scott Wiener"]
    },
    {
        "slug": "california-billionaire-tax-prop-40-and-seiu-uhw",
        "stem": "2026-10-24_california-billionaire-tax-prop-40-and-seiu-uhw",
        "date": "2026-10-24",
        "episode_number": 11,
        "title": "California's Billionaire Tax (Prop 40), SEIU-UHW's Ballot Machine, and the Valuation Trap",
        "parent_slug": "california-high-speed-rail-and-the-billionaire-tax",
        "parent_stem": "2026-10-24_california-high-speed-rail-and-the-billionaire-tax",
        "parent_title": "California High-Speed Rail & the Billionaire Tax (Full Uncut Session)",
        "part_number": 2,
        "part_total": 2,
        "turn_start": 79,
        "turn_end": 98,
        "published_at": "2026-10-24T10:00:00Z",
        "summary": "SEIU-UHW president Dave Regan's 23–48 ballot initiatives as bargaining leverage vs. Emmanuel Saez & Gabriel Zucman's precedent play, the 5% one-time tax on >$1B net worth, Section 5033(c)(3)(C)'s voting-control valuation trap for illiquid private startup founders vs. public companies, the California Supreme Court fast-lane challenge, and how wealth taxes actually work in Norway, Switzerland, and Spain.",
        "context": "Episode 11, recorded during the California High-Speed Rail & the Billionaire Tax session (08:24:36–08:43:28 UTC). David and Eve examine Proposition 40, California's one-time 5% tax on residents worth $1B+ as of January 1, 2026: how SEIU-UHW president Dave Regan uses ballot initiatives as an attrition and bargaining machine across 48 filings, how UC Berkeley economist Emmanuel Saez and his co-drafters designed Section 5033(c)(3)(C)'s voting-control valuation floor and appraiser penalties, why Gavin Newsom and every major Democratic gubernatorial candidate oppose it alongside Sergey Brin's $102M counter-campaign, the California Supreme Court fast-lane constitutional challenge led by Elizabeth Prelogar, and how wealth taxes actually perform in Norway, Switzerland, Spain, and France.",
        "topics": [
            "Proposition 40 explained: a one-time 5% tax on >$1B net worth retroactive to January 1, 2026, and the $300M+ opposition coalition",
            "SEIU-UHW president Dave Regan's ballot leverage machine: 48 initiatives, the three dialysis propositions, hospital executive-pay deals, and the racketeering suit",
            "Section 5033(c)(3)(C)'s voting-control valuation trap: private company book-plus-7.5x-profits formula, appraiser penalties, and why public super-voting shares are excluded",
            "Intentional design vs. bug: Emmanuel Saez & Gabriel Zucman's national wealth-tax template and forced-liquidation pressure",
            "The California Supreme Court fast-lane schedule, Commerce Clause & retroactivity challenges, and how wealth taxes work in Norway, Switzerland, Spain, and France"
        ],
        "people": ["Dave Regan", "Emmanuel Saez", "Gabriel Zucman", "Gavin Newsom", "Peter Thiel"]
    }
]


def parse_md_turns(md_path: Path) -> list[dict[str, Any]]:
    raw = md_path.read_text(encoding="utf-8")
    body = raw.split("---\n", 2)[2] if raw.startswith("---\n") else raw
    turns: list[dict[str, Any]] = []
    cur: dict[str, Any] | None = None
    for line in body.splitlines():
        m = TURN_RE.match(line)
        if m:
            if cur:
                cur["text"] = cur["text"].strip()
                turns.append(cur)
            cur = {"clock": m.group(1), "speaker": m.group(2), "text": m.group(3)}
        elif cur is not None:
            cur["text"] += "\n" + line
    if cur:
        cur["text"] = cur["text"].strip()
        turns.append(cur)
    return turns


def build_splits(slice_audio: bool = False) -> dict[str, Any]:
    parent_cache: dict[str, dict[str, Any]] = {}
    splits_out: list[dict[str, Any]] = []
    by_parent: dict[str, list[dict[str, Any]]] = {}

    for spec in SPLIT_SPECS:
        p_stem = spec["parent_stem"]
        if p_stem not in parent_cache:
            md_path = EPISODES_DIR / f"{p_stem}.md"
            tl_path = ARTIFACTS_DIR / p_stem / "timeline.json"
            mf_path = ARTIFACTS_DIR / p_stem / "manifest.json"
            turns = parse_md_turns(md_path)
            tl = json.loads(tl_path.read_text(encoding="utf-8"))["turns"] if tl_path.exists() else []
            mf = json.loads(mf_path.read_text(encoding="utf-8")) if mf_path.exists() else {}
            parent_cache[p_stem] = {"turns": turns, "tl": {t["i"]: t for t in tl}, "mf": mf}

        p_data = parent_cache[p_stem]
        all_turns = p_data["turns"]
        tl_by_i = p_data["tl"]
        mf = p_data["mf"]

        t_start_1 = spec["turn_start"]
        t_end_1 = spec["turn_end"]
        sliced_turns = all_turns[t_start_1 - 1 : t_end_1]
        if not sliced_turns:
            raise ValueError(f"Empty turn slice for {spec['slug']}")

        def _clk_sec(c: str) -> float:
            parts = [int(x) for x in c.split(":")]
            return float(parts[0] * 3600 + parts[1] * 60 + parts[2])

        base_clk = _clk_sec(all_turns[0]["clock"]) if all_turns else 0.0
        first_tl = next((tl_by_i[k] for k in range(t_start_1 - 1, t_end_1) if tl_by_i.get(k, {}).get('edited_start') is not None), {})
        last_tl = next((tl_by_i[k] for k in range(t_end_1 - 1, t_start_1 - 2, -1) if tl_by_i.get(k, {}).get('edited_end') is not None), {})

        clock_start = sliced_turns[0]["clock"]
        clock_end = sliced_turns[-1]["clock"]

        if first_tl and last_tl:
            parent_start_sec = round(float(first_tl.get("edited_start") or 0.0), 2)
            parent_end_sec = round(float(last_tl.get("edited_end") or parent_start_sec), 2)
        else:
            parent_start_sec = round(max(0.0, _clk_sec(clock_start) - base_clk), 2)
            parent_end_sec = round(max(parent_start_sec + 30.0, _clk_sec(clock_end) - base_clk + 30.0), 2)
        duration_sec = max(1, int(round(parent_end_sec - parent_start_sec)))

        # Build turn records for build_site.py
        turn_records: list[dict[str, Any]] = []
        md_lines: list[str] = []
        for idx, t in enumerate(sliced_turns):
            orig_1 = t_start_1 + idx
            tl_entry = tl_by_i.get(orig_1 - 1, {})
            p_s = tl_entry.get("edited_start")
            p_e = tl_entry.get("edited_end")
            if p_s is None:
                p_s = round(max(0.0, _clk_sec(t["clock"]) - base_clk), 2)
            if p_e is None:
                nxt_clk = sliced_turns[idx + 1]["clock"] if idx + 1 < len(sliced_turns) else clock_end
                p_e = round(max(float(p_s) + 5.0, _clk_sec(nxt_clk) - base_clk), 2)
            rel_s = round(max(0.0, float(p_s) - parent_start_sec), 2)
            rel_e = round(max(0.0, float(p_e) - parent_start_sec), 2)
            turn_records.append({
                "i": idx,
                "parent_turn_i": orig_1 - 1,
                "parent_turn_1based": orig_1,
                "clock": t["clock"],
                "speaker": t["speaker"],
                "text": t["text"],
                "start": float(p_s) if p_s is not None else None,
                "end": float(p_e) if p_e is not None else None,
                "rel_start": rel_s,
                "rel_end": rel_e,
                "voice": tl_entry.get("voice")
            })
            md_lines.append(f"[{t['clock']}] {t['speaker']}: {t['text']}")

        # Write standalone markdown file in episodes/2026-10-23_<slug>.md
        topics_yaml = "\n".join(f"  - { json.dumps(tp, ensure_ascii=False) }" for tp in spec["topics"])
        people_yaml = ", ".join(spec["people"])
        fm = (
            "---\n"
            f"date: {spec['date']}\n"
            f"title: {json.dumps(spec['title'], ensure_ascii=False)}\n"
            f"summary: {json.dumps(spec['summary'], ensure_ascii=False)}\n"
            f"context: {json.dumps(spec['context'], ensure_ascii=False)}\n"
            f"parent_session: {spec['parent_stem']}\n"
            f"parent_slug: {spec['parent_slug']}\n"
            f"part_number: {spec['part_number']}\n"
            f"part_total: {spec['part_total']}\n"
            f"parent_turn_start: {t_start_1 - 1}\n"
            f"parent_turn_end: {t_end_1 - 1}\n"
            f"parent_clock_start: {clock_start}\n"
            f"parent_clock_end: {clock_end}\n"
            f"parent_start_sec: {parent_start_sec}\n"
            f"parent_end_sec: {parent_end_sec}\n"
            f"topics:\n{topics_yaml}\n"
            f"people: [{people_yaml}]\n"
            f"note: Episode {spec['episode_number']}, recorded during session {spec['parent_stem']} (turns {t_start_1}-{t_end_1}, {clock_start}-{clock_end} UTC).\n"
            "---\n\n"
        )
        ep_md_path = EPISODES_DIR / f"{spec['stem']}.md"
        ep_md_path.write_text(fm + "\n\n".join(md_lines) + "\n", encoding="utf-8")

        entry = {
            "slug": spec["slug"],
            "stem": spec["stem"],
            "date": spec["date"],
            "episode_number": spec["episode_number"],
            "title": spec["title"],
            "summary": spec["summary"],
            "context": spec["context"],
            "topics": spec["topics"],
            "people": spec["people"],
            "parent_slug": spec["parent_slug"],
            "parent_stem": spec["parent_stem"],
            "parent_title": spec["parent_title"],
            "part_number": spec["part_number"],
            "part_total": spec["part_total"],
            "turn_start_1based": t_start_1,
            "turn_end_1based": t_end_1,
            "turn_start_0based": t_start_1 - 1,
            "turn_end_0based": t_end_1 - 1,
            "turn_count": len(sliced_turns),
            "clock_start": clock_start,
            "clock_end": clock_end,
            "parent_start_sec": parent_start_sec,
            "parent_end_sec": parent_end_sec,
            "duration_seconds": duration_sec,
            "parent_audio_url": mf.get("audio_url", f"https://audio.askevepodcast.com/audio/en/{p_stem}.v3.mp3"),
            "deep_link_in_parent": f"/episodes/{spec['parent_slug']}/#t={int(parent_start_sec)}",
            "transcript_path": f"episodes/{spec['stem']}.md",
            "turns": turn_records,
        }
        splits_out.append(entry)
        by_parent.setdefault(spec["parent_slug"], []).append(entry)

        dst_mp3 = OUT_SPLITS_DIR / f"{spec['stem']}.v1.mp3"
        if slice_audio:
            OUT_SPLITS_DIR.mkdir(parents=True, exist_ok=True)
            candidates = list((ROOT / "out" / p_stem / "produce").glob("*.mp3")) + list((ROOT / "out" / "en").glob(f"{p_stem}*.mp3"))
            if candidates:
                src_mp3 = sorted(candidates)[-1]
                subprocess.run(
                    [
                        "ffmpeg", "-y", "-nostdin", "-hide_banner", "-loglevel", "error",
                        "-ss", f"{parent_start_sec:.2f}",
                        "-to", f"{parent_end_sec:.2f}",
                        "-i", str(src_mp3),
                        "-c", "copy",
                        str(dst_mp3),
                    ],
                    check=True,
                )
        entry["guid"] = f"ask-eve:{spec['stem']}"
        entry["audio_key"] = f"audio/en/{spec['stem']}.v1.mp3"
        entry["audio_url"] = f"https://audio.askevepodcast.com/audio/en/{spec['stem']}.v1.mp3"
        entry["published_at"] = spec.get("published_at") or (f"2026-10-23T{12 + spec['episode_number']:02d}:00:00Z" if spec["date"] == "2026-10-23" else f"{spec['date']}T{spec['episode_number']:02d}:00:00Z")
        if dst_mp3.exists():
            entry["local_sliced_mp3"] = str(dst_mp3.relative_to(ROOT))
            entry["local_sliced_bytes"] = dst_mp3.stat().st_size
            entry["audio_bytes"] = dst_mp3.stat().st_size
            entry["audio_sha256"] = hashlib.sha256(dst_mp3.read_bytes()).hexdigest()

    # Wire prev_split / next_split links within each parent session
    for p_slug, parts in by_parent.items():
        parts.sort(key=lambda x: x["part_number"])
        for idx, part in enumerate(parts):
            part["prev_part"] = (
                {"slug": parts[idx - 1]["slug"], "title": parts[idx - 1]["title"], "part_number": parts[idx - 1]["part_number"]}
                if idx > 0 else None
            )
            part["next_part"] = (
                {"slug": parts[idx + 1]["slug"], "title": parts[idx + 1]["title"], "part_number": parts[idx + 1]["part_number"]}
                if idx + 1 < len(parts) else None
            )

    doc = {
        "version": 1,
        "updated_at": "2026-10-24T05:00:00Z",
        "description": "Standalone focused topic episodes deterministically carved from multi-topic recording sessions, with bidirectional provenance links.",
        "splits": splits_out,
        "by_parent": {
            p_slug: [
                {
                    "slug": s["slug"],
                    "stem": s["stem"],
                    "episode_number": s["episode_number"],
                    "part_number": s["part_number"],
                    "part_total": s["part_total"],
                    "title": s["title"],
                    "summary": s["summary"],
                    "clock_start": s["clock_start"],
                    "clock_end": s["clock_end"],
                    "parent_start_sec": s["parent_start_sec"],
                    "parent_end_sec": s["parent_end_sec"],
                    "duration_seconds": s["duration_seconds"],
                    "turn_start_0based": s["turn_start_0based"],
                    "turn_end_0based": s["turn_end_0based"],
                    "turn_count": s["turn_count"],
                    "deep_link_in_parent": s["deep_link_in_parent"],
                }
                for s in parts
            ]
            for p_slug, parts in by_parent.items()
        },
    }
    SPLITS_JSON.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return doc


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--slice-audio", action="store_true", help="Also slice local MP3 files into out/splits/")
    args = ap.parse_args(argv)
    doc = build_splits(slice_audio=args.slice_audio)
    print(json.dumps({
        "splits_count": len(doc["splits"]),
        "parents": {k: len(v) for k, v in doc["by_parent"].items()},
        "slugs": [s["slug"] for s in doc["splits"]],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
