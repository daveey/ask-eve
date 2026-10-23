#!/usr/bin/env bash
# Same HTTP calls, in the same order, as the "Eve Done" Shortcut (scripts are the Shortcut's steps 1:1):
#   audio-only encode (Encode Media) -> POST /presign -> PUT the m4a -> POST /ingest (with the Grok share link).
# Usage: UPLOAD_TOKEN=... eve_done_same_path.sh <recording.mp4|mov|m4a> <https://grok.com/share/...>
set -euo pipefail
rec=${1:?recording}; share=${2:?share url}
B=https://upload.askevepodcast.com
work=$(mktemp -d); trap 'rm -rf "$work"' EXIT
day=$(date +%F); slug="walk-$(date +%H%M)"
ffmpeg -nostdin -v error -y -i "$rec" -vn -c:a aac -b:a 128k "$work/audio.m4a"   # Shortcuts "Encode Media, Audio Only" -> M4A
echo "== encoded $(stat -f%z "$work/audio.m4a") bytes; date=$day slug=$slug"
R=(); [[ -n ${RESOLVE_IP:-} ]] && R=(--resolve "upload.askevepodcast.com:443:$RESOLVE_IP")   # only for a Mac with a stale negative DNS cache
auth="Authorization: Bearer $UPLOAD_TOKEN"
pre=$(curl -fsS "${R[@]}" "$B/presign" -H "$auth" -H 'Content-Type: application/json' -d "{\"date\":\"$day\",\"slug\":\"$slug\",\"ext\":\"m4a\"}")
echo "== presign: $(echo "$pre" | jq -c '{key, method, headers, expires_in}')"
code=$(curl -sS -o /dev/null -w '%{http_code}' -X PUT -H 'Content-Type: audio/mp4' --upload-file "$work/audio.m4a" "$(echo "$pre" | jq -r .put_url)")
echo "== PUT: $code"; [[ $code == 200 ]]
ing=$(curl -sS "${R[@]}" "$B/ingest" -H "$auth" -H 'Content-Type: application/json' \
  -d "{\"date\":\"$day\",\"slug\":\"$slug\",\"ext\":\"m4a\",\"share_url\":\"$share\"}")
echo "== ingest: $ing"
