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
  previewArtifactId: null,
  previewLabel: null,
};
const legacyRecipe: Record<string, unknown> = { ...recipe };
Reflect.deleteProperty(legacyRecipe, "previewArtifactId");
Reflect.deleteProperty(legacyRecipe, "previewLabel");

const photo = "data:image/jpeg;base64,c25hcHNob3Q=";
const v1StorageKey = "cook-mantra:saved-recipes:v1";
const v2StorageKey = "cook-mantra:saved-recipes:v2";
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

  it("stores only a durable photo and nulls process-private preview fields", () => {
    const liveRecipe = {
      ...recipe,
      previewArtifactId: "process-private-preview",
      previewLabel: "AI-generated image",
      processPrivate: "must-not-be-stored",
      ingredients: [
        {
          ...recipe.ingredients[0],
          processPrivate: "must-not-be-stored",
        },
      ],
      steps: [
        {
          ...recipe.steps[0],
          processPrivate: "must-not-be-stored",
        },
      ],
      nutrition: {
        ...recipe.nutrition!,
        processPrivate: "must-not-be-stored",
      },
    } as unknown as CompleteRecipeView;

    const saved = saveRecipe(liveRecipe, snapshot);
    const serialized = window.localStorage.getItem("cook-mantra:saved-recipes:v3");

    expect(SAVED_RECIPES_STORAGE_KEY).toBe("cook-mantra:saved-recipes:v3");
    expect(saved.ok).toBe(true);
    expect(saved.entries[0]).toMatchObject({
      photo,
      recipe: {
        previewArtifactId: null,
        previewLabel: null,
      },
    });
    expect(saved.entries[0].recipe).not.toHaveProperty("processPrivate");
    expect(saved.entries[0].recipe.ingredients[0]).not.toHaveProperty("processPrivate");
    expect(saved.entries[0].recipe.steps[0]).not.toHaveProperty("processPrivate");
    expect(saved.entries[0].recipe.nutrition).not.toHaveProperty("processPrivate");
    expect(serialized).not.toBeNull();
    expect(serialized).not.toContain("process-private-preview");
    expect(serialized).not.toContain("must-not-be-stored");
    expect(JSON.parse(serialized!)).toMatchObject({
      version: 3,
      entries: [
        {
          photo,
          recipe: {
            previewArtifactId: null,
            previewLabel: null,
          },
        },
      ],
    });
  });

  it("accepts only null preview fields in the v3 payload", () => {
    const validEntry = {
      id: "valid-v3",
      savedAt: "2026-08-01T10:30:00.000Z",
      recipe,
      photo: null,
      progress: null,
      ingredientSources: null,
    };
    window.localStorage.setItem(
      "cook-mantra:saved-recipes:v3",
      JSON.stringify({
        version: 3,
        entries: [
          validEntry,
          {
            ...validEntry,
            id: "missing-preview-fields",
            recipe: legacyRecipe,
          },
          {
            ...validEntry,
            id: "live-preview-fields",
            recipe: {
              ...recipe,
              previewArtifactId: "process-private-preview",
              previewLabel: "AI-generated image",
            },
          },
        ],
      }),
    );

    expect(loadSavedRecipes()).toEqual([validEntry]);
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

  it("hydrates v1 recipes and removes the old key only after writing v3", () => {
    const legacyEntry = {
      id: "option-1",
      savedAt: "2026-08-01T10:30:00.000Z",
      recipe: legacyRecipe,
    };
    window.localStorage.setItem(
      v1StorageKey,
      JSON.stringify({ version: 1, entries: [legacyEntry] }),
    );

    expect(loadSavedRecipes()).toEqual([
      {
        ...legacyEntry,
        recipe: {
          ...legacyRecipe,
          previewArtifactId: null,
          previewLabel: null,
        },
        photo: null,
        progress: null,
        ingredientSources: null,
      },
    ]);
    expect(window.localStorage.getItem(v1StorageKey)).not.toBeNull();
    expect(window.localStorage.getItem(SAVED_RECIPES_STORAGE_KEY)).toBeNull();

    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-02T12:00:00.000Z"));
    const saved = saveRecipe(recipe, snapshot);

    expect(saved.ok).toBe(true);
    expect(JSON.parse(window.localStorage.getItem(SAVED_RECIPES_STORAGE_KEY)!)).toEqual(
      {
        version: 3,
        entries: saved.entries,
      },
    );
    expect(window.localStorage.getItem(v1StorageKey)).toBeNull();
  });

  it("preserves v2 snapshots while hydrating private preview fields to null", () => {
    const previousSnapshot = {
      id: "option-1",
      savedAt: "2026-08-01T10:30:00.000Z",
      recipe: {
        ...legacyRecipe,
        previewArtifactId: "stale-process-preview",
        previewLabel: "AI-generated image",
      },
      photo,
      progress: snapshot.progress,
      ingredientSources,
    };
    window.localStorage.setItem(
      v2StorageKey,
      JSON.stringify({ version: 2, entries: [previousSnapshot] }),
    );
    window.localStorage.setItem(
      v1StorageKey,
      JSON.stringify({
        version: 1,
        entries: [
          {
            id: "older-v1",
            savedAt: "2026-07-01T10:30:00.000Z",
            recipe: legacyRecipe,
          },
        ],
      }),
    );

    expect(loadSavedRecipes()).toEqual([
      {
        ...previousSnapshot,
        recipe: {
          ...legacyRecipe,
          previewArtifactId: null,
          previewLabel: null,
        },
      },
    ]);
    expect(window.localStorage.getItem(v2StorageKey)).not.toBeNull();
    expect(window.localStorage.getItem(v1StorageKey)).not.toBeNull();
    expect(window.localStorage.getItem(SAVED_RECIPES_STORAGE_KEY)).toBeNull();

    const saved = saveRecipe(recipe, snapshot);

    expect(saved.ok).toBe(true);
    expect(saved.entries[0]).toEqual({
      ...previousSnapshot,
      recipe: {
        ...legacyRecipe,
        previewArtifactId: null,
        previewLabel: null,
      },
    });
    expect(window.localStorage.getItem(v2StorageKey)).toBeNull();
    expect(window.localStorage.getItem(v1StorageKey)).toBeNull();
  });

  it("hydrates snapshots from early v2 payloads written under the v1 key", () => {
    const earlyV2Snapshot = {
      id: "early-v2",
      savedAt: "2026-07-15T10:30:00.000Z",
      recipe: legacyRecipe,
      photo,
      progress: snapshot.progress,
      ingredientSources,
    };
    window.localStorage.setItem(
      v1StorageKey,
      JSON.stringify({ version: 2, entries: [earlyV2Snapshot] }),
    );

    expect(loadSavedRecipes()).toEqual([
      {
        ...earlyV2Snapshot,
        recipe: {
          ...legacyRecipe,
          previewArtifactId: null,
          previewLabel: null,
        },
      },
    ]);
    expect(window.localStorage.getItem(v1StorageKey)).not.toBeNull();
    expect(window.localStorage.getItem(SAVED_RECIPES_STORAGE_KEY)).toBeNull();
  });

  it("prefers v3 over v2 and v2 over v1 without mutating storage during reads", () => {
    const v1Entry = {
      id: "from-v1",
      savedAt: "2026-07-01T10:30:00.000Z",
      recipe: legacyRecipe,
    };
    const v2Entry = {
      id: "from-v2",
      savedAt: "2026-07-02T10:30:00.000Z",
      recipe: legacyRecipe,
      photo: null,
      progress: null,
      ingredientSources: null,
    };
    const v3Entry = {
      id: "from-v3",
      savedAt: "2026-07-03T10:30:00.000Z",
      recipe,
      photo: null,
      progress: null,
      ingredientSources: null,
    };
    window.localStorage.setItem(
      v1StorageKey,
      JSON.stringify({ version: 1, entries: [v1Entry] }),
    );
    window.localStorage.setItem(
      v2StorageKey,
      JSON.stringify({ version: 2, entries: [v2Entry] }),
    );

    expect(loadSavedRecipes()).toEqual([
      {
        ...v2Entry,
        recipe: {
          ...legacyRecipe,
          previewArtifactId: null,
          previewLabel: null,
        },
      },
    ]);

    window.localStorage.setItem(
      SAVED_RECIPES_STORAGE_KEY,
      JSON.stringify({ version: 3, entries: [v3Entry] }),
    );

    expect(loadSavedRecipes()).toEqual([v3Entry]);
    expect(window.localStorage.getItem(v1StorageKey)).not.toBeNull();
    expect(window.localStorage.getItem(v2StorageKey)).not.toBeNull();
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
        version: 3,
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
        version: 3,
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
      JSON.stringify({ version: 3, entries: [entry] }),
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
        version: 3,
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
        version: 3,
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

  it("reads unsupported v3 payloads as empty and refuses to mutate them", () => {
    const unsupportedPayload = JSON.stringify({
      version: 4,
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
    window.localStorage.setItem(
      v2StorageKey,
      JSON.stringify({
        version: 2,
        entries: [
          {
            id: "must-not-fall-back",
            savedAt: "2026-07-01T10:30:00.000Z",
            recipe: legacyRecipe,
            photo: null,
            progress: null,
            ingredientSources: null,
          },
        ],
      }),
    );

    expect(loadSavedRecipes()).toEqual([]);
    expect(saveRecipe(recipe, snapshot)).toEqual({ ok: false, entries: [] });
    expect(removeSavedRecipe("saved-test-curry")).toEqual({
      ok: false,
      entries: [],
    });
    expect(window.localStorage.getItem(SAVED_RECIPES_STORAGE_KEY)).toBe(
      unsupportedPayload,
    );
    expect(window.localStorage.getItem(v2StorageKey)).not.toBeNull();
  });

  it("keeps legacy payloads when the v3 replacement write fails", () => {
    const legacyEntry = {
      id: "option-1",
      savedAt: "2026-08-01T10:30:00.000Z",
      recipe: legacyRecipe,
      photo: null,
      progress: null,
      ingredientSources: null,
    };
    const serializedLegacy = JSON.stringify({
      version: 2,
      entries: [legacyEntry],
    });
    window.localStorage.setItem(v2StorageKey, serializedLegacy);
    vi.spyOn(window.localStorage, "setItem").mockImplementation(() => {
      throw new DOMException("Storage quota exceeded", "QuotaExceededError");
    });

    const removed = removeSavedRecipe("option-1");

    expect(removed.ok).toBe(false);
    expect(removed.entries).toHaveLength(1);
    expect(window.localStorage.getItem(v2StorageKey)).toBe(serializedLegacy);
    expect(window.localStorage.getItem(SAVED_RECIPES_STORAGE_KEY)).toBeNull();
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
