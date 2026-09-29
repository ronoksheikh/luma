// Apply the saved theme (System / Light / Dark) before first paint so there is no flash. Keep in sync with src/lib/theme.ts.
(function () {
  var t = "light";
  try {
    var m = localStorage.getItem("luma.theme");
    t = m === "dark" || (m !== "light" && window.matchMedia("(prefers-color-scheme: dark)").matches) ? "dark" : "light";
  } catch (e) {}
  var r = document.documentElement;
  r.classList.add(t);
  r.setAttribute("data-theme", t);
  r.style.colorScheme = t;
  var c = document.querySelector('meta[name="theme-color"]');
  if (c) c.setAttribute("content", t === "dark" ? "#0b1220" : "#f5f8fe");
})();
