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
        nutrition: null,
        nutritionNotice: "Estimated values; not medical advice.",
        allergenNotice: "Check ingredient labels for allergens.",
        assumptions: [],
        warnings: [],
        previewArtifactId: null,
        previewLabel: null,
      },
    },
  };
}

describe("cookSessionReducer", () => {
  it("starts with no cuisine preference", () => {
    expect(createInitialCookSessionState().preferences.cuisines).toEqual([]);
  });

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

  it("uses the current custom pantry defaults for manual entry", () => {
    const customized = cookSessionReducer(createInitialCookSessionState(), {
      type: "set-pantry-staples",
      names: ["Sea salt", "Olive oil"],
    });
    const state = cookSessionReducer(customized, { type: "start-manual-entry" });

    expect(state.pantryStaples).toEqual(["Sea salt", "Olive oil"]);
    expect(state.ingredients).toEqual([
      {
        id: "local-pantry-sea-salt",
        name: "Sea salt",
        source: "pantry_suggestion",
        confidence: null,
        confirmed: false,
      },
      {
        id: "local-pantry-olive-oil",
        name: "Olive oil",
        source: "pantry_suggestion",
        confidence: null,
        confirmed: false,
      },
    ]);
  });

  it("keeps reviewed inputs but invalidates orphaned generated work when a remote rebase switches to manual entry", () => {
    const current = {
      ...stateWithGeneratedContent(),
      view: "job" as const,
      mode: "api" as const,
      sessionId: "session-photo",
      photoPreviewUrl: "blob:committed-photo",
      weakDetection: true,
      job: {
        kind: "ideas" as const,
        returnView: "confirm" as const,
        progress: 0,
        selectedNames: [],
      },
      preferences: {
        ...stateWithGeneratedContent().preferences,
        servings: 4 as const,
        specialInstructions: "Keep it mild",
      },
    };

    const next = cookSessionReducer(current, {
      type: "continue-with-manual-entry",
    });

    expect(next).toMatchObject({
      view: "confirm",
      maxReached: 2,
      mode: "api",
      sessionId: null,
      photoPreviewUrl: null,
      weakDetection: false,
      job: null,
      error: null,
    });
    expect(next.ingredients).toEqual(current.ingredients);
    expect(next.preferences).toEqual(current.preferences);
    expect(next.options).toEqual([]);
    expect(next.selectedOptionIds).toEqual([]);
    expect(next.completeRecipes).toEqual({});
    expect(next.recipeFailures).toEqual({});
    expect(next.activeRecipeId).toBeNull();
    expect(next.completedSteps).toEqual({});
    expect(next.ideasExhausted).toBe(false);
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

  it("selects and deselects every pantry suggestion without changing other ingredients", () => {
    const state = stateWithGeneratedContent();
    const withPartialSelection = cookSessionReducer(state, {
      type: "receive-ingredients",
      mode: "demo",
      sessionId: null,
      photoPreviewUrl: null,
      weakDetection: false,
      ingredients: [
        ...state.ingredients,
        {
          id: "pantry-pepper",
          name: "Pepper powder",
          source: "pantry_suggestion",
          confidence: null,
          confirmed: true,
        },
      ],
    });
    const selected = cookSessionReducer(withPartialSelection, {
      type: "toggle-all-pantry",
      confirmed: true,
    });
    const deselected = cookSessionReducer(selected, {
      type: "toggle-all-pantry",
      confirmed: false,
    });

    expect(
      withPartialSelection.ingredients
        .filter((ingredient) => ingredient.source === "pantry_suggestion")
        .map((ingredient) => ingredient.confirmed),
    ).toContain(true);
    expect(
      withPartialSelection.ingredients
        .filter((ingredient) => ingredient.source === "pantry_suggestion")
        .map((ingredient) => ingredient.confirmed),
    ).toContain(false);
    expect(
      selected.ingredients
        .filter((ingredient) => ingredient.source === "pantry_suggestion")
        .every((ingredient) => ingredient.confirmed),
    ).toBe(true);
    expect(selected.ingredients[0]).toEqual(withPartialSelection.ingredients[0]);
    expect(selected.options).toEqual([]);
    expect(
      deselected.ingredients
        .filter((ingredient) => ingredient.source === "pantry_suggestion")
        .every((ingredient) => !ingredient.confirmed),
    ).toBe(true);
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
        diet: "non-vegetarian" as const,
        dietStyle: "halal" as const,
        dietAddOns: ["keto"],
        servings: 4 as const,
        optionCount: 6 as const,
        allergens: ["Peanuts"],
        cuisines: ["Indian"],
        spiceLevel: "hot" as const,
        specialInstructions: "Use a pressure cooker.",
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
      key: "spiceLevel",
      value: "hot",
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
      merge: false,
    });

    expect(next.view).toBe("recipes");
    expect(next.maxReached).toBe(4);
    expect(next.activeRecipeId).toBe("palak-paneer");
    expect(next.recipeFailures["tomato-rasam"]?.message).toContain(
      "could not be generated",
    );
  });

  it("merges retried recipes without losing successful recipes or checked steps", () => {
    const state: CookSessionState = {
      ...stateWithGeneratedContent(),
      view: "recipes",
      maxReached: 4,
      selectedOptionIds: ["palak-paneer", "tomato-rasam"],
      activeRecipeId: "palak-paneer",
      completedSteps: { "palak-paneer": [1, 2] },
      recipeFailures: {
        "tomato-rasam": {
          optionId: "tomato-rasam",
          code: "model_output_invalid",
          message: "The first attempt failed.",
          retryable: true,
        },
      },
    };
    const tomatoRecipe = {
      ...state.completeRecipes["palak-paneer"],
      optionId: "tomato-rasam",
      name: "Tomato Rasam",
    };

    const next = cookSessionReducer(state, {
      type: "receive-recipes",
      recipes: { "tomato-rasam": tomatoRecipe },
      failures: {},
      merge: true,
    });

    expect(next.completeRecipes).toEqual({
      "palak-paneer": state.completeRecipes["palak-paneer"],
      "tomato-rasam": tomatoRecipe,
    });
    expect(next.recipeFailures).toEqual({});
    expect(next.completedSteps).toEqual({ "palak-paneer": [1, 2] });
    expect(next.activeRecipeId).toBe("palak-paneer");
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

  it("merges every missing pantry default into a reviewed API session", () => {
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
    expect(
      state.ingredients
        .filter((ingredient) => ingredient.source === "pantry_suggestion")
        .map((ingredient) => ingredient.name),
    ).toEqual([
      "Salt",
      "Pepper powder",
      "Oil or ghee",
      "Chilli powder",
      "Onion",
      "Garlic",
      "Ginger",
    ]);
  });

  it("does not duplicate pantry defaults that match any ingredient name", () => {
    const customized = cookSessionReducer(createInitialCookSessionState(), {
      type: "set-pantry-staples",
      names: ["Tomato", "Sea salt", "Oil!", "Oil?"],
    });
    const state = cookSessionReducer(customized, {
      type: "receive-ingredients",
      mode: "api",
      sessionId: "session-123",
      photoPreviewUrl: null,
      weakDetection: false,
      ingredients: [
        {
          id: "pantry-oil",
          name: " tomato ",
          source: "detected",
          confidence: 0.94,
          confirmed: true,
        },
        {
          id: "server-pantry-salt",
          name: "SEA SALT",
          source: "pantry_suggestion",
          confidence: null,
          confirmed: false,
        },
      ],
    });

    expect(state.ingredients.map((ingredient) => ingredient.name)).toEqual([
      " tomato ",
      "SEA SALT",
      "Oil!",
      "Oil?",
    ]);
    expect(new Set(state.ingredients.map((ingredient) => ingredient.id)).size).toBe(
      state.ingredients.length,
    );
  });

  it("updates pantry defaults on a pristine upload without advancing the workflow", () => {
    const state = cookSessionReducer(createInitialCookSessionState(), {
      type: "set-pantry-staples",
      names: ["  Sea   salt ", "Olive oil", "sea salt"],
    });

    expect(state.pantryStaples).toEqual(["Sea salt", "Olive oil"]);
    expect(state.view).toBe("upload");
    expect(state.maxReached).toBe(1);
    expect(state.ingredients).toEqual([]);
  });

  it("reconciles current pantry rows immediately while preserving non-pantry rows", () => {
    const generated = stateWithGeneratedContent();
    const state: CookSessionState = {
      ...generated,
      ingredients: [
        generated.ingredients[0],
        { ...generated.ingredients[1], confirmed: true },
        {
          id: "user-basil",
          name: "Basil",
          source: "user_added",
          confidence: null,
          confirmed: true,
        },
        {
          id: "pantry-pepper",
          name: "Pepper powder",
          source: "pantry_suggestion",
          confidence: null,
          confirmed: false,
        },
      ],
    };
    const next = cookSessionReducer(state, {
      type: "set-pantry-staples",
      names: ["salt", "Sea salt"],
    });

    expect(next.pantryStaples).toEqual(["salt", "Sea salt"]);
    expect(next.ingredients).toEqual([
      state.ingredients[0],
      state.ingredients[2],
      {
        ...state.ingredients[1],
        name: "salt",
      },
      {
        id: "local-pantry-sea-salt",
        name: "Sea salt",
        source: "pantry_suggestion",
        confidence: null,
        confirmed: false,
      },
    ]);
    expect(next.options).toEqual([]);
    expect(next.view).toBe("confirm");
  });

  it("preserves custom pantry defaults across an in-app reset", () => {
    const state: CookSessionState = {
      ...stateWithGeneratedContent(),
      pantryStaples: ["Sea salt", "Olive oil"],
    };

    const reset = cookSessionReducer(state, { type: "reset" });

    expect(reset).toEqual({
      ...createInitialCookSessionState(),
      pantryStaples: ["Sea salt", "Olive oil"],
    });
  });

  it("keeps locally generated ingredient IDs unique after receiving server rows", () => {
    const received = cookSessionReducer(createInitialCookSessionState(), {
      type: "receive-ingredients",
      mode: "api",
      sessionId: "session-123",
      photoPreviewUrl: null,
      weakDetection: false,
      ingredients: [
        {
          id: "user-1",
          name: "Tomato",
          source: "user_added",
          confidence: null,
          confirmed: true,
        },
      ],
    });
    const next = cookSessionReducer(received, {
      type: "add-ingredient",
      name: "Basil",
    });

    expect(next.ingredients.find((ingredient) => ingredient.name === "Basil")?.id).toBe(
      "user-2",
    );
  });

  it("normalizes diet styles and allowed add-ons", () => {
    const withVegan = cookSessionReducer(stateWithGeneratedContent(), {
      type: "set-preference",
      key: "dietStyle",
      value: "vegan",
    });
    const withInvalidStyle = cookSessionReducer(withVegan, {
      type: "set-preference",
      key: "dietStyle",
      value: "halal",
    });
    const withAddOns = cookSessionReducer(withInvalidStyle, {
      type: "set-preference",
      key: "dietAddOns",
      value: [" KETO ", "keto", "No onion or garlic", "vegan", ""],
    });

    expect(withVegan.preferences.dietStyle).toBe("vegan");
    expect(withInvalidStyle.preferences.dietStyle).toBeNull();
    expect(withAddOns.preferences.dietAddOns).toEqual(["keto", "no onion or garlic"]);
  });

  it("resets style but keeps add-ons when the main diet changes", () => {
    const withPreferences: CookSessionState = {
      ...stateWithGeneratedContent(),
      preferences: {
        ...stateWithGeneratedContent().preferences,
        dietStyle: "vegan",
        dietAddOns: ["keto", "no onion or garlic"],
      },
    };
    const nonVegetarian = cookSessionReducer(withPreferences, {
      type: "set-preference",
      key: "diet",
      value: "non-vegetarian",
    });

    expect(nonVegetarian.preferences).toMatchObject({
      diet: "non-vegetarian",
      dietStyle: null,
      dietAddOns: ["keto", "no onion or garlic"],
    });
  });

  it("normalizes allergen entries and limits special instructions to 500 characters", () => {
    const withAllergens = cookSessionReducer(stateWithGeneratedContent(), {
      type: "set-preference",
      key: "allergens",
      value: [" Dairy ", "dairy", "Tree   nuts", "TREE NUTS", ""],
    });
    const withInstructions = cookSessionReducer(withAllergens, {
      type: "set-preference",
      key: "specialInstructions",
      value: "x".repeat(510),
    });
    const withSpice = cookSessionReducer(withInstructions, {
      type: "set-preference",
      key: "spiceLevel",
      value: "extra-hot",
    });

    expect(withAllergens.preferences.allergens).toEqual(["Dairy", "Tree nuts"]);
    expect(withInstructions.preferences.specialInstructions).toHaveLength(500);
    expect(withSpice.preferences.spiceLevel).toBe("extra-hot");
  });

  it("normalizes, deduplicates, and caps cuisine preferences", () => {
    const state = cookSessionReducer(stateWithGeneratedContent(), {
      type: "set-preference",
      key: "cuisines",
      value: [
        " Indian ",
        "indian",
        "Middle   Eastern",
        ...Array.from({ length: 25 }, (_, index) => `Cuisine ${index + 1}`),
      ],
    });

    expect(state.preferences.cuisines).toHaveLength(20);
    expect(state.preferences.cuisines.slice(0, 3)).toEqual([
      "Indian",
      "Middle Eastern",
      "Cuisine 1",
    ]);
  });

  it("invalidates generated content when any recipe preference changes", () => {
    const changes: Array<Extract<CookSessionAction, { type: "set-preference" }>> = [
      { type: "set-preference", key: "diet", value: "non-vegetarian" },
      { type: "set-preference", key: "dietStyle", value: "vegan" },
      { type: "set-preference", key: "dietAddOns", value: ["keto"] },
      { type: "set-preference", key: "servings", value: 4 },
      { type: "set-preference", key: "optionCount", value: 6 },
      { type: "set-preference", key: "allergens", value: ["dairy"] },
      { type: "set-preference", key: "cuisines", value: ["Indian"] },
      { type: "set-preference", key: "spiceLevel", value: "hot" },
      {
        type: "set-preference",
        key: "specialInstructions",
        value: "Kid friendly",
      },
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

      expect(next.preferences[change.key]).toEqual(change.value);
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

  it("treats normalized-equivalent cuisine preferences as unchanged", () => {
    const state: CookSessionState = {
      ...stateWithGeneratedContent(),
      preferences: {
        ...stateWithGeneratedContent().preferences,
        cuisines: ["Middle Eastern"],
      },
    };
    const next = cookSessionReducer(state, {
      type: "set-preference",
      key: "cuisines",
      value: ["  Middle   Eastern ", "middle eastern"],
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
