/* Ask Eve: player, transcript sync, theme toggle, small UI helpers. No dependencies, no tracking. */
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
  var reduceMotion = window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches;

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
    var names = { auto: "Auto", light: "Light", dark: "Dark" };
    var lab = $(".theme-btn__label", themeBtn);
    if (lab) lab.textContent = names[t];
    themeBtn.setAttribute("aria-label", "Color theme: " + (t === "auto" ? "system" : t) + ". Click to change.");
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

  /* ---------- copy feed URL ---------- */
  $$("[data-copy-feed]").forEach(function (b) {
    b.addEventListener("click", function () {
      var url = b.getAttribute("data-copy-feed"), old = b.textContent;
      var done = function () { b.textContent = "Copied"; b.classList.add("is-done"); setTimeout(function () { b.textContent = old; b.classList.remove("is-done"); }, 1800); };
      if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(url).then(done, function () { prompt("Feed URL", url); });
      else prompt("Feed URL", url);
    });
  });

  /* ---------- wide screens: open collapsible side boxes ---------- */
  if (window.matchMedia && matchMedia("(min-width: 1024px)").matches) $$("details[data-open-wide]").forEach(function (d) { d.open = true; });

  /* ---------- player ---------- */
  var P = $("#player");
  if (!P) return;
  var audio = $("audio", P), range = $(".range", P), cur = $("[data-cur]", P), durEl = $("[data-dur]", P);
  var toggle = $('[data-act="toggle"]', P), speedBtn = $('[data-act="speed"]', P), chapEl = $("[data-chapter]", P);
  var slug = P.getAttribute("data-slug"), title = P.getAttribute("data-title");
  var duration = parseFloat(P.getAttribute("data-duration")) || 0;
  var posKey = "ae:pos:" + slug, rateKey = "ae:rate";
  var SPEEDS = [1, 1.25, 1.5, 1.75, 2, 0.75];
  var pendingSeek = null, dragging = false;

  // Transcript and chapters (episode page only)
  var turnsEl = $("[data-turns]");
  var turns = turnsEl ? $$(".turn[data-start]", turnsEl).filter(function (t) { return !t.classList.contains("turn--gap"); }) : [];
  var starts = turns.map(function (t) { return parseFloat(t.getAttribute("data-start")); });
  var chaps = $$(".chap[data-seek]");
  var chapStarts = chaps.map(function (c) { return parseFloat(c.getAttribute("data-seek")); });
  var chapTitles = chaps.map(function (c) { var t = $(".chap__title", c); return t ? t.firstChild.textContent : ""; });
  var defaultSub = chapEl ? chapEl.textContent : "";
  var followBtn = $("[data-follow]"), following = true, autoScrolling = 0;
  var activeTurn = -1, activeChap = -1;

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

  function seek(t, play) {
    t = Math.max(0, t);
    show();
    if (audio.readyState >= 1) audio.currentTime = t;
    else { pendingSeek = t; if (play) audio.load(); }
    sync(t, true);
    if (play) audio.play().catch(function () {});
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
      var s = $("span", b); if (s) s.textContent = playing ? "Pause" : "Resume";
      b.setAttribute("aria-pressed", playing ? "true" : "false");
    });
  }
  audio.addEventListener("play", function () { P.classList.add("is-playing"); toggle.setAttribute("aria-label", "Pause"); show(); playLabels(true); });
  audio.addEventListener("pause", function () { P.classList.remove("is-playing"); toggle.setAttribute("aria-label", "Play"); playLabels(false); store.set(posKey, String(Math.floor(audio.currentTime))); });
  audio.addEventListener("waiting", function () { P.classList.add("is-loading"); });
  audio.addEventListener("playing", function () { P.classList.remove("is-loading"); });
  audio.addEventListener("canplay", function () { P.classList.remove("is-loading"); });
  audio.addEventListener("ended", function () { store.del(posKey); });

  function playPause() {
    show();
    if (audio.paused) {
      if (pendingSeek == null && audio.readyState < 1) { var s = parseFloat(store.get(posKey)); if (s > 0 && !audio.currentTime) pendingSeek = s; }
      audio.play().catch(function () {});
    } else audio.pause();
  }
  toggle.addEventListener("click", playPause);
  $('[data-act="back"]', P).addEventListener("click", function () { seek(now() - 15, false); });
  $('[data-act="fwd"]', P).addEventListener("click", function () { seek(Math.min(now() + 15, (audio.duration || duration) - 1), false); });

  function setRate(r) {
    audio.playbackRate = r; audio.defaultPlaybackRate = r;
    speedBtn.textContent = (r % 1 ? String(r) : r) + "×";
    speedBtn.setAttribute("aria-label", "Playback speed " + r + "x. Click to change.");
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
    h("play", function () { audio.play(); });
    h("pause", function () { audio.pause(); });
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
    show(); pendingSeek = t; sync(t, true);
    return true;
  }
  if (!fromHash() && turnsEl) {
    var saved = parseFloat(store.get(posKey));
    if (saved > 5) { pendingSeek = saved; setRange(saved); }
  }
  window.addEventListener("hashchange", fromHash);
})();
