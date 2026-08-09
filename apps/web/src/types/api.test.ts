import { describe, expectTypeOf, it } from "vitest";

import type {
  ApiErrorEnvelope,
  ApiErrorCode,
  CompleteRecipeResponse,
  DishPreview,
  IngredientReviewRequest,
  JobResponse,
  RecipeStep,
  RecipeOptionResponse,
  RecipePreferences,
  SessionResponse,
  ReadinessResponse,
  RuntimeStatusResponse,
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

  it("keeps previews on complete recipes rather than recipe options", () => {
    expectTypeOf<RecipeOptionResponse["nutrition"]>().toEqualTypeOf<{
      calories_kcal: number;
      protein_g: number;
      carbohydrates_g: number;
      fat_g: number;
      diet_tags: string[];
      allergen_warnings: string[];
      disclaimer: "Estimated values; not medical advice.";
    } | null>();
    expectTypeOf<keyof RecipeOptionResponse>().toEqualTypeOf<
      | "id"
      | "name"
      | "summary"
      | "cuisine"
      | "total_minutes"
      | "difficulty"
      | "used_ingredients"
      | "missing_ingredients"
      | "optional_ingredients"
      | "nutrition"
    >();
    expectTypeOf<
      CompleteRecipeResponse["preview"]
    >().toEqualTypeOf<DishPreview | null>();
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

  it("models generic providers and authoritative role readiness", () => {
    expectTypeOf<ApiErrorCode>().toEqualTypeOf<
      | "invalid_request"
      | "resource_not_found"
      | "invalid_session_transition"
      | "ingredients_not_confirmed"
      | "recipe_duplicate"
      | "model_configuration_invalid"
      | "provider_unavailable"
      | "provider_authentication_failed"
      | "provider_rate_limited"
      | "provider_payment_required"
      | "model_capability_missing"
      | "provider_protocol_error"
      | "ollama_unavailable"
      | "image_provider_unavailable"
      | "nutrition_provider_unavailable"
      | "model_not_found"
      | "model_output_invalid"
      | "operation_timed_out"
      | "service_busy"
      | "artifact_failure"
      | "runtime_status_stale"
      | "internal_error"
    >();
    expectTypeOf<ReadinessResponse["model_runtime"]["roles"]>().toMatchTypeOf<
      Record<
        "ingredient_extractor" | "master_chef" | "recipe_writer" | "image_generator",
        { ready: boolean; provider: "ollama" | "openrouter" | "codex" | null }
      >
    >();
  });

  it("models the safe process-local runtime status snapshot", () => {
    expectTypeOf<RuntimeStatusResponse>().toEqualTypeOf<{
      status: "ok" | "attention";
      runtime_revision: string;
      model_runtime: {
        ready: boolean;
        roles: Record<
          "ingredient_extractor" | "master_chef" | "recipe_writer" | "image_generator",
          {
            role:
              | "ingredient_extractor"
              | "master_chef"
              | "recipe_writer"
              | "image_generator";
            provider: "ollama" | "openrouter" | "codex" | null;
            model: string | null;
            enabled: boolean;
            ready: boolean;
            required_capabilities: Array<
              "text" | "vision" | "structured_output" | "image_output"
            >;
            available_capabilities: Array<
              "text" | "vision" | "structured_output" | "image_output"
            >;
            error?:
              | "unavailable"
              | "authentication_failed"
              | "rate_limited"
              | "payment_required"
              | "capability_missing"
              | "protocol_error"
              | "timed_out"
              | "invalid_output"
              | null;
          }
        >;
      };
    }>();
  });
});
