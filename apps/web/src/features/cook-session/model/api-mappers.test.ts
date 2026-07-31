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
    ]);

    expect(review).toEqual({
      ingredients: [
        { id: "detected-1", name: "Cherry tomato", confirmed: true },
        { id: null, name: "Basil", confirmed: true },
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
        diet: "vegan",
        servings: 4,
        maxMinutes: 30,
        optionCount: 6,
        allergens: " peanuts, Shellfish ",
      }),
    ).toEqual({
      dietary_preferences: ["vegan"],
      allergens: ["peanuts", "Shellfish"],
      preferred_cuisines: [],
      max_total_minutes: 30,
      servings: 4,
      option_count: 6,
    });
  });

  it("deduplicates allergens case-insensitively while preserving first spelling", () => {
    const preferences = preferencesToApi({
      diet: "none",
      servings: 2,
      maxMinutes: 45,
      optionCount: 4,
      allergens: "dairy, Dairy, SOY, soy, ",
    });

    expect(preferences.allergens).toEqual(["dairy", "SOY"]);
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
        label: "AI-generated illustration",
      },
      warnings: ["Illustration may vary."],
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
