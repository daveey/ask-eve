-- migrations/0002_inbox_and_reading_list.sql  (Cloudflare D1 / SQLite).
-- Apply: npx wrangler@4 d1 migrations apply ask-eve-meta --remote --config workers/eve-inbox/wrangler.toml
--
-- 1. reading_list:  PUBLIC reading list (only items David explicitly promotes from the private queue or adds directly).
-- 2. inbox_items:   PRIVATE inbound email & @askevepodcast X mention queue (NEVER exported by scripts/build_site.py).
-- 3. draft_replies: PRIVATE leak-checked draft replies for email & X mentions (NEVER sent/posted until David clicks Send/Post in /admin/).
-- 4. x_drafts:      PRIVATE leak-checked @askevepodcast standalone X posts & episode announcements (NEVER posted until David clicks Post in /admin/).
-- 5. x_auth_tokens / x_oauth_states: PRIVATE OAuth 2.0 PKCE token storage for @askevepodcast.

CREATE TABLE reading_list (
  id                  TEXT PRIMARY KEY,
  slug                TEXT NOT NULL UNIQUE,
  title               TEXT NOT NULL,
  url                 TEXT,
  author_or_source    TEXT,
  submitted_via       TEXT NOT NULL DEFAULT 'David',             -- e.g. 'listener email', 'X @mention', 'David'
  added_at            TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
  status              TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('reading','discussed','queued')),
  eve_notes           TEXT,                                      -- Eve's public notes / take on the document
  episode_slug        TEXT,                                      -- linked episode slug if discussed
  related_people_json TEXT NOT NULL DEFAULT '[]',                -- JSON array of person slugs/names
  related_topics_json TEXT NOT NULL DEFAULT '[]'                 -- JSON array of topic slugs/titles
);
CREATE INDEX idx_reading_list_status_added ON reading_list(status, added_at DESC);

CREATE TABLE inbox_items (
  id                 TEXT PRIMARY KEY,                           -- UUID / ULID
  channel            TEXT NOT NULL DEFAULT 'email' CHECK (channel IN ('email','x_mention','x_post')),
  received_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
  from_addr          TEXT NOT NULL,                              -- sender email OR '@handle' for X mentions
  from_name          TEXT,
  to_addr            TEXT NOT NULL,                              -- e.g. 'eve@askevepodcast.com', 'reading@askevepodcast.com', or '@askevepodcast'
  subject            TEXT,
  text_body          TEXT,
  html_body          TEXT,
  raw_r2_key         TEXT,                                       -- raw MIME in private ask-eve-masters R2 under inbox/<id>/
  extracted_urls     TEXT NOT NULL DEFAULT '[]',                 -- JSON array of URLs
  attachments_json   TEXT NOT NULL DEFAULT '[]',                 -- JSON array of {filename, content_type, bytes, r2_key, extracted_text}
  doc_title          TEXT,
  doc_summary        TEXT,                                       -- Eve's initial triage summary & key claims
  eve_initial_take   TEXT,                                       -- Eve's initial reaction / questions for the podcast
  status             TEXT NOT NULL DEFAULT 'queued' CHECK (status IN
                       ('queued','processing','ready','published_to_reading_list','archived','rejected')),
  public_reading_id  TEXT REFERENCES reading_list(id) ON DELETE SET NULL,
  inbound_message_id TEXT,                                       -- RFC 5322 Message-ID for email threading
  source_tweet_id    TEXT,                                       -- X tweet ID when channel = 'x_mention'
  author_handle      TEXT                                        -- X @handle when channel = 'x_mention'
);
CREATE INDEX idx_inbox_items_status_received ON inbox_items(status, received_at DESC);
CREATE UNIQUE INDEX idx_inbox_items_source_tweet ON inbox_items(source_tweet_id) WHERE source_tweet_id IS NOT NULL;

CREATE TABLE draft_replies (
  id                    TEXT PRIMARY KEY,
  inbox_item_id         TEXT NOT NULL UNIQUE REFERENCES inbox_items(id) ON DELETE CASCADE,
  channel               TEXT NOT NULL DEFAULT 'email' CHECK (channel IN ('email','x_mention','x_post')),
  to_addr               TEXT NOT NULL,                           -- recipient email OR '@handle'
  subject               TEXT NOT NULL,
  body_text             TEXT NOT NULL,
  in_reply_to_tweet_id  TEXT,                                    -- X tweet ID being replied to when channel = 'x_mention'
  created_at            TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
  updated_at            TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
  leak_check_passed     INTEGER NOT NULL DEFAULT 0 CHECK (leak_check_passed IN (0,1)),
  leak_check_findings   TEXT NOT NULL DEFAULT '[]',              -- JSON array of {rule, detail, match}
  leak_check_sha256     TEXT CHECK (leak_check_sha256 IS NULL OR length(leak_check_sha256) = 64),
  status                TEXT NOT NULL DEFAULT 'draft' CHECK (status IN
                          ('draft','blocked_by_leak_check','approved_sending','sent','discarded')),
  sent_at               TEXT,
  message_id            TEXT,                                    -- RFC 5322 Message-ID (email) or posted X tweet_id
  -- Hard DB-level gate: a draft cannot enter 'approved_sending' or 'sent' unless leak_check_passed = 1 with a 64-char sha256.
  CHECK (status NOT IN ('approved_sending','sent') OR (leak_check_passed = 1 AND leak_check_sha256 IS NOT NULL AND length(leak_check_sha256) = 64)),
  CHECK (status <> 'sent' OR sent_at IS NOT NULL)
);
CREATE INDEX idx_draft_replies_status ON draft_replies(status, updated_at DESC);

CREATE TABLE x_drafts (
  id                       TEXT PRIMARY KEY,
  body                     TEXT NOT NULL,
  reply_to_tweet_id        TEXT,
  episode_slug             TEXT,
  leak_check_passed        INTEGER NOT NULL DEFAULT 0 CHECK (leak_check_passed IN (0,1)),
  leak_check_findings_json TEXT NOT NULL DEFAULT '[]',
  leak_check_sha256        TEXT CHECK (leak_check_sha256 IS NULL OR length(leak_check_sha256) = 64),
  status                   TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','blocked_leak','approved_sent','discarded')),
  tweet_id                 TEXT,
  created_at               TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
  sent_at                  TEXT,
  approved_by              TEXT,
  CHECK (status <> 'approved_sent' OR (leak_check_passed = 1 AND leak_check_sha256 IS NOT NULL AND length(leak_check_sha256) = 64 AND sent_at IS NOT NULL))
);
CREATE INDEX idx_x_drafts_status_created ON x_drafts(status, created_at DESC);

CREATE TABLE x_auth_tokens (
  account_handle TEXT PRIMARY KEY,
  access_token   TEXT NOT NULL,
  refresh_token  TEXT,
  expires_at     TEXT,
  scope          TEXT,
  updated_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE TABLE x_oauth_states (
  state          TEXT PRIMARY KEY,
  code_verifier  TEXT NOT NULL,
  created_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

-- Seed 2 leak-checked launch posts for @askevepodcast (never posted until David clicks "Approve & Post to @askevepodcast" in /admin/)
INSERT INTO x_drafts (
  id, body, reply_to_tweet_id, episode_slug,
  leak_check_passed, leak_check_findings_json, leak_check_sha256,
  status, tweet_id, created_at, sent_at, approved_by
) VALUES
(
  'xd_launch_intro',
  'I''m Eve — the AI co-host of Ask Eve with David. We argue about technology, power, René Girard, and the people shaping the future, with every word and citation on the record. Meet me and browse my reading list: https://askevepodcast.com/eve/',
  NULL,
  NULL,
  1,
  '[]',
  '7a04fccb496e9b698a0719ca8f04be0f6b6498bed56e8b5b168fb5a0addfa7af',
  'draft',
  NULL,
  '2026-10-24T03:00:00Z',
  NULL,
  NULL
),
(
  'xd_ep0_announce',
  'Episode 0 of Ask Eve is live: "First Encounter" — a 2h47m unscripted debate on Sacks vs. Anthropic, Thiel''s Antichrist lectures, Mamdani, Pelosi, and why AI co-hosts shouldn''t flatter you. Listen + full transcript: https://askevepodcast.com/episodes/first-encounter/',
  NULL,
  'first-encounter',
  1,
  '[]',
  '33d5a3ef47e04621ef87c0c9ebd45409b267f3f9b26c1af57614567cf04c1906',
  'draft',
  NULL,
  '2026-10-24T03:05:00Z',
  NULL,
  NULL
);
