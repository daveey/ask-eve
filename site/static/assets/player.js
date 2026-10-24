/* Ask Eve: player, transcript sync, theme toggle, language scroll sync, audio fallback, small UI helpers. No dependencies, no tracking. */
(function () {
  "use strict";
  var doc = document, root = doc.documentElement;
  var $ = function (s, el) { return (el || doc).querySelector(s); };
  var $$ = function (s, el) { return Array.prototype.slice.call((el || doc).querySelectorAll(s)); };
  var store = {
    get: function (k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set: function (k, v) { try { localStorage.setItem(k, v); } catch (e) {} },
    del: function (k) { try { localStorage.removeItem(k); } catch (e) {} }
  };
  /* UI strings: translated pages carry <html data-i18n='{"English": "translation"}'> (see scripts/site_i18n.py) */
  var I18N = {}; try { I18N = JSON.parse(root.getAttribute("data-i18n") || "{}"); } catch (e) {}
  var L = function (s) { return I18N[s] || s; };
  var reduceMotion = window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches;
  var LANG_SCROLL_KEY = "askeve_lang_scroll";

  function clock(s) {
    s = Math.max(0, Math.floor(s || 0));
    var h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = s % 60;
    var mm = h ? String(m).padStart(2, "0") : String(m);
    return (h ? h + ":" : "") + mm + ":" + String(x).padStart(2, "0");
  }

  /* ---------- theme toggle: auto -> light -> dark -> auto ---------- */
  var themeBtn = $("[data-theme-toggle]");
  function themeLabel() {
    if (!themeBtn) return;
    var t = root.getAttribute("data-theme") || "auto";
    var names = { auto: L("Auto"), light: L("Light"), dark: L("Dark") };
    var lab = $(".theme-btn__label", themeBtn);
    if (lab) lab.textContent = names[t];
    themeBtn.setAttribute("aria-label", L("Color theme: ") + L(t === "auto" ? "system" : t) + L(". Click to change."));
  }
  if (themeBtn) {
    themeLabel();
    themeBtn.addEventListener("click", function () {
      var t = root.getAttribute("data-theme") || "auto";
      var next = t === "auto" ? "light" : t === "light" ? "dark" : "auto";
      if (next === "auto") { root.removeAttribute("data-theme"); store.del("ae:theme"); }
      else { root.setAttribute("data-theme", next); store.set("ae:theme", next); }
      themeLabel();
    });
  }

  /* ---------- language menu dropdown close on outside click / Escape ---------- */
  var langMenus = $$("details[data-menu]");
  if (langMenus.length) {
    doc.addEventListener("click", function (ev) {
      langMenus.forEach(function (m) {
        if (!m.contains(ev.target)) m.removeAttribute("open");
      });
    });
    doc.addEventListener("keydown", function (ev) {
      if (ev.key === "Escape") {
        langMenus.forEach(function (m) { m.removeAttribute("open"); });
      }
    });
  }

  /* ---------- clickable episode & transcript/quote cards (.split-card, .card, .session-part, .pm, .qa-turn) ---------- */
  doc.addEventListener("click", function (ev) {
    if (ev.defaultPrevented || ev.button !== 0) return;
    var interactive = ev.target.closest && ev.target.closest("a, button, input, select, textarea, summary, details, video, audio, code");
    if (interactive) return;
    var sel = window.getSelection ? String(window.getSelection()) : "";
    if (sel && sel.trim().length > 0) return;
    var quoteCard = ev.target.closest && ev.target.closest(".pm, .qa-turn");
    if (quoteCard) {
      var listenLink = quoteCard.querySelector('.pm__listen, a.cite[href*="/episodes/"][href*="#t="], a[href*="/episodes/"][href*="#t="]');
      if (listenLink) {
        listenLink.click();
        return;
      }
    }
    var card = ev.target.closest && ev.target.closest(".split-card, .card, .session-part");
    if (!card) return;
    var primary = card.querySelector(".split-card__title a, .card__title a, .session-part__title a");
    if (!primary || !primary.href) return;
    if (ev.metaKey || ev.ctrlKey) {
      window.open(primary.href, "_blank", "noopener");
    } else {
      window.location.href = primary.href;
    }
  });

  /* ---------- copy feed URL & clip share links ---------- */
  $$("[data-copy-feed]").forEach(function (b) {
    b.addEventListener("click", function () {
      var url = b.getAttribute("data-copy-feed"), old = b.textContent;
      var done = function () { b.textContent = L("Copied"); b.classList.add("is-done"); setTimeout(function () { b.textContent = old; b.classList.remove("is-done"); }, 1800); };
      if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(url).then(done, function () { prompt(L("Feed URL"), url); });
      else prompt(L("Feed URL"), url);
    });
  });
  doc.addEventListener("click", function (ev) {
    var b = ev.target.closest && ev.target.closest("[data-copy-url]");
    if (!b) return;
    ev.preventDefault();
    var url = b.getAttribute("data-copy-url");
    if (!url) return;
    var old = b.getAttribute("data-orig-label") || b.textContent;
    b.setAttribute("data-orig-label", old);
    var done = function () {
      b.textContent = L("Copied ✓");
      b.classList.add("is-copied");
      setTimeout(function () {
        b.textContent = old;
        b.classList.remove("is-copied");
      }, 2000);
    };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(url).then(done, function () { prompt(L("Copy link"), url); });
    } else {
      prompt(L("Copy link"), url);
    }
  });

  /* ---------- wide screens: open collapsible side boxes ---------- */
  if (window.matchMedia && matchMedia("(min-width: 1024px)").matches) $$("details[data-open-wide]").forEach(function (d) { d.open = true; });

  /* ---------- semantic scroll position preservation across language switches ---------- */
  var SEM_SELECTOR = [
    ".turn[id]",
    "h1[id], h2[id], h3[id]",
    "section[id]",
    ".rpg-card",
    ".rpg-stat",
    ".qa-turn",
    ".pm",
    ".pcard",
    ".tcard",
    ".ep-card",
    ".val",
    ".trait",
    ".cav",
    ".chg",
    "figure.q, figure.tq, figure.turnq, figure.stance",
    "details",
    "main p:not(.i18n-note)",
    "main li",
    "main blockquote"
  ].join(", ");

  function getSemanticElements() {
    var main = $("main") || doc.body;
    var nodes = $$(SEM_SELECTOR, main);
    var counts = Object.create(null);
    var out = [];
    for (var i = 0; i < nodes.length; i++) {
      var el = nodes[i];
      if (el.closest(".i18n-note, .eve-nav, .ep-side, aside, nav, .site-head, .site-foot, .player")) continue;
      if (!el.getAttribute("data-sem-id")) {
        var id = el.getAttribute("id");
        var key;
        if (id) {
          key = "id:" + id;
        } else {
          var sec = el.closest("section[id]");
          var secPrefix = sec ? sec.getAttribute("id") + "/" : "";
          var tag = el.tagName.toLowerCase();
          var cls = (el.className && typeof el.className === "string")
            ? el.className.trim().split(/\s+/)[0] || ""
            : "";
          var sig = secPrefix + tag + (cls ? "." + cls : "");
          counts[sig] = (counts[sig] || 0) + 1;
          key = sig + "#" + counts[sig];
        }
        el.setAttribute("data-sem-id", key);
      }
      out.push(el);
    }
    return out;
  }

  function getReadingLineY() {
    var hdr = $(".site-head");
    var bottom = hdr ? Math.max(0, hdr.getBoundingClientRect().bottom) : 0;
    return bottom + 24;
  }

  function clamp(v, lo, hi) {
    return Math.max(lo, Math.min(hi, v));
  }

  function captureLanguageScrollState() {
    var candidates = getSemanticElements();
    var lineY = getReadingLineY();
    var bestEl = null;
    var bestIdx = 0;
    var bestScore = Infinity;

    for (var i = 0; i < candidates.length; i++) {
      var el = candidates[i];
      var r = el.getBoundingClientRect();
      if (r.height <= 0 && r.width <= 0) continue;
      var dist = (r.top <= lineY && r.bottom >= lineY)
        ? 0
        : Math.min(Math.abs(r.top - lineY), Math.abs(r.bottom - lineY));
      var isWrapper = (el.tagName === "SECTION" || el.tagName === "DETAILS") && r.height > 260;
      var score = dist + (isWrapper ? 80 : 0) + (dist === 0 ? Math.min(r.height, 400) * 0.05 : 0);
      if (score < bestScore) {
        bestScore = score;
        bestEl = el;
        bestIdx = i;
      }
    }

    var progress = 0;
    var semId = null;
    if (bestEl) {
      var rect = bestEl.getBoundingClientRect();
      semId = bestEl.getAttribute("data-sem-id") || bestEl.id || null;
      progress = clamp((lineY - rect.top) / Math.max(1, rect.height), 0, 1);
    }

    var pWrap = $("#player");
    var pNode = $("#player audio");
    var audioTime = (pNode && isFinite(pNode.currentTime) && pNode.currentTime > 0) ? pNode.currentTime : 0;
    var audioPlaying = !!(pNode && !pNode.paused);
    var audioSlug = pWrap ? pWrap.getAttribute("data-slug") : null;
    var audioDur = pWrap ? (parseFloat(pWrap.getAttribute("data-duration")) || 0) : 0;

    return {
      semId: semId,
      tagIndex: bestIdx,
      progress: progress,
      scrollY: window.scrollY || window.pageYOffset || 0,
      hash: location.hash || "",
      audioSlug: audioSlug,
      audioDur: audioDur,
      audioTime: audioTime,
      audioPlaying: audioPlaying,
      ts: Date.now()
    };
  }

  function restoreLanguageScrollState() {
    var raw = null;
    try {
      raw = sessionStorage.getItem(LANG_SCROLL_KEY);
      if (raw) sessionStorage.removeItem(LANG_SCROLL_KEY);
    } catch (e) {}
    if (!raw) return null;

    var state = null;
    try { state = JSON.parse(raw); } catch (e) { return null; }
    if (!state || typeof state !== "object") return null;
    if (state.ts && (Date.now() - state.ts > 15000)) return null;

    var candidates = getSemanticElements();
    if (!candidates.length) return state;

    if (state.scrollY !== undefined && state.scrollY <= 8 && !state.hash) {
      window.scrollTo(0, 0);
      return state;
    }

    var target = null;
    if (state.semId) {
      for (var i = 0; i < candidates.length; i++) {
        if (candidates[i].getAttribute("data-sem-id") === state.semId || candidates[i].id === state.semId) {
          target = candidates[i];
          break;
        }
      }
    }
    if (!target && typeof state.tagIndex === "number" && candidates[state.tagIndex]) {
      target = candidates[state.tagIndex];
    }

    if (target) {
      var det = target.closest("details");
      while (det) {
        det.open = true;
        det = det.parentElement ? det.parentElement.closest("details") : null;
      }
      var lineY = getReadingLineY();
      var rect = target.getBoundingClientRect();
      var prog = clamp(Number(state.progress) || 0, 0, 1);
      var desiredTop = Math.max(0, Math.round(window.scrollY + rect.top + prog * rect.height - lineY));
      try {
        window.scrollTo({ top: desiredTop, left: 0, behavior: "instant" });
      } catch (e) {
        window.scrollTo(0, desiredTop);
      }
    }
    return state;
  }

  getSemanticElements();
  var restoredLangState = restoreLanguageScrollState();

  doc.addEventListener("click", function (ev) {
    var link = ev.target.closest && ev.target.closest("a[data-lang-option], .i18n-note a");
    if (!link) return;
    try {
      var st = captureLanguageScrollState();
      sessionStorage.setItem(LANG_SCROLL_KEY, JSON.stringify(st));
      if (location.hash && link.href && link.href.indexOf("#") === -1) {
        link.href = link.href + location.hash;
      }
    } catch (e) {}
  });

  /* ---------- Homepage Featured Episodes Carousel ---------- */
  $$("[data-hero-carousel]").forEach(function (carousel) {
    var slides = $$("[data-hero-slide]", carousel);
    var tabs = $$("[data-hero-tab]", carousel);
    var prevBtn = $("[data-hero-prev]", carousel);
    var nextBtn = $("[data-hero-next]", carousel);
    if (slides.length <= 1) return;
    var curIdx = 0;
    var pausedByUser = false;
    var hoverPaused = false;

    function showSlide(idx, userInitiated) {
      curIdx = ((idx % slides.length) + slides.length) % slides.length;
      slides.forEach(function (sl, i) {
        var active = (i === curIdx);
        sl.classList.toggle("is-active", active);
        if (active) sl.removeAttribute("hidden");
        else sl.setAttribute("hidden", "");
      });
      tabs.forEach(function (tb, i) {
        var active = (i === curIdx);
        tb.classList.toggle("is-active", active);
        tb.setAttribute("aria-selected", active ? "true" : "false");
        tb.setAttribute("tabindex", active ? "0" : "-1");
        if (active && userInitiated && tb.scrollIntoView) {
          try { tb.scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth", block: "nearest", inline: "nearest" }); } catch (e) {}
        }
      });
      if (userInitiated) pausedByUser = true;
    }

    tabs.forEach(function (tb, i) {
      tb.addEventListener("click", function () { showSlide(i, true); });
      tb.addEventListener("keydown", function (ev) {
        if (ev.key === "ArrowRight") { ev.preventDefault(); showSlide(i + 1, true); if (tabs[curIdx]) tabs[curIdx].focus(); }
        else if (ev.key === "ArrowLeft") { ev.preventDefault(); showSlide(i - 1, true); if (tabs[curIdx]) tabs[curIdx].focus(); }
      });
    });
    if (prevBtn) prevBtn.addEventListener("click", function () { showSlide(curIdx - 1, true); });
    if (nextBtn) nextBtn.addEventListener("click", function () { showSlide(curIdx + 1, true); });

    carousel.addEventListener("mouseenter", function () { hoverPaused = true; });
    carousel.addEventListener("mouseleave", function () { hoverPaused = false; });
    carousel.addEventListener("focusin", function () { hoverPaused = true; });
    carousel.addEventListener("focusout", function () { hoverPaused = false; });

    var touchStartX = 0, touchStartY = 0;
    carousel.addEventListener("touchstart", function (ev) {
      if (!ev.touches || !ev.touches.length) return;
      touchStartX = ev.touches[0].clientX;
      touchStartY = ev.touches[0].clientY;
    }, { passive: true });
    carousel.addEventListener("touchend", function (ev) {
      if (!ev.changedTouches || !ev.changedTouches.length) return;
      var dx = ev.changedTouches[0].clientX - touchStartX;
      var dy = ev.changedTouches[0].clientY - touchStartY;
      if (Math.abs(dx) > 48 && Math.abs(dx) > Math.abs(dy) * 1.4) {
        showSlide(dx < 0 ? curIdx + 1 : curIdx - 1, true);
      }
    }, { passive: true });

    if (!reduceMotion) {
      setInterval(function () {
        if (pausedByUser || hoverPaused || doc.hidden || doc.body.classList.contains("is-playing")) return;
        showSlide(curIdx + 1, false);
      }, 7500);
    }
  });

  /* ---------- player ---------- */
  var P = $("#player");
  if (!P) return;
  var audio = $("audio", P), range = $(".range", P), cur = $("[data-cur]", P), durEl = $("[data-dur]", P);
  var toggle = $('[data-act="toggle"]', P), speedBtn = $('[data-act="speed"]', P), chapEl = $("[data-chapter]", P);
  var slug = P.getAttribute("data-slug"), title = P.getAttribute("data-title");
  var duration = parseFloat(P.getAttribute("data-duration")) || 0;
  var clipStart = P.hasAttribute("data-clip-start") ? parseFloat(P.getAttribute("data-clip-start")) : null;
  var clipEnd = P.hasAttribute("data-clip-end") ? parseFloat(P.getAttribute("data-clip-end")) : null;
  var langCode = (root.lang || "en").toLowerCase();
  function makePosKey(s) { return "ae:pos:" + langCode + ":" + (s || ""); }
  var posKey = makePosKey(slug), rateKey = "ae:rate";
  var SPEEDS = [1, 1.25, 1.5, 1.75, 2, 0.75];
  var pendingSeek = null, dragging = false, playIntended = false;
  var CATALOG = {};
  try {
    var catEl = $("#episodes-catalog");
    if (catEl) CATALOG = JSON.parse(catEl.textContent || "{}");
  } catch (e) {}
  var drawerBtn = $('[data-act="drawer"]', P), drawer = $("#p-drawer", P);
  var drawerCue = $("[data-drawer-cue]", P), drawerLink = $("[data-drawer-ep-link]", P);
  if (drawerBtn && drawer) {
    drawerBtn.addEventListener("click", function () {
      var open = !drawer.hasAttribute("hidden");
      if (open) { drawer.setAttribute("hidden", ""); drawerBtn.setAttribute("aria-expanded", "false"); }
      else { drawer.removeAttribute("hidden"); drawerBtn.setAttribute("aria-expanded", "true"); }
    });
  }

  // Transcript and chapters (episode page only)
  var turnsEl = $("[data-turns]");
  var turns = turnsEl ? $$(".turn[data-start]", turnsEl).filter(function (t) { return !t.classList.contains("turn--gap"); }) : [];
  var starts = turns.map(function (t) { return parseFloat(t.getAttribute("data-start")); });
  var enStarts = turns.map(function (t) { return parseFloat(t.getAttribute("data-en-start") || t.getAttribute("data-start")); });
  var enEnds = turns.map(function (t) { return parseFloat(t.getAttribute("data-en-end") || t.getAttribute("data-end") || t.getAttribute("data-start")); });
  var chaps = $$(".chap[data-seek]");
  var chapStarts = chaps.map(function (c) { return parseFloat(c.getAttribute("data-seek")); });
  var enChapStarts = chaps.map(function (c) { return parseFloat(c.getAttribute("data-en-seek") || c.getAttribute("data-seek")); });
  var chapTitles = chaps.map(function (c) { var t = $(".chap__title", c); return t ? t.firstChild.textContent : ""; });
  var defaultSub = chapEl ? chapEl.textContent : "";
  var followBtn = $("[data-follow]"), following = true, autoScrolling = 0;
  var activeTurn = -1, activeChap = -1;

  /* ---------- Russian / localized audio fallback to English MP3 on error ---------- */
  function switchToFallbackAudio() {
    if (!audio) return false;
    var fb = audio.getAttribute("data-fallback-src");
    var stage = parseInt(audio.getAttribute("data-using-fallback-audio") || "0", 10);
    if (stage >= 1 && /\.v[3-9]\.mp3$/.test(audio.src || "")) {
      fb = (audio.src || "").replace(/\.v\d+\.mp3$/, ".v2.mp3");
    } else if (!fb || stage >= 1) {
      return false;
    }
    audio.setAttribute("data-using-fallback-audio", String(stage + 1));
    var savedTime = pendingSeek != null ? pendingSeek : (audio.currentTime || 0);
    var fbClipStart = audio.getAttribute("data-fallback-clip-start");
    var fbClipEnd = audio.getAttribute("data-fallback-clip-end");
    if (fbClipStart != null && fbClipEnd != null) {
      clipStart = parseFloat(fbClipStart);
      clipEnd = parseFloat(fbClipEnd);
      savedTime = clipStart + savedTime;
    }
    var enDur = parseFloat(P.getAttribute("data-en-duration") || "0");
    if (enDur > 0) {
      duration = enDur;
      if (durEl) durEl.textContent = clock(duration);
    }
    for (var i = 0; i < turns.length; i++) {
      if (!isNaN(enStarts[i])) {
        starts[i] = enStarts[i];
        turns[i].setAttribute("data-start", String(enStarts[i]));
      }
      if (!isNaN(enEnds[i])) {
        turns[i].setAttribute("data-end", String(enEnds[i]));
      }
    }
    for (var j = 0; j < chaps.length; j++) {
      if (!isNaN(enChapStarts[j])) {
        chapStarts[j] = enChapStarts[j];
        chaps[j].setAttribute("data-seek", String(enChapStarts[j]));
      }
    }
    if (savedTime > 0) pendingSeek = savedTime;
    setAudioSrc(fb);
    if (playIntended) {
      safePlay();
    }
    return true;
  }

  function setAudioSrc(newUrl) {
    if (!audio || !newUrl) return;
    var sources = $$("source", audio);
    for (var i = 0; i < sources.length; i++) {
      if (i === 0) sources[i].setAttribute("src", newUrl);
      else if (sources[i].parentNode) sources[i].parentNode.removeChild(sources[i]);
    }
    audio.setAttribute("src", newUrl);
    audio.src = newUrl;
    audio.load();
  }

  function safePlay() {
    if (!audio) return;
    playIntended = true;
    var p = audio.play();
    if (p && typeof p.catch === "function") {
      p.catch(function (err) {
        if (err && (err.name === "AbortError" || err.name === "NotAllowedError")) return;
        if (audio.error && audio.error.code !== 1) switchToFallbackAudio();
      });
    }
  }

  audio.addEventListener("error", function () {
    if (audio.error && audio.error.code === 1) return;
    switchToFallbackAudio();
  });
  $$("source", audio).forEach(function (srcNode) {
    srcNode.addEventListener("error", function () {
      if (audio.error && audio.error.code === 1) return;
      switchToFallbackAudio();
    });
  });

  function idx(arr, t) { // last index with arr[i] <= t
    var lo = 0, hi = arr.length - 1, ans = -1;
    while (lo <= hi) { var mid = (lo + hi) >> 1; if (arr[mid] <= t + 0.05) { ans = mid; lo = mid + 1; } else hi = mid - 1; }
    return ans;
  }

  function show() {
    if (P.classList.contains("is-idle")) { P.classList.remove("is-idle"); doc.body.classList.add("is-playing"); }
  }
  function effDuration() {
    if (clipStart != null && clipEnd != null && clipEnd > clipStart) return clipEnd - clipStart;
    return audio.duration && isFinite(audio.duration) ? audio.duration : duration;
  }

  function normalizeSeekTime(t, targetInfo) {
    t = Math.max(0, parseFloat(t) || 0);
    var d = targetInfo ? (parseFloat(targetInfo.duration) || 0) : effDuration();
    var fbStart = targetInfo
      ? targetInfo.fallback_clip_start
      : (CATALOG[slug] ? CATALOG[slug].fallback_clip_start : (audio && audio.hasAttribute("data-fallback-clip-start") ? parseFloat(audio.getAttribute("data-fallback-clip-start")) : null));
    if (d > 0 && t >= d) {
      if (fbStart != null && !isNaN(fbStart) && t >= fbStart && (t - fbStart) < d) {
        t = Math.max(0, t - fbStart);
      } else {
        t = Math.max(0, d - 5);
      }
    }
    return t;
  }

  function setRange(t) {
    var d = effDuration();
    var dispT = d > 0 ? Math.min(Math.max(0, t), d) : Math.max(0, t);
    range.max = Math.max(1, Math.floor(d));
    if (!dragging) range.value = Math.floor(dispT);
    range.style.setProperty("--p", (d ? (Math.min(dispT, d) / d) * 100 : 0) + "%");
    cur.textContent = clock(dispT);
    if (durEl) durEl.textContent = clock(d);
    range.setAttribute("aria-valuetext", clock(dispT) + " of " + clock(d));
  }

  function updateDrawerCue(t) {
    if (!drawerCue) return;
    var catEp = CATALOG[slug];
    if (catEp && catEp.cues && catEp.cues.length) {
      var lo = 0, hi = catEp.cues.length - 1, ans = -1;
      while (lo <= hi) { var mid = (lo + hi) >> 1; if (catEp.cues[mid][0] <= t + 0.05) { ans = mid; lo = mid + 1; } else hi = mid - 1; }
      if (ans >= 0) {
        var cue = catEp.cues[ans];
        drawerCue.textContent = "[" + clock(cue[0]) + "] " + cue[2] + ": " + cue[3];
        return;
      }
    }
    if (turns.length && activeTurn >= 0) {
      var body = $(".turn__body", turns[activeTurn]);
      if (body) drawerCue.textContent = "[" + clock(starts[activeTurn]) + "] " + body.textContent.trim().slice(0, 200);
    }
  }

  function scrollToTurn(el, force) {
    if (!el || (!following && !force)) return;
    var r = el.getBoundingClientRect(), vh = window.innerHeight;
    var bottomBar = P.getBoundingClientRect().height + 24, top = 90;
    if (!force && r.top >= top && r.bottom <= vh - bottomBar) return;
    autoScrolling = Date.now();
    var y = window.scrollY + r.top - Math.max(top, (vh - bottomBar) * 0.28);
    window.scrollTo({ top: y, behavior: reduceMotion ? "auto" : "smooth" });
  }

  function sync(t, forceScroll) {
    setRange(t);
    if (turns.length) {
      var i = idx(starts, t);
      if (i !== activeTurn) {
        if (activeTurn >= 0) { turns[activeTurn].classList.remove("is-active"); turns[activeTurn].removeAttribute("aria-current"); }
        activeTurn = i;
        if (i >= 0) { turns[i].classList.add("is-active"); turns[i].setAttribute("aria-current", "true"); scrollToTurn(turns[i], forceScroll); }
      } else if (forceScroll && i >= 0) scrollToTurn(turns[i], true);
    }
    if (chaps.length) {
      var c = idx(chapStarts, t);
      if (c !== activeChap) {
        if (activeChap >= 0) chaps[activeChap].classList.remove("is-current");
        activeChap = c;
        if (c >= 0) chaps[c].classList.add("is-current");
        if (chapEl) chapEl.textContent = c >= 0 ? chapTitles[c] : defaultSub;
        updateMeta();
      }
    }
    updateDrawerCue(t);
  }

  function seek(t, play, skipScroll) {
    t = normalizeSeekTime(t, null);
    var rawT = clipStart != null ? clipStart + t : t;
    show();
    if (audio.readyState >= 1) {
      try { audio.currentTime = rawT; } catch (e) { pendingSeek = rawT; }
    } else {
      pendingSeek = rawT;
      if (play && audio.networkState === 0) audio.load();
    }
    sync(t, !skipScroll);
    if (play) {
      safePlay();
    }
  }
  function now() {
    var raw = pendingSeek != null ? pendingSeek : (audio.currentTime || 0);
    return clipStart != null ? Math.max(0, raw - clipStart) : raw;
  }

  function loadEpisodeFromCatalog(targetSlug, seekSec, autoPlay) {
    var info = CATALOG[targetSlug];
    if (!info) return false;
    seekSec = normalizeSeekTime(seekSec || 0, info);
    if (targetSlug !== slug) {
      slug = info.slug;
      title = info.title;
      duration = info.duration || 0;
      clipStart = info.clip_start != null ? parseFloat(info.clip_start) : null;
      clipEnd = info.clip_end != null ? parseFloat(info.clip_end) : null;
      posKey = makePosKey(slug);
      P.setAttribute("data-slug", slug);
      P.setAttribute("data-title", title);
      P.setAttribute("data-duration", String(duration));
      var tEl = $(".player__title", P), infoLink = $(".player__info", P);
      if (tEl) tEl.textContent = title;
      if (chapEl) { chapEl.textContent = info.label || ""; defaultSub = info.label || ""; }
      var langPrefix = (root.lang && root.lang !== "en") ? "/" + root.lang : "";
      var epHref = langPrefix + "/episodes/" + slug + "/";
      if (infoLink) infoLink.setAttribute("href", epHref);
      if (drawerLink) drawerLink.setAttribute("href", epHref);
      turns = []; starts = []; chaps = []; chapStarts = []; activeTurn = -1; activeChap = -1;
      audio.removeAttribute("data-using-fallback-audio");
      if (info.fallback_audio_url) audio.setAttribute("data-fallback-src", info.fallback_audio_url);
      else audio.removeAttribute("data-fallback-src");
      if (info.fallback_clip_start != null && info.fallback_clip_end != null) {
        audio.setAttribute("data-fallback-clip-start", String(info.fallback_clip_start));
        audio.setAttribute("data-fallback-clip-end", String(info.fallback_clip_end));
      } else {
        audio.removeAttribute("data-fallback-clip-start");
        audio.removeAttribute("data-fallback-clip-end");
      }
      setAudioSrc(info.audio_url);
    }
    if (!turns.length && drawer && drawerBtn) {
      drawer.removeAttribute("hidden");
      drawerBtn.setAttribute("aria-expanded", "true");
    }
    seek(seekSec, autoPlay !== false);
    return true;
  }

  audio.addEventListener("loadedmetadata", function () {
    if (pendingSeek != null) { try { audio.currentTime = pendingSeek; } catch (e) {} pendingSeek = null; }
    else if (clipStart != null && audio.currentTime < clipStart) { try { audio.currentTime = clipStart; } catch (e) {} }
    durEl.textContent = clock(effDuration());
  });
  var lastSave = 0;
  audio.addEventListener("timeupdate", function () {
    var raw = audio.currentTime;
    if (clipEnd != null && raw >= clipEnd) {
      audio.pause();
      playIntended = false;
      return;
    }
    var t = clipStart != null ? Math.max(0, raw - clipStart) : raw;
    sync(t, false);
    if (Date.now() - lastSave > 4000) { lastSave = Date.now(); if (t > 5 && t < effDuration() - 15) store.set(posKey, String(Math.floor(t))); }
  });
  function playLabels(playing) {
    $$("[data-play]").forEach(function (b) {
      var bSlug = b.getAttribute("data-ep-slug");
      var isThisEp = !bSlug || bSlug === slug;
      var s = $("span", b);
      if (s) {
        if (isThisEp) s.textContent = playing ? L("Pause") : L("Resume");
        else s.textContent = L("Play episode");
      }
      b.setAttribute("aria-pressed", (isThisEp && playing) ? "true" : "false");
    });
  }
  audio.addEventListener("play", function () { P.classList.add("is-playing"); toggle.setAttribute("aria-label", L("Pause")); show(); playLabels(true); });
  audio.addEventListener("pause", function () {
    P.classList.remove("is-playing");
    toggle.setAttribute("aria-label", L("Play"));
    playLabels(false);
    var t = now(), d = effDuration();
    if (t > 5 && (d <= 0 || t < d - 10)) store.set(posKey, String(Math.floor(t)));
    else if (d > 0 && t >= d - 10) store.del(posKey);
  });
  audio.addEventListener("waiting", function () { P.classList.add("is-loading"); });
  audio.addEventListener("playing", function () { P.classList.remove("is-loading"); });
  audio.addEventListener("canplay", function () { P.classList.remove("is-loading"); });
  audio.addEventListener("ended", function () { playIntended = false; store.del(posKey); });

  function playPause() {
    show();
    if (audio.paused) {
      if (pendingSeek == null && audio.readyState < 1) {
        var s = parseFloat(store.get(posKey));
        var d = effDuration();
        if (d > 0 && s >= d - 5) { store.del(posKey); s = 0; }
        if (s > 0 && !audio.currentTime) pendingSeek = s;
      }
      safePlay();
    } else {
      playIntended = false;
      audio.pause();
    }
  }
  toggle.addEventListener("click", playPause);
  $('[data-act="back"]', P).addEventListener("click", function () { seek(now() - 15, false); });
  $('[data-act="fwd"]', P).addEventListener("click", function () { seek(Math.min(now() + 15, (audio.duration || duration) - 1), false); });

  function setRate(r) {
    audio.playbackRate = r; audio.defaultPlaybackRate = r;
    speedBtn.textContent = (r % 1 ? String(r) : r) + "×";
    speedBtn.setAttribute("aria-label", L("Playback speed ") + r + L("x. Click to change."));
    store.set(rateKey, String(r));
  }
  var savedRate = parseFloat(store.get(rateKey));
  if (SPEEDS.indexOf(savedRate) > 0) setRate(savedRate);
  speedBtn.addEventListener("click", function () {
    var i = SPEEDS.indexOf(audio.playbackRate); setRate(SPEEDS[(i + 1) % SPEEDS.length]);
  });

  range.addEventListener("input", function () { dragging = true; var t = parseFloat(range.value); cur.textContent = clock(t); range.style.setProperty("--p", (t / (parseFloat(range.max) || 1)) * 100 + "%"); });
  range.addEventListener("change", function () { dragging = false; seek(parseFloat(range.value), !audio.paused); });

  // Home page "Play episode" and episode hero play buttons
  $$("[data-play]").forEach(function (b) {
    b.addEventListener("click", function () {
      var bSlug = b.getAttribute("data-ep-slug");
      if (bSlug && bSlug !== slug) {
        if (CATALOG[bSlug]) {
          loadEpisodeFromCatalog(bSlug, 0, true);
          playLabels(true);
          return;
        }
        var bAudio = b.getAttribute("data-audio-url");
        if (bAudio) {
          slug = bSlug;
          title = b.getAttribute("data-ep-title") || title;
          posKey = makePosKey(slug);
          P.setAttribute("data-slug", slug);
          P.setAttribute("data-title", title);
          var tEl = $(".player__title", P), infoLink = $(".player__info", P);
          if (tEl) tEl.textContent = title;
          if (chapEl && b.getAttribute("data-ep-num")) chapEl.textContent = b.getAttribute("data-ep-num");
          if (infoLink && b.getAttribute("data-ep-url")) infoLink.setAttribute("href", b.getAttribute("data-ep-url"));
          var fb = b.getAttribute("data-en-audio-url");
          if (fb) audio.setAttribute("data-fallback-src", fb);
          else audio.removeAttribute("data-fallback-src");
          setAudioSrc(bAudio);
          seek(0, true);
          playLabels(true);
          return;
        }
      }
      playPause();
    });
  });

  // Timestamp links and chapter links
  doc.addEventListener("click", function (ev) {
    var a = ev.target.closest && ev.target.closest("[data-seek]");
    if (a) {
      ev.preventDefault();
      following = true; if (followBtn) followBtn.hidden = true;
      seek(parseFloat(a.getAttribute("data-seek")), true);
      if (history.replaceState) history.replaceState(null, "", "#t=" + Math.floor(parseFloat(a.getAttribute("data-seek"))));
      return;
    }
    var epLink = ev.target.closest && ev.target.closest('a[href*="/episodes/"][href*="#t="]');
    if (epLink && !ev.metaKey && !ev.ctrlKey && !ev.shiftKey && !ev.altKey) {
      var href = epLink.getAttribute("href") || "";
      var m = /\/episodes\/([^\/#]+)\/?#t=(\d+(?:\.\d+)?)/.exec(href);
      if (m) {
        var tSlug = m[1], tSec = parseFloat(m[2]);
        if (tSlug === slug) {
          ev.preventDefault();
          following = true; if (followBtn) followBtn.hidden = true;
          seek(tSec, true);
          return;
        } else if (CATALOG[tSlug]) {
          ev.preventDefault();
          loadEpisodeFromCatalog(tSlug, tSec, true);
          return;
        }
      }
    }
    var turn = ev.target.closest && ev.target.closest(".turn[data-start]");
    if (turn && !turn.classList.contains("turn--gap") && !ev.target.closest("a, button, summary, details, code")) {
      var sel = window.getSelection && String(window.getSelection());
      if (sel) return; // let people select text
      following = true; if (followBtn) followBtn.hidden = true;
      seek(parseFloat(turn.getAttribute("data-start")), true);
    }
  });

  // Keyboard: space toggles, arrows skip (only when not typing / on a control)
  doc.addEventListener("keydown", function (ev) {
    if (ev.defaultPrevented || ev.metaKey || ev.ctrlKey || ev.altKey) return;
    var tag = (ev.target.tagName || "").toLowerCase();
    if (/^(input|textarea|select|button|a|summary)$/.test(tag) || ev.target.isContentEditable) return;
    if (ev.key === " " || ev.key === "k") { ev.preventDefault(); playPause(); }
    else if (ev.key === "j" || ev.key === "ArrowLeft") { if (P.classList.contains("is-idle")) return; ev.preventDefault(); seek(now() - 15, false); }
    else if (ev.key === "l" || ev.key === "ArrowRight") { if (P.classList.contains("is-idle")) return; ev.preventDefault(); seek(now() + 15, false); }
  });

  // Follow-along: pause auto-scroll when the reader scrolls away, offer a pill to jump back
  if (turnsEl && followBtn) {
    var onUserScroll = function () {
      if (Date.now() - autoScrolling < 900 || audio.paused || activeTurn < 0) return;
      var r = turns[activeTurn].getBoundingClientRect();
      var off = r.bottom < 60 || r.top > window.innerHeight - 80;
      if (off && following) { following = false; followBtn.hidden = false; }
      else if (!off && !following) { following = true; followBtn.hidden = true; }
    };
    window.addEventListener("wheel", onUserScroll, { passive: true });
    window.addEventListener("touchmove", onUserScroll, { passive: true });
    window.addEventListener("scroll", function () { if (!following) onUserScroll(); }, { passive: true });
    followBtn.addEventListener("click", function () { following = true; followBtn.hidden = true; if (activeTurn >= 0) scrollToTurn(turns[activeTurn], true); });
  }

  // Media Session (lock screen / Control Center on iPhone)
  function updateMeta() {
    if (!("mediaSession" in navigator) || !window.MediaMetadata) return;
    try {
      navigator.mediaSession.metadata = new MediaMetadata({
        title: title, artist: "Ask Eve", album: activeChap >= 0 ? chapTitles[activeChap] : "Ask Eve",
        artwork: [{ src: "/icon-512.png", sizes: "512x512", type: "image/png" }]
      });
    } catch (e) {}
  }
  if ("mediaSession" in navigator) {
    updateMeta();
    var ms = navigator.mediaSession, h = function (a, f) { try { ms.setActionHandler(a, f); } catch (e) {} };
    h("play", function () { safePlay(); });
    h("pause", function () { playIntended = false; audio.pause(); });
    h("seekbackward", function (d) { seek(now() - ((d && d.seekOffset) || 15), false); });
    h("seekforward", function (d) { seek(now() + ((d && d.seekOffset) || 15), false); });
    h("seekto", function (d) { if (d && d.seekTime != null) seek(d.seekTime, false); });
    h("previoustrack", function () { var c = idx(chapStarts, now() - 3); seek(c >= 0 ? chapStarts[c] : 0, false); });
    h("nexttrack", function () { var c = idx(chapStarts, now()); if (c + 1 < chapStarts.length) seek(chapStarts[c + 1], false); });
  }

  // Deep links: #t=SECONDS (from people pages and shared links), or resume where you left off
  function fromHash() {
    var m = /^#t=(\d+(?:\.\d+)?)/.exec(location.hash);
    if (!m) return false;
    var t = normalizeSeekTime(parseFloat(m[1]), null);
    show(); pendingSeek = clipStart != null ? clipStart + t : t; sync(t, !restoredLangState);
    return true;
  }
  if (!fromHash()) {
    if (restoredLangState && restoredLangState.audioTime > 0) {
      var rSlug = restoredLangState.audioSlug || slug;
      if (rSlug !== slug && CATALOG[rSlug]) {
        var cDur = parseFloat(CATALOG[rSlug].duration) || 0;
        var oDur = parseFloat(restoredLangState.audioDur) || cDur;
        var cTime = (oDur > 0 && cDur > 0) ? (restoredLangState.audioTime * (cDur / oDur)) : restoredLangState.audioTime;
        if (cDur > 0) cTime = clamp(cTime, 0, Math.max(0, cDur - 5));
        loadEpisodeFromCatalog(rSlug, cTime, !!restoredLangState.audioPlaying);
      } else if (!restoredLangState.audioSlug || restoredLangState.audioSlug === slug) {
        var curDur = effDuration();
        var prevDur = parseFloat(restoredLangState.audioDur) || curDur;
        var sTime = (prevDur > 0 && curDur > 0) ? (restoredLangState.audioTime * (curDur / prevDur)) : restoredLangState.audioTime;
        if (curDur > 0) sTime = clamp(sTime, 0, Math.max(0, curDur - 5));
        seek(sTime, !!restoredLangState.audioPlaying, true);
      }
    } else if (turnsEl) {
      var saved = parseFloat(store.get(posKey));
      var maxD = effDuration();
      if (maxD > 0 && saved >= maxD - 5) { store.del(posKey); saved = 0; }
      if (saved > 5) { pendingSeek = clipStart != null ? clipStart + saved : saved; setRange(saved); }
    }
  }
  window.addEventListener("hashchange", fromHash);
})();
