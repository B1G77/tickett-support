// Light/dark: a saved choice wins, otherwise the device setting (handled in theme.css).

export type Theme = "light" | "dark";
const KEY = "tickett-theme";

export function savedTheme(): Theme | null {
  try {
    const v = localStorage.getItem(KEY);
    return v === "light" || v === "dark" ? v : null;
  } catch {
    return null;
  }
}

export function currentTheme(): Theme {
  const set = document.documentElement.dataset.theme;
  if (set === "light" || set === "dark") return set;
  return window.matchMedia?.("(prefers-color-scheme: light)").matches ? "light" : "dark";
}

export function applyTheme(theme: Theme | null): void {
  if (theme) document.documentElement.dataset.theme = theme;
  else delete document.documentElement.dataset.theme;
}

export function saveTheme(theme: Theme): void {
  applyTheme(theme);
  try {
    localStorage.setItem(KEY, theme);
  } catch {
    // private window or blocked storage: the choice lasts until reload
  }
}
