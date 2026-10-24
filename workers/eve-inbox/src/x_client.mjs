// workers/eve-inbox/src/x_client.mjs
// X (@askevepodcast) API v2 client supporting both:
//   1. OAuth 2.0 Authorization Code with PKCE (/admin/x/oauth/start -> /admin/x/oauth/callback, stored in D1 x_auth_tokens)
//   2. OAuth 1.0a HMAC-SHA1 user context via Worker secrets (X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_TOKEN_SECRET)

function base64UrlEncode(bytes) {
  let binary = "";
  for (let i = 0; i < bytes.length; i++) {
    binary += String.fromCharCode(bytes[i]);
  }
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function randomBase64Url(byteLen = 32) {
  const bytes = new Uint8Array(byteLen);
  crypto.getRandomValues(bytes);
  return base64UrlEncode(bytes);
}

async function sha256Base64Url(str) {
  const bytes = new TextEncoder().encode(str);
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return base64UrlEncode(new Uint8Array(digest));
}

function rfc3986Encode(str) {
  return encodeURIComponent(String(str)).replace(
    /[!'()*]/g,
    (c) => `%${c.charCodeAt(0).toString(16).toUpperCase()}`
  );
}

export async function buildOAuth1AuthorizationHeader({
  method,
  url,
  apiKey,
  apiSecret,
  accessToken,
  accessTokenSecret,
}) {
  const parsedUrl = new URL(url);
  const baseUrl = `${parsedUrl.origin}${parsedUrl.pathname}`;
  const oauthParams = {
    oauth_consumer_key: apiKey,
    oauth_nonce: randomBase64Url(18).replace(/[^A-Za-z0-9]/g, ""),
    oauth_signature_method: "HMAC-SHA1",
    oauth_timestamp: String(Math.floor(Date.now() / 1000)),
    oauth_token: accessToken,
    oauth_version: "1.0",
  };

  const allParams = [];
  for (const [k, v] of parsedUrl.searchParams.entries()) {
    allParams.push([rfc3986Encode(k), rfc3986Encode(v)]);
  }
  for (const [k, v] of Object.entries(oauthParams)) {
    allParams.push([rfc3986Encode(k), rfc3986Encode(v)]);
  }
  allParams.sort((a, b) => (a[0] === b[0] ? a[1].localeCompare(b[1]) : a[0].localeCompare(b[0])));

  const normalizedParams = allParams.map(([k, v]) => `${k}=${v}`).join("&");
  const signatureBase = [
    String(method).toUpperCase(),
    rfc3986Encode(baseUrl),
    rfc3986Encode(normalizedParams),
  ].join("&");

  const signingKey = `${rfc3986Encode(apiSecret)}&${rfc3986Encode(accessTokenSecret)}`;
  const cryptoKey = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(signingKey),
    { name: "HMAC", hash: "SHA-1" },
    false,
    ["sign"]
  );
  const sigBuf = await crypto.subtle.sign(
    "HMAC",
    cryptoKey,
    new TextEncoder().encode(signatureBase)
  );
  const sigBytes = new Uint8Array(sigBuf);
  let binary = "";
  for (let i = 0; i < sigBytes.length; i++) binary += String.fromCharCode(sigBytes[i]);
  oauthParams.oauth_signature = btoa(binary);

  const headerParts = Object.keys(oauthParams)
    .sort()
    .map((k) => `${rfc3986Encode(k)}="${rfc3986Encode(oauthParams[k])}"`);
  return `OAuth ${headerParts.join(", ")}`;
}

export async function getXConnectionStatus(env) {
  let d1TokenRow = null;
  try {
    d1TokenRow = await env?.DB?.prepare(
      `SELECT account_handle, expires_at, scope, updated_at FROM x_auth_tokens WHERE account_handle = '@askevepodcast' LIMIT 1`
    ).first();
  } catch {
    // Table may not exist in older test schemas
  }
  if (d1TokenRow) {
    return {
      connected: true,
      mode: "oauth2_pkce",
      account_handle: d1TokenRow.account_handle || "@askevepodcast",
      updated_at: d1TokenRow.updated_at,
    };
  }
  if (
    env?.X_API_KEY &&
    env?.X_API_SECRET &&
    env?.X_ACCESS_TOKEN &&
    env?.X_ACCESS_TOKEN_SECRET
  ) {
    return {
      connected: true,
      mode: "oauth1_env",
      account_handle: "@askevepodcast",
    };
  }
  if (env?.X_OAUTH2_ACCESS_TOKEN) {
    return {
      connected: true,
      mode: "oauth2_env",
      account_handle: "@askevepodcast",
    };
  }
  return {
    connected: false,
    mode: "unconfigured",
    account_handle: "@askevepodcast",
    oauth_start_path: "/admin/x/oauth/start",
  };
}

export async function startXOAuth2Pkce(request, env) {
  const clientId = env?.X_CLIENT_ID;
  if (!clientId) {
    return {
      ok: false,
      status: 503,
      error: "x_client_id_missing",
      detail: "Set Worker secret X_CLIENT_ID (and X_CLIENT_SECRET) or configure OAuth 1.0a secrets first.",
    };
  }
  const url = new URL(request.url);
  const redirectUri =
    env?.X_REDIRECT_URI || `${url.origin}/admin/x/oauth/callback`;
  const state = randomBase64Url(24);
  const codeVerifier = randomBase64Url(48);
  const codeChallenge = await sha256Base64Url(codeVerifier);
  const nowIso = new Date().toISOString();

  await env.DB.prepare(
    `INSERT INTO x_oauth_states (state, code_verifier, created_at) VALUES (?1, ?2, ?3)`
  )
    .bind(state, codeVerifier, nowIso)
    .run();

  const authUrl = new URL("https://x.com/i/oauth2/authorize");
  authUrl.searchParams.set("response_type", "code");
  authUrl.searchParams.set("client_id", clientId);
  authUrl.searchParams.set("redirect_uri", redirectUri);
  authUrl.searchParams.set(
    "scope",
    "tweet.read tweet.write users.read offline.access"
  );
  authUrl.searchParams.set("state", state);
  authUrl.searchParams.set("code_challenge", codeChallenge);
  authUrl.searchParams.set("code_challenge_method", "S256");

  return {
    ok: true,
    authorize_url: authUrl.toString(),
    state,
  };
}

export async function completeXOAuth2Callback(request, env) {
  const url = new URL(request.url);
  const code = url.searchParams.get("code") || "";
  const state = url.searchParams.get("state") || "";
  if (!code || !state) {
    return { ok: false, status: 400, error: "missing_code_or_state" };
  }

  const stateRow = await env.DB.prepare(
    `SELECT state, code_verifier FROM x_oauth_states WHERE state = ?1`
  )
    .bind(state)
    .first();
  if (!stateRow) {
    return { ok: false, status: 400, error: "invalid_or_expired_oauth_state" };
  }

  await env.DB.prepare(`DELETE FROM x_oauth_states WHERE state = ?1`)
    .bind(state)
    .run();

  const clientId = env?.X_CLIENT_ID || "";
  const clientSecret = env?.X_CLIENT_SECRET || "";
  const redirectUri =
    env?.X_REDIRECT_URI || `${url.origin}/admin/x/oauth/callback`;
  const tokenEndpoint =
    env?.X_OAUTH_TOKEN_URL || "https://api.x.com/2/oauth2/token";

  const headers = {
    "Content-Type": "application/x-www-form-urlencoded",
  };
  if (clientSecret) {
    headers.Authorization = `Basic ${btoa(`${clientId}:${clientSecret}`)}`;
  }

  const body = new URLSearchParams({
    code,
    grant_type: "authorization_code",
    client_id: clientId,
    redirect_uri: redirectUri,
    code_verifier: stateRow.code_verifier,
  });

  const resp = await fetch(tokenEndpoint, {
    method: "POST",
    headers,
    body: body.toString(),
  });
  if (!resp.ok) {
    const errText = await resp.text().catch(() => "");
    return {
      ok: false,
      status: 502,
      error: "x_token_exchange_failed",
      detail: errText.slice(0, 300),
    };
  }

  const tokenData = await resp.json();
  const now = Date.now();
  const expiresAt = tokenData.expires_in
    ? new Date(now + Number(tokenData.expires_in) * 1000).toISOString()
    : null;
  const nowIso = new Date(now).toISOString();

  await env.DB.prepare(
    `INSERT INTO x_auth_tokens (account_handle, access_token, refresh_token, expires_at, scope, updated_at)
     VALUES ('@askevepodcast', ?1, ?2, ?3, ?4, ?5)
     ON CONFLICT(account_handle) DO UPDATE SET
       access_token = excluded.access_token,
       refresh_token = COALESCE(excluded.refresh_token, x_auth_tokens.refresh_token),
       expires_at = excluded.expires_at,
       scope = excluded.scope,
       updated_at = excluded.updated_at`
  )
    .bind(
      tokenData.access_token,
      tokenData.refresh_token || null,
      expiresAt,
      tokenData.scope || "",
      nowIso
    )
    .run();

  return { ok: true, account_handle: "@askevepodcast", expires_at: expiresAt };
}

async function getXAuthHeader({ method, url, env }) {
  // 1. Check D1 x_auth_tokens first (OAuth 2.0 PKCE)
  try {
    const row = await env?.DB?.prepare(
      `SELECT access_token, refresh_token, expires_at FROM x_auth_tokens WHERE account_handle = '@askevepodcast' LIMIT 1`
    ).first();
    if (row?.access_token) {
      return `Bearer ${row.access_token}`;
    }
  } catch {
    // ignore if table not present
  }

  // 2. Check OAuth 2.0 access token env
  if (env?.X_OAUTH2_ACCESS_TOKEN) {
    return `Bearer ${env.X_OAUTH2_ACCESS_TOKEN}`;
  }

  // 3. Check OAuth 1.0a env secrets
  if (
    env?.X_API_KEY &&
    env?.X_API_SECRET &&
    env?.X_ACCESS_TOKEN &&
    env?.X_ACCESS_TOKEN_SECRET
  ) {
    return buildOAuth1AuthorizationHeader({
      method,
      url,
      apiKey: env.X_API_KEY,
      apiSecret: env.X_API_SECRET,
      accessToken: env.X_ACCESS_TOKEN,
      accessTokenSecret: env.X_ACCESS_TOKEN_SECRET,
    });
  }

  // 4. For read-only search requests, allow X_BEARER_TOKEN
  if (method === "GET" && env?.X_BEARER_TOKEN) {
    return `Bearer ${env.X_BEARER_TOKEN}`;
  }

  return null;
}

export async function postTweetToX({ text, replyToTweetId = null }, env) {
  if (typeof env?.X_POST_TWEET === "function") {
    return env.X_POST_TWEET({ text, replyToTweetId });
  }
  const endpoint = env?.X_TWEETS_API_URL || "https://api.x.com/2/tweets";
  const authHeader = await getXAuthHeader({ method: "POST", url: endpoint, env });
  if (!authHeader) {
    return {
      ok: false,
      status: 503,
      error: "x_auth_not_configured",
      detail:
        "Connect @askevepodcast via /admin/x/oauth/start or set X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_TOKEN_SECRET.",
    };
  }

  const payload = { text: String(text) };
  if (replyToTweetId) {
    payload.reply = { in_reply_to_tweet_id: String(replyToTweetId) };
  }

  const resp = await fetch(endpoint, {
    method: "POST",
    headers: {
      Authorization: authHeader,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });

  const data = await resp.json().catch(() => ({}));
  if (!resp.ok || !data?.data?.id) {
    return {
      ok: false,
      status: resp.status || 502,
      error: "x_post_failed",
      detail: data?.detail || data?.title || JSON.stringify(data).slice(0, 300),
    };
  }

  return {
    ok: true,
    tweet_id: String(data.data.id),
    text: String(data.data.text || text),
  };
}

export async function fetchRecentXMentions(env) {
  if (typeof env?.X_FETCH_MENTIONS === "function") {
    return env.X_FETCH_MENTIONS();
  }
  const baseUrl =
    env?.X_SEARCH_API_URL || "https://api.x.com/2/tweets/search/recent";
  const searchUrl = new URL(baseUrl);
  searchUrl.searchParams.set("query", "@askevepodcast -from:askevepodcast");
  searchUrl.searchParams.set(
    "tweet.fields",
    "created_at,author_id,conversation_id,entities,in_reply_to_user_id"
  );
  searchUrl.searchParams.set("expansions", "author_id");
  searchUrl.searchParams.set("user.fields", "username,name");
  searchUrl.searchParams.set("max_results", "20");

  const fullUrl = searchUrl.toString();
  const authHeader = await getXAuthHeader({ method: "GET", url: fullUrl, env });
  if (!authHeader) {
    return {
      ok: false,
      status: 503,
      error: "x_auth_not_configured",
      mentions: [],
    };
  }

  const resp = await fetch(fullUrl, {
    method: "GET",
    headers: { Authorization: authHeader },
  });
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    return {
      ok: false,
      status: resp.status || 502,
      error: "x_mentions_fetch_failed",
      detail: data?.detail || data?.title || "",
      mentions: [],
    };
  }

  const usersById = new Map();
  for (const u of data?.includes?.users || []) {
    usersById.set(String(u.id), u);
  }

  const mentions = (data?.data || []).map((t) => {
    const u = usersById.get(String(t.author_id)) || {};
    const handle = u.username ? `@${u.username.replace(/^@/, "")}` : `@user_${t.author_id || "x"}`;
    const urls = (t?.entities?.urls || [])
      .map((uObj) => uObj.expanded_url || uObj.url)
      .filter((uStr) => uStr && !uStr.includes("twitter.com/") && !uStr.includes("x.com/"));
    return {
      tweet_id: String(t.id),
      author_handle: handle,
      author_name: u.name || handle,
      text: String(t.text || ""),
      created_at: t.created_at || new Date().toISOString(),
      extracted_urls: urls,
    };
  });

  return { ok: true, mentions };
}
