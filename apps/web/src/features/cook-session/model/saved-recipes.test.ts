import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { CompleteRecipeView } from "./cook-session-state";
import {
  isRecipeSaved,
  loadSavedRecipes,
  removeSavedRecipe,
  SAVED_RECIPES_STORAGE_KEY,
  saveRecipe,
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

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("saved recipes", () => {
  it("round-trips a recipe and removes it by entry id", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-01T10:30:00.000Z"));

    const saved = saveRecipe(recipe);

    expect(saved).toEqual({
      ok: true,
      entries: [
        {
          id: "option-1",
          savedAt: "2026-08-01T10:30:00.000Z",
          recipe,
        },
      ],
    });
    expect(loadSavedRecipes()).toEqual(saved.entries);
    expect(isRecipeSaved(saved.entries, recipe)).toBe(true);

    const removed = removeSavedRecipe(saved.entries[0].id);

    expect(removed).toEqual({ ok: true, entries: [] });
    expect(loadSavedRecipes()).toEqual([]);
    expect(isRecipeSaved(removed.entries, recipe)).toBe(false);
  });

  it("updates a saved recipe with the same option id", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-01T10:30:00.000Z"));
    const first = saveRecipe(recipe);

    vi.setSystemTime(new Date("2026-08-02T12:00:00.000Z"));
    const updatedByOptionId = saveRecipe({
      ...recipe,
      totalMinutes: 25,
    });

    expect(first.ok).toBe(true);
    expect(updatedByOptionId).toEqual({
      ok: true,
      entries: [
        {
          id: "option-1",
          savedAt: "2026-08-02T12:00:00.000Z",
          recipe: { ...recipe, totalMinutes: 25 },
        },
      ],
    });
  });

  it("keeps recipes with different option ids even when their names match", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-01T10:30:00.000Z"));
    saveRecipe(recipe);

    vi.setSystemTime(new Date("2026-08-03T14:00:00.000Z"));
    const sameName = {
      ...recipe,
      optionId: "option-2",
      name: "  TEST   CURRY ",
      totalMinutes: 20,
    };
    const saved = saveRecipe(sameName);

    expect(saved).toEqual({
      ok: true,
      entries: [
        {
          id: "option-1",
          savedAt: "2026-08-01T10:30:00.000Z",
          recipe,
        },
        {
          id: "option-2",
          savedAt: "2026-08-03T14:00:00.000Z",
          recipe: sameName,
        },
      ],
    });
    expect(isRecipeSaved(saved.entries, sameName)).toBe(true);
    expect(
      isRecipeSaved(saved.entries, {
        ...sameName,
        optionId: "another-option-id",
      }),
    ).toBe(false);

    expect(removeSavedRecipe(sameName)).toEqual({
      ok: true,
      entries: [saved.entries[0]],
    });
  });

  it("drops corrupt payloads and malformed entries without throwing", () => {
    window.localStorage.setItem(SAVED_RECIPES_STORAGE_KEY, "{not-json");
    expect(loadSavedRecipes()).toEqual([]);

    const validEntry = {
      id: "saved-test-curry",
      savedAt: "2026-08-01T10:30:00.000Z",
      recipe,
    };
    window.localStorage.setItem(
      SAVED_RECIPES_STORAGE_KEY,
      JSON.stringify({
        version: 1,
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
        id: "null-step-duration",
        recipe: {
          ...recipe,
          steps: [{ ...recipe.steps[0], durationMinutes: null }],
        },
      },
    ]);
  });

  it("drops a payload with the wrong version", () => {
    window.localStorage.setItem(
      SAVED_RECIPES_STORAGE_KEY,
      JSON.stringify({
        version: 2,
        entries: [
          {
            id: "saved-test-curry",
            savedAt: "2026-08-01T10:30:00.000Z",
            recipe,
          },
        ],
      }),
    );

    expect(loadSavedRecipes()).toEqual([]);
  });

  it("returns a failure result when localStorage rejects a save", () => {
    vi.spyOn(window.localStorage, "setItem").mockImplementation(() => {
      throw new DOMException("Storage quota exceeded", "QuotaExceededError");
    });

    expect(saveRecipe(recipe)).toEqual({ ok: false, entries: [] });
  });

  it("does not access localStorage without a browser window", () => {
    const getItem = vi.spyOn(window.localStorage, "getItem");
    const setItem = vi.spyOn(window.localStorage, "setItem");
    vi.stubGlobal("window", undefined);

    expect(loadSavedRecipes()).toEqual([]);
    expect(saveRecipe(recipe)).toEqual({ ok: false, entries: [] });
    expect(removeSavedRecipe(recipe)).toEqual({ ok: false, entries: [] });
    expect(getItem).not.toHaveBeenCalled();
    expect(setItem).not.toHaveBeenCalled();
  });
});
