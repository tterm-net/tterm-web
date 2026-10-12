// The theme: what the person chose (system, light or dark) and what that
// means right now. The account keeps the choice; a copy in localStorage lets
// theme-boot.js paint the right colours before the account answers.

import type { Theme } from "./api";

export type Shown = "light" | "dark";

const KEY = "tterm.theme";
const LIGHT = "(prefers-color-scheme: light)";

export function systemTheme(): Shown {
  return window.matchMedia?.(LIGHT).matches ? "light" : "dark";
}

export function shown(theme: Theme): Shown {
  return theme === "system" ? systemTheme() : theme;
}

export function applyTheme(theme: Theme): void {
  document.documentElement.className = `tt-${shown(theme)}`;
  try {
    localStorage.setItem(KEY, theme);
  } catch {
    // Storage can be off; the account still has the choice.
  }
}

/** Calls back when the system switches between light and dark. */
export function onSystemThemeChange(callback: () => void): () => void {
  const query = window.matchMedia?.(LIGHT);
  if (!query) return () => {};
  query.addEventListener("change", callback);
  return () => query.removeEventListener("change", callback);
}

/** A convenience kept per browser: whether the sidebar stays open. */
export function rememberedPin(): boolean {
  try {
    return localStorage.getItem("tterm.sidebar.pinned") === "1";
  } catch {
    return false;
  }
}

export function rememberPin(pinned: boolean): void {
  try {
    localStorage.setItem("tterm.sidebar.pinned", pinned ? "1" : "0");
  } catch {
    // Not important enough to mention.
  }
}
