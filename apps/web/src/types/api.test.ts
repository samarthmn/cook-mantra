import { describe, expectTypeOf, it } from "vitest";

import type {
  ApiErrorEnvelope,
  CompleteRecipeResponse,
  IngredientReviewRequest,
  JobResponse,
  RecipeStep,
  RecipeOptionResponse,
  RecipePreferences,
  SessionResponse,
} from "./api";

describe("Cook Mantra API types", () => {
  it("models the session workflow and ingredient provenance contract", () => {
    expectTypeOf<SessionResponse["stage"]>().toEqualTypeOf<
      | "extracting"
      | "reviewing_ingredients"
      | "ingredients_confirmed"
      | "generating_options"
      | "options_ready"
      | "generating_recipes"
      | "recipes_ready"
    >();
    expectTypeOf<SessionResponse["ingredients"][number]>().toMatchTypeOf<{
      id: string;
      name: string;
      source: "detected" | "pantry_suggestion" | "user_added";
      confidence: number | null;
      confirmed: boolean;
    }>();
    expectTypeOf<IngredientReviewRequest["ingredients"][number]>().toEqualTypeOf<{
      id?: string | null;
      name: string;
      confirmed: boolean;
    }>();
  });

  it("models nullable recipe enrichments and complete recipe fields", () => {
    expectTypeOf<RecipeOptionResponse["nutrition"]>().toEqualTypeOf<{
      calories_kcal: number;
      protein_g: number;
      carbohydrates_g: number;
      fat_g: number;
      diet_tags: string[];
      allergen_warnings: string[];
      disclaimer: "Estimated values; not medical advice.";
    } | null>();
    expectTypeOf<RecipeOptionResponse["preview"]>().toEqualTypeOf<{
      artifact_id: string;
      label: "AI-generated image";
    } | null>();
    expectTypeOf<
      CompleteRecipeResponse["ingredients"][number]["availability"]
    >().toEqualTypeOf<"available" | "missing" | "optional">();
    expectTypeOf<CompleteRecipeResponse["nutrition"]>().toEqualTypeOf<
      | {
          calories_kcal: number;
          protein_g: number;
          carbohydrates_g: number;
          fat_g: number;
          diet_tags: string[];
          allergen_warnings: string[];
          disclaimer: "Estimated values; not medical advice.";
        }
      | null
      | undefined
    >();
    expectTypeOf<RecipeStep>().toEqualTypeOf<{
      number: number;
      instruction: string;
      duration_minutes: number | null;
      done_when?: string | null;
      heat_level?: "low" | "medium" | "medium-high" | "high" | null;
    }>();
  });

  it("models recipe preferences and job lifecycle fields", () => {
    expectTypeOf<RecipePreferences>().toEqualTypeOf<{
      dietary_preferences: string[];
      allergens: string[];
      preferred_cuisines: string[];
      max_total_minutes: number | null;
      servings: number;
      option_count: number;
      spice_level: "mild" | "medium" | "hot" | "extra-hot" | null;
      special_instructions: string;
    }>();
    expectTypeOf<JobResponse["status"]>().toEqualTypeOf<
      "queued" | "running" | "succeeded" | "failed"
    >();
    expectTypeOf<JobResponse["progress"]>().toEqualTypeOf<number>();
  });

  it("models the stable API error envelope", () => {
    expectTypeOf<ApiErrorEnvelope>().toMatchTypeOf<{
      error: {
        code: string;
        message: string;
        details: Record<string, unknown>;
        retryable: boolean;
        request_id: string;
        session_id: string | null;
        job_id: string | null;
      };
    }>();
  });
});
