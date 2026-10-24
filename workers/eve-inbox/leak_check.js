// workers/eve-inbox/leak_check.js
// Deterministic + optional LLM privacy & secret leak checker for outbound draft replies
// and public reading_list promotions. Mirrors scripts/leak_check.py.

export const PUBLIC_ALLOWED_EMAILS = new Set([
  "eve@askevepodcast.com",
  "reading@askevepodcast.com",
  "podcast@askevepodcast.com",
]);

export const FORBIDDEN_EMAIL_DOMAINS_IN_TEXT = new Set([
  "gmail.com",
  "googlemail.com",
  "dbloom.in",
]);

export const FORBIDDEN_EMAIL_DOMAINS_ANYWHERE = new Set([
  "dbloom.in",
]);

const EMAIL_RE = /[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})/g;

const RULE_PATTERNS = [
  {
    rule: "api_key_or_token",
    detail: "xAI API key (xai-...)",
    re: /\bxai-[A-Za-z0-9_-]{8,}\b/g,
  },
  {
    rule: "api_key_or_token",
    detail: "Secret key token (sk-...)",
    re: /\bsk-(?:proj-|ant-|live-|test-)?[A-Za-z0-9_-]{12,}\b/g,
  },
  {
    rule: "api_key_or_token",
    detail: "GitHub token (ghp_/gho_/ghu_/ghs_/ghr_/github_pat_)",
    re: /\b(?:gh[pousr]_[A-Za-z0-9_]{12,}|github_pat_[A-Za-z0-9_]{12,})\b/g,
  },
  {
    rule: "api_key_or_token",
    detail: "Cloudflare or AWS token/key",
    re: /\b(?:cfut_[A-Za-z0-9_-]{12,}|v1\.[0-9a-fA-F]{16,}|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{30,})\b/g,
  },
  {
    rule: "api_key_or_token",
    detail: "Bearer authorization token",
    re: /\bBearer\s+[A-Za-z0-9._~+/=-]{16,}/g,
  },
  {
    rule: "api_key_or_token",
    detail: "Zone/service credential identifier",
    re: /\bzone_fm_[A-Za-z0-9_-]+\b/g,
  },
  {
    rule: "api_key_or_token",
    detail: "32+ character hex secret or resource ID",
    re: /\b[0-9a-fA-F]{32,64}\b/g,
  },
  {
    rule: "internal_id_or_private_url",
    detail: "Internal UUID identifier",
    re: /\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b/g,
  },
  {
    rule: "internal_id_or_private_url",
    detail: "Internal subagent identifier (sand-subagent-*)",
    re: /\bsand-subagent-[A-Za-z0-9_-]*\b/gi,
  },
  {
    rule: "internal_id_or_private_url",
    detail: "Internal agent store identifier (store-*)",
    re: /\bstore-[0-9a-fA-F]{4,}(?:-[0-9a-fA-F]{4,})+\b|\bstore-[0-9a-fA-F]{8,}\b/gi,
  },
  {
    rule: "internal_id_or_private_url",
    detail: "Private Cursor canvas link (cursor.com/canvas/*)",
    re: /cursor\.com\/canvas(?:\/[^\s"'<>)]*)?/gi,
  },
  {
    rule: "internal_id_or_private_url",
    detail: "Private Grok share link (grok.com/share/*)",
    re: /grok\.com\/share(?:\/[^\s"'<>)]*)?|\bc2hhcmQt[A-Za-z0-9_-]+/gi,
  },
  {
    rule: "local_filesystem_path",
    detail: "Local sandbox/box filesystem path",
    re: /\/(?:home\/box|workspace|cursor\/stores|root\/\.ssh|private\/tmp)(?:\/[^\s"'<>)]*)?|\bfile:\/\/\/\S+/g,
  },
  {
    rule: "private_hostname_or_infra",
    detail: "Private Tailscale tailnet hostname (*.ts.net)",
    re: /\b[A-Za-z0-9._-]+\.ts\.net\b/gi,
  },
  {
    rule: "private_hostname_or_infra",
    detail: "Private infrastructure or bucket identifier (ask-eve-masters / hermes-hub / R2 endpoint)",
    re: /\b(?:ask-eve-masters|hermes-hub|upload\.askevepodcast\.com)\b|[A-Za-z0-9._-]*\.r2\.cloudflarestorage\.com\b/gi,
  },
];

export function extractEmailAddress(raw) {
  if (!raw) return "";
  const m = String(raw).match(/[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/);
  return m ? m[0].toLowerCase() : String(raw).trim().toLowerCase();
}

export function parsePrivateTerms(rawTerms) {
  if (!rawTerms) return [];
  const parts = Array.isArray(rawTerms)
    ? rawTerms
    : String(rawTerms).split(/[,\n\r]+/);
  const out = [];
  for (const part of parts) {
    const cleaned = String(part || "").trim();
    if (cleaned.length >= 3) out.push(cleaned);
  }
  return out;
}

export async function computeLeakCheckSha256(toAddr, subject, bodyText) {
  const payload = JSON.stringify([
    String(toAddr ?? ""),
    String(subject ?? ""),
    String(bodyText ?? ""),
  ]);
  const bytes = new TextEncoder().encode(payload);
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

function domainMatchesSet(domain, domainSet) {
  if (domainSet.has(domain)) return true;
  for (const d of domainSet) {
    if (domain.endsWith("." + d)) return true;
  }
  return false;
}

export function scanTextForRules(
  text,
  { fieldName, allowedRecipientEmail = "", isRecipientField = false, privateTerms = [] }
) {
  const findings = [];
  if (!text) return findings;
  const str = String(text);

  // 1. Email address rules
  for (const m of str.matchAll(EMAIL_RE)) {
    const fullEmail = m[0].toLowerCase();
    const domain = m[1].toLowerCase();

    if (domainMatchesSet(domain, FORBIDDEN_EMAIL_DOMAINS_ANYWHERE)) {
      findings.push({
        rule: "unauthorized_email",
        field: fieldName,
        detail: `Forbidden private domain (${domain}) in ${fieldName}`,
        match: m[0],
      });
      continue;
    }

    if (!isRecipientField && domainMatchesSet(domain, FORBIDDEN_EMAIL_DOMAINS_IN_TEXT)) {
      findings.push({
        rule: "unauthorized_email",
        field: fieldName,
        detail: `Personal/forbidden email domain (${domain}) in ${fieldName}`,
        match: m[0],
      });
      continue;
    }

    if (PUBLIC_ALLOWED_EMAILS.has(fullEmail)) continue;
    if (allowedRecipientEmail && fullEmail === allowedRecipientEmail) continue;

    findings.push({
      rule: "unauthorized_email",
      field: fieldName,
      detail: `Email address in ${fieldName} is not an allowed public Ask Eve address or the recipient's address`,
      match: m[0],
    });
  }

  // 2. Regex rules
  for (const { rule, detail, re } of RULE_PATTERNS) {
    const cloned = new RegExp(re.source, re.flags);
    for (const m of str.matchAll(cloned)) {
      findings.push({
        rule,
        field: fieldName,
        detail: `${detail} in ${fieldName}`,
        match: m[0],
      });
    }
  }

  // 3. Configurable PRIVATE_LEAK_TERMS (from Worker secret env.PRIVATE_LEAK_TERMS)
  if (privateTerms && privateTerms.length > 0) {
    const lower = str.toLowerCase();
    for (const term of privateTerms) {
      const idx = lower.indexOf(term.toLowerCase());
      if (idx !== -1) {
        findings.push({
          rule: "private_leak_term",
          field: fieldName,
          detail: `Matched private blocklist term in ${fieldName}`,
          match: str.slice(idx, idx + term.length),
        });
      }
    }
  }

  return findings;
}

export async function llmLeakCheck(textBundle, env = {}) {
  const apiKey = env?.XAI_API_KEY;
  if (!apiKey || !env?.ENABLE_LLM_LEAK_CHECK) return [];
  const model = env?.XAI_MODEL || "grok-4.7";
  const endpoint = env?.XAI_API_URL || "https://api.x.ai/v1/chat/completions";
  const prompt =
    "You are a strict security & privacy leak auditor for the Ask Eve podcast. " +
    "Inspect the following outbound text and check whether it leaks any private information: " +
    "personal email addresses (other than public @askevepodcast.com or the intended recipient), " +
    "street addresses, phone numbers, API keys, passwords, internal UUIDs, local filesystem paths, " +
    "or private server/bucket names. " +
    'Respond ONLY with JSON: {"safe": true, "findings": []} or ' +
    '{"safe": false, "findings": [{"rule": "llm_privacy_flag", "detail": "...", "match": "..."}]}.';
  try {
    const resp = await fetch(endpoint, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${apiKey}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        model,
        temperature: 0,
        response_format: { type: "json_object" },
        messages: [
          { role: "system", content: prompt },
          { role: "user", content: textBundle },
        ],
      }),
    });
    if (!resp.ok) return [];
    const data = await resp.json();
    const content = data?.choices?.[0]?.message?.content;
    if (!content) return [];
    const parsed = JSON.parse(content);
    if (parsed && parsed.safe === false && Array.isArray(parsed.findings)) {
      return parsed.findings.map((f) => ({
        rule: String(f.rule || "llm_privacy_flag"),
        field: String(f.field || "body_text"),
        detail: String(f.detail || "Flagged by LLM privacy review"),
        match: String(f.match || ""),
      }));
    }
  } catch {
    // Deterministic checks are primary; ignore transient LLM errors.
  }
  return [];
}

export async function leakCheck(draft, env = {}) {
  const toAddr = String(draft?.to_addr ?? "");
  const subject = String(draft?.subject ?? "");
  const bodyText = String(draft?.body_text ?? "");
  const privateTerms = parsePrivateTerms(env?.PRIVATE_LEAK_TERMS);
  const recipientEmail = extractEmailAddress(toAddr);
  const findings = [];

  if (!recipientEmail || !recipientEmail.includes("@")) {
    findings.push({
      rule: "invalid_recipient",
      field: "to_addr",
      detail: "Recipient to_addr must contain a valid email address",
      match: toAddr,
    });
  }

  findings.push(
    ...scanTextForRules(toAddr, {
      fieldName: "to_addr",
      allowedRecipientEmail: recipientEmail,
      isRecipientField: true,
      privateTerms,
    })
  );
  findings.push(
    ...scanTextForRules(subject, {
      fieldName: "subject",
      allowedRecipientEmail: recipientEmail,
      isRecipientField: false,
      privateTerms,
    })
  );
  findings.push(
    ...scanTextForRules(bodyText, {
      fieldName: "body_text",
      allowedRecipientEmail: recipientEmail,
      isRecipientField: false,
      privateTerms,
    })
  );

  if (findings.length === 0 && env?.ENABLE_LLM_LEAK_CHECK && env?.XAI_API_KEY) {
    const llmFindings = await llmLeakCheck(
      `To: ${toAddr}\nSubject: ${subject}\n\n${bodyText}`,
      env
    );
    findings.push(...llmFindings);
  }

  const sha256 = await computeLeakCheckSha256(toAddr, subject, bodyText);
  const passed = findings.length === 0;
  return {
    passed,
    leak_check_passed: passed ? 1 : 0,
    findings,
    sha256,
    leak_check_sha256: sha256,
  };
}

export function leakCheckReadingItem(item, env = {}) {
  const privateTerms = parsePrivateTerms(env?.PRIVATE_LEAK_TERMS);
  const findings = [];
  const fields = [
    "title",
    "slug",
    "url",
    "author_or_source",
    "submitted_via",
    "eve_notes",
    "episode_slug",
  ];
  for (const field of fields) {
    const val = String(item?.[field] ?? "");
    if (!val) continue;
    findings.push(
      ...scanTextForRules(val, {
        fieldName: field,
        allowedRecipientEmail: "",
        isRecipientField: false,
        privateTerms,
      })
    );
  }
  const passed = findings.length === 0;
  return {
    passed,
    leak_check_passed: passed ? 1 : 0,
    findings,
  };
}
