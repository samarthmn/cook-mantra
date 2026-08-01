"use client";

import { Moon, Sun } from "lucide-react";
import { useEffect, useSyncExternalStore } from "react";

type Theme = "light" | "dark";

const THEME_STORAGE_KEY = "cm-theme";
const THEME_CHANGE_EVENT = "cm-theme-change";
const DARK_THEME_QUERY = "(prefers-color-scheme: dark)";
const THEME_COLORS: Record<Theme, string> = {
  light: "#f3f2f2",
  dark: "#1a1817",
};

export function ThemeToggle() {
  const theme = useSyncExternalStore(
    subscribeToTheme,
    getThemeSnapshot,
    getServerThemeSnapshot,
  );

  useEffect(() => {
    applyTheme(theme);
  }, [theme]);

  const nextTheme: Theme = theme === "dark" ? "light" : "dark";
  const label = `Switch to ${nextTheme} theme`;
  const Icon = theme === "dark" ? Sun : Moon;

  function toggleTheme(): void {
    memoryTheme = nextTheme;
    applyTheme(nextTheme);
    try {
      localStorage.setItem(THEME_STORAGE_KEY, nextTheme);
    } catch {
      // Storage can be blocked (private mode, embedded webviews); the theme
      // still applies for this visit via the in-memory fallback.
    }
    window.dispatchEvent(new Event(THEME_CHANGE_EVENT));
  }

  return (
    <button
      aria-label={label}
      className="btn btn-secondary btn-icon"
      onClick={toggleTheme}
      title={label}
      type="button"
    >
      <Icon aria-hidden="true" size={18} strokeWidth={2} />
    </button>
  );
}

function applyTheme(theme: Theme): void {
  document.documentElement.dataset.theme = theme;
  document
    .querySelector<HTMLMetaElement>('meta[name="theme-color"]')
    ?.setAttribute("content", THEME_COLORS[theme]);
}

function subscribeToTheme(onStoreChange: () => void): () => void {
  const mediaQuery = window.matchMedia(DARK_THEME_QUERY);
  window.addEventListener("storage", onStoreChange);
  window.addEventListener(THEME_CHANGE_EVENT, onStoreChange);
  mediaQuery.addEventListener("change", onStoreChange);

  return () => {
    window.removeEventListener("storage", onStoreChange);
    window.removeEventListener(THEME_CHANGE_EVENT, onStoreChange);
    mediaQuery.removeEventListener("change", onStoreChange);
  };
}

// Fallback for environments where localStorage throws; only ever read after a
// toggle in such an environment.
let memoryTheme: Theme | null = null;

function getThemeSnapshot(): Theme {
  let savedTheme: string | null = null;
  try {
    savedTheme = localStorage.getItem(THEME_STORAGE_KEY);
  } catch {
    // Blocked storage: fall through to the in-memory value or the OS setting.
  }
  if (savedTheme === "light" || savedTheme === "dark") {
    return savedTheme;
  }
  if (memoryTheme) return memoryTheme;
  return window.matchMedia(DARK_THEME_QUERY).matches ? "dark" : "light";
}

function getServerThemeSnapshot(): Theme {
  return "light";
}
