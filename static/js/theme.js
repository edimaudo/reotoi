(() => {
  "use strict";

  const root = document.documentElement;
  const themeKey = "reotoi-theme";
  const fontKey = "reotoi-font-size";

  const validThemes = new Set(["light", "dark"]);
  const validFontSizes = new Set(["small", "medium", "large"]);

  function applyTheme(theme) {
    if (!validThemes.has(theme)) return;
    root.dataset.theme = theme;
    const toggle = document.getElementById("theme-toggle");
    const icon = document.getElementById("theme-icon");
    if (toggle) toggle.setAttribute("aria-pressed", String(theme === "dark"));
    if (icon) icon.textContent = theme === "dark" ? "☀" : "☾";
    localStorage.setItem(themeKey, theme);
  }

  function applyFontSize(size) {
    if (!validFontSizes.has(size)) return;
    root.dataset.fontSize = size;
    document.querySelectorAll("[data-font-size]").forEach((button) => {
      button.classList.toggle("is-active", button.dataset.fontSize === size);
    });
    localStorage.setItem(fontKey, size);
  }

  const savedTheme = localStorage.getItem(themeKey);
  const savedFont = localStorage.getItem(fontKey);
  const preferredTheme =
    savedTheme ||
    (window.matchMedia("(prefers-color-scheme: dark)").matches
      ? "dark"
      : "light");

  applyTheme(preferredTheme);
  applyFontSize(validFontSizes.has(savedFont) ? savedFont : "medium");

  document.getElementById("theme-toggle")?.addEventListener("click", () => {
    applyTheme(root.dataset.theme === "dark" ? "light" : "dark");
  });

  document
    .querySelectorAll(".control-button[data-font-size]")
    .forEach((button) => {
      button.addEventListener("click", () =>
        applyFontSize(button.dataset.fontSize)
      );
    });
})();
