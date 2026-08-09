export type ApiErrorCode =
  | "invalid_request"
  | "resource_not_found"
  | "invalid_session_transition"
  | "ingredients_not_confirmed"
  | "recipe_duplicate"
  | "runtime_status_stale"
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
  | "internal_error";

export interface ApiErrorDetail {
  code: ApiErrorCode;
  message: string;
  details: Record<string, unknown>;
  retryable: boolean;
  request_id: string;
  session_id: string | null;
  job_id: string | null;
}

export interface ApiErrorEnvelope {
  error: ApiErrorDetail;
}

export interface QueuedJobResponse {
  session_id: string;
  job_id: string;
}

export type IngredientSource = "detected" | "pantry_suggestion" | "user_added";

export interface IngredientResponse {
  id: string;
  name: string;
  source: IngredientSource;
  confidence: number | null;
  confirmed: boolean;
}

export interface IngredientDraft {
  id?: string | null;
  name: string;
  confirmed: boolean;
}

export interface IngredientReviewRequest {
  ingredients: IngredientDraft[];
}

export interface ManualSessionRequest {
  ingredients: string[];
}

export interface RecipePreferences {
  dietary_preferences: string[];
  allergens: string[];
  preferred_cuisines: string[];
  max_total_minutes: number | null;
  servings: number;
  option_count: number;
  spice_level: "mild" | "medium" | "hot" | "extra-hot" | null;
  special_instructions: string;
}

export type RecipeDifficulty = "easy" | "medium" | "hard";

export interface IngredientRequirement {
  name: string;
  reason: string;
  substitution: string | null;
}

export interface NutritionEstimate {
  calories_kcal: number;
  protein_g: number;
  carbohydrates_g: number;
  fat_g: number;
  diet_tags: string[];
  allergen_warnings: string[];
  disclaimer: "Estimated values; not medical advice.";
}

export interface DishPreview {
  artifact_id: string;
  label: "AI-generated image";
}

export interface RecipeOptionResponse {
  id: string;
  name: string;
  summary: string;
  cuisine: string;
  total_minutes: number;
  difficulty: RecipeDifficulty;
  used_ingredients: string[];
  missing_ingredients: IngredientRequirement[];
  optional_ingredients: IngredientRequirement[];
  nutrition: NutritionEstimate | null;
}

export type IngredientAvailability = "available" | "missing" | "optional";
export type RecipeHeatLevel = "low" | "medium" | "medium-high" | "high";

export interface RecipeIngredient {
  name: string;
  quantity: string;
  availability: IngredientAvailability;
  substitution: string | null;
}

export interface RecipeStep {
  number: number;
  instruction: string;
  duration_minutes: number | null;
  done_when?: string | null;
  heat_level?: RecipeHeatLevel | null;
}

export interface CompleteRecipeResponse {
  option_id: string;
  name: string;
  cuisine: string;
  servings: number;
  total_minutes: number;
  ingredients: RecipeIngredient[];
  steps: RecipeStep[];
  tips: string[];
  substitutions: string[];
  nutrition?: NutritionEstimate | null;
  nutrition_notice: "Estimated values; not medical advice.";
  allergen_notice: "Check ingredient labels for allergens.";
  assumptions: string[];
  warnings: string[];
  preview: DishPreview | null;
}

export interface RecipeFailureResponse {
  option_id: string;
  code: ApiErrorCode;
  message: string;
  retryable: boolean;
}

export type SessionStage =
  | "extracting"
  | "reviewing_ingredients"
  | "ingredients_confirmed"
  | "generating_options"
  | "options_ready"
  | "generating_recipes"
  | "recipes_ready";

export interface SessionResponse {
  id: string;
  stage: SessionStage;
  image_artifact_id: string | null;
  ingredients: IngredientResponse[];
  preferences: RecipePreferences;
  recipe_options: RecipeOptionResponse[];
  complete_recipes: Record<string, CompleteRecipeResponse>;
  recipe_failures: Record<string, RecipeFailureResponse>;
  excluded_recipe_names: string[];
  option_batch_number: number;
  warnings: string[];
  created_at: string;
  updated_at: string;
}

export type JobOperation =
  | "extract_ingredients"
  | "generate_options"
  | "generate_more_options"
  | "generate_recipes";

export type JobStatus = "queued" | "running" | "succeeded" | "failed";

export interface JobErrorResponse {
  code: ApiErrorCode;
  message: string;
  details: Record<string, unknown>;
  retryable: boolean;
}

export interface JobResponse {
  id: string;
  operation: JobOperation;
  session_id: string;
  status: JobStatus;
  progress: number;
  result: Record<string, unknown> | null;
  warnings: string[];
  error: JobErrorResponse | null;
  created_at: string;
  updated_at: string;
}

export type TerminalJobResponse = JobResponse & {
  status: "succeeded" | "failed";
};

export interface RecipeSelectionRequest {
  option_ids: string[];
}

export interface HealthResponse {
  status: "ok";
}

export interface OllamaStatus {
  reachable: boolean;
  available_models: string[];
  missing: string[];
}

export interface ReadinessResponse {
  status: "ok";
  model_runtime: ModelRuntimeReadiness;
  ollama: OllamaStatus;
}

export interface RuntimeStatusResponse {
  status: "ok" | "attention";
  runtime_revision: string;
  model_runtime: ModelRuntimeReadiness;
}

export type ModelProvider = "ollama" | "openrouter" | "codex";
export type ModelRole =
  "ingredient_extractor" | "master_chef" | "recipe_writer" | "image_generator";
export type ModelCapability = "text" | "vision" | "structured_output" | "image_output";

export interface ModelRoleReadiness {
  role: ModelRole;
  provider: ModelProvider | null;
  model: string | null;
  enabled: boolean;
  ready: boolean;
  required_capabilities: ModelCapability[];
  available_capabilities: ModelCapability[];
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

export interface ModelRuntimeReadiness {
  ready: boolean;
  roles: Record<ModelRole, ModelRoleReadiness>;
}
