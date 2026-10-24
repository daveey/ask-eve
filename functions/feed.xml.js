/**
 * Cloudflare Pages Function — Server-side RSS Feed Fetch Counter (`/feed.xml`)
 *
 * Podcast RSS apps (Apple Podcasts, Overcast, Pocket Casts, Spotify) fetch `/feed.xml`
 * directly via HTTP without executing browser JavaScript. This Pages Function records
 * an anonymous, zero-PII `rss_feed_fetch` event on `/feed.xml` in D1 `analytics_events`
 * and serves the static `/feed.xml` asset unchanged.
 */

import {
  sanitizePostHogEvent,
  persistEventsToD1,
  forwardEventsToPostHogUpstream,
} from "../workers/eve-inbox/src/analytics.mjs";

export async function onRequestGet(context) {
  const { request, env, next, waitUntil } = context;
  const response = await next();

  const ev = sanitizePostHogEvent({
    event: "rss_feed_fetch",
    distinct_id: "rss_reader",
    properties: {
      canonical_path: "/feed.xml",
      raw_path: new URL(request.url).pathname,
      locale: "en",
      section: "feed",
    },
  });

  if (ev) {
    const work = Promise.allSettled([
      persistEventsToD1([ev], env),
      forwardEventsToPostHogUpstream([ev], env),
    ]);
    if (typeof waitUntil === "function") {
      waitUntil(work);
    } else {
      await work;
    }
  }

  return response;
}
