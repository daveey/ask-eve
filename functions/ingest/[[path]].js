/**
 * Cloudflare Pages Function — First-Party PostHog-Compatible Ingestion (`/ingest/*`)
 *
 * Routes `/ingest/e`, `/ingest/batch`, and `/ingest/decide` on `askevepodcast.com`
 * directly to our own D1 `analytics_events` table (`migrations/0004_analytics_events.sql`)
 * and optionally dual-forwards to self-hosted PostHog (`POSTHOG_UPSTREAM_HOST`).
 *
 * Runs as a public first-party Pages Function (outside `/admin/*` Cloudflare Access)
 * so anonymous visitors are never blocked by Zero Trust or ad-blockers.
 */

import { handleAnalyticsRequest } from "../../workers/eve-inbox/src/analytics.mjs";

export async function onRequest(context) {
  const resp = await handleAnalyticsRequest(
    context.request,
    context.env,
    context
  );
  if (resp) return resp;
  return new Response(JSON.stringify({ error: "not_found" }), {
    status: 404,
    headers: { "Content-Type": "application/json; charset=utf-8" },
  });
}
