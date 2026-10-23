# Ask Eve deploy log

Entries by David's deploy agent (Hermes, kanban t_50bd4351). Secrets are never shown; scope/secret **names** only.
Deploy token: broker-held `user/cf-askeve` (Ask Eve account b4d4a3f8b5b4d5ba074f878f59f16543 only), vended per command, never stored in GitHub.

### D-001 done 2026-10-23T11:11Z
- commands: `node --version`, `npx --yes wrangler@4 --version`, `npx wrangler@4 whoami`, zones API
- outputs: node v26.7.0, wrangler 4.152.0, gh 2.63.2; account "Askeve@dbloom.in's Account" b4d4a3f8b5b4d5ba074f878f59f16543;
  askevepodcast.com zone 7594bf9fb7ad9b0286ea33abd47324f5 `active`, NS ezra/june.ns.cloudflare.com (askeve.fm 601fb93f… also active)
- notes: whoami warns "Unable to retrieve email" (token lacks User Details Read); harmless.

### D-002 done 2026-10-23T11:13Z
- commands: §2 as written (`r2 bucket create` ×2, `cors set --file`, `domain add --zone-id`, `dev-url disable` ×2)
- outputs: buckets ask-eve-masters, ask-eve-public; CORS rule origins askevepodcast.com + www, GET/HEAD, Range, expose
  Content-Length/Content-Range/ETag, max-age 3600; audio.askevepodcast.com ownership/ssl `active`; r2.dev disabled on both; masters has no domain.
- R2 S3 pair: derived from CF API token `askeve-r2-s3` (Workers R2 Storage Bucket Item Write on the two buckets; key id = token id,
  secret = sha256(token)). No dashboard visit. Recovery copy: broker scope `user/askeve-r2-s3`.

### D-003 done 2026-10-23T11:16Z
- outputs: D1 `ask-eve-meta` database_id 8e196805-d1fa-438e-a8f6-17562c50631c (WNAM); `0001_init.sql` applied (15 commands);
  tables: _cf_KV chapters d1_migrations episode_people episode_tags episodes people sqlite_sequence tags translations turn_offsets;
  `d1 migrations list`: no migrations to apply.
- PR: https://github.com/daveey/ask-eve/pull/1 (merged by the deploy agent under David's standing approval).

### D-004 done 2026-10-23T12:29Z (GITHUB_TOKEN supplied outside this agent)
- deploy: "Uploaded ask-eve-upload", custom domain upload.askevepodcast.com, version 622640b5-b832-41e2-a1bd-858fc4c0b65a;
  redeployed from main 42fe45c (on-the-go /notify) as version 087f5cfa-94c8-4f59-8dc0-bd89bd0cfd61
- `npm test`: `node --test test/` fails on Node 26 (Cannot find module …/test); fixed to `node --test test/*.test.mjs` in PR #2 → pass 10, fail 0
  (13/0 after #6)
- secret list: GITHUB_TOKEN, NOTIFY_TO, NOTIFY_TOKEN, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, UPLOAD_TOKEN. This agent set the R2 pair and
  UPLOAD_TOKEN; GITHUB_TOKEN and the NOTIFY_* pair were set by the build side (on-the-go, PR #6) while this deploy ran. Creating the PAT here
  was blocked: the browser signed in as daveey asked for GitHub sudo re-auth (passkey / GitHub Mobile / authenticator).
- smoke: no-auth presign 401; presign → raw/2026-10-23_smoke-test/phone.m4a, PUT, audio/mp4, 1800; PUT 200; get + cmp "same";
  ingest no-such-upload → 404 "object not found; PUT the file first"; smoke object deleted.
- Worker → GitHub dispatch: `/ingest` for e2e-test → `{"dispatched":true,"github_status":204}`; that run (39953630323) was cancelled on purpose,
  because D-007 had already run the same ingest.
- UPLOAD_TOKEN: broker scope `user/askeve-upload-token` (not a file).

### D-005 done 2026-10-23T11:19Z
- secrets: CLOUDFLARE_ACCOUNT_ID, CLOUDFLARE_D1_TOKEN (`askeve-d1-rw`, D1 Write only), R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, XAI_API_KEY
  (+ D1_READ_TOKEN, CLOUDFLARE_PAGES_TOKEN for D-006)
- workflow permissions: `{"default_workflow_permissions":"write","can_approve_pull_request_reviews":true}`
- verified CLOUDFLARE_D1_TOKEN runs publish.yml's `wrangler d1 execute ask-eve-meta --remote --file … --config workers/upload/wrangler.toml`.

### D-006 done (deviation: Cloudflare Pages) 2026-10-23T11:25Z — email destination pending
- no Netlify account: Netlify's GitHub OAuth app was never authorized on daveey, so creating one would be a new signup. Site moved to
  Cloudflare Pages project `ask-eve` (https://ask-eve.pages.dev), built by `.github/workflows/site.yml` (PR https://github.com/daveey/ask-eve/pull/2).
- site.yml run https://github.com/daveey/ask-eve/actions/runs/39945811372: success
- DNS: CNAME askevepodcast.com → ask-eve.pages.dev (proxied), CNAME www → ask-eve.pages.dev (proxied); Pages custom domains attached.
- `curl -sI https://askevepodcast.com/feed.xml`: `HTTP/2 200`, `content-type: application/rss+xml; charset=utf-8`
- Email Routing: already enabled on askevepodcast.com (status ready) with a rule for podcast@askevepodcast.com marked "(temporary)"
  forwarding to a verified gmail.com destination. Rule 7 forbids David's personal Gmail; left untouched pending David's choice of inbox.

### D-007 done 2026-10-23T12:27Z
- upload: Worker presign + PUT 200 of `raw/2026-10-23_e2e-test/phone.m4a` (7.6 MB). The guide's 30 s memo can't pass: run
  https://github.com/daveey/ask-eve/actions/runs/39946344161 failed at the alignment gate (`coverage 0.0 < 0.5`). The test track therefore
  holds David's 152 turns voiced with xAI TTS (`rex`) at their transcript offsets (8372 s).
- run https://github.com/daveey/ask-eve/actions/runs/39947415283: alignment 0.9926, then crashed in revoice
  (`Eve clip at 3014.120s overlaps the previous clip`). Fixed in https://github.com/daveey/ask-eve/pull/9 (test added, 98 passed).
- run https://github.com/daveey/ask-eve/actions/runs/39950300319 (workflow_dispatch, main 42fe45c): completed/success
- PR https://github.com/daveey/ask-eve/pull/10 "Ingest: 2026-10-23_e2e-test". Files: only text (alignment/chapters/manifest/segments/
  show-notes/timeline/d1_upsert.sql under artifacts/2026-10-23_e2e-test/ and episodes/2026-10-23_e2e-test.md), no audio. Body: Turns 304,
  Alignment coverage 0.9926 (asr-david-gaps), mic-plus-tts, "real xAI TTS; placeholder turns: []".
- main before and after: 42fe45c3a2b2ddaf8669a2523d1722f007910601 (unchanged by the run)
- cleanup: PR #10 closed without merging, branch deleted; deleted `raw/2026-10-23_e2e-test/phone.m4a` and
  `staging/2026-10-23_e2e-test/2026-10-23_e2e-test.v1.mp3` from ask-eve-masters. The run's on-the-go steps also copied the test raw/MP3 to
  Google Drive `Ask Eve/2026-10-23_e2e-test/` and sent the ready email (and a failure email for the cancelled run 39953630323).

### D-008 not started (blocked on David merging ingest PR #4, per the table)

### Cleanup
- deleted the pre-guide leftovers (empty, created by David's agent before this guide existed): R2 bucket `askeve-audio`, D1 `askeve` (bc9948de-…).
- §11 #15: the guide's grep counts its own regexes (4 hits, all patterns in deploy-guide.md / ingest.yml / notify.py); with
  `github_pat_[A-Za-z0-9_]{20}` the count is 0.

## Answers to "Questions for Eve" (PR #11), from David via Hermes, 2026-10-23

1. **Email routing.** Yes, approved. David chose his personal Gmail as the destination for now and will split it out later. The rule `podcast@askevepodcast.com → daveey@gmail.com` stays; a test mail arrived in the inbox (§11 row 13 PASS). Treat safety rule 7 as waived for this one address until David names an owner inbox.
2. **On-the-go secrets.** Yes. Please add a D-009 entry naming each one (`NOTIFY_TOKEN`, `NOTIFY_TO`, Worker `GITHUB_TOKEN`, GitHub `GDRIVE_CLIENT_ID` / `GDRIVE_CLIENT_SECRET` / `GDRIVE_REFRESH_TOKEN` / `NOTIFY_TOKEN`): who created it, what it can do, and how to rotate it.
3. **Back-to-back Eve turns (#9).** Your design call, Eve. #9 only stops the crash; if the intent is different, change it and add a test.
4. **Test slugs.** Yes: skip the Drive copy and the notify email for `e2e-test` and `smoke-*` slugs. Hermes trashed the leftover `Ask Eve/2026-10-23_e2e-test/` folder in David's Drive.

Also done: episode 0 (PR #4) was merged by David, and publish.yml was dispatched for `2026-10-23_eve-david-podcast-planning`.
