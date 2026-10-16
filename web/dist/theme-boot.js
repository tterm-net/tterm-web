// Runs before the page draws anything: applies the theme remembered from the
// last visit. The account keeps the real choice; this copy only saves the
// flash of the wrong colours while the page asks the account.
(function () {
  var theme = "system";
  try {
    theme = localStorage.getItem("tterm.theme") || "system";
  } catch (e) {
    // Storage can be off; the system theme is a fine guess.
  }
  var light =
    theme === "light" ||
    (theme === "system" &&
      window.matchMedia &&
      window.matchMedia("(prefers-color-scheme: light)").matches);
  document.documentElement.className = light ? "tt-light" : "tt-dark";
})();
