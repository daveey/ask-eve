/* Silent links: a linked name looks like plain text; tapping it opens a small menu of destinations
   (Eve's page, official site / X, Wikipedia, Grokipedia). Data: <script type="application/json" id="xl-data">.
   No third-party code; CSP-safe (no inline handlers, no inline styles in markup). */
(function () {
  "use strict";
  var data = null, menu = null, anchor = null;
  function getData() {
    if (data === null) {
      var s = document.getElementById("xl-data");
      try { data = s ? JSON.parse(s.textContent) : {}; } catch (e) { data = {}; }
    }
    return data;
  }
  function items() { return menu ? Array.prototype.slice.call(menu.querySelectorAll('[role="menuitem"]')) : []; }
  function close(focusBack) {
    if (!menu) return;
    menu.parentNode && menu.parentNode.removeChild(menu);
    menu = null;
    if (anchor) {
      anchor.setAttribute("aria-expanded", "false");
      if (focusBack) anchor.focus();
    }
    anchor = null;
  }
  function place() {
    var sheet = window.matchMedia("(max-width: 600px)").matches;
    menu.classList.toggle("xlm--sheet", sheet);
    if (sheet) return;                                       /* CSS pins the sheet to the bottom */
    var r = anchor.getClientRects()[0] || anchor.getBoundingClientRect();
    var mw = menu.offsetWidth, mh = menu.offsetHeight, vw = document.documentElement.clientWidth, vh = window.innerHeight;
    var left = Math.min(Math.max(8, r.left), vw - mw - 8);
    var top = r.bottom + 6;
    if (top + mh > vh - 8) top = Math.max(8, r.top - mh - 6);
    menu.style.left = left + "px";
    menu.style.top = top + "px";
  }
  function open(a) {
    close(false);
    var ent = getData()[a.getAttribute("data-xl")];
    if (!ent || !ent.items || !ent.items.length) return false;
    anchor = a;
    menu = document.createElement("div");
    menu.className = "xlm";
    menu.setAttribute("role", "menu");
    menu.setAttribute("aria-label", ent.name);
    var h = document.createElement("div");
    h.className = "xlm__h";
    h.setAttribute("aria-hidden", "true");
    h.textContent = ent.name;
    menu.appendChild(h);
    ent.items.forEach(function (it) {
      var m = document.createElement("a");
      m.className = "xlm__i xlm__i--" + it.kind;
      m.setAttribute("role", "menuitem");
      m.tabIndex = -1;
      m.href = it.url;
      if (!/^\//.test(it.url)) { m.target = "_blank"; m.rel = "noopener"; }
      var t = document.createElement("span");
      t.textContent = it.label;
      m.appendChild(t);
      menu.appendChild(m);
    });
    document.body.appendChild(menu);
    a.setAttribute("aria-expanded", "true");
    place();
    var first = items()[0];
    if (first) first.focus({ preventScroll: true });
    return true;
  }
  /* capture phase: runs before the transcript's click-to-seek handler, which must never see these clicks */
  document.addEventListener("click", function (ev) {
    var a = ev.target.closest && ev.target.closest("a.xl");
    if (a) {
      if (ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.button === 1) { ev.stopPropagation(); return; }
      ev.preventDefault();
      ev.stopPropagation();
      if (anchor === a) close(false); else open(a);
      return;
    }
    if (menu && !menu.contains(ev.target)) close(false);
    if (menu && ev.target.closest && ev.target.closest('[role="menuitem"]')) {
      ev.stopPropagation();
      setTimeout(function () { close(false); }, 0);
    }
  }, true);
  document.addEventListener("keydown", function (ev) {
    if (!menu) return;
    var list = items(), i = list.indexOf(document.activeElement);
    if (ev.key === "Escape") { ev.preventDefault(); close(true); }
    else if (ev.key === "ArrowDown") { ev.preventDefault(); list[(i + 1) % list.length].focus(); }
    else if (ev.key === "ArrowUp") { ev.preventDefault(); list[(i - 1 + list.length) % list.length].focus(); }
    else if (ev.key === "Home") { ev.preventDefault(); list[0].focus(); }
    else if (ev.key === "End") { ev.preventDefault(); list[list.length - 1].focus(); }
    else if (ev.key === "Tab") { close(true); }
  });
  window.addEventListener("scroll", function () { if (menu) close(false); }, { passive: true });
  window.addEventListener("resize", function () { if (menu) close(false); });
})();
