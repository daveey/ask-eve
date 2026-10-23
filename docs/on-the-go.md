# Interview Eve on the go (iPhone + AirPods)

From a locked phone to a finished, reviewed episode with as few touches as iOS allows.
Two Shortcuts do the work: **Eve Start** and **Eve Done** (signed files in `shortcuts/on-the-go/`,
built by `shortcuts/build_onthego.py`). Neither file contains a secret: Eve Done reads the upload token at run time from
`iCloud Drive/Shortcuts/Ask Eve/upload_token.txt`, which only exists in David's iCloud.

## The flow

| When | You do | What happens |
|---|---|---|
| Start | **Press the Action Button** (set to the *Screen Recording* control) | 3-second countdown, then the screen recording runs with the microphone on |
| | **Tap "Eve Start"** on the Lock Screen (Face ID unlocks as you look) | Do Not Disturb on (a call would stop the recording), the capture is marked open (`Ask Eve/active.txt`), Grok opens |
| | Tap Grok's voice button, say **"Ask Eve, take one."** | That phrase is the sync anchor the aligner looks for |
| Talk | Walk and talk. Leaving Grok mid-interview is safe: Eve Done sees no finished recording and leaves the capture open | |
| End | End the voice session, **⋯ → Share → Copy link** | The Grok share link is the transcript source; no Shortcut can fetch it for you |
| | **Tap the red recording pill → Stop** | The recording is saved to Photos |
| | Swipe home | The "Grok is closed" automation runs **Eve Done**: newest screen recording since Eve Start → audio only (m4a) → upload → ingest. Do Not Disturb off, notification "uploaded" |
| After | nothing | GitHub Actions (`ingest.yml`): copies the raw audio to **Google Drive → Ask Eve/&lt;date&gt;_&lt;slug&gt;/**, transcript from the share link, alignment, Eve re-voiced where needed, loudness-normalised MP3, show notes, PR; then the MP3, transcript and show notes go to the same Drive folder and you get an **email** ("Ask Eve: &lt;stem&gt; is ready" with Drive + PR links, or "ingest failure" with the run log) |
| Publish | Listen, merge the PR, `gh workflow run publish.yml -f stem=<stem>` | Unchanged (deploy guide §9) |

Touches: 2 presses from a locked phone to recording (Action Button, Eve Start), +1 tap on Grok's voice button.
After you stop: zero, as long as you copied the share link first (if the clipboard has no `grok.com/share/` link, Eve Done asks for it).

## One-time setup on the iPhone (about 3 minutes)

1. **Install both Shortcuts:** open each `.shortcut` file (Files → iCloud Drive → *Ask Eve on the go*, or the links in the install message) → *Add Shortcut*.
   Check that `Files → iCloud Drive → Shortcuts → Ask Eve → upload_token.txt` exists (it is synced from the Mac; never share it).
2. **Microphone on for screen recordings:** Control Center → long-press *Screen Recording* → *Microphone: On*. iOS remembers it.
3. **Action Button:** Settings → Action Button → *Controls* → *Screen Recording*.
   (No Action Button? Use the Lock Screen's other control slot, or Back Tap below for Eve Start and Control Center for recording.)
4. **Lock Screen:** long-press the Lock Screen → *Customize* → *Lock Screen* → remove the flashlight (−) → + → *Shortcut* → **Eve Start**.
5. **Back Tap** (Settings → Accessibility → Touch → Back Tap): *Double Tap* → **Eve Done** (manual fallback), *Triple Tap* → **Eve Start**.
6. **Zero-touch end:** Shortcuts → Automation → + → *App* → **Grok** → *Is Closed* → *Run Immediately* → **Eve Done**.
   It does nothing unless Eve Start opened a capture and a screen recording newer than that has finished.
7. Optional, better: edit Eve Start and replace *Open Grok* with Grok's own voice action (search "Grok" in the action list,
   e.g. *Talk to Grok* / voice mode). That drops the extra tap. It isn't in the signed file because Grok's action identifier
   can only be read on a phone with Grok installed.

Any recording can also go through by hand: Photos → the recording → Share → **Eve Done**.

## What iOS can't do (plainly)

- **No Shortcut can start or stop a screen recording.** Only the system control can, which is why the Action Button holds
  *Screen Recording* and stopping is the red pill. There is no "recording finished" trigger either; the Grok-closed automation stands in for it.
- **One Action Button.** It can hold the recording control *or* a Shortcut, not both, so Eve Start lives on the Lock Screen.
- **Lock Screen controls can run Eve Start, but opening Grok needs the phone unlocked** (Face ID does it as you look; with a passcode only, type it).
- **Whether the screen recording contains Eve's voice is unverified for Grok voice mode** (`docs/audio-pipeline.md` §1.5).
  If iOS captures only your mic, nothing breaks: the pipeline re-voices Eve from the transcript (xAI TTS) and labels it.
- **Interruptions stop the recording:** a phone call that breaks through Do Not Disturb (Favorites, repeated calls), running out of storage,
  or (reports conflict) locking the phone. Leave the screen on during a take; see §1.5 step 5 for the lock test.
- **The Grok share link has to be copied by hand.** Grok exposes no share-link action or API to Shortcuts.
- **Background time:** an automation-run Shortcut that encodes and uploads a long recording can be suspended if you leave the phone
  for a long time; if no "uploaded" notification arrives, double-tap the back (Eve Done) on Wi-Fi. Upload URLs expire after 30 minutes.
- **Audio quality:** AirPods in a two-way voice session use the call path (roughly phone-call bandwidth); see §1.4.
- Screen recordings are big (a 1 h take can be several GB of video). Eve Done uploads audio only (~1 MB/min), and the video stays in Photos;
  delete it when the episode is out.

## Where things live

| Thing | Where |
|---|---|
| Raw upload (canonical) | R2 `ask-eve-masters/raw/<date>_<slug>/phone.m4a` |
| Drive copies | Google Drive → `Ask Eve/<date>_<slug>/` (raw m4a first, then MP3, transcript, show notes). The ingest uses a `drive.file` OAuth token, so it can only see files it created |
| Email | Worker `POST /notify` (Cloudflare `send_email`, only to addresses verified in the account's Email Routing) |
| Secrets | GitHub: `GDRIVE_CLIENT_ID`, `GDRIVE_CLIENT_SECRET`, `GDRIVE_REFRESH_TOKEN`, `NOTIFY_TOKEN`. Worker: `NOTIFY_TOKEN`, `NOTIFY_TO` (+ the deploy guide's four) |
| Slug | `walk-HHmm` (local time); the date is the phone's local date. The stem is re-based to the transcript's UTC date if they differ |

## Test without the phone (same calls as Eve Done)
```bash
UPLOAD_TOKEN=... scripts/eve_done_same_path.sh recording.mp4 https://grok.com/share/<id>
```
It encodes audio-only m4a, then makes Eve Done's presign → PUT → ingest calls in the same order with the same bodies.
