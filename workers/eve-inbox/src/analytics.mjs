/**
 * workers/eve-inbox/src/analytics.mjs
 *
 * First-party, open-source PostHog-compatible analytics ingestion & public metrics API
 * for askevepodcast.com.
 *
 * Key Guarantees:
 *   1. Raw event ownership: Every event in our allowlisted taxonomy is validated and stored
 *      directly in our own Cloudflare D1 `analytics_events` table (`migrations/0004_analytics_events.sql`).
 *   2. Zero PII / Cookieless: Client IPs (`CF-Connecting-IP`, `X-Forwarded-For`), User-Agents,
 *      and cookies are never persisted. Ephemeral session IDs stay in page memory (`persistence: "memory"`).
 *   3. Dual-mode PostHog upstream: When `env.POSTHOG_UPSTREAM_HOST` is configured (either
 *      self-hosted `https://ph.askevepodcast.com` from `infra/posthog/docker-compose.yml` or
 *      PostHog Cloud free tier `https://us.i.posthog.com`), events are dual-forwarded with `$ip: null`.
 */

export const ALLOWED_ANALYTICS_EVENTS = new Set([
  "$pageview",
  "outbound_social_click",
  "donation_click",
  "rss_feed_click",
  "audio_play",
  "source_receipt_click",
  "rss_feed_fetch",
]);

export const KNOWN_LOCALES = new Set([
  "en",
  "es",
  "zh",
  "ru",
  "de",
  "pt",
  "fa",
  "fr",
]);

export const CANONICAL_SECTIONS = new Set([
  "home",
  "episodes",
  "people",
  "topics",
  "ledger",
  "reports",
  "guide",
  "growth",
  "metrics",
  "eve",
  "contact",
  "about",
  "feed",
  "other",
]);

/**
 * Normalize any raw path (including localized `/<lang>/...`) into:
 *   { canonical_path, raw_path, locale, section, episode_slug }
 */
export function normalizePathMetadata(rawPathInput) {
  const raw = String(rawPathInput || "/").split("?")[0].split("#")[0].trim() || "/";
  const cleanRaw = raw.startsWith("/") ? raw : `/${raw}`;

  const segments = cleanRaw.split("/").filter(Boolean);
  let locale = "en";
  let restSegments = segments;

  if (segments.length > 0 && KNOWN_LOCALES.has(segments[0].toLowerCase())) {
    locale = segments[0].toLowerCase();
    restSegments = segments.slice(1);
  }

  if (restSegments.length === 0) {
    return {
      canonical_path: "/",
      raw_path: cleanRaw,
      locale,
      section: "home",
      episode_slug: null,
    };
  }

  const first = restSegments[0].toLowerCase();
  if (first === "feed.xml") {
    return {
      canonical_path: "/feed.xml",
      raw_path: cleanRaw,
      locale,
      section: "feed",
      episode_slug: null,
    };
  }

  if (first === "episodes") {
    const epSlug = restSegments.length > 1 ? restSegments[1] : null;
    return {
      canonical_path: "/episodes/",
      raw_path: cleanRaw,
      locale,
      section: "episodes",
      episode_slug: epSlug,
    };
  }

  if (CANONICAL_SECTIONS.has(first)) {
    return {
      canonical_path: `/${first}/`,
      raw_path: cleanRaw,
      locale,
      section: first,
      episode_slug: null,
    };
  }

  return {
    canonical_path: cleanRaw.endsWith("/") ? cleanRaw : `${cleanRaw}/`,
    raw_path: cleanRaw,
    locale,
    section: "other",
    episode_slug: null,
  };
}

/**
 * Sanitize and normalize a single PostHog wire event payload.
 * Returns null if the event is not in ALLOWED_ANALYTICS_EVENTS.
 */
export function sanitizePostHogEvent(item) {
  if (!item || typeof item !== "object") return null;
  const event = String(item.event || "").trim();
  if (!ALLOWED_ANALYTICS_EVENTS.has(event)) {
    return null;
  }

  const props =
    item.properties && typeof item.properties === "object" ? item.properties : {};
  const rawPath =
    props.raw_path ||
    props.$pathname ||
    props.path ||
    props.source_path ||
    "/";
  const pathMeta = normalizePathMetadata(rawPath);

  const canonicalPath =
    typeof props.canonical_path === "string" && props.canonical_path.startsWith("/")
      ? props.canonical_path
      : pathMeta.canonical_path;
  const locale =
    typeof props.locale === "string" && KNOWN_LOCALES.has(props.locale)
      ? props.locale
      : pathMeta.locale;
  const section =
    typeof props.section === "string" && CANONICAL_SECTIONS.has(props.section)
      ? props.section
      : pathMeta.section;
  const episodeSlug =
    typeof props.episode_slug === "string" && props.episode_slug.trim()
      ? props.episode_slug.trim().slice(0, 120)
      : pathMeta.episode_slug;

  const platform =
    typeof props.platform === "string" && props.platform.trim()
      ? props.platform.trim().toLowerCase().slice(0, 32)
      : null;
  const donationMethod =
    typeof props.method === "string" && props.method.trim()
      ? props.method.trim().toLowerCase().slice(0, 32)
      : typeof props.donation_method === "string" && props.donation_method.trim()
        ? props.donation_method.trim().toLowerCase().slice(0, 32)
        : null;
  const targetUrl =
    typeof props.target_url === "string" && props.target_url.trim()
      ? props.target_url.trim().slice(0, 300)
      : null;
  const referrerDomain =
    typeof props.referrer_domain === "string" && props.referrer_domain.trim()
      ? props.referrer_domain.trim().toLowerCase().slice(0, 120)
      : typeof props.$referring_domain === "string" && props.$referring_domain.trim()
        ? props.$referring_domain.trim().toLowerCase().slice(0, 120)
        : "direct";

  const distinctIdRaw =
    item.distinct_id || props.distinct_id || "anon_memory";
  const distinctId = String(distinctIdRaw).replace(/[^A-Za-z0-9_.-]/g, "").slice(0, 48) || "anon_memory";

  // Build clean properties JSON with zero PII ($ip explicitly null)
  const cleanProps = {
    canonical_path: canonicalPath,
    raw_path: pathMeta.raw_path,
    locale,
    section,
    ...(episodeSlug ? { episode_slug: episodeSlug } : {}),
    ...(props.clip_id ? { clip_id: String(props.clip_id).slice(0, 80) } : {}),
    ...(props.source_id ? { source_id: String(props.source_id).slice(0, 80) } : {}),
    ...(platform ? { platform } : {}),
    ...(donationMethod ? { method: donationMethod } : {}),
    ...(targetUrl ? { target_url: targetUrl } : {}),
    referrer_domain: referrerDomain,
    $ip: null,
  };

  const nowIso = new Date().toISOString();
  const id = `ev_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 10)}`;

  return {
    id,
    event,
    distinct_id: distinctId,
    canonical_path: canonicalPath,
    raw_path: pathMeta.raw_path,
    locale,
    section,
    episode_slug: episodeSlug,
    platform,
    donation_method: donationMethod,
    target_url: targetUrl,
    referrer_domain: referrerDomain,
    properties_json: JSON.stringify(cleanProps),
    clean_properties: cleanProps,
    created_at: nowIso,
  };
}

/**
 * Persist sanitized PostHog events to D1 `analytics_events` table.
 */
export async function persistEventsToD1(events, env) {
  if (!env?.DB || !Array.isArray(events) || events.length === 0) {
    return 0;
  }
  let stored = 0;
  for (const ev of events) {
    try {
      await env.DB.prepare(
        `INSERT INTO analytics_events (
          id, event, distinct_id, canonical_path, raw_path, locale, section,
          episode_slug, platform, donation_method, target_url, referrer_domain,
          properties_json, created_at
        ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14)`
      )
        .bind(
          ev.id,
          ev.event,
          ev.distinct_id,
          ev.canonical_path,
          ev.raw_path,
          ev.locale,
          ev.section,
          ev.episode_slug,
          ev.platform,
          ev.donation_method,
          ev.target_url,
          ev.referrer_domain,
          ev.properties_json,
          ev.created_at
        )
        .run();
      stored += 1;
    } catch {
      // Table may not be migrated yet in test environments without 0004
    }
  }
  return stored;
}

/**
 * Optionally dual-forward sanitized events to a self-hosted PostHog instance
 * (`env.POSTHOG_UPSTREAM_HOST`, e.g. `https://ph.askevepodcast.com` or `https://us.i.posthog.com`).
 */
export async function forwardEventsToPostHogUpstream(events, env) {
  const upstreamHost = String(env?.POSTHOG_UPSTREAM_HOST || "").trim().replace(/\/+$/, "");
  const apiKey = String(env?.POSTHOG_PROJECT_API_KEY || "").trim();
  if (!upstreamHost || !apiKey || !Array.isArray(events) || events.length === 0) {
    return false;
  }
  const batchPayload = {
    api_key: apiKey,
    batch: events.map((ev) => ({
      event: ev.event,
      distinct_id: ev.distinct_id,
      timestamp: ev.created_at,
      properties: {
        ...ev.clean_properties,
        $lib: "askeve-posthog-firstparty",
        $ip: null,
      },
    })),
  };
  try {
    const resp = await fetch(`${upstreamHost}/batch/`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(batchPayload),
    });
    return resp.ok;
  } catch {
    return false;
  }
}

/**
 * Handle POST /ingest/e, POST /ingest/batch, POST /ingest/decide, and GET /api/metrics.
 */
export async function handleAnalyticsRequest(request, env, ctx) {
  const url = new URL(request.url);
  const path = url.pathname;

  const corsHeaders = {
    "Access-Control-Allow-Origin": "https://askevepodcast.com",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
  };

  if (request.method === "OPTIONS") {
    return new Response(null, { status: 204, headers: corsHeaders });
  }

  // PostHog /decide endpoint compatibility (disables invasive autocapture/session recording)
  if (path === "/ingest/decide" || path === "/ingest/decide/") {
    return new Response(
      JSON.stringify({
        config: { enable_collect_everything: false },
        toolbarParams: {},
        isAuthenticated: false,
        supportedCompression: [],
        featureFlags: {},
        sessionRecording: false,
        capturePerformance: false,
        autocapture_opt_out: true,
      }),
      {
        status: 200,
        headers: { ...corsHeaders, "Content-Type": "application/json; charset=utf-8" },
      }
    );
  }

  // PostHog event capture endpoints: /ingest/e, /ingest/batch, /ingest/capture
  if (
    request.method === "POST" &&
    (path === "/ingest/e" ||
      path === "/ingest/e/" ||
      path === "/ingest/batch" ||
      path === "/ingest/batch/" ||
      path === "/ingest/capture" ||
      path === "/ingest/capture/")
  ) {
    let payload = null;
    try {
      const rawText = await request.text();
      payload = rawText ? JSON.parse(rawText) : null;
    } catch {
      return new Response(
        JSON.stringify({ status: 0, error: "invalid_json" }),
        {
          status: 400,
          headers: { ...corsHeaders, "Content-Type": "application/json; charset=utf-8" },
        }
      );
    }

    const rawItems = Array.isArray(payload)
      ? payload
      : Array.isArray(payload?.batch)
        ? payload.batch
        : payload && typeof payload === "object"
          ? [payload]
          : [];

    const sanitized = rawItems
      .slice(0, 25)
      .map((item) => sanitizePostHogEvent(item))
      .filter(Boolean);

    if (sanitized.length === 0) {
      return new Response(
        JSON.stringify({
          status: 0,
          accepted: 0,
          error: "no_allowlisted_events",
          allowed_events: Array.from(ALLOWED_ANALYTICS_EVENTS),
        }),
        {
          status: 400,
          headers: { ...corsHeaders, "Content-Type": "application/json; charset=utf-8" },
        }
      );
    }

    const stored = await persistEventsToD1(sanitized, env);
    const forwardPromise = forwardEventsToPostHogUpstream(sanitized, env);
    if (ctx && typeof ctx.waitUntil === "function") {
      ctx.waitUntil(forwardPromise);
    } else {
      await forwardPromise;
    }

    return new Response(
      JSON.stringify({
        status: 1,
        accepted: sanitized.length,
        stored_d1: stored,
        events: sanitized.map((e) => ({
          id: e.id,
          event: e.event,
          canonical_path: e.canonical_path,
          locale: e.locale,
        })),
      }),
      {
        status: 200,
        headers: { ...corsHeaders, "Content-Type": "application/json; charset=utf-8" },
      }
    );
  }

  // Public live metrics aggregation endpoint: GET /api/metrics
  if (request.method === "GET" && (path === "/api/metrics" || path === "/api/metrics/")) {
    const windowDays = Math.min(
      Math.max(parseInt(url.searchParams.get("days") || "7", 10) || 7, 1),
      90
    );
    const sinceIso = new Date(
      Date.now() - windowDays * 24 * 60 * 60 * 1000
    ).toISOString();

    let byPath = [];
    let byEvent = [];
    let bySocial = [];
    let byDonation = [];
    let byLocale = [];

    if (env?.DB) {
      try {
        const pathRes = await env.DB.prepare(
          `SELECT canonical_path, COUNT(*) AS views
           FROM analytics_events
           WHERE event IN ('$pageview', 'rss_feed_fetch') AND created_at >= ?1
           GROUP BY canonical_path
           ORDER BY views DESC`
        )
          .bind(sinceIso)
          .all();
        byPath = pathRes?.results || [];

        const eventRes = await env.DB.prepare(
          `SELECT event, COUNT(*) AS count
           FROM analytics_events
           WHERE created_at >= ?1
           GROUP BY event
           ORDER BY count DESC`
        )
          .bind(sinceIso)
          .all();
        byEvent = eventRes?.results || [];

        const socialRes = await env.DB.prepare(
          `SELECT platform, COUNT(*) AS clicks
           FROM analytics_events
           WHERE event = 'outbound_social_click' AND created_at >= ?1
           GROUP BY platform
           ORDER BY clicks DESC`
        )
          .bind(sinceIso)
          .all();
        bySocial = socialRes?.results || [];

        const donationRes = await env.DB.prepare(
          `SELECT donation_method AS method, COUNT(*) AS clicks
           FROM analytics_events
           WHERE event = 'donation_click' AND created_at >= ?1
           GROUP BY donation_method
           ORDER BY clicks DESC`
        )
          .bind(sinceIso)
          .all();
        byDonation = donationRes?.results || [];

        const localeRes = await env.DB.prepare(
          `SELECT locale, COUNT(*) AS events
           FROM analytics_events
           WHERE created_at >= ?1
           GROUP BY locale
           ORDER BY events DESC`
        )
          .bind(sinceIso)
          .all();
        byLocale = localeRes?.results || [];
      } catch {
        // Table not yet initialized
      }
    }

    return new Response(
      JSON.stringify({
        ok: true,
        window_days: windowDays,
        since: sinceIso,
        source_of_truth: "cloudflare_d1:analytics_events",
        privacy: {
          cookieless: true,
          persistence: "memory",
          ip_stored: false,
        },
        views_by_path: byPath,
        events_by_type: byEvent,
        outbound_social_clicks: bySocial,
        donation_clicks: byDonation,
        events_by_locale: byLocale,
      }),
      {
        status: 200,
        headers: { ...corsHeaders, "Content-Type": "application/json; charset=utf-8" },
      }
    );
  }

  return null;
}
