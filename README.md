# Ask Eve: project workspace + memory

"Ask Eve" is a podcast. David brings the questions and Eve, the host persona, gives real takes.
This repo holds the text side of the show: verbatim transcripts, the people index, the publishing
templates, and Eve's local retrieval memory.

Episode audio and metadata will live in object storage (S3/R2) plus Postgres, not in git. Git holds
only text transcripts, code, and the source files that indexes are built from. Built artifacts
(`memory/eve.db`, the embedding model) are gitignored.

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

## Quickstart
```bash
cd /workspace/ask-eve
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
import sys; sys.path[:0] = ["/workspace/ask-eve", "/workspace/ask-eve/memory"]
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
publishing/          show-notes template
site/                Netlify site source (placeholder)
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
