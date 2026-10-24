// workers/eve-inbox/src/auth.mjs
// Cloudflare Access JWT verification + ADMIN_EMAIL defense-in-depth check.
// Fails closed if any required environment variable or Access header is missing/invalid.

const jwksCache = new Map();

function base64UrlToBytes(b64u) {
  const padded = String(b64u)
    .replace(/-/g, "+")
    .replace(/_/g, "/")
    .padEnd(Math.ceil(b64u.length / 4) * 4, "=");
  const bin = atob(padded);
  return Uint8Array.from(bin, (c) => c.charCodeAt(0));
}

function normalizeTeamDomain(raw) {
  const cleaned = String(raw || "")
    .trim()
    .replace(/^https?:\/\//i, "")
    .replace(/\/+$/, "");
  if (!cleaned) return "";
  return cleaned.includes(".") ? cleaned : `${cleaned}.cloudflareaccess.com`;
}

async function fetchJwksKeys(teamDomain, env) {
  if (Array.isArray(env?.JWKS_KEYS)) {
    return env.JWKS_KEYS;
  }
  const now = Date.now();
  const cached = jwksCache.get(teamDomain);
  if (cached && cached.expiresAt > now) {
    return cached.keys;
  }
  const url = `https://${teamDomain}/cdn-cgi/access/certs`;
  const resp = await fetch(url, {
    headers: { Accept: "application/json" },
  });
  if (!resp.ok) {
    throw new Error(`failed to fetch Cloudflare Access certs (${resp.status})`);
  }
  const data = await resp.json();
  const keys = Array.isArray(data?.keys) ? data.keys : [];
  jwksCache.set(teamDomain, { keys, expiresAt: now + 5 * 60 * 1000 });
  return keys;
}

export async function verifyAccessRequest(request, env) {
  const teamDomain = normalizeTeamDomain(env?.CF_ACCESS_TEAM_DOMAIN);
  const expectedAud = String(env?.CF_ACCESS_AUD || "").trim();
  const expectedEmail = String(env?.ADMIN_EMAIL || "").trim().toLowerCase();

  // Fail closed when Cloudflare Access / Admin config is absent
  if (!teamDomain || !expectedAud || !expectedEmail) {
    return {
      ok: false,
      status: 503,
      error: "admin authentication is not configured (fail-closed)",
    };
  }

  const jwt =
    request.headers.get("Cf-Access-Jwt-Assertion") ||
    request.headers.get("cf-access-jwt-assertion") ||
    "";
  if (!jwt) {
    return {
      ok: false,
      status: 401,
      error: "missing Cf-Access-Jwt-Assertion header",
    };
  }

  const headerEmail = (
    request.headers.get("Cf-Access-Authenticated-User-Email") ||
    request.headers.get("cf-access-authenticated-user-email") ||
    ""
  )
    .trim()
    .toLowerCase();

  if (!headerEmail || headerEmail !== expectedEmail) {
    return {
      ok: false,
      status: 403,
      error: "Cf-Access-Authenticated-User-Email does not match authorized admin",
    };
  }

  const parts = jwt.split(".");
  if (parts.length !== 3) {
    return {
      ok: false,
      status: 401,
      error: "malformed Cf-Access-Jwt-Assertion token",
    };
  }

  let header;
  let payload;
  try {
    header = JSON.parse(new TextDecoder().decode(base64UrlToBytes(parts[0])));
    payload = JSON.parse(new TextDecoder().decode(base64UrlToBytes(parts[1])));
  } catch {
    return {
      ok: false,
      status: 401,
      error: "invalid JWT encoding",
    };
  }

  if (header?.alg !== "RS256") {
    return {
      ok: false,
      status: 401,
      error: "unsupported JWT algorithm (expected RS256)",
    };
  }

  const nowSec = Math.floor(Date.now() / 1000);
  if (typeof payload?.exp !== "number" || payload.exp <= nowSec) {
    return {
      ok: false,
      status: 401,
      error: "Cf-Access-Jwt-Assertion token has expired",
    };
  }
  if (typeof payload?.nbf === "number" && payload.nbf > nowSec + 60) {
    return {
      ok: false,
      status: 401,
      error: "Cf-Access-Jwt-Assertion token is not yet valid",
    };
  }

  const expectedIssuer = `https://${teamDomain}`;
  if (String(payload?.iss || "").replace(/\/+$/, "") !== expectedIssuer) {
    return {
      ok: false,
      status: 401,
      error: "JWT issuer mismatch",
    };
  }

  const audList = Array.isArray(payload?.aud) ? payload.aud : [payload?.aud];
  if (!audList.includes(expectedAud)) {
    return {
      ok: false,
      status: 401,
      error: "JWT audience (aud) mismatch",
    };
  }

  const jwtEmail = String(payload?.email || "").trim().toLowerCase();
  if (!jwtEmail || jwtEmail !== expectedEmail) {
    return {
      ok: false,
      status: 403,
      error: "JWT email claim does not match authorized admin",
    };
  }

  // Verify RS256 cryptographic signature against Cloudflare Access JWKS
  let keys;
  try {
    keys = await fetchJwksKeys(teamDomain, env);
  } catch (err) {
    return {
      ok: false,
      status: 503,
      error: err.message || "unable to load Cloudflare Access JWKS",
    };
  }

  const candidateKeys = header.kid
    ? keys.filter((k) => k.kid === header.kid)
    : keys;
  if (!candidateKeys.length) {
    return {
      ok: false,
      status: 401,
      error: "no matching JWKS key id (kid) found",
    };
  }

  const signedBytes = new TextEncoder().encode(`${parts[0]}.${parts[1]}`);
  const sigBytes = base64UrlToBytes(parts[2]);

  let verified = false;
  for (const jwk of candidateKeys) {
    try {
      const cryptoKey = await crypto.subtle.importKey(
        "jwk",
        jwk,
        { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" },
        false,
        ["verify"]
      );
      const ok = await crypto.subtle.verify(
        "RSASSA-PKCS1-v1_5",
        cryptoKey,
        sigBytes,
        signedBytes
      );
      if (ok) {
        verified = true;
        break;
      }
    } catch {
      // try next key
    }
  }

  if (!verified) {
    return {
      ok: false,
      status: 401,
      error: "invalid Cf-Access-Jwt-Assertion signature",
    };
  }

  return {
    ok: true,
    email: jwtEmail,
    sub: payload.sub || "",
  };
}
