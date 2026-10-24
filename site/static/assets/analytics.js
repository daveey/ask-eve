/**
 * Ask Eve — Open-Source Self-Hosted PostHog Client (`assets/analytics.js`)
 *
 * Implements the PostHog wire capture protocol (`$pageview` + explicit interaction
 * taxonomy) with 100% first-party raw event ownership:
 *   - Sends events to first-party `/ingest/e` (Cloudflare Worker / Pages Function -> D1 `analytics_events`)
 *     and optionally dual-forwards to our self-hosted PostHog instance (`ph.askevepodcast.com`)
 *     or PostHog Cloud free tier (`us.i.posthog.com`) when configured.
 *   - Cookieless (`persistence: "memory"`): Never reads or writes `document.cookie`,
 *     `localStorage`, or `sessionStorage` for tracking IDs.
 *   - Zero PII (`ip: false`): Client IP addresses are stripped at the edge (`$ip: null`).
 */
(function () {
  "use strict";

  var KNOWN_LOCALES = {
    en: true,
    es: true,
    zh: true,
    ru: true,
    de: true,
    pt: true,
    fa: true,
    fr: true,
  };

  var CANONICAL_SECTIONS = {
    episodes: "episodes",
    people: "people",
    topics: "topics",
    ledger: "ledger",
    reports: "reports",
    guide: "guide",
    growth: "growth",
    metrics: "metrics",
    eve: "eve",
    contact: "contact",
    about: "about",
  };

  var ALLOWED_EVENTS = {
    $pageview: true,
    outbound_social_click: true,
    donation_click: true,
    rss_feed_click: true,
    audio_play: true,
    source_receipt_click: true,
  };

  // Ephemeral in-memory distinct_id (survives only within the current page load; zero cookies)
  var memoryDistinctId =
    "mem_" +
    Math.random().toString(36).slice(2, 10) +
    "_" +
    Date.now().toString(36);

  function readPageConfig() {
    var defaults = {
      api_host: "/ingest",
      ui_host: "https://ph.askevepodcast.com",
      project_api_key: "phc_askeve_selfhosted_public",
      persistence: "memory",
      autocapture: false,
      capture_pageview: true,
      ip: false,
    };
    try {
      var el = document.getElementById("posthog-config");
      if (el && el.textContent) {
        var parsed = JSON.parse(el.textContent);
        if (parsed && typeof parsed === "object") {
          for (var k in parsed) {
            if (Object.prototype.hasOwnProperty.call(parsed, k)) {
              defaults[k] = parsed[k];
            }
          }
        }
      }
    } catch (_e) {
      // Ignore malformed config JSON
    }
    return defaults;
  }

  function parseCanonicalPath(pathname) {
    var raw = String(pathname || "/").split("?")[0].split("#")[0] || "/";
    if (raw.charAt(0) !== "/") raw = "/" + raw;
    var parts = raw.split("/").filter(Boolean);
    var locale = "en";
    if (parts.length > 0 && KNOWN_LOCALES[parts[0].toLowerCase()]) {
      locale = parts[0].toLowerCase();
      parts = parts.slice(1);
    }
    if (parts.length === 0) {
      return {
        canonical_path: "/",
        raw_path: raw,
        locale: locale,
        section: "home",
        episode_slug: null,
      };
    }
    var first = parts[0].toLowerCase();
    if (first === "feed.xml") {
      return {
        canonical_path: "/feed.xml",
        raw_path: raw,
        locale: locale,
        section: "feed",
        episode_slug: null,
      };
    }
    if (first === "episodes") {
      return {
        canonical_path: "/episodes/",
        raw_path: raw,
        locale: locale,
        section: "episodes",
        episode_slug: parts.length > 1 ? parts[1] : null,
      };
    }
    if (CANONICAL_SECTIONS[first]) {
      return {
        canonical_path: "/" + first + "/",
        raw_path: raw,
        locale: locale,
        section: CANONICAL_SECTIONS[first],
        episode_slug: null,
      };
    }
    return {
      canonical_path: raw.slice(-1) === "/" ? raw : raw + "/",
      raw_path: raw,
      locale: locale,
      section: "other",
      episode_slug: null,
    };
  }

  function getReferrerDomain() {
    try {
      if (!document.referrer) return "direct";
      var u = new URL(document.referrer);
      if (u.hostname === location.hostname) return "internal";
      return u.hostname.toLowerCase();
    } catch (_e) {
      return "direct";
    }
  }

  var config = readPageConfig();
  var eventLog = [];

  function sendPayload(endpoint, bodyObj) {
    var json = JSON.stringify(bodyObj);
    try {
      if (navigator.sendBeacon) {
        var blob = new Blob([json], { type: "application/json" });
        if (navigator.sendBeacon(endpoint, blob)) {
          return;
        }
      }
    } catch (_e) {
      // Fallback to fetch
    }
    try {
      if (typeof fetch === "function") {
        fetch(endpoint, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: json,
          keepalive: true,
          credentials: "omit",
        }).catch(function () {});
      }
    } catch (_e2) {
      // Offline or blocked
    }
  }

  function capture(eventName, extraProps) {
    if (!ALLOWED_EVENTS[eventName]) {
      return null;
    }
    var meta = parseCanonicalPath(location.pathname);
    var props = {
      canonical_path: meta.canonical_path,
      raw_path: meta.raw_path,
      locale: meta.locale,
      section: meta.section,
      referrer_domain: getReferrerDomain(),
      $lib: "askeve-posthog-js",
      $ip: null,
    };
    if (meta.episode_slug) {
      props.episode_slug = meta.episode_slug;
    }
    if (extraProps && typeof extraProps === "object") {
      for (var k in extraProps) {
        if (
          Object.prototype.hasOwnProperty.call(extraProps, k) &&
          extraProps[k] !== undefined &&
          extraProps[k] !== null
        ) {
          props[k] = extraProps[k];
        }
      }
    }

    var wireEvent = {
      api_key: config.project_api_key,
      event: eventName,
      distinct_id: memoryDistinctId,
      timestamp: new Date().toISOString(),
      properties: props,
    };

    eventLog.push(wireEvent);
    var host = String(config.api_host || "/ingest").replace(/\/+$/, "");
    sendPayload(host + "/e", wireEvent);
    return wireEvent;
  }

  function classifySocialLink(href, anchor) {
    var explicit = anchor.getAttribute("data-social-platform");
    if (explicit) {
      return {
        platform: explicit.toLowerCase(),
        handle: anchor.getAttribute("data-social-handle") || "@askevepodcast",
      };
    }
    var lower = String(href || "").toLowerCase();
    if (
      lower.indexOf("x.com/askevepodcast") !== -1 ||
      lower.indexOf("twitter.com/askevepodcast") !== -1 ||
      lower.indexOf("x.com/intent/tweet") !== -1
    ) {
      return { platform: "x", handle: "@askevepodcast" };
    }
    if (lower.indexOf("bsky.app/profile/askevepodcast") !== -1) {
      return { platform: "bluesky", handle: "@askevepodcast.bsky.social" };
    }
    if (lower.indexOf("truthsocial.com/@askevepodcast") !== -1) {
      return { platform: "truthsocial", handle: "@askevepodcast" };
    }
    if (lower.indexOf("github.com/daveey/ask-eve") !== -1) {
      return { platform: "github", handle: "daveey/ask-eve" };
    }
    return null;
  }

  function classifyDonationTarget(href, el) {
    var explicit = el.getAttribute("data-donation-method");
    if (explicit) {
      return explicit.toLowerCase();
    }
    var lower = String(href || "").toLowerCase();
    if (
      lower.indexOf("paypal.com") !== -1 ||
      lower.indexOf("paypal.me") !== -1
    ) {
      return "paypal";
    }
    if (
      lower.indexOf("buy.stripe.com") !== -1 ||
      lower.indexOf("checkout.stripe.com") !== -1 ||
      lower.indexOf("stripe.com") !== -1
    ) {
      return "stripe";
    }
    if (
      lower.indexOf("bitcoin:") === 0 ||
      lower.indexOf("ethereum:") === 0 ||
      lower.indexOf("solana:") === 0
    ) {
      return "crypto";
    }
    return null;
  }

  function classifyRssLink(href, el) {
    if (el.hasAttribute("data-copy-rss")) {
      return {
        client: "rss_copy",
        feed_url: el.getAttribute("data-copy-rss") || "/feed.xml",
      };
    }
    var lower = String(href || "").toLowerCase();
    if (lower.indexOf("podcast://") === 0) {
      return { client: "apple_podcasts", feed_url: href };
    }
    if (lower.indexOf("open.spotify.com/show/") !== -1) {
      return { client: "spotify", feed_url: href };
    }
    if (
      lower.slice(-9) === "/feed.xml" ||
      lower === "feed.xml" ||
      lower.indexOf("/feed.xml?") !== -1
    ) {
      return { client: "rss", feed_url: href };
    }
    return null;
  }

  // Expose PostHog-compatible global interface (`window.posthog` & `window.AskEveAnalytics`)
  var api = {
    __loaded: true,
    config: config,
    init: function (apiKey, opts) {
      if (apiKey) config.project_api_key = apiKey;
      if (opts && typeof opts === "object") {
        for (var k in opts) {
          if (Object.prototype.hasOwnProperty.call(opts, k)) {
            config[k] = opts[k];
          }
        }
      }
      return api;
    },
    capture: capture,
    get_distinct_id: function () {
      return memoryDistinctId;
    },
    get_captured_events: function () {
      return eventLog.slice();
    },
    parseCanonicalPath: parseCanonicalPath,
  };

  window.posthog = api;
  window.AskEveAnalytics = api;

  // 1. Capture initial $pageview
  if (config.capture_pageview !== false) {
    capture("$pageview", {});
  }

  // 2. Delegated click listener for social, donation, RSS, and source receipt interactions
  document.addEventListener(
    "click",
    function (ev) {
      var target = ev.target;
      if (!target || typeof target.closest !== "function") return;

      var meta = parseCanonicalPath(location.pathname);

      // Check donation button / link (supports <button data-donation-method="..."> or <a ...>)
      var donationEl = target.closest("[data-donation-method], a[href]");
      if (donationEl) {
        var donHref = donationEl.getAttribute("href") || "";
        var method = classifyDonationTarget(donHref, donationEl);
        if (method) {
          capture("donation_click", {
            method: method,
            target_url:
              donHref ||
              donationEl.getAttribute("data-donation-target") ||
              meta.canonical_path,
            source_path: meta.canonical_path,
          });
          var walletAddr = donationEl.getAttribute("data-wallet-address");
          if (walletAddr && navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(walletAddr).catch(function () {});
          }
          return;
        }
      }

      var anchor = target.closest("a[href], button[data-copy-rss]");
      if (!anchor) return;
      var href = anchor.getAttribute("href") || "";

      // Check RSS / Apple Podcasts / Spotify subscribe clicks
      var rss = classifyRssLink(href, anchor);
      if (rss) {
        capture("rss_feed_click", {
          client: rss.client,
          target_url: rss.feed_url,
          source_path: meta.canonical_path,
        });
        return;
      }

      // Check outbound social account clicks (X @askevepodcast, Bluesky, Truth Social, GitHub)
      var social = classifySocialLink(href, anchor);
      if (social) {
        capture("outbound_social_click", {
          platform: social.platform,
          handle: social.handle,
          target_url: href,
          source_path: meta.canonical_path,
        });
        return;
      }

      // Check primary-source receipt clicks (.source-card a, a.cite, a.q__src, [data-source-id])
      var sourceCard = anchor.closest(".source-card");
      var isReceipt =
        sourceCard ||
        anchor.classList.contains("cite") ||
        anchor.classList.contains("q__src") ||
        anchor.classList.contains("src-badge") ||
        anchor.hasAttribute("data-source-id");
      if (isReceipt) {
        var sourceId =
          anchor.getAttribute("data-source-id") ||
          (sourceCard && sourceCard.id) ||
          anchor.textContent.trim().slice(0, 64);
        capture("source_receipt_click", {
          source_id: sourceId,
          target_url: href,
          source_path: meta.canonical_path,
        });
      }
    },
    true
  );

  // 3. Capture audio/audiogram playback starts (`audio_play`)
  var playedElements = new WeakSet();
  document.addEventListener(
    "play",
    function (ev) {
      var media = ev.target;
      if (!media || playedElements.has(media)) return;
      var tag = (media.tagName || "").toLowerCase();
      if (tag !== "audio" && tag !== "video") return;
      playedElements.add(media);

      var meta = parseCanonicalPath(location.pathname);
      var epSlug =
        media.getAttribute("data-ep-slug") ||
        meta.episode_slug ||
        (document.getElementById("player") &&
          document.getElementById("player").getAttribute("data-ep-slug")) ||
        "episode";
      var clipCard =
        typeof media.closest === "function" ? media.closest(".clip-card") : null;
      var clipId = clipCard ? clipCard.getAttribute("data-clip-id") : null;
      var audioLang = media.getAttribute("data-audio-lang") || meta.locale;

      capture("audio_play", {
        episode_slug: epSlug,
        clip_id: clipId,
        audio_lang: audioLang,
        source_path: meta.canonical_path,
      });
    },
    true
  );
})();
