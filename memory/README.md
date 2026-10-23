# Ask Eve memory search

Hybrid retrieval over every `*.md` in the repo (skips `.venv/`, `memory/models/`, this README),
stored in one SQLite file, `memory/eve.db`:

1. **Vectors**: `sqlite-vec` `vec0` table, 256-d cosine, from the local model2vec model `potion-base-8M`.
2. **Tags**: a `tags(chunk_id, kind, value)` table. Values come from YAML front matter (`people`, `companies`,
   `topics`, `stances`), known names from `index/people.md` (full name or surname found in the chunk), and the speaker.
3. **FTS5**: built-in full text search, ranked by bm25. Quoted phrases are passed through as-is.

How results are merged: **Reciprocal Rank Fusion** with k=60. Each layer returns up to 200 ranked chunk ids,
and a chunk's score is `sum(1/(60+rank))` over the layers that returned it. `--tag X` keeps only chunks
with a tag value containing X (case-insensitive). Chunks are speaker turns (`[hh:mm:ss] **Eve:** ...`),
table rows, or paragraphs.

## Install (one time)
```bash
python3 -m venv .venv && .venv/bin/pip install -r memory/requirements.txt
# one-time model download (~30 MB) into memory/models/; after this nothing touches the network
.venv/bin/python -c "from model2vec import StaticModel; StaticModel.from_pretrained('minishlab/potion-base-8M').save_pretrained('memory/models/potion-base-8M')"
```

## Index (safe to re-run: it drops and rebuilds every table)
```bash
.venv/bin/python memory/search.py index
```

## Query
```bash
.venv/bin/python memory/search.py query "Girardian" -k 5
.venv/bin/python memory/search.py query "Anthropic" --tag Sacks
```
Each result prints its fused score, the file path, which layers matched (`vec+fts+tag`), and a snippet.

## Calling it from Eve mid-conversation
Shell one-liner:
```bash
cd memory && ../.venv/bin/python search.py query "what did we say about Thiel" -k 3
```
Python (use the venv interpreter):
```python
import sys; sys.path.insert(0, "memory")   # run from the repo root
from search import query
for r in query("who changed politics for money", k=3, tag=None):
    print(r["path"], r["layers"], r["score"], r["text"][:120])
```
Re-run `index` after adding new transcripts to `episodes/`.
Env overrides: `EVE_ROOT`, `EVE_DB`, `EVE_MODEL`. Results carry `layers` as `{layer: rank}`; FTS input is sanitized
(balanced quotes = phrase, stray quotes dropped, all other tokens quoted literally). Tests: `.venv/bin/pytest` from the repo root.
