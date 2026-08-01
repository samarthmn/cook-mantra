import { describe, expect, it } from "vitest";

import type {
  CompleteRecipeResponse,
  IngredientResponse,
  RecipeOptionResponse,
} from "@/types/api";

import {
  ingredientsFromApi,
  ingredientsToRebasedReviewRequest,
  ingredientsToReviewRequest,
  optionFromApi,
  preferencesToApi,
  recipeFromApi,
} from "./api-mappers";

describe("API view mappers", () => {
  it("selects detections for explicit review but leaves pantry suggestions unchecked", () => {
    const response: IngredientResponse[] = [
      {
        id: "detected-1",
        name: "Tomato",
        source: "detected",
        confidence: 0.91,
        confirmed: false,
      },
      {
        id: "pantry-1",
        name: "Salt",
        source: "pantry_suggestion",
        confidence: null,
        confirmed: false,
      },
    ];

    expect(ingredientsFromApi(response)).toEqual([
      { ...response[0], confirmed: true },
      response[1],
    ]);
  });

  it("sends only client-editable ingredient fields back to the API", () => {
    const review = ingredientsToReviewRequest([
      {
        id: "detected-1",
        name: "Cherry tomato",
        source: "detected",
        confidence: 0.91,
        confirmed: true,
      },
      {
        id: "user-1",
        name: "Basil",
        source: "user_added",
        confidence: null,
        confirmed: true,
      },
      {
        id: "server-pantry-1",
        name: "Salt",
        source: "pantry_suggestion",
        confidence: null,
        confirmed: false,
      },
      {
        id: "local-pantry-cumin",
        name: "Cumin",
        source: "pantry_suggestion",
        confidence: null,
        confirmed: true,
      },
    ]);

    expect(review).toEqual({
      ingredients: [
        { id: "detected-1", name: "Cherry tomato", confirmed: true },
        { id: null, name: "Basil", confirmed: true },
        { id: "server-pantry-1", name: "Salt", confirmed: false },
        { id: null, name: "Cumin", confirmed: true },
      ],
    });
  });

  it("omits blank in-progress ingredient rows from the API review", () => {
    const review = ingredientsToReviewRequest([
      {
        id: "detected-1",
        name: "   ",
        source: "detected",
        confidence: 0.91,
        confirmed: true,
      },
      {
        id: "user-1",
        name: "Basil",
        source: "user_added",
        confidence: null,
        confirmed: true,
      },
    ]);

    expect(review.ingredients).toEqual([{ id: null, name: "Basil", confirmed: true }]);
  });

  it("rebases a reviewed list onto fresh server ingredient IDs", () => {
    const review = ingredientsToRebasedReviewRequest(
      [
        {
          id: "old-detected",
          name: "  Cherry   Tomato ",
          source: "detected",
          confidence: 0.91,
          confirmed: true,
        },
        {
          id: "old-renamed",
          name: "Baby spinach",
          source: "detected",
          confidence: 0.72,
          confirmed: true,
        },
        {
          id: "user-1",
          name: "Basil",
          source: "user_added",
          confidence: null,
          confirmed: true,
        },
      ],
      [
        {
          id: "fresh-detected",
          name: "cherry tomato",
          source: "detected",
          confidence: 0.95,
          confirmed: false,
        },
        {
          id: "fresh-spinach",
          name: "Spinach",
          source: "detected",
          confidence: 0.88,
          confirmed: false,
        },
      ],
    );

    expect(review).toEqual({
      ingredients: [
        { id: "fresh-detected", name: "Cherry   Tomato", confirmed: true },
        { id: null, name: "Baby spinach", confirmed: true },
        { id: null, name: "Basil", confirmed: true },
      ],
    });
  });

  it("normalizes preferences into the backend contract", () => {
    expect(
      preferencesToApi({
        diet: "vegetarian",
        dietStyle: "vegan",
        dietAddOns: ["KETO"],
        servings: 2,
        optionCount: 4,
        allergens: ["dairy", "Shellfish"],
        spiceLevel: "medium",
        specialInstructions: "  kid friendly  ",
      }),
    ).toEqual({
      dietary_preferences: ["vegetarian", "vegan", "keto"],
      allergens: ["dairy", "Shellfish"],
      preferred_cuisines: [],
      max_total_minutes: null,
      servings: 2,
      option_count: 4,
      spice_level: "medium",
      special_instructions: "kid friendly",
    });
  });

  it("deduplicates allergens case-insensitively while preserving first spelling", () => {
    const preferences = preferencesToApi({
      diet: "non-vegetarian",
      dietStyle: "halal",
      dietAddOns: ["KETO", "keto", ""],
      servings: 2,
      optionCount: 4,
      allergens: ["dairy", "Dairy", "SOY", "soy", ""],
      spiceLevel: "extra-hot",
      specialInstructions: "",
    });

    expect(preferences.dietary_preferences).toEqual([
      "non-vegetarian",
      "halal",
      "keto",
    ]);
    expect(preferences.allergens).toEqual(["dairy", "SOY"]);
    expect(preferences.spice_level).toBe("extra-hot");
    expect(preferences.special_instructions).toBe("");
  });

  it("maps an unqualified non-vegetarian preference without extra constraints", () => {
    const preferences = preferencesToApi({
      diet: "non-vegetarian",
      dietStyle: null,
      dietAddOns: [],
      servings: 2,
      optionCount: 4,
      allergens: [],
      spiceLevel: "medium",
      specialInstructions: "",
    });

    expect(preferences.dietary_preferences).toEqual(["non-vegetarian"]);
  });

  it("maps recipe option and complete recipe fields without losing honesty data", () => {
    const option: RecipeOptionResponse = {
      id: "option-1",
      name: "Tomato Pasta",
      summary: "A quick pasta.",
      cuisine: "Italian",
      total_minutes: 30,
      difficulty: "easy",
      used_ingredients: ["Tomato"],
      missing_ingredients: [
        { name: "Pasta", reason: "Base", substitution: "Use noodles" },
      ],
      optional_ingredients: [],
      nutrition: {
        calories_kcal: 520,
        protein_g: 18,
        carbohydrates_g: 78,
        fat_g: 15,
        diet_tags: ["vegetarian"],
        allergen_warnings: ["wheat"],
        disclaimer: "Estimated values; not medical advice.",
      },
      preview: {
        artifact_id: "artifact-1",
        label: "AI-generated image",
      },
      warnings: ["Image may vary."],
    };
    const recipe: CompleteRecipeResponse = {
      option_id: "option-1",
      name: "Tomato Pasta",
      cuisine: "Italian",
      servings: 2,
      total_minutes: 30,
      ingredients: [
        {
          name: "Pasta",
          quantity: "200 g",
          availability: "missing",
          substitution: "Use noodles",
        },
      ],
      steps: [{ number: 1, instruction: "Boil the pasta.", duration_minutes: 10 }],
      tips: ["Reserve pasta water."],
      substitutions: ["Pasta → noodles"],
      nutrition_notice: "Estimated values; not medical advice.",
      allergen_notice: "Check ingredient labels for allergens.",
      assumptions: [],
      warnings: ["Pasta must be purchased."],
    };

    expect(optionFromApi(option, 2)).toMatchObject({
      totalMinutes: 30,
      previewArtifactId: "artifact-1",
      batchNumber: 2,
      nutrition: { caloriesKcal: 520, allergenWarnings: ["wheat"] },
    });
    expect(recipeFromApi(recipe)).toMatchObject({
      optionId: "option-1",
      ingredients: [
        {
          name: "Pasta",
          quantity: "200 g",
          availability: "missing",
          substitution: "Use noodles",
        },
      ],
      steps: [{ number: 1, instruction: "Boil the pasta.", durationMinutes: 10 }],
    });
  });
});
