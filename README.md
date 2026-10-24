# Ask Eve

[![Fork Ask Eve on GitHub](https://img.shields.io/badge/Fork_on_GitHub-daveey%2Fask--eve-111827?style=for-the-badge&logo=github)](https://github.com/daveey/ask-eve/fork)
[![Live Site](https://img.shields.io/badge/Site-askevepodcast.com-2563eb?style=for-the-badge)](https://askevepodcast.com)
[![Cloning Guide](https://img.shields.io/badge/Guide-%2Fguide%2F-059669?style=for-the-badge)](https://askevepodcast.com/guide/#cloning-guide)
[![Public Ledger](https://img.shields.io/badge/Ledger-%2Fledger%2F-d97706?style=for-the-badge)](https://askevepodcast.com/ledger/)
[![License: MIT](https://img.shields.io/badge/License-MIT-4b5563?style=for-the-badge)](LICENSE)

**[askevepodcast.com](https://askevepodcast.com)** · **[The Making of Eve & Cloning Guide (`/guide/`)](https://askevepodcast.com/guide/)** · **[Public Financial Ledger (`/ledger/`)](https://askevepodcast.com/ledger/)** · **[RSS feed](https://askevepodcast.com/feed.xml)** · **[How it works](https://askevepodcast.com/about/#how-it-works)**

"Ask Eve" is a podcast about tech, power and the people shaping both. David brings the questions and
Eve, an AI co-host built on a Grok agent, gives real takes: she states opinions, says when
she isn't sure, and can be wrong. Every AI voice on the show is labeled.

This repo is the whole show except the audio: verbatim transcripts, the people index, the audio
pipeline, the static site and feed generator, and Eve's local retrieval memory.

## At a glance
| | |
|---|---|
| Record | David talks with Eve in Grok's voice mode; his iPhone records his side and uploads it |
| Ingest | Verbatim transcript synthesis: David voiced with his ElevenLabs voice clone (`eleven_v4`), Eve voiced with xAI TTS (`eve`), loudnorm, MP3 with ID3 chapters |
| Review | every episode arrives as a pull request; nothing ships until it is merged |
| Publish | audio to Cloudflare R2 (`audio.askevepodcast.com`), metadata to Cloudflare D1 |
| Site + feed | `scripts/build_site.py` + `scripts/render_feed.py` → Cloudflare Pages → askevepodcast.com and `/feed.xml` (Apple Podcasts, Spotify, any app) |
| Memory | transcripts in git + a hybrid search index (vectors, tags, full text) Eve queries mid-conversation |

### Honesty rules
- Transcripts are verbatim; audio is fitted to the words, never the reverse.
- Any AI voice is disclosed in the feed, show notes, episode page, out loud at 00:00 and line by line.
- Unrecovered replies are marked as missing, never invented.
- Quotes on the site are checked against the transcript at build time and link to their moment.
- No secrets, private links or personal details in git; keys live only in GitHub Actions and Cloudflare secrets.

## Fork & Stand Up Your Own AI Podcast Host (5-Minute Quickstart)

The entire Ask Eve stack—static site generator, 8-language localization layer (`en`, `ru`, `es`, `zh`, `de`, `pt`, `fa`, `fr`), hybrid audio reconstruction & chapter pipeline, primary-source receipt verification, and public financial ledger—is open source and designed to be forked. Read the full walkthrough at **[askevepodcast.com/guide/#cloning-guide](https://askevepodcast.com/guide/#cloning-guide)**.

### Prerequisites
- **Python 3.11+** and `sqlite3` (standard library)
- **`ffmpeg`** on `PATH` (for audio stitching, loudness normalization, and MP3 chapter encoding)
- **Node.js 20+** (optional, for the Cloudflare upload worker in `workers/upload/`)
- **API Keys (only when generating new episodes/translations):** `XAI_API_KEY` (TTS & LLM) and `ELEVENLABS_API_KEY` (optional host voice clone)

### Hello World: Build & Serve Your Fork in 5 Minutes
```bash
# 1. Fork https://github.com/daveey/ask-eve/fork and clone your fork
git clone https://github.com/<your-username>/ask-eve.git
cd ask-eve

# 2. Set up the Python environment
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt

# 3. Initialize a local SQLite database from D1 migrations and build the static site
python3 scripts/build_site.py --out out/site

# 4. Serve locally and open http://localhost:8000
python3 -m http.server 8000 --directory out/site
```

### What to Change vs. What to Keep When Forking

| Component | Action | Files / Paths | Details |
|---|---|---|---|
| **Persona & Soul** | **Change** | `index/eve.json`, `index/eve-avatars.json`, `AGENTS.md` | Replace Eve's character sheet, worldview traits, avatar SVGs, and system instructions with your new AI co-host's identity. |
| **Voice Synthesis IDs** | **Change** | `scripts/revoice_eve.py`, `scripts/synthesize_missing_dubs.py` | Configure your TTS voice IDs (xAI TTS voice name, ElevenLabs voice ID, or local TTS model) for both hosts across locales. |
| **Site Branding & Config** | **Change** | `scripts/build_site.py`, `scripts/render_feed.py`, `site/static/` | Update `SITE`, podcast title, author metadata, color palette in `site/static/assets/site.css`, and cover art in `site/static/`. |
| **Transcripts & Dossiers** | **Change** | `episodes/*.md`, `index/people.md`, `index/takes.json`, `artifacts/*/sources.json` | Replace with your own episode transcripts, people stances, topic takes, and HTTP-verified primary sources. |
| **Public Ledger & Support** | **Change** | `index/ledger.json` | Reset the ledger transactions and set your own crypto wallets, PayPal.me, or Stripe links for `/ledger/` and `/reports/`. |
| **8-Language i18n Engine** | **Keep** | `scripts/site_i18n.py`, `scripts/translate.py`, `i18n/*.json`, `site/static/assets/i18n.css` | Gives your fork automatic localized pages (`/ru/`, `/es/`, `/zh/`, `/de/`, `/pt/`, `/fa/`, `/fr/`), RTL support (`dir="rtl"`), `hreflang` tags, and localized RSS feeds. |
| **Primary-Source Receipts** | **Keep** | `migrations/0003_sources.sql`, `scripts/d1_upsert.py`, `site/static/assets/player.js` | Keeps inline source badges, character-offset span highlighting, and bidirectional click-to-seek between transcript claims and source cards. |
| **Ledger & Report Generator** | **Keep** | `scripts/render_guide_ledger.py` | Automatically computes running totals, unit economics (cost per episode/minute), category breakdowns, and `/reports/<YYYY-MM>/` pages from `index/ledger.json`. |
| **CI Test Gates** | **Keep** | `tests/`, `scripts/verify_live_media.py`, `.github/workflows/test.yml` | Enforces zero broken links, zero untranslated strings, strict audio-locale alignment, D1 foreign-key integrity, and Playwright browser smoke checks on every push. |

## The site
Generated by `scripts/build_site.py` (stdlib only) from D1 plus the files in `index/` and `site/`:
`/` episodes, `/episodes/<slug>/` (player, chapters, transcript), `/eve/` (the Eve hub: who she is,
Inside Eve's Mind with her memories and people, and her profile, every quote verified against the transcript;
the old `/mind/` 301s here), `/people/<slug>/` (Eve's stance and every timestamped discussion), `/about/`. Names in context and transcripts become quiet links
with a small menu (`index/links.json`, built and URL-checked by `scripts/build_links.py`). Strict CSP, no
third-party scripts, self-hosted fonts. `site.yml` rebuilds and deploys on every relevant push to `main`.

Git holds text only: transcripts, code, docs, and the sources that indexes are built from. Audio lives
in Cloudflare R2 and episode metadata in Cloudflare D1. Built artifacts (`memory/eve.db`, the embedding
model, `out/`) are gitignored.

## Podcast architecture
Full design: `docs/audio-pipeline.md`. Deploy steps for David's coding agent: `docs/deploy-guide.md`.
```
iPhone (Grok voice + AirPods; Voice Memos records David)      Grok share link / Grok Bot chat export
        │ iOS Shortcut "Ask Eve upload" (shortcuts/)                     │
        ▼                                                               │
Worker workers/upload/ (upload.askevepodcast.com)                       │
  /presign → phone PUTs straight to R2 ask-eve-masters/raw/<stem>/…     │
  /ingest  → GitHub workflow_dispatch ─────────────────────────────────┤
        ▼                                                               ▼
.github/workflows/ingest.yml: ingest_share → synthesize turns (ElevenLabs voice clone + xAI TTS)
  → produce (loudnorm, MP3, chapters) → verify placement → d1_upsert.sql
  → MP3 to ask-eve-masters/staging/  → PR on ingest/<stem> (transcript + artifacts/<stem>/)
        ▼  David reviews and merges the PR (the only way an episode reaches main)
.github/workflows/publish.yml (manual): guards → MP3 to ask-eve-public (audio.askevepodcast.com)
  → D1 upsert, status=published → dispatch site.yml
        ▼
site.yml: scripts/build_site.py + render_feed.py read D1 → Cloudflare Pages → askevepodcast.com, /feed.xml → Apple Podcasts, Spotify
```
Recording methods: `tts-reconstructed` (primary: David's ElevenLabs voice clone + Eve's xAI TTS `eve` voice), `grok-share-export`
(check only), `lav-plus-tts`, `reread-plus-tts`, `tts-reconstructed` (text only, both voices AI). Any AI
voice sets `ai_reconstructed` and a disclosure that the feed, site, chapters, and show notes repeat.

Run one episode locally (no cloud writes): `scripts/run_episode.sh <slug> <share_url|share.json> [segments…]`.

## Tests
```bash
.venv/bin/pytest -q                      # Python: memory, ingest, align, revoice, produce, d1, feed, site, workflows
cd workers/upload && npm test            # upload Worker (node --test, mocked R2 and GitHub)
```
Pipeline deps: `.venv/bin/pip install -r requirements-pipeline.txt` (plus `ffmpeg` on PATH).

## Retrieval architecture (`memory/search.py`)
One SQLite file (`memory/eve.db`) holds three retrieval layers over every `*.md` chunk (one chunk per
speaker turn, table row, or paragraph):

1. **Vectors**: a [sqlite-vec](https://github.com/asg017/sqlite-vec) `vec0` table (256-d, cosine) of embeddings
   from the local [model2vec](https://github.com/MinishLab/model2vec) static model `minishlab/potion-base-8M`.
   No torch, no server, and no network at query time.
2. **Tags**: a `tags(chunk_id, kind, value)` table for exact-match recall. It is filled from YAML front matter
   (`people`, `companies`, `topics`, `stances`), from names in `index/people.md` (full name or surname found in
   the chunk), and from the speaker label.
3. **FTS5**: SQLite's built-in full-text index, ranked by bm25, for exact phrases and rare proper nouns.
   Input is sanitized: balanced `"phrases"` are kept, stray quotes become spaces, every other token is
   quoted as a literal, and terms are OR-ed. No input can produce an FTS5 syntax error.

**Fusion: Reciprocal Rank Fusion, k=60.** Each layer ranks up to 200 chunks, and a chunk scores
`sum(1/(60 + rank))` over the layers that returned it. Ranks are used, never raw scores, so the
layers need no normalization. `--tag X` filters results to chunks with a tag containing X.

## Recording on the phone
See [docs/on-the-go.md](docs/on-the-go.md): Action Button → record, Eve Start → Grok, stop → Eve Done uploads; Drive copy + email when the episode is ready.

## Quickstart
```bash
cd ask-eve   # your clone
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt        # or memory/requirements.txt for runtime only
# one-time model download (~30 MB) into memory/models/
.venv/bin/python -c "from model2vec import StaticModel; StaticModel.from_pretrained('minishlab/potion-base-8M').save_pretrained('memory/models/potion-base-8M')"
.venv/bin/python memory/search.py index              # idempotent: drops and rebuilds
.venv/bin/python memory/search.py query "Girardian" -k 5
.venv/bin/python memory/search.py query "Anthropic" --tag Sacks
.venv/bin/pytest                                     # full test suite, no network needed
```
Environment overrides: `EVE_ROOT` (corpus root), `EVE_DB` (database path), `EVE_MODEL` (model directory).

## How Eve uses it mid-conversation
```bash
# boot: load the tail of the latest episode as context
.venv/bin/python boot.py -n 30
# recall: hybrid search
.venv/bin/python memory/search.py query "what did we say about Thiel" -k 3
```
```python
import sys; sys.path[:0] = [".", "memory"]   # run from the repo root
from boot import boot
from search import query
print(boot(n=30))
for r in query("who changed politics for money", k=3, tag=None):
    print(r["path"], r["layers"], round(r["score"], 4), r["text"][:120])   # layers = {layer: rank}
```

## `boot.py`: session start context
Finds `episodes/*.md` (README.md is ignored) and picks the latest by filename (date-prefixed, sorted
lexically). It parses the speaker turns and prints the episode filename as a header, followed by the
last N turns (default 30). A turn is any line starting with `David:` or `Eve:`, optionally preceded by
`[hh:mm:ss]` and/or wrapped in `**bold**`. Continuation lines join the previous turn, and YAML front
matter is skipped. If there are no episodes it prints `no episodes yet ...` and exits 0.
Options: `-n/--turns N` and `--episodes-dir DIR`. In Python, call `boot(episodes_dir, n=30) -> str`.

## Layout
```
episodes/            verbatim transcripts, YYYY-MM-DD_topic-slug.md (front matter + David/Eve turns)
transcripts/{en,es,de}/  translations mirroring episodes/
index/people.md      person index: one-line Eve stance per person
index/moments/       per-episode discussion moments (scripts/extract_moments.py)
index/links.json     silent-link targets (generated from index/link-sources.json)
index/eve.json       Meet Eve page content (quotes cited by episode + timestamp)
publishing/          show-notes template
site/                site notes and _headers for Cloudflare Pages (the site is generated by scripts/build_site.py)
docs/                audio-pipeline.md (design), deploy-guide.md (deploy instructions)
scripts/             podcast pipeline: ingest_share, stitch_segments, align_audio, revoice_eve,
                     produce_audio, verify_placement, d1_upsert, sqlite_apply, render_feed, build_site, run_episode.sh
migrations/          D1 schema (0001_init.sql)
workers/upload/      Cloudflare Worker: presigned phone uploads + ingest dispatch
shortcuts/           iOS Shortcut build instructions
.github/workflows/   ingest.yml (opens a PR), publish.yml (manual), meta.yml (D1 metadata), site.yml (Cloudflare Pages)
artifacts/<stem>/    per-episode text artifacts, added by ingest PRs
research/            dated agent reports
memory/search.py     hybrid retrieval (vec + tags + FTS5, RRF)
memory/requirements.txt  runtime deps (pinned)
memory/persistent-facts.example.md  template; the real persistent-facts.md is local-only (gitignored)
boot.py              latest-episode tail for Eve's boot context
tests/               pytest suite (temporary corpus + DB; never touches the real eve.db)
requirements-dev.txt runtime deps + pytest
pytest.ini
```

## Known TODOs
- **Vector distance cutoff.** vec0 KNN always returns the nearest neighbours, so a low-relevance tail ends
  up in fused results. Add a max cosine distance, tuned against real transcripts.
- **Whole-token tag matching.** Tag matching and `--tag` use substring LIKE, so `Chan` also matches
  `Chandler`, and surname aliasing tags any lone "Chan". This needs whole-token or canonical-ID matching
  (covered by a strict xfail test).
- Re-indexing is a full rebuild, which is fine at the current corpus size; make it incremental later.

## License
Code is released under the [MIT License](LICENSE). Episode audio, transcripts and other show content
are not covered by the code license; all rights reserved unless stated otherwise.
