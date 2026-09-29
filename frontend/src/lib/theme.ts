// Light / Dark / System theme. The choice lives in localStorage["luma.theme"]; index.html applies it before first paint
// (same logic as `apply` below) so there is no flash. HeroUI v3 themes on `.dark` / `[data-theme="dark"]` on <html>.
import { useSyncExternalStore } from "react";

export type ThemeMode = "system" | "light" | "dark";
export type ResolvedTheme = "light" | "dark";

const KEY = "luma.theme";
const THEME_COLOR: Record<ResolvedTheme, string> = { light: "#f5f8fe", dark: "#0b1220" };
const listeners = new Set<() => void>();
const media = typeof window !== "undefined" ? window.matchMedia("(prefers-color-scheme: dark)") : null;

export function getMode(): ThemeMode {
  try {
    const v = localStorage.getItem(KEY);
    return v === "light" || v === "dark" ? v : "system";
  } catch {
    return "system";
  }
}

export function resolve(mode: ThemeMode = getMode()): ResolvedTheme {
  return mode === "system" ? (media?.matches ? "dark" : "light") : mode;
}

function apply() {
  const t = resolve();
  const root = document.documentElement;
  root.classList.toggle("dark", t === "dark");
  root.classList.toggle("light", t === "light");
  root.dataset.theme = t;
  root.style.colorScheme = t;
  document.querySelector('meta[name="theme-color"]')?.setAttribute("content", THEME_COLOR[t]);
  listeners.forEach((l) => l());
}

export function setMode(mode: ThemeMode) {
  try {
    if (mode === "system") localStorage.removeItem(KEY);
    else localStorage.setItem(KEY, mode);
  } catch { /* private mode: the choice lasts for this page only */ }
  apply();
}

if (typeof window !== "undefined") {
  apply(); // no-op when /theme-init.js already ran; keeps the theme right if that script was blocked
  media?.addEventListener("change", () => { if (getMode() === "system") apply(); });
  window.addEventListener("storage", (e) => { if (e.key === KEY) apply(); });
}

const subscribe = (l: () => void) => { listeners.add(l); return () => { listeners.delete(l); }; };

/** [mode, setMode, resolved] — re-renders when the user or the OS changes the theme. */
export function useTheme(): [ThemeMode, (m: ThemeMode) => void, ResolvedTheme] {
  const mode = useSyncExternalStore(subscribe, getMode, () => "system" as ThemeMode);
  const resolved = useSyncExternalStore(subscribe, () => resolve(), () => "light" as ResolvedTheme);
  return [mode, setMode, resolved];
}
