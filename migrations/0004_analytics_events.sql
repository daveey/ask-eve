-- Ask Eve — First-Party PostHog-Compatible Raw Analytics Store (Cloudflare D1 / SQLite)
-- Stores cookieless, zero-PII PostHog events captured via first-party /ingest/e and /ingest/batch
-- plus server-side /feed.xml RSS reader fetches and weekly public snapshot archives.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS analytics_events (
    id               TEXT PRIMARY KEY,
    event            TEXT NOT NULL CHECK (
                         event IN (
                             '$pageview',
                             'outbound_social_click',
                             'donation_click',
                             'rss_feed_click',
                             'audio_play',
                             'source_receipt_click',
                             'rss_feed_fetch'
                         )
                     ),
    distinct_id      TEXT NOT NULL DEFAULT 'anon',
    canonical_path   TEXT NOT NULL DEFAULT '/',
    raw_path         TEXT NOT NULL DEFAULT '/',
    locale           TEXT NOT NULL DEFAULT 'en',
    section          TEXT NOT NULL DEFAULT 'home',
    episode_slug     TEXT,
    platform         TEXT,
    donation_method  TEXT,
    target_url       TEXT,
    referrer_domain  TEXT,
    properties_json  TEXT NOT NULL DEFAULT '{}',
    created_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_analytics_events_created_at
    ON analytics_events(created_at DESC);

CREATE INDEX IF NOT EXISTS idx_analytics_events_event_created
    ON analytics_events(event, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_analytics_events_path_created
    ON analytics_events(canonical_path, created_at DESC);

CREATE TABLE IF NOT EXISTS analytics_weekly_snapshots (
    week_id          TEXT PRIMARY KEY,
    period_start     TEXT NOT NULL,
    period_end       TEXT NOT NULL,
    snapshot_json    TEXT NOT NULL,
    created_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
