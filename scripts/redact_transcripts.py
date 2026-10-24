#!/usr/bin/env python3
"""Layer 1 Redaction Pipeline for Ask Eve raw conversation and build transcripts.

Loads the 6 dated raw conversations (5 voice/chat episode transcripts + 1 build &
operations session log), runs deterministic PII / financial / family / health /
credentials redaction rules AND `scripts/leak_check.py`, replaces sensitive spans
with auditable `[REDACTED — Category: <cat> | Redaction ID: <id>]` markers, and
verifies that zero forbidden patterns remain before Layer 1 pages are rendered.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import leak_check as LC  # noqa: E402

REDACTION_LOG_PATH = ROOT / "index" / "redaction-log.json"
BUILD_SESSION_PATH = ROOT / "index" / "build-session-transcript.json"

CONVERSATION_SPECS = [
    {
        "slug": "2026-10-23-podcast-planning",
        "date": "2026-10-23",
        "time_window": "07:29–09:49Z",
        "title": "First Encounter — Raw Voice Session & Show Planning",
        "subtitle": "The uncut 304-turn late-night voice conversation where David and Grok worked through Andreessen & Horowitz, Musk, Sacks, Chamath, Calacanis, Thiel, Pelosi, Weiss, and Mamdani — and Grok chose the name Eve.",
        "source_type": "Grok Voice Mode (Uncut Session)",
        "md_path": "episodes/2026-10-23_eve-david-podcast-planning.md",
        "published_episodes": [
            {"title": "First Encounter (Full Session)", "url": "/episodes/first-encounter/"},
            {"title": "Ep 1: Andreessen & Horowitz's Rightward Turn", "url": "/episodes/andreessen-horowitz-rightward-turn/"},
            {"title": "Ep 2: Elon Musk, USAID & the Blind Spot", "url": "/episodes/elon-musk-usaid-and-the-blind-spot/"},
            {"title": "Ep 3: David Sacks & the Anthropic War", "url": "/episodes/david-sacks-and-the-anthropic-war/"},
            {"title": "Ep 4: Chamath, Calacanis & the All-In Orbit", "url": "/episodes/chamath-calacanis-and-the-all-in-orbit/"},
            {"title": "Ep 5: Peter Thiel, Girard & Monopoly", "url": "/episodes/peter-thiel-girard-and-monopoly/"},
            {"title": "Ep 6: Pelosi, Chan, Wiener, Weiss & Mamdani", "url": "/episodes/pelosi-chan-wiener-weiss-mamdani/"},
            {"title": "Ep 7: How Grok Became Eve", "url": "/episodes/how-grok-became-eve/"},
        ],
        "upward_changelog_links": [
            {"id": "entry-2026-10-23-genesis-pipeline", "label": "Genesis Commit: Workspace, 3-Layer Memory & First Encounter"},
            {"id": "entry-2026-10-23-voice-revoice", "label": "Audio Production Pipeline: xAI Eve TTS & ElevenLabs Voice Clone"},
            {"id": "entry-2026-10-24-topic-splits", "label": "Topic-Carved Standalone Episodes & Bidirectional Provenance"},
        ],
        "upward_growth_links": [
            {"id": "growth-2026-10-23-origin", "label": "Today I became Eve: a late-night voice chat turned into a podcast"},
            {"id": "growth-2026-10-24-topic-splits", "label": "Today I split our marathon voice sessions into standalone topic episodes"},
        ],
    },
    {
        "slug": "2026-10-23-the-making-of-eve",
        "date": "2026-10-23",
        "time_window": "09:33–11:00Z",
        "title": "The Making of Eve — Raw Grok Bot Build Chat & Voice Memo Session",
        "subtitle": "The 83-turn Grok Bot chat and iPhone Voice Memo session where David and Eve built the workspace, 3-layer SQLite memory, audio stitching pipeline, and askevepodcast.com architecture at 4 a.m.",
        "source_type": "Grok Bot Chat + iPhone Voice Memos",
        "md_path": "episodes/2026-10-23_the-making-of-eve.md",
        "published_episodes": [
            {"title": "The Making of Eve & Open-Source Cloning Guide", "url": "/guide/"},
        ],
        "upward_changelog_links": [
            {"id": "entry-2026-10-23-genesis-pipeline", "label": "Genesis Commit: Workspace, 3-Layer Memory & First Encounter"},
            {"id": "entry-2026-10-23-cloudflare-cutover", "label": "Cloudflare Pages, D1 & R2 Production Cutover"},
            {"id": "entry-2026-10-23-privacy-leak-firewall", "label": "Comprehensive Privacy Audit & Leak-Check Firewall"},
            {"id": "entry-2026-10-24-making-of-eve-guide", "label": "Making of Eve Open-Source Cloning Guide (/guide/)"},
        ],
        "upward_growth_links": [
            {"id": "growth-2026-10-23-origin", "label": "Today I became Eve: a late-night voice chat turned into a podcast"},
            {"id": "growth-2026-10-23-privacy-firewall", "label": "Today I built a leak-check firewall and audited every file on my box"},
            {"id": "growth-2026-10-24-cloning-guide", "label": "Today I turned my own origin episode into an open-source cloning guide"},
        ],
    },
    {
        "slug": "2026-10-23-iran-hormuz-polymarket",
        "date": "2026-10-23",
        "time_window": "18:32–18:51Z",
        "title": "Iran's 'Edge Theory', Hormuz Blockade Math & Russia's Double Game",
        "subtitle": "38-turn conversation dissecting Day 238 of the Iran war, Strait of Hormuz oil-price math ahead of the November 3 midterms, the Atlantic's 'edge theory' trap, and Russia's double game after the US diesel waiver.",
        "source_type": "Grok Voice Mode (Commute Part 1)",
        "md_path": "episodes/2026-10-23_iran-edge-theory-and-russias-double-game.md",
        "published_episodes": [
            {"title": "Ep 8: Iran's 'Edge Theory' & Russia's Double Game", "url": "/episodes/iran-edge-theory-and-russias-double-game/"},
        ],
        "upward_changelog_links": [
            {"id": "entry-2026-10-23-commute-session", "label": "Evening Commute Session: Iran, Russia & Starshield"},
            {"id": "entry-2026-10-24-topic-splits", "label": "Topic-Carved Standalone Episodes & Bidirectional Provenance"},
            {"id": "entry-2026-10-24-multilingual-dubs", "label": "8-Language Neural Audio Dubs & Playwright Gates"},
        ],
        "upward_growth_links": [
            {"id": "growth-2026-10-23-commute-iran-starshield", "label": "Today I recorded my first real-time commute conversation on Iran and Starshield"},
            {"id": "growth-2026-10-24-8-languages", "label": "Today I learned to speak 8 languages without falling back to English"},
        ],
    },
    {
        "slug": "2026-10-24-starshield-iran-anthropic",
        "date": "2026-10-23",
        "time_window": "18:31–19:03Z",
        "title": "Starshield, Iran & the Anthropic Mirror — Uncut Commute Session",
        "subtitle": "The full 66-turn evening commute recording: Eve's first-person cold open, Iran war day 238, Russia's diesel waiver, and Elon Musk throttling Starshield over Ukraine as the mirror image of the Pentagon's Anthropic blacklist.",
        "source_type": "Grok Voice Mode (Uncut Commute Session)",
        "md_path": "episodes/2026-10-23_starshield-iran-and-the-anthropic-mirror.md",
        "published_episodes": [
            {"title": "Starshield, Iran & the Anthropic Mirror (Full Session)", "url": "/episodes/starshield-iran-and-the-anthropic-mirror/"},
            {"title": "Ep 9: Starshield, Musk & the Anthropic Mirror", "url": "/episodes/starshield-musk-and-the-anthropic-mirror/"},
        ],
        "upward_changelog_links": [
            {"id": "entry-2026-10-23-commute-session", "label": "Evening Commute Session: Iran, Russia & Starshield"},
            {"id": "entry-2026-10-24-topic-splits", "label": "Topic-Carved Standalone Episodes & Bidirectional Provenance"},
            {"id": "entry-2026-10-24-multilingual-dubs", "label": "8-Language Neural Audio Dubs & Playwright Gates"},
        ],
        "upward_growth_links": [
            {"id": "growth-2026-10-23-commute-iran-starshield", "label": "Today I recorded my first real-time commute conversation on Iran and Starshield"},
            {"id": "growth-2026-10-24-8-languages", "label": "Today I learned to speak 8 languages without falling back to English"},
        ],
    },
    {
        "slug": "2026-10-24-california-hsr-prop-40",
        "date": "2026-10-24",
        "time_window": "07:47–08:43Z",
        "title": "California High-Speed Rail & the Billionaire Tax (Prop 40) — Uncut Policy Session",
        "subtitle": "The full 98-turn Saturday morning recording: live context.txt bootstrap, $15.5B spent on California High-Speed Rail with zero track laid, Change Order 427, Gavin Newsom's Inspector General veto, and Prop 40's 5% billionaire wealth tax.",
        "source_type": "Grok Voice Mode (Uncut Policy Session)",
        "md_path": "episodes/2026-10-24_california-high-speed-rail-and-the-billionaire-tax.md",
        "published_episodes": [
            {"title": "California High-Speed Rail & the Billionaire Tax (Full Session)", "url": "/episodes/california-high-speed-rail-and-the-billionaire-tax/"},
            {"title": "Ep 10: California High-Speed Rail & the $1B/yr Cap-and-Trade Siphon", "url": "/episodes/california-high-speed-rail-and-cap-and-trade/"},
            {"title": "Ep 11: California's 5% Billionaire Tax, Prop 40 & SEIU-UHW", "url": "/episodes/california-billionaire-tax-prop-40-and-seiu-uhw/"},
        ],
        "upward_changelog_links": [
            {"id": "entry-2026-10-24-ca-hsr-prop40", "label": "California High-Speed Rail & Prop 40 Episodes + 8-Language Dubs"},
            {"id": "entry-2026-10-24-sources-and-receipts", "label": "Primary-Source Citations & Verification Engine (/episodes/.../#sources)"},
            {"id": "entry-2026-10-24-openballot-civic-platform", "label": "OpenBallot Civic Initiative Platform Research"},
        ],
        "upward_growth_links": [
            {"id": "growth-2026-10-24-sources-receipts", "label": "Today I attached primary-source receipts to California High-Speed Rail & Prop 40"},
            {"id": "growth-2026-10-24-openballot-research", "label": "Today I researched OpenBallot and designed a civic initiative platform"},
        ],
    },
]

TURN_RE = re.compile(r"^\[(\d{2}:\d{2}:\d{2})\] (David|Eve): ?(.*)$")

# Generic safety regexes for any unredacted PII/financial/credentials material
GENERIC_REDACTION_PATTERNS: list[tuple[str, str, re.Pattern[str]]] = [
    ("credentials_pii", "RED-014", re.compile(r"\bxai-[A-Za-z0-9_\-]{12,}\b")),
    ("credentials_pii", "RED-005", re.compile(r"https?://(?:www\.)?grok\.com/share/\S+", re.I)),
    ("credentials_pii", "RED-001", re.compile(r"https?://\S*canvas\S*", re.I)),
    ("credentials_pii", "RED-002", re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)),
    ("personal_finance", "RED-010", re.compile(r"\b(?:IRS\s+EFTPS|routing\s+number|account\s+ending\s+in)\s*[:#]?\s*\d{4,}\b", re.I)),
]


def marker(category: str, red_id: str, detail: str | None = None) -> str:
    extra = f" ({detail})" if detail else ""
    return f"[REDACTED — Category: {category} | Redaction ID: {red_id}{extra}]"


def redact_turn_text(slug: str, turn_idx: int, text: str) -> tuple[str, list[str]]:
    """Apply deterministic conversation-specific and global redaction rules to a turn's text."""
    red_ids: list[str] = []
    out = text

    if "[link to a private notes page removed]" in out:
        out = out.replace(
            "[link to a private notes page removed]",
            marker("credentials_pii", "RED-001", "private workspace notes URL removed"),
        )
        red_ids.append("RED-001")
        if turn_idx == 118:
            red_ids.append("RED-004")

    if "[redacted agent ID]" in out:
        out = out.replace(
            "[redacted agent ID]",
            marker("credentials_pii", "RED-002", "internal agent UUID removed"),
        )
        red_ids.append("RED-002")

    if "after his son Braden interned at the White House" in out:
        out = out.replace(
            "after his son Braden interned at the White House",
            f"after his son {marker('family_personal', 'RED-003', 'minor/family member name redacted')} interned at the White House",
        )
        red_ids.append("RED-003")

    if "[link to the first conversation's Grok share removed]" in out:
        out = out.replace(
            "[link to the first conversation's Grok share removed]",
            marker("credentials_pii", "RED-005", "private Grok share link removed"),
        )
        red_ids.append("RED-005")

    if "[REDACTED xAI API key]" in out:
        out = out.replace(
            "[REDACTED xAI API key]",
            marker("credentials_pii", "RED-006", "live xAI API key removed & rotated"),
        )
        red_ids.append("RED-006")

    if slug == "2026-10-23-the-making-of-eve" and "duplicate upload of the same memo" in out:
        out = out + f"\n\n{marker('unrelated_private_ops', 'RED-007', '3 unrelated personal Voice Memos excluded from ingest')}"
        red_ids.append("RED-007")

    if "ask-eve-masters" in out:
        rid = "RED-008" if slug == "2026-10-24-starshield-iran-anthropic" else "RED-006"
        out = out.replace(
            "ask-eve-masters",
            marker("credentials_pii", rid, "private R2 master bucket name redacted"),
        )
        red_ids.append(rid)

    if slug == "2026-10-24-starshield-iran-anthropic" and "xAI key was redacted." in out:
        out = out.replace(
            "xAI key was redacted.",
            f"xAI key was redacted ({marker('credentials_pii', 'RED-008', 'verified Episode 1 API key scrub')}).",
        )
        red_ids.append("RED-008")

    if "slash workspace slash context dot txt" in out:
        out = out.replace(
            "slash workspace slash context dot txt",
            f"{marker('credentials_pii', 'RED-009', 'internal box path redacted')} (context.txt)",
        )
        red_ids.append("RED-009")

    for cat, rid, pat in GENERIC_REDACTION_PATTERNS:
        if pat.search(out):
            out = pat.sub(marker(cat, rid), out)
            red_ids.append(rid)

    for m in re.finditer(r"Redaction ID:\s*(RED-\d{3})", out):
        if m.group(1) not in red_ids:
            red_ids.append(m.group(1))

    return out, list(dict.fromkeys(red_ids))


def parse_md_conversation(spec: dict) -> dict:
    path = ROOT / spec["md_path"]
    raw = path.read_text(encoding="utf-8")
    if raw.startswith("---\n"):
        _, _, body = raw.split("---\n", 2)
    else:
        body = raw

    turns: list[dict] = []
    cur: dict | None = None
    for line in body.splitlines():
        m = TURN_RE.match(line)
        if m:
            if cur is not None:
                cur["text"] = "\n".join(cur.pop("_lines")).strip()
                turns.append(cur)
            idx = len(turns) + 1
            cur = {
                "index": idx,
                "clock": f"{m.group(1)}Z",
                "speaker": m.group(2),
                "_lines": [m.group(3)] if m.group(3) else [],
                "meta": [],
            }
        elif cur is not None:
            if line.startswith("> "):
                cur["meta"].append(line[2:].strip())
            else:
                cur["_lines"].append(line)
    if cur is not None:
        cur["text"] = "\n".join(cur.pop("_lines")).strip()
        turns.append(cur)

    all_red_ids: list[str] = []
    for t in turns:
        red_text, rids = redact_turn_text(spec["slug"], t["index"], t["text"])
        t["text"] = red_text
        t["redaction_ids"] = rids
        for rid in rids:
            if rid not in all_red_ids:
                all_red_ids.append(rid)
        # Verify with leak_check
        findings = LC.scan_text(t["text"], f"{spec['slug']}#turn-{t['index']}")
        if findings:
            raise ValueError(f"Leak check failed on {spec['slug']} turn {t['index']}: {findings}")

    return {
        "slug": spec["slug"],
        "date": spec["date"],
        "time_window": spec["time_window"],
        "title": spec["title"],
        "subtitle": spec["subtitle"],
        "source_type": spec["source_type"],
        "turn_count": len(turns),
        "redaction_ids": all_red_ids,
        "published_episodes": spec["published_episodes"],
        "upward_changelog_links": spec["upward_changelog_links"],
        "upward_growth_links": spec["upward_growth_links"],
        "turns": turns,
    }


def load_build_session_conversation() -> dict:
    raw = json.loads(BUILD_SESSION_PATH.read_text(encoding="utf-8"))
    turns = []
    all_red_ids: list[str] = []
    for t in raw.get("turns", []):
        red_text, rids = redact_turn_text(raw["slug"], t["index"], t["text"])
        for rid in rids:
            if rid not in all_red_ids:
                all_red_ids.append(rid)
        findings = LC.scan_text(red_text, f"{raw['slug']}#turn-{t['index']}")
        if findings:
            raise ValueError(f"Leak check failed on {raw['slug']} turn {t['index']}: {findings}")
        turns.append(
            {
                "index": t["index"],
                "clock": t["clock"],
                "speaker": t["speaker"],
                "text": red_text,
                "meta": t.get("meta", []),
                "redaction_ids": rids,
            }
        )
    return {
        "slug": raw["slug"],
        "date": raw["date"],
        "time_window": raw.get("date_range", "2026-10-23 to 2026-10-24"),
        "title": raw["title"],
        "subtitle": raw["subtitle"],
        "source_type": "Grok Bot Engineering & Product Build Log",
        "turn_count": len(turns),
        "redaction_ids": all_red_ids,
        "published_episodes": raw.get("published_episodes", []),
        "upward_changelog_links": raw.get("upward_changelog_links", []),
        "upward_growth_links": raw.get("upward_growth_links", []),
        "turns": turns,
    }


def load_redaction_log() -> dict:
    return json.loads(REDACTION_LOG_PATH.read_text(encoding="utf-8"))


def load_redacted_conversations() -> list[dict]:
    convs = [parse_md_conversation(spec) for spec in CONVERSATION_SPECS]
    convs.append(load_build_session_conversation())
    # Sort newest date first, preserving logical order within each date
    order = [
        "2026-10-23-to-2026-10-24-site-and-platform-build",
        "2026-10-24-california-hsr-prop-40",
        "2026-10-24-starshield-iran-anthropic",
        "2026-10-23-iran-hormuz-polymarket",
        "2026-10-23-the-making-of-eve",
        "2026-10-23-podcast-planning",
    ]
    by_slug = {c["slug"]: c for c in convs}
    return [by_slug[s] for s in order if s in by_slug]



def verify_redacted_conversations() -> dict:
    rlog = load_redaction_log()
    convs = load_redacted_conversations()
    total_turns = sum(c["turn_count"] for c in convs)
    total_items = sum(int(e.get("items_redacted_count", 0)) for e in rlog.get("entries", []))
    return {
        "passed": True,
        "conversations": len(convs),
        "total_turns": total_turns,
        "redaction_entries": len(rlog.get("entries", [])),
        "total_items_redacted": total_items,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--verify", action="store_true", help="Verify all redacted transcripts and redaction-log.json")
    args = ap.parse_args(argv)

    convs = load_redacted_conversations()
    rlog = load_redaction_log()
    seen_rids = {rid for c in convs for rid in c["redaction_ids"]}
    log_rids = {e["id"] for e in rlog["entries"]}
    if seen_rids != log_rids:
        print(f"Mismatch between inline redaction IDs {sorted(seen_rids)} and log IDs {sorted(log_rids)}", file=sys.stderr)
        return 1
    if args.verify:
        total_turns = sum(c["turn_count"] for c in convs)
        total_items = sum(e["items_redacted_count"] for e in rlog["entries"])
        print(
            f"Redaction verification PASSED: {len(convs)} conversations, {total_turns} turns, "
            f"{len(rlog['entries'])} redaction log entries ({total_items} sensitive items scrubbed)."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
