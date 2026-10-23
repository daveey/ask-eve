#!/usr/bin/env bash
# Runbook: one command per episode, local only (no cloud writes). See docs/audio-pipeline.md §9.
#
#   scripts/run_episode.sh <slug> <share_url | share.json> [segment files ...]
#
#   With segments  -> recording_method mic-plus-tts: stitch -> align (David-only ASR) -> re-voice Eve -> produce
#   Without        -> recording_method tts-reconstructed (fully synthetic, AI-disclosure label + spoken notice):
#                     voice every turn (EVE_VOICE / NARRATOR_VOICE) -> produce -> alignment pass over the mix to verify
#   Always         -> d1_upsert SQL applied to a LOCAL sqlite file, feed.xml + show notes rendered locally,
#                     PR-ready text artifacts in out/<stem>/artifacts/ (no audio in git).
#
# Env: TITLE, PEOPLE (comma list), SHARE_ID (required for a saved share.json), EPISODE_NUMBER,
#      EVE_VOICE (eve), NARRATOR_VOICE (rex), TTS_BACKEND (auto: xai when XAI_API_KEY is set, else silent PLACEHOLDER),
#      PYTHON (.venv/bin/python), ASR_PYTHON (python with faster-whisper), ASR_MODEL (small), OUT (out), DB (out/local-d1.sqlite)
set -euo pipefail
cd "$(dirname "$0")/.."

slug=${1:?usage: run_episode.sh <slug> <share_url|share.json> [segments...]}; src=${2:?share url or json}; shift 2
[[ $slug =~ ^[a-z0-9-]{1,60}$ ]] || { echo "bad slug: $slug" >&2; exit 2; }
PY=${PYTHON:-.venv/bin/python}; ASR_PY=${ASR_PYTHON:-$PY}; OUT=${OUT:-out}; DB=${DB:-$OUT/local-d1.sqlite}
export EVE_VOICE=${EVE_VOICE:-eve} NARRATOR_VOICE=${NARRATOR_VOICE:-rex}
backend=${TTS_BACKEND:-}; if [[ -z $backend ]]; then if [[ -n ${XAI_API_KEY:-} ]]; then backend=xai; else backend=silent; fi; fi
echo "== TTS backend: $backend (eve=$EVE_VOICE narrator=$NARRATOR_VOICE)"; [[ $backend == xai ]] || echo "!! PLACEHOLDER AUDIO: no real TTS" >&2

# 1. transcript (verbatim; refuses to overwrite a different existing file)
args=("$src" --slug "$slug" --out episodes/)
[[ $src == https://* ]] || args+=(--share-id "${SHARE_ID:?SHARE_ID is required for a saved share JSON}" --raw-dir "$OUT/raw-$slug")
existing=$(ls episodes/*_"$slug".md 2>/dev/null | head -1 || true)
if [[ -n $existing ]]; then
  TITLE=${TITLE:-$(sed -n 's/^title: //p' "$existing" | head -1)}
  PEOPLE=${PEOPLE:-$(sed -n 's/^people: \[\(.*\)\]$/\1/p' "$existing" | head -1)}
fi
args+=(--title "${TITLE:?TITLE is required for a new episode}")
[[ -n ${PEOPLE:-} ]] && args+=(--people "$PEOPLE")
info=$("$PY" scripts/ingest_share.py "${args[@]}")
stem=$(printf '%s' "$info" | "$PY" -c 'import json,sys;print(json.load(sys.stdin)["stem"])')
tr=episodes/$stem.md; W=$OUT/$stem; A=$W/artifacts; mkdir -p "$W" "$A"
echo "$info" > "$W/ingest.json"; echo "== transcript $tr ($(printf '%s' "$info" | "$PY" -c 'import json,sys;d=json.load(sys.stdin);print(d["turns"],"turns",d["sha256"][:12])'))"

if (($# > 0)); then
  # 2a. phone segments: David-only capture, Eve re-voiced into the gaps
  "$PY" scripts/stitch_segments.py "$@" --out "$W/stitch" ${CLEAN_CHAIN+--clean-chain "$CLEAN_CHAIN"}
  sj=(); [[ -f $OUT/raw-$slug/share.json ]] && sj=(--share-json "$OUT/raw-$slug/share.json")
  "$ASR_PY" scripts/align_audio.py --transcript "$tr" --audio "$W/stitch/david_16k.wav" "${sj[@]}" \
      --model "${ASR_MODEL:-small}" --save-words "$W/asr_words.json" --out "$A/alignment.json"
  "$PY" scripts/revoice_eve.py --backend "$backend" --alignment "$A/alignment.json" --transcript "$tr" \
      --audio "$W/stitch/david_48k.wav" --out "$W/revoice" --cache "$OUT/tts-cache" \
      --max-pause "${MAX_PAUSE:-1.5}"
  cp "$W/stitch/segments.json" "$A/segments.json"
else
  # 2b. fully synthetic (tts-reconstructed)
  "$PY" scripts/revoice_eve.py --synthetic --backend "$backend" --transcript "$tr" --out "$W/revoice" --cache "$OUT/tts-cache" --workers "${TTS_WORKERS:-4}"
fi
cp "$W/revoice/revoice.json" "$A/timeline.json"

# 3. produce: loudnorm, MP3, chapters, manifest
"$PY" scripts/produce_audio.py --mix "$W/revoice/mix.wav" --transcript "$tr" --timeline "$W/revoice/revoice.json" --out "$W/produce"
cp "$W/produce/chapters.json" "$W/produce/manifest.json" "$A/"

if (($# == 0)); then
  # 3b. verification: ASR over the generated mix, both speakers matched (placement check)
  ffmpeg -nostdin -v error -y -i "$W/revoice/mix.wav" -ac 1 -ar 16000 -c:a pcm_s16le "$W/verify_16k.wav"
  "$ASR_PY" scripts/align_audio.py --transcript "$tr" --audio "$W/verify_16k.wav" --match-speakers David,Eve --sync-phrase "" \
      --model "${ASR_MODEL:-small}" --save-words "$W/verify_words.json" --out "$W/verify_alignment.json" --allow-low-coverage || true
  "$PY" scripts/verify_placement.py --timeline "$A/timeline.json" --alignment "$W/verify_alignment.json" --out "$A/placement_check.json"
fi

# 4. D1 SQL -> local sqlite, feed + show notes
"$PY" scripts/d1_upsert.py --transcript "$tr" --slug "$slug" --revoice "$A/timeline.json" --manifest "$A/manifest.json" \
    ${EPISODE_NUMBER:+--episode-number "$EPISODE_NUMBER"} $([[ -f $A/alignment.json ]] && echo --alignment "$A/alignment.json") --out "$A/d1_upsert.sql"
"$PY" scripts/sqlite_apply.py --db "$DB" --migrations migrations --sql "$A/d1_upsert.sql"
ph=(); "$PY" -c 'import json,sys;sys.exit(0 if json.load(open(sys.argv[1]))["placeholder_audio"] else 1)' "$A/manifest.json" && ph=(--allow-placeholder)
"$PY" scripts/render_feed.py --db "$DB" --include-drafts "${ph[@]}" --manifests "$A/manifest.json" --out "$W/feed.xml" --show-notes-dir "$W/show-notes"
cp "$W/show-notes/$stem.md" "$A/show-notes.md"
echo "== done: $W (artifacts: $A)"; ls -la "$A"
