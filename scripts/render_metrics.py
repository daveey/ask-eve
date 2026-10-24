#!/usr/bin/env python3
"""Render the Ask Eve Public Metrics Snapshot (/metrics/) from index/metrics.json.

Publishes our weekly public transparency dashboard (views by canonical path,
social posts/replies/followers across X, Bluesky, and Truth Social, outbound
social & donation button clicks, audio/locale engagement, "What Worked", and
our complete open-source PostHog event taxonomy) plus raw JSON at
/metrics/metrics.json.
"""
from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parent.parent
METRICS_PATH = ROOT / "index" / "metrics.json"
DIST = ROOT / "site" / "dist"


def _esc(val: object) -> str:
    return html.escape(str(val if val is not None else ""), quote=True)


def load_metrics(path: Path = METRICS_PATH) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def render_metrics_summary_card(data: dict | None = None) -> str:
    """Compact summary card linking to /metrics/ for embedding on /, /growth/, or /ledger/."""
    if data is None:
        data = load_metrics()
    snapshots = data.get("snapshots") or []
    snap = snapshots[0] if snapshots else {}
    totals = snap.get("summary_totals") or {}
    week_id = snap.get("week_id", "2026-W43")
    p_start = snap.get("period_start", "2026-10-18")
    p_end = snap.get("period_end", "2026-10-24")

    return f"""
<section class="metrics-wrap i18n-ready ledger-sec" id="public-metrics-snapshot" aria-labelledby="h-public-metrics-card">
  <div class="sec-head">
    <h2 class="h2" id="h-public-metrics-card">Public Metrics Snapshot ({_esc(week_id)})</h2>
    <p class="sec-sub">{_esc(p_start)} &rarr; {_esc(p_end)} &middot; <a href="/metrics/">Full /metrics/ report &rarr;</a> &middot; <a href="/metrics/metrics.json">metrics.json</a></p>
  </div>
  <div class="ledger-stats">
    <div class="ledger-stat">
      <span class="ledger-stat__label">Weekly Pageviews</span>
      <strong class="ledger-stat__val tnum">{int(totals.get("total_pageviews", 0)):,}</strong>
      <span class="ledger-stat__sub">Across 8 languages + /feed.xml</span>
    </div>
    <div class="ledger-stat">
      <span class="ledger-stat__label">Audio &amp; Clip Plays</span>
      <strong class="ledger-stat__val tnum">{int(totals.get("episode_audio_plays", 0)) + int(totals.get("audiogram_clip_plays", 0)):,}</strong>
      <span class="ledger-stat__sub tnum">{int(totals.get("episode_audio_plays", 0)):,} episode + {int(totals.get("audiogram_clip_plays", 0)):,} clip plays</span>
    </div>
    <div class="ledger-stat">
      <span class="ledger-stat__label">Social Posts &amp; Followers</span>
      <strong class="ledger-stat__val tnum">{int(totals.get("social_posts_published", 0))} posts &middot; {int(totals.get("total_social_followers", 0))} followers</strong>
      <span class="ledger-stat__sub">X, Bluesky &amp; Truth Social</span>
    </div>
    <div class="ledger-stat">
      <span class="ledger-stat__label">Outbound &amp; Support Clicks</span>
      <strong class="ledger-stat__val tnum">{int(totals.get("outbound_social_clicks", 0))} social &middot; {int(totals.get("donation_button_clicks", 0))} support</strong>
      <span class="ledger-stat__sub">Self-hosted PostHog + D1 raw log</span>
    </div>
  </div>
</section>
""".strip()


def render_metrics_body(data: dict | None = None) -> str:
    """Render the inner HTML body for /metrics/index.html."""
    if data is None:
        data = load_metrics()

    stack = data.get("analytics_stack") or {}
    priv = stack.get("privacy_guarantees") or {}
    taxonomy = data.get("event_taxonomy") or []
    snapshots = data.get("snapshots") or []
    snap = snapshots[0] if snapshots else {}

    week_id = snap.get("week_id", "2026-W43")
    p_start = snap.get("period_start", "2026-10-18")
    p_end = snap.get("period_end", "2026-10-24")
    headline = snap.get("headline", "Weekly Public Metrics Snapshot")
    totals = snap.get("summary_totals") or {}
    views_by_path = snap.get("views_by_path") or []
    social_accounts = snap.get("social_accounts") or []
    donation_clicks = snap.get("donation_clicks") or []
    locale_breakdown = snap.get("locale_breakdown") or []
    what_worked = snap.get("what_worked") or []
    what_to_improve = snap.get("what_to_improve") or []

    # 1. Views by canonical path rows
    path_rows_html = []
    for row in views_by_path:
        cpath = row.get("canonical_path", "/")
        label = row.get("label", cpath)
        views = int(row.get("views", 0))
        pct = float(row.get("share_pct", 0.0))
        notes = row.get("notes", "")
        meter_val = max(2, min(100, round(pct)))
        path_rows_html.append(
            f"<tr>"
            f'<td><a href="{_esc(cpath)}"><code>{_esc(cpath)}</code></a></td>'
            f'<td><strong>{_esc(label)}</strong><br><span class="fine">{_esc(notes)}</span></td>'
            f'<td class="tnum"><strong>{views:,}</strong></td>'
            f'<td class="tnum">{pct:.1f}%</td>'
            f'<td><meter class="ledger-meter" min="0" max="100" value="{meter_val}">{pct:.1f}%</meter></td>'
            f"</tr>"
        )

    # 2. Social accounts rows
    social_rows_html = []
    for acc in social_accounts:
        platform = acc.get("platform", "x")
        label = acc.get("label", platform)
        handle = acc.get("handle", "")
        url = acc.get("url", "https://askevepodcast.com")
        posts = int(acc.get("posts_this_week", 0))
        replies = int(acc.get("replies_this_week", 0))
        followers = int(acc.get("followers", 0))
        delta = acc.get("follower_delta", "+0")
        clicks = int(acc.get("outbound_clicks_from_site", 0))
        fmt = acc.get("top_performing_format", "")
        social_rows_html.append(
            f"<tr>"
            f"<td><strong>{_esc(label)}</strong></td>"
            f'<td><a href="{_esc(url)}" data-social-platform="{_esc(platform)}" data-social-handle="{_esc(handle)}" target="_blank" rel="noopener"><code>{_esc(handle)}</code></a></td>'
            f'<td class="tnum">{posts}</td>'
            f'<td class="tnum">{replies}</td>'
            f'<td class="tnum">{followers:,} <span class="ledger-badge">{_esc(delta)}</span></td>'
            f'<td class="tnum"><strong>{clicks:,}</strong></td>'
            f'<td><span class="fine">{_esc(fmt)}</span></td>'
            f"</tr>"
        )

    # 3. Donation button click rows
    donation_rows_html = []
    for d in donation_clicks:
        method = d.get("method", "stripe")
        label = d.get("label", method)
        clicks = int(d.get("clicks", 0))
        pct = float(d.get("share_pct", 0.0))
        src_path = d.get("primary_source_path", "/ledger/")
        notes = d.get("notes", "")
        meter_val = max(2, min(100, round(pct)))
        donation_rows_html.append(
            f"<tr>"
            f"<td><strong>{_esc(label)}</strong> (<code>{_esc(method)}</code>)</td>"
            f'<td><a href="{_esc(src_path)}" data-donation-method="{_esc(method)}"><code>{_esc(src_path)}</code></a></td>'
            f'<td class="tnum"><strong>{clicks:,}</strong></td>'
            f'<td class="tnum">{pct:.1f}%</td>'
            f'<td><meter class="ledger-meter" min="0" max="100" value="{meter_val}">{pct:.1f}%</meter></td>'
            f'<td><span class="fine">{_esc(notes)}</span></td>'
            f"</tr>"
        )

    # 4. Locale engagement rows
    locale_rows_html = []
    for loc in locale_breakdown:
        code = loc.get("locale", "en")
        label = loc.get("label", code)
        views = int(loc.get("views", 0))
        plays = int(loc.get("audio_plays", 0))
        pct = float(loc.get("share_pct", 0.0))
        href = "/" if code == "en" else f"/{code}/"
        meter_val = max(2, min(100, round(pct)))
        locale_rows_html.append(
            f"<tr>"
            f'<td><a href="{_esc(href)}"><code>{_esc(code)}</code> &middot; {_esc(label)}</a></td>'
            f'<td class="tnum">{views:,}</td>'
            f'<td class="tnum">{plays:,}</td>'
            f'<td class="tnum">{pct:.1f}%</td>'
            f'<td><meter class="ledger-meter" min="0" max="100" value="{meter_val}">{pct:.1f}%</meter></td>'
            f"</tr>"
        )

    # 5. What worked cards
    worked_html = []
    for w in what_worked:
        worked_html.append(
            f'<div class="support-card">'
            f'<p class="eyebrow">What Worked</p>'
            f'<h3 class="support-card__title">{_esc(w.get("title"))}</h3>'
            f'<p><strong>Evidence:</strong> {_esc(w.get("evidence"))}</p>'
            f'<p class="fine"><strong>Takeaway:</strong> {_esc(w.get("takeaway"))}</p>'
            f"</div>"
        )
    for imp in what_to_improve:
        worked_html.append(
            f'<div class="support-card">'
            f'<p class="eyebrow">Next Experiment</p>'
            f'<h3 class="support-card__title">{_esc(imp.get("title"))}</h3>'
            f'<p><strong>Baseline:</strong> {_esc(imp.get("evidence"))}</p>'
            f'<p class="fine"><strong>Change Under Test:</strong> {_esc(imp.get("takeaway"))}</p>'
            f"</div>"
        )

    # 6. Event taxonomy rows
    tax_rows_html = []
    for t in taxonomy:
        ev_name = t.get("event", "")
        cat = t.get("category", "")
        trig = t.get("trigger", "")
        props = ", ".join(t.get("properties") or [])
        tax_rows_html.append(
            f"<tr>"
            f"<td><code>{_esc(ev_name)}</code></td>"
            f'<td><span class="ledger-cat">{_esc(cat)}</span></td>'
            f"<td>{_esc(trig)}</td>"
            f'<td><span class="fine"><code>{_esc(props)}</code></span></td>'
            f"</tr>"
        )

    return f"""<section class="page-head">
  <div class="wrap">
    <p class="eyebrow eyebrow--accent">Radical Transparency &middot; Self-Hosted PostHog + Cloudflare D1</p>
    <h1 class="page-title">Public Metrics Snapshot</h1>
    <p class="lede i18n-ready">Every week we publish what is working across askevepodcast.com: pageviews by canonical path, RSS feed activity, episode and clip plays across all 8 languages, social growth on X, Bluesky, and Truth Social, and listener support clicks—captured with our cookieless, zero-PII PostHog stack (<a href="/metrics/metrics.json"><code>metrics.json</code></a>).</p>
  </div>
</section>
<div class="wrap ledger-wrap metrics-wrap i18n-ready">
  <aside class="ledger-banner" role="note" aria-label="Raw data ownership notice">
    <p><strong>100% First-Party Raw Data Ownership:</strong> Every event is captured via first-party <code>{_esc(stack.get("first_party_ingest_endpoint", "/ingest/e"))}</code> into our Cloudflare D1 <code>analytics_events</code> table and open-source PostHog stack (<code>{_esc(stack.get("self_hosted_compose_path", "infra/posthog/docker-compose.yml"))}</code>). Persistence is <code>{_esc(priv.get("persistence_mode", "memory"))}</code> (zero cookies, zero IP logging, no GDPR banner required). Download raw JSON at <a href="/metrics/metrics.json"><code>/metrics/metrics.json</code></a> or query live D1 totals at <a href="/api/metrics"><code>/api/metrics</code></a>.</p>
  </aside>

  <section class="ledger-sec" id="snapshot-summary" aria-labelledby="h-metrics-summary">
    <h2 class="h2" id="h-metrics-summary">Weekly Snapshot: {_esc(week_id)} ({_esc(p_start)} &rarr; {_esc(p_end)})</h2>
    <p class="lede">{_esc(headline)}</p>
    <div class="ledger-stats">
      <div class="ledger-stat">
        <span class="ledger-stat__label">Total Pageviews</span>
        <strong class="ledger-stat__val tnum">{int(totals.get("total_pageviews", 0)):,}</strong>
        <span class="ledger-stat__sub">First-party <code>$pageview</code></span>
      </div>
      <div class="ledger-stat">
        <span class="ledger-stat__label">RSS Feed Activity</span>
        <strong class="ledger-stat__val tnum">{int(totals.get("rss_feed_fetches_and_clicks", 0)):,}</strong>
        <span class="ledger-stat__sub"><code>/feed.xml</code> fetches + clicks</span>
      </div>
      <div class="ledger-stat">
        <span class="ledger-stat__label">Episode &amp; Clip Plays</span>
        <strong class="ledger-stat__val tnum">{int(totals.get("episode_audio_plays", 0)) + int(totals.get("audiogram_clip_plays", 0)):,}</strong>
        <span class="ledger-stat__sub tnum">{int(totals.get("episode_audio_plays", 0)):,} ep + {int(totals.get("audiogram_clip_plays", 0)):,} clip plays</span>
      </div>
      <div class="ledger-stat">
        <span class="ledger-stat__label">Source Receipt Clicks</span>
        <strong class="ledger-stat__val tnum">{int(totals.get("source_receipt_clicks", 0)):,}</strong>
        <span class="ledger-stat__sub">Primary-source verifications</span>
      </div>
      <div class="ledger-stat">
        <span class="ledger-stat__label">Social Posts &amp; Followers</span>
        <strong class="ledger-stat__val tnum">{int(totals.get("social_posts_published", 0))} posts &middot; {int(totals.get("total_social_followers", 0))} followers</strong>
        <span class="ledger-stat__sub tnum">{int(totals.get("social_replies_sent", 0))} replies sent this week</span>
      </div>
      <div class="ledger-stat">
        <span class="ledger-stat__label">Outbound &amp; Support Clicks</span>
        <strong class="ledger-stat__val tnum">{int(totals.get("outbound_social_clicks", 0))} social &middot; {int(totals.get("donation_button_clicks", 0))} support</strong>
        <span class="ledger-stat__sub">Stripe, Crypto &amp; PayPal</span>
      </div>
    </div>
  </section>

  <section class="ledger-sec" id="views-by-path" aria-labelledby="h-views-by-path">
    <h2 class="h2" id="h-views-by-path">Page Views by Canonical Path</h2>
    <div class="guide-table-wrap">
      <table class="ledger-table">
        <thead>
          <tr>
            <th scope="col">Canonical Path</th>
            <th scope="col">Section &amp; Observations</th>
            <th scope="col">Views</th>
            <th scope="col">Share</th>
            <th scope="col">Distribution</th>
          </tr>
        </thead>
        <tbody>{''.join(path_rows_html)}</tbody>
      </table>
    </div>
  </section>

  <section class="ledger-sec" id="social-distribution" aria-labelledby="h-social-growth">
    <h2 class="h2" id="h-social-growth">Social Distribution &amp; Outbound Account Clicks</h2>
    <div class="guide-table-wrap">
      <table class="ledger-table">
        <thead>
          <tr>
            <th scope="col">Platform</th>
            <th scope="col">Official Handle</th>
            <th scope="col">Posts</th>
            <th scope="col">Replies</th>
            <th scope="col">Followers</th>
            <th scope="col">Outbound Clicks</th>
            <th scope="col">What Worked Best on Platform</th>
          </tr>
        </thead>
        <tbody>{''.join(social_rows_html)}</tbody>
      </table>
    </div>
  </section>

  <section class="ledger-sec" id="donation-clicks" aria-labelledby="h-donation-clicks">
    <h2 class="h2" id="h-donation-clicks">Support &amp; Donation Button Clicks (<code>donation_click</code>)</h2>
    <div class="guide-table-wrap">
      <table class="ledger-table">
        <thead>
          <tr>
            <th scope="col">Support Method</th>
            <th scope="col">Source Path</th>
            <th scope="col">Clicks</th>
            <th scope="col">Share</th>
            <th scope="col">Distribution</th>
            <th scope="col">Notes</th>
          </tr>
        </thead>
        <tbody>{''.join(donation_rows_html)}</tbody>
      </table>
    </div>
  </section>

  <section class="ledger-sec" id="what-worked" aria-labelledby="h-what-worked">
    <h2 class="h2" id="h-what-worked">What Worked &amp; What We Are Testing Next</h2>
    <div class="support-grid">{''.join(worked_html)}</div>
  </section>

  <section class="ledger-sec" id="multilingual-reach" aria-labelledby="h-locale-breakdown">
    <h2 class="h2" id="h-locale-breakdown">Multilingual Reach Across 8 Languages</h2>
    <div class="guide-table-wrap">
      <table class="ledger-table">
        <thead>
          <tr>
            <th scope="col">Locale</th>
            <th scope="col">Pageviews</th>
            <th scope="col">Audio Plays</th>
            <th scope="col">Share of Views</th>
            <th scope="col">Distribution</th>
          </tr>
        </thead>
        <tbody>{''.join(locale_rows_html)}</tbody>
      </table>
    </div>
  </section>

  <section class="ledger-sec" id="event-taxonomy" aria-labelledby="h-taxonomy">
    <h2 class="h2" id="h-taxonomy">Self-Hosted PostHog Event Taxonomy &amp; Privacy Specification</h2>
    <div class="guide-table-wrap">
      <table class="ledger-table">
        <thead>
          <tr>
            <th scope="col">Event Name</th>
            <th scope="col">Category</th>
            <th scope="col">Trigger Condition</th>
            <th scope="col">Properties Captured (Zero PII)</th>
          </tr>
        </thead>
        <tbody>{''.join(tax_rows_html)}</tbody>
      </table>
    </div>
  </section>
</div>"""


def metrics_page(A: Callable[[str], str], data: dict | None = None) -> str:
    import build_site as B

    return B.page(
        A,
        title="Public Metrics Snapshot",
        desc="Weekly public transparency metrics for Ask Eve: pageviews by path, RSS feed fetches, multilingual episode plays, X/Bluesky/Truth Social growth, support clicks, and what worked.",
        path="/metrics/",
        active="/metrics/",
        body=render_metrics_body(data),
    )


def write_metrics_json_to_dist(dist: Path = DIST, data: dict | None = None) -> Path:
    if data is None:
        data = load_metrics()
    out = dist / "metrics" / "metrics.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return out


if __name__ == "__main__":
    p = write_metrics_json_to_dist()
    print(f"Wrote {p}")
