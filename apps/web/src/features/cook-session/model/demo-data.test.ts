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
  dietStyle: null,
  dietAddOns: [],
  servings: 2,
  optionCount: 4,
  allergens: [],
  cuisines: [],
  spiceLevel: "medium",
  specialInstructions: "",
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
      preferences: { ...preferences, dietStyle: "vegan" },
      batchNumber: 1,
      excludedIds: [],
      ingredients: [...demoDetectedIngredients(), ...demoPantryIngredients()],
    });

    expect(options.map((option) => option.name)).toEqual([
      "Tomato Rasam",
      "Spinach Tomato Dal",
    ]);
  });

  it("returns plausible demo fixtures for a non-vegetarian preference", () => {
    const options = createDemoOptions({
      preferences: { ...preferences, diet: "non-vegetarian" },
      batchNumber: 1,
      excludedIds: [],
      ingredients: [...demoDetectedIngredients(), ...demoPantryIngredients()],
    });

    expect(options.map((option) => option.name)).toEqual([
      "Egg Bhurji",
      "Chicken Saag",
      "Pepper Chicken",
    ]);
    expect(
      Object.values(
        createDemoRecipes(
          options,
          [...demoDetectedIngredients(), ...demoPantryIngredients()],
          2,
        ),
      ).map((recipe) => recipe.name),
    ).toEqual(["Egg Bhurji", "Chicken Saag", "Pepper Chicken"]);
  });

  it("does not filter demo options by total cooking time", () => {
    const options = createDemoOptions({
      preferences: { ...preferences, optionCount: 6 },
      batchNumber: 1,
      excludedIds: [],
      ingredients: [...demoDetectedIngredients(), ...demoPantryIngredients()],
    });

    expect(options.map((option) => option.name)).toContain("Paneer-Stuffed Tomatoes");
    expect(
      options.find((option) => option.name === "Paneer-Stuffed Tomatoes")?.totalMinutes,
    ).toBe(40);
  });

  it("filters demo options against the allergen array", () => {
    const options = createDemoOptions({
      preferences: { ...preferences, optionCount: 6, allergens: ["Dairy"] },
      batchNumber: 1,
      excludedIds: [],
      ingredients: [...demoDetectedIngredients(), ...demoPantryIngredients()],
    });

    expect(options.map((option) => option.name)).toEqual([
      "Tomato Rasam",
      "Spinach Tomato Dal",
    ]);
  });

  it("filters demo options by a sample cuisine preference", () => {
    const options = createDemoOptions({
      preferences: { ...preferences, cuisines: ["Indian"] },
      batchNumber: 1,
      excludedIds: [],
      ingredients: [...demoDetectedIngredients(), ...demoPantryIngredients()],
    });

    expect(options.map((option) => option.name)).toEqual([
      "Palak Paneer",
      "Paneer Bhurji",
      "Tomato Rasam",
      "Spinach Tomato Dal",
    ]);
  });

  it("falls back to eligible demo options when no fixture matches the cuisine", () => {
    const options = createDemoOptions({
      preferences: { ...preferences, cuisines: ["Thai"] },
      batchNumber: 1,
      excludedIds: [],
      ingredients: [...demoDetectedIngredients(), ...demoPantryIngredients()],
    });

    expect(options.map((option) => option.name)).toEqual([
      "Palak Paneer",
      "Paneer Bhurji",
      "Tomato Rasam",
      "Chilli Paneer",
    ]);
  });

  it("creates unchecked demo pantry rows from custom defaults", () => {
    expect(demoPantryIngredients(["Sea salt", "Olive oil"])).toEqual([
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
      recipe.ingredients.find((ingredient) => ingredient.name === "onion"),
    ).toMatchObject({ availability: "missing" });
  });

  it("includes doneness and heat guidance in sample recipe steps", () => {
    const ingredients = [...demoDetectedIngredients(), ...demoPantryIngredients()];
    const options = createDemoOptions({
      preferences,
      batchNumber: 1,
      excludedIds: [],
      ingredients,
    });
    const recipe = createDemoRecipes([options[0]], ingredients, 2)[options[0].id];

    expect(recipe.steps[2]).toMatchObject({
      doneWhen: "the onion is soft and translucent with no raw garlic smell",
      heatLevel: "medium",
    });
    expect(recipe.steps[3]).toMatchObject({
      doneWhen: "the tomatoes are jammy and oil separates at the edges",
      heatLevel: "medium-high",
    });
  });

  it("includes recipe-stage nutrition in a sample recipe", () => {
    const ingredients = [...demoDetectedIngredients(), ...demoPantryIngredients()];
    const options = createDemoOptions({
      preferences,
      batchNumber: 1,
      excludedIds: [],
      ingredients,
    });
    const recipe = createDemoRecipes([options[0]], ingredients, 2)[options[0].id];

    expect(recipe.nutrition).toMatchObject({
      caloriesKcal: 438,
      proteinG: 21,
      carbohydratesG: 17,
      fatG: 32,
    });
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
      recipe.ingredients.find((ingredient) => ingredient.name === "onion"),
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
      "onion",
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

    expect(missingNames).not.toContain("onion");
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

  it("counts every confirmed recipe ingredient in the option usage summary", () => {
    const ingredients = [
      ...demoDetectedIngredients(),
      ...demoPantryIngredients().map((ingredient) => ({
        ...ingredient,
        confirmed: true,
      })),
    ];
    const option = createDemoOptions({
      preferences: { ...preferences, optionCount: 6 },
      batchNumber: 1,
      excludedIds: [],
      ingredients,
    }).find((candidate) => candidate.name === "Palak Paneer");

    expect(option?.usedIngredients).toEqual([
      "Spinach",
      "Paneer",
      "Tomatoes",
      "Green chillies",
      "Onion",
      "Garlic",
      "Oil or ghee",
      "Chilli powder",
      "Salt",
      "Coriander",
    ]);
    expect(option?.previewLabel).toBe("AI-generated image");
  });

  it("formats allergen notices as a natural-language list", () => {
    const ingredients = [...demoDetectedIngredients(), ...demoPantryIngredients()];
    const [option] = createDemoOptions({
      preferences,
      batchNumber: 1,
      excludedIds: [],
      ingredients,
    });
    const optionWithAllergens = {
      ...option,
      nutrition: option.nutrition
        ? {
            ...option.nutrition,
            allergenWarnings: ["dairy", "soy", "tree nuts"],
          }
        : null,
    };

    expect(
      createDemoRecipes([optionWithAllergens], ingredients, 2)[option.id]
        .allergenNotice,
    ).toBe("May contain dairy, soy, and tree nuts.");
  });
});
