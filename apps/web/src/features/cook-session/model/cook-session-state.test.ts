import { describe, expect, it } from "vitest";

import {
  confirmedIngredientCount,
  cookSessionReducer,
  createInitialCookSessionState,
  type CookSessionAction,
  type CookSessionState,
  type RecipeOptionView,
} from "./cook-session-state";

const options: RecipeOptionView[] = [
  {
    id: "palak-paneer",
    name: "Palak Paneer",
    summary: "Spinach gravy with paneer.",
    cuisine: "North Indian",
    totalMinutes: 35,
    difficulty: "easy",
    usedIngredients: ["Spinach", "Paneer"],
    missingIngredients: [],
    optionalIngredients: [],
    nutrition: null,
    previewArtifactId: null,
    previewLabel: null,
    warnings: [],
    batchNumber: 1,
  },
  {
    id: "tomato-rasam",
    name: "Tomato Rasam",
    summary: "A light tomato broth.",
    cuisine: "South Indian",
    totalMinutes: 25,
    difficulty: "medium",
    usedIngredients: ["Tomatoes"],
    missingIngredients: [],
    optionalIngredients: [],
    nutrition: null,
    previewArtifactId: null,
    previewLabel: null,
    warnings: [],
    batchNumber: 1,
  },
];

function stateWithGeneratedContent(): CookSessionState {
  const initial = createInitialCookSessionState();
  return {
    ...initial,
    view: "options",
    maxReached: 3,
    ingredients: [
      {
        id: "detected-spinach",
        name: "Spinach",
        source: "detected",
        confidence: 0.91,
        confirmed: true,
      },
      {
        id: "pantry-salt",
        name: "Salt",
        source: "pantry_suggestion",
        confidence: null,
        confirmed: false,
      },
    ],
    options,
    selectedOptionIds: ["palak-paneer"],
    completeRecipes: {
      "palak-paneer": {
        optionId: "palak-paneer",
        name: "Palak Paneer",
        cuisine: "North Indian",
        servings: 2,
        totalMinutes: 35,
        ingredients: [],
        steps: [],
        tips: [],
        substitutions: [],
        nutritionNotice: "Estimated values; not medical advice.",
        allergenNotice: "Check ingredient labels for allergens.",
        assumptions: [],
        warnings: [],
      },
    },
  };
}

describe("cookSessionReducer", () => {
  it("starts manual entry with an empty confirmed list and unchecked pantry", () => {
    const state = cookSessionReducer(createInitialCookSessionState(), {
      type: "start-manual-entry",
    });

    expect(state.view).toBe("confirm");
    expect(state.mode).toBe("api");
    expect(state.sessionId).toBeNull();
    expect(
      state.ingredients.filter((ingredient) => ingredient.source === "detected"),
    ).toHaveLength(0);
    expect(
      state.ingredients
        .filter((ingredient) => ingredient.source === "pantry_suggestion")
        .every((ingredient) => !ingredient.confirmed),
    ).toBe(true);
    expect(confirmedIngredientCount(state.ingredients)).toBe(0);
  });

  it("adds a trimmed user ingredient as confirmed and rejects a duplicate name", () => {
    const manualState = cookSessionReducer(createInitialCookSessionState(), {
      type: "start-manual-entry",
    });
    const withSpinach = cookSessionReducer(manualState, {
      type: "add-ingredient",
      name: "  Spinach  ",
    });
    const duplicate = cookSessionReducer(withSpinach, {
      type: "add-ingredient",
      name: "spinach",
    });

    expect(
      withSpinach.ingredients.find((ingredient) => ingredient.name === "Spinach"),
    ).toMatchObject({ source: "user_added", confirmed: true, confidence: null });
    expect(confirmedIngredientCount(withSpinach.ingredients)).toBe(1);
    expect(duplicate.ingredients).toEqual(withSpinach.ingredients);
  });

  it("confirms an existing pantry suggestion when it is added by name", () => {
    const manualState = cookSessionReducer(createInitialCookSessionState(), {
      type: "start-manual-entry",
    });
    const withOnion = cookSessionReducer(manualState, {
      type: "add-ingredient",
      name: "onion",
    });

    expect(
      withOnion.ingredients.find((ingredient) => ingredient.name === "Onion"),
    ).toMatchObject({ source: "pantry_suggestion", confirmed: true });
    expect(
      withOnion.ingredients.filter((ingredient) => ingredient.source === "user_added"),
    ).toHaveLength(0);
    expect(confirmedIngredientCount(withOnion.ingredients)).toBe(1);
  });

  it("preserves source and confidence when a detected ingredient is renamed", () => {
    const state = stateWithGeneratedContent();
    const next = cookSessionReducer(state, {
      type: "rename-ingredient",
      id: "detected-spinach",
      name: "Baby spinach",
    });

    expect(next.ingredients[0]).toEqual({
      id: "detected-spinach",
      name: "Baby spinach",
      source: "detected",
      confidence: 0.91,
      confirmed: true,
    });
  });

  it("preserves in-progress whitespace while an ingredient name is edited", () => {
    const state = stateWithGeneratedContent();
    const next = cookSessionReducer(state, {
      type: "rename-ingredient",
      id: "detected-spinach",
      name: "Baby ",
    });

    expect(next.ingredients[0].name).toBe("Baby ");
  });

  it("does not count a blank in-progress ingredient name as confirmed", () => {
    const state = stateWithGeneratedContent();
    const next = cookSessionReducer(state, {
      type: "rename-ingredient",
      id: "detected-spinach",
      name: "   ",
    });

    expect(confirmedIngredientCount(next.ingredients)).toBe(0);
  });

  it("flags a renamed ingredient that duplicates an unchecked pantry suggestion", () => {
    const state = stateWithGeneratedContent();
    const next = cookSessionReducer(state, {
      type: "rename-ingredient",
      id: "detected-spinach",
      name: " salt ",
    });

    expect(next.ingredientNameErrors).toEqual({
      "detected-spinach":
        "Ingredient names must be unique. Rename or remove the duplicate.",
      "pantry-salt": "Ingredient names must be unique. Rename or remove the duplicate.",
    });
  });

  it("invalidates stale options and recipes when confirmed ingredients change", () => {
    const state = stateWithGeneratedContent();
    const next = cookSessionReducer(state, {
      type: "toggle-ingredient",
      id: "pantry-salt",
    });

    expect(next.ingredients[1].confirmed).toBe(true);
    expect(next.options).toEqual([]);
    expect(next.selectedOptionIds).toEqual([]);
    expect(next.completeRecipes).toEqual({});
    expect(next.maxReached).toBe(2);
  });

  it("keeps recipe selections unique and toggles them off", () => {
    const state = stateWithGeneratedContent();
    const selectedRasam = cookSessionReducer(state, {
      type: "toggle-option",
      id: "tomato-rasam",
    });
    const deselectedPalak = cookSessionReducer(selectedRasam, {
      type: "toggle-option",
      id: "palak-paneer",
    });

    expect(selectedRasam.selectedOptionIds).toEqual(["palak-paneer", "tomato-rasam"]);
    expect(deselectedPalak.selectedOptionIds).toEqual(["tomato-rasam"]);
  });

  it("tracks checked method steps independently for each recipe", () => {
    const state = {
      ...stateWithGeneratedContent(),
      completedSteps: { "palak-paneer": [1] },
    };
    const rasamChecked = cookSessionReducer(state, {
      type: "toggle-recipe-step",
      optionId: "tomato-rasam",
      stepNumber: 1,
    });
    const palakUnchecked = cookSessionReducer(rasamChecked, {
      type: "toggle-recipe-step",
      optionId: "palak-paneer",
      stepNumber: 1,
    });

    expect(rasamChecked.completedSteps).toEqual({
      "palak-paneer": [1],
      "tomato-rasam": [1],
    });
    expect(palakUnchecked.completedSteps).toEqual({ "tomato-rasam": [1] });
  });

  it("resets every workflow preference and generated value", () => {
    const state = {
      ...stateWithGeneratedContent(),
      preferences: {
        diet: "vegan" as const,
        servings: 4 as const,
        maxMinutes: 60 as const,
        optionCount: 6 as const,
        allergens: "peanuts",
      },
    };

    expect(cookSessionReducer(state, { type: "reset" })).toEqual(
      createInitialCookSessionState(),
    );
  });

  it("returns to the previous screen with state intact when a job fails", () => {
    const state = cookSessionReducer(stateWithGeneratedContent(), {
      type: "start-job",
      kind: "more-ideas",
      returnView: "options",
      selectedNames: [],
    });
    const failed = cookSessionReducer(state, {
      type: "fail-job",
      error: {
        title: "That took too long",
        message: "Nothing was lost.",
        retryable: true,
      },
    });

    expect(failed.view).toBe("options");
    expect(failed.options).toEqual(options);
    expect(failed.selectedOptionIds).toEqual(["palak-paneer"]);
    expect(failed.job).toBeNull();
    expect(failed.error?.retryable).toBe(true);
  });

  it("clears a stale generation error when the user changes an input", () => {
    const running = cookSessionReducer(stateWithGeneratedContent(), {
      type: "start-job",
      kind: "more-ideas",
      returnView: "options",
      selectedNames: [],
    });
    const failed = cookSessionReducer(running, {
      type: "fail-job",
      error: {
        title: "That took too long",
        message: "Nothing was lost.",
        retryable: true,
      },
    });

    const edited = cookSessionReducer(failed, {
      type: "set-preference",
      key: "maxMinutes",
      value: 60,
    });

    expect(edited.error).toBeNull();
  });

  it("cancels a job back to its previous screen without showing an error", () => {
    const state = cookSessionReducer(stateWithGeneratedContent(), {
      type: "start-job",
      kind: "more-ideas",
      returnView: "options",
      selectedNames: [],
    });
    const cancelled = cookSessionReducer(state, { type: "cancel-job" });

    expect(cancelled.view).toBe("options");
    expect(cancelled.job).toBeNull();
    expect(cancelled.error).toBeNull();
    expect(cancelled.options).toEqual(options);
  });

  it("appends a fresh option batch without clearing an existing selection", () => {
    const state = stateWithGeneratedContent();
    const freshOption = { ...options[0], id: "new-option", batchNumber: 2 };
    const next = cookSessionReducer(state, {
      type: "receive-options",
      options: [freshOption],
      append: true,
    });

    expect(next.options.map((option) => option.id)).toEqual([
      "palak-paneer",
      "tomato-rasam",
      "new-option",
    ]);
    expect(next.selectedOptionIds).toEqual(["palak-paneer"]);
    expect(next.view).toBe("options");
    expect(next.maxReached).toBe(3);
  });

  it("marks ideas exhausted when an API append contains only already-shown options", () => {
    const state = stateWithGeneratedContent();
    const next = cookSessionReducer(state, {
      type: "receive-options",
      options,
      append: true,
    });

    expect(next.options).toEqual(options);
    expect(next.ideasExhausted).toBe(true);
  });

  it("marks server-signalled idea exhaustion without showing a job error", () => {
    const running = cookSessionReducer(stateWithGeneratedContent(), {
      type: "start-job",
      kind: "more-ideas",
      returnView: "options",
      selectedNames: [],
    });
    const next = cookSessionReducer(running, { type: "mark-ideas-exhausted" });

    expect(next.view).toBe("options");
    expect(next.job).toBeNull();
    expect(next.error).toBeNull();
    expect(next.ideasExhausted).toBe(true);
  });

  it("locks recipes and clears old cooking progress after regenerating options", () => {
    const completed = {
      ...stateWithGeneratedContent(),
      view: "recipes" as const,
      maxReached: 4 as const,
      activeRecipeId: "palak-paneer",
      completedSteps: { "palak-paneer": [1] },
    };
    const next = cookSessionReducer(completed, {
      type: "receive-options",
      options: [{ ...options[0], summary: "A newly generated batch." }],
      append: false,
    });
    const attemptedRecipeNavigation = cookSessionReducer(next, {
      type: "navigate",
      view: "recipes",
    });

    expect(next.maxReached).toBe(3);
    expect(next.completeRecipes).toEqual({});
    expect(next.recipeFailures).toEqual({});
    expect(next.activeRecipeId).toBeNull();
    expect(next.completedSteps).toEqual({});
    expect(attemptedRecipeNavigation.view).toBe("options");
  });

  it("opens the first successful selected recipe and retains partial failures", () => {
    const state = {
      ...stateWithGeneratedContent(),
      selectedOptionIds: ["tomato-rasam", "palak-paneer"],
    };
    const palakRecipe = state.completeRecipes["palak-paneer"];
    const next = cookSessionReducer(state, {
      type: "receive-recipes",
      recipes: { "palak-paneer": palakRecipe },
      failures: {
        "tomato-rasam": {
          optionId: "tomato-rasam",
          code: "model_output_invalid",
          message: "The recipe could not be generated.",
          retryable: false,
        },
      },
    });

    expect(next.view).toBe("recipes");
    expect(next.maxReached).toBe(4);
    expect(next.activeRecipeId).toBe("palak-paneer");
    expect(next.recipeFailures["tomato-rasam"]?.message).toContain(
      "could not be generated",
    );
  });

  it("allows navigation across every previously reached step", () => {
    const state = {
      ...stateWithGeneratedContent(),
      view: "recipes" as const,
      maxReached: 4 as const,
    };
    const next = cookSessionReducer(state, { type: "navigate", view: "options" });
    const restoredRecipe = cookSessionReducer(next, {
      type: "navigate",
      view: "recipes",
    });

    expect(next.view).toBe("options");
    expect(next.selectedOptionIds).toEqual(["palak-paneer"]);
    expect(restoredRecipe.view).toBe("recipes");
    expect(restoredRecipe.completeRecipes).toEqual(state.completeRecipes);
  });

  it("rejects a seventh recipe selection with an actionable limit error", () => {
    const expandedOptions = Array.from({ length: 7 }, (_, index) => ({
      ...options[0],
      id: `option-${index + 1}`,
      name: `Option ${index + 1}`,
    }));
    const state = {
      ...stateWithGeneratedContent(),
      options: expandedOptions,
      selectedOptionIds: expandedOptions.slice(0, 6).map((option) => option.id),
    };

    const next = cookSessionReducer(state, {
      type: "toggle-option",
      id: "option-7",
    });

    expect(next.selectedOptionIds).toEqual([
      "option-1",
      "option-2",
      "option-3",
      "option-4",
      "option-5",
      "option-6",
    ]);
    expect(next.error).toEqual({
      title: "Choose up to 6 recipes",
      message: "Deselect one recipe before choosing another.",
      retryable: false,
    });
  });

  it("allows a selected recipe to be replaced after reaching the limit", () => {
    const expandedOptions = Array.from({ length: 7 }, (_, index) => ({
      ...options[0],
      id: `option-${index + 1}`,
      name: `Option ${index + 1}`,
    }));
    const state = {
      ...stateWithGeneratedContent(),
      options: expandedOptions,
      selectedOptionIds: expandedOptions.slice(0, 6).map((option) => option.id),
    };

    const withSpace = cookSessionReducer(state, {
      type: "toggle-option",
      id: "option-1",
    });
    const replaced = cookSessionReducer(withSpace, {
      type: "toggle-option",
      id: "option-7",
    });

    expect(replaced.selectedOptionIds).toEqual([
      "option-2",
      "option-3",
      "option-4",
      "option-5",
      "option-6",
      "option-7",
    ]);
    expect(replaced.error).toBeNull();
  });

  it("loads a reviewed API session without assuming pantry ingredients", () => {
    const state = cookSessionReducer(createInitialCookSessionState(), {
      type: "receive-ingredients",
      mode: "api",
      sessionId: "session-123",
      photoPreviewUrl: "blob:photo",
      weakDetection: false,
      ingredients: [
        {
          id: "detected-tomato",
          name: "Tomato",
          source: "detected",
          confidence: 0.94,
          confirmed: true,
        },
        {
          id: "pantry-salt",
          name: "Salt",
          source: "pantry_suggestion",
          confidence: null,
          confirmed: false,
        },
      ],
    });

    expect(state).toMatchObject({
      view: "confirm",
      maxReached: 2,
      mode: "api",
      sessionId: "session-123",
      photoPreviewUrl: "blob:photo",
    });
    expect(confirmedIngredientCount(state.ingredients)).toBe(1);
  });

  it("invalidates generated content when any recipe preference changes", () => {
    const changes: Array<Extract<CookSessionAction, { type: "set-preference" }>> = [
      { type: "set-preference", key: "diet", value: "vegan" },
      { type: "set-preference", key: "servings", value: 4 },
      { type: "set-preference", key: "maxMinutes", value: 60 },
      { type: "set-preference", key: "optionCount", value: 6 },
      { type: "set-preference", key: "allergens", value: "dairy" },
    ];

    for (const change of changes) {
      const state: CookSessionState = {
        ...stateWithGeneratedContent(),
        view: "recipes",
        maxReached: 4,
        activeRecipeId: "palak-paneer",
        completedSteps: { "palak-paneer": [1] },
        ideasExhausted: true,
        error: {
          title: "Old error",
          message: "This must not survive a valid edit.",
          retryable: false,
        },
      };

      const next = cookSessionReducer(state, change);
      const blockedForwardNavigation = cookSessionReducer(next, {
        type: "navigate",
        view: "recipes",
      });

      expect(next.preferences[change.key]).toBe(change.value);
      expect(next).toMatchObject({
        view: "confirm",
        maxReached: 2,
        options: [],
        selectedOptionIds: [],
        completeRecipes: {},
        recipeFailures: {},
        activeRecipeId: null,
        completedSteps: {},
        ideasExhausted: false,
        error: null,
      });
      expect(blockedForwardNavigation.view).toBe("confirm");
    }
  });

  it("preserves generated content when a preference value is unchanged", () => {
    const state = stateWithGeneratedContent();
    const next = cookSessionReducer(state, {
      type: "set-preference",
      key: "diet",
      value: "vegetarian",
    });

    expect(next).toBe(state);
  });

  it("replaces an API session ID without disturbing reviewed client state", () => {
    const state: CookSessionState = {
      ...stateWithGeneratedContent(),
      view: "confirm",
      maxReached: 2,
      mode: "api",
      sessionId: "session-old",
      options: [],
      selectedOptionIds: [],
      completeRecipes: {},
    };

    const next = cookSessionReducer(state, {
      type: "replace-session-id",
      sessionId: "session-new",
    });

    expect(next).toEqual({ ...state, sessionId: "session-new", error: null });
  });

  it("switches only to a generated recipe that exists", () => {
    const state = stateWithGeneratedContent();
    const validTab = cookSessionReducer(
      {
        ...state,
        completeRecipes: {
          ...state.completeRecipes,
          "tomato-rasam": {
            ...state.completeRecipes["palak-paneer"],
            optionId: "tomato-rasam",
            name: "Tomato Rasam",
          },
        },
      },
      { type: "set-active-recipe", optionId: "tomato-rasam" },
    );
    const invalidTab = cookSessionReducer(validTab, {
      type: "set-active-recipe",
      optionId: "unknown",
    });

    expect(validTab.activeRecipeId).toBe("tomato-rasam");
    expect(invalidTab.activeRecipeId).toBe("tomato-rasam");
  });
});
