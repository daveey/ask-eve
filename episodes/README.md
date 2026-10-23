# Episodes
Naming: `YYYY-MM-DD_topic-slug.md` (e.g. `2026-10-23_sacks-andreessen-musk.md`). Files are written by
`scripts/ingest_share.py` and reach `main` only through an ingest PR.

File format:
```
---
date: YYYY-MM-DD
title: ...
episode: N            # optional
people: []
source: https://grok.com/share/<id>   # or: grokbot-chat (chat exports, minute precision → [HH:MM:00])
note: ...             # optional, e.g. redactions or AI-reconstruction disclosure
---
[HH:MM:SS] David: ...

[HH:MM:SS] Eve: ...
```
- Times are **UTC wall-clock** times of each turn (from the source's `createTime`), not offsets from the
  start of the episode. Times in the published MP3 come from `turn_offsets` in D1.
- One turn per block, separated by a blank line; a turn may span several paragraphs.
- Only verbatim text goes here; no reconstructed or paraphrased dialogue. A turn whose text was not
  recovered is written as `[transcript gap: ...]`, never invented.
