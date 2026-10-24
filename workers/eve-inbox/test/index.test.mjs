// workers/eve-inbox/test/index.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import worker from "../src/index.mjs";
import {
  leakCheck,
  leakCheckReadingItem,
  computeLeakCheckSha256,
} from "../leak_check.js";

function toBase64Url(bytes) {
  const bin = Array.from(bytes, (b) => String.fromCharCode(b)).join("");
  return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function strToBase64Url(str) {
  return toBase64Url(new TextEncoder().encode(str));
}

async function createAccessFixture({
  adminEmail = "admin@askevepodcast.com",
  teamDomain = "askeve.cloudflareaccess.com",
  aud = "aud_tag_ask_eve_admin",
} = {}) {
  const keyPair = await crypto.subtle.generateKey(
    {
      name: "RSASSA-PKCS1-v1_5",
      modulusLength: 2048,
      publicExponent: new Uint8Array([1, 0, 1]),
      hash: "SHA-256",
    },
    true,
    ["sign", "verify"]
  );
  const publicJwk = await crypto.subtle.exportKey("jwk", keyPair.publicKey);
  publicJwk.kid = "test-kid-1";
  publicJwk.alg = "RS256";
  publicJwk.use = "sig";

  async function signJwt(claims = {}) {
    const now = Math.floor(Date.now() / 1000);
    const header = { alg: "RS256", kid: "test-kid-1", typ: "JWT" };
    const payload = {
      iss: `https://${teamDomain}`,
      aud: [aud],
      email: adminEmail,
      sub: "user-1",
      iat: now - 10,
      exp: now + 3600,
      ...claims,
    };
    const signingInput = `${strToBase64Url(JSON.stringify(header))}.${strToBase64Url(
      JSON.stringify(payload)
    )}`;
    const sig = await crypto.subtle.sign(
      "RSASSA-PKCS1-v1_5",
      keyPair.privateKey,
      new TextEncoder().encode(signingInput)
    );
    return `${signingInput}.${toBase64Url(new Uint8Array(sig))}`;
  }

  return {
    adminEmail,
    teamDomain,
    aud,
    jwksKeys: [publicJwk],
    signJwt,
  };
}

function createMockEnv(accessFixture, overrides = {}) {
  const inboxItems = new Map();
  const draftReplies = new Map();
  const xDrafts = new Map();
  const readingList = new Map();
  const r2Objects = new Map();
  const sentEmails = [];
  const postedTweets = [];

  const db = {
    inboxItems,
    draftReplies,
    xDrafts,
    readingList,
    prepare(sql) {
      const norm = sql.replace(/\s+/g, " ").trim();
      let bound = [];
      const stmt = {
        bind(...args) {
          bound = args;
          return stmt;
        },
        async run() {
          if (norm.startsWith("INSERT INTO inbox_items") && norm.includes("'x_mention'")) {
            const [
              id,
              received_at,
              from_addr,
              from_name,
              subject,
              text_body,
              extracted_urls,
              doc_title,
              doc_summary,
              eve_initial_take,
              source_tweet_id,
              author_handle,
            ] = bound;
            inboxItems.set(id, {
              id,
              channel: "x_mention",
              received_at,
              from_addr,
              from_name,
              to_addr: "@askevepodcast",
              subject,
              text_body,
              html_body: "",
              raw_r2_key: null,
              extracted_urls,
              attachments_json: "[]",
              doc_title,
              doc_summary,
              eve_initial_take,
              status: "ready",
              public_reading_id: null,
              inbound_message_id: null,
              source_tweet_id,
              author_handle,
            });
            return { success: true };
          }
          if (norm.startsWith("INSERT INTO inbox_items")) {
            const [
              id,
              received_at,
              from_addr,
              from_name,
              to_addr,
              subject,
              text_body,
              html_body,
              raw_r2_key,
              extracted_urls,
              attachments_json,
              doc_title,
              doc_summary,
              eve_initial_take,
              status,
              inbound_message_id,
            ] = bound;
            // Mirror migration 0002's CHECK so mocks cannot accept invalid states.
            assert.ok(["queued", "processing", "ready", "published_to_reading_list", "archived", "rejected"].includes(status), "invalid inbox status");
            inboxItems.set(id, {
              id,
              channel: "email",
              received_at,
              from_addr,
              from_name,
              to_addr,
              subject,
              text_body,
              html_body,
              raw_r2_key,
              extracted_urls,
              attachments_json,
              doc_title,
              doc_summary,
              eve_initial_take,
              status: status || "ready",
              public_reading_id: null,
              inbound_message_id,
              source_tweet_id: null,
              author_handle: null,
            });
            return { success: true };
          }
          if (norm.startsWith("INSERT INTO draft_replies") && norm.includes("'x_mention'")) {
            const [
              id,
              inbox_item_id,
              to_addr,
              subject,
              body_text,
              in_reply_to_tweet_id,
              created_at,
              updated_at,
              leak_check_passed,
              leak_check_findings,
              leak_check_sha256,
              status,
            ] = bound;
            draftReplies.set(id, {
              id,
              inbox_item_id,
              channel: "x_mention",
              to_addr,
              subject,
              body_text,
              in_reply_to_tweet_id,
              created_at,
              updated_at,
              leak_check_passed,
              leak_check_findings,
              leak_check_sha256,
              status,
              sent_at: null,
              message_id: null,
            });
            return { success: true };
          }
          if (norm.startsWith("INSERT INTO draft_replies")) {
            const [
              id,
              inbox_item_id,
              to_addr,
              subject,
              body_text,
              created_at,
              updated_at,
              leak_check_passed,
              leak_check_findings,
              leak_check_sha256,
              status,
            ] = bound;
            draftReplies.set(id, {
              id,
              inbox_item_id,
              channel: "email",
              to_addr,
              subject,
              body_text,
              in_reply_to_tweet_id: null,
              created_at,
              updated_at,
              leak_check_passed,
              leak_check_findings,
              leak_check_sha256,
              status,
              sent_at: null,
              message_id: null,
            });
            return { success: true };
          }
          if (norm.startsWith("UPDATE draft_replies SET to_addr")) {
            const [
              to_addr,
              subject,
              body_text,
              updated_at,
              leak_check_passed,
              leak_check_findings,
              leak_check_sha256,
              status,
              id,
            ] = bound;
            const d = draftReplies.get(id);
            if (d) {
              Object.assign(d, {
                to_addr,
                subject,
                body_text,
                updated_at,
                leak_check_passed,
                leak_check_findings,
                leak_check_sha256,
                status,
              });
            }
            return { success: true };
          }
          if (norm.startsWith("UPDATE draft_replies SET leak_check_passed = 0")) {
            const [leak_check_findings, updated_at, id] = bound;
            const d = draftReplies.get(id);
            if (d) {
              Object.assign(d, {
                leak_check_passed: 0,
                leak_check_findings,
                status: "blocked_by_leak_check",
                updated_at,
              });
            }
            return { success: true };
          }
          if (norm.startsWith("UPDATE draft_replies SET status = 'approved_sending'")) {
            const [updated_at, id] = bound;
            const d = draftReplies.get(id);
            if (d) Object.assign(d, { status: "approved_sending", updated_at });
            return { success: true };
          }
          if (norm.startsWith("UPDATE draft_replies SET status = 'sent'")) {
            const [sent_at, message_id, id] = bound;
            const d = draftReplies.get(id);
            if (d) {
              Object.assign(d, {
                status: "sent",
                sent_at,
                message_id,
                updated_at: sent_at,
              });
            }
            return { success: true };
          }
          if (norm.startsWith("INSERT INTO x_drafts")) {
            const [
              id,
              body,
              reply_to_tweet_id,
              episode_slug,
              leak_check_passed,
              leak_check_findings_json,
              leak_check_sha256,
              status,
              created_at,
            ] = bound;
            xDrafts.set(id, {
              id,
              body,
              reply_to_tweet_id,
              episode_slug,
              leak_check_passed,
              leak_check_findings_json,
              leak_check_sha256,
              status,
              tweet_id: null,
              created_at,
              sent_at: null,
              approved_by: null,
            });
            return { success: true };
          }
          if (norm.startsWith("UPDATE x_drafts SET body")) {
            const [
              body,
              reply_to_tweet_id,
              episode_slug,
              leak_check_passed,
              leak_check_findings_json,
              leak_check_sha256,
              status,
              id,
            ] = bound;
            const xd = xDrafts.get(id);
            if (xd) {
              Object.assign(xd, {
                body,
                reply_to_tweet_id,
                episode_slug,
                leak_check_passed,
                leak_check_findings_json,
                leak_check_sha256,
                status,
              });
            }
            return { success: true };
          }
          if (norm.startsWith("UPDATE x_drafts SET status = 'approved_sent'")) {
            const [tweet_id, sent_at, approved_by, id] = bound;
            const xd = xDrafts.get(id);
            if (xd) {
              Object.assign(xd, {
                status: "approved_sent",
                tweet_id,
                sent_at,
                approved_by,
              });
            }
            return { success: true };
          }
          if (norm.startsWith("INSERT INTO reading_list")) {
            const [
              id,
              slug,
              title,
              url,
              author_or_source,
              submitted_via,
              added_at,
              status,
              eve_notes,
              episode_slug,
              related_people_json,
              related_topics_json,
            ] = bound;
            readingList.set(slug, {
              id,
              slug,
              title,
              url,
              author_or_source,
              submitted_via,
              added_at,
              status,
              eve_notes,
              episode_slug,
              related_people_json,
              related_topics_json,
            });
            return { success: true };
          }
          if (norm.startsWith("UPDATE inbox_items SET status = 'published_to_reading_list'")) {
            const [public_reading_id, id] = bound;
            const i = inboxItems.get(id);
            if (i) {
              i.status = "published_to_reading_list";
              i.public_reading_id = public_reading_id;
            }
            return { success: true };
          }
          if (norm.startsWith("UPDATE inbox_items SET doc_title")) {
            const [doc_title, doc_summary, eve_initial_take, status, id] = bound;
            const i = inboxItems.get(id);
            if (i) {
              Object.assign(i, { doc_title, doc_summary, eve_initial_take, status });
            }
            return { success: true };
          }
          return { success: true };
        },
        async first() {
          if (norm.startsWith("SELECT * FROM draft_replies WHERE id")) {
            return draftReplies.get(bound[0]) || null;
          }
          if (norm.startsWith("SELECT * FROM inbox_items WHERE id")) {
            return inboxItems.get(bound[0]) || null;
          }
          if (norm.startsWith("SELECT id FROM inbox_items WHERE source_tweet_id")) {
            const found = Array.from(inboxItems.values()).find(
              (it) => it.source_tweet_id === bound[0]
            );
            return found ? { id: found.id } : null;
          }
          if (norm.startsWith("SELECT * FROM x_drafts WHERE id")) {
            return xDrafts.get(bound[0]) || null;
          }
          if (norm.startsWith("SELECT COUNT(*) AS cnt FROM inbox_items WHERE from_addr")) {
            const [sender, since] = bound;
            const cnt = Array.from(inboxItems.values()).filter(
              (it) => it.from_addr === sender && it.received_at >= since
            ).length;
            return { cnt };
          }
          if (norm.startsWith("SELECT COUNT(*) AS cnt FROM inbox_items WHERE received_at >= ?1 AND status != 'queued'")) {
            const [since] = bound;
            const cnt = Array.from(inboxItems.values()).filter(
              (it) => it.received_at >= since && it.status !== "queued"
            ).length;
            return { cnt };
          }
          if (norm.startsWith("SELECT COUNT(*) AS cnt FROM inbox_items WHERE received_at >= ?1")) {
            const [since] = bound;
            const cnt = Array.from(inboxItems.values()).filter(
              (it) => it.received_at >= since
            ).length;
            return { cnt };
          }
          return null;
        },
        async all() {
          if (norm.includes("FROM inbox_items i")) {
            const results = [];
            for (const item of inboxItems.values()) {
              const draft = Array.from(draftReplies.values()).find(
                (d) => d.inbox_item_id === item.id
              );
              results.push({
                ...item,
                draft_id: draft?.id || null,
                draft_channel: draft?.channel || "email",
                draft_to_addr: draft?.to_addr || null,
                draft_subject: draft?.subject || null,
                draft_body_text: draft?.body_text || null,
                in_reply_to_tweet_id: draft?.in_reply_to_tweet_id || null,
                leak_check_passed: draft?.leak_check_passed ?? 0,
                leak_check_findings: draft?.leak_check_findings || "[]",
                leak_check_sha256: draft?.leak_check_sha256 || null,
                draft_status: draft?.status || null,
                sent_at: draft?.sent_at || null,
                message_id: draft?.message_id || null,
              });
            }
            return { results };
          }
          if (norm.includes("FROM reading_list")) {
            return { results: Array.from(readingList.values()) };
          }
          if (norm.includes("FROM x_drafts")) {
            return { results: Array.from(xDrafts.values()) };
          }
          return { results: [] };
        },
      };
      return stmt;
    },
  };

  return {
    DB: db,
    MASTERS: {
      objects: r2Objects,
      async put(key, val, meta) {
        r2Objects.set(key, { val, meta });
      },
    },
    SEND_EMAIL: {
      sent: sentEmails,
      async send(msg) {
        sentEmails.push(msg);
      },
    },
    X_POSTED: postedTweets,
    async X_POST_TWEET({ text, replyToTweetId }) {
      const tweet_id = `tw_${postedTweets.length + 1001}`;
      postedTweets.push({ tweet_id, text, replyToTweetId });
      return { ok: true, tweet_id, text };
    },
    ADMIN_EMAIL: accessFixture.adminEmail,
    CF_ACCESS_TEAM_DOMAIN: accessFixture.teamDomain,
    CF_ACCESS_AUD: accessFixture.aud,
    JWKS_KEYS: accessFixture.jwksKeys,
    PRIVATE_LEAK_TERMS: "742 Evergreen Terrace,secret-project-codename",
    DISABLE_ARTICLE_FETCH: true,
    ...overrides,
  };
}

test("leakCheck passes clean draft and blocks emails, keys, UUIDs, canvas links, paths, and PRIVATE_LEAK_TERMS", async () => {
  const clean = await leakCheck(
    {
      to_addr: "reader@university.edu",
      subject: "Re: Girard on Mimetic Desire",
      body_text:
        "Hi Alex,\n\nThanks for sending this essay to eve@askevepodcast.com! I added it to the queue for https://askevepodcast.com.\n\nWarmly,\nEve",
    },
    { PRIVATE_LEAK_TERMS: "742 Evergreen Terrace,my-secret-alias" }
  );
  assert.equal(clean.passed, true);
  assert.equal(clean.leak_check_passed, 1);
  assert.equal(clean.findings.length, 0);
  assert.equal(clean.sha256.length, 64);

  const leakCases = [
    {
      name: "personal gmail in body",
      body: "You can reach the host directly at somebody@gmail.com.",
      rule: "unauthorized_email",
    },
    {
      name: "private domain in body",
      body: "Forwarded to ops@dbloom.in for review.",
      rule: "unauthorized_email",
    },
    {
      name: "xAI API key",
      body: "Debug key: xai-" + "A".repeat(24),
      rule: "api_key_or_token",
    },
    {
      name: "GitHub PAT",
      body: "Token ghp_" + "b".repeat(30),
      rule: "api_key_or_token",
    },
    {
      name: "UUIDv4",
      body: "Internal session a5b26010-7059-4a7e-a876-adbefdaad242",
      rule: "internal_id_or_private_url",
    },
    {
      name: "Cursor canvas link",
      body: "See https://cursor.com/canvas/abc-123",
      rule: "internal_id_or_private_url",
    },
    {
      name: "Grok share link",
      body: "See https://grok.com/share/c2hhcmQtXYZ123",
      rule: "internal_id_or_private_url",
    },
    {
      name: "local box path",
      body: "Saved under /home/box/agent-data/notes.md and /workspace/ask-eve",
      rule: "local_filesystem_path",
    },
    {
      name: "tailnet / private R2 bucket",
      body: "Stored on hermes-hub.tail123.ts.net in ask-eve-masters",
      rule: "private_hostname_or_infra",
    },
    {
      name: "custom PRIVATE_LEAK_TERMS",
      body: "Meet at 742 Evergreen Terrace at 5pm",
      rule: "private_leak_term",
    },
  ];

  for (const tc of leakCases) {
    const res = await leakCheck(
      {
        to_addr: "reader@university.edu",
        subject: "Re: Test",
        body_text: tc.body,
      },
      { PRIVATE_LEAK_TERMS: "742 Evergreen Terrace,my-secret-alias" }
    );
    assert.equal(res.passed, false, `Expected ${tc.name} to fail leak check`);
    assert.equal(res.leak_check_passed, 0);
    assert.ok(
      res.findings.some((f) => f.rule === tc.rule),
      `Expected finding rule ${tc.rule} for ${tc.name}, got ${JSON.stringify(res.findings)}`
    );
  }
});

test("inbound email handler queues item, creates leak-checked draft reply, and never auto-sends", async () => {
  const access = await createAccessFixture();
  const env = createMockEnv(access);

  const rawMime = [
    'From: "Clara Vance" <clara@philosophy.org>',
    "To: eve@askevepodcast.com",
    "Subject: Girard and AI Mimetic Loops",
    "Message-ID: <msg-clara-001@philosophy.org>",
    'Content-Type: multipart/mixed; boundary="bound123"',
    "",
    "--bound123",
    "Content-Type: text/plain; charset=utf-8",
    "",
    "Hi Eve, check out this paper on mimetic contagion in frontier models: https://example.org/mimetic-ai.pdf",
    "--bound123",
    'Content-Type: text/markdown; name="notes.md"',
    'Content-Disposition: attachment; filename="notes.md"',
    "",
    "# Key Thesis\nModels imitate human desire structures via RLHF.",
    "--bound123--",
    "",
  ].join("\r\n");

  const result = await worker.email(
    {
      from: "clara@philosophy.org",
      to: "eve@askevepodcast.com",
      forward: async (to) => assert.equal(to, env.ADMIN_EMAIL),
      raw: rawMime,
    },
    env,
    {}
  );

  assert.equal(result.draftStatus, "draft");
  assert.equal(result.leakResult.passed, true);
  assert.equal(env.SEND_EMAIL.sent.length, 0, "Must never auto-send on inbound email");
  assert.equal(env.DB.inboxItems.size, 1);
  assert.equal(env.DB.draftReplies.size, 1);
  assert.ok(env.MASTERS.objects.has(`inbox/${result.itemId}/raw.eml`));
  assert.ok(env.MASTERS.objects.has(`inbox/${result.itemId}/attachments/notes.md`));
});

test("admin console fails closed without valid Cloudflare Access JWT + matching ADMIN_EMAIL", async () => {
  const access = await createAccessFixture();
  const env = createMockEnv(access);

  // 1. Missing headers -> 401
  const r1 = await worker.fetch(
    new Request("https://askevepodcast.com/admin/api/queue"),
    env,
    {}
  );
  assert.equal(r1.status, 401);

  // 2. Wrong email -> 403
  const wrongToken = await access.signJwt({ email: "intruder@example.com" });
  const r2 = await worker.fetch(
    new Request("https://askevepodcast.com/admin/api/queue", {
      headers: {
        "Cf-Access-Jwt-Assertion": wrongToken,
        "Cf-Access-Authenticated-User-Email": "intruder@example.com",
      },
    }),
    env,
    {}
  );
  assert.equal(r2.status, 403);

  // 3. Missing env config -> 503 fail-closed
  const r3 = await worker.fetch(
    new Request("https://askevepodcast.com/admin/api/queue"),
    { ...env, ADMIN_EMAIL: "" },
    {}
  );
  assert.equal(r3.status, 503);
});

test("send endpoint enforces hard leak-check + sha256 gate and sends only after clean re-check", async () => {
  const access = await createAccessFixture();
  const env = createMockEnv(access);
  const token = await access.signJwt();
  const authHeaders = {
    "Cf-Access-Jwt-Assertion": token,
    "Cf-Access-Authenticated-User-Email": access.adminEmail,
    "Content-Type": "application/json",
  };

  // Seed an inbound email
  const inbound = await worker.email(
    {
      from: "listener@example.org",
      to: "eve@askevepodcast.com",
      forward: async (to) => assert.equal(to, env.ADMIN_EMAIL),
      raw: [
        "From: Listener <listener@example.org>",
        "To: eve@askevepodcast.com",
        "Subject: Question on Episode 1",
        "Message-ID: <ep1-q@example.org>",
        "",
        "Loved the Girard discussion!",
      ].join("\r\n"),
    },
    env,
    {}
  );

  // Edit the draft to introduce a private leak -> status becomes blocked_by_leak_check
  const badUpdateResp = await worker.fetch(
    new Request(`https://askevepodcast.com/admin/api/draft/${inbound.draftId}/update`, {
      method: "POST",
      headers: authHeaders,
      body: JSON.stringify({
        subject: "Re: Question on Episode 1",
        body_text: "Thanks! Internal debug path /home/box/secret and 742 Evergreen Terrace.",
      }),
    }),
    env,
    {}
  );
  assert.equal(badUpdateResp.status, 200);
  const badUpdate = await badUpdateResp.json();
  assert.equal(badUpdate.leak_check_passed, 0);
  assert.equal(badUpdate.status, "blocked_by_leak_check");

  // Attempt to send while blocked -> HTTP 422 and SEND_EMAIL not called
  const blockedSendResp = await worker.fetch(
    new Request(`https://askevepodcast.com/admin/api/draft/${inbound.draftId}/send`, {
      method: "POST",
      headers: authHeaders,
      body: JSON.stringify({
        confirm: true,
        expected_sha256: badUpdate.leak_check_sha256,
      }),
    }),
    env,
    {}
  );
  assert.equal(blockedSendResp.status, 422);
  assert.equal(env.SEND_EMAIL.sent.length, 0);

  // Fix the draft so it passes leak check
  const cleanBody =
    "Hi there,\n\nThank you for listening to Episode 1! I've added your note to our prep queue.\n\nWarmly,\nEve\nhttps://askevepodcast.com";
  const goodUpdateResp = await worker.fetch(
    new Request(`https://askevepodcast.com/admin/api/draft/${inbound.draftId}/update`, {
      method: "POST",
      headers: authHeaders,
      body: JSON.stringify({
        subject: "Re: Question on Episode 1",
        body_text: cleanBody,
      }),
    }),
    env,
    {}
  );
  const goodUpdate = await goodUpdateResp.json();
  assert.equal(goodUpdate.leak_check_passed, 1);
  assert.equal(goodUpdate.status, "draft");

  // Attempt to send with wrong expected_sha256 -> HTTP 422
  const mismatchResp = await worker.fetch(
    new Request(`https://askevepodcast.com/admin/api/draft/${inbound.draftId}/send`, {
      method: "POST",
      headers: authHeaders,
      body: JSON.stringify({
        confirm: true,
        expected_sha256: "0".repeat(64),
      }),
    }),
    env,
    {}
  );
  assert.equal(mismatchResp.status, 422);
  assert.equal(env.SEND_EMAIL.sent.length, 0);

  // Now send with confirm: true and matching expected_sha256 -> HTTP 200 & sent!
  const sendResp = await worker.fetch(
    new Request(`https://askevepodcast.com/admin/api/draft/${inbound.draftId}/send`, {
      method: "POST",
      headers: authHeaders,
      body: JSON.stringify({
        confirm: true,
        expected_sha256: goodUpdate.leak_check_sha256,
      }),
    }),
    env,
    {}
  );
  assert.equal(sendResp.status, 200);
  const sendData = await sendResp.json();
  assert.equal(sendData.status, "sent");
  assert.equal(env.SEND_EMAIL.sent.length, 1);
  assert.ok(env.SEND_EMAIL.sent[0].raw.includes("In-Reply-To: <ep1-q@example.org>"));

  // Promote item to reading list: blocked if eve_notes has a leak, allowed when clean
  const badPromote = await worker.fetch(
    new Request(
      `https://askevepodcast.com/admin/api/item/${inbound.itemId}/promote-to-reading-list`,
      {
        method: "POST",
        headers: authHeaders,
        body: JSON.stringify({
          title: "Listener Note",
          eve_notes: "Contains secret xai-" + "C".repeat(24),
        }),
      }
    ),
    env,
    {}
  );
  assert.equal(badPromote.status, 422);

  const goodPromote = await worker.fetch(
    new Request(
      `https://askevepodcast.com/admin/api/item/${inbound.itemId}/promote-to-reading-list`,
      {
        method: "POST",
        headers: authHeaders,
        body: JSON.stringify({
          title: "Girard and Mimetic Contagion",
          author_or_source: "Listener submission",
          eve_notes: "Sharp question on mimetic rivalry in AI alignment.",
        }),
      }
    ),
    env,
    {}
  );
  assert.equal(goodPromote.status, 200);
});

test("public @askevepodcast X mentions land in queue with <=280 char leak-checked draft X reply and post only when approved", async () => {
  const access = await createAccessFixture();
  const env = createMockEnv(access);
  const token = await access.signJwt();
  const authHeaders = {
    "Cf-Access-Jwt-Assertion": token,
    "Cf-Access-Authenticated-User-Email": access.adminEmail,
    "Content-Type": "application/json",
  };

  // 1. Ingest a public @askevepodcast mention with a linked paper
  const ingestResp = await worker.fetch(
    new Request("https://askevepodcast.com/admin/api/x/ingest-mention", {
      method: "POST",
      headers: authHeaders,
      body: JSON.stringify({
        tweet_id: "1849900112233445566",
        author_handle: "@girard_scholar",
        author_name: "Dr. René Scholar",
        text: "@askevepodcast Your Thiel Antichrist take misses how Girard distinguished katechon from apocalypse: https://example.org/katechon-essay",
      }),
    }),
    env,
    {}
  );
  assert.equal(ingestResp.status, 200);
  const ingested = await ingestResp.json();
  assert.equal(ingested.skipped, false);
  assert.equal(ingested.draftStatus, "draft");
  assert.equal(ingested.leakResult.passed, true);
  assert.equal(env.X_POSTED.length, 0, "Must never auto-post to @askevepodcast on mention ingest");

  // 2. Duplicate tweet_id is skipped idempotently
  const dupResp = await worker.fetch(
    new Request("https://askevepodcast.com/admin/api/x/ingest-mention", {
      method: "POST",
      headers: authHeaders,
      body: JSON.stringify({
        tweet_id: "1849900112233445566",
        author_handle: "@girard_scholar",
        text: "@askevepodcast duplicate",
      }),
    }),
    env,
    {}
  );
  const dup = await dupResp.json();
  assert.equal(dup.skipped, true);

  // 3. Edit the X reply draft to exceed 280 chars -> blocked by x_char_limit_exceeded
  const overlongResp = await worker.fetch(
    new Request(`https://askevepodcast.com/admin/api/draft/${ingested.draftId}/update`, {
      method: "POST",
      headers: authHeaders,
      body: JSON.stringify({
        body_text: "@girard_scholar " + "A".repeat(285),
      }),
    }),
    env,
    {}
  );
  const overlong = await overlongResp.json();
  assert.equal(overlong.leak_check_passed, 0);
  assert.equal(overlong.status, "blocked_by_leak_check");
  assert.ok(overlong.leak_check_findings.some((f) => f.rule === "x_char_limit_exceeded"));

  // 4. Edit the X reply draft to a sharp, clean <=280 char reply
  const sharpReply =
    "@girard_scholar Fair pushback — Girard's reading of the katechon in Battling to the End is much more ambivalent than a simple endorsement. Queued your essay for Episode 2: https://askevepodcast.com/eve/#reading-list";
  assert.ok(sharpReply.length <= 280);
  const cleanXResp = await worker.fetch(
    new Request(`https://askevepodcast.com/admin/api/draft/${ingested.draftId}/update`, {
      method: "POST",
      headers: authHeaders,
      body: JSON.stringify({ body_text: sharpReply }),
    }),
    env,
    {}
  );
  const cleanX = await cleanXResp.json();
  assert.equal(cleanX.leak_check_passed, 1);

  // 5. Approve & Post the X reply -> calls X_POST_TWEET with in_reply_to_tweet_id
  const postReplyResp = await worker.fetch(
    new Request(`https://askevepodcast.com/admin/api/draft/${ingested.draftId}/send`, {
      method: "POST",
      headers: authHeaders,
      body: JSON.stringify({
        confirm: true,
        expected_sha256: cleanX.leak_check_sha256,
      }),
    }),
    env,
    {}
  );
  assert.equal(postReplyResp.status, 200);
  const postedReply = await postReplyResp.json();
  assert.equal(postedReply.status, "sent");
  assert.equal(postedReply.channel, "x_mention");
  assert.equal(env.X_POSTED.length, 1);
  assert.equal(env.X_POSTED[0].replyToTweetId, "1849900112233445566");
  assert.equal(env.X_POSTED[0].text, sharpReply);

  // 6. Standalone @askevepodcast X draft creation, leak check gate, and posting
  const leakedXDraftResp = await worker.fetch(
    new Request("https://askevepodcast.com/admin/api/x/draft", {
      method: "POST",
      headers: authHeaders,
      body: JSON.stringify({
        body: "New episode out! Debug key xai-" + "D".repeat(24),
      }),
    }),
    env,
    {}
  );
  const leakedXDraft = await leakedXDraftResp.json();
  assert.equal(leakedXDraft.leak_check_passed, 0);
  assert.equal(leakedXDraft.status, "blocked_leak");

  // Posting a blocked x_draft fails with 422
  const blockedPostResp = await worker.fetch(
    new Request(`https://askevepodcast.com/admin/api/x/draft/${leakedXDraft.id}/post`, {
      method: "POST",
      headers: authHeaders,
      body: JSON.stringify({ confirm: true, expected_sha256: leakedXDraft.leak_check_sha256 }),
    }),
    env,
    {}
  );
  assert.equal(blockedPostResp.status, 422);
  assert.equal(env.X_POSTED.length, 1);

  // Clean x_draft passes and posts when approved
  const cleanXDraftResp = await worker.fetch(
    new Request("https://askevepodcast.com/admin/api/x/draft", {
      method: "POST",
      headers: authHeaders,
      body: JSON.stringify({
        body: "Episode 1 is live: The Making of Eve. Listen + transcript: https://askevepodcast.com/episodes/the-making-of-eve/",
        episode_slug: "the-making-of-eve",
      }),
    }),
    env,
    {}
  );
  const cleanXDraft = await cleanXDraftResp.json();
  assert.equal(cleanXDraft.leak_check_passed, 1);
  assert.equal(cleanXDraft.status, "draft");

  const approvedPostResp = await worker.fetch(
    new Request(`https://askevepodcast.com/admin/api/x/draft/${cleanXDraft.id}/post`, {
      method: "POST",
      headers: authHeaders,
      body: JSON.stringify({
        confirm: true,
        expected_sha256: cleanXDraft.leak_check_sha256,
      }),
    }),
    env,
    {}
  );
  assert.equal(approvedPostResp.status, 200);
  assert.equal(env.X_POSTED.length, 2);
});


test("inbound email forwards to ADMIN_EMAIL (including X verification codes) and filters spam/rate-limits/SSRF", async () => {
  const { isSafePublicHttpUrl } = await import("../src/index.mjs");
  const { rawStreamToBytes } = await import("../src/mime.mjs");

  const access = await createAccessFixture();
  const env = createMockEnv(access);
  const forwardedTo = [];

  // 1. X verification email from verify@x.com is stored in D1 AND forwarded to ADMIN_EMAIL with 0 LLM spend
  const xVerifyRes = await worker.email(
    {
      from: "verify@x.com",
      to: "eve@askevepodcast.com",
      forward: async (to) => assert.equal(to, env.ADMIN_EMAIL),
      raw: [
        "From: X <verify@x.com>",
        "To: eve@askevepodcast.com",
        "Subject: 849201 is your X verification code",
        "Message-ID: <x-verify-01@x.com>",
        "",
        "Your X confirmation code is 849201.",
      ].join("\r\n"),
      async forward(dest) {
        forwardedTo.push(dest);
      },
    },
    env,
    {}
  );
  assert.equal(xVerifyRes.itemStatus, "queued");
  assert.deepEqual(forwardedTo, [access.adminEmail]);
  assert.equal(env.DB.inboxItems.size, 1);

  // 2. Automated bounce / bulk / DMARC-fail emails are dropped pre-ingest before R2/D1/LLM
  const bounceRes = await worker.email(
    {
      from: "mailer-daemon@mx.example.com",
      to: "eve@askevepodcast.com",
      forward: async (to) => assert.equal(to, env.ADMIN_EMAIL),
      raw: [
        "From: Mail Delivery Subsystem <mailer-daemon@mx.example.com>",
        "To: eve@askevepodcast.com",
        "Return-Path: <>",
        "Auto-Submitted: auto-replied",
        "Subject: Undelivered Mail Returned to Sender",
        "",
        "Bounce body",
      ].join("\r\n"),
    },
    env,
    {}
  );
  assert.equal(bounceRes.skipped, true);
  assert.equal(env.DB.inboxItems.size, 1);

  const dmarcFailRes = await worker.email(
    {
      from: "spoof@attacker.org",
      to: "eve@askevepodcast.com",
      forward: async (to) => assert.equal(to, env.ADMIN_EMAIL),
      raw: [
        "From: Spoof <spoof@attacker.org>",
        "To: eve@askevepodcast.com",
        "Authentication-Results: mx.cloudflare.net; spf=fail; dkim=fail; dmarc=fail",
        "Subject: Urgent",
        "",
        "Spoofed message",
      ].join("\r\n"),
    },
    env,
    {}
  );
  assert.equal(dmarcFailRes.skipped, true);
  assert.equal(dmarcFailRes.reason, "email_auth_failed");

  // 3. Per-sender rate limit (max 3 per 24h)
  for (let i = 1; i <= 3; i++) {
    const r = await worker.email(
      {
        from: "repeat@reader.org",
        to: "eve@askevepodcast.com",
        forward: async (to) => assert.equal(to, env.ADMIN_EMAIL),
      raw: `From: repeat@reader.org\r\nTo: eve@askevepodcast.com\r\nSubject: Note ${i}\r\n\r\nHello ${i}`,
      },
      env,
      {}
    );
    assert.ok(r.itemId);
  }
  const fourth = await worker.email(
    {
      from: "repeat@reader.org",
      to: "eve@askevepodcast.com",
      forward: async (to) => assert.equal(to, env.ADMIN_EMAIL),
      raw: "From: repeat@reader.org\r\nTo: eve@askevepodcast.com\r\nSubject: Note 4\r\n\r\nHello 4",
    },
    env,
    {}
  );
  assert.equal(fourth.skipped, true);
  assert.equal(fourth.reason, "sender_rate_limit");

  // 4. SSRF & scheme check + 2MB MIME stream cap
  assert.equal(isSafePublicHttpUrl("https://arxiv.org/abs/2401.00001"), true);
  assert.equal(isSafePublicHttpUrl("http://127.0.0.1:8080/admin"), false);
  assert.equal(isSafePublicHttpUrl("http://169.254.169.254/latest/meta-data/"), false);
  assert.equal(isSafePublicHttpUrl("http://hermes.tail123.ts.net/"), false);
  assert.equal(isSafePublicHttpUrl("javascript:alert(1)"), false);

  await assert.rejects(
    () => rawStreamToBytes(new Uint8Array(2 * 1024 * 1024 + 10)),
    /raw_email_too_large/
  );

  // 5. Admin console CSP headers & CSRF Origin block
  const token = await access.signJwt();
  const htmlResp = await worker.fetch(
    new Request("https://askevepodcast.com/admin/", {
      headers: {
        "Cf-Access-Jwt-Assertion": token,
        "Cf-Access-Authenticated-User-Email": access.adminEmail,
      },
    }),
    env,
    {}
  );
  assert.equal(htmlResp.status, 200);
  assert.ok(htmlResp.headers.get("Content-Security-Policy")?.includes("default-src 'self'"));
  assert.equal(htmlResp.headers.get("X-Content-Type-Options"), "nosniff");

  const csrfResp = await worker.fetch(
    new Request("https://askevepodcast.com/admin/api/x/draft", {
      method: "POST",
      headers: {
        "Cf-Access-Jwt-Assertion": token,
        "Cf-Access-Authenticated-User-Email": access.adminEmail,
        Origin: "https://evil.example.com",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ body: "test" }),
    }),
    env,
    {}
  );
  assert.equal(csrfResp.status, 403);
});

// Inbox reception must not depend on Access being provisioned.
test("forwarding precedes storage and succeeds without Access configuration", async () => {
  const access = await createAccessFixture();
  const env = createMockEnv(access, { CF_ACCESS_AUD: "", CF_ACCESS_TEAM_DOMAIN: "" });
  const order = [];
  const put = env.MASTERS.put.bind(env.MASTERS);
  env.MASTERS.put = async (...args) => { order.push("store"); return put(...args); };
  await worker.email({ from: "reader@example.org", to: "reading@askevepodcast.com",
    raw: "From: reader@example.org\r\nTo: reading@askevepodcast.com\r\nSubject: Delivery test\r\n\r\nA note.",
    forward: async to => { assert.equal(to, env.ADMIN_EMAIL); order.push("forward"); },
  }, env, {});
  assert.equal(order[0], "forward");
  assert.equal(env.DB.inboxItems.size, 1);
  assert.equal(env.DB.draftReplies.size, 1);
  assert.equal(env.SEND_EMAIL.sent.length, 0);
  for (const path of ["/admin/", "/admin/api/queue", "/admin/x/oauth/start"]) {
    const response = await worker.fetch(new Request(`https://askevepodcast.com${path}`), env, {});
    assert.equal(response.status, 503);
  }
});

test("storage failure cannot prevent forwarding; forwarding failure is not swallowed", async () => {
  let forwarded = 0;
  const env = { ADMIN_EMAIL: "owner@example.org", MASTERS: { put: async () => { throw new Error("storage offline"); } } };
  const message = { from: "reader@example.org", to: "eve@askevepodcast.com",
    raw: "Subject: Test\r\n\r\nHello", forward: async () => { forwarded++; } };
  await assert.rejects(worker.email(message, env, {}), /storage offline/);
  assert.equal(forwarded, 1);
  await assert.rejects(worker.email({ ...message, forward: async () => { throw new Error("forward failed"); } }, env, {}), /forward failed/);
  await assert.rejects(worker.email(message, {}, {}), /forwarding is not configured/);
});
