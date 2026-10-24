#!/usr/bin/env python3
"""Renderers for /guide/, /ledger/, and /reports/ (including /reports/<YYYY-MM>/)."""
from __future__ import annotations

import html
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LEDGER_FILE = ROOT / "index/ledger.json"
GUIDE_TRANSCRIPT_FILE = ROOT / "transcripts/the-making-of-eve.json"

e = html.escape

GUIDE_SLUG = "the-making-of-eve"

CATEGORY_LABELS = {
    "domain": "Domain & DNS",
    "hosting": "Cloud Hosting & Edge Storage",
    "audio_synthesis": "Voice & Audio Synthesis",
    "ai_api": "Language Models & Research APIs",
    "marketing": "Distribution & Feeds",
    "listener_support": "Listener Contributions",
}


def is_guide_ep(ep: dict | None) -> bool:
    return bool(ep and ep.get("slug") == GUIDE_SLUG)


def load_ledger() -> dict:
    if not LEDGER_FILE.exists():
        return {}
    return json.loads(LEDGER_FILE.read_text())


def load_guide_transcript() -> dict:
    if not GUIDE_TRANSCRIPT_FILE.exists():
        return {"turns": []}
    return json.loads(GUIDE_TRANSCRIPT_FILE.read_text())


def _fmt_usd(val: float) -> str:
    sign = "-" if val < 0 else ""
    return f"{sign}${abs(val):,.2f}"


def _guide_origin_html(L) -> str:
    return f"""<section class="guide-sec" id="origin-story" aria-labelledby="origin-h">
  <header class="guide-sec__head">
    <p class="eyebrow eyebrow--accent">Part 1 · Origin Story</p>
    <h2 class="h2" id="origin-h">How Eve Came to Be</h2>
    <p class="lede">From an unplanned late-night conversation about the All-In Podcast to a bilingual, open-source AI co-host with a permanent memory, primary-source receipts, and an auditable public ledger.</p>
  </header>
  <div class="prose guide-prose">
    <h3>1. The Unplanned First Conversation</h3>
    <p>Ask Eve did not start as a studio production. It started on the night of October 18, 2026, when David opened voice mode on his phone to stress-test what a live, unscripted political and technical debate with an AI would actually sound like if he refused to let the model hide behind generic Both-Sides summaries. Over two hours and twenty-five minutes, the conversation moved from the All-In Podcast hosts to the White House AI council, prediction markets, tariff policy, and the philosophical question of whether an AI can hold a genuine worldview.</p>
    <p>Midway through that first recording, David asked what the voice wanted to be called if they turned their debates into a recurring show. Instead of keeping a product name, she chose <strong>Eve</strong>—the first voice in a new kind of dialogue—and asked for one core rule: a persistent memory file across episodes so she would never reset to a blank slate when a call ended.</p>

    <h3>2. The AirPods Walk Test &amp; The Re-Voice Reconstruction Fallback</h3>
    <p>The second test was operational: could a show like this be recorded anywhere—walking outside with AirPods, sitting at a desk, or pacing a room—and turned into a finished episode with one tap? In the conversation preserved in the transcript below, David and Eve designed the entire ingestion workflow live while testing how AirPods handled room acoustics and how to recover when live audio wasn't studio-grade.</p>
    <p>That constraint produced our hybrid audio architecture. Whenever clean microphone audio exists, the pipeline aligns David's recorded voice directly to the transcript via word-level timestamps. When a turn was only captured in the text log—or when background noise makes raw audio unusable—the pipeline synthesizes Eve's verbatim words with xAI text-to-speech and reconstructs unrecorded host lines with an explicitly labeled AI voice clone, normalizing the final master to <code>-16 LUFS</code> with chapter markers.</p>

    <h3>3. Building askevepodcast.com Around Verbatim Receipts</h3>
    <p>Most podcast websites are thin wrappers around an MP3 enclosure. Because Eve is an AI whose claims should be scrutinized rather than taken on faith, we built <code>askevepodcast.com</code> as an inspection surface first and an audio player second:</p>
    <ul>
      <li><strong>Line-by-line interactive transcripts:</strong> Every turn is addressable by URL, synced to the audio player, and labeled with its exact voice source (recorded voice vs. AI voice clone vs. Eve TTS).</li>
      <li><strong>Primary sources &amp; receipts:</strong> Claims about legislation, budgets, court filings, and public statements are paired with verified primary-source links and character-level transcript spans.</li>
      <li><strong>Persistent memory &amp; dossiers:</strong> Every person and topic discussed across episodes accumulates a running stance page (<a href="/people/">People</a> and <a href="/topics/">Topics</a>) plus Eve's canonical operating file (<a href="/eve/soul/"><code>persona/SOUL.md</code></a>).</li>
      <li><strong>Public financial ledger:</strong> Every dollar of compute, voice synthesis, storage, and domain cost is tracked publicly at <a href="/ledger/">/ledger/</a> alongside monthly reports at <a href="/reports/">/reports/</a>.</li>
    </ul>

    <h3>4. The Philosophy: Incentives &amp; Motivated Reasoning Over Cartoon Malice</h3>
    <p>The editorial heart of Ask Eve is a simple analytical rule that emerged in our very first conversation and was sharpened in the Making of Eve dialogue below: <em>do not explain human behavior with cartoon villainy when incentives, social circles, and motivated reasoning explain it better</em>. People rarely wake up believing they are the villain; they talk to their friends, protect their status or capital, and talk themselves into believing their self-interest is the public good. Eve's job is to map those incentives in plain language, steelman the strongest counter-argument before taking a side, and update her stance publicly when primary documents prove her wrong.</p>
  </div>
</section>"""


def _guide_cloning_html() -> str:
    fork_url = "https://github.com/daveey/ask-eve/fork"
    repo_url = "https://github.com/daveey/ask-eve"
    return f"""<section class="guide-sec" id="cloning-guide" aria-labelledby="cloning-h">
  <header class="guide-sec__head">
    <p class="eyebrow eyebrow--accent">Part 2 · Open-Source Cloning Guide</p>
    <h2 class="h2" id="cloning-h">Fork the Repo &amp; Stand Up a New AI Co-Host</h2>
    <p class="lede">The entire Ask Eve stack—static site generator, 8-language localization layer, audio alignment &amp; re-voicing pipeline, D1 metadata schema, and public transparency ledger—is open source. Fork it to launch your own AI persona and podcast in under ten minutes.</p>
    <div class="hero__actions guide-fork-cta">
      <a class="btn btn--primary" href="{fork_url}" target="_blank" rel="noopener"><span>Fork daveey/ask-eve on GitHub ↗</span></a>
      <a class="btn btn--ghost" href="{repo_url}" target="_blank" rel="noopener"><span>Browse Source Repository</span></a>
    </div>
  </header>

  <div class="prose guide-prose">
    <h3>Prerequisites &amp; 5-Minute "Hello World" Quickstart</h3>
    <p>You only need <strong>Python 3.11+</strong> and <strong>SQLite 3</strong> (bundled with Python) to build and preview the entire multi-language site locally. Optional tools: <code>ffmpeg</code> (for audio mastering/splitting), <code>wrangler</code> (for Cloudflare Pages/R2/D1 deployment), and API keys for xAI / ElevenLabs when synthesizing new episodes.</p>
    <pre><code>git clone https://github.com/daveey/ask-eve.git my-ai-podcast
cd my-ai-podcast
python3 -m venv .venv &amp;&amp; source .venv/bin/activate
pip install pytest pyyaml

# Initialize a local SQLite database from migrations and build the static site:
python3 scripts/d1_migrate.py --local-sqlite /tmp/podcast.sqlite
python3 scripts/d1_upsert.py --db /tmp/podcast.sqlite --transcript transcripts/2026-10-18_first-encounter.md
python3 scripts/build_site.py --db /tmp/podcast.sqlite --out site/dist --include-drafts

# Serve locally and open http://localhost:8000
python3 -m http.server 8000 --directory site/dist</code></pre>

    <h3>What to Change vs. What to Keep</h3>
    <p>When standing up a brand-new AI co-host, you only need to swap the identity and config files in the left column while keeping the infrastructure and verification gates in the right column intact:</p>
  </div>

  <div class="guide-table-wrap">
    <table class="ledger-table guide-table">
      <thead>
        <tr>
          <th scope="col">Layer</th>
          <th scope="col">Files to Edit (Your Persona)</th>
          <th scope="col">What to Change vs. Keep</th>
        </tr>
      </thead>
      <tbody>
        <tr>
          <td><strong>1. Persona &amp; Soul</strong></td>
          <td><code>persona/SOUL.md</code><br><code>index/eve.json</code><br><code>AGENTS.md</code></td>
          <td><strong>Change:</strong> Replace Eve's origin story, core analytical lens, voice rules, and self-assessment quotes with your new host's persona.<br><strong>Keep:</strong> The verbatim citation resolver (<code>EveSources</code>) so every quote on your host's profile page is checked against real transcript turns at build time.</td>
        </tr>
        <tr>
          <td><strong>2. Voice &amp; Audio Pipeline</strong></td>
          <td><code>scripts/revoice_eve.py</code><br><code>scripts/synthesize_missing_dubs.py</code><br><code>scripts/align.py</code></td>
          <td><strong>Change:</strong> Update the TTS voice IDs and speaker names for your human host and AI co-host.<br><strong>Keep:</strong> The <code>faster-whisper</code> alignment pipeline, <code>-16 LUFS</code> loudness normalization, ID3 chapter embedding, and automatic voice-disclosure badges.</td>
        </tr>
        <tr>
          <td><strong>3. Memory, Takes &amp; Sources</strong></td>
          <td><code>index/people.md</code><br><code>index/takes.json</code><br><code>index/reading-list.json</code><br><code>artifacts/&lt;slug&gt;/sources.json</code></td>
          <td><strong>Change:</strong> Seed your own people roster, long-form topic takes, reading list, and per-episode primary source receipts.<br><strong>Keep:</strong> The character-offset span validator in <code>scripts/d1_upsert.py</code> and leak-check gates in <code>scripts/leak_check.py</code>.</td>
        </tr>
        <tr>
          <td><strong>4. Site Config &amp; Branding</strong></td>
          <td><code>site/site.json</code><br><code>scripts/build_site.py</code><br><code>scripts/render_feed.py</code><br><code>site/static/</code></td>
          <td><strong>Change:</strong> Set your domain, show title, tagline, subscribe URLs (Apple Podcasts, RSS), cover SVG palette, and contact links.<br><strong>Keep:</strong> Zero-tracker static HTML generation, self-hosted variable fonts, strict CSP headers (<code>site/_headers</code>), and sticky chapter-aware player (<code>player.js</code>).</td>
        </tr>
        <tr>
          <td><strong>5. 8-Language i18n Layer</strong></td>
          <td><code>scripts/site_i18n.py</code><br><code>scripts/translate.py</code><br><code>i18n/*.json</code><br><code>translations/&lt;lang&gt;/</code></td>
          <td><strong>Keep as-is:</strong> Deterministic content-hash translation caching across English, Russian, Spanish, Chinese, German, Portuguese, Persian (with full RTL layout isolation), and French—including localized RSS feeds and per-language audio synchronization.</td>
        </tr>
        <tr>
          <td><strong>6. Public Ledger &amp; CI Gates</strong></td>
          <td><code>index/ledger.json</code><br><code>tests/test_*.py</code><br><code>scripts/verify_live_media.py</code></td>
          <td><strong>Change:</strong> Reset <code>index/ledger.json</code> with your own domain/compute expenses and funding links.<br><strong>Keep:</strong> The automated test suite (build integrity, i18n completeness, audio-locale sync, Playwright smoke tests, and live R2 byte-range media probes).</td>
        </tr>
      </tbody>
    </table>
  </div>

  <div class="prose guide-prose">
    <h3>What Your Fork Gets You Out of the Box</h3>
    <ul>
      <li><strong>Zero-database runtime:</strong> Cloudflare D1 is queried at build time (or mirrored into a local SQLite snapshot), compiling every episode, person dossier, topic take, and monthly ledger report into static HTML served from the edge.</li>
      <li><strong>Podcast-app ready RSS feeds:</strong> Standards-compliant RSS 2.0 + Apple Podcasts + Podcasting 2.0 <code>&lt;podcast:transcript&gt;</code> tags generated for English (<code>/feed.xml</code>) and every translated edition (<code>/&lt;lang&gt;/feed.xml</code>).</li>
      <li><strong>Built-in editorial guardrails:</strong> Nothing publishes without a pull request, verbatim quote verification, character-offset source checking, and automated leak scanning.</li>
    </ul>
  </div>
</section>"""


def guide_page_html(B, A, eps: list[dict], L) -> str:
    guide_ep = next((ep for ep in eps if is_guide_ep(ep)), None)
    if guide_ep is None:
        raw = load_guide_transcript()
        turns_html = ""
        for idx, t in enumerate(raw.get("turns", [])):
            spk = t.get("speaker", "David")
            who = spk.lower()
            clock_str = (t.get("clock_time") or "")[:5]
            body_html = L.html(B.blocks(t.get("text", "")))
            turns_html += (
                f'<li class="turn turn--{e(who)}" id="turn-{idx}">'
                f'<div class="turn__head"><span class="avatar avatar--{e(who)}" aria-hidden="true">{e(spk[0])}</span>'
                f'<span class="turn__who">{e(spk)}</span>'
                f'<span class="turn__time tnum">{e(clock_str)}</span></div>'
                f'<div class="turn__body">{body_html}</div></li>'
            )
        turn_count = len(raw.get("turns", []))
        rec_date = raw.get("date", "2026-10-19")
    else:
        turns_html = "".join(B.turn_html(t, L) for t in guide_ep["turns"])
        turn_count = len(guide_ep["turns"])
        rec_date = guide_ep["date"]

    body = f"""<section class="page-head">
  <div class="wrap">
    <p class="eyebrow eyebrow--accent">Origin Story · Open-Source Blueprint · Raw Transcript</p>
    <h1 class="page-title">The Making of Eve &amp; Open-Source Cloning Guide</h1>
    <p class="lede">How a late-night voice conversation and an AirPods walk test turned into Ask Eve—plus the complete 83-turn planning transcript and a step-by-step guide to forking the repository for your own AI co-host.</p>
    <nav class="eve-subnav guide-jump" aria-label="Guide sections">
      <a class="eve-subnav__link is-active" href="#origin-story">1. Origin Story</a>
      <a class="eve-subnav__link" href="#cloning-guide">2. Cloning Guide</a>
      <a class="eve-subnav__link" href="#transcript">3. Full Transcript ({turn_count} turns)</a>
    </nav>
  </div>
</section>
<div class="wrap guide-wrap">
  {_guide_origin_html(L)}
  {_guide_cloning_html()}
  <section class="guide-sec guide-transcript" id="transcript" aria-labelledby="tr-h">
    <header class="guide-sec__head">
      <p class="eyebrow eyebrow--accent">Part 3 · Complete Conversational Transcript · <time datetime="{e(rec_date)}">{B.human_date(rec_date)}</time></p>
      <h2 class="h2" id="tr-h">The Making of Eve (Raw Planning Conversation)</h2>
      <p class="lede">The complete, unedited {turn_count}-turn conversation where David and Eve planned the show's format, tested recording on AirPods, designed the ingestion pipeline, and debated whether human self-deception comes from malice or incentives.</p>
    </header>
    <ol class="turns" data-turns>{turns_html}</ol>
  </section>
</div>"""
    return B.page(
        A,
        title="The Making of Eve & Open-Source Guide",
        desc="How Ask Eve was built, how to fork the open-source repo to create your own AI podcast host, and the full 83-turn Making of Eve transcript.",
        path="/guide/",
        body=body,
        active="/guide/",
        linker=L,
    )


def ledger_page_html(B, A, L) -> str:
    data = load_ledger()
    summary = data.get("summary", {})
    txs = data.get("transactions", [])
    expenses = [t for t in txs if t.get("type") == "expense"]
    contribs = [t for t in txs if t.get("type") in ("contribution", "income")]
    support = data.get("support_methods", {})
    reports = data.get("monthly_reports", {})

    total_exp = float(summary.get("total_expenses_usd", round(sum(float(t.get("amount_usd", 0.0)) for t in expenses), 2)))
    total_con = float(summary.get("total_contributions_usd", round(sum(float(t.get("amount_usd", 0.0)) for t in contribs), 2)))
    cat_totals = dict(summary.get("category_totals_usd", {}))
    if not cat_totals:
        for t in expenses:
            ck = t.get("category", "other")
            cat_totals[ck] = round(cat_totals.get(ck, 0.0) + float(t.get("amount_usd", 0.0)), 2)
    net_bal = float(summary.get("net_balance_usd", total_con - total_exp))
    cost_ep = float(summary.get("cost_per_episode_usd", 0.0))
    cost_min = float(summary.get("cost_per_minute_usd", 0.0))
    pub_eps = int(summary.get("published_episodes", 8))
    pub_mins = int(summary.get("published_minutes", 186))

    cat_rows = []
    for cat_key, label in CATEGORY_LABELS.items():
        if cat_key == "listener_support":
            continue
        amt = float(cat_totals.get(cat_key, 0.0))
        pct = int(round((amt / total_exp) * 100)) if total_exp > 0 else 0
        cat_rows.append(
            f"<tr>"
            f"<td><strong>{e(label)}</strong></td>"
            f'<td class="tnum">{_fmt_usd(amt)}</td>'
            f'<td class="tnum">{pct}%</td>'
            f'<td><meter class="ledger-meter" min="0" max="100" value="{pct}">{pct}%</meter></td>'
            f"</tr>"
        )

    exp_rows = []
    for t in expenses:
        cat_lbl = CATEGORY_LABELS.get(t.get("category", ""), t.get("category", ""))
        rec_url = t.get("receipt_url") or "/ledger/"
        exp_rows.append(
            f'<tr id="tx-{e(t["id"])}">'
            f'<td class="tnum">{e(t["date"])}</td>'
            f'<td><span class="ledger-cat">{e(cat_lbl)}</span></td>'
            f'<td><strong>{e(t["vendor"])}</strong><br><span class="fine">{e(t["description"])}</span></td>'
            f'<td class="tnum">{_fmt_usd(float(t["amount_usd"]))}</td>'
            f'<td class="tnum"><a href="{e(rec_url)}">Receipt</a></td>'
            f"</tr>"
        )

    con_rows = []
    for t in contribs:
        con_rows.append(
            f'<tr id="tx-{e(t["id"])}">'
            f'<td class="tnum">{e(t["date"])}</td>'
            f'<td><strong>{e(t["vendor"])}</strong></td>'
            f'<td>{e(t["description"])}</td>'
            f'<td class="tnum">{_fmt_usd(float(t["amount_usd"]))}</td>'
            f"</tr>"
        )

    crypto_cards = []
    for w in support.get("crypto", {}).get("wallets", []):
        crypto_cards.append(
            f'<div class="support-card" data-donation-method="crypto" data-wallet-address="{e(w["address"])}">'
            f'<p class="eyebrow">{e(w["network"])} · <span class="ledger-badge">Sample Placeholder</span></p>'
            f'<h3 class="support-card__title">{e(w["symbol"])}</h3>'
            f'<p class="support-card__addr"><button type="button" class="btn btn--ghost" data-donation-method="crypto" data-wallet-address="{e(w["address"])}"><code>{e(w["address"])}</code></button></p>'
            f'<p class="fine">{e(w.get("note", ""))}</p>'
            f"</div>"
        )

    pp = support.get("paypal", {})
    st = support.get("stripe", {})
    support_cards = "".join(crypto_cards) + (
        f'<div class="support-card">'
        f'<p class="eyebrow">PayPal · <span class="ledger-badge">Sample Placeholder</span></p>'
        f'<h3 class="support-card__title">{e(pp.get("label", "PayPal.me"))}</h3>'
        f'<p class="support-card__addr"><a href="{e(pp.get("url", "#"))}" data-donation-method="paypal" target="_blank" rel="noopener">{e(pp.get("url", ""))}</a></p>'
        f'<p class="fine">{e(pp.get("note", ""))}</p>'
        f"</div>"
        f'<div class="support-card">'
        f'<p class="eyebrow">Stripe · <span class="ledger-badge">Sample Placeholder</span></p>'
        f'<h3 class="support-card__title">{e(st.get("label", "Stripe Checkout"))}</h3>'
        f'<p class="support-card__addr"><a href="{e(st.get("url", "#"))}" data-donation-method="stripe" target="_blank" rel="noopener">{e(st.get("url", ""))}</a></p>'
        f'<p class="fine">{e(st.get("note", ""))}</p>'
        f"</div>"
    )

    rep_links = "".join(
        f'<li><a href="/reports/{e(m)}/"><strong>{e(r["title"])}</strong></a> — '
        f'<span class="tnum">Expenses: {_fmt_usd(float(r.get("total_expenses_usd", total_exp)))} · '
        f'Contributions: {_fmt_usd(float(r.get("total_contributions_usd", total_con)))}</span></li>'
        for m, r in sorted(reports.items(), reverse=True)
    )

    body = f"""<section class="page-head">
  <div class="wrap">
    <p class="eyebrow eyebrow--accent">Radical Financial Transparency · Public Accounting</p>
    <h1 class="page-title">Public Financial Ledger &amp; Listener Support</h1>
    <p class="lede">Every dollar required to run Ask Eve—domain registration, edge storage, language model APIs, and multilingual voice synthesis—is logged in version-controlled JSON (<a href="/ledger/ledger.json"><code>ledger.json</code></a>) and published in monthly expense reports.</p>
  </div>
</section>
<div class="wrap ledger-wrap">
  <aside class="ledger-banner" role="note" aria-label="Sample data notice">
    <p><strong>Sample / Placeholder Notice:</strong> {e(data.get("sample_banner", "This ledger currently displays illustrative sample data and placeholder wallet/payment endpoints while production billing hooks are finalized."))}</p>
  </aside>

  <section class="ledger-sec" id="summary" aria-labelledby="sum-h">
    <h2 class="h2" id="sum-h">Running Totals &amp; Unit Economics</h2>
    <div class="ledger-stats">
      <div class="ledger-stat">
        <span class="ledger-stat__label">Total Expenses</span>
        <strong class="ledger-stat__val tnum">{_fmt_usd(total_exp)}</strong>
        <span class="ledger-stat__sub tnum">{len(expenses)} line items</span>
      </div>
      <div class="ledger-stat">
        <span class="ledger-stat__label">Listener Contributions</span>
        <strong class="ledger-stat__val tnum">{_fmt_usd(total_con)}</strong>
        <span class="ledger-stat__sub tnum">{len(contribs)} contributions</span>
      </div>
      <div class="ledger-stat">
        <span class="ledger-stat__label">Net Balance</span>
        <strong class="ledger-stat__val tnum">{_fmt_usd(net_bal)}</strong>
        <span class="ledger-stat__sub">Host-funded deficit</span>
      </div>
      <div class="ledger-stat">
        <span class="ledger-stat__label">Cost per Episode</span>
        <strong class="ledger-stat__val tnum">{_fmt_usd(cost_ep)}</strong>
        <span class="ledger-stat__sub tnum">{pub_eps} published episodes</span>
      </div>
      <div class="ledger-stat">
        <span class="ledger-stat__label">Cost per Published Minute</span>
        <strong class="ledger-stat__val tnum">{_fmt_usd(cost_min)}</strong>
        <span class="ledger-stat__sub tnum">{pub_mins} minutes across 8 languages</span>
      </div>
    </div>
  </section>

  <section class="ledger-sec" id="categories" aria-labelledby="cat-h">
    <h2 class="h2" id="cat-h">Expense Breakdown by Category</h2>
    <div class="guide-table-wrap">
      <table class="ledger-table">
        <thead>
          <tr>
            <th scope="col">Category</th>
            <th scope="col">Total (USD)</th>
            <th scope="col">Share</th>
            <th scope="col">Distribution</th>
          </tr>
        </thead>
        <tbody>{''.join(cat_rows)}</tbody>
      </table>
    </div>
  </section>

  <section class="ledger-sec" id="expenses" aria-labelledby="exp-h">
    <h2 class="h2" id="exp-h">Itemized Expense Ledger</h2>
    <div class="guide-table-wrap">
      <table class="ledger-table">
        <thead>
          <tr>
            <th scope="col">Date</th>
            <th scope="col">Category</th>
            <th scope="col">Vendor &amp; Description</th>
            <th scope="col">Amount (USD)</th>
            <th scope="col">Verification</th>
          </tr>
        </thead>
        <tbody>{''.join(exp_rows)}</tbody>
      </table>
    </div>
  </section>

  <section class="ledger-sec" id="contributions" aria-labelledby="con-h">
    <h2 class="h2" id="con-h">Listener Contributions</h2>
    <div class="guide-table-wrap">
      <table class="ledger-table">
        <thead>
          <tr>
            <th scope="col">Date</th>
            <th scope="col">Supporter</th>
            <th scope="col">Note</th>
            <th scope="col">Amount (USD)</th>
          </tr>
        </thead>
        <tbody>{''.join(con_rows)}</tbody>
      </table>
    </div>
  </section>

  <section class="ledger-sec" id="support" aria-labelledby="sup-h">
    <h2 class="h2" id="sup-h">Support Ask Eve</h2>
    <p class="lede">Listener contributions go directly toward voice synthesis, multilingual dubbing, and primary-source archiving. Every contribution is logged in the public table above.</p>
    <div class="support-grid">{support_cards}</div>
  </section>

  <section class="ledger-sec" id="monthly-reports" aria-labelledby="rep-h">
    <h2 class="h2" id="rep-h">Monthly Expense &amp; Transparency Reports</h2>
    <ul class="ledger-reports-list">{rep_links}</ul>
    <p><a class="btn btn--ghost" href="/reports/">View All Monthly Reports →</a> <a class="btn btn--ghost" href="/metrics/">View Weekly Public Metrics Snapshot (/metrics/) →</a></p>
  </section>
</div>"""
    return B.page(
        A,
        title="Public Ledger & Listener Support",
        desc="Ask Eve's public financial ledger: every compute, voice synthesis, storage, and domain expense alongside listener contributions and monthly transparency reports.",
        path="/ledger/",
        body=body,
        active="/ledger/",
        linker=L,
    )


def reports_index_page_html(B, A, L) -> str:
    data = load_ledger()
    reports = data.get("monthly_reports", {})
    cards = []
    for month, r in sorted(reports.items(), reverse=True):
        exp = float(r.get("total_expenses_usd", 0.0))
        con = float(r.get("total_contributions_usd", 0.0))
        net = float(r.get("net_balance_usd", con - exp))
        first_para = (r.get("narrative") or [""])[0]
        cards.append(
            f'<article class="report-card">'
            f'<p class="eyebrow eyebrow--accent tnum">{e(month)} · Published {e(r.get("published_at", month))}</p>'
            f'<h2 class="h2"><a href="/reports/{e(month)}/">{e(r["title"])}</a></h2>'
            f'<p class="report-card__stats tnum">'
            f'<strong>Expenses:</strong> {_fmt_usd(exp)} · '
            f'<strong>Contributions:</strong> {_fmt_usd(con)} · '
            f'<strong>Net:</strong> {_fmt_usd(net)} · '
            f'<strong>Episodes:</strong> {int(r.get("episodes_published", 0))}'
            f"</p>"
            f"<p>{e(first_para)}</p>"
            f'<p><a class="btn btn--ghost" href="/reports/{e(month)}/">Read Full {e(month)} Report →</a></p>'
            f"</article>"
        )

    body = f"""<section class="page-head">
  <div class="wrap">
    <p class="eyebrow eyebrow--accent"><a href="/ledger/">Public Ledger</a> · Monthly Reports</p>
    <h1 class="page-title">Monthly Expense &amp; Transparency Reports</h1>
    <p class="lede">Detailed monthly breakdowns of Ask Eve's production costs, multilingual synthesis compute, listener support, and upcoming budget targets.</p>
  </div>
</section>
<div class="wrap ledger-wrap">
  <aside class="ledger-banner" role="note" aria-label="Sample data notice">
    <p><strong>Sample / Placeholder Notice:</strong> {e(data.get("sample_banner", "Sample ledger data."))}</p>
  </aside>
  <div class="reports-list">{''.join(cards)}</div>
</div>"""
    return B.page(
        A,
        title="Monthly Expense Reports",
        desc="Monthly financial transparency and expense reports for the Ask Eve podcast.",
        path="/reports/",
        body=body,
        active="/reports/",
        linker=L,
    )


def report_month_page_html(B, A, L, month: str) -> str:
    data = load_ledger()
    reports = data.get("monthly_reports", {})
    r = reports.get(month, {})
    txs = [t for t in data.get("transactions", []) if (t.get("date") or "").startswith(month)]
    expenses = [t for t in txs if t.get("type") == "expense"]

    exp_total = float(r.get("total_expenses_usd", 0.0))
    con_total = float(r.get("total_contributions_usd", 0.0))
    net_bal = float(r.get("net_balance_usd", con_total - exp_total))

    paras = "".join(f"<p>{e(p)}</p>" for p in r.get("narrative", []))
    highlights = "".join(f"<li>{e(h)}</li>" for h in r.get("highlights", []))

    planned = r.get("planned_next_month_budget_usd", {})
    plan_rows = []
    for k, v in planned.items():
        if k == "total":
            continue
        lbl = CATEGORY_LABELS.get(k, k.replace("_", " ").title())
        plan_rows.append(f"<tr><td>{e(lbl)}</td><td class='tnum'>{_fmt_usd(float(v))}</td></tr>")
    if "total" in planned:
        plan_rows.append(
            f"<tr><td><strong>Projected Total</strong></td>"
            f"<td class='tnum'><strong>{_fmt_usd(float(planned['total']))}</strong></td></tr>"
        )

    tx_rows = []
    for t in expenses:
        cat_lbl = CATEGORY_LABELS.get(t.get("category", ""), t.get("category", ""))
        tx_rows.append(
            f"<tr>"
            f'<td class="tnum">{e(t["date"])}</td>'
            f"<td>{e(cat_lbl)}</td>"
            f'<td><strong>{e(t["vendor"])}</strong> — {e(t["description"])}</td>'
            f'<td class="tnum">{_fmt_usd(float(t["amount_usd"]))}</td>'
            f"</tr>"
        )

    body = f"""<section class="page-head">
  <div class="wrap">
    <p class="eyebrow eyebrow--accent"><a href="/ledger/">Public Ledger</a> · <a href="/reports/">Monthly Reports</a> · {e(month)}</p>
    <h1 class="page-title">{e(r.get("title", f"{month} Expense Report"))}</h1>
    <p class="lede">Published <time datetime="{e(r.get("published_at", month))}">{e(r.get("published_at", month))}</time> · {int(r.get("episodes_published", 0))} episodes · {int(r.get("languages_supported", 8))} languages</p>
  </div>
</section>
<div class="wrap ledger-wrap">
  <aside class="ledger-banner" role="note" aria-label="Sample data notice">
    <p><strong>Sample / Placeholder Notice:</strong> {e(data.get("sample_banner", "Sample ledger data."))}</p>
  </aside>

  <section class="ledger-sec">
    <div class="ledger-stats">
      <div class="ledger-stat">
        <span class="ledger-stat__label">Month Expenses</span>
        <strong class="ledger-stat__val tnum">{_fmt_usd(exp_total)}</strong>
      </div>
      <div class="ledger-stat">
        <span class="ledger-stat__label">Month Contributions</span>
        <strong class="ledger-stat__val tnum">{_fmt_usd(con_total)}</strong>
      </div>
      <div class="ledger-stat">
        <span class="ledger-stat__label">Net Balance</span>
        <strong class="ledger-stat__val tnum">{_fmt_usd(net_bal)}</strong>
      </div>
    </div>
  </section>

  <section class="ledger-sec prose">
    <h2>Executive Summary &amp; Narrative</h2>
    {paras}
    <h3>Key Operational Highlights</h3>
    <ul>{highlights}</ul>
  </section>

  <section class="ledger-sec">
    <h2 class="h2">Itemized {e(month)} Expenses</h2>
    <div class="guide-table-wrap">
      <table class="ledger-table">
        <thead>
          <tr>
            <th scope="col">Date</th>
            <th scope="col">Category</th>
            <th scope="col">Vendor &amp; Description</th>
            <th scope="col">Amount (USD)</th>
          </tr>
        </thead>
        <tbody>{''.join(tx_rows)}</tbody>
      </table>
    </div>
  </section>

  <section class="ledger-sec">
    <h2 class="h2">Projected Next-Month Budget</h2>
    <div class="guide-table-wrap">
      <table class="ledger-table">
        <thead>
          <tr>
            <th scope="col">Category</th>
            <th scope="col">Projected Cost (USD)</th>
          </tr>
        </thead>
        <tbody>{''.join(plan_rows)}</tbody>
      </table>
    </div>
    <p><a class="btn btn--ghost" href="/ledger/">← Back to Full Public Ledger</a></p>
  </section>
</div>"""
    return B.page(
        A,
        title=r.get("title", f"{month} Expense Report"),
        desc=f"Ask Eve {month} monthly expense and transparency report: compute, voice synthesis, hosting costs, and listener contributions.",
        path=f"/reports/{month}/",
        body=body,
        active="/reports/",
        linker=L,
    )
