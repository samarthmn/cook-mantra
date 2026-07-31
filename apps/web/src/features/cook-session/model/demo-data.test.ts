import { describe, expect, it } from "vitest";

import type { IngredientView, PreferenceView } from "./cook-session-state";
import {
  createDemoOptions,
  createDemoRecipes,
  demoDetectedIngredients,
  demoPantryIngredients,
} from "./demo-data";

const preferences: PreferenceView = {
  diet: "vegetarian",
  servings: 2,
  maxMinutes: 45,
  optionCount: 4,
  allergens: "",
};

describe("demo data", () => {
  it("marks weak detections for review while keeping pantry unchecked", () => {
    const ingredients = [...demoDetectedIngredients(true), ...demoPantryIngredients()];

    expect(ingredients[0]).toMatchObject({
      name: "Tomatoes",
      source: "detected",
      confidence: 0.41,
      confirmed: true,
    });
    expect(
      ingredients
        .filter((ingredient) => ingredient.source === "pantry_suggestion")
        .every((ingredient) => !ingredient.confirmed),
    ).toBe(true);
  });

  it("filters demo options by dietary preference", () => {
    const options = createDemoOptions({
      preferences: { ...preferences, diet: "vegan", maxMinutes: 30 },
      batchNumber: 1,
      excludedIds: [],
      ingredients: [...demoDetectedIngredients(), ...demoPantryIngredients()],
    });

    expect(options.map((option) => option.name)).toEqual([
      "Tomato Rasam",
      "Spinach Tomato Dal",
    ]);
  });

  it("returns no fixed demo dishes when no confirmed ingredient overlaps", () => {
    const ingredients: IngredientView[] = [
      {
        id: "user-banana",
        name: "Banana",
        source: "user_added",
        confidence: null,
        confirmed: true,
      },
      ...demoPantryIngredients(),
    ];

    const options = createDemoOptions({
      preferences,
      batchNumber: 1,
      excludedIds: [],
      ingredients,
    });

    expect(options).toEqual([]);
  });

  it("never marks an unchecked pantry staple as available in a recipe", () => {
    const ingredients: IngredientView[] = [
      ...demoDetectedIngredients(),
      ...demoPantryIngredients(),
    ];
    const options = createDemoOptions({
      preferences,
      batchNumber: 1,
      excludedIds: [],
      ingredients,
    });
    const recipe = createDemoRecipes([options[0]], ingredients, 2)[options[0].id];

    expect(
      recipe.ingredients.find((ingredient) => ingredient.name === "onion, chopped"),
    ).toMatchObject({ availability: "missing" });
  });

  it("marks a pantry staple available only after the user confirms it", () => {
    const ingredients = [
      ...demoDetectedIngredients(),
      ...demoPantryIngredients().map((ingredient) =>
        ingredient.name === "Onion" ? { ...ingredient, confirmed: true } : ingredient,
      ),
    ];
    const options = createDemoOptions({
      preferences,
      batchNumber: 1,
      excludedIds: [],
      ingredients,
    });
    const recipe = createDemoRecipes([options[0]], ingredients, 2)[options[0].id];

    expect(
      recipe.ingredients.find((ingredient) => ingredient.name === "onion, chopped"),
    ).toMatchObject({ availability: "available" });
  });

  it("only describes a substitution as confirmed when the user confirmed it", () => {
    const withoutCurd = [
      ...demoDetectedIngredients().filter((ingredient) => ingredient.name !== "Curd"),
      ...demoPantryIngredients(),
    ];
    const withCurd: IngredientView[] = [
      ...withoutCurd,
      {
        id: "user-curd",
        name: "Curd",
        source: "user_added",
        confidence: null,
        confirmed: true,
      },
    ];
    const options = createDemoOptions({
      preferences,
      batchNumber: 1,
      excludedIds: [],
      ingredients: withoutCurd,
    });

    const unconfirmedRecipe = createDemoRecipes([options[0]], withoutCurd, 2)[
      options[0].id
    ];
    const confirmedRecipe = createDemoRecipes([options[0]], withCurd, 2)[options[0].id];

    expect(unconfirmedRecipe.substitutions).toContain(
      "Cream → whisked curd (curd is not confirmed).",
    );
    expect(confirmedRecipe.substitutions).toContain(
      "Cream → whisked curd (you confirmed curd).",
    );
  });

  it("does not mark fixture ingredients as available unless the user confirmed them", () => {
    const ingredients: IngredientView[] = [
      {
        id: "user-spinach",
        name: "Spinach",
        source: "user_added",
        confidence: null,
        confirmed: true,
      },
      ...demoPantryIngredients(),
    ];
    const options = createDemoOptions({
      preferences,
      batchNumber: 1,
      excludedIds: [],
      ingredients,
    });
    expect(
      options.find((option) => option.name === "Palak Paneer")?.usedIngredients,
    ).toEqual(["Spinach"]);
    expect(options.some((option) => option.name === "Paneer Bhurji")).toBe(false);
    const recipe = createDemoRecipes([options[0]], ingredients, 2)[options[0].id];

    expect(
      recipe.ingredients.find((ingredient) => ingredient.name === "spinach"),
    ).toMatchObject({ availability: "available" });
    expect(
      recipe.ingredients.find((ingredient) => ingredient.name === "paneer"),
    ).toMatchObject({ availability: "missing" });
    expect(
      recipe.ingredients.find((ingredient) => ingredient.name === "tomatoes"),
    ).toMatchObject({ availability: "missing" });
  });

  it("lists every unconfirmed required fixture ingredient as missing from an option", () => {
    const ingredients: IngredientView[] = [
      {
        id: "user-spinach",
        name: "Spinach",
        source: "user_added",
        confidence: null,
        confirmed: true,
      },
      ...demoPantryIngredients(),
    ];
    const option = createDemoOptions({
      preferences,
      batchNumber: 1,
      excludedIds: [],
      ingredients,
    }).find((candidate) => candidate.name === "Palak Paneer");

    expect(option?.missingIngredients.map((ingredient) => ingredient.name)).toEqual([
      "paneer",
      "tomatoes",
      "green chilli",
      "onion, chopped",
      "garlic",
      "oil or ghee",
      "chilli powder",
      "salt",
      "cream",
    ]);
  });

  it("removes a required pantry item from Missing after the user confirms it", () => {
    const ingredients = [
      ...demoDetectedIngredients(),
      ...demoPantryIngredients().map((ingredient) =>
        ingredient.name === "Onion" ? { ...ingredient, confirmed: true } : ingredient,
      ),
    ];
    const option = createDemoOptions({
      preferences,
      batchNumber: 1,
      excludedIds: [],
      ingredients,
    }).find((candidate) => candidate.name === "Palak Paneer");
    const missingNames = option?.missingIngredients.map(
      (ingredient) => ingredient.name,
    );

    expect(missingNames).not.toContain("onion, chopped");
    expect(missingNames).toContain("garlic");
    expect(missingNames?.filter((name) => name === "cream")).toHaveLength(1);
  });

  it("removes a default-missing fixture ingredient after the user confirms it", () => {
    const ingredients: IngredientView[] = [
      ...demoDetectedIngredients(),
      ...demoPantryIngredients(),
      {
        id: "user-cream",
        name: "Cream",
        source: "user_added",
        confidence: null,
        confirmed: true,
      },
    ];
    const option = createDemoOptions({
      preferences,
      batchNumber: 1,
      excludedIds: [],
      ingredients,
    }).find((candidate) => candidate.name === "Palak Paneer");

    expect(
      option?.missingIngredients.map((ingredient) => ingredient.name),
    ).not.toContain("cream");
  });

  it("excludes every option already shown when creating a fresh batch", () => {
    const firstBatch = createDemoOptions({
      preferences,
      batchNumber: 1,
      excludedIds: [],
      ingredients: [...demoDetectedIngredients(), ...demoPantryIngredients()],
    });
    const secondBatch = createDemoOptions({
      preferences,
      batchNumber: 2,
      excludedIds: firstBatch.map((option) => option.id),
      ingredients: [...demoDetectedIngredients(), ...demoPantryIngredients()],
    });

    expect(secondBatch.map((option) => option.name)).toEqual([
      "Spinach Tomato Dal",
      "Paneer-Stuffed Tomatoes",
    ]);
    expect(secondBatch.every((option) => option.batchNumber === 2)).toBe(true);
  });
});
