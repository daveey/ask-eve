# Self-Hosted PostHog & First-Party Raw Event Ownership (`askevepodcast.com`)

Ask Eve follows a strict **"raw data is the source of truth"** architecture. Our analytics stack combines:

1. **First-Party Raw Event Ownership in Cloudflare D1 (`migrations/0004_analytics_events.sql`):**
   - `askevepodcast.com` runs serverlessly on Cloudflare Pages + Workers + D1 + R2.
   - Every page loads `/assets/analytics.<hash>.js` (built from `site/static/assets/analytics.js`), which emits PostHog-wire-protocol events (`$pageview`, `outbound_social_click`, `donation_click`, `rss_feed_click`, `audio_play`, `source_receipt_click`) to our first-party endpoint `https://askevepodcast.com/ingest/e`.
   - Our Cloudflare Worker (`workers/eve-inbox/src/index.mjs`) and Pages Function (`functions/ingest/[[path]].js`) validate the event against our allowlisted taxonomy, strip all client IP / cookie headers (`$ip = null`, `persistence = "memory"`), and insert the raw event row directly into our own Cloudflare D1 database (`analytics_events`).
   - Because raw events land in our own D1 SQLite database first, **100% of raw event data is owned by Ask Eve in git/D1 from day one** with zero third-party scripts or cookies.

2. **Open-Source Self-Hosted PostHog UI (`infra/posthog/docker-compose.yml`) or PostHog Cloud Free Tier (`us.i.posthog.com`):**
   - Full PostHog requires stateful ClickHouse, Redpanda (Kafka), PostgreSQL, and Redis containers (~4 vCPU / 8–16 GB RAM), which cannot run natively inside stateless Cloudflare Workers.
   - To run the dedicated open-source PostHog UI on a VM (`ph.askevepodcast.com`), run:
     ```bash
     cd infra/posthog
     POSTHOG_DOMAIN=ph.askevepodcast.com POSTHOG_SECRET_KEY="$(openssl rand -base64 32)" docker compose up -d
     ```
   - Point the Cloudflare Worker variable `POSTHOG_UPSTREAM_HOST` at either:
     - `https://ph.askevepodcast.com` (self-hosted Docker stack above), or
     - `https://us.i.posthog.com` (PostHog Cloud free tier — 1M events/month free, used as zero-ops UI fallback when no dedicated VM is provisioned).
   - In both cases, our first-party `/ingest/e` proxy strips visitor IPs and cookies before forwarding upstream, and D1 `analytics_events` remains the canonical raw event store.

## Weekly Public Metrics Snapshot (`/metrics/`)

Every week, `scripts/export_weekly_metrics.py` (automated via `.github/workflows/weekly-metrics.yml`) queries D1 `analytics_events` (and/or PostHog HogQL API) plus public social profile counts, writes the canonical snapshot to `index/metrics.json`, and `scripts/build_site.py` renders the human-readable dashboard at `/metrics/` (and `/<lang>/metrics/` across all 8 languages) alongside `/metrics/metrics.json`.
