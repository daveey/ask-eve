/* Runs before first paint: apply the saved theme so there is no flash. */
(function () {
  var d = document.documentElement;
  d.classList.add("js");
  try {
    var t = localStorage.getItem("ae:theme");
    if (t === "light" || t === "dark") d.setAttribute("data-theme", t);
  } catch (e) {}
})();
/* Header menus (language menu + narrow-screen nav menu): close on outside tap, Escape, or opening another menu. */
document.addEventListener("click", function (ev) {
  var menus = document.querySelectorAll("[data-menu][open]");
  for (var i = 0; i < menus.length; i++) {
    if (!menus[i].contains(ev.target)) menus[i].open = false;
  }
});
document.addEventListener("keydown", function (ev) {
  if (ev.key !== "Escape") return;
  var menus = document.querySelectorAll("[data-menu][open]");
  for (var i = 0; i < menus.length; i++) {
    menus[i].open = false;
    var s = menus[i].querySelector("summary");
    if (s) s.focus();
  }
});
