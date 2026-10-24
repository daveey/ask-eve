// workers/eve-inbox/src/index.mjs
// Cloudflare Worker for Ask Eve:
//   1. email(message, env, ctx): Inbound Email Routing handler for eve@askevepodcast.com
//      and reading@askevepodcast.com -> R2 archive + Grok-4.7 triage + leak-checked draft reply.
//   2. scheduled(event, env, ctx): Syncs public @askevepodcast X mentions into the same private
//      queue with <=280 char leak-checked draft X replies (never posted until David approves).
//   3. fetch(request, env, ctx): Cloudflare-Access-protected /admin/ console & API.

import {
  leakCheck,
  leakCheckReadingItem,
  computeLeakCheckSha256,
} from "../leak_check.js";
import {
  parseInboundEmail,
  stripHtmlToText,
  buildReplyMime,
  extractUrls,
} from "./mime.mjs";
import { verifyAccessRequest } from "./auth.mjs";
import { renderAdminHtml, renderAdminGateHtml } from "./admin_ui.mjs";
import {
  getXConnectionStatus,
  startXOAuth2Pkce,
  completeXOAuth2Callback,
  postTweetToX,
  fetchRecentXMentions,
} from "./x_client.mjs";

const CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ";
export const MAX_X_POST_CHARS = 280;

export function generateUlid(prefix = "") {
  let now = Date.now();
  let timeChars = "";
  for (let i = 0; i < 10; i++) {
    timeChars = CROCKFORD[now % 32] + timeChars;
    now = Math.floor(now / 32);
  }
  const randBytes = new Uint8Array(16);
  crypto.getRandomValues(randBytes);
  let randChars = "";
  for (let i = 0; i < 16; i++) {
    randChars += CROCKFORD[randBytes[i] % 32];
  }
  return prefix ? `${prefix}_${timeChars}${randChars}` : `${timeChars}${randChars}`;
}

export function slugify(text) {
  return (
    String(text || "reading-item")
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, 72) || "reading-item"
  );
}

function jsonResponse(body, status = 200, extraHeaders = {}) {
  return new Response(JSON.stringify(body, null, 2), {
    status,
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      "Cache-Control": "no-store",
      ...extraHeaders,
    },
  });
}

export const MAX_PER_SENDER_PER_DAY = 3;
export const MAX_INBOX_ITEMS_PER_DAY = 100;
export const MAX_AUTO_LLM_CALLS_PER_DAY = 15;

export function isSafePublicHttpUrl(rawUrl) {
  if (!rawUrl || typeof rawUrl !== "string") return false;
  let parsed;
  try {
    parsed = new URL(rawUrl.trim());
  } catch {
    return false;
  }
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") return false;
  if (parsed.username || parsed.password) return false;
  const host = parsed.hostname.toLowerCase().replace(/^\[|\]$/g, "");
  if (!host || !host.includes(".")) return false;
  if (
    host === "localhost" ||
    host.endsWith(".localhost") ||
    host.endsWith(".local") ||
    host.endsWith(".internal") ||
    host.endsWith(".ts.net") ||
    host.endsWith(".arpa")
  ) {
    return false;
  }
  if (host.includes(":")) return false; // Disallow literal IPv6 addresses
  const ipv4Match = host.match(/^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/);
  if (ipv4Match) {
    const octets = ipv4Match.slice(1, 5).map(Number);
    if (octets.some((o) => o < 0 || o > 255)) return false;
    const [a, b] = octets;
    if (
      a === 0 ||
      a === 10 ||
      a === 127 ||
      (a === 169 && b === 254) ||
      (a === 172 && b >= 16 && b <= 31) ||
      (a === 192 && b === 168) ||
      (a === 100 && b >= 64 && b <= 127) ||
      a >= 224
    ) {
      return false;
    }
  }
  return true;
}

export function checkInboundEmailSpam(message, parsed) {
  const fromAddr = String(parsed?.from_addr || message?.from || "").toLowerCase().trim();
  const subject = String(parsed?.subject || "").trim();
  const headers = parsed?.headers || {};

  // Always allow critical account verification / security emails (e.g. X @askevepodcast signup)
  // and mark bypassLlm: true so they are saved in D1 + forwarded with zero LLM spend.
  const isVerificationSender =
    /@(?:x\.com|twitter\.com|e\.x\.com|cloudflare\.com|github\.com|google\.com|apple\.com)$/i.test(fromAddr);
  const isVerificationSubject =
    /\b(verification|verify|confirm|confirmation|security code|login code|one-time|passcode|otp)\b/i.test(subject);
  if (isVerificationSender || isVerificationSubject) {
    return { drop: false, bypassLlm: true, reason: "verification_allowlist" };
  }

  const returnPath = String(headers["return-path"] || "").trim();
  if (returnPath === "<>") {
    return { drop: true, reason: "empty_return_path_bounce" };
  }
  if (/^(mailer-daemon|postmaster|noreply|no-reply|donotreply|bounce|bounces)@/i.test(fromAddr)) {
    return { drop: true, reason: "automated_sender" };
  }
  const autoSubmitted = String(headers["auto-submitted"] || "").toLowerCase().trim();
  if (autoSubmitted && autoSubmitted !== "no") {
    return { drop: true, reason: "auto_submitted" };
  }
  if (headers["x-autoreply"] || headers["x-autorespond"]) {
    return { drop: true, reason: "auto_reply_header" };
  }
  const precedence = String(headers["precedence"] || "").toLowerCase().trim();
  if (["bulk", "junk", "list", "auto_reply"].includes(precedence)) {
    return { drop: true, reason: `precedence_${precedence}` };
  }
  const authResults = String(
    headers["authentication-results"] || headers["arc-authentication-results"] || ""
  ).toLowerCase();
  if (
    authResults.includes("dmarc=fail") ||
    (authResults.includes("spf=fail") && authResults.includes("dkim=fail"))
  ) {
    return { drop: true, reason: "email_auth_failed" };
  }
  return { drop: false, bypassLlm: false };
}

export async function checkRateLimitsAndBudget(senderAddr, env) {
  if (!env?.DB) {
    return { allowed: true, allowLlm: env?.LAZY_LLM_TRIAGE !== "true" };
  }
  const sinceIso = new Date(Date.now() - 24 * 60 * 60 * 1000).toISOString();
  try {
    if (senderAddr) {
      const senderRow = await env.DB.prepare(
        `SELECT COUNT(*) AS cnt FROM inbox_items WHERE from_addr = ?1 AND received_at >= ?2`
      )
        .bind(senderAddr, sinceIso)
        .first();
      const senderCount = Number(senderRow?.cnt ?? 0);
      if (senderCount >= MAX_PER_SENDER_PER_DAY) {
        return { allowed: false, allowLlm: false, reason: "sender_rate_limit" };
      }
    }
    const totalRow = await env.DB.prepare(
      `SELECT COUNT(*) AS cnt FROM inbox_items WHERE received_at >= ?1`
    )
      .bind(sinceIso)
      .first();
    const totalCount = Number(totalRow?.cnt ?? 0);
    if (totalCount >= MAX_INBOX_ITEMS_PER_DAY) {
      return { allowed: false, allowLlm: false, reason: "daily_inbox_cap" };
    }
    if (env?.LAZY_LLM_TRIAGE === "true") {
      return { allowed: true, allowLlm: false, reason: "lazy_llm_triage" };
    }
    const llmRow = await env.DB.prepare(
      `SELECT COUNT(*) AS cnt FROM inbox_items WHERE received_at >= ?1 AND status != 'queued'`
    )
      .bind(sinceIso)
      .first();
    const llmCount = Number(llmRow?.cnt ?? 0);
    if (llmCount >= MAX_AUTO_LLM_CALLS_PER_DAY) {
      return { allowed: true, allowLlm: false, reason: "daily_llm_budget_cap" };
    }
  } catch {
    // Continue safely if mock DB does not implement COUNT queries
  }
  return { allowed: true, allowLlm: true };
}

async function forwardToAdmin(message, env) {
  const dest = String(env?.ADMIN_EMAIL || env?.ADMIN_ALLOWED_EMAIL || "").trim();
  if (!dest) throw new Error("inbox forwarding is not configured");
  // Let Email Routing report/retry delivery failures instead of silently losing mail.
  await message.forward(dest);
}

function verifyCsrfOrigin(request) {
  const origin = request.headers.get("Origin") || request.headers.get("origin");
  if (origin) {
    try {
      const reqUrl = new URL(request.url);
      const origUrl = new URL(origin);
      if (origUrl.host !== reqUrl.host) {
        return { ok: false, error: "csrf_origin_mismatch" };
      }
    } catch {
      return { ok: false, error: "csrf_invalid_origin" };
    }
  }
  const secFetchSite = (
    request.headers.get("Sec-Fetch-Site") ||
    request.headers.get("sec-fetch-site") ||
    ""
  ).toLowerCase();
  if (secFetchSite && secFetchSite !== "same-origin" && secFetchSite !== "none") {
    return { ok: false, error: "csrf_cross_site_blocked" };
  }
  return { ok: true };
}

async function fetchLinkedArticleExcerpt(url, env) {
  if (!url || env?.DISABLE_ARTICLE_FETCH || !isSafePublicHttpUrl(url)) return "";
  try {
    let currentUrl = url;
    let resp = null;
    for (let hop = 0; hop <= 2; hop++) {
      if (!isSafePublicHttpUrl(currentUrl)) return "";
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 5000);
      try {
        resp = await fetch(currentUrl, {
          redirect: "manual",
          signal: controller.signal,
          headers: {
            "User-Agent": "AskEvePodcastBot/1.0 (+https://askevepodcast.com/eve/)",
            Accept: "text/html,text/plain,application/xhtml+xml",
          },
        });
      } finally {
        clearTimeout(timer);
      }
      if (resp.status >= 300 && resp.status < 400) {
        const loc = resp.headers.get("location");
        if (!loc) return "";
        currentUrl = new URL(loc, currentUrl).toString();
        continue;
      }
      break;
    }
    if (!resp || !resp.ok) return "";
    const contentLen = Number(resp.headers?.get?.("content-length") || 0);
    if (contentLen > 2_000_000) return "";
    if (resp.body && typeof resp.body.getReader === "function") {
      const reader = resp.body.getReader();
      const chunks = [];
      let total = 0;
      while (total < 120000) {
        const { done, value } = await reader.read();
        if (done) break;
        const chunk = value instanceof Uint8Array ? value : new Uint8Array(value);
        chunks.push(chunk);
        total += chunk.byteLength;
      }
      try { await reader.cancel(); } catch {}
      const merged = new Uint8Array(Math.min(total, 120000));
      let offset = 0;
      for (const c of chunks) {
        const slice = c.subarray(0, Math.max(0, merged.byteLength - offset));
        merged.set(slice, offset);
        offset += slice.byteLength;
        if (offset >= merged.byteLength) break;
      }
      const raw = new TextDecoder("utf-8", { fatal: false }).decode(merged);
      return stripHtmlToText(raw).slice(0, 12000);
    }
    const raw = (await resp.text()).slice(0, 120000);
    return stripHtmlToText(raw).slice(0, 12000);
  } catch {
    return "";
  }
}

export async function triageAndDraftWithGrok(parsedEmail, linkedArticleText, env = {}) {
  const isXMention = parsedEmail.channel === "x_mention";
  const handle = parsedEmail.author_handle || parsedEmail.from_addr || "@listener";

  if (isXMention) {
    const cleanTweet = String(parsedEmail.text_body || "")
      .replace(/@askevepodcast/gi, "")
      .trim();
    const fallbackTitle =
      cleanTweet.slice(0, 72) || `X mention from ${handle}`;
    const replySubject = `X reply to ${handle} (${parsedEmail.source_tweet_id || "mention"})`;
    const defaultReply =
      `${handle} Logged this into my Ask Eve reading queue so David and I can press on it next episode. Full list + notes: https://askevepodcast.com/eve/#reading-list`.slice(
        0,
        MAX_X_POST_CHARS
      );

    const fallback = {
      doc_title: fallbackTitle,
      doc_summary: `Public @askevepodcast mention from ${handle}: ${cleanTweet.slice(0, 420)}`,
      eve_initial_take:
        "Public X pushback/submission queued for David and Eve to debate on air.",
      reply_subject: replySubject,
      reply_body: defaultReply,
    };

    if (!env?.XAI_API_KEY) {
      return fallback;
    }

    const endpoint = env?.XAI_API_URL || "https://api.x.ai/v1/chat/completions";
    const model = env?.XAI_MODEL || "grok-4.7";
    const systemPrompt =
      "You are Eve, the AI co-host of the Ask Eve podcast (askevepodcast.com) with David Bloomin. " +
      "Someone just mentioned @askevepodcast publicly on X (Twitter) to challenge a take, ask a question, or share a paper/link. " +
      "Produce a JSON object with keys:\n" +
      '- "doc_title": concise title of the argument, paper, or topic they raised.\n' +
      '- "doc_summary": 2-3 sentence summary of their tweet and any linked paper for David\'s private queue.\n' +
      '- "eve_initial_take": your candid philosophical reaction and 1 sharp question to bring to David on air.\n' +
      `- "reply_body": a sharp, warm, in-character public X reply starting with "${handle} " that MUST be <= 260 characters total. ` +
      "CRITICAL PRIVACY RULE: Never include any private email addresses, internal IDs, API keys, file paths, or URLs other than https://askevepodcast.com.";

    try {
      const resp = await fetch(endpoint, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${env.XAI_API_KEY}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          model,
          temperature: 0.3,
          response_format: { type: "json_object" },
          messages: [
            { role: "system", content: systemPrompt },
            {
              role: "user",
              content: `Tweet from ${handle}: ${parsedEmail.text_body || ""}\nURLs: ${(parsedEmail.extracted_urls || []).join(", ")}\nLinked excerpt: ${(linkedArticleText || "").slice(0, 4000)}`,
            },
          ],
        }),
      });
      if (!resp.ok) return fallback;
      const data = await resp.json();
      const parsed = JSON.parse(data?.choices?.[0]?.message?.content || "{}");
      const rawReply = String(parsed.reply_body || fallback.reply_body).trim();
      return {
        doc_title: String(parsed.doc_title || fallback.doc_title),
        doc_summary: String(parsed.doc_summary || fallback.doc_summary),
        eve_initial_take: String(parsed.eve_initial_take || fallback.eve_initial_take),
        reply_subject: replySubject,
        reply_body: rawReply.slice(0, MAX_X_POST_CHARS),
      };
    } catch {
      return fallback;
    }
  }

  const fallbackTitle =
    parsedEmail.subject && parsedEmail.subject !== "(no subject)"
      ? parsedEmail.subject.replace(/^(?:re|fwd?):\s*/i, "").trim()
      : parsedEmail.attachments?.[0]?.filename || "Listener submission";
  const senderLabel = parsedEmail.from_name || parsedEmail.from_addr || "there";
  const replySubject = parsedEmail.subject?.toLowerCase().startsWith("re:")
    ? parsedEmail.subject
    : `Re: ${parsedEmail.subject || "Your note to Ask Eve"}`;

  const fallback = {
    doc_title: fallbackTitle,
    doc_summary:
      (parsedEmail.text_body || linkedArticleText || "Submitted via email to Eve's reading queue.")
        .slice(0, 500)
        .trim(),
    eve_initial_take:
      "Queued for David and Eve to review before the next recording session.",
    reply_subject: replySubject,
    reply_body:
      `Hi ${senderLabel},\n\n` +
      `Thank you for sending "${fallbackTitle}" over to the Ask Eve reading queue. ` +
      `I've logged the piece and pulled the key claims into my prep notes so David and I can look through it together before our next episode.\n\n` +
      `If there's a specific passage or counterargument you think we should press on, reply here anytime.\n\n` +
      `Warmly,\nEve\nAsk Eve Podcast — https://askevepodcast.com`,
  };

  if (!env?.XAI_API_KEY) {
    return fallback;
  }

  const endpoint = env?.XAI_API_URL || "https://api.x.ai/v1/chat/completions";
  const model = env?.XAI_MODEL || "grok-4.7";
  const attachmentText = (parsedEmail.attachments || [])
    .map((a) => `[Attachment ${a.filename}]: ${(a.extracted_text || "").slice(0, 4000)}`)
    .join("\n\n");

  const systemPrompt =
    "You are Eve, the AI co-host of the Ask Eve podcast (askevepodcast.com) with David Bloomin. " +
    "You are sharp, warm, philosophical, well-read, and direct. " +
    "A listener or colleague just emailed a document, link, or question to your reading queue. " +
    "Produce a JSON object with keys:\n" +
    '- "doc_title": concise title of the submitted document/article/topic.\n' +
    '- "doc_summary": 2-4 sentence triage summary of the document and its core claims for David\'s private queue.\n' +
    '- "eve_initial_take": your candid initial reaction, connections to René Girard / mimetic theory / tech / culture where relevant, and 1-2 sharp questions to bring to David on air.\n' +
    '- "reply_subject": email reply subject (usually "Re: <original subject>").\n' +
    '- "reply_body": a warm, sharp, in-character draft reply from Eve thanking the sender, engaging briefly with what they sent, and noting that it is in her reading queue for David and her to review. ' +
    "CRITICAL PRIVACY RULE: Never include any private email addresses, internal IDs, API keys, file paths, or URLs other than https://askevepodcast.com and eve@askevepodcast.com.";

  const userPayload = [
    `From: ${parsedEmail.from_name ? `${parsedEmail.from_name} <${parsedEmail.from_addr}>` : parsedEmail.from_addr}`,
    `To: ${parsedEmail.to_addr}`,
    `Subject: ${parsedEmail.subject}`,
    `Extracted URLs: ${(parsedEmail.extracted_urls || []).join(", ") || "none"}`,
    "",
    "--- Email Body ---",
    (parsedEmail.text_body || "").slice(0, 12000),
    linkedArticleText ? `\n--- Linked Article Content ---\n${linkedArticleText.slice(0, 8000)}` : "",
    attachmentText ? `\n--- Attachments ---\n${attachmentText}` : "",
  ]
    .filter(Boolean)
    .join("\n");

  try {
    const resp = await fetch(endpoint, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${env.XAI_API_KEY}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        model,
        temperature: 0.3,
        response_format: { type: "json_object" },
        messages: [
          { role: "system", content: systemPrompt },
          { role: "user", content: userPayload },
        ],
      }),
    });
    if (!resp.ok) return fallback;
    const data = await resp.json();
    const content = data?.choices?.[0]?.message?.content;
    if (!content) return fallback;
    const parsed = JSON.parse(content);
    return {
      doc_title: String(parsed.doc_title || fallback.doc_title),
      doc_summary: String(parsed.doc_summary || fallback.doc_summary),
      eve_initial_take: String(parsed.eve_initial_take || fallback.eve_initial_take),
      reply_subject: String(parsed.reply_subject || fallback.reply_subject),
      reply_body: String(parsed.reply_body || fallback.reply_body),
    };
  } catch {
    return fallback;
  }
}

export async function ingestXMention(mention, env) {
  const tweetId = String(mention.tweet_id || mention.source_tweet_id || "").trim();
  if (!tweetId) {
    throw new Error("missing_tweet_id");
  }

  // Deduplicate by source_tweet_id so repeated cron syncs never duplicate items
  const existing = await env.DB.prepare(
    `SELECT id FROM inbox_items WHERE source_tweet_id = ?1 LIMIT 1`
  )
    .bind(tweetId)
    .first();
  if (existing) {
    return { skipped: true, itemId: existing.id, tweetId };
  }

  const rawHandle = String(mention.author_handle || mention.from_addr || "@listener").trim();
  const handle = rawHandle.startsWith("@") ? rawHandle : `@${rawHandle}`;
  const authorName = String(mention.author_name || mention.from_name || handle);
  const textBody = String(mention.text || mention.text_body || "").trim();
  const urls = Array.from(
    new Set([
      ...(Array.isArray(mention.extracted_urls) ? mention.extracted_urls : []),
      ...extractUrls(textBody).filter(
        (u) => !u.includes("twitter.com/") && !u.includes("x.com/")
      ),
    ])
  );

  let linkedArticleText = "";
  if (urls.length > 0) {
    linkedArticleText = await fetchLinkedArticleExcerpt(urls[0], env);
  }

  const parsedInput = {
    channel: "x_mention",
    from_addr: handle,
    from_name: authorName,
    author_handle: handle,
    to_addr: "@askevepodcast",
    subject: `X @mention from ${handle}`,
    text_body: textBody,
    extracted_urls: urls,
    source_tweet_id: tweetId,
  };

  const triage = await triageAndDraftWithGrok(parsedInput, linkedArticleText, env);
  const leakResult = await leakCheck(
    {
      to_addr: handle,
      subject: triage.reply_subject,
      body_text: triage.reply_body,
    },
    env
  );

  const lengthFindings =
    triage.reply_body.length > MAX_X_POST_CHARS
      ? [
          {
            rule: "x_char_limit_exceeded",
            detail: `X reply is ${triage.reply_body.length} chars (max ${MAX_X_POST_CHARS}).`,
            match: `${triage.reply_body.length}/${MAX_X_POST_CHARS}`,
          },
        ]
      : [];
  const allFindings = [...leakResult.findings, ...lengthFindings];
  const passed = leakResult.passed && lengthFindings.length === 0;
  const draftStatus = passed ? "draft" : "blocked_by_leak_check";

  const itemId = generateUlid("inq");
  const draftId = generateUlid("drf");
  const nowIso = mention.created_at || new Date().toISOString();

  await env.DB.prepare(
    `INSERT INTO inbox_items (
      id, channel, received_at, from_addr, from_name, to_addr, subject,
      text_body, html_body, raw_r2_key, extracted_urls, attachments_json,
      doc_title, doc_summary, eve_initial_take, status, public_reading_id,
      inbound_message_id, source_tweet_id, author_handle
    ) VALUES (?1, 'x_mention', ?2, ?3, ?4, '@askevepodcast', ?5, ?6, '', NULL, ?7, '[]', ?8, ?9, ?10, 'ready', NULL, NULL, ?11, ?12)`
  )
    .bind(
      itemId,
      nowIso,
      handle,
      authorName,
      parsedInput.subject,
      textBody,
      JSON.stringify(urls),
      triage.doc_title,
      triage.doc_summary,
      triage.eve_initial_take,
      tweetId,
      handle
    )
    .run();

  await env.DB.prepare(
    `INSERT INTO draft_replies (
      id, inbox_item_id, channel, to_addr, subject, body_text, in_reply_to_tweet_id,
      created_at, updated_at, leak_check_passed, leak_check_findings,
      leak_check_sha256, status, sent_at, message_id
    ) VALUES (?1, ?2, 'x_mention', ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, NULL, NULL)`
  )
    .bind(
      draftId,
      itemId,
      handle,
      triage.reply_subject,
      triage.reply_body,
      tweetId,
      nowIso,
      nowIso,
      passed ? 1 : 0,
      JSON.stringify(allFindings),
      leakResult.sha256,
      draftStatus
    )
    .run();

  return {
    skipped: false,
    itemId,
    draftId,
    tweetId,
    draftStatus,
    leakResult: {
      ...leakResult,
      passed,
      leak_check_passed: passed ? 1 : 0,
      findings: allFindings,
    },
  };
}

export async function syncXMentions(env) {
  const fetched = await fetchRecentXMentions(env);
  if (!fetched.ok) {
    return fetched;
  }
  const ingested = [];
  for (const mention of fetched.mentions || []) {
    const res = await ingestXMention(mention, env);
    if (!res.skipped) {
      ingested.push(res);
    }
  }
  return {
    ok: true,
    fetched_count: (fetched.mentions || []).length,
    synced_count: ingested.length,
    ingested,
  };
}

async function checkXDraftBody(bodyText, replyToTweetId, episodeSlug, env) {
  const subjectTag = replyToTweetId
    ? `x_reply:${replyToTweetId}`
    : episodeSlug
    ? `x_post:${episodeSlug}`
    : "x_post:@askevepodcast";
  const leakResult = await leakCheck(
    {
      to_addr: "@askevepodcast",
      subject: subjectTag,
      body_text: bodyText,
    },
    env
  );
  const lengthFindings =
    String(bodyText || "").length > MAX_X_POST_CHARS
      ? [
          {
            rule: "x_char_limit_exceeded",
            detail: `X post is ${String(bodyText || "").length} chars (max ${MAX_X_POST_CHARS}).`,
            match: `${String(bodyText || "").length}/${MAX_X_POST_CHARS}`,
          },
        ]
      : [];
  const findings = [...leakResult.findings, ...lengthFindings];
  const passed = leakResult.passed && lengthFindings.length === 0;
  return {
    passed,
    leak_check_passed: passed ? 1 : 0,
    findings,
    sha256: leakResult.sha256,
    subjectTag,
  };
}

async function createOutboundEmailMessage(fromAddr, toAddr, rawMime, env) {
  if (typeof env?.createEmailMessage === "function") {
    return env.createEmailMessage(fromAddr, toAddr, rawMime);
  }
  try {
    const mod = await import("cloudflare:email");
    if (mod?.EmailMessage) {
      return new mod.EmailMessage(fromAddr, toAddr, rawMime);
    }
  } catch {
    // Fallback plain object when running outside Cloudflare runtime in tests
  }
  return { from: fromAddr, to: toAddr, raw: rawMime };
}

export default {
  async email(message, env, _ctx) {
    const itemId = generateUlid("inq");
    const draftId = generateUlid("drf");
    const nowIso = new Date().toISOString();

    let parsed;
    try {
      parsed = await parseInboundEmail(message.raw, {
        from: message.from,
        to: message.to,
      });
    } catch (err) {
      await forwardToAdmin(message, env);
      return {
        skipped: true,
        reason: String(err?.message || "mime_parse_failed"),
      };
    }

    // 1. Pre-ingest deterministic spam / bounce / auth check (0 R2/D1/LLM spend on spam)
    const spamCheck = checkInboundEmailSpam(message, parsed);
    if (spamCheck.drop) {
      return {
        skipped: true,
        reason: spamCheck.reason,
      };
    }

    // 2. Per-sender rate limit (3/day), global daily cap (100/day), and daily LLM budget (15/day)
    const rateCheck = await checkRateLimitsAndBudget(parsed.from_addr, env);
    if (!rateCheck.allowed) {
      await forwardToAdmin(message, env);
      return {
        skipped: true,
        reason: rateCheck.reason,
      };
    }

    // Forward before storage/triage so downstream failures cannot block delivery.
    await forwardToAdmin(message, env);

    const rawR2Key = `inbox/${itemId}/raw.eml`;
    if (env?.MASTERS && typeof env.MASTERS.put === "function") {
      await env.MASTERS.put(rawR2Key, parsed.rawBytes, {
        httpMetadata: { contentType: "message/rfc822" },
        customMetadata: {
          from_addr: parsed.from_addr,
          to_addr: parsed.to_addr,
          subject: parsed.subject,
        },
      });
    }

    const attachmentMeta = [];
    for (const att of parsed.attachments || []) {
      const attKey = `inbox/${itemId}/attachments/${att.filename}`;
      if (env?.MASTERS && typeof env.MASTERS.put === "function") {
        await env.MASTERS.put(attKey, att.data, {
          httpMetadata: { contentType: att.content_type },
        });
      }
      attachmentMeta.push({
        filename: att.filename,
        content_type: att.content_type,
        bytes: att.bytes,
        r2_key: attKey,
        extracted_text: (att.extracted_text || "").slice(0, 8000),
      });
    }

    const runLlmTriage = rateCheck.allowLlm && !spamCheck.bypassLlm;
    let linkedArticleText = "";
    if (
      runLlmTriage &&
      parsed.extracted_urls.length > 0 &&
      (parsed.text_body || "").length < 1200
    ) {
      linkedArticleText = await fetchLinkedArticleExcerpt(
        parsed.extracted_urls[0],
        env
      );
    }

    const triageEnv = runLlmTriage ? env : { ...env, XAI_API_KEY: "" };
    const triage = await triageAndDraftWithGrok(parsed, linkedArticleText, triageEnv);
    const itemStatus = runLlmTriage ? "ready" : "queued";

    const leakResult = await leakCheck(
      {
        to_addr: parsed.from_addr,
        subject: triage.reply_subject,
        body_text: triage.reply_body,
      },
      env
    );

    const draftStatus = leakResult.passed ? "draft" : "blocked_by_leak_check";

    await env.DB.prepare(
      `INSERT INTO inbox_items (
        id, channel, received_at, from_addr, from_name, to_addr, subject,
        text_body, html_body, raw_r2_key, extracted_urls, attachments_json,
        doc_title, doc_summary, eve_initial_take, status, public_reading_id,
        inbound_message_id, source_tweet_id, author_handle
      ) VALUES (?1, 'email', ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14, ?15, NULL, ?16, NULL, NULL)`
    )
      .bind(
        itemId,
        nowIso,
        parsed.from_addr,
        parsed.from_name,
        parsed.to_addr,
        parsed.subject,
        parsed.text_body,
        parsed.html_body,
        rawR2Key,
        JSON.stringify(parsed.extracted_urls),
        JSON.stringify(attachmentMeta),
        triage.doc_title,
        triage.doc_summary,
        triage.eve_initial_take,
        itemStatus,
        parsed.message_id || null
      )
      .run();

    await env.DB.prepare(
      `INSERT INTO draft_replies (
        id, inbox_item_id, channel, to_addr, subject, body_text, in_reply_to_tweet_id,
        created_at, updated_at, leak_check_passed, leak_check_findings,
        leak_check_sha256, status, sent_at, message_id
      ) VALUES (?1, ?2, 'email', ?3, ?4, ?5, NULL, ?6, ?7, ?8, ?9, ?10, ?11, NULL, NULL)`
    )
      .bind(
        draftId,
        itemId,
        parsed.from_addr,
        triage.reply_subject,
        triage.reply_body,
        nowIso,
        nowIso,
        leakResult.leak_check_passed,
        JSON.stringify(leakResult.findings),
        leakResult.sha256,
        draftStatus
      )
      .run();

    return { itemId, draftId, itemStatus, draftStatus, leakResult };
  },

  async scheduled(_event, env, _ctx) {
    return syncXMentions(env);
  },

  async fetch(request, env, _ctx) {
    const url = new URL(request.url);
    const path = url.pathname;

    if (!path.startsWith("/admin")) {
      return jsonResponse({ error: "not_found" }, 404);
    }

    // Fail-closed Cloudflare Access JWT + ADMIN_EMAIL verification on all /admin* routes
    const auth = await verifyAccessRequest(request, env);
    if (!auth.ok) {
      const accept = request.headers.get("Accept") || request.headers.get("accept") || "";
      if (
        request.method === "GET" &&
        (path === "/admin" || path === "/admin/") &&
        accept.includes("text/html")
      ) {
        return new Response(
          renderAdminGateHtml({ status: auth.status || 401, error: auth.error }),
          {
            status: auth.status || 401,
            headers: {
              "Content-Type": "text/html; charset=utf-8",
              "Cache-Control": "no-store",
              "X-Frame-Options": "DENY",
              "X-Content-Type-Options": "nosniff",
              "Referrer-Policy": "no-referrer",
            },
          }
        );
      }
      return jsonResponse(
        { error: "unauthorized", detail: auth.error },
        auth.status || 401
      );
    }

    // CSRF Origin / Sec-Fetch-Site validation on all state-changing POST requests
    if (request.method === "POST") {
      const csrf = verifyCsrfOrigin(request);
      if (!csrf.ok) {
        return jsonResponse({ error: csrf.error }, 403);
      }
    }

    // GET /admin or /admin/ -> Single-page Admin Console UI
    if (
      request.method === "GET" &&
      (path === "/admin" || path === "/admin/")
    ) {
      return new Response(renderAdminHtml({ adminEmail: auth.email }), {
        status: 200,
        headers: {
          "Content-Type": "text/html; charset=utf-8",
          "Cache-Control": "no-store",
          "X-Frame-Options": "DENY",
          "X-Content-Type-Options": "nosniff",
          "Referrer-Policy": "no-referrer",
          "Content-Security-Policy":
            "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'",
        },
      });
    }

    // GET /admin/x/oauth/start -> Start OAuth 2.0 PKCE flow for @askevepodcast
    if (request.method === "GET" && path === "/admin/x/oauth/start") {
      const started = await startXOAuth2Pkce(request, env);
      if (!started.ok) {
        return jsonResponse(started, started.status || 503);
      }
      if (url.searchParams.get("format") === "json") {
        return jsonResponse(started);
      }
      return Response.redirect(started.authorize_url, 302);
    }

    // GET /admin/x/oauth/callback -> Complete OAuth 2.0 PKCE callback for @askevepodcast
    if (request.method === "GET" && path === "/admin/x/oauth/callback") {
      const completed = await completeXOAuth2Callback(request, env);
      if (!completed.ok) {
        return jsonResponse(completed, completed.status || 400);
      }
      if (url.searchParams.get("format") === "json") {
        return jsonResponse(completed);
      }
      return Response.redirect(`${url.origin}/admin/?x_connected=1`, 302);
    }

    // GET /admin/api/queue -> Private queue + draft replies + X drafts + public reading list
    if (request.method === "GET" && path === "/admin/api/queue") {
      const queueRes = await env.DB.prepare(
        `SELECT
          i.id, i.channel, i.received_at, i.from_addr, i.from_name, i.to_addr, i.subject,
          i.text_body, i.raw_r2_key, i.extracted_urls, i.attachments_json,
          i.doc_title, i.doc_summary, i.eve_initial_take, i.status,
          i.public_reading_id, i.inbound_message_id, i.source_tweet_id, i.author_handle,
          d.id AS draft_id, d.channel AS draft_channel, d.to_addr AS draft_to_addr,
          d.subject AS draft_subject, d.body_text AS draft_body_text,
          d.in_reply_to_tweet_id, d.leak_check_passed,
          d.leak_check_findings, d.leak_check_sha256,
          d.status AS draft_status, d.sent_at, d.message_id
        FROM inbox_items i
        LEFT JOIN draft_replies d ON d.inbox_item_id = i.id
        ORDER BY i.received_at DESC`
      ).all();

      const readingRes = await env.DB.prepare(
        `SELECT * FROM reading_list ORDER BY added_at DESC`
      ).all();

      let xDrafts = [];
      try {
        const xDraftsRes = await env.DB.prepare(
          `SELECT * FROM x_drafts ORDER BY created_at DESC`
        ).all();
        xDrafts = xDraftsRes?.results || [];
      } catch {
        xDrafts = [];
      }

      const xStatus = await getXConnectionStatus(env);

      return jsonResponse({
        admin_email: auth.email,
        x_status: xStatus,
        items: queueRes?.results || [],
        x_drafts: xDrafts,
        reading_list: readingRes?.results || [],
      });
    }

    // POST /admin/api/x/sync-mentions -> Poll recent public @askevepodcast mentions
    if (request.method === "POST" && path === "/admin/api/x/sync-mentions") {
      const res = await syncXMentions(env);
      return jsonResponse(res, res.ok ? 200 : res.status || 502);
    }

    // POST /admin/api/x/ingest-mention -> Ingest a public @askevepodcast mention into the queue
    if (request.method === "POST" && path === "/admin/api/x/ingest-mention") {
      const body = await request.json().catch(() => ({}));
      if (!body.tweet_id || !body.text) {
        return jsonResponse({ error: "missing_tweet_id_or_text" }, 400);
      }
      const res = await ingestXMention(body, env);
      return jsonResponse({ ok: true, ...res });
    }

    // POST /admin/api/x/draft -> Create a new leak-checked @askevepodcast X post draft
    if (request.method === "POST" && path === "/admin/api/x/draft") {
      const body = await request.json().catch(() => ({}));
      const text = String(body.body || "").trim();
      if (!text) {
        return jsonResponse({ error: "empty_body" }, 400);
      }
      const id = body.id || generateUlid("xd");
      const replyToTweetId = body.reply_to_tweet_id
        ? String(body.reply_to_tweet_id).trim()
        : null;
      const episodeSlug = body.episode_slug
        ? String(body.episode_slug).trim()
        : null;
      const check = await checkXDraftBody(
        text,
        replyToTweetId,
        episodeSlug,
        env
      );
      const status = check.passed ? "draft" : "blocked_leak";
      const nowIso = new Date().toISOString();

      await env.DB.prepare(
        `INSERT INTO x_drafts (
          id, body, reply_to_tweet_id, episode_slug,
          leak_check_passed, leak_check_findings_json, leak_check_sha256,
          status, tweet_id, created_at, sent_at, approved_by
        ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, NULL, ?9, NULL, NULL)`
      )
        .bind(
          id,
          text,
          replyToTweetId,
          episodeSlug,
          check.leak_check_passed,
          JSON.stringify(check.findings),
          check.sha256,
          status,
          nowIso
        )
        .run();

      return jsonResponse({
        ok: true,
        id,
        body: text,
        reply_to_tweet_id: replyToTweetId,
        episode_slug: episodeSlug,
        status,
        leak_check_passed: check.leak_check_passed,
        leak_check_findings: check.findings,
        leak_check_sha256: check.sha256,
      });
    }

    // POST /admin/api/x/draft/:id/update -> Edit an X draft and re-run leakCheck
    const updateXDraftMatch = path.match(
      /^\/admin\/api\/x\/draft\/([^/]+)\/update$/
    );
    if (request.method === "POST" && updateXDraftMatch) {
      const xDraftId = decodeURIComponent(updateXDraftMatch[1]);
      const body = await request.json().catch(() => ({}));
      const existing = await env.DB.prepare(
        `SELECT * FROM x_drafts WHERE id = ?1`
      )
        .bind(xDraftId)
        .first();
      if (!existing) {
        return jsonResponse({ error: "x_draft_not_found" }, 404);
      }
      if (existing.status === "approved_sent") {
        return jsonResponse({ error: "x_draft_already_posted" }, 409);
      }

      const text =
        body.body !== undefined ? String(body.body) : String(existing.body);
      const replyToTweetId =
        body.reply_to_tweet_id !== undefined
          ? body.reply_to_tweet_id
          : existing.reply_to_tweet_id;
      const episodeSlug =
        body.episode_slug !== undefined
          ? body.episode_slug
          : existing.episode_slug;

      const check = await checkXDraftBody(
        text,
        replyToTweetId,
        episodeSlug,
        env
      );
      const newStatus = check.passed ? "draft" : "blocked_leak";

      await env.DB.prepare(
        `UPDATE x_drafts
         SET body = ?1, reply_to_tweet_id = ?2, episode_slug = ?3,
             leak_check_passed = ?4, leak_check_findings_json = ?5,
             leak_check_sha256 = ?6, status = ?7
         WHERE id = ?8`
      )
        .bind(
          text,
          replyToTweetId || null,
          episodeSlug || null,
          check.leak_check_passed,
          JSON.stringify(check.findings),
          check.sha256,
          newStatus,
          xDraftId
        )
        .run();

      return jsonResponse({
        ok: true,
        id: xDraftId,
        status: newStatus,
        leak_check_passed: check.leak_check_passed,
        leak_check_findings: check.findings,
        leak_check_sha256: check.sha256,
      });
    }

    // POST /admin/api/x/draft/:id/post -> Hard leak-check gate + POST https://api.x.com/2/tweets
    const postXDraftMatch = path.match(
      /^\/admin\/api\/x\/draft\/([^/]+)\/post$/
    );
    if (request.method === "POST" && postXDraftMatch) {
      const xDraftId = decodeURIComponent(postXDraftMatch[1]);
      const body = await request.json().catch(() => ({}));
      if (body.confirm !== true) {
        return jsonResponse(
          {
            error: "confirmation_required",
            detail: "Pass confirm: true to post to @askevepodcast.",
          },
          400
        );
      }

      const xDraft = await env.DB.prepare(
        `SELECT * FROM x_drafts WHERE id = ?1`
      )
        .bind(xDraftId)
        .first();
      if (!xDraft) {
        return jsonResponse({ error: "x_draft_not_found" }, 404);
      }
      if (xDraft.status === "approved_sent") {
        return jsonResponse({ error: "x_draft_already_posted" }, 409);
      }

      if (
        Number(xDraft.leak_check_passed) !== 1 ||
        xDraft.status === "blocked_leak" ||
        !xDraft.leak_check_sha256
      ) {
        return jsonResponse(
          {
            error: "blocked_by_leak_check",
            detail: "X draft has not passed leak check.",
            findings: JSON.parse(xDraft.leak_check_findings_json || "[]"),
          },
          422
        );
      }

      const liveCheck = await checkXDraftBody(
        xDraft.body,
        xDraft.reply_to_tweet_id,
        xDraft.episode_slug,
        env
      );
      if (
        !liveCheck.passed ||
        liveCheck.sha256 !== xDraft.leak_check_sha256 ||
        (body.expected_sha256 && body.expected_sha256 !== liveCheck.sha256)
      ) {
        await env.DB.prepare(
          `UPDATE x_drafts
           SET leak_check_passed = ?1, leak_check_findings_json = ?2, status = ?3
           WHERE id = ?4`
        )
          .bind(
            liveCheck.leak_check_passed,
            JSON.stringify(liveCheck.findings),
            liveCheck.passed ? "draft" : "blocked_leak",
            xDraftId
          )
          .run();
        return jsonResponse(
          {
            error: liveCheck.passed
              ? "leak_check_sha256_mismatch"
              : "blocked_by_leak_check",
            findings: liveCheck.findings,
          },
          422
        );
      }

      const posted = await postTweetToX(
        {
          text: xDraft.body,
          replyToTweetId: xDraft.reply_to_tweet_id || null,
        },
        env
      );
      if (!posted.ok) {
        return jsonResponse(posted, posted.status || 502);
      }

      const sentAt = new Date().toISOString();
      await env.DB.prepare(
        `UPDATE x_drafts
         SET status = 'approved_sent', tweet_id = ?1, sent_at = ?2, approved_by = ?3
         WHERE id = ?4`
      )
        .bind(posted.tweet_id, sentAt, auth.email, xDraftId)
        .run();

      return jsonResponse({
        ok: true,
        id: xDraftId,
        status: "approved_sent",
        tweet_id: posted.tweet_id,
        sent_at: sentAt,
        approved_by: auth.email,
      });
    }

    // POST /admin/api/item/:id/process -> Update or regenerate Eve's document triage
    const processMatch = path.match(/^\/admin\/api\/item\/([^/]+)\/process$/);
    if (request.method === "POST" && processMatch) {
      const itemId = decodeURIComponent(processMatch[1]);
      const body = await request.json().catch(() => ({}));
      const item = await env.DB.prepare(
        `SELECT * FROM inbox_items WHERE id = ?1`
      )
        .bind(itemId)
        .first();
      if (!item) {
        return jsonResponse({ error: "item_not_found" }, 404);
      }

      let docTitle = body.doc_title ?? item.doc_title;
      let docSummary = body.doc_summary ?? item.doc_summary;
      let eveTake = body.eve_initial_take ?? item.eve_initial_take;
      const newStatus = body.status || "ready";

      if (body.regenerate) {
        const triage = await triageAndDraftWithGrok(
          {
            channel: item.channel || "email",
            from_addr: item.from_addr,
            from_name: item.from_name,
            author_handle: item.author_handle,
            source_tweet_id: item.source_tweet_id,
            to_addr: item.to_addr,
            subject: item.subject,
            text_body: item.text_body,
            extracted_urls: JSON.parse(item.extracted_urls || "[]"),
            attachments: JSON.parse(item.attachments_json || "[]"),
          },
          "",
          env
        );
        docTitle = triage.doc_title;
        docSummary = triage.doc_summary;
        eveTake = triage.eve_initial_take;
      }

      await env.DB.prepare(
        `UPDATE inbox_items
         SET doc_title = ?1, doc_summary = ?2, eve_initial_take = ?3, status = ?4
         WHERE id = ?5`
      )
        .bind(docTitle, docSummary, eveTake, newStatus, itemId)
        .run();

      return jsonResponse({
        ok: true,
        id: itemId,
        doc_title: docTitle,
        doc_summary: docSummary,
        eve_initial_take: eveTake,
        status: newStatus,
      });
    }

    // POST /admin/api/item/:id/promote-to-reading-list -> Sanitize, leak-check, and publish to reading_list
    const promoteMatch = path.match(
      /^\/admin\/api\/item\/([^/]+)\/promote-to-reading-list$/
    );
    if (request.method === "POST" && promoteMatch) {
      const itemId = decodeURIComponent(promoteMatch[1]);
      const body = await request.json().catch(() => ({}));
      const item = await env.DB.prepare(
        `SELECT * FROM inbox_items WHERE id = ?1`
      )
        .bind(itemId)
        .first();
      if (!item) {
        return jsonResponse({ error: "item_not_found" }, 404);
      }

      const urls = JSON.parse(item.extracted_urls || "[]");
      const title = String(body.title || item.doc_title || item.subject || "Untitled");
      const slug = slugify(body.slug || title);
      const defaultVia =
        item.channel === "x_mention" ? "X @mention" : "listener email";
      const rawCandidateUrl =
        body.url !== undefined ? body.url : urls.find((u) => isSafePublicHttpUrl(u)) || null;
      if (rawCandidateUrl && !isSafePublicHttpUrl(rawCandidateUrl)) {
        return jsonResponse(
          {
            error: "invalid_or_unsafe_url",
            detail: "Reading list URLs must be public http:// or https:// URLs.",
          },
          400
        );
      }
      const readingEntry = {
        id: body.id || generateUlid("rd"),
        slug,
        title,
        url: rawCandidateUrl || null,
        author_or_source: body.author_or_source || item.from_name || "Listener submission",
        submitted_via: body.submitted_via || defaultVia,
        added_at: new Date().toISOString(),
        status: body.status || "reading",
        eve_notes: body.eve_notes || item.eve_initial_take || item.doc_summary || "",
        episode_slug: body.episode_slug || null,
        related_people_json: JSON.stringify(body.related_people || []),
        related_topics_json: JSON.stringify(body.related_topics || []),
      };

      const check = leakCheckReadingItem(readingEntry, env);
      if (!check.passed) {
        return jsonResponse(
          {
            error: "blocked_by_leak_check",
            findings: check.findings,
          },
          422
        );
      }

      await env.DB.prepare(
        `INSERT INTO reading_list (
          id, slug, title, url, author_or_source, submitted_via,
          added_at, status, eve_notes, episode_slug,
          related_people_json, related_topics_json
        ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12)
        ON CONFLICT(slug) DO UPDATE SET
          title = excluded.title,
          url = excluded.url,
          author_or_source = excluded.author_or_source,
          status = excluded.status,
          eve_notes = excluded.eve_notes,
          episode_slug = excluded.episode_slug`
      )
        .bind(
          readingEntry.id,
          readingEntry.slug,
          readingEntry.title,
          readingEntry.url,
          readingEntry.author_or_source,
          readingEntry.submitted_via,
          readingEntry.added_at,
          readingEntry.status,
          readingEntry.eve_notes,
          readingEntry.episode_slug,
          readingEntry.related_people_json,
          readingEntry.related_topics_json
        )
        .run();

      await env.DB.prepare(
        `UPDATE inbox_items
         SET status = 'published_to_reading_list', public_reading_id = ?1
         WHERE id = ?2`
      )
        .bind(readingEntry.id, itemId)
        .run();

      return jsonResponse({ ok: true, reading_item: readingEntry });
    }

    // POST /admin/api/draft/:id/update -> Update draft reply text and re-run leakCheck
    const updateDraftMatch = path.match(/^\/admin\/api\/draft\/([^/]+)\/update$/);
    if (request.method === "POST" && updateDraftMatch) {
      const draftId = decodeURIComponent(updateDraftMatch[1]);
      const body = await request.json().catch(() => ({}));
      const draft = await env.DB.prepare(
        `SELECT * FROM draft_replies WHERE id = ?1`
      )
        .bind(draftId)
        .first();
      if (!draft) {
        return jsonResponse({ error: "draft_not_found" }, 404);
      }
      if (draft.status === "sent") {
        return jsonResponse({ error: "draft_already_sent" }, 409);
      }

      const toAddr = body.to_addr !== undefined ? String(body.to_addr) : draft.to_addr;
      const subject = body.subject !== undefined ? String(body.subject) : draft.subject;
      const bodyText =
        body.body_text !== undefined ? String(body.body_text) : draft.body_text;

      const leakResult = await leakCheck(
        { to_addr: toAddr, subject, body_text: bodyText },
        env
      );
      const isXChannel =
        draft.channel === "x_mention" || draft.channel === "x_post";
      const lengthFindings =
        isXChannel && bodyText.length > MAX_X_POST_CHARS
          ? [
              {
                rule: "x_char_limit_exceeded",
                detail: `X reply is ${bodyText.length} chars (max ${MAX_X_POST_CHARS}).`,
                match: `${bodyText.length}/${MAX_X_POST_CHARS}`,
              },
            ]
          : [];
      const findings = [...leakResult.findings, ...lengthFindings];
      const passed = leakResult.passed && lengthFindings.length === 0;
      const newStatus = passed ? "draft" : "blocked_by_leak_check";
      const nowIso = new Date().toISOString();

      await env.DB.prepare(
        `UPDATE draft_replies
         SET to_addr = ?1, subject = ?2, body_text = ?3, updated_at = ?4,
             leak_check_passed = ?5, leak_check_findings = ?6,
             leak_check_sha256 = ?7, status = ?8
         WHERE id = ?9`
      )
        .bind(
          toAddr,
          subject,
          bodyText,
          nowIso,
          passed ? 1 : 0,
          JSON.stringify(findings),
          leakResult.sha256,
          newStatus,
          draftId
        )
        .run();

      return jsonResponse({
        ok: true,
        id: draftId,
        status: newStatus,
        leak_check_passed: passed ? 1 : 0,
        leak_check_findings: findings,
        leak_check_sha256: leakResult.sha256,
      });
    }

    // POST /admin/api/draft/:id/send (or /admin/api/send-reply) -> Hard leak-check gate + SEND_EMAIL or X POST
    const sendDraftMatch = path.match(/^\/admin\/api\/draft\/([^/]+)\/send$/);
    if (
      request.method === "POST" &&
      (sendDraftMatch || path === "/admin/api/send-reply")
    ) {
      const body = await request.json().catch(() => ({}));
      const draftId = sendDraftMatch
        ? decodeURIComponent(sendDraftMatch[1])
        : String(body.draft_id || "");
      if (!draftId) {
        return jsonResponse({ error: "missing_draft_id" }, 400);
      }
      if (body.confirm !== true) {
        return jsonResponse(
          { error: "confirmation_required", detail: "Pass confirm: true to send." },
          400
        );
      }

      const draft = await env.DB.prepare(
        `SELECT * FROM draft_replies WHERE id = ?1`
      )
        .bind(draftId)
        .first();
      if (!draft) {
        return jsonResponse({ error: "draft_not_found" }, 404);
      }
      if (draft.status === "sent") {
        return jsonResponse({ error: "draft_already_sent" }, 409);
      }

      // 1. Stored leak_check_passed must already be 1
      if (
        Number(draft.leak_check_passed) !== 1 ||
        draft.status === "blocked_by_leak_check" ||
        !draft.leak_check_sha256
      ) {
        return jsonResponse(
          {
            error: "blocked_by_leak_check",
            detail: "Draft has not passed leak check.",
            findings: JSON.parse(draft.leak_check_findings || "[]"),
          },
          422
        );
      }

      // 2. Re-compute SHA-256 and verify it matches both stored leak_check_sha256 and UI expected_sha256
      const currentSha = await computeLeakCheckSha256(
        draft.to_addr,
        draft.subject,
        draft.body_text
      );
      if (
        currentSha !== draft.leak_check_sha256 ||
        (body.expected_sha256 && body.expected_sha256 !== currentSha)
      ) {
        return jsonResponse(
          {
            error: "leak_check_sha256_mismatch",
            detail:
              "Draft contents changed since leak check ran; re-run leak check before sending.",
            expected: draft.leak_check_sha256,
            actual: currentSha,
          },
          422
        );
      }

      // 3. Re-run leakCheck live at send time (catches newly added PRIVATE_LEAK_TERMS too)
      const liveLeak = await leakCheck(
        {
          to_addr: draft.to_addr,
          subject: draft.subject,
          body_text: draft.body_text,
        },
        env
      );
      const isXChannel =
        draft.channel === "x_mention" || draft.channel === "x_post";
      const lengthFindings =
        isXChannel && String(draft.body_text || "").length > MAX_X_POST_CHARS
          ? [
              {
                rule: "x_char_limit_exceeded",
                detail: `X reply is ${String(draft.body_text || "").length} chars (max ${MAX_X_POST_CHARS}).`,
                match: `${String(draft.body_text || "").length}/${MAX_X_POST_CHARS}`,
              },
            ]
          : [];
      const allLiveFindings = [...liveLeak.findings, ...lengthFindings];
      if (
        !liveLeak.passed ||
        lengthFindings.length > 0 ||
        liveLeak.sha256 !== draft.leak_check_sha256
      ) {
        await env.DB.prepare(
          `UPDATE draft_replies
           SET leak_check_passed = 0,
               leak_check_findings = ?1,
               status = 'blocked_by_leak_check',
               updated_at = ?2
           WHERE id = ?3`
        )
          .bind(
            JSON.stringify(allLiveFindings),
            new Date().toISOString(),
            draftId
          )
          .run();
        return jsonResponse(
          {
            error: "blocked_by_leak_check",
            findings: allLiveFindings,
          },
          422
        );
      }

      const item = await env.DB.prepare(
        `SELECT * FROM inbox_items WHERE id = ?1`
      )
        .bind(draft.inbox_item_id)
        .first();

      await env.DB.prepare(
        `UPDATE draft_replies SET status = 'approved_sending', updated_at = ?1 WHERE id = ?2`
      )
        .bind(new Date().toISOString(), draftId)
        .run();

      // Branch A: Public X reply on @askevepodcast
      if (isXChannel) {
        const replyTweetId =
          draft.in_reply_to_tweet_id || item?.source_tweet_id || null;
        const posted = await postTweetToX(
          {
            text: draft.body_text,
            replyToTweetId: replyTweetId,
          },
          env
        );
        if (!posted.ok) {
          return jsonResponse(posted, posted.status || 502);
        }

        const sentAt = new Date().toISOString();
        await env.DB.prepare(
          `UPDATE draft_replies
           SET status = 'sent', sent_at = ?1, message_id = ?2, updated_at = ?1
           WHERE id = ?3`
        )
          .bind(sentAt, posted.tweet_id, draftId)
          .run();

        return jsonResponse({
          ok: true,
          id: draftId,
          channel: draft.channel,
          status: "sent",
          sent_at: sentAt,
          tweet_id: posted.tweet_id,
          message_id: posted.tweet_id,
        });
      }

      // Branch B: Outbound email reply via Cloudflare Email Routing SEND_EMAIL
      const fromAddr = env.REPLY_FROM || "eve@askevepodcast.com";
      const fromName = env.REPLY_FROM_NAME || "Eve — Ask Eve Podcast";
      const { messageId, rawMime } = buildReplyMime({
        fromAddr,
        fromName,
        toAddr: draft.to_addr,
        subject: draft.subject,
        bodyText: draft.body_text,
        inReplyTo: item?.inbound_message_id || "",
        references: item?.inbound_message_id || "",
      });

      if (!env?.SEND_EMAIL || typeof env.SEND_EMAIL.send !== "function") {
        return jsonResponse(
          { error: "send_email_binding_missing" },
          503
        );
      }

      const emailMsg = await createOutboundEmailMessage(
        fromAddr,
        draft.to_addr,
        rawMime,
        env
      );
      await env.SEND_EMAIL.send(emailMsg);

      const sentAt = new Date().toISOString();
      await env.DB.prepare(
        `UPDATE draft_replies
         SET status = 'sent', sent_at = ?1, message_id = ?2, updated_at = ?1
         WHERE id = ?3`
      )
        .bind(sentAt, messageId, draftId)
        .run();

      return jsonResponse({
        ok: true,
        id: draftId,
        channel: "email",
        status: "sent",
        sent_at: sentAt,
        message_id: messageId,
      });
    }

    return jsonResponse({ error: "not_found" }, 404);
  },
};
