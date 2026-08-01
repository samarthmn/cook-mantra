import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { CompleteRecipeView, IngredientSource } from "./cook-session-state";
import {
  loadSavedRecipes,
  removeSavedRecipe,
  SAVED_RECIPES_STORAGE_KEY,
  saveRecipe,
  type SaveRecipeSnapshot,
} from "./saved-recipes";

const recipe: CompleteRecipeView = {
  optionId: "option-1",
  name: "Test Curry",
  cuisine: "Indian",
  servings: 2,
  totalMinutes: 30,
  ingredients: [
    {
      name: "Tomato",
      quantity: "2 medium",
      availability: "available",
      substitution: null,
    },
    {
      name: "Paneer",
      quantity: "200 g",
      availability: "missing",
      substitution: "firm tofu",
    },
  ],
  steps: [
    {
      number: 1,
      instruction: "Simmer the sauce.",
      durationMinutes: 10,
      doneWhen: "the tomatoes are soft",
      heatLevel: "medium",
    },
  ],
  tips: ["Taste before adding salt."],
  substitutions: ["Use tofu for paneer."],
  nutrition: {
    caloriesKcal: 420,
    proteinG: 18,
    carbohydratesG: 25,
    fatG: 28,
    dietTags: ["Vegetarian"],
    allergenWarnings: ["Dairy"],
    disclaimer: "Estimated values; not medical advice.",
  },
  nutritionNotice: "Estimated values; not medical advice.",
  allergenNotice: "Check ingredient labels for allergens.",
  assumptions: ["Salt is available."],
  warnings: ["Contains dairy."],
};

const photo = "data:image/jpeg;base64,c25hcHNob3Q=";
const legacyStorageKey = "cook-mantra:saved-recipes:v1";
const ingredientSources = {
  Tomato: "detected",
  Paneer: "user_added",
} satisfies Record<string, IngredientSource>;
const snapshot: SaveRecipeSnapshot = {
  photo,
  progress: {
    doneStepNumbers: [1],
    ingredientsExpanded: true,
  },
  ingredientSources,
};

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("saved recipes", () => {
  it("round-trips a snapshot and removes it by entry id", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-01T10:30:00.000Z"));

    const saved = saveRecipe(recipe, snapshot);

    expect(saved).toEqual({
      ok: true,
      entries: [
        {
          id: "option-1",
          savedAt: "2026-08-01T10:30:00.000Z",
          recipe,
          photo,
          progress: snapshot.progress,
          ingredientSources,
        },
      ],
    });
    expect(loadSavedRecipes()).toEqual(saved.entries);

    const removed = removeSavedRecipe(saved.entries[0].id);

    expect(removed).toEqual({ ok: true, entries: [] });
    expect(loadSavedRecipes()).toEqual([]);
  });

  it("appends a new version every time the same recipe is saved", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-01T10:30:00.000Z"));
    const first = saveRecipe(recipe, {
      photo: null,
      progress: {
        doneStepNumbers: [],
        ingredientsExpanded: false,
      },
      ingredientSources: null,
    });

    vi.setSystemTime(new Date("2026-08-02T12:00:00.000Z"));
    const newestRecipe = { ...recipe, totalMinutes: 25 };
    const second = saveRecipe(newestRecipe, snapshot);

    expect(first.ok).toBe(true);
    expect(second).toEqual({
      ok: true,
      entries: [
        {
          id: "option-1",
          savedAt: "2026-08-01T10:30:00.000Z",
          recipe,
          photo: null,
          progress: {
            doneStepNumbers: [],
            ingredientsExpanded: false,
          },
          ingredientSources: null,
        },
        {
          id: "option-1-2",
          savedAt: "2026-08-02T12:00:00.000Z",
          recipe: newestRecipe,
          photo,
          progress: snapshot.progress,
          ingredientSources,
        },
      ],
    });
    expect(loadSavedRecipes()).toEqual(second.entries);
  });

  it("migrates entries from the legacy key and removes it after writing v2", () => {
    const legacyEntry = {
      id: "option-1",
      savedAt: "2026-08-01T10:30:00.000Z",
      recipe,
    };
    window.localStorage.setItem(
      legacyStorageKey,
      JSON.stringify({ version: 1, entries: [legacyEntry] }),
    );

    expect(loadSavedRecipes()).toEqual([
      {
        ...legacyEntry,
        photo: null,
        progress: null,
        ingredientSources: null,
      },
    ]);

    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-02T12:00:00.000Z"));
    const saved = saveRecipe(recipe, snapshot);

    expect(saved.ok).toBe(true);
    expect(JSON.parse(window.localStorage.getItem(SAVED_RECIPES_STORAGE_KEY)!)).toEqual(
      {
        version: 2,
        entries: saved.entries,
      },
    );
    expect(window.localStorage.getItem(legacyStorageKey)).toBeNull();
  });

  it("preserves snapshots previously written as v2 under the legacy key", () => {
    const previousSnapshot = {
      id: "option-1",
      savedAt: "2026-08-01T10:30:00.000Z",
      recipe,
      photo,
      progress: snapshot.progress,
    };
    window.localStorage.setItem(
      legacyStorageKey,
      JSON.stringify({ version: 2, entries: [previousSnapshot] }),
    );

    expect(loadSavedRecipes()).toEqual([
      { ...previousSnapshot, ingredientSources: null },
    ]);

    const saved = saveRecipe(recipe, snapshot);

    expect(saved.ok).toBe(true);
    expect(saved.entries[0]).toEqual({
      ...previousSnapshot,
      ingredientSources: null,
    });
    expect(window.localStorage.getItem(legacyStorageKey)).toBeNull();
  });

  it("drops corrupt payloads and malformed entries without throwing", () => {
    window.localStorage.setItem(SAVED_RECIPES_STORAGE_KEY, "{not-json");
    expect(loadSavedRecipes()).toEqual([]);

    const validEntry = {
      id: "saved-test-curry",
      savedAt: "2026-08-01T10:30:00.000Z",
      recipe,
      photo: null,
      progress: null,
      ingredientSources,
    };
    window.localStorage.setItem(
      SAVED_RECIPES_STORAGE_KEY,
      JSON.stringify({
        version: 2,
        entries: [
          validEntry,
          { ...validEntry, savedAt: "last Tuesday" },
          {
            ...validEntry,
            id: "bad-ingredient",
            recipe: {
              ...recipe,
              ingredients: [
                {
                  name: "Tomato",
                  quantity: "2",
                  availability: "nearby",
                  substitution: null,
                },
              ],
            },
          },
          {
            ...validEntry,
            id: "empty-ingredients",
            recipe: { ...recipe, ingredients: [] },
          },
          {
            ...validEntry,
            id: "empty-steps",
            recipe: { ...recipe, steps: [] },
          },
          {
            ...validEntry,
            id: "zero-servings",
            recipe: { ...recipe, servings: 0 },
          },
          {
            ...validEntry,
            id: "negative-servings",
            recipe: { ...recipe, servings: -1 },
          },
          {
            ...validEntry,
            id: "fractional-servings",
            recipe: { ...recipe, servings: 1.5 },
          },
          {
            ...validEntry,
            id: "zero-total-minutes",
            recipe: { ...recipe, totalMinutes: 0 },
          },
          {
            ...validEntry,
            id: "negative-total-minutes",
            recipe: { ...recipe, totalMinutes: -1 },
          },
          {
            ...validEntry,
            id: "zero-step-duration",
            recipe: {
              ...recipe,
              steps: [{ ...recipe.steps[0], durationMinutes: 0 }],
            },
          },
          {
            ...validEntry,
            id: "negative-step-duration",
            recipe: {
              ...recipe,
              steps: [{ ...recipe.steps[0], durationMinutes: -1 }],
            },
          },
          {
            ...validEntry,
            id: "bad-progress-number",
            progress: {
              doneStepNumbers: [1.5],
              ingredientsExpanded: true,
            },
          },
          {
            ...validEntry,
            id: "bad-progress-expanded",
            progress: {
              doneStepNumbers: [1],
              ingredientsExpanded: "yes",
            },
          },
          {
            ...validEntry,
            id: "null-step-duration",
            recipe: {
              ...recipe,
              steps: [{ ...recipe.steps[0], durationMinutes: null }],
            },
          },
          null,
        ],
      }),
    );

    expect(loadSavedRecipes()).toEqual([
      validEntry,
      {
        ...validEntry,
        id: "bad-progress-number",
        progress: null,
      },
      {
        ...validEntry,
        id: "bad-progress-expanded",
        progress: null,
      },
      {
        ...validEntry,
        id: "null-step-duration",
        recipe: {
          ...recipe,
          steps: [{ ...recipe.steps[0], durationMinutes: null }],
        },
      },
    ]);
  });

  it("keeps valid entries when progress is missing or invalid", () => {
    const entry = {
      id: "progress-entry",
      savedAt: "2026-08-01T10:30:00.000Z",
      recipe,
      photo: null,
      ingredientSources: null,
    };
    window.localStorage.setItem(
      SAVED_RECIPES_STORAGE_KEY,
      JSON.stringify({
        version: 2,
        entries: [
          entry,
          {
            ...entry,
            id: "invalid-progress",
            progress: {
              doneStepNumbers: [1.5],
              ingredientsExpanded: true,
            },
          },
        ],
      }),
    );

    expect(loadSavedRecipes()).toEqual([
      { ...entry, progress: null },
      { ...entry, id: "invalid-progress", progress: null },
    ]);
  });

  it("deduplicates progress and drops step numbers absent from the recipe", () => {
    const entry = {
      id: "progress-entry",
      savedAt: "2026-08-01T10:30:00.000Z",
      recipe,
      photo: null,
      ingredientSources: null,
      progress: {
        doneStepNumbers: [1, 4, 1, -2],
        ingredientsExpanded: true,
      },
    };
    window.localStorage.setItem(
      SAVED_RECIPES_STORAGE_KEY,
      JSON.stringify({ version: 2, entries: [entry] }),
    );

    expect(loadSavedRecipes()).toEqual([
      {
        ...entry,
        progress: {
          doneStepNumbers: [1],
          ingredientsExpanded: true,
        },
      },
    ]);
  });

  it("normalizes missing or invalid ingredient provenance to null", () => {
    const entry = {
      id: "source-entry",
      savedAt: "2026-08-01T10:30:00.000Z",
      recipe,
      photo: null,
      progress: null,
    };
    window.localStorage.setItem(
      SAVED_RECIPES_STORAGE_KEY,
      JSON.stringify({
        version: 2,
        entries: [
          entry,
          {
            ...entry,
            id: "invalid-source",
            ingredientSources: { Tomato: "somewhere" },
          },
          {
            ...entry,
            id: "valid-source",
            ingredientSources,
          },
        ],
      }),
    );

    expect(loadSavedRecipes()).toEqual([
      { ...entry, ingredientSources: null },
      { ...entry, id: "invalid-source", ingredientSources: null },
      { ...entry, id: "valid-source", ingredientSources },
    ]);
  });

  it("keeps valid photos and drops invalid or oversized photos without dropping entries", () => {
    const oversizedPhoto = `data:image/jpeg;base64,${"a".repeat(400 * 1024)}`;
    const entry = {
      id: "photo-entry",
      savedAt: "2026-08-01T10:30:00.000Z",
      recipe,
      progress: null,
    };
    window.localStorage.setItem(
      SAVED_RECIPES_STORAGE_KEY,
      JSON.stringify({
        version: 2,
        entries: [
          { ...entry, id: "valid", photo },
          { ...entry, id: "remote", photo: "https://example.com/dish.jpg" },
          { ...entry, id: "oversized", photo: oversizedPhoto },
        ],
      }),
    );

    expect(loadSavedRecipes()).toEqual([
      { ...entry, id: "valid", photo, ingredientSources: null },
      { ...entry, id: "remote", photo: null, ingredientSources: null },
      { ...entry, id: "oversized", photo: null, ingredientSources: null },
    ]);
  });

  it("reads unsupported v2 payloads as empty and refuses to mutate them", () => {
    const unsupportedPayload = JSON.stringify({
      version: 3,
      entries: [
        {
          id: "saved-test-curry",
          savedAt: "2026-08-01T10:30:00.000Z",
          recipe,
          photo: null,
          progress: null,
        },
      ],
    });
    window.localStorage.setItem(SAVED_RECIPES_STORAGE_KEY, unsupportedPayload);

    expect(loadSavedRecipes()).toEqual([]);
    expect(saveRecipe(recipe, snapshot)).toEqual({ ok: false, entries: [] });
    expect(removeSavedRecipe("saved-test-curry")).toEqual({
      ok: false,
      entries: [],
    });
    expect(window.localStorage.getItem(SAVED_RECIPES_STORAGE_KEY)).toBe(
      unsupportedPayload,
    );
  });

  it("retries without the new photo while preserving a non-empty cookbook", () => {
    const existing = saveRecipe(recipe, {
      photo: null,
      progress: null,
      ingredientSources: null,
    });
    expect(existing.ok).toBe(true);

    const originalSetItem = window.localStorage.setItem.bind(window.localStorage);
    const setItem = vi
      .spyOn(window.localStorage, "setItem")
      .mockImplementationOnce(() => {
        throw new DOMException("Storage quota exceeded", "QuotaExceededError");
      })
      .mockImplementation(originalSetItem);

    const saved = saveRecipe(recipe, snapshot);

    expect(setItem).toHaveBeenCalledTimes(2);
    expect(saved.ok).toBe(true);
    expect(saved.entries).toHaveLength(2);
    expect(saved.entries[0]).toEqual(existing.entries[0]);
    expect(saved.entries[1]).toEqual(
      expect.objectContaining({
        recipe,
        photo: null,
        progress: snapshot.progress,
        ingredientSources,
      }),
    );
    expect(loadSavedRecipes()).toEqual(saved.entries);
  });

  it("returns a failure result when localStorage rejects both save attempts", () => {
    const setItem = vi.spyOn(window.localStorage, "setItem").mockImplementation(() => {
      throw new DOMException("Storage quota exceeded", "QuotaExceededError");
    });

    expect(saveRecipe(recipe, snapshot)).toEqual({ ok: false, entries: [] });
    expect(setItem).toHaveBeenCalledTimes(2);
  });

  it("does not access localStorage without a browser window", () => {
    const getItem = vi.spyOn(window.localStorage, "getItem");
    const setItem = vi.spyOn(window.localStorage, "setItem");
    vi.stubGlobal("window", undefined);

    expect(loadSavedRecipes()).toEqual([]);
    expect(saveRecipe(recipe, snapshot)).toEqual({ ok: false, entries: [] });
    expect(removeSavedRecipe("option-1")).toEqual({ ok: false, entries: [] });
    expect(getItem).not.toHaveBeenCalled();
    expect(setItem).not.toHaveBeenCalled();
  });
});
