# Ask Eve: audio pipeline design

Status: revised 2026-10-23 (~11:15 UTC) to match the code on `main`. The code exists and is tested (`.venv/bin/pytest -q`, `cd workers/upload && npm test`). **No cloud resources exist yet**: no bucket, database, Worker, token, Netlify site, DNS record, or feed. Setup is in [`docs/deploy-guide.md`](deploy-guide.md); this doc is the design and the reasons for it.
Scope: how an Eve–David session becomes a verbatim transcript, an MP3, metadata, a website, and a Spotify-listed RSS feed.
Conventions: **[DAVID]** marks steps that need David (accounts, tokens, recording, judgment calls). **UNVERIFIED** marks claims I could not confirm from a primary source or a box test. `<stem>` means `<date>_<slug>`, for example `2026-10-23_eve-david-podcast-planning`.

Show identifiers (decided 10:37):

| What | Value |
|---|---|
| Site | https://askevepodcast.com |
| Feed (permanent once submitted) | https://askevepodcast.com/feed.xml |
| Public audio | https://audio.askevepodcast.com/audio/en/<stem>.v<N>.mp3 (R2 custom domain on bucket `ask-eve-public`) |
| Upload Worker | https://upload.askevepodcast.com (`/presign`, `/ingest`) |
| Owner email (public, in the feed) | podcast@askevepodcast.com, routed by Cloudflare Email Routing. Spotify fallback in §6.3 |
| Repo | https://github.com/daveey/ask-eve (renamed from `ask-eve-memory` on 2026-10-23) |

Decisions this doc follows. Where a later decision supersedes an earlier one, the later one wins:

| Time (UTC) | Decision | Notes |
|---|---|---|
| 09:17 | "Voice mode doesn't export"; fall back to regenerated voices | Desktop capture superseded by 10:08. ElevenLabs replaced by xAI TTS, which has a native `eve` voice (§1.8). |
| 09:22 | Audio-only first; YouTube later | |
| 09:23, 09:28 | Show name **Ask Eve** | Supersedes "Raw Signal". |
| 09:27 | Record raw and cut only pauses. English keeps David's real voice. Other languages use AI voices for both speakers | |
| 09:18–09:19 | Translations per language; a human pass only for languages David reads | `translations` table exists; no translation code yet. |
| 09:37–09:41 | Open-source all code. Audio in **Cloudflare R2**, metadata in **D1**. Search stays local | Audio never goes in git. |
| 09:42 | David creates the Cloudflare credentials himself | Eve can't create Cloudflare credentials. |
| ~10:08 | **Phone is the primary recording device: iPhone + AirPods, often walking outdoors. No Mac. Transcripts stay in git** | Supersedes the Mac/BlackHole path and R2-canonical transcripts. |
| 10:34 | **Re-voice is the primary path (`mic-plus-tts`)**: David's real voice from a phone recording of his side; Eve re-voiced with xAI TTS from the verbatim transcript and placed in the gaps | Supersedes "screen-record both voices" as the plan. Screen recording is demoted to an optional check (§1.3). |
| 10:36 | **Ingest is PR-only**: CI opens a pull request on `ingest/<stem>`; it never commits to `main`. Publishing is a separate, manual `publish.yml` run after David merges | There is no direct-to-main option in the code. |
| 10:37 | Domain **askevepodcast.com** | Replaces every `<domain>` placeholder. |
| 10:43 | Narrator voice **`rex`** for David's lines when his audio is missing; every AI-reconstructed stretch is labeled (front matter, chapters, D1 `ai_disclosure`, feed, show notes); one session may arrive as **several recordings** (`phone-01`, `phone-02`, …) | Segments are stitched on their recording clocks (§3.0). |
| 10:45 | **Two buckets** (`ask-eve-public`, `ask-eve-masters`) and **no Cloudflare API token inside the Worker** (it presigns with an R2 S3 key scoped to the masters bucket) | Deviation from David's one-bucket spec, for safety. Explained in the deploy guide. |
| 10:59–11:01 | A Grok Bot chat can be an episode source (`--source-type grokbot-chat`); episode 1 "The Making of Eve" is this whole chat, with David's real audio where it exists and narrator fill elsewhere (hybrid mode, §11.5) | |

---

## 0. Where transcripts canonically live: git

- **The repo is the text source of truth.** `episodes/<stem>.md` is the verbatim transcript. It reaches `main` only through a reviewed PR.
- Audio and other binary originals (phone recordings, share JSON, masters, staged MP3s) **never** go in git. They live in the private bucket (§7).
- `artifacts/<stem>/` (committed in the ingest PR) holds the **text** outputs: `timeline.json`, `chapters.json`, `manifest.json` (audio key, bytes, sha256, duration, loudness, placeholder flag, AI label), `placement_check.json`, `d1_upsert.sql`, `show-notes.md`. `publish.yml` reads these from `main`.
- D1 stores `transcript_path` and `transcript_sha256`. `scripts/build_site.py` fails the build if the checked-out file doesn't match.
- **The transcript text is never rewritten from ASR** (§11).

---

## Build order (what's done, what's left)

1. Done (code, local tests): `ingest_share.py`, `stitch_segments.py`, `align_audio.py`, `revoice_eve.py`, `produce_audio.py`, `verify_placement.py`, `d1_upsert.py`, `sqlite_apply.py`, `render_feed.py`, `build_site.py`, `run_episode.sh`, `migrations/0001_init.sql`, `workers/upload/`, `.github/workflows/{ingest,publish}.yml`, `netlify.toml`, `shortcuts/ask-eve-upload.md`.
2. **[DAVID]** Run the 2-minute recording test (§1.5). It confirms the default recording path.
3. **[DAVID]** Cloud setup, in the order of `docs/deploy-guide.md` (D-001…D-008): R2 buckets and S3 key, D1, Worker, GitHub secrets, Netlify + DNS + Email Routing, end-to-end test.
4. **[DAVID]** Cover art at 3000×3000 (§6.2), show description, category.
5. **[DAVID]** Merge the episode-zero and episode-one ingest PRs after review, then run `publish.yml` for each.
6. Validate the feed (§6.5). **[DAVID]** Submit to Spotify for Creators and enter the 8-digit code (§6.6).

---

## 1. Recording: iPhone + AirPods

The setup is David on an iPhone with AirPods, Grok voice mode running Eve, often walking outdoors. Eve exists only in his ears. Nothing in the room can record her, and the in-phone capture options are unproven (§1.2). So the plan does not depend on capturing Eve at all: **record David's side, and re-voice Eve from the verbatim transcript.**

### 1.1 What Grok gives us today
| Source | Audio? | Status |
|---|---|---|
| Share link JSON `GET https://grok.com/rest/app-chat/share_links/<share_id>` | **No.** It has text turns, `createTime`, and `sender`. Every response has `mediaTypes: ["audio"]` but empty `fileUris`/`fileAttachments`, and there's no audio URL or voice field anywhere | **Verified** 2026-10-23 on episode 1: the saved copy plus a live read-only fetch (HTTP 200, 304 turns). It stays the **transcript** source (§2). |
| Grok voice mode in-app | Eve said at 09:17 "voice mode doesn't export"; at 09:49 the transcript couldn't be exported from the session either | Transcript claim. |
| Grok iOS "share voice conversations as videos or podcasts" | Possibly. An @grok post reported 2026-02-12 (https://www.eonmsk.com/2026/02/12/xai-grok-voice-share-conversations/) says "Share your voice conversations as videos or podcasts. Try it now on iOS!" A Threads post from 2026-02-14 (@oncescuradu) describes the same feature | **UNVERIFIED.** Unknown: whether it carries David's real mic audio or re-synthesized audio, the maximum length, the file format, and whether you get a file or only a link. Covered by the 2-minute test (§1.5, step 6). |


### 1.2 iOS capture options (research)
Research kept for reference. Since 10:34 none of these is the primary path; the screen-recording row is the optional check in §1.3.
| Option | Captures Grok in-app voice + David? | Evidence | Verdict |
|---|---|---|---|
| **iOS Screen Recording with Microphone On** (Control Center → long-press Screen Recording → Microphone) | Possibly both: app audio is mixed with the mic into one recording. Apple warns "Some apps might not allow you to record audio or video" (https://support.apple.com/en-us/102653). Apple Community threads report that during calls or voice sessions with a **Bluetooth** mic, the other side's audio can be missing (https://discussions.apple.com/thread/255971801, https://discussions.apple.com/thread/255450278). A call-recorder vendor (recapmycalls.com) claims iOS strips incoming call audio from screen recordings. That's a vendor source, and it's about carrier/VoIP calls | **UNVERIFIED for Grok voice; this is the #1 risk.** Grok voice probably runs a `voiceChat`-style audio session; whether ReplayKit's system capture includes it isn't documented. **The test is first (§1.5).** |
| iOS 18.1+ native call recording | No. Phone app calls and 1:1 FaceTime audio only (https://support.apple.com/guide/iphone/iph57c6590e9/ios, https://support.apple.com/en-us/121583) | Applies to Grok: **no** (inference: Grok isn't a phone or FaceTime call) | Not usable. |
| TapeACall / Rev Call Recorder | No. TapeACall records through a "three-way conference call between you, the person you're speaking with, and our recording line" (https://support.tapeacall.com/hc/en-us/articles/38577452382356, https://www.tapeacall.com/support/troubleshooting), so it needs a **carrier** call and carrier 3-way merge. In-app VoIP audio can't be merged. Rev's recorder is assumed to work the same way (UNVERIFIED) | Verified for TapeACall | Not usable for Grok voice. |
| Grok share-as-podcast/video export | Unknown (§1.1) | UNVERIFIED | Test; it's fallback #2. |
| Voice Memos running alongside Grok voice | Probably not. Starting Grok voice most likely interrupts the Voice Memos audio session, or vice versa, and even if it worked it would capture only David's mic | UNVERIFIED | Optional test step. Not a plan. |
| Hardware tap (cable splitter, recorder in the headphone path) | AirPods are Bluetooth, so there's no analog path to tap. A Lightning/USB-C wired headset plus a hardware recorder would mean giving up AirPods while walking | Inference | Impractical; not pursued. |
| Lav/wireless mic with **on-board** recording, worn alongside AirPods | David only, in good quality. Eve isn't captured. RØDE Wireless GO (Gen 3) records on-board, including an "Always" mode that records even when unpaired, 32-bit float (rode.com Wireless GO user guide, "On-board recording" section). Getting files onto an iPhone without a computer is UNVERIFIED (RØDE Central is a desktop and mobile app; mobile export not confirmed). DJI Mic Mini has no on-board storage; "Mic Mini 2S" storage figures are from search snippets, UNVERIFIED | Partly verified | Backup for David's voice only (`lav-plus-tts`). Buy only if the screen-record test captures Eve badly or not at all. |

Android (one line, out of scope): `AudioPlaybackCapture` can't capture `USAGE_VOICE_COMMUNICATION` audio (developer.android.com AudioAttributes / AudioPlaybackCaptureConfiguration), and Samsung's recorder says it won't record VoIP, so expect the same problem there.
Desktop (one line): at a computer, a loopback driver (for example BlackHole on macOS) gives two clean tracks. That's no longer planned (superseded 10:08).

### 1.3 Recording paths and fallback order
`episodes.recording_method` stores which one was used (§4). Default: **`mic-plus-tts`**.
1. **`mic-plus-tts` (primary, decided 10:34).** David records his own side on the phone (Voice Memos, or Screen Recording with the mic on, which also captures David). Eve's turns are rendered with xAI TTS (`voice_id: "eve"`) from the verbatim transcript and placed in the gaps between David's ASR-matched turns (§11). The episode is labeled "Eve's voice re-rendered from the transcript". David's voice is real.
2. **`grok-share-export` (a check, not a path we plan on).** If Grok's iOS "share as podcast/video" export turns out to contain both real voices at full length, it can replace the re-voice for that episode. The §1.5 test checks it once; nothing in the pipeline depends on it.
3. **`lav-plus-tts`.** The same as 1, but David's audio comes from an on-board lav recording (better outdoors). Use it only if the phone mic is unusable while walking.
4. **`reread-plus-tts` / `tts-reconstructed`.** David re-reads his lines, or both voices are synthetic: Eve plus the `rex` narrator for David, with a spoken disclaimer and the AI label (episode zero). Non-English editions always use `tts-reconstructed` (09:27).

`ios-screen-record` (both voices captured live by Screen Recording) stays in the schema in case the test shows it works, but it is no longer a planned path: Apple warns some apps block capture, and Bluetooth-mic reports are negative (§1.2).

Hybrid (episode one, §11.5): a session where David's recording covers only part of the conversation. Matched David turns use his real voice, the rest use the narrator, Eve is always TTS, and every reconstructed stretch is labeled. `recording_method` is `mic-plus-tts`, with `ai_reconstructed = 1`.

### 1.4 AirPods mic quality: realistic expectations
- In a two-way voice session, AirPods use the Bluetooth hands-free path. Classic HFP is CVSD at 8 kHz or mSBC at 16 kHz (Apple Bluetooth accessory guidelines; https://discussions.apple.com/thread/7794112). Since 2021, AirPods 3 and AirPods Pro have used **AAC-ELD** for calls, mono, about 24 kHz effective with SBR (Pfeiffer, medium.marco.zone; ithinkdiff coverage; exact article URLs not re-checked). Whether Grok's session gets AAC-ELD or falls back to mSBC is UNVERIFIED. ffprobe on the test file shows the container rate (likely 44.1/48 kHz), not the true bandwidth; check a spectrogram.
- iOS 26's "studio-quality audio recording" for AirPods 4 / AirPods 4 ANC / AirPods Pro 2 (Apple Newsroom, 2025-06-09) covers Camera, Voice Memos, Messages dictation, Webex, and compatible camera apps. Apps opt in via `bluetoothHighQualityRecording` / `AVCaptureSession.configuresApplicationAudioSessionForBluetoothHighQualityRecording` (WWDC25 session 251). **It likely does not apply** to Grok voice or the screen recorder (UNVERIFIED).
- **Voice Isolation** mic mode (Control Center → App Controls/Mic Mode; https://support.apple.com/en-us/101993) can cut wind and traffic. Whether it's offered during Grok voice is UNVERIFIED. The test checks it, and if available it should be on while walking.
- Expect **phone-call quality** for David: about 7–12 kHz bandwidth, with on-device noise suppression already applied. Eve is probably captured cleaner (digital app audio), but in the same single mono mix.
- Realistic post-processing (§3.0) consists of an 80 Hz high-pass (wind rumble and handling), light FFT or RNNoise denoise, gentle compression, and two-pass loudnorm. It can tame steady traffic and hiss. It **cannot** fully fix wind gusts, clipped bursts, or dropouts, and pushing denoise harder makes speech sound watery. Keep denoise mild, because Eve passes through the same chain.

### 1.5 The 2-minute test [DAVID]: do this first
Goal: confirm that David's side records cleanly through AirPods, indoors and walking, and that the alignment finds his turns. Capturing Eve is **not** required.

Settings: note the iOS version, iPhone model, and AirPods model. Turn on Focus/Do Not Disturb. Set Auto-Lock to Never for the test.

Run it twice: **A indoors**, then **B walking outside** (about 2 min each):
1. AirPods in. Start **Voice Memos** recording (or Screen Recording with Microphone On; either records David).
2. Open Grok and start voice mode. Say **"Ask Eve, take one."**
3. Talk normally for about 90 s, with at least 4 back-and-forths. Say **"blue giraffe seventeen"** once.
4. Optional: open Control Center and look for Mic Mode / Voice Isolation.
5. Last 15 s: lock the phone, keep talking, then unlock. Note whether the recording or Grok stopped.
6. End the voice session. **Share the conversation → copy the link.** Optional check (path 2): also try **share as podcast/video**, save it to Files, and note whether it has both real voices.
7. Stop the recording.

Send the recording(s) and the share link via the "Ask Eve upload" Shortcut (`shortcuts/ask-eve-upload.md`), or for a test, any route (Google Drive works). Then run, locally:
```bash
scripts/run_episode.sh airpods-test-a https://grok.com/share/<id> ~/Downloads/<recording>.m4a
```
**Pass** means: `alignment_coverage` ≥ 0.5 over David's turns (`align_audio.py --match-speakers David`), "take one" and "giraffe" found, Eve's TTS clips land in the gaps (`placement_check.json` all ok), and the recording's `creationdate` is present. Pass → `mic-plus-tts` (default). David's audio unusable while walking → consider a lav (`lav-plus-tts`). If the share export has both real voices, note it (`grok-share-export`).

### 1.6 Session ritual (every recorded episode) [DAVID]
1. Focus on, AirPods in, phone charged.
2. **Start the recording first** (Voice Memos), then open Grok voice.
3. First words: **"Ask Eve, take one."** This is the sync anchor (§11). After a restart, say "take two". If a recording stops, start a new one; several segments per session are fine (§3.0).
4. At the end: stop the voice session, **share the conversation** (copy the link), stop the recording.
5. Voice Memos → the recording → Share → **"Ask Eve upload"**. Paste the share link when asked; for a second segment, give segment number 2. CI opens a PR; nothing is published until David merges it and runs `publish.yml`.

### 1.7 What a David-only recording implies
- Eve may bleed faintly into the recording from the AirPods. Re-voiced clips are placed in the gaps between David's turns, so residual bleed sits under Eve's clip; it isn't removed.
- Overlaps (David talking over Eve) stay as recorded.
- Speaker labels come from the transcript plus alignment (§11), not from diarization.
- Denoise only affects David. Keep it mild and A/B it on the walking test.

### 1.8 xAI TTS details (for paths 3–5 and translations)
`POST https://api.x.ai/v1/tts`, `voice_id: "eve"`, MP3 or WAV output. The limit is 60,000 characters per request, and the WebSocket `wss://api.x.ai/v1/tts` streams with `with_timestamps` (https://docs.x.ai/developers/model-capabilities/audio/text-to-speech). Needs `XAI_API_KEY` **[DAVID]**. Generate one clip per Eve turn (`eve_<index>.wav`). For `mic-plus-tts`, place each clip at that turn's `edited_seconds` from `turn_offsets.json`. A cloned David voice (xAI Custom Voices) needs David's consent and a reference clip **[DAVID]**. Non-English editions use Eve's voice plus David's cloned voice, reading the translated transcript (es-ES/es-MX, de, and others per the docs).

---

## 2. `scripts/ingest_share.py`: source to `episodes/<stem>.md`

Format: plain `[HH:MM:SS] David: …` / `[HH:MM:SS] Eve: …`, where the time is the **UTC wall-clock** from the source, not an audio offset (`episodes/README.md` documents this).

```
.venv/bin/python scripts/ingest_share.py <https://grok.com/share/<id> | share.json> --slug <slug> --title "<title>" \
    [--people "A,B"] [--share-id <id>] [--out episodes/] [--raw-dir out/<stem>/] [--force]
.venv/bin/python scripts/ingest_share.py chat_turns.json --source-type grokbot-chat --slug <slug> --title "<title>" \
    --date 2026-10-23 --episode 1 --note "<front-matter note>"
```

Source type `grok-share` (default):
1. `share_id` = last URL path segment; read-only `GET https://grok.com/rest/app-chat/share_links/<share_id>`. Raw bytes are saved to `--raw-dir` (uploaded to the private bucket, never committed).
2. Turns sorted by `createTime`; `sender` must be `human` or `assistant`.
3. Each turn renders as `[{createTime[11:19]}] {David|Eve}: {message.strip()}`, joined with blank lines. Empty turns are kept (`[08:21:13] Eve: `).
4. Front matter: `date`, `title`, `people`, `source` (the share URL), `note`.
5. `--people`: if omitted, names from `index/people.md` are suggested and printed, never auto-committed.
6. Refuses to overwrite a differing file unless `--force`. Prints `path`, `sha256`, `bytes`, `turns`, `session_started_at`, `session_ended_at` for `d1_upsert.py`.

**Verified:** on episode zero's share JSON this reproduces the committed file byte for byte (sha256 `15d413f1…adcea4`, 151,711 bytes).

Source type `grokbot-chat` (added for episode one): a JSON list `[{"speaker": "David"|"Eve", "text": …, "clock_utc_minute": "10:34 AM"}]` extracted from a Grok Bot conversation. Minute precision, rendered as `[HH:MM:00]`, list order kept, clocks must be non-decreasing, `source: grokbot-chat`, optional `episode:` key. Turns that look like secrets (API keys, tokens) are rejected, so a pasted key must already be redacted (episode one: `[REDACTED xAI API key]`). A reply that wasn't recovered is a turn whose text starts `[transcript gap`; it is kept in the transcript and never voiced.

Tests: `tests/test_ingest_share.py` (synthetic share JSON, the episode-zero sha when `/workspace/share.json` is present, grokbot-chat rendering and validation).

---

## 3. Audio production

The pipeline, as `scripts/run_episode.sh <slug> <share_url|share.json> [segment files…]` runs it (local only; CI's `ingest.yml` runs the same scripts):

| Step | Script | Output |
|---|---|---|
| Transcript | `ingest_share.py` | `episodes/<stem>.md` |
| Stitch segments on their recording clocks, clean | `stitch_segments.py --out <dir> seg1 seg2 …` | `david_48k.wav`, `david_16k.wav`, `segments.json` (offsets, gaps, sha256 per segment) |
| Align David's turns | `align_audio.py --match-speakers David` (§11) | `alignment.json` (raw times; Eve turns `gap-placed`) |
| Re-voice Eve (and narrator) | `revoice_eve.py` (§11.4–11.5) | `mix.wav`, `revoice.json` (time map, per-turn edited times, voices, coverage) |
| Encode + chapters | `produce_audio.py --mix --transcript --timeline --out` | `<stem>.v1.mp3`, `chapters.json`, `manifest.json` |
| Verify placement | `align_audio.py` over the mix, then `verify_placement.py` | `placement_check.json` |
| Metadata | `d1_upsert.py`, `sqlite_apply.py`, `render_feed.py` | `d1_upsert.sql`, local sqlite, `feed.xml` preview, `show-notes.md` |

### 3.0 Extract, stitch, and clean
Several recordings per session are normal (a memo stopped, the phone locked). `stitch_segments.py` reads each file's `com.apple.quicktime.creationdate` (or `creation_time`), places segments on the shared UTC clock, fills real gaps with silence (recorded in `segments.json` as `recording_gaps`), and applies the clean chain. Segment keys in R2 are `raw/<stem>/phone-01.m4a`, `phone-02.m4a`, …

Reference ffmpeg commands (run with ffmpeg 7.1.5 on the box):
```bash
# inspect: number of audio streams, codec, rate, duration, Apple creation date
ffprobe -v error -show_entries format=duration,size:format_tags=com.apple.quicktime.creationdate,creation_time \
  -show_entries stream=index,codec_type,codec_name,sample_rate,channels -of json phone.mp4
# lossless copy of the audio track (kept for reference; 0.09 s for 120 s on the box)
ffmpeg -i phone.mp4 -map 0:a:0 -vn -c:a copy phone_audio.m4a
# working master: mono 48 kHz 24-bit
ffmpeg -i phone.mp4 -map 0:a:0 -vn -ac 1 -ar 48000 -c:a pcm_s24le work.wav
# ASR input for §11: mono 16 kHz
ffmpeg -i work.wav -ac 1 -ar 16000 -c:a pcm_s16le asr16k.wav
# clean, default (no model file): 0.58 s per 120 s on the box
ffmpeg -i work.wav -af "highpass=f=80,afftdn=nr=12:nf=-40:tn=1,acompressor=threshold=-21dB:ratio=3:attack=10:release=200:makeup=3" clean.wav
# clean, RNNoise A/B (needs a model file): 1.2 s per 120 s on the box (~70 s for 2 h)
ffmpeg -i work.wav -af "highpass=f=80,arnndn=m=models/sh.rnnn:mix=0.8,acompressor=threshold=-21dB:ratio=3:attack=10:release=200:makeup=3" clean_rnn.wav
```
- `arnndn` needs a model. The box test used `somnolent-hogwash-2018-09-01/sh.rnnn` from https://github.com/GregorR/rnnoise-models (297,646 bytes, sha256 `70bb6685…fdec0`). Pin it in `models/` with its sha, or fetch it in CI.
- `mix=0.8` blends 20% of the original back in, which reduces artifacts. `afftdn tn=1` tracks the noise floor as it changes while walking.
- Pick the default after an A/B on the **real** walking test. The box test used synthetic noise, so its loudness and quality numbers mean nothing.
- Store the exact chain string in `episodes.processing_chain` (§4).
- If two audio streams exist (§1.7), extract `0:a:1` too and clean only the mic stream.
- `speechnorm` and `dynaudnorm` are also available in this ffmpeg if compression isn't enough.

### 3.1 Edit: cut only pauses (09:27)
- Input is the single cleaned mono track `clean.wav` (§3.0). For `mic-plus-tts`/`lav-plus-tts`, first mix David's track with the TTS Eve clips placed at `turn_offsets.json` raw times (`adelay` + `amix=normalize=0`).
- Find long pauses: `ffmpeg -i clean.wav -af silencedetect=n=-45dB:d=1.5 -f null -`. Parse `silence_start`/`silence_end`.
- Build a keep-list that shortens every pause longer than 1.5 s to 0.7 s, and render it with `atrim`/`concat`. **Store the cut map** (`cutmap.json`: list of kept segments `[orig_start, orig_end, new_start]`) so chapter and turn offsets from `turn_offsets.json` (raw time) can be remapped to edited time (§11.4). A one-liner `silenceremove=stop_periods=-1:stop_duration=1.2:stop_threshold=-45dB:stop_silence=0.7` also works (tested: a 4 s gap became about 1.9 s), but it produces no cut map, so use it only when chapters don't matter.
- Nothing else gets edited. Save `master.wav` and upload it to private `masters/…`.

### 3.2 Loudness: two-pass loudnorm
Targets: **-16 LUFS integrated for stereo, -19 LUFS for mono**, true peak -1.5 dBTP, LRA 11. These are common podcast guidance figures; Apple's published recommendation is around -16 LKFS. The exact citation for the -19 mono figure was **not re-verified** here.
```bash
# pass 1: measure
ffmpeg -hide_banner -i master.wav -af loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json -f null - 2> ln.log
# pass 2: apply measured values (script parses the JSON block from ln.log)
loudnorm=I=-16:TP=-1.5:LRA=11:measured_I=<input_i>:measured_TP=<input_tp>:measured_LRA=<input_lra>:measured_thresh=<input_thresh>:offset=<target_offset>:linear=true
```
Recommendation: **stereo, -16 LUFS** by default (the source is mono; `-ac 2` just duplicates it). It's a single target, and every player handles it. Use `--mono` (`-ac 1`, `I=-19`) if file size ever matters. After encoding, check with `ffmpeg -i out.mp3 -af ebur128=peak=true -f null -` and store the measured value in `episodes.loudness_lufs`. In the box test, a 6 s tone landed at -16.4 LUFS. Real speech of a minute or more is measured more accurately.

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
Created by the deploy guide (D-002), not by this doc: buckets `ask-eve-public` (custom domain `audio.askevepodcast.com`, r2.dev disabled) and `ask-eve-masters` (private: no custom domain, no r2.dev).

Flow: `ingest.yml` stages the MP3 in the **private** bucket at `staging/<stem>/<stem>.v<N>.mp3` and opens the PR. After David merges, `publish.yml` checks the manifest (refuses `placeholder_audio`, requires the AI label where needed), copies the MP3 to `ask-eve-public/audio/en/<stem>.v<N>.mp3` after a sha256 check, uploads `chapters.json`, upserts D1, marks the episode published, and triggers the Netlify build hook. Uploads use the aws CLI against the R2 S3 endpoint (`https://$CLOUDFLARE_ACCOUNT_ID.r2.cloudflarestorage.com`, region `auto`).

Public URL: `https://audio.askevepodcast.com/audio/en/2026-10-23_eve-david-podcast-planning.v1.mp3`.

- Buckets are private by default. **Use a custom domain, not `r2.dev`**: r2.dev is for development, is rate-limited (it can return 429), and has no Cloudflare cache. A custom domain must be a zone in the same Cloudflare account. Sources: https://developers.cloudflare.com/r2/buckets/public-buckets/ and https://developers.cloudflare.com/r2/platform/limits/.
- Through a custom domain, files up to 512 MB are cacheable on Free/Pro/Business (https://developers.cloudflare.com/cache/interaction-cloudflare-products/r2/). Episodes stay well under that.

Headers and rules:
- `Content-Type: audio/mpeg`.
- `Cache-Control: public, max-age=31536000, immutable`. This is safe because keys are **versioned** (`.v1`, `.v2`). Spotify won't re-download a changed file at an unchanged URL (spec v1.10), so a fix always means a new key plus a D1 update.
- Apple requires HEAD and byte-range support (https://podcasters.apple.com/support/823-podcast-requirements). Check after the first upload:
  `curl -sI "$URL"` should return `200` with `content-length` equal to `audio_bytes`, and `curl -s -r 0-1 -o /dev/null -w '%{http_code}' "$URL"` should print `206`.
- Add CORS on `ask-eve-public` (GET/HEAD from `https://<site>`) only if the site player uses Web Audio or a waveform. A plain `<audio>` element doesn't need it.

---

## 4. D1 schema: `migrations/0001_init.sql`

The migration is the source of truth; this is a summary. Applied with `npx wrangler@4 d1 migrations apply ask-eve-meta --remote --config workers/upload/wrangler.toml` (the `[[d1_databases]]` block there has `migrations_dir = "../../migrations"`), and locally with `scripts/sqlite_apply.py`.

| Table | Holds |
|---|---|
| `episodes` | slug, guid (`ask-eve:<stem>`), `episode_number`, `episode_type` (full/trailer/bonus), title, summary, date, status (`draft`/`published`/`hidden`), transcript path/sha/bytes, session start/end, turn count, `recording_method`, recording device/environment, raw audio key/format/bytes/sha, sync offset, `alignment_method` (`asr-david-gaps`, `asr-fuzzy`, `linear-fit`, `creationdate-only`, `tts-exact`), `alignment_coverage`, ASR model, processing chain, audio key/url/bytes/sha/duration, loudness, `tts_voice`, `narrator_voice`, `ai_reconstructed`, `ai_disclosure`, explicit, timestamps |
| `people`, `episode_people` | people index (from `index/people.md`) and per-episode stance |
| `tags`, `episode_tags` | tags |
| `chapters` | start seconds and title per chapter (from `chapters.json`) |
| `turn_offsets` | one row per transcript turn: speaker, `created_at`, raw start/end seconds (David's recording), edited start/end seconds (published MP3), `method`, `score` |
| `translations` | per-language editions (no code yet) |

Key CHECKs:
- `recording_method` ∈ `mic-plus-tts`, `grok-share-export`, `lav-plus-tts`, `reread-plus-tts`, `tts-reconstructed`, `ios-screen-record`, `none`.
- `turn_offsets.method` ∈ `asr-match`, `sync-phrase`, `gap-placed`, `interpolated`, `tts-placed`, `tts-synthetic`, `fit`. `score` is only set for `asr-match`/`sync-phrase`; `gap-placed` and `tts-placed` are Eve-only.
- `ai_reconstructed = 1` requires a non-empty `ai_disclosure`, and `tts-reconstructed` requires `ai_reconstructed = 1`.
- `status = 'published'` requires `audio_url`, `audio_bytes > 0`, and `published_at`, so the feed can never contain an item without an enclosure.

Design notes: search stays local (09:41). `turn_offsets` is derived data: rebuild it by re-running the pipeline. Backups: `npx wrangler@4 d1 export ask-eve-meta --remote --output backup.sql`, kept in the private bucket under `backups/d1/`.

---

## 5. Website (Netlify): build-time D1

Built: `scripts/build_site.py` (stdlib Python, no Node on Netlify). `netlify.toml` runs `python3 scripts/build_site.py` and publishes `site/dist`, with an `application/rss+xml` header on `/feed.xml`.
- At build time it reads D1 through the REST API (`POST https://api.cloudflare.com/client/v4/accounts/{account_id}/d1/database/{database_id}/query`) with Netlify env vars `CLOUDFLARE_ACCOUNT_ID`, `D1_DATABASE_ID`, and `D1_READ_TOKEN` (a separate token with **D1 Read** only, so a build-log leak can't write). Locally, `--db out/local-d1.sqlite` reads a sqlite file instead.
- It writes `feed.xml` (via `render_feed.py`), `index.html`, and `episodes/<slug>/index.html` plus `transcript.txt`. Each transcript is checked against `transcript_sha256`; a mismatch fails the build, so it never ships a wrong transcript or an empty feed. Drafts are excluded unless `--include-drafts`.
- Cloudflare notes the REST API "primarily interacts with the control plane" (https://developers.cloudflare.com/d1/best-practices/query-d1/). A few queries per build is fine; per-visitor reads would not be, and the static site makes none.
- This is a deliberately minimal site (no Astro). Moving to Astro or Cloudflare Pages later doesn't change the data model.

---

## 6. RSS feed and Spotify for Creators

### 6.1 What generates it
`scripts/render_feed.py` renders `feed.xml` from the D1 rows (published episodes only, unless `--include-drafts` for a local preview), plus per-episode `show-notes/<stem>.md`. It refuses placeholder audio unless `--allow-placeholder`. AI-reconstructed episodes get their `ai_disclosure` in the item description and a banner in the show notes. `build_site.py` calls it at build time, so the feed is the static file `https://askevepodcast.com/feed.xml`.

The feed URL is permanent once submitted. **Use `https://askevepodcast.com/feed.xml` from day one, not `*.netlify.app`.** Changing it later requires Spotify's update-RSS flow (https://support.spotify.com/us/creators/article/updating-an-rss-feed-link-or-hosting-provider/).

### 6.2 Required content
Sources: Spotify Podcast Delivery Specification v1.10 (linked from https://support.spotify.com/us/creators/article/podcast-specification-doc/; PDF at https://assets.ctfassets.net/jtdj514wr91r/4r4op9KhH3fY1t3BKt2eiH/7b1b682acf4c41baf79f9574d6dedd7f/Podcast_Delivery_Specification_v1.10_-_master_doc.pdf), Apple requirements (https://podcasters.apple.com/support/823-podcast-requirements, https://help.apple.com/itc/podcasts_connect/en.lproj/itcb54353390.html), and Spotify's email article (https://support.spotify.com/us/creators/article/adding-your-email-address-to-your-rss-feed/).

- UTF-8 XML. Escape `&`, or wrap descriptions in CDATA.
- Namespaces: `itunes` (required by Spotify), `content`, and `psc` (`http://podlove.org/simple-chapters`, which Spotify supports in version 1.1 only).
- Channel: `title` (Ask Eve), `link`, `description`, `language` (en), `itunes:author`, `itunes:image href` (3000×3000), `itunes:category`, `itunes:explicit`, `itunes:type` (episodic), and **`itunes:owner` → `itunes:name` + `itunes:email`**. Spotify sends its claim code to the address in `<itunes:email>` (verified in Spotify's articles). Apple lists `itunes:owner` as optional, but we need it.
- Item: `title`, **`guid` (stable, `isPermaLink="false"`)**, `pubDate` (RFC 2822), **`enclosure url length type`** (`length` is the byte count from D1; Spotify treats it as optional, but always fill it), `itunes:duration` (seconds or HH:MM:SS), `itunes:episodeType`, `itunes:explicit`, `description`/`content:encoded` (show note plus a link to the transcript page), and `psc:chapters version="1.1"`.
- Artwork **[DAVID]**: square, 1400–3000 px (use 3000), JPEG or PNG, RGB, no alpha, and the file extension must match the format (Apple). Spotify asks for square, highest resolution. Store it at public `art/cover-3000.v1.jpg`.
- Episode zero contains profanity (for example "piece-of-shit" at line 355 and "fucking" at line 641), so set `explicit = true`.
- AI disclosure: state in the channel description that Eve is an AI host. This is a recommendation, not a platform requirement I verified.

Item sketch, emitted by the endpoint from the D1 rows:
```xml
<item>
  <title>Eve-David podcast planning session</title>
  <guid isPermaLink="false">ask-eve:2026-10-23_eve-david-podcast-planning</guid>
  <pubDate>Fri, 23 Oct 2026 12:00:00 +0000</pubDate>
  <enclosure url="https://audio.askevepodcast.com/audio/en/2026-10-23_eve-david-podcast-planning.v1.mp3" length="<audio_bytes>" type="audio/mpeg"/>
  <itunes:duration><duration_seconds></itunes:duration>
  <itunes:episodeType>full</itunes:episodeType>
  <itunes:explicit>true</itunes:explicit>
  <description><![CDATA[<show note> Transcript: https://askevepodcast.com/episodes/eve-david-podcast-planning/]]></description>
  <psc:chapters version="1.1"><psc:chapter start="00:00:00" title="Cold open"/></psc:chapters>
</item>
```
Channel owner block: `<itunes:owner><itunes:name>Ask Eve</itunes:name><itunes:email>podcast@askevepodcast.com</itunes:email></itunes:owner>`.

### 6.3 Owner email [DAVID]
The email in the feed is **public**. Spotify notes that verification emails "sometimes … don't make it through" to custom or company domains, and suggests Gmail or Outlook (https://support.spotify.com/us/creators/article/not-receiving-verification-email/). The plan:
1. Use `podcast@askevepodcast.com`, routed to an inbox David reads (Cloudflare Email Routing or similar). Don't put David's personal Gmail in the feed.
2. If the code doesn't arrive within about 4 hours, temporarily switch `itunes:email` to a **new dedicated** Gmail or Outlook address made for the show, claim the show, then switch back if desired. Spotify says a feed change can take "a couple of hours" to be seen.

### 6.4 Spotify ingestion behavior to design around (spec v1.10)
- Spotify polls several times per hour and respects ETag/Last-Modified, so Netlify's static ETags are fine. Allow about an hour for changes to show up.
- **The guid must never change.** It's derived from `<date>_<slug>` and stored in D1.
- Changing an MP3 at the same URL won't be re-fetched. Fixes always use a new key (`.v2.mp3`).

### 6.5 Validate before submitting
1. `curl -sI https://askevepodcast.com/feed.xml` should return 200 with `content-type: application/rss+xml`. Then `curl -s https://askevepodcast.com/feed.xml | xmllint --noout -`.
2. Run the feed through https://podba.se/validate/ and https://www.castfeedvalidator.com/. Apple Podcasts Connect also validates on submit (https://podcasters.apple.com/support/829-validate-your-podcast).
3. Run the `curl -sI` and `curl -r 0-1` checks from §3.4 on every enclosure.

### 6.6 Submit and verify on Spotify for Creators [DAVID]
Per https://support.spotify.com/us/creators/article/claiming-your-podcast-on-spotify-for-creators/:
1. The feed has at least one published item with an enclosure. **Unverified for Spotify specifically.** Apple and most directories require it, so don't submit an empty feed.
2. Go to creators.spotify.com and log in with any Spotify account. Choose **Find an existing show → Somewhere else** and enter `https://askevepodcast.com/feed.xml`. If you already have a show there, use avatar → **Add a new show** (https://support.spotify.com/us/creators/article/adding-a-new-show-to-your-spotify-for-creators-account/).
3. Spotify emails an 8-digit code, from `noreply@hello.creators.spotify.com`, to the `<itunes:email>` address. Paste it in. Check spam. If it never arrives, see §6.3.
4. A show can only be claimed once, so do it from the account David wants to own long-term.
5. Later feed URL changes: https://support.spotify.com/us/creators/article/updating-an-rss-feed-link-or-hosting-provider/.

---
---

## 7. R2 key structure

Public access in R2 is per **bucket** (custom domain or r2.dev), not per prefix, so private material lives in a **separate private bucket** (decided 10:45).

`ask-eve-public` (custom domain `audio.askevepodcast.com`; everything here is world-readable; written only by `publish.yml`):
```
audio/<lang>/<stem>.v<N>.mp3                 # enclosures; immutable, new N per fix
chapters/<lang>/<stem>.v<N>.json             # chapters for the site player
art/cover-3000.v<N>.jpg                      # show art [DAVID]
```
`ask-eve-masters` (no custom domain, r2.dev disabled; S3 credentials and presigned URLs only):
```
raw/<stem>/phone.<ext>                       # untouched phone recording (one segment)
raw/<stem>/phone-NN.<ext>                    # several segments of one session (01..20), stitched on their clocks
raw/<stem>/lav[-NN].<ext>                    # optional lav recording (lav-plus-tts)
raw/<stem>/export[-NN].<ext>                 # optional Grok share-as-podcast export (the grok-share-export check)
staging/<stem>/<stem>.v<N>.mp3               # produced by ingest.yml; publish.yml copies it to the public bucket
backups/d1/<YYYY-MM-DD>.sql                  # wrangler d1 export
```
The upload Worker presigns only keys matching `^raw/\d{4}-\d{2}-\d{2}_[a-z0-9-]{1,60}/(phone|lav|export)(-\d{2})?\.(mp4|mov|m4a|wav|mp3)$` (`KEY_RE` in `workers/upload/src/index.mjs`). The masters bucket is not a git mirror: intermediate WAVs and ASR words stay in the CI run's artifacts.

---

## 8. Scripts, GitHub Actions, secrets

| Unit | Runs where | Does |
|---|---|---|
| `scripts/ingest_share.py` | local / CI | §2 |
| `scripts/stitch_segments.py` | local / CI | §3.0: segments → David track on one clock, `segments.json` |
| `scripts/align_audio.py` | local / CI | §11: faster-whisper words + fuzzy match → `alignment.json` |
| `scripts/revoice_eve.py` | local / CI | §11.4–11.5: TTS clips (xAI, cached by text hash in `out/tts-cache`), placement, time map, `mix.wav`, `revoice.json` |
| `scripts/produce_audio.py` | local / CI | loudnorm, MP3, ID3 chapters, `chapters.json`, `manifest.json` |
| `scripts/verify_placement.py` | local / CI | ASR over the final mix vs the timeline → `placement_check.json` |
| `scripts/d1_upsert.py` | local / CI | `d1_upsert.sql` (episode, people, chapters, turn offsets), always `status='draft'` |
| `scripts/sqlite_apply.py` | local | applies the migration + SQL to a local sqlite file (D1 stand-in) |
| `scripts/render_feed.py` | local / Netlify | `feed.xml`, show notes |
| `scripts/build_site.py` | Netlify | §5 |
| `scripts/run_episode.sh` | local | the whole chain for one episode, no cloud writes |
| `workers/upload/` | Cloudflare Worker | §10: presign phone → R2 PUTs; `/ingest` → GitHub `workflow_dispatch` |
| `.github/workflows/ingest.yml` | GitHub Actions | raw recording(s) → pipeline → MP3 staged in the private bucket → **PR** on `ingest/<stem>` with `episodes/<stem>.md` and `artifacts/<stem>/` |
| `.github/workflows/publish.yml` | GitHub Actions | manual, after the PR is merged: placeholder/AI-label guard → copy MP3 to public (sha256-checked) → D1 upsert + `published` → Netlify build hook |

Workflow safety: every input reaches the shell only through `env:` and is regex-validated first; there is no `pull_request` or `pull_request_target` trigger; actions are pinned by commit sha; ingest can only open PRs (it needs the repo setting "Allow GitHub Actions to create and approve pull requests", deploy guide §6).

**Secrets.** None exist yet. David creates them (09:42); the deploy guide (D-002…D-005) has exact steps. Names match the code:

| Name | Scope / permission | Stored in |
|---|---|---|
| `CLOUDFLARE_ACCOUNT_ID` | not sensitive | GitHub secret; Netlify env; Worker var `CF_ACCOUNT_ID` in `wrangler.toml` |
| `R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY` | R2 S3 token, **Object Read & Write** on `ask-eve-public` and `ask-eve-masters` | GitHub secrets (ingest, publish) |
| `CLOUDFLARE_D1_TOKEN` | API token, **D1: Edit** on this account | GitHub secret (publish) |
| `NETLIFY_BUILD_HOOK` | build hook URL (anyone holding it can trigger builds) | GitHub secret |
| `XAI_API_KEY` | xAI TTS | GitHub secret (ingest); local `/home/box/.secrets/xai.env` style file outside the repo |
| `D1_READ_TOKEN` (+ `D1_DATABASE_ID`) | API token, **D1: Read** only | Netlify env vars |
| `UPLOAD_TOKEN` | random bearer secret (`openssl rand -hex 32`) the Shortcut sends | Worker secret; typed into the Shortcut |
| Worker `R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY` | a **second** R2 S3 token, Object Read & Write on `ask-eve-masters` only (used to presign) | Worker secrets |
| Worker `GITHUB_TOKEN` | fine-grained PAT, repo `daveey/ask-eve` only, **Actions: Read and write**, with an expiry | Worker secret only, never on the phone |

The Worker holds **no Cloudflare API token** (10:45). `wrangler.toml` lists the Worker secrets by name only.

---

## 9. Episodes zero and one (2026-10-23)

### 9.1 Episode zero: `2026-10-23_eve-david-podcast-planning` (`tts-reconstructed`)
- The 304-turn Grok voice session (07:29:52–09:49:22 UTC), sha256 `15d413f1…adcea4`, 151,711 bytes. No audio of it exists.
- Produced fully synthetic: Eve in the `eve` voice, David's lines in the `rex` narrator voice, a spoken disclaimer first ("Voices in this episode are AI-reconstructed from the text transcript."), the AI label in D1, feed, chapters, and show notes. It enters `main` via its ingest PR; `publish.yml` refuses it if any clip was a placeholder.
- It contains profanity (`explicit = true`), an internal canvas URL, and a reference to a private facts file; David decides whether a curated edit is better before publishing.

### 9.2 Episode one: `2026-10-23_the-making-of-eve` (hybrid `mic-plus-tts`)
- Source: this whole Grok Bot chat (`--source-type grokbot-chat`), 09:33–11:00 UTC, with the pasted xAI key redacted. David's side exists only for 10:31–10:49 (two Voice Memos, stitched with a 2 min 39 s gap). Turns whose reply wasn't recovered are `[transcript gap: …]` markers.
- David's real voice only where his recording matches the transcript text; narrator `rex` elsewhere; Eve always TTS. §11.5.

### 9.3 Local rehearsal (no cloud)
```bash
.venv/bin/pytest -q && (cd workers/upload && npm test)
scripts/run_episode.sh <slug> <share_url|share.json> [segments…]      # PLACEHOLDER audio unless XAI_API_KEY is set
.venv/bin/python scripts/build_site.py --db out/local-d1.sqlite --include-drafts --out /tmp/site
```
Real TTS: load the key from a file outside the repo in the shell that runs TTS (`. /path/to/xai.env`); never echo it. Clips are cached by text hash, so reruns don't re-bill.

---

## 10. Phone → R2 upload

### 10.1 Design: iOS Shortcut + presign Worker (`workers/upload/`)
The phone never holds R2 or GitHub credentials. It holds one bearer secret (`UPLOAD_TOKEN`) for a small Worker at `https://upload.askevepodcast.com`:
1. `POST /presign` `{date, slug, ext, bytes, kind?, segment?}` → a presigned PUT URL for `ask-eve-masters/raw/<stem>/<kind>[-NN].<ext>` (1 h). The multi-GB file goes phone → R2 directly; Workers' 100 MB request-body limit makes proxying impossible (https://developers.cloudflare.com/workers/platform/limits/). Signed with `aws4fetch` and `allHeaders: true` so Content-Type is pinned (on the box, aws4fetch 1.0.20 otherwise signed only `host`).
2. `POST /ingest` `{date, slug, share_url, segments?, title?}` → HEADs each object through the R2 binding, then calls GitHub `workflow_dispatch` on `ingest.yml` (ref `main`) with `GITHUB_TOKEN`.
3. Constant-time bearer check, strict key/slug/date/share-URL validation, 4.5 GB single-PUT guard (R2's single-PUT max is 5 GiB), up to 20 segments.

Tests: `cd workers/upload && npm test` (mocked R2 binding and GitHub endpoint).

### 10.2 The iOS Shortcut "Ask Eve upload" [DAVID builds it once]
Step-by-step build instructions: `shortcuts/ask-eve-upload.md`. It runs from the share sheet (Voice Memos, Photos, Files), asks for the slug, date, and share link, presigns and PUTs each shared file (several files become `phone-01`, `phone-02`, … in share-sheet order), then calls `/ingest`. Personal Automations have no "recording finished" trigger, so it is manual.

UNVERIFIED, check on the test file: whether Shortcuts sends the exact Content-Type on a File-body PUT (a mismatch gives R2 403 `SignatureDoesNotMatch`); whether a large PUT survives backgrounding or locking (keep it in the foreground, on Wi-Fi).

### 10.3 Sizes
- Voice Memos (AAC) is roughly 0.5–1 MB/min, so a 2 h session is about 60–120 MB. Size limits don't matter for the primary path.
- Screen recordings (only if used) run about 30–45 MB/min, up to 5–10 GB/h for busy screens (maclaunch.com, UNVERIFIED for Grok's UI), and can exceed the 4.5 GB guard. The Shortcut can encode audio-only on the phone for oversize files.

### 10.4 Alternatives (not primary)
| Alternative | Why not primary |
|---|---|
| Google Drive (what episode one used) | Needs a human or a connector to move the file; no ingest trigger. Fine for tests |
| Cloudflare dashboard upload from iOS Safari | Manual; no ingest trigger |
| A third-party S3 upload app | Puts R2 keys on the phone |

---

## 11. Sync: aligning the verbatim transcript to David's audio

Goal: for every transcript turn, its time in David's recording (raw) and in the published MP3 (edited). Used to place Eve's clips, for chapters, and for "play from here". **The transcript stays verbatim. ASR is used only to find times; it is never published or merged into `episodes/*.md`.**

### 11.1 Anchors and clock trust
- **Coarse anchor:** `com.apple.quicktime.creationdate` in the recording file (Voice Memos `.m4a`, or a screen recording if used) (local time with offset, for example `2026-10-23T10:31:05-0700` → UTC 17:31:05). The box test read it via ffprobe `format_tags`, along with `creation_time` (UTC). The initial guess is `offset_i ≈ createTime_i − creationdate`.
  - Whether creationdate marks recording **start** (expected) or end/save is UNVERIFIED. Check it on the test: if `first speech time ≈ (createTime_0 − creationdate)`, it's the start; if that's ≈ `−duration`, it's the end.
  - The epoch in `RPReplay_Final<epoch>` is likewise unverified (start vs save).
- **Clock trust:** both clocks are network-synced (iPhone automatic time, xAI servers), so expect about a second of difference. More important is `createTime` semantics: whether it's stamped when an utterance starts, ends, or is finalized by ASR is UNVERIFIED. So createTime is only a **prior**, with tolerances of ±30 s.
- **Fine anchor:** the sync phrase "Ask Eve, take one". It's found in ASR words by fuzzy match within the first 120 s, and fixes `sync_offset_seconds` (the raw time of turn 0).
- Real recordings run in real time, so `raw_seconds` vs `createTime` should have a slope of **≈1.000**. A fitted slope outside 0.99–1.01 signals a bad match or a split session, so fail loudly.


### 11.2 `scripts/align_audio.py`: alignment over David-only audio
```
align_audio.py --transcript episodes/<stem>.md --audio david_16k.wav [--asr-words words.json | --save-words words.json]
    [--anchor-from <recording>] [--sync-phrase "ask eve take one"] [--model small] [--match-speakers David]
    [--min-coverage 0.5] [--allow-low-coverage] --out alignment.json
```
1. Parse turns (`[HH:MM:SS] Speaker: …`, multi-paragraph turns supported).
2. ASR with faster-whisper (`small`, int8, CPU, word timestamps, VAD), audio loaded as a float32 array.
3. Only **David's** turns are searched for (`--match-speakers David`): the recording has David only, so Eve's turns are **gaps**. Each Eve turn gets the silence between the David turn before it and the one after it (`method='gap-placed'`).
4. The sync phrase anchors turn 0 if found (`sync-phrase`). Each David turn is fuzzy-matched (RapidFuzz `partial_ratio_alignment`, first ~25 normalized words, windowed after the previous anchor, accept ≥ 70) → `asr-match` with `score`. Very short turns are not matched.
5. Unmatched David turns are interpolated between anchors by `createTime` (`interpolated`), or placed by the global linear fit outside them (`fit`). The fitted slope must be ≈ 1.000 (real time).
6. Coverage below `--min-coverage` exits non-zero unless `--allow-low-coverage`.

### 11.3 Tests
`tests/test_align_audio.py` injects ASR words as JSON (no model): synthetic drops and noise, short turns interpolated, low coverage failing, gap placement for Eve.
**POC result on the box (episode zero's real transcript, 304 turns / 22,035 words, synthetic ASR with drift):** 254 turns matched (coverage 0.836). Matched-turn error: max 2.95 s, median 0.0 s. Unmatched (interpolated) turns: p95 24.7 s, max 38.4 s. Those large gap errors come from the synthetic generator's artificial pacing drift; real audio is wall-clock-linear, so expect much smaller errors. Runtime is about 0.2 s. Earlier iterations failed (0.19 coverage with a naive parser; false matches on short turns with too wide a window). The fixes are in the steps above.

**ASR benchmark on the box (8 vCPU, int8, beam 1, VAD, word timestamps; 381 s of LibriVox public-domain speech degraded to 16 kHz with a 7.5 kHz low-pass plus noise to mimic AirPods):** `base` RTF 0.023 (8.9 s), `small` RTF 0.058 (21.9 s). For 2 h that's about 2.8 min (base) or about 7 min (small) on this box; the GitHub runner is likely about 2× slower (UNVERIFIED). Default to `small`. Versions: faster-whisper 1.2.1, RapidFuzz 3.14.6. An alternative is whisper.cpp (v1.9.5, 2026-10-06) with its full-JSON output; its flags weren't tested here.


### 11.4 Re-voice placement and the time map (`revoice_eve.py`)
- Each Eve clip starts at its gap's start (end of the preceding David turn plus a short lead).
- If the clip is **longer** than its gap, silence is inserted at the gap start so the clip plus `--pause-after` fits; everything later shifts. David is never overlapped.
- If the clip is **shorter**, the remaining natural pause is kept (optionally capped by `--max-pause`, which cuts surplus silence only between ASR-matched David turns, per "cut only pauses", 09:27).
- The result is a **time map** (raw → edited segments, inserts and cuts) written into `revoice.json`. Every edited time (chapters, `turn_offsets.edited_*`) goes through it, never raw times.
- `verify_placement.py` runs ASR over the final mix and checks each turn starts within tolerance of its planned edited time → `placement_check.json`.
- TTS clips are cached by `sha256(voice, cleaned text)`; a failed clip becomes silence and sets `placeholder_audio = true`, which `publish.yml` refuses.

### 11.5 Synthetic and hybrid modes
- **`tts-reconstructed` (`--synthetic`)**: no recording. A spoken disclaimer, then every turn in order (Eve `eve`, David `rex`), 0.6 s apart. `alignment_method = 'tts-exact'`.
- **Hybrid (`--synthetic --real-clips clips.json --audio david_48k.wav`)**: for a session that David recorded only in part. `clips.json` lists David turns whose recording matches the transcript text (`{turn_index: {start, end, score}}` in seconds of the stitched track); those use his recorded voice (`method='asr-match'`, voice `david-recorded`). All other David turns use the narrator; Eve is always TTS; `[transcript gap …]` markers are never voiced. The disclaimer says so, `revoice.json` carries `ai_reconstructed: true`, an `ai_disclosure`, and a `coverage` block (David lines real vs narrator, seconds of each, gap turns), `alignment_coverage` = share of David lines with real audio. `produce_audio.py` adds one chapter per David turn labeled "his recorded voice" or "AI-reconstructed, narrator voice", plus a chapter per transcript gap.
- Matching rule (applied when building `clips.json`; for episode one this was done from the ASR words by a one-off script, not yet a repo CLI): a David turn counts as real only if the ASR text of his recording matches the transcript text (fuzzy ratio ≥ 75). A recording of him talking about the same thing in different words does not count, because the published transcript must match the audio.

---

## Open questions and unverified items
1. Whether David's side records cleanly through AirPods while walking, and whether Voice Isolation is offered in Grok voice (§1.4–1.5).
2. Whether the Grok iOS "share as podcast/video" export has both real voices at full length (the `grok-share-export` check, §1.3).
3. Whether `creationdate` marks recording start (assumed) and what `createTime` marks (utterance start, end, or finalization) (§11.1).
4. faster-whisper accuracy on AirPods audio, and GitHub runner ASR time for a 2 h session (estimated about 15 min, §11.3).
5. Shortcuts: exact Content-Type on a File PUT; large uploads surviving backgrounding (§10.2).
6. xAI TTS rate limits for a 300-clip episode (episode zero ran 303 clips with 4 workers and no failures on the box).
7. Whether Spotify for Creators rejects a feed with zero episodes (assumed yes).
8. Exact Cloudflare dashboard labels for token permissions, and whether R2 offers an object-write-only token.
9. The -19 LUFS mono / -16 stereo targets citation was not re-verified.
10. Whether `podcast@askevepodcast.com` receives Spotify's code (§6.3).
11. "Chan" in the people index is ambiguous; confirm before it becomes a public page.
12. **[DAVID]** Still to decide: default denoise after the walking A/B; whether to buy a lav; show category; author and owner name strings; cover art; whether episode zero ships as-is or as a curated edit.
