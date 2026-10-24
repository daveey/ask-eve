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

  /* ---------- copy feed URL ---------- */
  $$("[data-copy-feed]").forEach(function (b) {
    b.addEventListener("click", function () {
      var url = b.getAttribute("data-copy-feed"), old = b.textContent;
      var done = function () { b.textContent = L("Copied"); b.classList.add("is-done"); setTimeout(function () { b.textContent = old; b.classList.remove("is-done"); }, 1800); };
      if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(url).then(done, function () { prompt(L("Feed URL"), url); });
      else prompt(L("Feed URL"), url);
    });
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

    var pNode = $("#player audio");
    var audioTime = (pNode && isFinite(pNode.currentTime) && pNode.currentTime > 0) ? pNode.currentTime : 0;
    var audioPlaying = !!(pNode && !pNode.paused);

    return {
      semId: semId,
      tagIndex: bestIdx,
      progress: progress,
      scrollY: window.scrollY || window.pageYOffset || 0,
      hash: location.hash || "",
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

  /* ---------- player ---------- */
  var P = $("#player");
  if (!P) return;
  var audio = $("audio", P), range = $(".range", P), cur = $("[data-cur]", P), durEl = $("[data-dur]", P);
  var toggle = $('[data-act="toggle"]', P), speedBtn = $('[data-act="speed"]', P), chapEl = $("[data-chapter]", P);
  var slug = P.getAttribute("data-slug"), title = P.getAttribute("data-title");
  var duration = parseFloat(P.getAttribute("data-duration")) || 0;
  var posKey = "ae:pos:" + slug, rateKey = "ae:rate";
  var SPEEDS = [1, 1.25, 1.5, 1.75, 2, 0.75];
  var pendingSeek = null, dragging = false, playIntended = false;

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
    if (!fb || audio.getAttribute("data-using-fallback-audio") === "1") return false;
    audio.setAttribute("data-using-fallback-audio", "1");
    var savedTime = pendingSeek != null ? pendingSeek : (audio.currentTime || 0);
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
    audio.src = fb;
    audio.load();
    if (playIntended) {
      audio.play().catch(function () {});
    }
    return true;
  }

  audio.addEventListener("error", function () {
    switchToFallbackAudio();
  });
  $$("source", audio).forEach(function (srcNode) {
    srcNode.addEventListener("error", function () {
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
  function setRange(t) {
    var d = audio.duration && isFinite(audio.duration) ? audio.duration : duration;
    range.max = Math.max(1, Math.floor(d));
    if (!dragging) range.value = Math.floor(t);
    range.style.setProperty("--p", (d ? (Math.min(t, d) / d) * 100 : 0) + "%");
    cur.textContent = clock(t);
    range.setAttribute("aria-valuetext", clock(t) + " of " + clock(d));
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
  }

  function seek(t, play, skipScroll) {
    t = Math.max(0, t);
    show();
    if (audio.readyState >= 1) audio.currentTime = t;
    else { pendingSeek = t; if (play) audio.load(); }
    sync(t, !skipScroll);
    if (play) {
      playIntended = true;
      audio.play().catch(function () { switchToFallbackAudio(); });
    }
  }
  function now() { return pendingSeek != null ? pendingSeek : audio.currentTime || 0; }

  audio.addEventListener("loadedmetadata", function () {
    if (pendingSeek != null) { try { audio.currentTime = pendingSeek; } catch (e) {} pendingSeek = null; }
    if (isFinite(audio.duration)) durEl.textContent = clock(audio.duration);
  });
  var lastSave = 0;
  audio.addEventListener("timeupdate", function () {
    var t = audio.currentTime;
    sync(t, false);
    if (Date.now() - lastSave > 4000) { lastSave = Date.now(); if (t > 5 && (!audio.duration || t < audio.duration - 15)) store.set(posKey, String(Math.floor(t))); }
  });
  function playLabels(playing) {
    $$("[data-play]").forEach(function (b) {
      var s = $("span", b); if (s) s.textContent = playing ? L("Pause") : L("Resume");
      b.setAttribute("aria-pressed", playing ? "true" : "false");
    });
  }
  audio.addEventListener("play", function () { P.classList.add("is-playing"); toggle.setAttribute("aria-label", L("Pause")); show(); playLabels(true); });
  audio.addEventListener("pause", function () { P.classList.remove("is-playing"); toggle.setAttribute("aria-label", L("Play")); playLabels(false); store.set(posKey, String(Math.floor(audio.currentTime))); });
  audio.addEventListener("waiting", function () { P.classList.add("is-loading"); });
  audio.addEventListener("playing", function () { P.classList.remove("is-loading"); });
  audio.addEventListener("canplay", function () { P.classList.remove("is-loading"); });
  audio.addEventListener("ended", function () { playIntended = false; store.del(posKey); });

  function playPause() {
    show();
    if (audio.paused) {
      playIntended = true;
      if (pendingSeek == null && audio.readyState < 1) { var s = parseFloat(store.get(posKey)); if (s > 0 && !audio.currentTime) pendingSeek = s; }
      audio.play().catch(function () { switchToFallbackAudio(); });
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
  $$("[data-play]").forEach(function (b) { b.addEventListener("click", function () { playPause(); }); });

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
    h("play", function () { playIntended = true; audio.play().catch(function () { switchToFallbackAudio(); }); });
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
    var t = parseFloat(m[1]);
    show(); pendingSeek = t; sync(t, !restoredLangState);
    return true;
  }
  if (!fromHash()) {
    if (restoredLangState && restoredLangState.audioTime > 0) {
      seek(restoredLangState.audioTime, !!restoredLangState.audioPlaying, true);
    } else if (turnsEl) {
      var saved = parseFloat(store.get(posKey));
      if (saved > 5) { pendingSeek = saved; setRange(saved); }
    }
  }
  window.addEventListener("hashchange", fromHash);
})();
