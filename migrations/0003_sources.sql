-- Ask Eve — Sources & Receipts schema (Cloudflare D1 / SQLite)
-- Links curated primary documents, filings, and reporting to episodes, people,
-- topics (tags), and exact verbatim character spans in transcript turns.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS sources (
    id                 INTEGER PRIMARY KEY,
    episode_id         INTEGER NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
    episode_slug       TEXT NOT NULL DEFAULT '',
    sort_order         INTEGER NOT NULL DEFAULT 0,
    title              TEXT NOT NULL,
    url                TEXT NOT NULL,
    publisher          TEXT,
    domain             TEXT,
    published_date     TEXT,
    description        TEXT NOT NULL DEFAULT '',
    quote              TEXT,
    turn_index         INTEGER CHECK (turn_index IS NULL OR turn_index >= 0),
    timestamp_seconds  REAL CHECK (timestamp_seconds IS NULL OR timestamp_seconds >= 0),
    timestamp_label    TEXT,
    http_status        INTEGER,
    verified_at        TEXT,
    created_at         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE (episode_id, url)
);
CREATE INDEX IF NOT EXISTS idx_sources_episode ON sources(episode_id, sort_order);
CREATE INDEX IF NOT EXISTS idx_sources_episode_slug ON sources(episode_slug, sort_order);

CREATE TABLE IF NOT EXISTS source_people (
    source_id  INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    person_id  INTEGER NOT NULL REFERENCES people(id)  ON DELETE CASCADE,
    PRIMARY KEY (source_id, person_id)
);
CREATE INDEX IF NOT EXISTS idx_source_people_person ON source_people(person_id);

CREATE TABLE IF NOT EXISTS source_tags (
    source_id  INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    tag_id     INTEGER NOT NULL REFERENCES tags(id)    ON DELETE CASCADE,
    PRIMARY KEY (source_id, tag_id)
);
CREATE INDEX IF NOT EXISTS idx_source_tags_tag ON source_tags(tag_id);

CREATE TABLE IF NOT EXISTS source_spans (
    id            INTEGER PRIMARY KEY,
    source_id     INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    turn_index    INTEGER NOT NULL CHECK (turn_index >= 0),
    start_offset  INTEGER NOT NULL CHECK (start_offset >= 0),
    end_offset    INTEGER NOT NULL CHECK (end_offset > start_offset),
    quote         TEXT NOT NULL CHECK (length(quote) > 0),
    UNIQUE (source_id, turn_index, start_offset, end_offset)
);
CREATE INDEX IF NOT EXISTS idx_source_spans_source ON source_spans(source_id, turn_index, start_offset);
