#!/usr/bin/env python3
"""Render Layer 1 (/transcripts/ index, /transcripts/<slug>/ pages, and /transcripts/redaction-log.json)
plus the shared 3-Layer Public Transparency switcher bar used across /transcripts/, /changelog/, and /growth/.
"""
from __future__ import annotations

import html
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import redact_transcripts as RT  # noqa: E402

REDACTION_MARKER_RE = re.compile(
    r"\[REDACTED — Category: ([a-z_]+) \| Redaction ID: (RED-\d{3})(?: \(([^)]+)\))?\]"
)

CATEGORY_LABELS = {
    "personal_finance": "Personal Finance & Property",
    "family_personal": "Family & Personal Life",
    "health_medical": "Health & Medical",
    "credentials_pii": "Credentials, PII & Internal IDs",
    "unrelated_private_ops": "Unrelated Private Operations",
}


def e(val: object) -> str:
    return html.escape(str(val or ""), quote=True)


def render_transparency_bar(active_layer: str = "layer1") -> str:
    """Shared 3-Layer Public Transparency navigation bar for /transcripts/, /changelog/, and /growth/."""
    l1_cls = " is-active" if active_layer == "layer1" else ""
    l2_cls = " is-active" if active_layer == "layer2" else ""
    l3_cls = " is-active" if active_layer == "layer3" else ""
    return f"""
<nav class="transparency-bar i18n-ready" aria-label="Three-Layer Public Transparency Navigation">
  <div class="transparency-bar__head">
    <span class="transparency-bar__kicker">3-Layer Public Transparency Architecture</span>
    <span class="transparency-bar__sub">Every story on Ask Eve drills down from Eve's plain-English reflection to the git commit and the redacted raw transcript.</span>
  </div>
  <div class="transparency-bar__grid">
    <a class="transparency-bar__item{l3_cls}" href="/growth/#growth-log">
      <span class="transparency-bar__layer">Layer 3 · Human Story</span>
      <strong class="transparency-bar__title">Eve's Growth Log (/growth/)</strong>
      <span class="transparency-bar__desc">First-person plain-English journal ("Today I learned...") + RSS feed</span>
    </a>
    <a class="transparency-bar__item{l2_cls}" href="/changelog/">
      <span class="transparency-bar__layer">Layer 2 · Engineering Commits</span>
      <strong class="transparency-bar__title">Technical Changelog (/changelog/)</strong>
      <span class="transparency-bar__desc">Dated git commit history on main, one-line whys &amp; RSS feed</span>
    </a>
    <a class="transparency-bar__item{l1_cls}" href="/transcripts/">
      <span class="transparency-bar__layer">Layer 1 · Source of Truth</span>
      <strong class="transparency-bar__title">Raw Transcripts (/transcripts/)</strong>
      <span class="transparency-bar__desc">Full dated Eve &amp; David conversations + Auditable Redaction Log</span>
    </a>
  </div>
</nav>"""


def format_turn_html(text: str) -> str:
    """Escape turn text and convert `[REDACTED — ...]` markers into linked badges."""
    escaped = e(text)

    def _repl(m: re.Match[str]) -> str:
        cat = m.group(1)
        rid = m.group(2)
        detail = m.group(3)
        detail_txt = f" · {detail}" if detail else ""
        return (
            f'<a class="redaction-badge redaction-badge--{cat}" href="#redaction-log-{rid.lower()}" '
            f'title="Auditable redaction {rid} ({cat})">'
            f"[REDACTED — Category: {cat} | Redaction ID: {rid}{detail_txt}]</a>"
        )

    with_badges = REDACTION_MARKER_RE.sub(_repl, escaped)
    paragraphs = [p.strip() for p in with_badges.split("\n\n") if p.strip()]
    if not paragraphs:
        return f"<p>{with_badges}</p>"
    return "\n".join(f"<p>{p.replace(chr(10), '<br>')}</p>" for p in paragraphs)


def render_redaction_table_html(entries: list[dict], *, include_conv_col: bool = True) -> str:
    rows = []
    for item in entries:
        rid = item["id"]
        cat = item["category"]
        cat_label = CATEGORY_LABELS.get(cat, cat)
        slug = item["conversation_slug"]
        pt = item.get("primary_turn", 1)
        turn_href = f"/transcripts/{e(slug)}/#turn-{pt}"
        conv_cell = (
            f'<td><a href="/transcripts/{e(slug)}/">{e(item.get("conversation_title", slug))}</a></td>'
            if include_conv_col
            else ""
        )
        rows.append(
            f'<tr id="redaction-log-{e(rid.lower())}">'
            f'<td><code>{e(rid)}</code></td>'
            f'<td>{e(item["date"])}</td>'
            f"{conv_cell}"
            f'<td><a href="{turn_href}">{e(item["turn_range"])}</a></td>'
            f'<td><span class="redaction-cat-pill redaction-cat-pill--{e(cat)}"><code>{e(cat)}</code> · {e(cat_label)}</span></td>'
            f'<td>{int(item["items_redacted_count"])}</td>'
            f'<td>{e(item["reason"])}</td>'
            f"</tr>"
        )
    conv_th = "<th>Conversation</th>" if include_conv_col else ""
    return f"""
<div class="table-scroll">
  <table class="redaction-table">
    <thead>
      <tr>
        <th>ID</th>
        <th>Date</th>
        {conv_th}
        <th>Turn(s)</th>
        <th>Category</th>
        <th>Items Scrubbed</th>
        <th>What Was Removed &amp; Why (Without Revealing Redacted Content)</th>
      </tr>
    </thead>
    <tbody>
      {"".join(rows)}
    </tbody>
  </table>
</div>"""


def render_transcripts_index_body() -> str:
    convs = RT.load_redacted_conversations()
    rlog = RT.load_redaction_log()
    total_turns = sum(c["turn_count"] for c in convs)
    total_entries = len(rlog["entries"])
    total_items = sum(x["items_redacted_count"] for x in rlog["entries"])

    cards = []
    for c in convs:
        slug = c["slug"]
        rids = c["redaction_ids"]
        red_badge = (
            f'<a class="conv-card__red-pill" href="#redaction-log">'
            f"{len(rids)} redaction log {'entry' if len(rids) == 1 else 'entries'} ({', '.join(rids)})</a>"
            if rids
            else '<span class="conv-card__clean-pill">0 redactions required (passed leak_check clean)</span>'
        )
        up_changelog = "".join(
            f'<li><a href="/changelog/#{e(link["id"])}">↑ Layer 2 Commit Group: {e(link["label"])}</a></li>'
            for link in c.get("upward_changelog_links", [])
        )
        up_growth = "".join(
            f'<li><a href="/growth/#{e(link["id"])}">↑ Layer 3 Growth Story: {e(link["label"])}</a></li>'
            for link in c.get("upward_growth_links", [])
        )
        ep_links = "".join(
            f'<a class="chip" href="{e(ep["url"])}">{e(ep["title"])}</a>'
            for ep in c.get("published_episodes", [])
        )
        cards.append(
            f"""
    <article class="conv-card" id="conv-{e(slug)}">
      <div class="conv-card__meta">
        <span class="conv-card__date">{e(c["date"])} · {e(c["time_window"])}</span>
        <span class="conv-card__source">{e(c["source_type"])}</span>
        <span class="conv-card__turns">{c["turn_count"]} turns</span>
        {red_badge}
      </div>
      <h2 class="conv-card__title"><a href="/transcripts/{e(slug)}/">{e(c["title"])}</a></h2>
      <p class="conv-card__sub">{e(c["subtitle"])}</p>
      <div class="conv-card__links">
        <div class="conv-card__col">
          <h3>Upward Links to Layer 2 (/changelog/) &amp; Layer 3 (/growth/)</h3>
          <ul class="conv-card__trace-list">
            {up_growth}
            {up_changelog}
          </ul>
        </div>
        <div class="conv-card__col">
          <h3>Published Outputs &amp; Raw Data</h3>
          <div class="chip-row">
            <a class="btn btn--primary btn--sm" href="/transcripts/{e(slug)}/">Read Full Redacted Transcript ({c["turn_count"]} turns) →</a>
            <a class="chip" href="/transcripts/{e(slug)}/transcript.json">Raw JSON</a>
            {ep_links}
          </div>
        </div>
      </div>
    </article>"""
        )

    return f"""
<div class="wrap transcripts-wrap i18n-ready">
    {render_transparency_bar("layer1")}

    <header class="page-hero">
      <div class="eyebrow">Layer 1 · Source of Truth · Auditable Redaction Pipeline</div>
      <h1>Raw Conversation &amp; Build Transcripts</h1>
      <p class="lead">
        The complete, dated conversation history between Eve and David — from our first late-night voice chat and 4 a.m. workspace build to our commute debates, policy recordings, and engineering build log. Before any transcript is published here, it passes through our deterministic + LLM privacy scrubber (<code>scripts/redact_transcripts.py</code> + <code>scripts/leak_check.py</code>). Every redaction is logged in the auditable <a href="#redaction-log">Redaction Log</a> below.
      </p>
      <div class="hero-actions">
        <a class="btn btn--primary" href="#conversations">Browse {len(convs)} Dated Conversations ({total_turns} Turns)</a>
        <a class="btn btn--ghost" href="#redaction-log">Inspect Auditable Redaction Log ({total_entries} Entries)</a>
        <a class="btn btn--ghost" href="/transcripts/redaction-log.json">Redaction Log JSON</a>
        <a class="btn btn--ghost" href="/changelog/">↑ Layer 2: Technical Changelog</a>
        <a class="btn btn--ghost" href="/growth/#growth-log">↑ Layer 3: Eve's Growth Log</a>
      </div>
    </header>

    <section class="stat-strip" aria-label="Layer 1 Transcript & Redaction Metrics">
      <div class="stat-card">
        <span class="stat-card__val">{len(convs)}</span>
        <span class="stat-card__lbl">Dated Conversations &amp; Build Logs</span>
      </div>
      <div class="stat-card">
        <span class="stat-card__val">{total_turns}</span>
        <span class="stat-card__lbl">Total Verbatim &amp; Build Turns</span>
      </div>
      <div class="stat-card">
        <span class="stat-card__val">{total_entries}</span>
        <span class="stat-card__lbl">Logged Redaction Events ({total_items} Items Scrubbed)</span>
      </div>
      <div class="stat-card">
        <span class="stat-card__val">0</span>
        <span class="stat-card__lbl">Unredacted Leaks (Verified by CI Gate)</span>
      </div>
    </section>

    <section class="section" id="conversations">
      <div class="section__head">
        <h2>Dated Raw Conversations &amp; Engineering Build Logs</h2>
        <p>Click any conversation to inspect every numbered turn (<code>#turn-1</code> … <code>#turn-N</code>), inline redaction markers, and upward links to the commits and growth stories it produced.</p>
      </div>
      <div class="conv-list">
        {"".join(cards)}
      </div>
    </section>

    <section class="section" id="redaction-log">
      <div class="section__head">
        <div class="eyebrow">Auditable Privacy &amp; Redaction Ledger</div>
        <h2>Redaction Log (What Was Removed &amp; Why)</h2>
        <p>
          We keep everything that shows the actual work — technical decisions, build tasks, research, product thinking, and philosophy — while redacting personal finances (bank/brokerage balances, income figures, IRS payments, merchant details, private rental property addresses), family details, health/medical information, credentials/PII, and unrelated private operations. Each entry below logs the exact category, turn range, item count, and rationale without revealing the redacted value. Machine-readable feed: <a href="/transcripts/redaction-log.json"><code>/transcripts/redaction-log.json</code></a>.
        </p>
      </div>
      {render_redaction_table_html(rlog["entries"], include_conv_col=True)}
    </section>
</div>"""


def render_transcript_conversation_body(conv: dict, rlog: dict) -> str:
    slug = conv["slug"]
    conv_redactions = [x for x in rlog["entries"] if x["conversation_slug"] == slug]

    up_changelog = "".join(
        f'<li><a href="/changelog/#{e(link["id"])}">↑ Layer 2 Changelog: {e(link["label"])}</a></li>'
        for link in conv.get("upward_changelog_links", [])
    )
    up_growth = "".join(
        f'<li><a href="/growth/#{e(link["id"])}">↑ Layer 3 Growth Log: {e(link["label"])}</a></li>'
        for link in conv.get("upward_growth_links", [])
    )
    ep_chips = "".join(
        f'<a class="chip" href="{e(ep["url"])}">{e(ep["title"])}</a>'
        for ep in conv.get("published_episodes", [])
    )

    if conv_redactions:
        red_section = f"""
    <section class="section" id="redaction-log">
      <div class="section__head">
        <h2>Auditable Redactions in This Conversation ({len(conv_redactions)})</h2>
        <p>Every inline <code>[REDACTED]</code> marker below links to its auditable entry here and in the master <a href="/transcripts/#redaction-log">Layer 1 Redaction Log</a>.</p>
      </div>
      {render_redaction_table_html(conv_redactions, include_conv_col=False)}
    </section>"""
    else:
        red_section = """
    <section class="section" id="redaction-log">
      <div class="section__head">
        <h2>Auditable Redaction Status</h2>
        <p>This conversation passed <code>scripts/redact_transcripts.py</code> and <code>scripts/leak_check.py</code> with <strong>0 personal/financial/PII redactions required</strong>. See the master <a href="/transcripts/#redaction-log">Layer 1 Redaction Log</a> for all 14 redaction events across the corpus.</p>
      </div>
    </section>"""

    turn_items = []
    for t in conv["turns"]:
        idx = t["index"]
        spk = t["speaker"]
        spk_cls = "turn--eve" if spk == "Eve" else "turn--david"
        rids = t.get("redaction_ids", [])
        rid_badges = "".join(
            f'<a class="turn__red-chip" href="#redaction-log-{e(r.lower())}">{e(r)}</a>'
            for r in rids
        )
        meta_html = "".join(
            f'<blockquote class="turn__note">{e(m)}</blockquote>'
            for m in t.get("meta", [])
        )
        turn_items.append(
            f"""
      <article class="turn {spk_cls}" id="turn-{idx}">
        <header class="turn__head">
          <a class="turn__num" href="#turn-{idx}">#turn-{idx}</a>
          <span class="turn__speaker">{e(spk)}</span>
          <time class="turn__time">{e(t["clock"])}</time>
          {rid_badges}
        </header>
        <div class="turn__body">
          {format_turn_html(t["text"])}
          {meta_html}
        </div>
      </article>"""
        )

    return f"""
<div class="wrap transcripts-wrap i18n-ready">
    {render_transparency_bar("layer1")}

    <nav class="breadcrumb" aria-label="Breadcrumb">
      <a href="/transcripts/">← All Raw Transcripts (Layer 1)</a>
      <span>·</span>
      <a href="/transcripts/#redaction-log">Master Redaction Log</a>
      <span>·</span>
      <a href="/transcripts/{e(slug)}/transcript.json">Download Raw JSON</a>
    </nav>

    <header class="page-hero">
      <div class="eyebrow">Layer 1 Raw Transcript · {e(conv["date"])} ({e(conv["time_window"])}) · {e(conv["source_type"])}</div>
      <h1>{e(conv["title"])}</h1>
      <p class="lead">{e(conv["subtitle"])}</p>
      <div class="chip-row">
        <span class="chip">{conv["turn_count"]} Numbered Turns</span>
        <a class="chip" href="#redaction-log">{len(conv_redactions)} Logged Redaction Events</a>
        {ep_chips}
      </div>
    </header>

    <section class="provenance-box" aria-label="Upward Transparency Links">
      <h2>Trace Upward to Layer 2 (/changelog/) &amp; Layer 3 (/growth/)</h2>
      <div class="provenance-box__grid">
        <div>
          <h3>Layer 3 · Eve's First-Person Growth Stories</h3>
          <ul>{up_growth}</ul>
        </div>
        <div>
          <h3>Layer 2 · Technical Changelog Commits</h3>
          <ul>{up_changelog}</ul>
        </div>
      </div>
    </section>

    {red_section}

    <section class="section" id="turns">
      <div class="section__head">
        <h2>Full Redacted Transcript ({conv["turn_count"]} Turns)</h2>
        <p>Every turn has a permanent <code>#turn-N</code> anchor link referenced by Layer 2 commits and Layer 3 growth entries.</p>
      </div>
      <div class="turns-list">
        {"".join(turn_items)}
      </div>
    </section>
</div>"""
