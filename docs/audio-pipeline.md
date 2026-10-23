# Ask Eve: audio pipeline design

Status: design draft, 2026-10-23. Nothing described here exists in the cloud yet. There is no bucket, no database, no token, no Netlify site, and no feed.
Scope: how a recorded Eve–David voice session becomes a transcript, an MP3, metadata, a website, and a Spotify-listed RSS feed.
Conventions: steps tagged **[DAVID]** need David (accounts, tokens, domain, cover art, audio, judgment calls). Untagged steps are code or ops work that Eve or any builder can do. `<domain>` is the not-yet-chosen show domain. `podcast@<domain>` is a placeholder owner email.

Decisions this doc follows, from the episode-1 transcript (`episodes/2026-10-23_eve-david-podcast-planning.md`). Where a decision was later superseded, the later one wins:

| Time (UTC) | Decision | Notes |
|---|---|---|
| 09:17 | "Voice mode doesn't export"; capture system audio (BlackHole/VoiceMeeter); fall back to regenerated voices | The same turn suggested Spotify for Podcasters hosting with auto-RSS. That is **superseded** by 09:37–09:41 (self-host on R2 plus our own RSS). The ElevenLabs fallback is replaced here by xAI TTS, which has a native `eve` voice (§1.3). |
| 09:22 | Audio-only first; YouTube later | |
| 09:23, 09:28 | Show name **Ask Eve** | Supersedes "Raw Signal". |
| 09:27 | Record raw and cut only pauses. English keeps David's real voice. Other languages use AI voices for both speakers | |
| 09:18–09:19 | Translations per language; a human pass only for languages David reads | |
| 09:37–09:38 | Open-source all code. **David does not want episodes in a git repo.** Audio goes to S3-like storage. Preserve original content | See §0 for the tension with the committed transcript. |
| 09:41 | Storage = **Cloudflare R2 (audio) + D1 (metadata)**. A scoped API token lives in an env var and is never committed. Search stays local (D1 can't run sqlite-vec/FTS5 the same way) | Supersedes README.md's "S3/R2 plus Postgres". README needs a one-line update. |
| 09:42 | David creates the Cloudflare credentials himself | Eve can't create Cloudflare credentials (§8). |
| 09:43–09:44, 09:47 | Boot script and cutover. Eve owns transcripts going forward. Episode 1 transcript saved and committed | |

---

## 0. Where transcripts canonically live (the tension)

The facts:
- At 09:38 David said he doesn't want episodes in a git repo.
- At 09:47 the episode-1 transcript was committed (`cce3971`). The repo is public (`github.com/daveey/ask-eve-memory`), so the file is in public history whether or not it gets deleted later.
- Today `memory/boot.py`, the search index, and `site/README.md` all assume `episodes/*.md` exists on local disk.

**Recommendation (David to confirm) [DAVID]:**
1. **The canonical store is R2 + D1, not git.**
   - The original artifacts (share JSON, raw recordings) go to the private bucket, under `raw/…`.
   - The published transcript goes to the public bucket at `transcripts/en/<date>_<slug>.md`.
   - D1 records `transcript_sha256` so any copy can be checked against the canonical one.
2. **`episodes/` becomes a local working copy.**
   - Add `episodes/*.md` (keeping `episodes/README.md`) to `.gitignore`.
   - Add `scripts/sync_episodes.py pull|push`, which copies between R2 and `episodes/`.
   - `boot.py` and the search index keep working unchanged because they still read local files.
   - The Netlify build runs `sync_episodes.py pull` against the public transcript URLs. It needs no credentials, because published transcripts are public anyway.
3. **Don't rewrite history.** Episode 1 is already public, and a force-push would break clones while gaining nothing. Once R2 holds the file and its sha matches, run `git rm --cached episodes/2026-10-23_eve-david-podcast-planning.md`. Until then, leave the repo as it is.
4. Interim: until R2 exists, the repo copy *is* the canonical copy. Everything below works either way, because consumers only see `transcript_path` plus `transcript_sha256`.

The alternative is to keep transcripts in git permanently. That is simpler: no sync step, and the site builds straight from the repo. It does contradict 09:38. If David now prefers it, drop step 2 and keep everything else.

---

## Build order (what a solo builder does, in order)

1. **[DAVID]** Pick and buy a domain, or choose a subdomain of one he owns. Add it as a zone in Cloudflare; the R2 custom domain must be a zone in the same account (§3.4). Decide `<domain>`.
2. **[DAVID]** Create the Cloudflare tokens and the Netlify site/hook (§8 table). Put them in GitHub secrets and Netlify env vars. Locally they go in a gitignored `.env`.
3. **[DAVID]** Create the buckets and D1 database, using the commands in §3.4 and §4. They're cheap and reversible, but they are cloud resources, so David runs or approves them.
4. Write `scripts/ingest_share.py` (§2). Test it against episode 1 (expected sha in §9).
5. Write `migrations/0001_init.sql` (§4) and apply it.
6. Write `scripts/produce_audio.py` and `scripts/upload_r2.py` (§3).
7. Build the Astro site and `feed.xml` endpoint (§5, §6). Deploy to Netlify.
8. **[DAVID]** Cover art at 3000×3000 (§6.2). Show description. Category choice.
9. **[DAVID]** Record the first real audio. This can be a trailer or the next session (§1, §9.4).
10. Run the dry run (§9), then publish via GitHub Actions (§8).
11. Validate the feed (§6.5). **[DAVID]** Submit to Spotify for Creators and enter the 8-digit code (§6.6).

---

## 1. Recording

### 1.1 What Grok gives us today (research)

| Source | Audio? | Status |
|---|---|---|
| Share link JSON `GET https://grok.com/rest/app-chat/share_links/<share_id>` | **No.** It has text turns, `createTime`, and `sender`. Every response has `mediaTypes: ["audio"]` but empty `fileUris`/`fileAttachments`, and the JSON has no audio URL or voice field anywhere | **Verified** 2026-10-23 on episode 1, both on the saved copy and on a live read-only fetch (HTTP 200, `application/json`, 304 turns). |
| Grok voice mode in-app | Eve said at 09:17 "voice mode doesn't export"; at 09:49 the transcript couldn't be exported from the session either | Transcript claim. |
| Grok iOS "share voice conversations as videos or podcasts" | Possibly. An @grok post (reported Feb 12, 2026, https://www.eonmsk.com/2026/02/12/xai-grok-voice-share-conversations/) says: "Share your voice conversations as videos or podcasts. Try it now on iOS!" It's described as a video replay or audio-only "podcast" export, of a clip or the full conversation | **Unverified.** Unknown: whether it includes David's real mic audio or re-synthesized audio, the maximum length, the file format, whether the output is a downloadable file or only a link, and whether it works on sessions held on web/desktop. **Test on a 2-minute session before relying on it.** |

Conclusion: we must **capture audio ourselves** while the session runs. The share JSON stays the transcript source (§2).

### 1.2 Primary capture path: Mac, headphones, two tracks

Two separate tracks (Eve's output and David's mic) make "cut only pauses" simple, keep levels balanced, and leave no echo. **David must wear headphones** so the mic doesn't pick up Eve.

One-time setup **[DAVID]** (macOS):
1. Install the BlackHole 2ch virtual audio driver (`brew install blackhole-2ch`, then reboot or log out).
2. Audio MIDI Setup → "+" → **Create Multi-Output Device** containing the headphones and BlackHole 2ch. Set it as the system output, so you hear Eve and BlackHole receives her too.
3. Run Grok voice in the browser or Mac app, with output going to the Multi-Output device.
4. Find device indexes: `ffmpeg -f avfoundation -list_devices true -i ""`.

Per-session capture, sketched as `scripts/record.sh`:
```bash
SESSION=$(date -u +%Y-%m-%d)_<slug>; mkdir -p "rec/$SESSION"
date -u +%Y-%m-%dT%H:%M:%S.%3NZ > "rec/$SESSION/T0.txt"   # GNU date; on macOS use: python3 -c 'import datetime;print(datetime.datetime.utcnow().isoformat()+"Z")'
ffmpeg -hide_banner \
  -f avfoundation -i ":<BlackHole index>" \
  -f avfoundation -i ":<mic index>" \
  -map 0:a -c:a pcm_s24le -ar 48000 "rec/$SESSION/eve.wav" \
  -map 1:a -c:a pcm_s24le -ar 48000 "rec/$SESSION/david.wav"
```
- `T0.txt` is the wall-clock UTC start. Share-JSON `createTime` values are also UTC, so a turn's approximate audio offset is `createTime − T0`. That is how chapters and "jump to" links get computed. **Unverified:** whether `createTime` marks the start or end of an utterance. Calibrate on the first session; ±a few seconds is fine for chapters.
- Say "marker" or clap at the start. This gives a sync point if the two tracks drift.
- At the end, do the share flow in Grok and run `ingest_share.py` (§2).
- Upload the raw WAVs untouched to the **private** bucket (`raw/…`, §7). This is the "original content preserved" requirement.

Windows: VoiceMeeter (from 09:17) does the same job as BlackHole. iPhone-only sessions: **unverified** whether iOS screen recording captures both Grok output and the mic during a voice session. Test it, and otherwise use a Mac.

### 1.3 Fallback when there is no recording (applies to episode 1)

In order of preference:
1. **Try the Grok iOS podcast/video share** on the session (§1.1). **[DAVID]**, since it needs his iPhone and account. If the export contains real audio, run it through `produce_audio.py` as a single-track master.
2. **David re-reads his lines, and Eve's lines go through TTS.** This keeps David's real voice (09:27).
   - Eve: xAI TTS, `POST https://api.x.ai/v1/tts`, `voice_id: "eve"`, MP3 or WAV output. The limit is 60,000 characters per request; the WebSocket at `wss://api.x.ai/v1/tts` has no limit; `with_timestamps` is available. Source: https://docs.x.ai/developers/model-capabilities/audio/text-to-speech. Needs `XAI_API_KEY` **[DAVID]**.
   - Generate one clip per Eve turn, named `eve_<index>.wav`. David records `david_<index>.wav` from a teleprompter list of his 152 turns. Concatenate in turn order with 0.4 s gaps.
3. **Both voices via TTS.** Eve uses `eve`; David uses a custom cloned voice (xAI Custom Voices; consent and a reference clip from David **[DAVID]**). This contradicts "English keeps David's real voice". Use it only if David explicitly OKs it, and label the episode "voices reconstructed by AI from the text transcript".

Non-English editions (09:27) always use path 3: Eve's voice plus David's cloned voice, reading the translated transcript. xAI TTS supports `es-ES`/`es-MX`, `de`, and others per the docs above.

---

## 2. `scripts/ingest_share.py`: share URL to `episodes/<date>_<slug>.md`

**Format note:** `episodes/README.md` documents `[00:00:00] **David:** …` (bold names, offsets). The actual episode-1 file uses **plain** `[HH:MM:SS] David: …`, where the time is the **UTC wall-clock** from `createTime`, not an audio offset. `boot.py`'s regex accepts both. The script follows the **actual file**. Fix `episodes/README.md` to match.

CLI:
```
python scripts/ingest_share.py <https://grok.com/share/<id> | path/to/share.json> \
    --slug eve-david-podcast-planning --title "Eve-David podcast planning session" \
    [--people "David Sacks,Marc Andreessen,..."] [--out episodes/] [--raw-dir rec/<date>_<slug>/] [--force]
```

Behavior:
1. Take `share_id` = last path segment of the URL, then `curl -sS -A "Mozilla/5.0" https://grok.com/rest/app-chat/share_links/<share_id>` (or Python `urllib` with the same UA). This is read-only. Save the raw bytes to `<raw-dir>/share.json` for the private bucket.
2. `r = sorted(d["responses"], key=lambda x: x["createTime"])`. Fail if `sender` is anything other than `human` or `assistant`.
3. Render each turn as `[{createTime[11:19]}] {David|Eve}: {message.strip()}`, join the turns with `\n\n`, and add a trailing `\n`. Keep empty turns: an empty Eve turn renders as `[08:21:13] Eve: ` with a trailing space, and that is correct.
4. The front matter has exactly these keys in this order:
   ```
   ---
   date: {first createTime[:10]}
   title: {--title}
   people: [{comma-space joined}]
   source: https://grok.com/share/{share_id}
   note: Verbatim export of the shared Grok voice session ({N} turns, {first HH:MM}-{last HH:MM} UTC). Human turns labeled David, assistant turns labeled Eve. Text unedited, ASR artifacts kept.
   ---
   ```
   The file is `front_matter + "\n" + body`.
5. `--people`: if omitted, suggest names from `index/people.md` that appear with word boundaries, in table order, and **print them for confirmation**. Short names like "Chan" create false positives, so don't auto-commit them.
6. Refuse to overwrite an existing file whose sha256 differs unless `--force` is given. Print `path`, `sha256`, `bytes`, `turns`, `session_started_at`, `session_ended_at`; this is the JSON that §4's upsert consumes.
7. Don't use the Grok `conversation.title` as the episode title. For episode 1 it's "Andreessen and Horowitz Political Shift Narrative"; the episode title was chosen by hand.

**Verified:** this logic, applied to episode 1's share JSON with the title and people from the existing front matter, reproduces the committed file **byte for byte** (sha256 `15d413f1…adcea4`, 151,711 bytes).
Test: `tests/test_ingest_share.py` with a tiny synthetic share JSON (3 turns, including one empty one). Add an optional test, skipped if `../share.json` is absent, that asserts the episode-1 sha. Don't commit the 691 KB share JSON (§0).

---

## 3. Audio production

Script: `scripts/produce_audio.py <rec/<date>_<slug>/> --slug … [--mono]`. It wraps the ffmpeg commands below; all of them were tested with ffmpeg 7.1 on the box.

### 3.1 Edit: cut only pauses (09:27)
- Mix the tracks: `ffmpeg -i david.wav -i eve.wav -filter_complex "[0][1]amix=inputs=2:normalize=0[a]" -map "[a]" mix.wav`. Balance the tracks first if needed (`volume=` per input).
- Find long pauses: `ffmpeg -i mix.wav -af silencedetect=n=-45dB:d=1.5 -f null -`. Parse `silence_start`/`silence_end`.
- Build a keep-list that shortens every pause longer than 1.5 s to 0.7 s, and render it with `atrim`/`concat`. **Store the cut map** (`[(orig_t, new_t)]`) so chapter and turn offsets computed from `createTime − T0` can be remapped. A one-liner `silenceremove=stop_periods=-1:stop_duration=1.2:stop_threshold=-45dB:stop_silence=0.7` also works (tested: a 4 s gap became about 1.9 s), but it produces no cut map, so use it only when chapters don't matter.
- Nothing else gets edited. Save `master.wav` and upload it to private `masters/…`.

### 3.2 Loudness: two-pass loudnorm
Targets: **-16 LUFS integrated for stereo, -19 LUFS for mono**, true peak -1.5 dBTP, LRA 11. These are common podcast guidance figures; Apple's published recommendation is around -16 LKFS. The exact citation for the -19 mono figure was **not re-verified** here.
```bash
# pass 1: measure
ffmpeg -hide_banner -i master.wav -af loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json -f null - 2> ln.log
# pass 2: apply measured values (script parses the JSON block from ln.log)
loudnorm=I=-16:TP=-1.5:LRA=11:measured_I=<input_i>:measured_TP=<input_tp>:measured_LRA=<input_lra>:measured_thresh=<input_thresh>:offset=<target_offset>:linear=true
```
Recommendation: **stereo, -16 LUFS** by default. It's a single target, and every player handles it. Use `--mono` (`-ac 1`, `I=-19`) if file size ever matters. After encoding, check with `ffmpeg -i out.mp3 -af ebur128=peak=true -f null -` and store the measured value in `episodes.loudness_lufs`. In the box test, a 6 s tone landed at -16.4 LUFS. Real speech of a minute or more is measured more accurately.

### 3.3 Encode: MP3 128k CBR, 44.1 kHz, ID3 chapters
`chapters.txt` is generated from the `chapters` rows (ffmetadata format, ms timebase):
```
;FFMETADATA1
title=Eve-David podcast planning session
artist=Ask Eve
[CHAPTER]
TIMEBASE=1/1000
START=0
END=312000
title=Cold open
```
```bash
ffmpeg -i master.wav -i chapters.txt -map 0:a -map_metadata 1 -map_chapters 1 \
  -af "<pass-2 loudnorm>,aresample=44100" -ar 44100 -ac 2 \
  -c:a libmp3lame -b:a 128k -id3v2_version 3 \
  out/<date>_<slug>.v1.mp3
ffprobe -v error -show_chapters -show_entries format=duration,size -of json out/<date>_<slug>.v1.mp3
```
Spotify's spec recommends MP3 at ≥128 kbps; MP3 is the only format that works for passthrough; the maximum length is 12 h (Spotify Podcast Delivery Specification v1.10, §6). Expect about 0.96 MB per minute; a 2 h 19 m session is about 134 MB.
Outputs: the MP3, `manifest.json` (`audio_bytes` from `stat`, `audio_sha256`, `duration_seconds` from ffprobe, rounded, `loudness_lufs`, chapters), and `chapters.json` (the same chapters, for the site).

### 3.4 Upload to R2, public URL, headers
One-time setup **[DAVID]**. These create cloud resources, so they are not run as part of this doc:
```bash
npx wrangler@4 login
npx wrangler@4 r2 bucket create ask-eve-public
npx wrangler@4 r2 bucket create ask-eve-masters        # stays private: no custom domain, no r2.dev
# Dashboard: R2 → ask-eve-public → Settings → Custom Domains → add audio.<domain>
```
- Buckets are private by default. **Use a custom domain, not `r2.dev`**: r2.dev is for development, is rate-limited (it can return 429), and has no Cloudflare cache. A custom domain must be a zone in the same Cloudflare account. Sources: https://developers.cloudflare.com/r2/buckets/public-buckets/ and https://developers.cloudflare.com/r2/platform/limits/.
- Through a custom domain, files up to 512 MB are cacheable on Free/Pro/Business (https://developers.cloudflare.com/cache/interaction-cloudflare-products/r2/). Episodes stay well under that.

Upload, option A (wrangler). **v4 defaults to local storage, so `--remote` is mandatory** (https://developers.cloudflare.com/workers/wrangler/migration/update-v3-to-v4/, flags per https://developers.cloudflare.com/workers/wrangler/commands/r2/):
```bash
npx wrangler@4 r2 object put "ask-eve-public/audio/en/2026-10-23_eve-david-podcast-planning.v1.mp3" \
  --file out/2026-10-23_eve-david-podcast-planning.v1.mp3 --remote \
  --content-type audio/mpeg --cache-control "public, max-age=31536000, immutable"
```
Option B (aws CLI with the R2 S3 API; region `auto`; R2 API token with Object Read & Write; https://developers.cloudflare.com/r2/examples/aws/aws-cli/). This is what `upload_r2.py` and CI use:
```bash
aws s3 cp out/2026-10-23_eve-david-podcast-planning.v1.mp3 \
  s3://ask-eve-public/audio/en/2026-10-23_eve-david-podcast-planning.v1.mp3 \
  --endpoint-url "https://$CLOUDFLARE_ACCOUNT_ID.r2.cloudflarestorage.com" --region auto \
  --content-type audio/mpeg --cache-control "public, max-age=31536000, immutable" \
  --metadata sha256=<audio_sha256>
```
Public URL: `https://audio.<domain>/audio/en/2026-10-23_eve-david-podcast-planning.v1.mp3`.

Headers and rules:
- `Content-Type: audio/mpeg`.
- `Cache-Control: public, max-age=31536000, immutable`. This is safe because keys are **versioned** (`.v1`, `.v2`). Spotify won't re-download a changed file at an unchanged URL (spec v1.10), so a fix always means a new key plus a D1 update.
- Apple requires HEAD and byte-range support (https://podcasters.apple.com/support/823-podcast-requirements). Check after the first upload:
  `curl -sI "$URL"` should return `200` with `content-length` equal to `audio_bytes`, and `curl -s -r 0-1 -o /dev/null -w '%{http_code}' "$URL"` should print `206`.
- Add CORS on `ask-eve-public` (GET/HEAD from `https://<site>`) only if the site player uses Web Audio or a waveform. A plain `<audio>` element doesn't need it.

---

## 4. D1 schema: `migrations/0001_init.sql`

One-time setup **[DAVID]** (cloud resource):
```bash
npx wrangler@4 d1 create ask-eve-meta          # prints database_id
```
Add the binding to `wrangler.toml` at the repo root. `database_id` isn't a secret, so it can be committed:
```toml
name = "ask-eve"
compatibility_date = "2026-10-01"
[[d1_databases]]
binding = "DB"
database_name = "ask-eve-meta"
database_id = "<from d1 create>"
migrations_dir = "migrations"
```
Migrations are tracked in the `d1_migrations` table (https://developers.cloudflare.com/d1/reference/migrations/):
```bash
npx wrangler@4 d1 migrations create ask-eve-meta init     # creates migrations/0001_init.sql; paste schema below
npx wrangler@4 d1 migrations apply ask-eve-meta --local   # test against local SQLite first
npx wrangler@4 d1 migrations apply ask-eve-meta --remote
npx wrangler@4 d1 execute ask-eve-meta --remote --command "SELECT name FROM sqlite_master WHERE type='table'"
```
D1 enforces foreign keys by default, as if `PRAGMA foreign_keys = on` were set (https://developers.cloudflare.com/d1/sql-api/foreign-keys/).
The schema plus the §9 INSERTs were **tested on SQLite 3.46 on the box**, including the publish guard CHECK:

```sql
-- migrations/0001_init.sql  (Cloudflare D1 / SQLite)
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
  transcript_path   TEXT NOT NULL,                     -- repo-relative or R2 key of the .md
  transcript_sha256 TEXT NOT NULL CHECK (length(transcript_sha256) = 64),
  transcript_bytes  INTEGER,
  audio_key         TEXT,                              -- R2 key in public bucket; NULL until produced
  audio_url         TEXT,                              -- https://audio.<domain>/<audio_key>
  audio_bytes       INTEGER,                           -- <enclosure length>
  audio_sha256      TEXT,
  audio_mime        TEXT DEFAULT 'audio/mpeg',
  duration_seconds  INTEGER,
  loudness_lufs     REAL,                              -- measured integrated loudness of final MP3
  image_url         TEXT,                              -- per-episode art (optional)
  status            TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','published','hidden')),
  published_at      TEXT,                              -- ISO-8601 UTC; drives <pubDate>
  created_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
  updated_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
  CHECK (status <> 'published' OR (audio_url IS NOT NULL AND audio_bytes > 0 AND published_at IS NOT NULL))
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

CREATE TABLE translations (
  episode_id        INTEGER NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
  language          TEXT NOT NULL,                     -- 'es', 'de', ...
  title             TEXT NOT NULL,
  summary           TEXT,
  transcript_path   TEXT NOT NULL,                     -- transcripts/<lang>/<date>_<slug>.md
  transcript_sha256 TEXT NOT NULL CHECK (length(transcript_sha256) = 64),
  audio_key         TEXT,
  audio_url         TEXT,
  audio_bytes       INTEGER,
  duration_seconds  INTEGER,
  voice_method      TEXT,                              -- e.g. 'xai-tts:eve+custom:david'
  human_reviewed    INTEGER NOT NULL DEFAULT 0 CHECK (human_reviewed IN (0,1)),
  status            TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','published','hidden')),
  published_at      TEXT,
  updated_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
  PRIMARY KEY (episode_id, language)
);
CREATE INDEX idx_translations_lang_status ON translations(language, status);
```
Design notes:
- **Search stays local** (09:41). D1 holds only what the site and feed need. People and tags are mirrored from `index/people.md` and the front matter by `scripts/d1_upsert.py`.
- The publish CHECK means an episode can't be marked `published` without audio, so the feed can never contain an item without an enclosure.
- Backups: `npx wrangler@4 d1 export ask-eve-meta --remote --output backup.sql`, uploaded to private `backups/d1/`.

---

## 5. Netlify site: build-time D1 vs a Worker

| Option | How | Pros | Cons |
|---|---|---|---|
| **A. Astro static build that reads D1 via the REST API (recommended)** | At build, `POST https://api.cloudflare.com/client/v4/accounts/{account_id}/d1/database/{database_id}/query` with `{"sql","params"}` and a **read-only** D1 token (https://developers.cloudflare.com/api/resources/d1/subresources/database/methods/query/) | The output is plain static files: no runtime, no CORS, no token in the browser, and the feed is a static file. Netlify stays the host | Data changes need a rebuild. The build hook handles that (§8) |
| B. Cloudflare Worker JSON API (D1 binding), with the site fetching it | A Worker exposes `/api/episodes`; Astro fetches it at build or at runtime | Live data; the D1 binding is the intended path for request traffic | One more deployed service, and a public API to secure |
| C. Cloudflare Pages instead of Netlify | Same Astro build; Pages Functions get a D1 binding | One vendor; native D1 binding | Moves off Netlify, which was the stated default |

Why A: Cloudflare notes that the REST API "primarily interacts with the control plane" (https://developers.cloudflare.com/d1/best-practices/query-d1/). That makes it fine for a few queries per build, and wrong for per-visitor reads, which option A never makes. Moving to C later is a few hours of work, because the Astro code is identical.

Setup:
1. **[DAVID]** Create the Netlify site from the GitHub repo. Base directory `site`, build `npm ci && npm run build`, publish `dist`. The repo's `netlify.toml`:
   ```toml
   [build]
     base = "site"
     command = "npm ci && npm run build"
     publish = "dist"
   [[headers]]
     for = "/feed.xml"
     [headers.values]
       Content-Type = "application/rss+xml; charset=utf-8"
       Cache-Control = "public, max-age=300"
   ```
2. **[DAVID]** Netlify env vars (Site configuration → Environment variables): `CLOUDFLARE_ACCOUNT_ID`, `D1_DATABASE_ID`, and `D1_READ_TOKEN`. The last is a separate token with **D1 Read** only, so a build-log leak can't write.
3. Data loader `site/src/lib/d1.ts`:
   ```ts
   export async function q(sql: string, params: unknown[] = []) {
     const { CLOUDFLARE_ACCOUNT_ID: a, D1_DATABASE_ID: d, D1_READ_TOKEN: t } = process.env;
     const r = await fetch(`https://api.cloudflare.com/client/v4/accounts/${a}/d1/database/${d}/query`,
       { method: "POST", headers: { Authorization: `Bearer ${t}`, "Content-Type": "application/json" },
         body: JSON.stringify({ sql, params }) });
     const j = await r.json();
     if (!j.success) throw new Error(JSON.stringify(j.errors));   // fail the build, never ship an empty feed
     return j.result[0].results;
   }
   ```
4. Pages (from `site/README.md`): `/` (episode list), `/episodes/<slug>/` (player `<audio src=audio_url preload="none">`, chapter list, transcript rendered from the `.md`, people sidebar), `/people/<slug>/`, and `/feed.xml` (§6). The transcript comes from `episodes/` (interim) or from `sync_episodes.py pull` (§0). Each page checks `transcript_sha256` against D1 and fails the build on a mismatch.

---

## 6. RSS feed and Spotify for Creators

### 6.1 What generates it
Recommendation: the **Astro endpoint `site/src/pages/feed.xml.ts`**. In static mode Astro calls endpoints at build time and writes the output as a file (https://docs.astro.build/en/guides/endpoints/), so the feed becomes the static file `https://<site>/feed.xml` on Netlify. It uses the same D1 query as the pages: one build, one data source, and no Python needed on Netlify.
`scripts/build_feed.py` is not needed. If an offline check is wanted, a small Python test can parse `site/dist/feed.xml` after `npm run build`.

The feed URL is permanent once submitted. **Use the custom domain (`https://<domain>/feed.xml`) from day one, not `*.netlify.app`.** Changing it later requires Spotify's update-RSS flow (https://support.spotify.com/us/creators/article/updating-an-rss-feed-link-or-hosting-provider/) and redirects everywhere else.

### 6.2 Required content
Sources: Spotify Podcast Delivery Specification v1.10 (linked from https://support.spotify.com/us/creators/article/podcast-specification-doc/; PDF at https://assets.ctfassets.net/jtdj514wr91r/4r4op9KhH3fY1t3BKt2eiH/7b1b682acf4c41baf79f9574d6dedd7f/Podcast_Delivery_Specification_v1.10_-_master_doc.pdf), Apple requirements (https://podcasters.apple.com/support/823-podcast-requirements, https://help.apple.com/itc/podcasts_connect/en.lproj/itcb54353390.html), and Spotify's email article (https://support.spotify.com/us/creators/article/adding-your-email-address-to-your-rss-feed/).

- UTF-8 XML. Escape `&`, or wrap descriptions in CDATA.
- Namespaces: `itunes` (required by Spotify), `content`, and `psc` (`http://podlove.org/simple-chapters`, which Spotify supports in version 1.1 only).
- Channel: `title` (Ask Eve), `link`, `description`, `language` (en), `itunes:author`, `itunes:image href` (3000×3000), `itunes:category`, `itunes:explicit`, `itunes:type` (episodic), and **`itunes:owner` → `itunes:name` + `itunes:email`**. Spotify sends its claim code to the address in `<itunes:email>` (verified in Spotify's articles). Apple lists `itunes:owner` as optional, but we need it.
- Item: `title`, **`guid` (stable, `isPermaLink="false"`)**, `pubDate` (RFC 2822), **`enclosure url length type`** (`length` is the byte count from D1; Spotify treats it as optional, but always fill it), `itunes:duration` (seconds or HH:MM:SS), `itunes:episodeType`, `itunes:explicit`, `description`/`content:encoded` (show note plus a link to the transcript page), and `psc:chapters version="1.1"`.
- Artwork **[DAVID]**: square, 1400–3000 px (use 3000), JPEG or PNG, RGB, no alpha, and the file extension must match the format (Apple). Spotify asks for square, highest resolution. Store it at public `art/cover-3000.v1.jpg`.
- Episode 1 contains profanity (for example "piece-of-shit" at line 355 and "fucking" at line 641), so set `explicit = true`.
- AI disclosure: state in the channel description that Eve is an AI host. This is a recommendation, not a platform requirement I verified.

Item sketch, emitted by the endpoint from the D1 rows:
```xml
<item>
  <title>Eve-David podcast planning session</title>
  <guid isPermaLink="false">ask-eve:2026-10-23_eve-david-podcast-planning</guid>
  <pubDate>Fri, 23 Oct 2026 12:00:00 +0000</pubDate>
  <enclosure url="https://audio.&lt;domain&gt;/audio/en/2026-10-23_eve-david-podcast-planning.v1.mp3" length="<audio_bytes>" type="audio/mpeg"/>
  <itunes:duration><duration_seconds></itunes:duration>
  <itunes:episodeType>full</itunes:episodeType>
  <itunes:explicit>true</itunes:explicit>
  <description><![CDATA[<show note> Transcript: https://<domain>/episodes/eve-david-podcast-planning/]]></description>
  <psc:chapters version="1.1"><psc:chapter start="00:00:00" title="Cold open"/></psc:chapters>
</item>
```
Channel owner block: `<itunes:owner><itunes:name>Ask Eve</itunes:name><itunes:email>podcast@&lt;domain&gt;</itunes:email></itunes:owner>`.

### 6.3 Owner email [DAVID]
The email in the feed is **public**. Spotify notes that verification emails "sometimes … don't make it through" to custom or company domains, and suggests Gmail or Outlook (https://support.spotify.com/us/creators/article/not-receiving-verification-email/). The plan:
1. Use `podcast@<domain>`, routed to an inbox David reads (Cloudflare Email Routing or similar). Don't put David's personal Gmail in the feed.
2. If the code doesn't arrive within about 4 hours, temporarily switch `itunes:email` to a **new dedicated** Gmail or Outlook address made for the show, claim the show, then switch back if desired. Spotify says a feed change can take "a couple of hours" to be seen.

### 6.4 Spotify ingestion behavior to design around (spec v1.10)
- Spotify polls several times per hour and respects ETag/Last-Modified, so Netlify's static ETags are fine. Allow about an hour for changes to show up.
- **The guid must never change.** It's derived from `<date>_<slug>` and stored in D1.
- Changing an MP3 at the same URL won't be re-fetched. Fixes always use a new key (`.v2.mp3`).

### 6.5 Validate before submitting
1. `curl -sI https://<domain>/feed.xml` should return 200 with `content-type: application/rss+xml`. Then `curl -s https://<domain>/feed.xml | xmllint --noout -`.
2. Run the feed through https://podba.se/validate/ and https://www.castfeedvalidator.com/. Apple Podcasts Connect also validates on submit (https://podcasters.apple.com/support/829-validate-your-podcast).
3. Run the `curl -sI` and `curl -r 0-1` checks from §3.4 on every enclosure.

### 6.6 Submit and verify on Spotify for Creators [DAVID]
Per https://support.spotify.com/us/creators/article/claiming-your-podcast-on-spotify-for-creators/:
1. The feed has at least one published item with an enclosure. **Unverified for Spotify specifically.** Apple and most directories require it, so don't submit an empty feed.
2. Go to creators.spotify.com and log in with any Spotify account. Choose **Find an existing show → Somewhere else** and enter `https://<domain>/feed.xml`. If you already have a show there, use avatar → **Add a new show** (https://support.spotify.com/us/creators/article/adding-a-new-show-to-your-spotify-for-creators-account/).
3. Spotify emails an 8-digit code, from `noreply@hello.creators.spotify.com`, to the `<itunes:email>` address. Paste it in. Check spam. If it never arrives, see §6.3.
4. A show can only be claimed once, so do it from the account David wants to own long-term.
5. Later feed URL changes: https://support.spotify.com/us/creators/article/updating-an-rss-feed-link-or-hosting-provider/.

---

## 7. R2 key structure

Public access in R2 is per **bucket** (custom domain or r2.dev), not per prefix. So "private prefixes" live in a **separate private bucket**. This is a design inference from the R2 public-bucket docs: one bucket's custom domain exposes every key in it. A WAF rule could block a prefix on the public bucket instead, but a separate bucket is simpler and safer.

`ask-eve-public` (custom domain `audio.<domain>`; everything here is world-readable):
```
audio/<lang>/<date>_<slug>.v<N>.mp3          # enclosures; immutable, new N per fix
transcripts/<lang>/<date>_<slug>.md          # published transcripts (canonical per §0)
chapters/<lang>/<date>_<slug>.v<N>.json      # chapters for the site player
art/cover-3000.v<N>.jpg                      # show art
art/episodes/<date>_<slug>.v<N>.jpg          # optional episode art
```
`ask-eve-masters` (no custom domain, r2.dev disabled; S3-API credentials only):
```
raw/<date>_<slug>/eve.wav, david.wav, T0.txt # untouched captures ("original content preserved")
raw/<date>_<slug>/share.json                 # Grok share JSON as fetched
masters/<date>_<slug>/master.wav             # pause-cut, pre-loudnorm
masters/<date>_<slug>/cutmap.json            # orig→edited time map
drafts/<lang>/<date>_<slug>.md               # unreviewed translations
backups/d1/<YYYY-MM-DD>.sql                  # wrangler d1 export
```
Episode 1's key is `2026-10-23_eve-david-podcast-planning`, the same stem as the episode file.

---

## 8. Scripts, GitHub Actions, secrets

Scripts. All are sketches to write: Python 3, stdlib plus `boto3` for R2 if wanted, otherwise shelling out to `aws`, `ffmpeg`, and `npx wrangler@4`:

| Script | Runs where | Does |
|---|---|---|
| `scripts/record.sh` | David's Mac | §1.2 two-track capture plus T0 |
| `scripts/ingest_share.py` | local | §2 |
| `scripts/produce_audio.py` | local | §3.1–3.3: master, MP3, `manifest.json` |
| `scripts/upload_r2.py` | local | raw/masters to the private bucket; MP3, transcript, and chapters to the public bucket (§3.4, §7) |
| `scripts/d1_upsert.py` | local or CI | Upserts episodes, people, tags, and chapters from the front matter plus `manifest.json`, using `wrangler d1 execute --remote --file` or the REST API. Always `status='draft'` |
| `scripts/sync_episodes.py` | local and Netlify | §0 pull/push of transcripts |

The workflow `.github/workflows/publish.yml` is the only place an episode gets flipped to `published`:
```yaml
name: publish
on:
  workflow_dispatch:
    inputs:
      slug: { description: "episode slug", required: true }
  push:
    branches: [main]
    paths: ["migrations/**"]
permissions: { contents: read }
concurrency: publish
jobs:
  publish:
    runs-on: ubuntu-latest
    environment: production          # optional: require David's approval
    env:
      CLOUDFLARE_API_TOKEN: ${{ secrets.CLOUDFLARE_API_TOKEN }}
      CLOUDFLARE_ACCOUNT_ID: ${{ secrets.CLOUDFLARE_ACCOUNT_ID }}
      AWS_ACCESS_KEY_ID: ${{ secrets.R2_ACCESS_KEY_ID }}
      AWS_SECRET_ACCESS_KEY: ${{ secrets.R2_SECRET_ACCESS_KEY }}
      AWS_DEFAULT_REGION: auto
      SLUG: ${{ inputs.slug }}
    steps:
      - uses: actions/checkout@v4
      - run: npx wrangler@4 d1 migrations apply ask-eve-meta --remote
      - if: github.event_name == 'workflow_dispatch'
        run: |
          [[ "$SLUG" =~ ^[a-z0-9-]+$ ]] || { echo "bad slug"; exit 1; }
          python3 scripts/publish_check.py "$SLUG"   # reads D1 row; HEAD + Range checks on audio_url; size == audio_bytes; head-object sha256 metadata == audio_sha256
          npx wrangler@4 d1 execute ask-eve-meta --remote --command \
            "UPDATE episodes SET status='published', published_at=strftime('%Y-%m-%dT%H:%M:%SZ','now'), updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE slug='$SLUG' AND status='draft'"
      - run: curl -fsS -X POST -d '{}' "${{ secrets.NETLIFY_BUILD_HOOK }}?trigger_title=publish+${SLUG:-migrations}"
      - if: github.event_name == 'workflow_dispatch'
        run: npx wrangler@4 d1 export ask-eve-meta --remote --output backup.sql && aws s3 cp backup.sql "s3://ask-eve-masters/backups/d1/$(date -u +%F).sql" --endpoint-url "https://$CLOUDFLARE_ACCOUNT_ID.r2.cloudflarestorage.com"
```
- `publish_check.py` is one more small script. The final step can poll `https://<domain>/feed.xml` until the guid appears.
- The slug is validated by regex before it touches SQL.
- Build hooks need no auth, so the URL itself is the secret. The hook accepts `trigger_title`, and the POST body is exposed to the build as `INCOMING_HOOK_BODY` (https://docs.netlify.com/build/configure-builds/build-hooks).
- **Public-repo safety:** never use `pull_request_target`. Forked PRs don't receive secrets, and this workflow has no `pull_request` trigger at all.

**Secrets. None of these exist yet. Eve cannot create Cloudflare credentials; David must (09:42). [DAVID]**

| Name | Where David creates it | Scope / permission | Stored in |
|---|---|---|---|
| `CLOUDFLARE_API_TOKEN` | Cloudflare dash → My Profile → API Tokens → Create Token → Custom token | Account → **D1: Edit**, limited to this account. Add **Workers R2 Storage: Edit** only if CI should run `wrangler r2` (it doesn't need to; it uses the S3 keys). Exact UI labels unverified | GitHub Actions secret; local `.env` |
| `CLOUDFLARE_ACCOUNT_ID` | Cloudflare dash → account home or Workers & Pages overview (sidebar "Account ID") | Not sensitive, but kept as a secret for convenience | GitHub secret; Netlify env; `.env` |
| `R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY` | Cloudflare dash → R2 → Manage API tokens → Create API token | **Object Read & Write**, applied to `ask-eve-public` and `ask-eve-masters` only. The secret is shown once (https://developers.cloudflare.com/r2/api/tokens/) | GitHub secrets; `.env` |
| `NETLIFY_BUILD_HOOK` | Netlify → site → Site configuration → Build & deploy → Continuous deployment → Build hooks → Add build hook (branch `main`) | Anyone holding the URL can trigger builds | GitHub secret |
| `D1_READ_TOKEN` (extra) | Cloudflare API Tokens, second custom token | Account → **D1: Read** only | Netlify env var |
| `XAI_API_KEY` (extra, fallback/translations only) | xAI console | TTS | local `.env` only |

Add GitHub secrets via repo → Settings → Secrets and variables → Actions, or with `gh secret set CLOUDFLARE_API_TOKEN`. Add `.env` and `rec/`, `out/` to `.gitignore` before the first recording.

---

## 9. First-episode dry run (episode 1, 2026-10-23)

### 9.1 Facts computed on the box, 2026-10-23
- File: `episodes/2026-10-23_eve-david-podcast-planning.md` (in commit `cce3971`).
- `sha256sum` → `15d413f1fc34283cc1bb50f31d694395979201996d803a5871ee1020b3adcea4`.
- 151,711 bytes; 304 turns (152 David, 152 Eve).
- Session from `2026-10-23T07:29:52.883Z` to `2026-10-23T09:49:22.567Z`, a span of 2 h 19 m 30 s (8,370 s). That is the conversation span, **not** an audio duration.
- Audio: **none exists** (§1.1).
- `ingest_share.py` logic regenerates the file byte for byte from the share JSON (§2).

### 9.2 Local rehearsal (no cloud)
```bash
cd ask-eve
python3 scripts/ingest_share.py https://grok.com/share/c2hhcmQtMg_***REDACTED*** \
  --slug eve-david-podcast-planning --title "Eve-David podcast planning session" \
  --people "David Sacks,Marc Andreessen,Ben Horowitz,Elon Musk,Chamath Palihapitiya,Jason Calacanis,Peter Thiel,Nancy Pelosi,Chan,Scott Wiener,Bari Weiss,Zohran Mamdani" \
  --out /tmp/ep1/ --raw-dir /tmp/ep1/raw/
sha256sum /tmp/ep1/2026-10-23_eve-david-podcast-planning.md    # expect 15d413f1…adcea4
npx wrangler@4 d1 migrations apply ask-eve-meta --local
npx wrangler@4 d1 execute ask-eve-meta --local --file=dryrun/ep1.sql   # the SQL below
npx wrangler@4 d1 execute ask-eve-meta --local --command "SELECT slug,status,turn_count,transcript_sha256 FROM episodes"
```

### 9.3 The INSERT (real values; tested on local SQLite)
```sql
INSERT INTO episodes (slug, guid, date, title, explicit, language, source_url,
  session_started_at, session_ended_at, turn_count,
  transcript_path, transcript_sha256, transcript_bytes, status)
VALUES ('eve-david-podcast-planning',
  'ask-eve:2026-10-23_eve-david-podcast-planning',
  '2026-10-23', 'Eve-David podcast planning session', 1, 'en',
  'https://grok.com/share/c2hhcmQtMg_***REDACTED***',
  '2026-10-23T07:29:52.883Z', '2026-10-23T09:49:22.567Z', 304,
  'episodes/2026-10-23_eve-david-podcast-planning.md',
  '15d413f1fc34283cc1bb50f31d694395979201996d803a5871ee1020b3adcea4', 151711, 'draft');

INSERT INTO people (slug, name, stance) VALUES
 ('david-sacks','David Sacks','Anthropic war scored on three tests (symmetric scrutiny, public updating, costly principle); all currently leaning fail.'),
 ('marc-andreessen','Marc Andreessen','Rightward turn reads as motivated reasoning: ideology and money reinforcing each other.'),
 ('ben-horowitz','Ben Horowitz','Moved right alongside Andreessen; same motivated-reasoning read.'),
 ('elon-musk','Elon Musk','USAID cuts and his blind spots covered; motivated reasoning over malice.'),
 ('chamath-palihapitiya','Chamath Palihapitiya','SPAC era and family estrangement covered.'),
 ('jason-calacanis','Jason Calacanis','Politics are reactive rather than principled.'),
 ('peter-thiel','Peter Thiel','Girardian roots explain a lot, including the Antichrist lectures.'),
 ('nancy-pelosi','Nancy Pelosi','Backing Chan over Wiener is a succession move.'),
 ('chan','Chan','Pelosi''s preferred successor in the Chan-over-Wiener move.'),
 ('scott-wiener','Scott Wiener','The candidate passed over in Pelosi''s succession play.'),
 ('bari-weiss','Bari Weiss','Free Press pivot covered.'),
 ('zohran-mamdani','Zohran Mamdani','Housing push covered.')
ON CONFLICT(slug) DO UPDATE SET stance = excluded.stance;

INSERT INTO episode_people (episode_id, person_id, stance)
SELECT e.id, p.id, p.stance FROM episodes e, people p
WHERE e.slug = 'eve-david-podcast-planning'
ON CONFLICT DO NOTHING;
```
The stances are copied from `index/people.md`. Box check: 1 episode row and 12 `episode_people` rows. `UPDATE … SET status='published'` is correctly rejected by the CHECK, because there's no audio.

### 9.4 What episode 1 becomes [DAVID decides]
Episode 1 has no audio, and the publish guard keeps it out of the feed. Options:
- **(Recommended)** Keep episode 1 as `status='draft'` in D1 and render it on the site as a **text-only "pilot" transcript page**. The site can list drafts that have a transcript under "Transcripts". Launch the feed with a **2–3 minute trailer**: `episode_type='trailer'`, David's real voice introducing the show, with Eve via TTS or captured live with §1.2. Then make the next fully recorded session the first full episode. This matches "keep David's real voice", gives Spotify a valid first item, and doesn't fake a recording.
- Re-voice episode 1 per §1.3 path 2 (David re-reads 152 turns) or path 3 (fully synthetic, explicitly labeled). Either way, rerun §3 and §9.5.
- Try the Grok iOS podcast share first (§1.3 path 1). If it yields real audio of the full session, episode 1 can ship as is.

Also consider before publishing episode 1 anywhere: it's about 2 h 19 m and mostly planning and production talk. It also contains internal details, such as an internal canvas URL (line 485) and a reference to the private persistent-facts file holding David's Gmail (line 927). It's already in the public repo, but a curated edit might make a better listening episode.

### 9.5 Cloud dry run (after §8 setup), for the trailer or any episode with audio
1. `produce_audio.py` → verify `ebur128` is about -16 LUFS, and that `ffprobe` shows the chapters.
2. `upload_r2.py` → run the §3.4 `curl -sI` / `curl -r 0-1` checks against `https://audio.<domain>/…`.
3. `d1_upsert.py` sets `audio_key, audio_url, audio_bytes, audio_sha256, duration_seconds, loudness_lufs`, with status still `draft`.
4. Actions → publish → slug. Check: the D1 row is `published`; Netlify deploy is green; `feed.xml` has the item; validators pass (§6.5).
5. **[DAVID]** Spotify submission (§6.6). About an hour later, check that the episode appears and chapters show.

---

## Open questions and unverified items
1. Grok iOS "share as podcast/video": does it contain real audio of both speakers, the full length, and a downloadable file? (Unverified; test it.)
2. Whether `createTime` marks the start or end of a turn. This affects chapter offsets (§1.2). Calibrate on the first recording.
3. Whether iOS screen recording captures Grok voice output plus the mic (§1.2). Unverified.
4. Whether Spotify for Creators rejects a feed with zero episodes. Assumed yes; unverified for Spotify specifically.
5. Exact Cloudflare dashboard labels for token permissions (§8). The concepts are verified; the labels might differ.
6. The citation for -19 LUFS mono / -16 stereo was not re-verified here. They're the targets David specified.
7. Whether custom-domain email (`podcast@<domain>`) receives Spotify's code. Spotify warns that it sometimes doesn't (§6.3).
8. "Chan" in the people index is ambiguous (full name unknown). Confirm it before it becomes a public `/people/chan/` page.
9. **[DAVID]** Decisions still needed: domain; the canonical-transcript choice (§0); what episode 1 becomes (§9.4); show category (for example News → Politics, or Technology); author and owner name strings; cover art; whether to require an Actions environment approval.
10. Doc fixes to make: README.md ("Postgres" → D1, per 09:41); `episodes/README.md` (format is plain `David:` with UTC wall-clock times, not bold names with offsets).
