import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { AppHeader } from "./AppHeader";

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

describe("AppHeader", () => {
  beforeEach(() => {
    installMemoryStorage();
    window.localStorage.clear();
    document.documentElement.removeAttribute("data-theme");
  });

  afterEach(() => cleanup());

  it("links the brand home and exposes the theme action", () => {
    render(<AppHeader />);

    const header = screen.getByRole("banner");
    const brand = within(header).getByRole("link", { name: "COOK MANTRA" });

    expect(header).toHaveClass("app-header");
    expect(brand).toHaveAttribute("href", "/");
    expect(brand).toHaveClass("app-brand");
    const mark = brand.querySelector(".app-brand-mark");
    expect(mark).toHaveAttribute("aria-hidden", "true");
    expect(mark?.tagName).toBe("IMG");
    // Decorative: the adjacent wordmark already names the link.
    expect(mark).toHaveAttribute("alt", "");
    expect(mark?.getAttribute("src")).toContain("cook-mantra-mark");
    // The design guide stays in the codebase as a reference, but is not linked
    // from the product UI.
    expect(
      within(header).queryByRole("link", { name: "Design guide" }),
    ).not.toBeInTheDocument();
    expect(
      within(header).getByRole("button", { name: "Switch to dark theme" }),
    ).toBeInTheDocument();
  });
});
