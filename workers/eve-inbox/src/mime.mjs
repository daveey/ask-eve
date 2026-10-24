// workers/eve-inbox/src/mime.mjs
// Zero-dependency RFC 5322 / MIME parser & outbound reply builder for Cloudflare Email Workers.

export async function rawStreamToBytes(raw) {
  if (!raw) return new Uint8Array(0);
  if (raw instanceof Uint8Array) return raw;
  if (raw instanceof ArrayBuffer) return new Uint8Array(raw);
  if (typeof raw === "string") return new TextEncoder().encode(raw);
  if (typeof raw.getReader === "function") {
    const reader = raw.getReader();
    const chunks = [];
    let total = 0;
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      const chunk = value instanceof Uint8Array ? value : new Uint8Array(value);
      chunks.push(chunk);
      total += chunk.byteLength;
    }
    const merged = new Uint8Array(total);
    let offset = 0;
    for (const c of chunks) {
      merged.set(c, offset);
      offset += c.byteLength;
    }
    return merged;
  }
  return new TextEncoder().encode(String(raw));
}

export function decodeRfc2047(str) {
  if (!str) return "";
  return String(str).replace(
    /=\?([^?]+)\?([BbQq])\?([^?]*)\?=/g,
    (_, _charset, encoding, payload) => {
      try {
        if (encoding.toUpperCase() === "B") {
          const bin = atob(payload.replace(/\s+/g, ""));
          const bytes = Uint8Array.from(bin, (c) => c.charCodeAt(0));
          return new TextDecoder("utf-8", { fatal: false }).decode(bytes);
        }
        const q = payload.replace(/_/g, " ");
        const bytes = [];
        for (let i = 0; i < q.length; i++) {
          if (q[i] === "=" && /^[0-9A-Fa-f]{2}$/.test(q.slice(i + 1, i + 3))) {
            bytes.push(parseInt(q.slice(i + 1, i + 3), 16));
            i += 2;
          } else {
            bytes.push(q.charCodeAt(i));
          }
        }
        return new TextDecoder("utf-8", { fatal: false }).decode(new Uint8Array(bytes));
      } catch {
        return payload;
      }
    }
  );
}

export function parseHeaders(headerBlock) {
  const headers = new Map();
  if (!headerBlock) return headers;
  const unfolded = String(headerBlock).replace(/\r?\n([ \t]+)/g, " ");
  for (const line of unfolded.split(/\r?\n/)) {
    const idx = line.indexOf(":");
    if (idx <= 0) continue;
    const key = line.slice(0, idx).trim().toLowerCase();
    const val = decodeRfc2047(line.slice(idx + 1).trim());
    headers.set(key, val);
  }
  return headers;
}

export function parseAddressHeader(raw) {
  if (!raw) return { name: "", address: "" };
  const decoded = decodeRfc2047(raw).trim();
  const angle = decoded.match(/^(.*?)\s*<([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})>/);
  if (angle) {
    const name = angle[1].replace(/^["']|["']$/g, "").trim();
    return { name, address: angle[2].toLowerCase() };
  }
  const bare = decoded.match(/([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})/);
  if (bare) {
    return { name: "", address: bare[1].toLowerCase() };
  }
  return { name: "", address: decoded.toLowerCase() };
}

export function decodeTransferEncoding(bodyStr, transferEncoding) {
  const enc = String(transferEncoding || "").trim().toLowerCase();
  if (enc === "base64") {
    try {
      const cleaned = bodyStr.replace(/[^A-Za-z0-9+/=]/g, "");
      const bin = atob(cleaned);
      return Uint8Array.from(bin, (c) => c.charCodeAt(0));
    } catch {
      return new TextEncoder().encode(bodyStr);
    }
  }
  if (enc === "quoted-printable") {
    const softStripped = bodyStr.replace(/=\r?\n/g, "");
    const bytes = [];
    for (let i = 0; i < softStripped.length; i++) {
      if (
        softStripped[i] === "=" &&
        /^[0-9A-Fa-f]{2}$/.test(softStripped.slice(i + 1, i + 3))
      ) {
        bytes.push(parseInt(softStripped.slice(i + 1, i + 3), 16));
        i += 2;
      } else {
        bytes.push(softStripped.charCodeAt(i));
      }
    }
    return new Uint8Array(bytes);
  }
  return new TextEncoder().encode(bodyStr);
}

export function stripHtmlToText(html) {
  if (!html) return "";
  return String(html)
    .replace(/<style[\s\S]*?<\/style>/gi, " ")
    .replace(/<script[\s\S]*?<\/script>/gi, " ")
    .replace(/<br\s*\/?>/gi, "\n")
    .replace(/<\/(p|div|li|h[1-6]|blockquote|tr)>/gi, "\n")
    .replace(/<[^>]+>/g, " ")
    .replace(/&nbsp;/gi, " ")
    .replace(/&amp;/gi, "&")
    .replace(/&lt;/gi, "<")
    .replace(/&gt;/gi, ">")
    .replace(/&quot;/gi, '"')
    .replace(/&#39;/gi, "'")
    .replace(/[ \t]+\n/g, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

export function extractPdfTextBasic(bytes) {
  try {
    const raw = new TextDecoder("latin1").decode(bytes);
    const chunks = [];
    for (const m of raw.matchAll(/\(([^()\\]{3,200})\)\s*Tj/g)) {
      chunks.push(m[1]);
    }
    if (chunks.length > 0) return chunks.join(" ").slice(0, 16000);
  } catch {
    // ignore
  }
  return "";
}

function getHeaderParam(headerVal, paramName) {
  if (!headerVal) return "";
  const re = new RegExp(`${paramName}\\s*=\\s*(?:"([^"]+)"|([^;\\s]+))`, "i");
  const m = String(headerVal).match(re);
  return m ? decodeRfc2047(m[1] || m[2] || "").trim() : "";
}

function splitHeaderAndBody(rawPart) {
  const crlfIdx = rawPart.indexOf("\r\n\r\n");
  const lfIdx = rawPart.indexOf("\n\n");
  if (crlfIdx !== -1 && (lfIdx === -1 || crlfIdx < lfIdx)) {
    return [rawPart.slice(0, crlfIdx), rawPart.slice(crlfIdx + 4)];
  }
  if (lfIdx !== -1) {
    return [rawPart.slice(0, lfIdx), rawPart.slice(lfIdx + 2)];
  }
  return [rawPart, ""];
}

function walkMimePart(rawPart, accum) {
  const [headerBlock, bodyBlock] = splitHeaderAndBody(rawPart);
  const headers = parseHeaders(headerBlock);
  const contentTypeHeader = headers.get("content-type") || "text/plain; charset=utf-8";
  const contentType = contentTypeHeader.split(";")[0].trim().toLowerCase();
  const disposition = (headers.get("content-disposition") || "").toLowerCase();
  const transferEncoding = headers.get("content-transfer-encoding") || "";

  if (contentType.startsWith("multipart/")) {
    const boundary = getHeaderParam(contentTypeHeader, "boundary");
    if (!boundary) return;
    const delim = `--${boundary}`;
    const segments = bodyBlock.split(delim);
    for (let i = 1; i < segments.length; i++) {
      const seg = segments[i];
      if (seg.startsWith("--")) break;
      const trimmed = seg.replace(/^\r?\n/, "").replace(/\r?\n$/, "");
      if (trimmed) walkMimePart(trimmed, accum);
    }
    return;
  }

  const filename =
    getHeaderParam(headers.get("content-disposition"), "filename") ||
    getHeaderParam(contentTypeHeader, "name");
  const isAttachment =
    disposition.includes("attachment") ||
    Boolean(filename) ||
    (!contentType.startsWith("text/plain") && !contentType.startsWith("text/html"));

  const decodedBytes = decodeTransferEncoding(bodyBlock, transferEncoding);

  if (!isAttachment && contentType === "text/plain") {
    const text = new TextDecoder("utf-8", { fatal: false }).decode(decodedBytes).trim();
    if (text) accum.textParts.push(text);
    return;
  }

  if (!isAttachment && contentType === "text/html") {
    const html = new TextDecoder("utf-8", { fatal: false }).decode(decodedBytes).trim();
    if (html) accum.htmlParts.push(html);
    return;
  }

  const safeFilename = (filename || `attachment-${accum.attachments.length + 1}`)
    .replace(/[^A-Za-z0-9._-]/g, "_")
    .slice(0, 120);

  let extractedText = "";
  if (
    contentType.startsWith("text/") ||
    safeFilename.endsWith(".md") ||
    safeFilename.endsWith(".txt")
  ) {
    extractedText = new TextDecoder("utf-8", { fatal: false })
      .decode(decodedBytes)
      .slice(0, 24000);
    if (contentType === "text/html") {
      extractedText = stripHtmlToText(extractedText);
    }
  } else if (contentType === "application/pdf" || safeFilename.endsWith(".pdf")) {
    extractedText = extractPdfTextBasic(decodedBytes);
  }

  accum.attachments.push({
    filename: safeFilename,
    content_type: contentType || "application/octet-stream",
    bytes: decodedBytes.byteLength,
    data: decodedBytes,
    extracted_text: extractedText,
  });
}

export function extractUrls(text) {
  if (!text) return [];
  const seen = new Set();
  const out = [];
  for (const m of String(text).matchAll(/https?:\/\/[^\s"'<>)\]]+/gi)) {
    const cleaned = m[0].replace(/[.,;:!?)]+$/, "");
    if (!seen.has(cleaned)) {
      seen.add(cleaned);
      out.push(cleaned);
    }
  }
  return out;
}

export async function parseInboundEmail(rawInput, envelope = {}) {
  const rawBytes = await rawStreamToBytes(rawInput);
  const rawString = new TextDecoder("utf-8", { fatal: false }).decode(rawBytes);
  const [topHeaderBlock] = splitHeaderAndBody(rawString);
  const topHeaders = parseHeaders(topHeaderBlock);

  const parsedFrom = parseAddressHeader(topHeaders.get("from") || envelope.from || "");
  const parsedTo = parseAddressHeader(topHeaders.get("to") || envelope.to || "");

  const accum = {
    textParts: [],
    htmlParts: [],
    attachments: [],
  };
  walkMimePart(rawString, accum);

  const htmlBody = accum.htmlParts.join("\n\n");
  const textBody =
    accum.textParts.join("\n\n") || (htmlBody ? stripHtmlToText(htmlBody) : "");
  const urls = extractUrls([textBody, stripHtmlToText(htmlBody)].join("\n"));

  return {
    rawBytes,
    from_addr: (envelope.from || parsedFrom.address || "").toLowerCase(),
    from_name: parsedFrom.name || "",
    to_addr: (envelope.to || parsedTo.address || "eve@askevepodcast.com").toLowerCase(),
    subject: topHeaders.get("subject") || "(no subject)",
    message_id: topHeaders.get("message-id") || "",
    date: topHeaders.get("date") || new Date().toISOString(),
    text_body: textBody,
    html_body: htmlBody,
    extracted_urls: urls,
    attachments: accum.attachments,
  };
}

export function buildReplyMime({
  fromAddr = "eve@askevepodcast.com",
  fromName = "Eve — Ask Eve Podcast",
  toAddr,
  subject,
  bodyText,
  inReplyTo = "",
  references = "",
  messageId = "",
}) {
  const msgId =
    messageId ||
    `<eve-reply-${Date.now()}-${Math.random().toString(36).slice(2, 10)}@askevepodcast.com>`;
  const dateHeader = new Date().toUTCString();
  const safeSubject = String(subject || "").replace(/[\r\n]+/g, " ").trim();
  const safeTo = String(toAddr || "").replace(/[\r\n]+/g, "").trim();

  const lines = [
    `From: "${fromName.replace(/"/g, "")}" <${fromAddr}>`,
    `To: ${safeTo}`,
    `Subject: ${safeSubject}`,
    `Date: ${dateHeader}`,
    `Message-ID: ${msgId}`,
  ];
  if (inReplyTo) {
    const cleanReplyTo = String(inReplyTo).replace(/[\r\n]+/g, " ").trim();
    lines.push(`In-Reply-To: ${cleanReplyTo}`);
    lines.push(`References: ${references ? String(references).trim() : cleanReplyTo}`);
  }
  lines.push(
    "MIME-Version: 1.0",
    "Content-Type: text/plain; charset=UTF-8",
    "Content-Transfer-Encoding: 8bit",
    "",
    String(bodyText || "").replace(/\r?\n/g, "\r\n"),
    ""
  );
  return {
    messageId: msgId,
    rawMime: lines.join("\r\n"),
  };
}
