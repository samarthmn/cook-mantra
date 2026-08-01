import type {
  CompleteRecipeResponse,
  IngredientResponse,
  IngredientReviewRequest,
  RecipeOptionResponse,
  RecipePreferences,
} from "@/types/api";

import { LOCAL_PANTRY_INGREDIENT_ID_PREFIX } from "./cook-session-state";

import type {
  CompleteRecipeView,
  IngredientView,
  PreferenceView,
  RecipeOptionView,
} from "./cook-session-state";

export function ingredientsFromApi(
  ingredients: IngredientResponse[],
): IngredientView[] {
  return ingredients.map((ingredient) => ({
    ...ingredient,
    confirmed: ingredient.source === "detected" ? true : Boolean(ingredient.confirmed),
  }));
}

export function ingredientsToReviewRequest(
  ingredients: IngredientView[],
): IngredientReviewRequest {
  return {
    ingredients: ingredients
      .filter((ingredient) => ingredient.name.trim().length > 0)
      .map((ingredient) => ({
        id:
          ingredient.source === "user_added" ||
          ingredient.id.startsWith(LOCAL_PANTRY_INGREDIENT_ID_PREFIX)
            ? null
            : ingredient.id,
        name: ingredient.name.trim(),
        confirmed: ingredient.confirmed,
      })),
  };
}

export function ingredientsToRebasedReviewRequest(
  ingredients: IngredientView[],
  freshIngredients: IngredientResponse[],
): IngredientReviewRequest {
  const freshIdByName = new Map(
    freshIngredients.map((ingredient) => [
      normalizedIngredientName(ingredient.name),
      ingredient.id,
    ]),
  );

  return {
    ingredients: ingredients
      .filter((ingredient) => ingredient.name.trim().length > 0)
      .map((ingredient) => ({
        id: freshIdByName.get(normalizedIngredientName(ingredient.name)) ?? null,
        name: ingredient.name.trim(),
        confirmed: ingredient.confirmed,
      })),
  };
}

function normalizedIngredientName(name: string): string {
  return name.trim().replaceAll(/\s+/g, " ").toLocaleLowerCase();
}

export function preferencesToApi(preferences: PreferenceView): RecipePreferences {
  const allergens = uniqueNormalizedValues(preferences.allergens, false);
  const dietaryPreferences = uniqueNormalizedValues(
    [
      preferences.diet,
      ...(preferences.dietStyle ? [preferences.dietStyle] : []),
      ...preferences.dietAddOns,
    ],
    true,
  );

  return {
    dietary_preferences: dietaryPreferences,
    allergens,
    preferred_cuisines: [],
    max_total_minutes: null,
    servings: preferences.servings,
    option_count: preferences.optionCount,
    spice_level: preferences.spiceLevel,
    special_instructions: preferences.specialInstructions.trim().slice(0, 500),
  };
}

function uniqueNormalizedValues(
  values: readonly string[],
  lowercase: boolean,
): string[] {
  const result: string[] = [];
  const seen = new Set<string>();
  for (const value of values) {
    const normalized = value.trim().replaceAll(/\s+/g, " ");
    const comparisonValue = normalized.toLocaleLowerCase();
    if (!normalized || seen.has(comparisonValue)) continue;
    seen.add(comparisonValue);
    result.push(lowercase ? comparisonValue : normalized);
  }
  return result;
}

export function optionFromApi(
  option: RecipeOptionResponse,
  batchNumber: number,
): RecipeOptionView {
  return {
    id: option.id,
    name: option.name,
    summary: option.summary,
    cuisine: option.cuisine,
    totalMinutes: option.total_minutes,
    difficulty: option.difficulty,
    usedIngredients: option.used_ingredients,
    missingIngredients: option.missing_ingredients,
    optionalIngredients: option.optional_ingredients,
    nutrition: option.nutrition
      ? {
          caloriesKcal: option.nutrition.calories_kcal,
          proteinG: option.nutrition.protein_g,
          carbohydratesG: option.nutrition.carbohydrates_g,
          fatG: option.nutrition.fat_g,
          dietTags: option.nutrition.diet_tags,
          allergenWarnings: option.nutrition.allergen_warnings,
          disclaimer: option.nutrition.disclaimer,
        }
      : null,
    previewArtifactId: option.preview?.artifact_id ?? null,
    previewLabel: option.preview?.label ?? null,
    warnings: option.warnings,
    batchNumber,
  };
}

export function recipeFromApi(recipe: CompleteRecipeResponse): CompleteRecipeView {
  return {
    optionId: recipe.option_id,
    name: recipe.name,
    cuisine: recipe.cuisine,
    servings: recipe.servings,
    totalMinutes: recipe.total_minutes,
    ingredients: recipe.ingredients,
    steps: recipe.steps.map((step) => ({
      number: step.number,
      instruction: step.instruction,
      durationMinutes: step.duration_minutes,
    })),
    tips: recipe.tips,
    substitutions: recipe.substitutions,
    nutritionNotice: recipe.nutrition_notice,
    allergenNotice: recipe.allergen_notice,
    assumptions: recipe.assumptions,
    warnings: recipe.warnings,
  };
}
