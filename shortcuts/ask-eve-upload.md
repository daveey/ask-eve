# iOS Shortcut: "Ask Eve upload"

Uploads one recording (or several segments of one session) from the iPhone Share Sheet straight to the
private R2 bucket `ask-eve-masters`, then asks the Worker to start `ingest.yml`, which opens a PR.
The phone never holds R2 or GitHub keys: it holds only `UPLOAD_TOKEN`, which can do nothing but request
upload URLs for `raw/<date>_<slug>/...` and trigger ingest.

**Signing:** a `.shortcut` file can only be signed on macOS (`shortcuts sign --mode anyone --input in.shortcut --output out.shortcut`).
No Mac was available here, so no signed file is provided. Build it once by hand from the steps below (about 10 minutes).

Worker: `https://upload.askevepodcast.com` (code: `workers/upload/src/index.mjs`).

## Before you start
- Get `UPLOAD_TOKEN` from whoever deployed the Worker (docs/deploy-guide.md §4). Keep it only inside this Shortcut.
- Copy the Grok share link of the conversation to the clipboard *before* running the Shortcut
  (Grok app → the conversation → Share → copy link). It looks like `https://grok.com/share/...`.

## Build it (Shortcuts app → + → name it "Ask Eve upload")

Shortcut details (ⓘ): turn on **Show in Share Sheet**; Share Sheet types: **Media** and **Files** only.

| # | Action | Settings |
|---|---|---|
| 1 | **Receive** *Media and Files* input from *Share Sheet* | If there's no input: *Stop and Respond* "Share a recording" |
| 2 | **Text** | Paste `Bearer <UPLOAD_TOKEN>`. Rename the variable **Auth** |
| 3 | **Ask for Input** | Type *Text*, prompt "Episode slug (lowercase-with-dashes)", default `the-making-of-eve`. Variable **Slug** |
| 4 | **Format Date** | *Current Date*, Custom format `yyyy-MM-dd`. Variable **Day** (UTC caveat: after 5 pm Pacific the UTC date is tomorrow; type the date the conversation started if that matters) |
| 5 | **Get Clipboard** | Variable **ShareURL** |
| 6 | **Count** | *Items* in *Shortcut Input*. Variable **N** |
| 7 | **Repeat with Each** item in *Shortcut Input* | Everything below up to step 13 is inside the loop |
| 8 | **Get Details of Files** | *File Extension* of *Repeat Item* → variable **Ext** (must be m4a, mp4, mov, wav or mp3) |
| 9 | **Get Contents of URL** | URL `https://upload.askevepodcast.com/presign`, Method **POST**, Headers: `Authorization` = **Auth**; Request Body **JSON**: `date` = **Day**, `slug` = **Slug**, `ext` = **Ext**, and, only if **N** > 1, `segment` (Number) = *Repeat Index* |
| 10 | **Get Dictionary Value** | `put_url` from the result → **PutURL**; also get `headers` → `Content-Type` → **CT** |
| 11 | **Get Contents of URL** | URL **PutURL**, Method **PUT**, Headers: `Content-Type` = **CT** (must match exactly: the signature covers it), Request Body **File** = *Repeat Item* |
| 12 | **If** the result has any value / status is not 200 | **Show Alert** "Upload failed for segment *Repeat Index*" and **Stop** |
| 13 | **End Repeat** | |
| 14 | **Get Contents of URL** | URL `https://upload.askevepodcast.com/ingest`, Method **POST**, Headers `Authorization` = **Auth**; JSON body: `date` = **Day**, `slug` = **Slug**, `ext` = **Ext**, `share_url` = **ShareURL**, and only if **N** > 1: `segments` (Number) = **N** |
| 15 | **Show Result** | The JSON: `dispatched: true`, `branch: ingest/<date>_<slug>`, and a link to the workflow runs |

Notes
- Several segments: share them together from Files (select → Share). They become `phone-01.m4a`, `phone-02.m4a`, ... in share-sheet order;
  ingest re-orders them by recording creation time anyway (`scripts/stitch_segments.py`). All segments must have the same extension.
- Errors you may see: `401` wrong token; `400` bad slug/date/extension or the clipboard isn't a `https://grok.com/share/...` link;
  `404` from `/ingest` means a PUT didn't land; `413` file over 4.5 GiB (share audio-only).
- Presigned URLs expire after 30 minutes; a slow upload over cellular may need a rerun on Wi-Fi.

## Test without the phone (same calls as the Shortcut)
```bash
read -rs UPLOAD_TOKEN   # paste, Enter (not echoed, not in history)
R=$(curl -fsS https://upload.askevepodcast.com/presign -H "Authorization: Bearer $UPLOAD_TOKEN" \
     -d '{"date":"2026-10-23","slug":"smoke-test","ext":"m4a"}')
curl -fsS -X PUT -H "Content-Type: audio/mp4" --upload-file test.m4a "$(echo "$R" | jq -r .put_url)"
curl -fsS https://upload.askevepodcast.com/ingest -H "Authorization: Bearer $UPLOAD_TOKEN" \
     -d '{"date":"2026-10-23","slug":"smoke-test","ext":"m4a","share_url":"https://grok.com/share/<id>"}'
```

## Unsigned reference (structure only, unverified)
Action identifiers that the steps above map to, for anyone generating a plist on a Mac before `shortcuts sign`
(identifiers from community documentation, not verified on iOS 27):
`is.workflow.actions.gettext`, `is.workflow.actions.ask`, `is.workflow.actions.format.date`, `is.workflow.actions.getclipboard`,
`is.workflow.actions.count`, `is.workflow.actions.repeat.each`, `is.workflow.actions.getfiledetails`
(hypothetical name), `is.workflow.actions.downloadurl` (Get Contents of URL), `is.workflow.actions.getvalueforkey`,
`is.workflow.actions.conditional`, `is.workflow.actions.alert`, `is.workflow.actions.showresult`.
