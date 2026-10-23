# Ask Eve: audio pipeline design

Status: design draft, revised 2026-10-23 (~10:10 UTC) for **iPhone + AirPods, often while walking outdoors**. Nothing described here exists in the cloud yet. There is no bucket, database, Worker, token, Netlify site, or feed.
Scope: how an Eve–David Grok voice session becomes a transcript, an MP3, metadata, a website, and a Spotify-listed RSS feed.
Conventions: steps tagged **[DAVID]** need David (accounts, tokens, domain, cover art, audio, judgment calls). Untagged steps are code or ops work that Eve or any builder can do. `<domain>` is the not-yet-chosen show domain. `podcast@<domain>` is a placeholder owner email. **UNVERIFIED** marks claims I could not confirm from a primary source or a box test. `<stem>` means `<date>_<slug>`, for example `2026-10-23_eve-david-podcast-planning`.

Decisions this doc follows, from the episode-1 transcript (`episodes/2026-10-23_eve-david-podcast-planning.md`) and later messages from David. Where a decision was later superseded, the later one wins:

| Time (UTC) | Decision | Notes |
|---|---|---|
| 09:17 | "Voice mode doesn't export"; capture system audio; fall back to regenerated voices | The desktop system-audio capture idea (BlackHole/VoiceMeeter) is **superseded by 10:08**. The same turn suggested Spotify for Podcasters hosting with auto-RSS; that is **superseded** by 09:37–09:41 (self-host on R2 plus our own RSS). The ElevenLabs fallback is replaced here by xAI TTS, which has a native `eve` voice (§1.8). |
| 09:22 | Audio-only first; YouTube later | |
| 09:23, 09:28 | Show name **Ask Eve** | Supersedes "Raw Signal". |
| 09:27 | Record raw and cut only pauses. English keeps David's real voice. Other languages use AI voices for both speakers | |
| 09:18–09:19 | Translations per language; a human pass only for languages David reads | |
| 09:37–09:38 | Open-source all code. David did not want episodes in a git repo. Audio goes to S3-like storage. Preserve original content | The "no episodes in git" part is **superseded by 10:08** for text. Audio still never goes in git. |
| 09:41 | Storage = **Cloudflare R2 (audio) + D1 (metadata)**. A scoped API token lives in an env var and is never committed. Search stays local (D1 can't run sqlite-vec/FTS5 the same way) | Supersedes README.md's "S3/R2 plus Postgres". README needs a one-line update. |
| 09:42 | David creates the Cloudflare credentials himself | Eve can't create Cloudflare credentials (§8). |
| 09:43–09:44, 09:47 | Boot script and cutover. Eve owns transcripts going forward. Episode 1 transcript saved and committed | |
| **~10:08** (David, after this doc's first draft) | **The phone is the primary recording device; no Mac. Specifically an iPhone with AirPods (never the phone speaker), often while walking outdoors. The repo is the text source of truth: transcripts stay in git.** | Supersedes the Mac/BlackHole capture path, the R2-canonical transcript proposal, and `sync_episodes.py`. A speakerphone or second-device-in-the-room setup is not a primary option, because Eve is only in David's ears. Android is out of scope. |

---

## 0. Where transcripts canonically live: git (decided ~10:08)

- **The repo is the text source of truth.** `episodes/<stem>.md` is the canonical, verbatim share-text transcript. It is committed and public, as episode 1 already is (`cce3971`).
- Audio and other binary originals (phone recordings, share JSON, masters) **never** go in git. They live in the private R2 bucket (§7).
- D1 stores `transcript_path` (repo-relative) and `transcript_sha256`. The site build and `publish_check.py` fail if the file in the checked-out repo doesn't match the sha in D1.
- `sync_episodes.py` is **dropped**. Nothing needs to copy transcripts between R2 and the repo. If a public copy outside GitHub is ever wanted, `upload_r2.py --mirror-transcript` can write `transcripts/<lang>/<stem>.md` to the public bucket. That copy is optional and never canonical.
- `memory/boot.py`, the local search index, and the site all keep reading `episodes/*.md` from the checkout, with no changes.
- Derived timing data (`turn_offsets.json`, the cut map) lives in R2 `masters/` and D1, not in the transcript. **The transcript text is never rewritten from ASR** (§11).

---

## Build order (what a solo builder does, in order)

1. **[DAVID] Run the 2-minute iPhone + AirPods test (§1.5) first.** It decides the recording path, and everything else can wait on it. Send the resulting files and share link to the box (any route works for a test; §10.4).
2. **[DAVID]** Pick and buy a domain, or choose a subdomain of one he owns. Add it as a zone in Cloudflare; the R2 custom domain must be a zone in the same account (§3.4). Decide `<domain>`.
3. **[DAVID]** Create the Cloudflare tokens, the Worker secrets, the GitHub dispatch token, and the Netlify site/hook (§8 table). Put them in GitHub secrets, Worker secrets, and Netlify env vars. Locally they go in a gitignored `.env`.
4. **[DAVID]** Create the buckets and D1 database (§3.4, §4) and deploy the upload Worker (§10). They're cheap and reversible, but they are cloud resources, so David runs or approves them.
5. Write `scripts/ingest_share.py` (§2). Test it against episode 1 (expected sha in §9).
6. Write `migrations/0001_init.sql` (§4) and apply it.
7. Write `scripts/produce_audio.py` (extract, clean, cut, loudnorm, encode; §3), `scripts/align_audio.py` (§11), and `scripts/upload_r2.py`.
8. Write `workers/upload-url/` and the iOS Shortcut (§10), plus `.github/workflows/ingest.yml` (§8).
9. Build the Astro site and `feed.xml` endpoint (§5, §6). Deploy to Netlify.
10. **[DAVID]** Cover art at 3000×3000 (§6.2). Show description. Category choice.
11. **[DAVID]** Record the trailer on the iPhone using the session ritual (§1.6), then run the Shortcut.
12. Run the dry run (§9), then publish via GitHub Actions (§8).
13. Validate the feed (§6.5). **[DAVID]** Submit to Spotify for Creators and enter the 8-digit code (§6.6).

---

## 1. Recording: iPhone + AirPods (decided ~10:08)

The setup is David on an iPhone with AirPods in, Grok voice mode running Eve, and often walking outdoors. Eve exists only in his ears, so nothing in the room can record her. There's no second device and no speakerphone. Anything that captures Eve has to capture her **inside the phone**.

### 1.1 What Grok gives us today
| Source | Audio? | Status |
|---|---|---|
| Share link JSON `GET https://grok.com/rest/app-chat/share_links/<share_id>` | **No.** It has text turns, `createTime`, and `sender`. Every response has `mediaTypes: ["audio"]` but empty `fileUris`/`fileAttachments`, and there's no audio URL or voice field anywhere | **Verified** 2026-10-23 on episode 1: the saved copy plus a live read-only fetch (HTTP 200, 304 turns). It stays the **transcript** source (§2). |
| Grok voice mode in-app | Eve said at 09:17 "voice mode doesn't export"; at 09:49 the transcript couldn't be exported from the session either | Transcript claim. |
| Grok iOS "share voice conversations as videos or podcasts" | Possibly. An @grok post reported 2026-02-12 (https://www.eonmsk.com/2026/02/12/xai-grok-voice-share-conversations/) says "Share your voice conversations as videos or podcasts. Try it now on iOS!" A Threads post from 2026-02-14 (@oncescuradu) describes the same feature | **UNVERIFIED.** Unknown: whether it carries David's real mic audio or re-synthesized audio, the maximum length, the file format, and whether you get a file or only a link. Covered by the 2-minute test (§1.5, step 6). |

### 1.2 iOS capture options (research)
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

### 1.3 Recommended path and fallback order
`episodes.recording_method` stores which one was used (§4).
1. **`ios-screen-record`**: Screen Recording with Mic On, AirPods, Grok voice. Use it if the §1.5 test hears both voices clearly, indoors **and** walking. One file per session, and both voices are real.
2. **`grok-share-export`**: use it if the share-as-podcast export contains both real voices and the full length. Record the screen anyway as a backup.
3. **`mic-plus-tts`**: use it if the screen recording captures only David's mic. David's real voice comes from the recording. Eve's turns are rendered with xAI TTS (`voice_id: "eve"`, https://docs.x.ai/developers/model-capabilities/audio/text-to-speech) and placed at the aligned offsets (§11), which ducks or replaces any residual Eve bleed. The episode is labeled "Eve's voice re-rendered from the transcript".
4. **`lav-plus-tts`**: the same as 3, but David comes from the on-board lav recording (better outdoors). Alignment uses the lav file.
5. **`reread-plus-tts` / `tts-reconstructed`**: David re-reads his lines, or both voices are synthetic (only with David's explicit OK, labeled as such). Episode 1 can only use these (§9.4). Non-English editions always use `tts-reconstructed` (09:27).

### 1.4 AirPods mic quality: realistic expectations
- In a two-way voice session, AirPods use the Bluetooth hands-free path. Classic HFP is CVSD at 8 kHz or mSBC at 16 kHz (Apple Bluetooth accessory guidelines; https://discussions.apple.com/thread/7794112). Since 2021, AirPods 3 and AirPods Pro have used **AAC-ELD** for calls, mono, about 24 kHz effective with SBR (Pfeiffer, medium.marco.zone; ithinkdiff coverage; exact article URLs not re-checked). Whether Grok's session gets AAC-ELD or falls back to mSBC is UNVERIFIED. ffprobe on the test file shows the container rate (likely 44.1/48 kHz), not the true bandwidth; check a spectrogram.
- iOS 26's "studio-quality audio recording" for AirPods 4 / AirPods 4 ANC / AirPods Pro 2 (Apple Newsroom, 2025-06-09) covers Camera, Voice Memos, Messages dictation, Webex, and compatible camera apps. Apps opt in via `bluetoothHighQualityRecording` / `AVCaptureSession.configuresApplicationAudioSessionForBluetoothHighQualityRecording` (WWDC25 session 251). **It likely does not apply** to Grok voice or the screen recorder (UNVERIFIED).
- **Voice Isolation** mic mode (Control Center → App Controls/Mic Mode; https://support.apple.com/en-us/101993) can cut wind and traffic. Whether it's offered during Grok voice is UNVERIFIED. The test checks it, and if available it should be on while walking.
- Expect **phone-call quality** for David: about 7–12 kHz bandwidth, with on-device noise suppression already applied. Eve is probably captured cleaner (digital app audio), but in the same single mono mix.
- Realistic post-processing (§3.0) consists of an 80 Hz high-pass (wind rumble and handling), light FFT or RNNoise denoise, gentle compression, and two-pass loudnorm. It can tame steady traffic and hiss. It **cannot** fully fix wind gusts, clipped bursts, or dropouts, and pushing denoise harder makes speech sound watery. Keep denoise mild, because Eve passes through the same chain.

### 1.5 The 2-minute test [DAVID] — do this first
Settings: note the iOS version, iPhone model, and AirPods model. Turn on Focus/Do Not Disturb (an incoming call can stop a screen recording; reported by maclaunch, UNVERIFIED) and set Auto-Lock to Never for the test. Control Center → long-press Screen Recording → **Microphone On**.

Run it twice: **A indoors**, then **B walking outside** (same steps, about 2 min each):
1. Put the AirPods in and connect them. Start the screen recording, then wait for the 3-second countdown.
2. Open Grok and start voice mode. Say **"Ask Eve, take one."** Eve replies; for example, she says "Take one" back.
3. Talk normally for about 90 s, with at least 4 back-and-forths. Say the check phrase **"blue giraffe seventeen"** once, and ask Eve to repeat it.
4. Optional: open Control Center and look for Mic Mode / Voice Isolation. Note whether it's offered.
5. Last 15 s: press the side button to lock, keep talking, then unlock. This tests whether locking stops the recording or Grok. Reports conflict: apple.stackexchange 411265 vs the AppleVis iOS 18.1 thread.
6. End the voice session. Share the conversation → copy the link. Also try **share as podcast/video** and save the result to Files.
7. Stop the screen recording, which saves to Photos.
8. Optional C: start Voice Memos, then Grok voice, and note whether either one stops.

Get the files to the box (any route for a test: the §10 Shortcut if it exists, otherwise Files → Google Drive), plus the share link. Then run §9.6. **Pass** means: both voices audible in the extracted audio, ASR finds "take one" and "giraffe" in both speakers' turns, no gaps over 2 s, and `com.apple.quicktime.creationdate` present. Decision: pass → path 1; David only → path 3 (and consider the lav for path 4); nothing usable → test the export (path 2).

### 1.6 Session ritual (every recorded episode) [DAVID]
1. Focus on, AirPods in, phone charged (a 2 h screen recording also uses storage; see §10.3).
2. **Start Screen Recording (Mic On) first**, before opening Grok.
3. Open Grok voice. The first words are **"Ask Eve, take one."** Eve answers. This is the sync anchor (§11) and the cold-open marker. If restarting after a problem, say "take two".
4. Talk. Don't lock the phone unless the test showed that locking is safe.
5. At the end: stop the voice session, **share the conversation** (copy the link), and stop the screen recording.
6. Photos → the recording → Share → **"Ask Eve upload"** Shortcut (§10). Paste the share link when asked. Done; CI takes it from there.

### 1.7 What a mono single-track recording implies
- No per-speaker levels: you can't turn David up and Eve down separately. Use `acompressor`/`speechnorm` on the mix, then loudnorm.
- Denoise affects Eve too. Keep it mild, and A/B it on the walking test.
- Pause-cutting runs on the mix, which is fine, since "cut only pauses" (09:27) doesn't need speaker separation.
- Overlaps (David talking over Eve) can't be untangled. Both voices stay in.
- If ffprobe shows **two** audio streams (app and mic separately, which some iOS versions may produce; UNVERIFIED), use them as tracks and process only the mic.
- Speaker labels come from the share JSON plus alignment (§11), not from diarization.

### 1.8 xAI TTS details (for paths 3–5 and translations)
`POST https://api.x.ai/v1/tts`, `voice_id: "eve"`, MP3 or WAV output. The limit is 60,000 characters per request, and the WebSocket `wss://api.x.ai/v1/tts` streams with `with_timestamps` (https://docs.x.ai/developers/model-capabilities/audio/text-to-speech). Needs `XAI_API_KEY` **[DAVID]**. Generate one clip per Eve turn (`eve_<index>.wav`). For `mic-plus-tts`, place each clip at that turn's `edited_seconds` from `turn_offsets.json`. A cloned David voice (xAI Custom Voices) needs David's consent and a reference clip **[DAVID]**. Non-English editions use Eve's voice plus David's cloned voice, reading the translated transcript (es-ES/es-MX, de, and others per the docs).

---

## 2. `scripts/ingest_share.py`: share URL to `episodes/<date>_<slug>.md`

**Format note:** `episodes/README.md` documents `[00:00:00] **David:** …` (bold names, offsets). The actual episode-1 file uses **plain** `[HH:MM:SS] David: …`, where the time is the **UTC wall-clock** from `createTime`, not an audio offset. `boot.py`'s regex accepts both. The script follows the **actual file**. Fix `episodes/README.md` to match.

CLI:
```
python scripts/ingest_share.py <https://grok.com/share/<id> | path/to/share.json> \
    --slug eve-david-podcast-planning --title "Eve-David podcast planning session" \
    [--people "David Sacks,Marc Andreessen,..."] [--out episodes/] [--raw-dir work/<stem>/] [--force]
```

Behavior:
1. Take `share_id` = last path segment of the URL, then `curl -sS -A "Mozilla/5.0" https://grok.com/rest/app-chat/share_links/<share_id>` (or Python `urllib` with the same UA). This is read-only. Save the raw bytes to `<raw-dir>/share.json`; it is uploaded to the private bucket as `raw/<stem>/share.json`.
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
Test: `tests/test_ingest_share.py` with a tiny synthetic share JSON (3 turns, including one empty one). Add an optional test, skipped if `../share.json` is absent, that asserts the episode-1 sha. Don't commit the 691 KB share JSON (§0: binary/raw originals stay out of git).

---

## 3. Audio production

Script: `scripts/produce_audio.py <work/<stem>/> --slug … [--denoise afftdn|arnndn|none] [--mono]`. It wraps the ffmpeg commands below. All of them were run with **ffmpeg 7.1.5 on the box** against a synthetic iPhone-style `.mov` (H.264 + AAC 44.1 kHz stereo, 120 s, with a `com.apple.quicktime.creationdate` tag). Extraction and denoising happen **on the box/CI, not the phone**. The phone uploads the original file.

### 3.0 Extract and clean (phone recording)
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
One-time setup **[DAVID]**. These create cloud resources, so they are not run as part of this doc:
```bash
npx wrangler@4 login
npx wrangler@4 r2 bucket create ask-eve-public
npx wrangler@4 r2 bucket create ask-eve-masters        # stays private: no custom domain, no r2.dev
# Dashboard: R2 → ask-eve-public → Settings → Custom Domains → add audio.<domain>
```
- Buckets are private by default. **Use a custom domain, not `r2.dev`**: r2.dev is for development, is rate-limited (it can return 429), and has no Cloudflare cache. A custom domain must be a zone in the same Cloudflare account. Sources: https://developers.cloudflare.com/r2/buckets/public-buckets/ and https://developers.cloudflare.com/r2/platform/limits/.
- Through a custom domain, files up to 512 MB are cacheable on Free/Pro/Business (https://developers.cloudflare.com/cache/interaction-cloudflare-products/r2/). Episodes stay well under that.

Upload, option A (wrangler; `r2 object put` is limited to 315 MB per object, https://developers.cloudflare.com/r2/platform/limits/, so never use it for phone raws). **v4 defaults to local storage, so `--remote` is mandatory** (https://developers.cloudflare.com/workers/wrangler/migration/update-v3-to-v4/, flags per https://developers.cloudflare.com/workers/wrangler/commands/r2/):
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
The schema plus the §9 INSERTs (episode 1 and a phone-recorded trailer draft with 2 `turn_offsets` rows) were **re-tested 2026-10-23 with Python's sqlite3 (SQLite 3.46.1) on the box, with foreign keys on**. Checks: 2 episodes, 12 `episode_people`, 2 `turn_offsets`. These were all rejected as expected: an unknown `recording_method`, `alignment_coverage=1.2`, `raw_audio_format='webm'`, `recording_environment='beach'`, a `turn_offsets.speaker` of `'Grok'`, an FK to episode 99, and publishing without audio. Deleting the episode cascades its `turn_offsets` rows. (The `sqlite3` CLI isn't installed on the box.)

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
  transcript_path   TEXT NOT NULL,                     -- repo-relative path; git is canonical (§0)
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
  -- recording provenance (phone-first, §1/§10). All NULL for text-only episodes such as episode 1.
  recording_method  TEXT CHECK (recording_method IN
                      ('ios-screen-record','grok-share-export','mic-plus-tts','lav-plus-tts','reread-plus-tts','tts-reconstructed','none')),
  recording_device  TEXT,                              -- free text, e.g. 'iPhone 16 Pro + AirPods Pro 2'
  recording_environment TEXT CHECK (recording_environment IN ('indoor','walk-outdoor','vehicle','other')),
  raw_audio_key     TEXT,                              -- ask-eve-masters key, e.g. raw/<date>_<slug>/phone.mp4
  raw_audio_format  TEXT CHECK (raw_audio_format IN ('mp4','mov','m4a','wav','mp3')),
  raw_audio_bytes   INTEGER,
  raw_audio_sha256  TEXT CHECK (raw_audio_sha256 IS NULL OR length(raw_audio_sha256) = 64),
  recording_started_at TEXT,                           -- ISO-8601 UTC from com.apple.quicktime.creationdate (coarse)
  sync_offset_seconds REAL,                            -- raw-audio time (s) at which turn 0 starts
  alignment_method  TEXT CHECK (alignment_method IN ('asr-fuzzy','linear-fit','creationdate-only','tts-exact')),
  alignment_coverage REAL CHECK (alignment_coverage IS NULL OR (alignment_coverage >= 0 AND alignment_coverage <= 1)),
  asr_model         TEXT,                              -- e.g. 'faster-whisper small int8' (alignment only, never published text)
  processing_chain  TEXT,                              -- exact ffmpeg -af string used for the master
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

CREATE TABLE turn_offsets (                          -- written by align_audio.py via d1_upsert.py
  episode_id      INTEGER NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
  turn_index      INTEGER NOT NULL CHECK (turn_index >= 0), -- 0-based order in the transcript file
  speaker         TEXT NOT NULL CHECK (speaker IN ('David','Eve')),
  created_at      TEXT NOT NULL,                       -- share-JSON createTime (UTC)
  raw_seconds     REAL,                                -- offset in the raw phone audio
  edited_seconds  REAL,                                -- offset in the published MP3 (after pause-cut map)
  method          TEXT NOT NULL CHECK (method IN ('asr','sync-phrase','interpolated','fit','tts-exact')),
  score           REAL,                                -- fuzzy-match score 0-100; NULL unless method='asr'
  PRIMARY KEY (episode_id, turn_index)
);

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
- `turn_offsets` holds one row per transcript turn (§11). It's derived data: rebuild it by re-running `align_audio.py`. Nothing in it is canonical.
- None of this is deployed. These fields were folded into `0001` instead of a `0002` migration, because no database exists yet.
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
4. Pages (from `site/README.md`): `/` (episode list), `/episodes/<slug>/` (player `<audio src=audio_url preload="none">`, chapter list, per-turn "play from here" links from `turn_offsets.edited_seconds`, transcript rendered from the `.md`, people sidebar), `/people/<slug>/`, and `/feed.xml` (§6). The transcript comes straight from `episodes/` in the checkout, since git is canonical (§0). Each page checks `transcript_sha256` against D1 and fails the build on a mismatch.

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

Public access in R2 is per **bucket** (custom domain or r2.dev), not per prefix. So "private prefixes" live in a **separate private bucket**. This is a design inference from the R2 public-bucket docs: one bucket's custom domain exposes every key in it.

`ask-eve-public` (custom domain `audio.<domain>`; everything here is world-readable):
```
audio/<lang>/<stem>.v<N>.mp3                 # enclosures; immutable, new N per fix
chapters/<lang>/<stem>.v<N>.json             # chapters for the site player
art/cover-3000.v<N>.jpg                      # show art
art/episodes/<stem>.v<N>.jpg                 # optional episode art
transcripts/<lang>/<stem>.md                 # OPTIONAL mirror only (upload_r2.py --mirror-transcript); git is canonical (§0)
```
`ask-eve-masters` (no custom domain, r2.dev disabled; S3-API credentials and presigned URLs only):
```
raw/<stem>/phone.<ext>                       # untouched iPhone file (mp4/mov, or m4a if encoded on-phone); "original content preserved"
raw/<stem>/lav.wav                           # optional on-board lav recording (path 4)
raw/<stem>/share.json                        # Grok share JSON as fetched
raw/<stem>/export.<ext>                      # optional Grok share-as-podcast export (path 2)
masters/<stem>/master.wav                    # cleaned + pause-cut, pre-loudnorm
masters/<stem>/cutmap.json                   # kept segments [orig_start, orig_end, new_start]
masters/<stem>/turn_offsets.json             # §11 output (also loaded into D1)
masters/<stem>/asr_words.json                # ASR words + timestamps; alignment input only, never published
drafts/<lang>/<stem>.md                      # unreviewed translations
backups/d1/<YYYY-MM-DD>.sql                  # wrangler d1 export
```
The upload Worker (§10) can only presign keys matching `^raw/\d{4}-\d{2}-\d{2}_[a-z0-9-]{1,60}/(phone|lav|export)\.(mp4|mov|m4a|wav|mp3)$`. Episode 1's stem is `2026-10-23_eve-david-podcast-planning`, the same as its episode file. It has no `raw/` audio.

---

## 8. Scripts, GitHub Actions, secrets

Scripts. All are sketches to write: Python 3, stdlib plus `faster-whisper` and `rapidfuzz` for alignment, otherwise shelling out to `aws`, `ffmpeg`, and `npx wrangler@4`:

| Script / unit | Runs where | Does |
|---|---|---|
| `scripts/ingest_share.py` | local or CI | §2: share URL → `episodes/<stem>.md` + `share.json` |
| `scripts/produce_audio.py` | local or CI | §3.0 extract/clean, §3.1 pause-cut + `cutmap.json`, §3.2–3.3 loudnorm/MP3/chapters, `manifest.json` |
| `scripts/align_audio.py` | local or CI | §11: ASR word timestamps + fuzzy alignment → `turn_offsets.json` |
| `scripts/upload_r2.py` | local or CI | masters to the private bucket; MP3 and chapters to the public bucket (§3.4, §7); `--mirror-transcript` optional |
| `scripts/d1_upsert.py` | local or CI | Upserts episodes (including §4 recording/alignment fields), people, tags, chapters, and `turn_offsets` from the front matter + `manifest.json` + `turn_offsets.json`. Always `status='draft'` |
| `scripts/publish_check.py` | CI | Pre-publish checks (below) |
| `workers/upload-url/` | Cloudflare Worker | §10: presign phone→R2 PUTs, `/ingest` → GitHub dispatch |
| `.github/workflows/ingest.yml` | GitHub Actions | Raw phone file → draft episode (below) |
| `.github/workflows/publish.yml` | GitHub Actions | The only place an episode is flipped to `published` |

`record.sh` and `sync_episodes.py` from the first draft are **dropped** (superseded 10:08).

`.github/workflows/ingest.yml` (sketch). It's triggered by the Worker's `workflow_dispatch` call (§10.2), or by hand from the Actions tab:
```yaml
name: ingest
on:
  workflow_dispatch:
    inputs:
      stem:      { description: "<date>_<slug>", required: true }
      raw_key:   { description: "raw/<stem>/phone.<ext>", required: true }
      share_url: { description: "https://grok.com/share/<id>", required: true }
      title:     { description: "episode title", required: false }
permissions: { contents: write }      # commits episodes/<stem>.md
concurrency: ingest-${{ inputs.stem }}
jobs:
  ingest:
    runs-on: ubuntu-latest
    timeout-minutes: 120
    env:
      AWS_ACCESS_KEY_ID: ${{ secrets.R2_ACCESS_KEY_ID }}
      AWS_SECRET_ACCESS_KEY: ${{ secrets.R2_SECRET_ACCESS_KEY }}
      AWS_DEFAULT_REGION: auto
      R2: https://${{ secrets.CLOUDFLARE_ACCOUNT_ID }}.r2.cloudflarestorage.com
      CLOUDFLARE_API_TOKEN: ${{ secrets.CLOUDFLARE_API_TOKEN }}
      CLOUDFLARE_ACCOUNT_ID: ${{ secrets.CLOUDFLARE_ACCOUNT_ID }}
      STEM: ${{ inputs.stem }}
      RAW_KEY: ${{ inputs.raw_key }}
      SHARE_URL: ${{ inputs.share_url }}
      TITLE: ${{ inputs.title }}
    steps:
      - uses: actions/checkout@v4
      - run: |
          [[ "$STEM" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}_[a-z0-9-]{1,60}$ ]] || exit 1
          [[ "$RAW_KEY" =~ ^raw/${STEM}/(phone|lav|export)\.(mp4|mov|m4a|wav|mp3)$ ]] || exit 1
          [[ "$SHARE_URL" =~ ^https://grok\.com/share/[A-Za-z0-9_-]+$ ]] || exit 1
      - run: sudo apt-get update && sudo apt-get install -y ffmpeg && pip install faster-whisper==1.2.1 rapidfuzz==3.14.6
      - uses: actions/cache@v4
        with: { path: ~/.cache/huggingface, key: whisper-small }
      - run: |
          mkdir -p work/$STEM && aws s3 cp "s3://ask-eve-masters/$RAW_KEY" "work/$STEM/" --endpoint-url "$R2"
          python3 scripts/ingest_share.py "$SHARE_URL" --slug "${STEM#*_}" --title "${TITLE:-Untitled}" --out episodes/ --raw-dir work/$STEM/
          python3 scripts/produce_audio.py work/$STEM --slug "${STEM#*_}" --denoise afftdn --stage clean
          python3 scripts/align_audio.py --transcript episodes/$STEM.md --audio work/$STEM/asr16k.wav \
            --anchor-from work/$STEM/$(basename "$RAW_KEY") --model small --out work/$STEM/turn_offsets.json
          python3 scripts/produce_audio.py work/$STEM --slug "${STEM#*_}" --stage master,encode --offsets work/$STEM/turn_offsets.json
          python3 scripts/upload_r2.py work/$STEM
          python3 scripts/d1_upsert.py "$STEM" --manifest work/$STEM/manifest.json --offsets work/$STEM/turn_offsets.json
      - run: |
          git config user.name "ask-eve-bot"; git config user.email "podcast@<domain>"
          git add episodes/$STEM.md && git commit -m "Add transcript $STEM (ingest)" && git push   # [DAVID]: direct to main vs PR
```
- Notes: the inputs are regex-validated before use. Every input reaches the shell only through `env:`, never as `${{ }}` inside `run:`, which prevents script injection. `ingest_share.py` refuses to overwrite a differing file. Prefer a PR (`peter-evans/create-pull-request`) if David wants to review transcripts before they land on main **[DAVID]**.
- Runner sizing: the standard public-repo `ubuntu-latest` runner has 4 vCPU / 16 GB RAM / about 14 GB free disk (GitHub docs; not re-checked today). A 5 GB raw plus WAVs fits. 2 h of 48 kHz 24-bit mono WAV is about 1 GB. ASR time for 2 h is about 7 min on this 8-vCPU box with `small`, so expect roughly 15 min on the runner (an estimate, UNVERIFIED).

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
| `CLOUDFLARE_API_TOKEN` | Cloudflare dash → My Profile → API Tokens → Create Token → Custom token | Account → **D1: Edit**, limited to this account. Deploying the Worker from a laptop or CI additionally needs **Workers Scripts: Edit** (or use `wrangler login` locally). Add **Workers R2 Storage: Edit** only if CI should run `wrangler r2` (it doesn't need to; it uses the S3 keys). Exact UI labels unverified | GitHub Actions secret; local `.env` |
| `CLOUDFLARE_ACCOUNT_ID` | Cloudflare dash → account home or Workers & Pages overview (sidebar "Account ID") | Not sensitive, but kept as a secret for convenience | GitHub secret; Netlify env; `.env` |
| `R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY` | Cloudflare dash → R2 → Manage API tokens → Create API token | **Object Read & Write**, applied to `ask-eve-public` and `ask-eve-masters` only. The secret is shown once (https://developers.cloudflare.com/r2/api/tokens/) | GitHub secrets; `.env` |
| `NETLIFY_BUILD_HOOK` | Netlify → site → Site configuration → Build & deploy → Continuous deployment → Build hooks → Add build hook (branch `main`) | Anyone holding the URL can trigger builds | GitHub secret |
| `D1_READ_TOKEN` (extra) | Cloudflare API Tokens, second custom token | Account → **D1: Read** only | Netlify env var |
| `XAI_API_KEY` (extra, fallback/translations only) | xAI console | TTS | local `.env`; GitHub secret if CI renders `mic-plus-tts` |
| `UPLOAD_AUTH_SECRET` | David generates it (`openssl rand -hex 32`) | Bearer secret the Shortcut sends to the upload Worker | Worker secret (`npx wrangler@4 secret put UPLOAD_AUTH_SECRET`); typed into the iOS Shortcut "Text" action |
| `PRESIGN_R2_ACCESS_KEY_ID` / `PRESIGN_R2_SECRET_ACCESS_KEY` | Cloudflare dash → R2 → Manage API tokens, a **second** token | **Object Read & Write on `ask-eve-masters` only**. Write-only isn't offered as a separate level, as far as I could tell (UNVERIFIED) | Worker secrets |
| `GH_DISPATCH_TOKEN` | GitHub → Settings → Developer settings → Fine-grained tokens | Repository `daveey/ask-eve` only, **Actions: Read and write** (https://docs.github.com/rest/actions/workflows#create-a-workflow-dispatch-event). Set an expiry and rotate it | Worker secret only, never on the phone |

Add GitHub secrets via repo → Settings → Secrets and variables → Actions, or with `gh secret set CLOUDFLARE_API_TOKEN`. Add `.env`, `work/`, `out/`, and `models/*.rnnn` (unless pinned deliberately) to `.gitignore` before the first recording.

---

## 9. First-episode dry run (episode 1, 2026-10-23)

### 9.1 Facts computed on the box, 2026-10-23
- File: `episodes/2026-10-23_eve-david-podcast-planning.md` (in commit `cce3971`).
- `sha256sum` → `15d413f1fc34283cc1bb50f31d694395979201996d803a5871ee1020b3adcea4`.
- 151,711 bytes; 304 turns (152 David, 152 Eve).
- Session from `2026-10-23T07:29:52.883Z` to `2026-10-23T09:49:22.567Z`, a span of 2 h 19 m 30 s (8,370 s). That is the conversation span, **not** an audio duration.
- Audio: **none exists** (§1.1). Episode 1 predates the phone path; it was not screen-recorded.
- `ingest_share.py` logic regenerates the file byte for byte from the share JSON (§2).

### 9.2 Local rehearsal (no cloud)
```bash
cd ask-eve
python3 scripts/ingest_share.py https://grok.com/share/c2hhcmQtMg_90f617bb-3947-4fc2-8300-c34b6eeed2d1 \
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
  transcript_path, transcript_sha256, transcript_bytes, recording_method, status)
VALUES ('eve-david-podcast-planning',
  'ask-eve:2026-10-23_eve-david-podcast-planning',
  '2026-10-23', 'Eve-David podcast planning session', 1, 'en',
  'https://grok.com/share/c2hhcmQtMg_90f617bb-3947-4fc2-8300-c34b6eeed2d1',
  '2026-10-23T07:29:52.883Z', '2026-10-23T09:49:22.567Z', 304,
  'episodes/2026-10-23_eve-david-podcast-planning.md',
  '15d413f1fc34283cc1bb50f31d694395979201996d803a5871ee1020b3adcea4', 151711, 'none', 'draft');

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
The stances are copied from `index/people.md`. Box check: 1 episode row (`recording_method='none'`) and 12 `episode_people` rows. `UPDATE … SET status='published'` is correctly rejected by the CHECK, because there's no audio.

### 9.4 What episode 1 becomes [DAVID decides]
Episode 1 has no audio, and the publish guard keeps it out of the feed. Options:
- **(Recommended)** Keep episode 1 as `status='draft'` in D1 and render it on the site as a **text-only "pilot" transcript page**. Launch the feed with a **2–3 minute trailer recorded on the iPhone** using the §1.6 ritual: `episode_type='trailer'`, David's real voice, and Eve live if the §1.5 test passed, otherwise Eve via TTS (`mic-plus-tts`). Then make the next fully recorded session the first full episode. This keeps David's real voice and doesn't fake a recording.
- Re-voice episode 1 with `reread-plus-tts` (David re-reads 152 turns) or `tts-reconstructed` (fully synthetic, explicitly labeled). Either way, rerun §3 and §9.5.
- Try the Grok iOS share-as-podcast export on episode 1's conversation (path 2). If it yields real audio of the full session, episode 1 can ship as `grok-share-export`.

Also consider before publishing episode 1 anywhere: it's about 2 h 19 m and mostly planning and production talk. It also contains internal details, such as an internal canvas URL (line 485) and a reference to the private persistent-facts file holding David's Gmail (line 927). It's already in the public repo, but a curated edit might make a better listening episode.

### 9.5 Trailer draft row (phone-recorded; tested with placeholder values)
Run after `ingest.yml` (or the §9.6 commands) on the real trailer. The values below are **placeholders** (sha, bytes, offsets) that were used only to test the schema:
```sql
INSERT INTO episodes (slug, guid, date, title, episode_type, explicit, language, source_url,
  session_started_at, session_ended_at, turn_count, transcript_path, transcript_sha256, transcript_bytes,
  recording_method, recording_device, recording_environment, raw_audio_key, raw_audio_format,
  raw_audio_bytes, raw_audio_sha256, recording_started_at, sync_offset_seconds, alignment_method,
  alignment_coverage, asr_model, processing_chain, status)
VALUES ('trailer', 'ask-eve:2026-10-24_trailer', '2026-10-24', 'Ask Eve: trailer', 'trailer', 0, 'en',
  'https://grok.com/share/<id>', '2026-10-24T17:31:20.000Z', '2026-10-24T17:33:40.000Z', 8,
  'episodes/2026-10-24_trailer.md', '0000000000000000000000000000000000000000000000000000000000000000', 2000,
  'ios-screen-record', 'iPhone + AirPods Pro 2', 'walk-outdoor', 'raw/2026-10-24_trailer/phone.mp4', 'mp4',
  2147060, 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', '2026-10-24T17:31:05Z', 14.2,
  'asr-fuzzy', 0.88, 'faster-whisper small int8',
  'highpass=f=80,afftdn=nr=12:nf=-40:tn=1,acompressor=threshold=-21dB:ratio=3:attack=10:release=200:makeup=3', 'draft');
INSERT INTO turn_offsets (episode_id, turn_index, speaker, created_at, raw_seconds, edited_seconds, method, score)
SELECT id, 0, 'David', '2026-10-24T17:31:20.000Z', 14.2, 13.5, 'sync-phrase', 97 FROM episodes WHERE slug='trailer';
INSERT INTO turn_offsets (episode_id, turn_index, speaker, created_at, raw_seconds, edited_seconds, method, score)
SELECT id, 1, 'Eve', '2026-10-24T17:31:23.000Z', 16.9, 16.0, 'asr', 91 FROM episodes WHERE slug='trailer';
```

### 9.6 iPhone test-recording dry run (exact commands; local, no cloud)
For the §1.5 2-minute test file. `RPReplay_Final<epoch>.MP4` is the usual iOS screen-recording name; it may be `.mov`/`.MP4`, so check. Steps 1–4 were run on the box (2026-10-23) against a synthetic `.mov` with the same structure. Steps 5–9 use scripts marked *not yet written*, which this doc specifies, or cloud endpoints that don't exist yet.
```bash
STEM=2026-10-24_airpods-test-a; W=work/$STEM; mkdir -p $W
cp ~/Downloads/RPReplay_Final*.MP4 $W/phone.mp4            # or: aws s3 cp s3://ask-eve-masters/raw/$STEM/phone.mp4 $W/ --endpoint-url "$R2"
sha256sum $W/phone.mp4; stat -c %s $W/phone.mp4             # -> raw_audio_sha256, raw_audio_bytes; bytes/min gives the real bitrate for §10.3
# 1. streams + anchor: expect 1 (or 2?) audio streams; note codec/rate; read creationdate
ffprobe -v error -show_entries format=duration,size:format_tags=com.apple.quicktime.creationdate,creation_time \
  -show_entries stream=index,codec_type,codec_name,sample_rate,channels -of json $W/phone.mp4
# 2. extract (§3.0)
ffmpeg -i $W/phone.mp4 -map 0:a:0 -vn -c:a copy $W/phone_audio.m4a
ffmpeg -i $W/phone.mp4 -map 0:a:0 -vn -ac 1 -ar 48000 -c:a pcm_s24le $W/work.wav
ffmpeg -i $W/work.wav -ac 1 -ar 16000 -c:a pcm_s16le $W/asr16k.wav
# 3. is Eve there at all? loudness over time + a spectrogram to eyeball both voices and bandwidth
ffmpeg -nostats -i $W/work.wav -af ebur128=peak=true -f null - 2>&1 | grep -E "^ +(I|LRA|Peak):"
ffmpeg -i $W/work.wav -lavfi showspectrumpic=s=1600x600:legend=1 $W/spectrum.png
# 4. denoise A/B (listen to both; walking file especially)
ffmpeg -i $W/work.wav -af "highpass=f=80,afftdn=nr=12:nf=-40:tn=1,acompressor=threshold=-21dB:ratio=3:attack=10:release=200:makeup=3" $W/clean.wav
ffmpeg -i $W/work.wav -af "highpass=f=80,arnndn=m=models/sh.rnnn:mix=0.8,acompressor=threshold=-21dB:ratio=3:attack=10:release=200:makeup=3" $W/clean_rnn.wav
# 5. transcript from the share link (§2)
python3 scripts/ingest_share.py https://grok.com/share/<id> --slug airpods-test-a --title "AirPods test A" --out /tmp/ep/ --raw-dir $W/   # not yet written
# 6. align (§11): look for the sync phrase, coverage, both speakers matched
python3 scripts/align_audio.py --transcript /tmp/ep/$STEM.md --audio $W/asr16k.wav --anchor-from $W/phone.mp4 \
  --model small --out $W/turn_offsets.json --report                                                                      # not yet written
# 7. produce (pause-cut, loudnorm, MP3, chapters) and check
python3 scripts/produce_audio.py $W --slug airpods-test-a --denoise afftdn --offsets $W/turn_offsets.json             # not yet written
ffmpeg -nostats -i out/$STEM.v1.mp3 -af ebur128=peak=true -f null - 2>&1 | grep -E "^ +(I|Peak):"; ffprobe -v error -show_chapters out/$STEM.v1.mp3
# 8. local D1 upsert (draft), no cloud
npx wrangler@4 d1 migrations apply ask-eve-meta --local
python3 scripts/d1_upsert.py $STEM --manifest $W/manifest.json --offsets $W/turn_offsets.json --local                 # not yet written
# 9. (only after §8/§10 setup) real upload path: run the "Ask Eve upload" Shortcut on the phone, or:
curl -fsS -X POST https://upload.<domain>/presign -H "Authorization: Bearer $UPLOAD_AUTH_SECRET" \
  -H 'Content-Type: application/json' -d "{\"date\":\"2026-10-24\",\"slug\":\"airpods-test-a\",\"ext\":\"mp4\",\"bytes\":$(stat -c %s $W/phone.mp4)}"
curl -fsS -X PUT -H 'Content-Type: video/mp4' --upload-file $W/phone.mp4 "<put_url from the response>"
```
Pass/fail per §1.5. Record the results (stream count, whether Eve was audible, coverage, whether locking was safe, whether Voice Isolation was offered, MB/min) in this doc's open questions.

### 9.7 Cloud dry run (after §8 setup), for the trailer or any episode with audio
1. Run the Shortcut (§10) on the trailer recording. Check that `ingest.yml` ran, the transcript commit (or PR) landed, and the D1 row is `draft` with the §4 recording fields filled in.
2. Verify `ebur128` is about -16 LUFS, and that `ffprobe` shows the chapters.
3. Run the §3.4 `curl -sI` / `curl -r 0-1` checks against `https://audio.<domain>/…`.
4. Actions → publish → slug. Check: the D1 row is `published`; the Netlify deploy is green; `feed.xml` has the item; validators pass (§6.5).
5. **[DAVID]** Spotify submission (§6.6). About an hour later, check that the episode appears and chapters show.

---

## 10. Phone → R2 upload

### 10.1 Design (recommended: iOS Shortcut + presign Worker)
The phone never holds R2 or GitHub credentials. It holds one bearer secret (`UPLOAD_AUTH_SECRET`) for a small Worker that does three things:
1. It **presigns** a direct PUT to the private bucket, so the multi-GB file goes phone → R2 without passing through the Worker. Workers' request-body limit is 100 MB on Free/Pro (https://developers.cloudflare.com/workers/platform/limits/), so proxying wouldn't work. R2 presigned URLs: https://developers.cloudflare.com/r2/api/s3/presigned-urls/.
2. It **validates** the key. Only `raw/<date>_<slug>/(phone|lav|export).<ext>` is allowed.
3. On `/ingest`, it HEADs the object via an R2 binding, then calls GitHub `workflow_dispatch` on `ingest.yml` with `GH_DISPATCH_TOKEN` (`POST /repos/{owner}/{repo}/actions/workflows/{id}/dispatches`, body `{"ref":"main","inputs":{…}}`; https://docs.github.com/rest/actions/workflows#create-a-workflow-dispatch-event).

`workers/upload-url/wrangler.toml` (deploy is **[DAVID]**; none of this exists yet):
```toml
name = "ask-eve-upload"
main = "src/index.js"
compatibility_date = "2026-10-01"
routes = [{ pattern = "upload.<domain>", custom_domain = true }]
[[r2_buckets]]
binding = "MASTERS"
bucket_name = "ask-eve-masters"
[vars]
GH_REPO = "daveey/ask-eve"
# secrets (npx wrangler@4 secret put …): UPLOAD_AUTH_SECRET, PRESIGN_R2_ACCESS_KEY_ID,
#   PRESIGN_R2_SECRET_ACCESS_KEY, CLOUDFLARE_ACCOUNT_ID, GH_DISPATCH_TOKEN
```
`workers/upload-url/src/index.js`. This sketch was **run locally on the box** (Node 20, `aws4fetch@1.0.20`) with a mocked R2 binding and GitHub endpoint. Results: presign returned 200 with `X-Amz-SignedHeaders=content-type;host`; a bad secret got 401; slug `../x` and ext `exe` got 400; 5 GB got 413; `/ingest` dispatched `stem=2026-10-24_trailer`; a missing object got 404; a non-Grok share URL got 400. **Finding:** Cloudflare's aws4fetch example (https://developers.cloudflare.com/r2/examples/aws/aws4fetch/) shows `X-Amz-SignedHeaders=content-type;host` with `signQuery: true` alone. On the box, aws4fetch 1.0.20 produced `SignedHeaders=host` for that exact call, and needed `allHeaders: true` to pin Content-Type. Keep `allHeaders: true`, and check `X-Amz-SignedHeaders` in the response.
```js
import { AwsClient } from "aws4fetch";

const BUCKET = "ask-eve-masters";
const TYPES = { mp4: "video/mp4", mov: "video/quicktime", m4a: "audio/mp4", wav: "audio/wav", mp3: "audio/mpeg" };
const SINGLE_PUT_MAX = 4.5 * 1024 ** 3;          // stay under R2's 5 GiB single-PUT limit
const json = (o, s = 200) => new Response(JSON.stringify(o), { status: s, headers: { "content-type": "application/json" } });

function authed(req, env) {
  const h = req.headers.get("authorization") || "";
  const a = new TextEncoder().encode(h), b = new TextEncoder().encode(`Bearer ${env.UPLOAD_AUTH_SECRET}`);
  if (a.length !== b.length) return false;
  let d = 0; for (let i = 0; i < a.length; i++) d |= a[i] ^ b[i];   // constant-time compare
  return d === 0;
}
function keyFor({ date, slug, kind = "phone", ext }) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date) || !/^[a-z0-9-]{1,60}$/.test(slug) ||
      !["phone", "lav", "export"].includes(kind) || !TYPES[ext]) return null;
  return `raw/${date}_${slug}/${kind}.${ext}`;
}

export default {
  async fetch(req, env) {
    if (req.method !== "POST") return json({ error: "POST only" }, 405);
    if (!authed(req, env)) return json({ error: "unauthorized" }, 401);
    const url = new URL(req.url), body = await req.json().catch(() => ({}));
    const key = keyFor(body);
    if (!key) return json({ error: "bad date/slug/kind/ext" }, 400);
    const r2 = new AwsClient({ accessKeyId: env.PRESIGN_R2_ACCESS_KEY_ID, secretAccessKey: env.PRESIGN_R2_SECRET_ACCESS_KEY,
                               service: "s3", region: "auto" });
    const objUrl = `https://${env.CLOUDFLARE_ACCOUNT_ID}.r2.cloudflarestorage.com/${BUCKET}/${key}`;

    if (url.pathname === "/presign") {
      if (body.bytes > SINGLE_PUT_MAX) return json({ error: "too large for single PUT; encode audio-only on phone or use multipart", key }, 413);
      const u = new URL(objUrl); u.searchParams.set("X-Amz-Expires", "3600");
      const content_type = TYPES[body.ext];
      // allHeaders: true makes content-type part of the signature (aws4fetch otherwise signs only host for signQuery)
      const signed = await r2.sign(new Request(u, { method: "PUT", headers: { "content-type": content_type } }),
                                   { aws: { signQuery: true, allHeaders: true } });
      return json({ key, put_url: signed.url, content_type, expires_in: 3600 });
    }

    if (url.pathname === "/ingest") {
      const head = await env.MASTERS.head(key);              // R2 binding; confirms the PUT landed
      if (!head) return json({ error: "object not found", key }, 404);
      if (!/^https:\/\/grok\.com\/share\/[A-Za-z0-9_-]+$/.test(body.share_url || "")) return json({ error: "bad share_url" }, 400);
      const r = await fetch(`https://api.github.com/repos/${env.GH_REPO}/actions/workflows/ingest.yml/dispatches`, {
        method: "POST",
        headers: { authorization: `Bearer ${env.GH_DISPATCH_TOKEN}`, accept: "application/vnd.github+json",
                   "x-github-api-version": "2022-11-28", "user-agent": "ask-eve-upload-worker" },
        body: JSON.stringify({ ref: "main", inputs: { stem: `${body.date}_${body.slug}`, raw_key: key,
                                                      share_url: body.share_url, title: (body.title || "").slice(0, 120) } }) });
      return json({ key, bytes: head.size, dispatched: r.status === 204, github_status: r.status }, r.status === 204 ? 200 : 502);
    }
    return json({ error: "not found" }, 404);
  },
};
```
Dependency: `aws4fetch` (`npm i aws4fetch`), as in Cloudflare's R2 examples. R2 single-PUT max is 5 GiB, and multipart parts are 5 MiB–5 GiB with ≤10,000 parts (https://developers.cloudflare.com/r2/platform/limits/). R2 aborts incomplete multipart uploads after 7 days by default.

### 10.2 The iOS Shortcut "Ask Eve upload" [DAVID builds it once]
Shortcuts app → + → name it "Ask Eve upload" → (i) → **Show in Share Sheet** on, accepting **Media** and **Files**. Personal Automations have no "screen recording finished" trigger (https://support.apple.com/guide/shortcuts/apdfbdbd7123/ios), so the trigger is **manual from the share sheet**, at §1.6 step 6. Actions, in order:
1. **Receive** Media/Files input from the Share Sheet. If there's no input: **Ask For** Files.
2. **Text** → paste `UPLOAD_AUTH_SECRET` and set a variable `secret`. It's stored inside the Shortcut, so don't share the Shortcut via iCloud link.
3. **Ask for Input** (Text) "Slug (lowercase-with-dashes)" → variable `slug`.
4. **Format Date** (Current Date, custom format `yyyy-MM-dd`) → `date`. Optionally **Ask for Input** with that as the default, for recordings made before midnight.
5. **Get Clipboard** → **Ask for Input** (URL) "Grok share link", default = Clipboard → `share`.
6. **Get Details of Files** → File Extension → **Change Case** lowercase → `ext`. Then **Get Details of Files** → File Size → `bytes`. If the size is reported in a unit rather than bytes, use **Get Details** "File Size" plus **Format File Size** with bytes; UNVERIFIED, so check on the test.
7. **If** `bytes` > 4,500,000,000: **Encode Media** (Audio Only, M4A) → set input to the result and `ext` = `m4a` (howtogeek documents the Encode Media audio-only option). This is the only on-phone processing, and only for oversize files.
8. **Get Contents of URL** `https://upload.<domain>/presign`. Method **POST**. Headers `Authorization: Bearer [secret]`. Request Body **JSON**: `date`, `slug`, `ext`, `bytes` (Number), `kind` = `phone`. Then **Get Dictionary Value** `put_url` and `content_type`.
9. **Get Contents of URL** `[put_url]`. Method **PUT**. Header `Content-Type: [content_type]`. Request Body **File** = the input (support.apple.com/guide/shortcuts/apd58d46713f/ios lists GET/POST/PUT/PATCH/DELETE, headers, and JSON/Form/File bodies).
10. **Get Contents of URL** `https://upload.<domain>/ingest`. POST, the same header, JSON `date`, `slug`, `ext`, `kind`, `share_url` = `share`, and `title` (Ask for Input, optional).
11. **Show Notification**: "Uploaded [bytes] → ingest started" (or show the error JSON).

UNVERIFIED and to be checked on the test file: whether Shortcuts sends the exact Content-Type header on a File-body PUT (a mismatch makes R2 return 403 `SignatureDoesNotMatch`; the fix is to drop `allHeaders` and the header); whether a multi-GB PUT completes when Shortcuts goes to the background or the screen locks (keep it in the foreground; use Wi-Fi); and how long the share sheet takes to hand over a multi-GB Photos video.

### 10.3 Sizes
- iOS screen recordings run about **30–45 MB/min** for calm screen content, so **1.8–2.7 GB/h**, and up to 5–10 GB/h for busy screens (maclaunch.com/iphone-screen-recording-file-size, 2026-07; UNVERIFIED for Grok's animated voice UI). A 2 h session is about 3.6–5.4 GB, which **can exceed** the 4.5 GB guard. The test file gives the real MB/min (`stat` ÷ duration).
- Mitigation, in order: (a) keep the phone on a still screen; (b) step 7's on-phone audio-only encode (`raw_audio_format='m4a'`; about 1 MB/min at AAC 128k); (c) multipart. A `/mpu/create`, `/mpu/part?n=`, `/mpu/complete` set of Worker endpoints that presign `UploadPart` URLs is easy to add, but Shortcuts has no obvious action for byte-range slicing a file. UNVERIFIED, so multipart isn't the primary plan.
- Uploads over cellular while walking are slow and may stall. Run the Shortcut on Wi-Fi.

### 10.4 Alternatives (not recommended as primary)
| Alternative | Why not primary |
|---|---|
| Cloudflare dashboard upload from iOS Safari | Manual. The per-file dashboard upload limit is UNVERIFIED (historically 300 MB), and there's no ingest trigger. Fine for a 2-minute test |
| Photos → Files → Google Drive/iCloud, then CI pulls | Needs Drive/iCloud API credentials in CI and an extra trigger. iCloud has no simple server API |
| A third-party S3 upload app | Puts R2 keys on the phone |
| AirDrop/USB to a computer | There's no Mac in this workflow (10:08) |

---

## 11. Sync: aligning the verbatim transcript to the phone audio

Goal: for every transcript turn, its time in the raw audio (`raw_seconds`) and in the edited MP3 (`edited_seconds`). These are used for chapters, "play from here" links, and placing TTS clips (path 3). **The transcript stays the verbatim share text. ASR output is used only to find times. It's never published and never merged into `episodes/*.md`.**

### 11.1 Anchors and clock trust
- **Coarse anchor:** `com.apple.quicktime.creationdate` in the screen-recording file (local time with offset, for example `2026-10-23T10:31:05-0700` → UTC 17:31:05). The box test read it via ffprobe `format_tags`, along with `creation_time` (UTC). The initial guess is `offset_i ≈ createTime_i − creationdate`.
  - Whether creationdate marks recording **start** (expected) or end/save is UNVERIFIED. Check it on the test: if `first speech time ≈ (createTime_0 − creationdate)`, it's the start; if that's ≈ `−duration`, it's the end.
  - The epoch in `RPReplay_Final<epoch>` is likewise unverified (start vs save).
- **Clock trust:** both clocks are network-synced (iPhone automatic time, xAI servers), so expect about a second of difference. More important is `createTime` semantics: whether it's stamped when an utterance starts, ends, or is finalized by ASR is UNVERIFIED. So createTime is only a **prior**, with tolerances of ±30 s.
- **Fine anchor:** the sync phrase "Ask Eve, take one". It's found in ASR words by fuzzy match within the first 120 s, and fixes `sync_offset_seconds` (the raw time of turn 0).
- Real recordings run in real time, so `raw_seconds` vs `createTime` should have a slope of **≈1.000**. A fitted slope outside 0.99–1.01 signals a bad match or a split session, so fail loudly.

### 11.2 `scripts/align_audio.py` (sketch)
```
python scripts/align_audio.py --transcript episodes/<stem>.md --audio work/<stem>/asr16k.wav
    [--asr-words work/<stem>/asr_words.json]       # reuse ASR output instead of recomputing
    [--anchor-from work/<stem>/phone.mp4 | --anchor-utc 2026-10-24T17:31:05Z]
    [--sync-phrase "ask eve take one"] [--model small|base] [--compute-type int8]
    [--cutmap work/<stem>/cutmap.json]             # adds edited_seconds
    [--min-coverage 0.5] [--report] --out work/<stem>/turn_offsets.json
```
Algorithm (the POC lives at `/workspace/align_poc.py` on the box and isn't committed):
1. **Parse** the episode body into turns. Turns can span several paragraphs, so split on `^\[HH:MM:SS\] (David|Eve): ` and take the full-precision `createTime` from `share.json` when it's available.
2. **ASR:** faster-whisper (`WhisperModel(model, device="cpu", compute_type="int8")`, `transcribe(audio, word_timestamps=True, vad_filter=True, beam_size=1, language="en")`). Load the audio as a float32 numpy array (via `wave` + numpy): on the box, PyAV-based loading raised a `metadata_errors` TypeError. Write `asr_words.json` as `[{w, start, end, p}]`.
3. **Normalize** both sides: lowercase, strip punctuation, collapse whitespace.
4. **Sync phrase:** `rapidfuzz.fuzz.partial_ratio_alignment("ask eve take one", first ~300 ASR words)`. If score ≥ 80 → anchor turn 0, `method='sync-phrase'`.
5. **Per turn, in order:** query = the first 25 normalized words (skip queries under 12 characters, such as "Stop. Keep going."). The search window starts at the previous anchor and spans `1.2 × words since that anchor + 150` ASR words (cap 1,500). Use `partial_ratio_alignment`, accept score ≥ 70, then refine the start with the first `difflib` matching block. `method='asr'`.
6. **Gaps:** interpolate unmatched turns between neighbouring anchors in proportion to `createTime` (`method='interpolated'`). Before the first or after the last anchor, use the global linear fit of `raw_seconds ~ createTime` (`method='fit'`). Then enforce monotonic order.
7. **Fallbacks:** if coverage is below `--min-coverage`, exit non-zero unless `--allow-fit`. In that case use the creationdate prior plus the fit only (`alignment_method='linear-fit'` or `'creationdate-only'`). For TTS-built episodes, offsets are exact from clip placement (`'tts-exact'`).
8. **Edited times:** map each `raw_seconds` through `cutmap.json` (§11.4).

Output `turn_offsets.json` (also loaded into D1 `turn_offsets` and the §4 episode fields):
```json
{ "stem": "2026-10-24_trailer", "transcript_sha256": "…", "raw_audio_sha256": "…",
  "asr_model": "faster-whisper small int8", "recording_started_at": "2026-10-24T17:31:05Z",
  "sync_offset_seconds": 14.2, "alignment_method": "asr-fuzzy", "alignment_coverage": 0.88,
  "fit": { "slope": 1.0002, "intercept_s": 13.9 },
  "turns": [ { "i": 0, "speaker": "David", "created_at": "2026-10-24T17:31:20.000Z",
               "raw_seconds": 14.2, "edited_seconds": 13.5, "method": "sync-phrase", "score": 97 } ] }
```

### 11.3 Tests (`tests/test_align_audio.py`; no ASR model needed, since words are injected as JSON)
- Synthetic: build ASR words from a transcript, with 0.35 s per word, 12% of words dropped, 10% replaced by noise tokens, and a known offset. Assert coverage ≥ 0.8, matched error ≤ 3 s, monotonic output, and the sync phrase setting turn 0.
- Short turns ("Stop. Keep going.") are interpolated, never fuzzy-matched.
- Low coverage (shuffled words) exits non-zero; `--allow-fit` yields `linear-fit`.
- Cut-map remap: a time inside a removed pause snaps to the next kept segment's start.
- Optional and skipped by default: a real-audio test on the §1.5 test recording, if it's present locally.

**POC result on the box (episode 1's real transcript, 304 turns / 22,035 words, synthetic ASR with drift):** 254 turns matched (coverage 0.836). Matched-turn error: max 2.95 s, median 0.0 s. Unmatched (interpolated) turns: p95 24.7 s, max 38.4 s. Those large gap errors come from the synthetic generator's artificial pacing drift; real audio is wall-clock-linear, so expect much smaller errors. Runtime is about 0.2 s. Earlier iterations failed (0.19 coverage with a naive parser; false matches on short turns with too wide a window). The fixes are in the steps above.

**ASR benchmark on the box (8 vCPU, int8, beam 1, VAD, word timestamps; 381 s of LibriVox public-domain speech degraded to 16 kHz with a 7.5 kHz low-pass plus noise to mimic AirPods):** `base` RTF 0.023 (8.9 s), `small` RTF 0.058 (21.9 s). For 2 h that's about 2.8 min (base) or about 7 min (small) on this box; the GitHub runner is likely about 2× slower (UNVERIFIED). Default to `small`. Versions: faster-whisper 1.2.1, RapidFuzz 3.14.6. An alternative is whisper.cpp (v1.9.5, 2026-10-06) with its full-JSON output; its flags weren't tested here.

### 11.4 Pause-cut map
`cutmap.json` is a list of kept segments `[orig_start, orig_end, new_start]`, written by `produce_audio.py` as it renders the keep-list (§3.1). Remap: find the segment containing `t`, then `edited = new_start + (t − orig_start)`. If `t` falls in a removed gap, use the next segment's `new_start`. Chapters and `edited_seconds` are always computed through this map, never from raw times.

---

## Open questions and unverified items
1. **#1 risk: does iOS Screen Recording (Mic On) capture Grok voice-mode audio of Eve while AirPods are the mic?** Unverified; Apple says some apps block capture, and Bluetooth-mic reports are negative (§1.2). The first action is the §1.5 test.
2. Whether the recording continues when the phone locks, and whether Grok voice keeps running when locked (§1.5 step 5).
3. Whether the recording has one mixed audio stream or two (§1.7). What the real AirPods bandwidth is in a Grok session (AAC-ELD vs mSBC), and whether Voice Isolation is offered (§1.4).
4. Grok iOS "share as podcast/video": does it have real audio of both speakers, the full length, a downloadable file? (§1.1)
5. Whether `com.apple.quicktime.creationdate` / `RPReplay_Final<epoch>` mark start or end, and whether `createTime` marks utterance start, end, or finalization (§11.1).
6. Real screen-recording MB/min for Grok's UI, and whether a 2 h file stays under the 4.5 GB single-PUT guard (§10.3).
7. Shortcuts: whether it sends the exact Content-Type on a File PUT, how it reports File Size, whether multi-GB uploads survive backgrounding, and whether it can slice a file for multipart (§10.2–10.3).
8. Voice Memos concurrency with Grok voice. RØDE/DJI on-board files to iPhone without a computer (§1.2).
9. GitHub runner ASR speed (estimated about 2× slower than the box) (§8, §11.3).
10. Whether Spotify for Creators rejects a feed with zero episodes. Assumed yes; unverified for Spotify specifically.
11. Exact Cloudflare dashboard labels for token permissions, and whether R2 offers an object-write-only token (§8).
12. The citation for the -19 LUFS mono / -16 stereo targets was not re-verified here.
13. Whether custom-domain email (`podcast@<domain>`) receives Spotify's code (§6.3).
14. "Chan" in the people index is ambiguous. Confirm it before it becomes a public `/people/chan/` page.
15. **[DAVID]** Decisions still needed: run the §1.5 test; domain; ingest commits direct to main vs PR (§8); default denoise after the A/B (§3.0); buy a lav backup only if the test fails; what episode 1 becomes (§9.4); show category; author and owner name strings; cover art; whether to require an Actions environment approval for publish.
16. Doc fixes to make: README.md ("Postgres" → D1, per 09:41); `episodes/README.md` (format is plain `David:` with UTC wall-clock times, not bold names with offsets).
