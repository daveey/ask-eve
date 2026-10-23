// Ask Eve upload Worker: upload.askevepodcast.com
//   POST /presign  -> presigned R2 PUT URL for the PRIVATE masters bucket (ask-eve-masters), key raw/<date>_<slug>/<kind>.<ext>
//   POST /ingest   -> confirms the object landed (R2 binding head()) and dispatches .github/workflows/ingest.yml,
//                     which ALWAYS opens a PR on ingest/<date>_<slug> (never commits to main).
// Secrets (wrangler secret put): UPLOAD_TOKEN, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, GITHUB_TOKEN.
// Vars (wrangler.toml): CF_ACCOUNT_ID, MASTERS_BUCKET, GH_REPO, GH_WORKFLOW, GH_REF. Binding: MASTERS (R2).
// The Worker holds no Cloudflare API token.
import { AwsClient } from "aws4fetch";

export const TYPES = { mp4: "video/mp4", mov: "video/quicktime", m4a: "audio/mp4", wav: "audio/wav", mp3: "audio/mpeg" };
export const KINDS = ["phone", "lav", "export"];
export const MAX_BYTES = 4.5 * 1024 ** 3;   // R2 single PUT limit is 5 GiB; stay under it
export const EXPIRES = 1800;                // presigned URL lifetime (s)
export const KEY_RE = /^raw\/\d{4}-\d{2}-\d{2}_[a-z0-9-]{1,60}\/(phone|lav|export)(-\d{2})?\.(mp4|mov|m4a|wav|mp3)$/;
export const MAX_SEGMENTS = 20;   // one session split across several recordings: phone-01.m4a, phone-02.m4a, ...

const json = (o, status = 200) =>
  new Response(JSON.stringify(o), { status, headers: { "content-type": "application/json", "cache-control": "no-store" } });

async function digest(s) {
  return new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(s)));
}

// Constant-time: compare SHA-256 digests (fixed length) with a XOR fold, so neither length nor content leaks via timing.
export async function authorized(req, env) {
  if (!env.UPLOAD_TOKEN || env.UPLOAD_TOKEN.length < 32) return false;   // misconfigured -> deny
  const [a, b] = await Promise.all([digest(req.headers.get("authorization") || ""), digest(`Bearer ${env.UPLOAD_TOKEN}`)]);
  let d = 0;
  for (let i = 0; i < a.length; i++) d |= a[i] ^ b[i];
  return d === 0;
}

function validDate(s) {
  if (typeof s !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(s)) return false;
  const d = new Date(`${s}T00:00:00Z`);
  return !Number.isNaN(d.getTime()) && d.toISOString().slice(0, 10) === s;
}

const validSeg = (n) => Number.isInteger(n) && n >= 1 && n <= MAX_SEGMENTS;
const segSuffix = (n) => `-${String(n).padStart(2, "0")}`;

export function keyFor(b, segment = b.segment) {
  const kind = b.kind ?? "phone";
  if (segment !== undefined && !validSeg(segment)) return [null, `segment must be an integer 1..${MAX_SEGMENTS}`];
  if (!validDate(b.date)) return [null, "date must be YYYY-MM-DD"];
  if (typeof b.slug !== "string" || !/^[a-z0-9-]{1,60}$/.test(b.slug) || /^-|-$/.test(b.slug)) return [null, "slug must be [a-z0-9-]{1,60}"];
  if (!KINDS.includes(kind)) return [null, `kind must be one of ${KINDS.join(", ")}`];
  if (!Object.hasOwn(TYPES, b.ext ?? "")) return [null, `ext must be one of ${Object.keys(TYPES).join(", ")}`];
  const key = `raw/${b.date}_${b.slug}/${kind}${segment === undefined ? "" : segSuffix(segment)}.${b.ext}`;
  return KEY_RE.test(key) ? [key, null] : [null, "bad key"];
}

// Accept https://grok.com/share/<id> (optional trailing slash / query); return the canonical URL or null.
export function canonicalShareUrl(s) {
  let u;
  try { u = new URL(s); } catch { return null; }
  const m = /^\/share\/([A-Za-z0-9_-]{1,200})\/?$/.exec(u.pathname);
  if (u.protocol !== "https:" || u.hostname !== "grok.com" || u.port || u.username || u.password || !m) return null;
  return `https://grok.com/share/${m[1]}`;
}

async function presign(body, env, key) {
  const content_type = TYPES[body.ext];
  if (body.content_type !== undefined && body.content_type !== content_type)
    return json({ error: `content_type must be ${content_type} for .${body.ext}` }, 400);
  if (body.size !== undefined && (!Number.isInteger(body.size) || body.size <= 0))
    return json({ error: "size must be a positive integer (bytes)" }, 400);
  if (body.size > MAX_BYTES) return json({ error: "too large for a single PUT (4.5 GiB max); share audio-only", key }, 413);
  const r2 = new AwsClient({ accessKeyId: env.R2_ACCESS_KEY_ID, secretAccessKey: env.R2_SECRET_ACCESS_KEY, service: "s3", region: "auto" });
  const u = new URL(`https://${env.CF_ACCOUNT_ID}.r2.cloudflarestorage.com/${env.MASTERS_BUCKET}/${key}`);
  u.searchParams.set("X-Amz-Expires", String(EXPIRES));
  // allHeaders: true puts content-type into X-Amz-SignedHeaders, so the PUT must send exactly this Content-Type.
  const signed = await r2.sign(new Request(u, { method: "PUT", headers: { "content-type": content_type } }),
    { aws: { signQuery: true, allHeaders: true } });
  return json({ key, put_url: signed.url, method: "PUT", headers: { "Content-Type": content_type }, expires_in: EXPIRES });
}

async function ingest(body, env, key) {
  const share_url = canonicalShareUrl(body.share_url);
  if (!share_url) return json({ error: "share_url must be https://grok.com/share/<id>" }, 400);
  // body.segments = N -> raw/<stem>/phone-01..phone-N.<ext> (all must exist); otherwise the single key
  if (body.segments !== undefined && !validSeg(body.segments)) return json({ error: `segments must be an integer 1..${MAX_SEGMENTS}` }, 400);
  const keys = body.segments === undefined ? [key]
    : Array.from({ length: body.segments }, (_, i) => keyFor({ ...body, segment: undefined }, i + 1)[0]);
  let bytes = 0;
  for (const k of keys) {
    const head = await env.MASTERS.head(k);
    if (!head) return json({ error: "object not found; PUT the file first", key: k }, 404);
    bytes += head.size;
  }
  const r = await fetch(`https://api.github.com/repos/${env.GH_REPO}/actions/workflows/${env.GH_WORKFLOW}/dispatches`, {
    method: "POST",
    headers: {
      authorization: `Bearer ${env.GITHUB_TOKEN}`, accept: "application/vnd.github+json",
      "x-github-api-version": "2022-11-28", "user-agent": "ask-eve-upload-worker", "content-type": "application/json",
    },
    body: JSON.stringify({ ref: env.GH_REF, inputs: { slug: body.slug, date: body.date, share_url, raw_key: keys.join(",") } }),
  });
  const ok = r.status === 204;
  return json({
    keys, bytes, dispatched: ok, github_status: r.status,
    branch: `ingest/${body.date}_${body.slug}`,
    runs: `https://github.com/${env.GH_REPO}/actions/workflows/${env.GH_WORKFLOW}`,
    ...(ok ? {} : { error: "GitHub dispatch failed", detail: (await r.text()).slice(0, 300) }),
  }, ok ? 200 : 502);
}

export default {
  async fetch(req, env) {
    const { pathname } = new URL(req.url);
    if (!["/presign", "/ingest"].includes(pathname)) return json({ error: "not found" }, 404);
    if (req.method !== "POST") return json({ error: "POST only" }, 405);
    if (!(await authorized(req, env))) return json({ error: "unauthorized" }, 401);
    const body = await req.json().catch(() => null);
    if (!body || typeof body !== "object" || Array.isArray(body)) return json({ error: "JSON object body required" }, 400);
    const [key, err] = keyFor(body);
    if (!key) return json({ error: err }, 400);
    return pathname === "/presign" ? presign(body, env, key) : ingest(body, env, key);
  },
};
