import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ThemeToggle } from "./ThemeToggle";

const originalMatchMedia = globalThis.matchMedia;

function installMemoryStorage(): void {
  const values = new Map<string, string>();
  const storage: Storage = {
    get length() {
      return values.size;
    },
    clear: () => values.clear(),
    getItem: (key) => values.get(key) ?? null,
    key: (index) => [...values.keys()][index] ?? null,
    removeItem: (key) => values.delete(key),
    setItem: (key, value) => values.set(key, String(value)),
  };
  Object.defineProperty(globalThis, "localStorage", {
    configurable: true,
    value: storage,
  });
}

function setSystemTheme(isDark: boolean): void {
  Object.defineProperty(globalThis, "matchMedia", {
    configurable: true,
    writable: true,
    value: vi.fn((query: string) => ({
      matches: isDark,
      media: query,
      onchange: null,
      addEventListener() {},
      removeEventListener() {},
      addListener() {},
      removeListener() {},
      dispatchEvent: () => false,
    })),
  });
}

describe("ThemeToggle", () => {
  beforeEach(() => {
    installMemoryStorage();
    window.localStorage.clear();
    document.documentElement.removeAttribute("data-theme");
    setSystemTheme(false);
  });

  afterEach(() => {
    cleanup();
    Object.defineProperty(globalThis, "matchMedia", {
      configurable: true,
      writable: true,
      value: originalMatchMedia,
    });
  });

  it("uses the saved cm-theme preference before the system preference", async () => {
    window.localStorage.setItem("cm-theme", "dark");
    setSystemTheme(false);

    render(<ThemeToggle />);

    await waitFor(() => {
      expect(document.documentElement).toHaveAttribute("data-theme", "dark");
    });
    expect(
      screen.getByRole("button", { name: "Switch to light theme" }),
    ).toBeInTheDocument();
  });

  it("uses the initial system preference without saving an automatic choice", async () => {
    setSystemTheme(true);

    render(<ThemeToggle />);

    await waitFor(() => {
      expect(document.documentElement).toHaveAttribute("data-theme", "dark");
    });
    expect(window.localStorage.getItem("cm-theme")).toBeNull();
  });

  it("applies and persists each manual light or dark choice", async () => {
    const user = userEvent.setup();
    render(<ThemeToggle />);

    const darkButton = await screen.findByRole("button", {
      name: "Switch to dark theme",
    });
    await user.click(darkButton);

    expect(document.documentElement).toHaveAttribute("data-theme", "dark");
    expect(window.localStorage.getItem("cm-theme")).toBe("dark");

    await user.click(screen.getByRole("button", { name: "Switch to light theme" }));
    expect(document.documentElement).toHaveAttribute("data-theme", "light");
    expect(window.localStorage.getItem("cm-theme")).toBe("light");
  });
});
