import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import worker, { keyFor, canonicalShareUrl, KEY_RE } from "../src/index.mjs";

const TOKEN = "t".repeat(43);
let dispatched, ghStatus, objects;
const env = {
  UPLOAD_TOKEN: TOKEN, R2_ACCESS_KEY_ID: "AKIDTEST", R2_SECRET_ACCESS_KEY: "SECRETTEST", GITHUB_TOKEN: "gh-test",
  CF_ACCOUNT_ID: "acct123", MASTERS_BUCKET: "ask-eve-masters", GH_REPO: "daveey/ask-eve", GH_WORKFLOW: "ingest.yml", GH_REF: "main",
  MASTERS: { head: async (k) => (objects.has(k) ? { size: objects.get(k) } : null) },
};
beforeEach(() => {
  dispatched = []; ghStatus = 204; objects = new Map([["raw/2026-10-23_the-making-of-eve/phone.m4a", 1234]]);
  globalThis.fetch = async (url, init) => { dispatched.push({ url, init }); return new Response(ghStatus === 204 ? null : "nope", { status: ghStatus }); };
});
const call = (path, body, { auth = `Bearer ${TOKEN}`, method = "POST" } = {}) =>
  worker.fetch(new Request(`https://upload.askevepodcast.com${path}`, {
    method, headers: auth ? { authorization: auth } : {}, body: method === "POST" ? (typeof body === "string" ? body : JSON.stringify(body)) : undefined,
  }), env);
const ok = { date: "2026-10-23", slug: "the-making-of-eve", ext: "m4a", content_type: "audio/mp4", size: 1234 };

test("auth: missing, wrong, same-length wrong, misconfigured", async () => {
  assert.equal((await call("/presign", ok, { auth: null })).status, 401);
  assert.equal((await call("/presign", ok, { auth: "Bearer nope" })).status, 401);
  assert.equal((await call("/presign", ok, { auth: `Bearer ${"x".repeat(43)}` })).status, 401);
  assert.equal((await call("/presign", ok, { auth: TOKEN })).status, 401);
  const r = await worker.fetch(new Request("https://u/presign", { method: "POST", headers: { authorization: "Bearer short" }, body: "{}" }),
    { ...env, UPLOAD_TOKEN: "short" });
  assert.equal(r.status, 401);
});

test("routing and method", async () => {
  assert.equal((await call("/", ok)).status, 404);
  assert.equal((await call("/presign", null, { method: "GET" })).status, 405);
  assert.equal((await call("/presign", "not json")).status, 400);
  assert.equal((await call("/presign", "[1]")).status, 400);
});

test("presign returns a short-lived signed PUT bound to content-type", async () => {
  const r = await call("/presign", ok);
  assert.equal(r.status, 200);
  const j = await r.json();
  assert.equal(j.key, "raw/2026-10-23_the-making-of-eve/phone.m4a");
  assert.equal(j.method, "PUT");
  assert.deepEqual(j.headers, { "Content-Type": "audio/mp4" });
  const u = new URL(j.put_url);
  assert.equal(u.host, "acct123.r2.cloudflarestorage.com");
  assert.equal(u.pathname, "/ask-eve-masters/raw/2026-10-23_the-making-of-eve/phone.m4a");
  assert.equal(u.searchParams.get("X-Amz-Expires"), "1800");
  assert.equal(u.searchParams.get("X-Amz-SignedHeaders"), "content-type;host");
  assert.match(u.searchParams.get("X-Amz-Credential"), /^AKIDTEST\/\d{8}\/auto\/s3\/aws4_request$/);
  assert.match(u.searchParams.get("X-Amz-Signature"), /^[0-9a-f]{64}$/);
  assert.ok(!j.put_url.includes("SECRETTEST"));
  assert.equal(r.headers.get("cache-control"), "no-store");
});

test("presign validation", async () => {
  for (const bad of [
    { ...ok, slug: "../x" }, { ...ok, slug: "UPPER" }, { ...ok, slug: "-lead" }, { ...ok, slug: "a".repeat(61) },
    { ...ok, date: "2026-13-01" }, { ...ok, date: "2026-02-30" }, { ...ok, date: "20261023" },
    { ...ok, ext: "exe" }, { ...ok, ext: "toString" }, { ...ok, kind: "video" },
    { ...ok, content_type: "video/mp4" }, { ...ok, size: -1 }, { ...ok, size: "12" },
  ]) assert.equal((await call("/presign", bad)).status, 400, JSON.stringify(bad));
  assert.equal((await call("/presign", { ...ok, size: 5 * 1024 ** 3 })).status, 413);
  assert.equal((await call("/presign", { date: ok.date, slug: ok.slug, ext: "mov" })).status, 200);   // content_type/size optional
  assert.equal((await (await call("/presign", { ...ok, ext: "mov", content_type: undefined, kind: "lav" })).json()).key,
    "raw/2026-10-23_the-making-of-eve/lav.mov");
});

test("ingest: 404 when the object is missing, nothing dispatched", async () => {
  const r = await call("/ingest", { ...ok, slug: "missing", share_url: "https://grok.com/share/abc" });
  assert.equal(r.status, 404);
  assert.equal(dispatched.length, 0);
});

test("ingest: bad share urls rejected before dispatch", async () => {
  for (const s of ["https://evil.com/share/a", "http://grok.com/share/a", "https://grok.com.evil.com/share/a",
                   "https://grok.com/share/", "https://grok.com/chat/abc", "https://user@grok.com/share/a", "javascript:alert(1)", 42, undefined])
    assert.equal((await call("/ingest", { ...ok, share_url: s })).status, 400, String(s));
  assert.equal(dispatched.length, 0);
});

test("ingest dispatches ingest.yml with canonical inputs", async () => {
  const r = await call("/ingest", { ...ok, share_url: "https://grok.com/share/c2hhcmQtMg_90f6-17bb/?referrer=x" });
  assert.equal(r.status, 200);
  const j = await r.json();
  assert.equal(j.dispatched, true);
  assert.equal(j.bytes, 1234);
  assert.deepEqual(j.keys, ["raw/2026-10-23_the-making-of-eve/phone.m4a"]);
  assert.equal(j.branch, "ingest/2026-10-23_the-making-of-eve");
  assert.equal(dispatched.length, 1);
  const { url, init } = dispatched[0];
  assert.equal(url, "https://api.github.com/repos/daveey/ask-eve/actions/workflows/ingest.yml/dispatches");
  assert.equal(init.headers.authorization, "Bearer gh-test");
  assert.deepEqual(JSON.parse(init.body), { ref: "main", inputs: {
    slug: "the-making-of-eve", date: "2026-10-23", share_url: "https://grok.com/share/c2hhcmQtMg_90f6-17bb",
    raw_key: "raw/2026-10-23_the-making-of-eve/phone.m4a" } });
});

test("multi-segment: presign phone-NN, ingest checks every segment", async () => {
  const j = await (await call("/presign", { ...ok, segment: 2 })).json();
  assert.equal(j.key, "raw/2026-10-23_the-making-of-eve/phone-02.m4a");
  for (const bad of [0, 21, 1.5, "1"]) assert.equal((await call("/presign", { ...ok, segment: bad })).status, 400);
  objects.set("raw/2026-10-23_the-making-of-eve/phone-01.m4a", 10);
  const share_url = "https://grok.com/share/abc";
  let r = await call("/ingest", { ...ok, segments: 2, share_url });
  assert.equal(r.status, 404);
  assert.equal((await r.json()).key, "raw/2026-10-23_the-making-of-eve/phone-02.m4a");
  assert.equal(dispatched.length, 0);
  objects.set("raw/2026-10-23_the-making-of-eve/phone-02.m4a", 20);
  r = await call("/ingest", { ...ok, segments: 2, share_url });
  assert.equal(r.status, 200);
  assert.equal((await r.json()).bytes, 30);
  assert.equal(JSON.parse(dispatched[0].init.body).inputs.raw_key,
    "raw/2026-10-23_the-making-of-eve/phone-01.m4a,raw/2026-10-23_the-making-of-eve/phone-02.m4a");
  assert.equal((await call("/ingest", { ...ok, segments: 0, share_url })).status, 400);
});

test("ingest reports GitHub failures as 502", async () => {
  ghStatus = 403;
  const r = await call("/ingest", { ...ok, share_url: "https://grok.com/share/abc" });
  assert.equal(r.status, 502);
  assert.equal((await r.json()).dispatched, false);
});

test("helpers", () => {
  assert.equal(keyFor({ date: "2026-10-23", slug: "x", ext: "wav" })[0], "raw/2026-10-23_x/phone.wav");
  assert.equal(canonicalShareUrl("https://grok.com/share/A_b-9"), "https://grok.com/share/A_b-9");
  assert.ok(KEY_RE.test("raw/2026-10-23_x/export.mp4") && !KEY_RE.test("raw/2026-10-23_x/../phone.mp4"));
});

// ---- /notify (ingest.yml's last step emails David) ----
const NOTIFY_TOKEN = "n".repeat(40);
class FakeEmailMessage { constructor(from, to, raw) { Object.assign(this, { from, to, raw }); } }
const mailEnv = (sent, extra = {}) => ({
  ...env, NOTIFY_TOKEN, NOTIFY_TO: "owner@example.com", NOTIFY_FROM: "eve@askevepodcast.com", EmailMessage: FakeEmailMessage,
  MAILER: { send: async (m) => { sent.push(m); } }, ...extra,
});
const notifyCall = (e, body, auth = `Bearer ${NOTIFY_TOKEN}`) =>
  worker.fetch(new Request("https://upload.askevepodcast.com/notify", {
    method: "POST", headers: auth ? { authorization: auth } : {}, body: JSON.stringify(body) }), e);

test("notify: sends one email to the configured address only", async () => {
  const sent = [];
  const r = await notifyCall(mailEnv(sent), { subject: "Ask Eve: 2026-10-23_walk is ready ✓", text: "Listen: https://drive/x\n", to: "evil@x.com" });
  assert.equal(r.status, 200);
  assert.deepEqual(await r.json(), { sent: true });
  assert.equal(sent.length, 1);
  assert.equal(sent[0].to, "owner@example.com");          // body.to is ignored
  assert.equal(sent[0].from, "eve@askevepodcast.com");
  const raw = sent[0].raw;
  assert.match(raw, /^From: Ask Eve <eve@askevepodcast\.com>\r\nTo: owner@example\.com\r\nSubject: =\?UTF-8\?B\?/);
  assert.match(raw, /\r\nMessage-ID: <[0-9a-f-]+@askevepodcast\.com>\r\n/);
  const subj = /Subject: =\?UTF-8\?B\?([^?]+)\?=/.exec(raw)[1];
  assert.equal(Buffer.from(subj, "base64").toString("utf8"), "Ask Eve: 2026-10-23_walk is ready ✓");
  const body = raw.split("\r\n\r\n")[1].replace(/\r\n/g, "");
  assert.equal(Buffer.from(body, "base64").toString("utf8"), "Listen: https://drive/x\n");
});

test("notify: auth is NOTIFY_TOKEN, not UPLOAD_TOKEN; and the reverse", async () => {
  const sent = [];
  assert.equal((await notifyCall(mailEnv(sent), { subject: "s", text: "t" }, `Bearer ${TOKEN}`)).status, 401);
  assert.equal((await notifyCall(mailEnv(sent), { subject: "s", text: "t" }, null)).status, 401);
  assert.equal((await notifyCall(mailEnv(sent, { NOTIFY_TOKEN: undefined }), { subject: "s", text: "t" })).status, 401);
  const r = await worker.fetch(new Request("https://u/presign", { method: "POST", headers: { authorization: `Bearer ${NOTIFY_TOKEN}` },
    body: JSON.stringify(ok) }), mailEnv(sent));
  assert.equal(r.status, 401);
  assert.equal(sent.length, 0);
});

test("notify: validation, header injection, unconfigured, send failure", async () => {
  const sent = [];
  for (const bad of [{ text: "t" }, { subject: "", text: "t" }, { subject: "a\r\nBcc: x@y", text: "t" }, { subject: "x".repeat(201), text: "t" },
                     { subject: "s" }, { subject: "s", text: "x".repeat(20001) }])
    assert.equal((await notifyCall(mailEnv(sent), bad)).status, 400, JSON.stringify(bad).slice(0, 60));
  assert.equal((await notifyCall(mailEnv(sent, { MAILER: undefined }), { subject: "s", text: "t" })).status, 501);
  assert.equal(sent.length, 0);
  const r = await notifyCall(mailEnv(sent, { MAILER: { send: async () => { throw new Error("destination not verified"); } } }), { subject: "s", text: "t" });
  assert.equal(r.status, 502);
  assert.match((await r.json()).detail, /not verified/);
});
