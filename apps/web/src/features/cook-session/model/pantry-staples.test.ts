import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  PANTRY_STAPLES,
  loadPantryStaples,
  normalizePantryStaples,
  savePantryStaples,
} from "./pantry-staples";

class MemoryStorage implements Storage {
  private readonly values = new Map<string, string>();

  get length(): number {
    return this.values.size;
  }

  clear(): void {
    this.values.clear();
  }

  getItem(key: string): string | null {
    return this.values.get(key) ?? null;
  }

  key(index: number): string | null {
    return [...this.values.keys()][index] ?? null;
  }

  removeItem(key: string): void {
    this.values.delete(key);
  }

  setItem(key: string, value: string): void {
    this.values.set(key, String(value));
  }
}

class ThrowingReadStorage extends MemoryStorage {
  override getItem(): string | null {
    throw new Error("storage unavailable");
  }
}

class ThrowingWriteStorage extends MemoryStorage {
  override setItem(): void {
    throw new Error("quota exceeded");
  }
}

function installStorage(storage: Storage = new MemoryStorage()): void {
  Object.defineProperty(globalThis, "localStorage", {
    configurable: true,
    value: storage,
  });
}

describe("pantry staples persistence", () => {
  beforeEach(() => {
    installStorage();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    installStorage();
  });

  it("round-trips normalized, case-insensitively deduplicated staples", () => {
    expect(
      savePantryStaples(["  Kosher   salt  ", "EXTRA\tvirgin oil", "kosher salt"]),
    ).toBe(true);

    expect(localStorage.getItem("cook-mantra:pantry-staples")).toBe(
      '["Kosher salt","EXTRA virgin oil"]',
    );
    expect(loadPantryStaples()).toEqual(["Kosher salt", "EXTRA virgin oil"]);
  });

  it("preserves the first spelling while normalizing whitespace", () => {
    expect(
      normalizePantryStaples(["  Garam   Masala ", "garam masala", " Cumin "]),
    ).toEqual(["Garam Masala", "Cumin"]);
  });

  it("uses built-ins when normalization leaves no valid names", () => {
    expect(normalizePantryStaples(["  ", "x".repeat(81)])).toEqual([...PANTRY_STAPLES]);
  });

  it.each([
    ["missing", null],
    ["malformed JSON", "not-json"],
    ["a non-array", '{"name":"Salt"}'],
    ["an empty array", "[]"],
    ["a blank name", '["   "]'],
    ["a non-string name", '["Salt",42]'],
    ["a name over 80 characters", JSON.stringify(["x".repeat(81)])],
  ])("falls back to built-ins for %s storage", (_label, storedValue) => {
    if (storedValue !== null) {
      localStorage.setItem("cook-mantra:pantry-staples", storedValue);
    }

    expect(loadPantryStaples()).toEqual([
      "Salt",
      "Pepper powder",
      "Oil or ghee",
      "Chilli powder",
      "Onion",
      "Garlic",
      "Ginger",
    ]);
  });

  it("returns a fresh built-in fallback array", () => {
    const loaded = loadPantryStaples();
    loaded.push("Cumin");

    expect(PANTRY_STAPLES).not.toContain("Cumin");
    expect(loadPantryStaples()).not.toContain("Cumin");
  });

  it("falls back when reading storage throws", () => {
    installStorage(new ThrowingReadStorage());

    expect(loadPantryStaples()).toEqual([...PANTRY_STAPLES]);
  });

  it("returns false without throwing when writing storage fails", () => {
    installStorage(new ThrowingWriteStorage());

    expect(savePantryStaples(["Salt", "Cumin"])).toBe(false);
  });

  it("rejects invalid values without replacing saved defaults", () => {
    expect(savePantryStaples(["Cumin"])).toBe(true);

    expect(savePantryStaples([])).toBe(false);
    expect(savePantryStaples(["Salt", 42] as unknown as string[])).toBe(false);
    expect(loadPantryStaples()).toEqual(["Cumin"]);
  });

  it("uses built-ins and skips writes during SSR", () => {
    vi.stubGlobal("window", undefined);

    expect(loadPantryStaples()).toEqual([...PANTRY_STAPLES]);
    expect(savePantryStaples(["Cumin"])).toBe(false);
  });
});
