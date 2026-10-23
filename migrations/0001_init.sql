-- migrations/0001_init.sql  (Cloudflare D1 / SQLite). Apply: wrangler d1 migrations apply ask-eve-meta --remote
-- Git is canonical for transcripts (docs/audio-pipeline.md §0); D1 indexes them for the site/feed.
-- D1 enforces foreign keys by default (tests run SQLite with PRAGMA foreign_keys=ON).
CREATE TABLE episodes (
  id                INTEGER PRIMARY KEY,
  slug              TEXT NOT NULL UNIQUE,              -- 'eve-david-podcast-planning'
  guid              TEXT NOT NULL UNIQUE,              -- RSS <guid>, never changes once published
  date              TEXT NOT NULL,                     -- recording date, YYYY-MM-DD
  title             TEXT NOT NULL,
  summary           TEXT,                              -- ~200-word show note (plain text)
  episode_type      TEXT NOT NULL DEFAULT 'full' CHECK (episode_type IN ('full','trailer','bonus')),
  season            INTEGER,
  episode_number    INTEGER,
  explicit          INTEGER NOT NULL DEFAULT 0 CHECK (explicit IN (0,1)),
  language          TEXT NOT NULL DEFAULT 'en',
  source_url        TEXT,                              -- grok.com/share/... link
  session_started_at TEXT,                             -- ISO-8601 UTC of first turn
  session_ended_at  TEXT,                              -- ISO-8601 UTC of last turn
  turn_count        INTEGER,
  transcript_path   TEXT NOT NULL,                     -- repo-relative path; git is canonical (§0)
  transcript_sha256 TEXT NOT NULL CHECK (length(transcript_sha256) = 64),
  transcript_bytes  INTEGER,
  audio_key         TEXT,                              -- R2 key in public bucket; NULL until produced
  audio_url         TEXT,                              -- https://audio.askevepodcast.com/<audio_key>
  audio_bytes       INTEGER,                           -- <enclosure length>
  audio_sha256      TEXT,
  audio_mime        TEXT DEFAULT 'audio/mpeg',
  duration_seconds  INTEGER,
  loudness_lufs     REAL,                              -- measured integrated loudness of final MP3
  image_url         TEXT,                              -- per-episode art (optional)
  -- recording provenance (§1/§10). Default path since 10:34 UTC: David-only phone capture + Eve re-voiced
  -- with xAI TTS ('mic-plus-tts'). Text-only episodes such as episode 1 set 'none' explicitly.
  recording_method  TEXT NOT NULL DEFAULT 'mic-plus-tts' CHECK (recording_method IN
                      ('mic-plus-tts','grok-share-export','lav-plus-tts','reread-plus-tts','tts-reconstructed','ios-screen-record','none')),
  recording_device  TEXT,                              -- free text, e.g. 'iPhone 16 Pro + AirPods Pro 2'
  recording_environment TEXT CHECK (recording_environment IN ('indoor','walk-outdoor','vehicle','other')),
  raw_audio_key     TEXT,                              -- ask-eve-masters key, e.g. raw/<date>_<slug>/phone.mp4 (private bucket)
  raw_audio_format  TEXT CHECK (raw_audio_format IN ('mp4','mov','m4a','wav','mp3')),
  raw_audio_bytes   INTEGER,
  raw_audio_sha256  TEXT CHECK (raw_audio_sha256 IS NULL OR length(raw_audio_sha256) = 64),
  recording_started_at TEXT,                           -- ISO-8601 UTC from com.apple.quicktime.creationdate (coarse)
  sync_offset_seconds REAL,                            -- raw-audio time (s) at which turn 0 starts
  alignment_method  TEXT CHECK (alignment_method IN ('asr-david-gaps','asr-fuzzy','linear-fit','creationdate-only','tts-exact')),
  alignment_path    TEXT,                              -- repo path of the alignment JSON (time map lives there)
  alignment_coverage REAL CHECK (alignment_coverage IS NULL OR (alignment_coverage >= 0 AND alignment_coverage <= 1)),
  asr_model         TEXT,                              -- e.g. 'faster-whisper small int8' (alignment only, never published text)
  tts_voice         TEXT,                              -- e.g. 'xai_tts voice=eve' for mic-plus-tts
  narrator_voice    TEXT,                              -- e.g. 'xai_tts voice=rex' (David's lines, tts-reconstructed only)
  ai_reconstructed  INTEGER NOT NULL DEFAULT 0 CHECK (ai_reconstructed IN (0,1)),
  ai_disclosure     TEXT,                              -- shown in show notes + feed description; spoken at 00:00
  processing_chain  TEXT,                              -- exact ffmpeg -af string used for the master
  status            TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','published','hidden')),
  published_at      TEXT,                              -- ISO-8601 UTC; drives <pubDate>
  created_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
  updated_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
  CHECK (status <> 'published' OR (audio_url IS NOT NULL AND audio_bytes > 0 AND published_at IS NOT NULL)),
  -- fully synthetic episodes must carry the AI-reconstructed label (10:43 UTC decision)
  CHECK (recording_method <> 'tts-reconstructed' OR (ai_reconstructed = 1 AND ai_disclosure IS NOT NULL AND length(ai_disclosure) > 0)),
  CHECK (ai_reconstructed = 0 OR (ai_disclosure IS NOT NULL AND length(ai_disclosure) > 0))
);
CREATE INDEX idx_episodes_status_pub ON episodes(status, published_at DESC);
CREATE INDEX idx_episodes_date ON episodes(date);

CREATE TABLE people (
  id      INTEGER PRIMARY KEY,
  slug    TEXT NOT NULL UNIQUE,                        -- 'david-sacks'
  name    TEXT NOT NULL UNIQUE,
  stance  TEXT                                         -- Eve's current one-line stance (index/people.md)
);

CREATE TABLE episode_people (
  episode_id  INTEGER NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
  person_id   INTEGER NOT NULL REFERENCES people(id)   ON DELETE RESTRICT,
  stance      TEXT,                                    -- stance as of this episode (snapshot)
  first_mention_seconds INTEGER,                       -- audio offset, for "jump to" links
  PRIMARY KEY (episode_id, person_id)
);
CREATE INDEX idx_episode_people_person ON episode_people(person_id);

CREATE TABLE tags (
  id    INTEGER PRIMARY KEY,
  kind  TEXT NOT NULL CHECK (kind IN ('topic','company','place','other')),
  value TEXT NOT NULL,
  UNIQUE (kind, value)
);

CREATE TABLE episode_tags (
  episode_id INTEGER NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
  tag_id     INTEGER NOT NULL REFERENCES tags(id)     ON DELETE RESTRICT,
  PRIMARY KEY (episode_id, tag_id)
);
CREATE INDEX idx_episode_tags_tag ON episode_tags(tag_id);

CREATE TABLE chapters (
  id            INTEGER PRIMARY KEY,
  episode_id    INTEGER NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
  language      TEXT NOT NULL DEFAULT 'en',
  start_seconds REAL NOT NULL CHECK (start_seconds >= 0),
  title         TEXT NOT NULL,
  href          TEXT,
  UNIQUE (episode_id, language, start_seconds)
);

CREATE TABLE turn_offsets (                          -- one row per transcript turn; align_audio.py + revoice_eve.py via d1_upsert.py
  episode_id      INTEGER NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
  turn_index      INTEGER NOT NULL CHECK (turn_index >= 0), -- 0-based order in the transcript file
  speaker         TEXT NOT NULL CHECK (speaker IN ('David','Eve')),
  created_at      TEXT,                                -- share-JSON createTime (UTC)
  raw_start_seconds    REAL CHECK (raw_start_seconds IS NULL OR raw_start_seconds >= 0),   -- David-only phone audio
  raw_end_seconds      REAL,
  edited_start_seconds REAL CHECK (edited_start_seconds IS NULL OR edited_start_seconds >= 0), -- published MP3, after the time map
  edited_end_seconds   REAL,
  method          TEXT NOT NULL CHECK (method IN ('asr-match','sync-phrase','gap-placed','interpolated','tts-placed','tts-synthetic','fit')),
  score           REAL CHECK (score IS NULL OR (score >= 0 AND score <= 100)),  -- fuzzy score; asr-match/sync-phrase only
  PRIMARY KEY (episode_id, turn_index),
  CHECK (raw_end_seconds IS NULL OR raw_start_seconds IS NULL OR raw_end_seconds >= raw_start_seconds),
  CHECK (edited_end_seconds IS NULL OR edited_start_seconds IS NULL OR edited_end_seconds >= edited_start_seconds),
  CHECK (score IS NULL OR method IN ('asr-match','sync-phrase')),
  CHECK (method <> 'gap-placed' OR speaker = 'Eve'),
  CHECK (method <> 'tts-placed' OR speaker = 'Eve')
);
CREATE INDEX idx_turn_offsets_edited ON turn_offsets(episode_id, edited_start_seconds);

CREATE TABLE translations (
  episode_id        INTEGER NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
  language          TEXT NOT NULL,                     -- 'es', 'de', ...
  title             TEXT NOT NULL,
  summary           TEXT,
  transcript_path   TEXT NOT NULL,                     -- repo path, e.g. episodes/es/<stem>.md (git canonical)
  transcript_sha256 TEXT NOT NULL CHECK (length(transcript_sha256) = 64),
  audio_key         TEXT,
  audio_url         TEXT,
  audio_bytes       INTEGER,
  duration_seconds  INTEGER,
  voice_method      TEXT,                              -- e.g. 'xai_tts:eve+custom:david'
  human_reviewed    INTEGER NOT NULL DEFAULT 0 CHECK (human_reviewed IN (0,1)),
  status            TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','published','hidden')),
  published_at      TEXT,
  updated_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
  PRIMARY KEY (episode_id, language)
);
CREATE INDEX idx_translations_lang_status ON translations(language, status);
