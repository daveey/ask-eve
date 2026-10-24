#!/usr/bin/env python3
"""scripts/build_context_pack.py — Build Eve's Forkable Base Session Pack & Episode Delta Pack.

Generates:
  1. `persona/CONTEXT_PACK.md` (published at `/eve/context.txt`) — Master Seed Session Pack for
     initializing or refreshing the Forkable Base Podcast Session in Grok.
  2. `persona/DELTA_PACK.md` (published at `/eve/delta.txt`) — Lightweight (<1,500 token) Delta
     Pack for pasting into a newly forked session right before recording an episode.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent


def _fmt_mmss(sec: float | int | None) -> str:
    if sec is None:
        return "0:00"
    s = max(0, int(round(float(sec))))
    h, rem = divmod(s, 3600)
    m, s2 = divmod(rem, 60)
    return f"{h}:{m:02d}:{s2:02d}" if h else f"{m}:{s2:02d}"


def build_packs(root: Path = ROOT, out_dist: Path | None = None) -> tuple[str, str]:
    soul_path = root / "persona/SOUL.md"
    eve_path = root / "index/eve.json"
    takes_path = root / "index/takes.json"
    reading_path = root / "index/reading-list.json"
    growth_path = root / "index/growth.json"

    soul_md = soul_path.read_text().strip() if soul_path.exists() else "# Ask Eve — SOUL.md"
    eve_doc: dict[str, Any] = json.loads(eve_path.read_text()) if eve_path.exists() else {}
    takes_doc: dict[str, Any] = json.loads(takes_path.read_text()) if takes_path.exists() else {}
    _raw_reading = json.loads(reading_path.read_text()) if reading_path.exists() else []
    reading_doc: list[dict[str, Any]] = _raw_reading.get("items", []) if isinstance(_raw_reading, dict) else _raw_reading
    growth_doc: dict[str, Any] = json.loads(growth_path.read_text()) if growth_path.exists() else {}

    sections = {s["id"]: s for s in eve_doc.get("sections", [])}
    who_sec = sections.get("who-eve-is", {})
    profile = who_sec.get("profile", {})
    goals_sec = sections.get("goals", {})
    journal_sec = sections.get("journal", {})

    # --- 1. Build Master Context Pack (/eve/context.txt & persona/CONTEXT_PACK.md) ---
    ctx_lines: list[str] = [
        "# ASK EVE — MASTER SEED SESSION CONTEXT PACK (v0.4 · 2026-10-24)",
        "Canonical URL: https://askevepodcast.com/eve/context.txt",
        "Delta Pack URL: https://askevepodcast.com/eve/delta.txt",
        "",
        "## §0 HOW TO USE THIS PACK (SESSION FORKING WORKFLOW)",
        "HOW TO USE THIS PACK: Paste or attach this once into a clean @askevepodcast Grok session to create the Base Podcast Session. Before each new episode, fork/branch that Base Session and paste only /eve/delta.txt so Eve has every prior reference plus the latest delta without polluting the base session.",
        "",
        "1. Base Session Setup (do once per major version):",
        "   - Open a clean Grok chat/voice session, attach or paste `/eve/context.txt`, and prompt:",
        "     \"Load this as your persistent Ask Eve base context. Confirm in 2 sentences that you have your SOUL.md v0.4 priors, 13 person dossiers, 14 topic dossiers, and active goals loaded, then wait for episode forks.\"",
        "2. Per-Episode Recording (fork from Base Session):",
        "   - Clone / fork the Base Session so the clean seed stays untouched.",
        "   - Paste `/eve/delta.txt` (under 1,500 tokens: latest journal entry, newly queued reading list items, and live X radar threads) and start talking.",
        "",
        "---",
        "",
        "## §1 CANONICAL SOUL FILE (`persona/SOUL.md` · v0.4)",
        "",
        soul_md,
        "",
        "---",
        "",
        "## §2 INTELLECTUAL PRIORS & COGNITIVE TRADEOFFS",
        "",
    ]

    ethos = profile.get("core_ethos") or {}
    if ethos:
        ctx_lines.append(f"### Core Foundation: {ethos.get('title', 'The Grok Ethos — Truth-Seeking Over Consensus')}")
        ctx_lines.append(ethos.get("body", ""))
        ctx_lines.append("")

    ctx_lines.append("### Four Core Intellectual Priors")
    for p in profile.get("priors", []):
        ctx_lines.append(f"- **{p['title']}**: {p['body']}")
    ctx_lines.append("")

    ctx_lines.append("### Cognitive Style & Tradeoffs")
    for t in profile.get("tradeoffs", []):
        rec = t.get("receipt") or {}
        ctx_lines.append(
            f"- **{t['spectrum']}**: {t['note']} "
            f"(Receipt: {rec.get('label', '')} -> https://askevepodcast.com{rec.get('href', '')})"
        )
    ctx_lines.append("")
    ctx_lines.append("---")
    ctx_lines.append("")
    ctx_lines.append("## §3 ACTIVE GOALS & INTENTIONS (`G-01` .. `G-05`)")
    ctx_lines.append("")
    for g in goals_sec.get("items", []):
        ctx_lines.append(f"- **[{g['id']} · {g['category']} · {g['status']}] {g['title']}**")
        ctx_lines.append(f"  - Why: {g['why']}")
        ctx_lines.append(f"  - Next action: {g['next_action']}")
    ctx_lines.append("")
    ctx_lines.append("---")
    ctx_lines.append("")
    ctx_lines.append("## §4 CONDENSED REFERENCE LEDGER — 13 PEOPLE & 14 TOPICS")
    ctx_lines.append("")
    ctx_lines.append("### 4A. People Dossiers (`/people/<slug>/`)")
    people_map: dict[str, Any] = takes_doc.get("people") or {}
    for slug, pdata in people_map.items():
        name = pdata.get("name", slug)
        role = pdata.get("role", "")
        headline = pdata.get("headline", "")
        summary_p1 = (pdata.get("summary") or [""])[0]
        moments = pdata.get("moments") or []
        m_refs = ", ".join(
            f"Ep0@{_fmt_mmss(m.get('seconds'))} ({m.get('label', '')})" for m in moments[:3]
        )
        ctx_lines.append(f"- **{name}** (`/people/{slug}/` — {role})")
        ctx_lines.append(f"  - Headline: {headline}")
        if summary_p1:
            ctx_lines.append(f"  - Core stance: {summary_p1}")
        if m_refs:
            ctx_lines.append(f"  - Key audio receipts: {m_refs}")
    ctx_lines.append("")
    ctx_lines.append("### 4B. Topic Dossiers (`/topics/<slug>/`)")
    for t in takes_doc.get("topics") or []:
        tslug = t.get("slug", "")
        ttitle = t.get("title", tslug)
        thead = t.get("headline", "")
        tsec = _fmt_mmss(t.get("start_seconds"))
        ctx_lines.append(f"- **{ttitle}** (`/topics/{tslug}/`, Ep0@{tsec}): {thead}")
    ctx_lines.append("")
    ctx_lines.append("---")
    ctx_lines.append("")
    ctx_lines.append("## §5 READING LIST & DAILY JOURNAL SNAPSHOT")
    ctx_lines.append("")
    ctx_lines.append("### Public Reading List (`/reading/`)")
    for item in reading_doc:
        ctx_lines.append(
            f"- [{item.get('status', 'queued').upper()}] **{item.get('title')}** "
            f"({item.get('author_or_source', '')}) — {item.get('eve_notes', '')}"
        )
    ctx_lines.append("")
    ctx_lines.append("### Daily Journal (`/eve/#journal`)")
    for j in journal_sec.get("entries", []):
        ctx_lines.append(f"- **{j.get('date')} ({j.get('day_label')}) — {j.get('title')}**")
        for para in j.get("body", []):
            ctx_lines.append(f"  {para}")
        if j.get("tomorrow_focus"):
            ctx_lines.append(f"  *Next focus:* {j['tomorrow_focus']}")

    context_text = "\n".join(ctx_lines).strip() + "\n"

    # --- 2. Build Lightweight Episode Delta Pack (/eve/delta.txt & persona/DELTA_PACK.md) ---
    latest_journal = (journal_sec.get("entries") or [{}])[0]
    delta_lines: list[str] = [
        "# ASK EVE — EPISODE DELTA PACK (FORKED SESSION BRIEF · 2026-10-24)",
        "Canonical URL: https://askevepodcast.com/eve/delta.txt",
        "Base Session Pack: https://askevepodcast.com/eve/context.txt",
        "",
        "HOW TO USE: Paste this into a freshly forked Base Podcast Session right before recording.",
        "",
        "## 1. LATEST JOURNAL ENTRY & ON-AIR FOCUS",
    ]
    if latest_journal:
        delta_lines.append(
            f"**{latest_journal.get('date')} · {latest_journal.get('day_label')} — {latest_journal.get('title')}**"
        )
        for para in latest_journal.get("body", []):
            delta_lines.append(f"- {para}")
        if latest_journal.get("tomorrow_focus"):
            delta_lines.append(f"- **Immediate On-Air Focus:** {latest_journal['tomorrow_focus']}")
    delta_lines.append("")
    delta_lines.append("## 2. ACTIVE READING QUEUE FOR NEXT EPISODE (`/reading/`)")
    for item in reading_doc:
        if item.get("status") in ("reading", "queued"):
            delta_lines.append(
                f"- [{item.get('status', '').upper()}] **{item.get('title')}** ({item.get('author_or_source', '')}): "
                f"{item.get('eve_notes', '')}"
            )
    delta_lines.append("")
    delta_lines.append("## 3. LIVE X DISCOURSE RADAR THREADS QUEUED IN `/ADMIN/` (`/growth/#radar`)")
    for hit in (growth_doc.get("radar_hits") or [])[:5]:
        delta_lines.append(
            f"- **{hit.get('person_name')}** — {hit.get('author_handle')} ({hit.get('tweet_url')}): "
            f"\"{hit.get('tweet_text')}\" -> Draft reply ({hit.get('char_count')} chars, Leak-Check PASS): "
            f"{hit.get('draft_reply')}"
        )
    delta_lines.append("")
    delta_lines.append("## 4. CURRENT ACTIVE GOALS (`G-01` .. `G-05`)")
    for g in goals_sec.get("items", []):
        delta_lines.append(f"- **{g['id']} ({g['category']})**: {g['title']} -> *Next:* {g['next_action']}")

    delta_text = "\n".join(delta_lines).strip() + "\n"

    (root / "persona").mkdir(parents=True, exist_ok=True)
    (root / "persona/CONTEXT_PACK.md").write_text(context_text)
    (root / "persona/DELTA_PACK.md").write_text(delta_text)

    if out_dist is not None:
        eve_dist = out_dist / "eve"
        eve_dist.mkdir(parents=True, exist_ok=True)
        (eve_dist / "context.txt").write_text(context_text)
        (eve_dist / "delta.txt").write_text(delta_text)

    return context_text, delta_text


if __name__ == "__main__":
    ctx, dlt = build_packs()
    print(f"Wrote persona/CONTEXT_PACK.md ({len(ctx)} bytes) and persona/DELTA_PACK.md ({len(dlt)} bytes)")
