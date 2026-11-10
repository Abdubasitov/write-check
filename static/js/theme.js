// Переключатель тёмной/светлой темы. Начальную тему ставит inline-скрипт в <head>
// (до отрисовки); здесь — кнопки, сохранение выбора и событие для мини-окон (pip.js).
(function () {
  "use strict";

  var KEY = "zap_theme";  // "dark" | "light"
  var COLORS = { dark: "#141513", light: "#f4f3ee" };  // = --bg каждой темы
  var root = document.documentElement;

  function current() { return root.getAttribute("data-theme") === "light" ? "light" : "dark"; }

  function renderButtons(theme) {
    var label = theme === "light" ? "Включить тёмную тему" : "Включить светлую тему";
    document.querySelectorAll("[data-theme-toggle]").forEach(function (b) {
      b.setAttribute("aria-label", label);
      b.title = label;
      b.setAttribute("aria-pressed", String(theme === "light"));  // нажата = светлая включена
    });
  }

  function apply(theme) {
    root.setAttribute("data-theme", theme);
    var cs = document.querySelector('meta[name="color-scheme"]');
    var tc = document.querySelector('meta[name="theme-color"]');
    if (cs) cs.content = theme;
    if (tc) tc.content = COLORS[theme];
    renderButtons(theme);
    try { localStorage.setItem(KEY, theme); } catch (e) {}
    window.dispatchEvent(new CustomEvent("zap:theme", { detail: theme }));
  }

  document.addEventListener("click", function (e) {
    var btn = e.target.closest && e.target.closest("[data-theme-toggle]");
    if (btn) apply(current() === "light" ? "dark" : "light");
  });

  // выбор в другой вкладке — подхватываем и здесь
  window.addEventListener("storage", function (e) {
    if (e.key === KEY && (e.newValue === "light" || e.newValue === "dark") && e.newValue !== current()) apply(e.newValue);
  });

  renderButtons(current());
  window.Theme = { current: current, colors: COLORS };
})();
