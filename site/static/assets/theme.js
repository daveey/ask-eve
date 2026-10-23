/* Runs before first paint: apply the saved theme so there is no flash. */
(function () {
  var d = document.documentElement;
  d.classList.add("js");
  try {
    var t = localStorage.getItem("ae:theme");
    if (t === "light" || t === "dark") d.setAttribute("data-theme", t);
  } catch (e) {}
})();
/* Header menu (narrow screens): close on outside tap, Escape, or link tap. */
document.addEventListener("click", function (ev) {
  var m = document.querySelector("[data-menu]");
  if (m && m.open && !m.contains(ev.target)) m.open = false;
});
document.addEventListener("keydown", function (ev) {
  var m = document.querySelector("[data-menu]");
  if (ev.key === "Escape" && m && m.open) { m.open = false; m.querySelector("summary").focus(); }
});
