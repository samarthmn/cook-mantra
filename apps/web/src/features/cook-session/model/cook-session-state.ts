import { normalizePantryStaples, PANTRY_STAPLES } from "./pantry-staples";

export type AppView = "upload" | "job" | "confirm" | "options" | "recipes" | "saved";
export type CookSessionView = Exclude<AppView, "saved">;
export type SessionMode = "demo" | "api";
export type IngredientSource = "detected" | "pantry_suggestion" | "user_added";
export type DietPreference = "vegetarian" | "non-vegetarian";
export type DietStyle = "vegan" | "eggetarian" | "jain" | "halal" | "kosher";
export type SpiceLevel = "mild" | "medium" | "hot" | "extra-hot";
export type RecipeDifficulty = "easy" | "medium" | "hard";
export type IngredientAvailability = "available" | "missing" | "optional";
export type RecipeHeatLevel = "low" | "medium" | "medium-high" | "high";

export interface IngredientView {
  id: string;
  name: string;
  source: IngredientSource;
  confidence: number | null;
  confirmed: boolean;
}

export interface PreferenceView {
  diet: DietPreference;
  dietStyle: DietStyle | null;
  dietAddOns: string[];
  servings: 1 | 2 | 4;
  optionCount: 3 | 4 | 6;
  allergens: string[];
  cuisines: string[];
  spiceLevel: SpiceLevel;
  specialInstructions: string;
}

export interface IngredientRequirementView {
  name: string;
  reason: string;
  substitution: string | null;
}

export interface NutritionView {
  caloriesKcal: number;
  proteinG: number;
  carbohydratesG: number;
  fatG: number;
  dietTags: string[];
  allergenWarnings: string[];
  disclaimer: string;
}

export interface RecipeOptionView {
  id: string;
  name: string;
  summary: string;
  cuisine: string;
  totalMinutes: number;
  difficulty: RecipeDifficulty;
  usedIngredients: string[];
  missingIngredients: IngredientRequirementView[];
  optionalIngredients: IngredientRequirementView[];
  nutrition: NutritionView | null;
  batchNumber: number;
}

export interface RecipeIngredientView {
  name: string;
  quantity: string;
  availability: IngredientAvailability;
  substitution: string | null;
}

export interface RecipeStepView {
  number: number;
  instruction: string;
  durationMinutes: number | null;
  doneWhen?: string;
  heatLevel?: RecipeHeatLevel;
}

export interface CompleteRecipeView {
  optionId: string;
  name: string;
  cuisine: string;
  servings: number;
  totalMinutes: number;
  ingredients: RecipeIngredientView[];
  steps: RecipeStepView[];
  tips: string[];
  substitutions: string[];
  nutrition: NutritionView | null;
  nutritionNotice: string;
  allergenNotice: string;
  assumptions: string[];
  warnings: string[];
  previewArtifactId: string | null;
  previewLabel: string | null;
}

export interface RecipeFailureView {
  optionId: string;
  code: string;
  message: string;
  retryable: boolean;
}

export interface AppErrorView {
  title: string;
  message: string;
  retryable: boolean;
}

export interface JobView {
  kind: "extraction" | "ideas" | "more-ideas" | "recipes";
  returnView: Exclude<CookSessionView, "job">;
  progress: number;
  selectedNames: string[];
}

export interface CookSessionState {
  view: CookSessionView;
  maxReached: 1 | 2 | 3 | 4;
  mode: SessionMode;
  sessionId: string | null;
  photoPreviewUrl: string | null;
  weakDetection: boolean;
  ingredients: IngredientView[];
  ingredientNameErrors: Record<string, string>;
  pantryStaples: string[];
  preferences: PreferenceView;
  options: RecipeOptionView[];
  selectedOptionIds: string[];
  completeRecipes: Record<string, CompleteRecipeView>;
  recipeFailures: Record<string, RecipeFailureView>;
  activeRecipeId: string | null;
  completedSteps: Record<string, number[]>;
  ideasExhausted: boolean;
  job: JobView | null;
  error: AppErrorView | null;
  nextLocalId: number;
}

type SetPreferenceAction = {
  type: "set-preference";
  key: keyof PreferenceView;
  value: PreferenceView[keyof PreferenceView];
};

export type CookSessionAction =
  | { type: "start-manual-entry" }
  | { type: "continue-with-manual-entry" }
  | {
      type: "receive-ingredients";
      mode: SessionMode;
      sessionId: string | null;
      photoPreviewUrl: string | null;
      weakDetection: boolean;
      ingredients: IngredientView[];
    }
  | { type: "replace-session-id"; sessionId: string }
  | SetPreferenceAction
  | { type: "set-active-recipe"; optionId: string }
  | {
      type: "start-job";
      kind: JobView["kind"];
      returnView: JobView["returnView"];
      selectedNames: string[];
    }
  | { type: "update-job-progress"; progress: number }
  | { type: "cancel-job" }
  | { type: "fail-job"; error: AppErrorView }
  | { type: "dismiss-error" }
  | { type: "mark-ideas-exhausted" }
  | { type: "receive-options"; options: RecipeOptionView[]; append: boolean }
  | {
      type: "receive-recipes";
      recipes: Record<string, CompleteRecipeView>;
      failures: Record<string, RecipeFailureView>;
      merge: boolean;
    }
  | { type: "navigate"; view: Exclude<CookSessionView, "job"> }
  | { type: "add-ingredient"; name: string }
  | { type: "rename-ingredient"; id: string; name: string }
  | { type: "remove-ingredient"; id: string }
  | { type: "toggle-ingredient"; id: string }
  | { type: "toggle-all-pantry"; confirmed: boolean }
  | { type: "set-pantry-staples"; names: string[] }
  | { type: "toggle-option"; id: string }
  | { type: "toggle-recipe-step"; optionId: string; stepNumber: number }
  | { type: "reset" };

const MAX_RECIPE_SELECTIONS = 6;
export const MAX_CUISINE_PREFERENCES = 20;
export const LOCAL_PANTRY_INGREDIENT_ID_PREFIX = "local-pantry-";

const VALID_DIET_STYLES: Record<DietPreference, ReadonlySet<DietStyle>> = {
  vegetarian: new Set(["vegan", "eggetarian", "jain"]),
  "non-vegetarian": new Set(["halal", "kosher"]),
};
const VALID_DIET_ADD_ONS = new Set(["keto", "no onion or garlic"]);

export function createInitialCookSessionState(
  pantryStaples: readonly string[] = PANTRY_STAPLES,
): CookSessionState {
  return {
    view: "upload",
    maxReached: 1,
    mode: "demo",
    sessionId: null,
    photoPreviewUrl: null,
    weakDetection: false,
    ingredients: [],
    ingredientNameErrors: {},
    pantryStaples: normalizePantryStaples(pantryStaples),
    preferences: {
      diet: "vegetarian",
      dietStyle: null,
      dietAddOns: [],
      servings: 2,
      optionCount: 4,
      allergens: [],
      cuisines: [],
      spiceLevel: "medium",
      specialInstructions: "",
    },
    options: [],
    selectedOptionIds: [],
    completeRecipes: {},
    recipeFailures: {},
    activeRecipeId: null,
    completedSteps: {},
    ideasExhausted: false,
    job: null,
    error: null,
    nextLocalId: 1,
  };
}

export function confirmedIngredientCount(ingredients: IngredientView[]): number {
  return ingredients.filter(
    (ingredient) => ingredient.confirmed && ingredient.name.trim().length > 0,
  ).length;
}

export function confirmedIngredientNames(ingredients: IngredientView[]): string[] {
  return ingredients
    .filter((ingredient) => ingredient.confirmed)
    .map((ingredient) => normalizedName(ingredient.name))
    .filter((name) => name.length > 0);
}

function normalizedComparisonName(name: string): string {
  return normalizedName(name).toLocaleLowerCase();
}

function ingredientIdSlug(name: string): string {
  return (
    normalizedComparisonName(name)
      .normalize("NFKD")
      .replaceAll(/[\u0300-\u036f]/g, "")
      .replaceAll(/[^a-z0-9]+/g, "-")
      .replaceAll(/^-+|-+$/g, "") || "staple"
  );
}

function nextUniqueId(baseId: string, usedIds: Set<string>): string {
  let id = baseId;
  let suffix = 2;
  while (usedIds.has(id)) {
    id = `${baseId}-${suffix}`;
    suffix += 1;
  }
  usedIds.add(id);
  return id;
}

function newPantryIngredient(name: string, usedIds: Set<string>): IngredientView {
  return {
    id: nextUniqueId(
      `${LOCAL_PANTRY_INGREDIENT_ID_PREFIX}${ingredientIdSlug(name)}`,
      usedIds,
    ),
    name,
    source: "pantry_suggestion",
    confidence: null,
    confirmed: false,
  };
}

function pantryIngredients(names: readonly string[]): IngredientView[] {
  const usedIds = new Set<string>();
  return normalizePantryStaples(names).map((name) =>
    newPantryIngredient(name, usedIds),
  );
}

function appendMissingPantryIngredients(
  ingredients: IngredientView[],
  pantryStaples: readonly string[],
): IngredientView[] {
  const usedIds = new Set(ingredients.map((ingredient) => ingredient.id));
  const usedNames = new Set(
    ingredients.map((ingredient) => normalizedComparisonName(ingredient.name)),
  );
  const additions: IngredientView[] = [];

  for (const name of normalizePantryStaples(pantryStaples)) {
    const comparisonName = normalizedComparisonName(name);
    if (usedNames.has(comparisonName)) continue;
    usedNames.add(comparisonName);
    additions.push(newPantryIngredient(name, usedIds));
  }

  return additions.length === 0 ? ingredients : [...ingredients, ...additions];
}

function reconcilePantryIngredients(
  ingredients: IngredientView[],
  pantryStaples: readonly string[],
): IngredientView[] {
  const retainedIngredients = ingredients.filter(
    (ingredient) => ingredient.source !== "pantry_suggestion",
  );
  const retainedNames = new Set(
    retainedIngredients.map((ingredient) => normalizedComparisonName(ingredient.name)),
  );
  const existingPantryByName = new Map<string, IngredientView>();
  for (const ingredient of ingredients) {
    if (ingredient.source !== "pantry_suggestion") continue;
    const name = normalizedComparisonName(ingredient.name);
    if (!existingPantryByName.has(name)) existingPantryByName.set(name, ingredient);
  }

  const usedIds = new Set(retainedIngredients.map((ingredient) => ingredient.id));
  const reconciledPantry: IngredientView[] = [];
  for (const name of normalizePantryStaples(pantryStaples)) {
    const comparisonName = normalizedComparisonName(name);
    if (retainedNames.has(comparisonName)) continue;

    const existing = existingPantryByName.get(comparisonName);
    if (existing && !usedIds.has(existing.id)) {
      usedIds.add(existing.id);
      reconciledPantry.push(existing.name === name ? existing : { ...existing, name });
    } else {
      reconciledPantry.push(newPantryIngredient(name, usedIds));
    }
  }

  return [...retainedIngredients, ...reconciledPantry];
}

function arraysEqual(left: readonly string[], right: readonly string[]): boolean {
  return (
    left.length === right.length && left.every((value, index) => value === right[index])
  );
}

function ingredientsEqual(
  left: readonly IngredientView[],
  right: readonly IngredientView[],
): boolean {
  return (
    left.length === right.length &&
    left.every((ingredient, index) => {
      const other = right[index];
      return (
        ingredient.id === other?.id &&
        ingredient.name === other.name &&
        ingredient.source === other.source &&
        ingredient.confidence === other.confidence &&
        ingredient.confirmed === other.confirmed
      );
    })
  );
}

function normalizedUniqueNames(values: readonly string[]): string[] {
  const result: string[] = [];
  const seen = new Set<string>();
  for (const value of values) {
    const name = normalizedName(value);
    const comparisonName = name.toLocaleLowerCase();
    if (!name || seen.has(comparisonName)) continue;
    seen.add(comparisonName);
    result.push(name);
  }
  return result;
}

function normalizedDietStyle(
  style: DietStyle | null,
  diet: DietPreference,
): DietStyle | null {
  if (typeof style !== "string") return null;
  const normalizedStyle = normalizedName(style).toLocaleLowerCase() as DietStyle;
  return VALID_DIET_STYLES[diet].has(normalizedStyle) ? normalizedStyle : null;
}

function normalizedDietAddOns(addOns: readonly string[]): string[] {
  return normalizedUniqueNames(addOns)
    .map((addOn) => addOn.toLocaleLowerCase())
    .filter((addOn) => VALID_DIET_ADD_ONS.has(addOn));
}

function nextPreferences(
  preferences: PreferenceView,
  action: SetPreferenceAction,
): PreferenceView {
  switch (action.key) {
    case "diet": {
      const diet = action.value as PreferenceView["diet"];
      return {
        ...preferences,
        diet,
        dietStyle:
          diet === preferences.diet
            ? normalizedDietStyle(preferences.dietStyle, diet)
            : null,
        dietAddOns: normalizedDietAddOns(preferences.dietAddOns),
      };
    }
    case "dietStyle":
      return {
        ...preferences,
        dietStyle: normalizedDietStyle(
          action.value as PreferenceView["dietStyle"],
          preferences.diet,
        ),
      };
    case "dietAddOns":
      return {
        ...preferences,
        dietAddOns: normalizedDietAddOns(action.value as PreferenceView["dietAddOns"]),
      };
    case "allergens":
      return {
        ...preferences,
        allergens: normalizedUniqueNames(action.value as PreferenceView["allergens"]),
      };
    case "cuisines":
      return {
        ...preferences,
        cuisines: normalizedUniqueNames(
          action.value as PreferenceView["cuisines"],
        ).slice(0, MAX_CUISINE_PREFERENCES),
      };
    case "specialInstructions":
      return {
        ...preferences,
        specialInstructions: (
          action.value as PreferenceView["specialInstructions"]
        ).slice(0, 500),
      };
    case "servings":
      return {
        ...preferences,
        servings: action.value as PreferenceView["servings"],
      };
    case "optionCount":
      return {
        ...preferences,
        optionCount: action.value as PreferenceView["optionCount"],
      };
    case "spiceLevel":
      return {
        ...preferences,
        spiceLevel: action.value as PreferenceView["spiceLevel"],
      };
  }
}

function preferencesEqual(left: PreferenceView, right: PreferenceView): boolean {
  return (
    left.diet === right.diet &&
    left.dietStyle === right.dietStyle &&
    arraysEqual(left.dietAddOns, right.dietAddOns) &&
    left.servings === right.servings &&
    left.optionCount === right.optionCount &&
    arraysEqual(left.allergens, right.allergens) &&
    arraysEqual(left.cuisines, right.cuisines) &&
    left.spiceLevel === right.spiceLevel &&
    left.specialInstructions === right.specialInstructions
  );
}

function hasGeneratedContent(state: CookSessionState): boolean {
  return (
    state.options.length > 0 ||
    state.selectedOptionIds.length > 0 ||
    Object.keys(state.completeRecipes).length > 0 ||
    Object.keys(state.recipeFailures).length > 0 ||
    state.activeRecipeId !== null ||
    Object.keys(state.completedSteps).length > 0 ||
    state.ideasExhausted
  );
}

function invalidateGeneratedContent(
  state: CookSessionState,
  ingredients: IngredientView[],
): CookSessionState {
  return {
    ...state,
    view: "confirm",
    maxReached: 2,
    ingredients,
    ingredientNameErrors: findIngredientNameErrors(ingredients),
    options: [],
    selectedOptionIds: [],
    completeRecipes: {},
    recipeFailures: {},
    activeRecipeId: null,
    completedSteps: {},
    ideasExhausted: false,
    error: null,
  };
}

function normalizedName(name: string): string {
  return name.trim().replaceAll(/\s+/g, " ");
}

function findIngredientNameErrors(
  ingredients: IngredientView[],
): Record<string, string> {
  const idsByName = new Map<string, string[]>();
  for (const ingredient of ingredients) {
    const name = normalizedName(ingredient.name).toLocaleLowerCase();
    if (!name) continue;
    idsByName.set(name, [...(idsByName.get(name) ?? []), ingredient.id]);
  }

  const errors: Record<string, string> = {};
  for (const ids of idsByName.values()) {
    if (ids.length < 2) continue;
    for (const id of ids) {
      errors[id] = "Ingredient names must be unique. Rename or remove the duplicate.";
    }
  }
  return errors;
}

export function cookSessionReducer(
  state: CookSessionState,
  action: CookSessionAction,
): CookSessionState {
  switch (action.type) {
    case "start-manual-entry":
      return {
        ...createInitialCookSessionState(state.pantryStaples),
        view: "confirm",
        maxReached: 2,
        // Typed ingredients open a real server session on Generate, so this is
        // an API session that simply does not have its id yet.
        mode: "api",
        ingredients: pantryIngredients(state.pantryStaples),
      };

    case "continue-with-manual-entry":
      return {
        ...state,
        view: "confirm",
        maxReached: 2,
        mode: "api",
        sessionId: null,
        photoPreviewUrl: null,
        weakDetection: false,
        options: [],
        selectedOptionIds: [],
        completeRecipes: {},
        recipeFailures: {},
        activeRecipeId: null,
        completedSteps: {},
        ideasExhausted: false,
        job: null,
        error: null,
      };

    case "receive-ingredients": {
      const ingredients = appendMissingPantryIngredients(
        action.ingredients,
        state.pantryStaples,
      );
      return {
        ...state,
        view: "confirm",
        maxReached: 2,
        mode: action.mode,
        sessionId: action.sessionId,
        photoPreviewUrl: action.photoPreviewUrl,
        weakDetection: action.weakDetection,
        ingredients,
        ingredientNameErrors: findIngredientNameErrors(ingredients),
        job: null,
        error: null,
      };
    }

    case "replace-session-id":
      return { ...state, sessionId: action.sessionId, error: null };

    case "set-preference": {
      const preferences = nextPreferences(state.preferences, action);
      if (preferencesEqual(state.preferences, preferences)) return state;
      const invalidated = invalidateGeneratedContent(state, state.ingredients);
      return {
        ...invalidated,
        preferences,
      };
    }

    case "set-active-recipe":
      return state.completeRecipes[action.optionId]
        ? { ...state, activeRecipeId: action.optionId }
        : state;

    case "start-job":
      return {
        ...state,
        view: "job",
        job: {
          kind: action.kind,
          returnView: action.returnView,
          progress: 0,
          selectedNames: action.selectedNames,
        },
        error: null,
      };

    case "update-job-progress":
      if (!state.job) return state;
      return {
        ...state,
        job: {
          ...state.job,
          // Monotonic within a job: early client-side ticks (e.g. "upload
          // finished") must not be undone by a slower first server report.
          progress: Math.max(state.job.progress, Math.min(100, action.progress)),
        },
      };

    case "cancel-job":
      return {
        ...state,
        view: state.job?.returnView ?? "upload",
        job: null,
        error: null,
      };

    case "fail-job":
      return {
        ...state,
        view: state.job?.returnView ?? "upload",
        job: null,
        error: action.error,
      };

    case "dismiss-error":
      return { ...state, error: null };

    case "mark-ideas-exhausted":
      return {
        ...state,
        view: "options",
        maxReached: Math.max(state.maxReached, 3) as 3 | 4,
        ideasExhausted: true,
        job: null,
        error: null,
      };

    case "receive-options": {
      const existingIds = new Set(state.options.map((option) => option.id));
      const incomingOptions = action.append
        ? action.options.filter((option) => !existingIds.has(option.id))
        : action.options;
      const options = action.append
        ? [...state.options, ...incomingOptions]
        : incomingOptions;
      return {
        ...state,
        view: "options",
        maxReached: action.append ? (Math.max(state.maxReached, 3) as 3 | 4) : 3,
        options,
        selectedOptionIds: action.append ? state.selectedOptionIds : [],
        completeRecipes: action.append ? state.completeRecipes : {},
        recipeFailures: action.append ? state.recipeFailures : {},
        activeRecipeId: action.append ? state.activeRecipeId : null,
        completedSteps: action.append ? state.completedSteps : {},
        job: null,
        error: null,
        ideasExhausted: action.append && incomingOptions.length === 0,
      };
    }

    case "receive-recipes": {
      const retriedIds = new Set([
        ...Object.keys(action.recipes),
        ...Object.keys(action.failures),
      ]);
      const completeRecipes = action.merge
        ? { ...state.completeRecipes }
        : action.recipes;
      const recipeFailures = action.merge
        ? { ...state.recipeFailures }
        : action.failures;

      if (action.merge) {
        for (const optionId of retriedIds) {
          delete completeRecipes[optionId];
          delete recipeFailures[optionId];
        }
        Object.assign(completeRecipes, action.recipes);
        Object.assign(recipeFailures, action.failures);
      }

      const activeRecipeId = action.merge
        ? ((state.activeRecipeId && completeRecipes[state.activeRecipeId]
            ? state.activeRecipeId
            : null) ??
          state.selectedOptionIds.find((id) => completeRecipes[id] !== undefined) ??
          Object.keys(completeRecipes)[0] ??
          null)
        : (state.selectedOptionIds.find((id) => completeRecipes[id] !== undefined) ??
          Object.keys(completeRecipes)[0] ??
          null);
      return {
        ...state,
        view: "recipes",
        maxReached: 4,
        completeRecipes,
        recipeFailures,
        activeRecipeId,
        job: null,
        error: null,
      };
    }

    case "navigate": {
      if (state.view === "job") return state;
      const ranks: Record<Exclude<CookSessionView, "job">, number> = {
        upload: 1,
        confirm: 2,
        options: 3,
        recipes: 4,
      };
      if (ranks[action.view] > state.maxReached || action.view === state.view) {
        return state;
      }
      return { ...state, view: action.view, error: null };
    }

    case "add-ingredient": {
      const name = normalizedName(action.name);
      if (!name) return state;

      const existing = state.ingredients.find(
        (ingredient) =>
          normalizedComparisonName(ingredient.name) === normalizedComparisonName(name),
      );
      if (existing) {
        if (existing.confirmed) return state;
        return invalidateGeneratedContent(
          state,
          state.ingredients.map((ingredient) =>
            ingredient.id === existing.id
              ? { ...ingredient, confirmed: true }
              : ingredient,
          ),
        );
      }

      const usedIds = new Set(state.ingredients.map((ingredient) => ingredient.id));
      let nextLocalId = state.nextLocalId;
      while (usedIds.has(`user-${nextLocalId}`)) nextLocalId += 1;
      const ingredients = [
        ...state.ingredients,
        {
          id: `user-${nextLocalId}`,
          name,
          source: "user_added" as const,
          confidence: null,
          confirmed: true,
        },
      ];
      return {
        ...invalidateGeneratedContent(state, ingredients),
        nextLocalId: nextLocalId + 1,
      };
    }

    case "rename-ingredient": {
      const ingredients = state.ingredients.map((ingredient) =>
        ingredient.id === action.id
          ? { ...ingredient, name: action.name.slice(0, 80) }
          : ingredient,
      );
      return invalidateGeneratedContent(state, ingredients);
    }

    case "remove-ingredient":
      return invalidateGeneratedContent(
        state,
        state.ingredients.filter((ingredient) => ingredient.id !== action.id),
      );

    case "toggle-ingredient":
      return invalidateGeneratedContent(
        state,
        state.ingredients.map((ingredient) =>
          ingredient.id === action.id
            ? { ...ingredient, confirmed: !ingredient.confirmed }
            : ingredient,
        ),
      );

    case "toggle-all-pantry": {
      const pantryIngredients = state.ingredients.filter(
        (ingredient) => ingredient.source === "pantry_suggestion",
      );
      if (
        pantryIngredients.length === 0 ||
        pantryIngredients.every(
          (ingredient) => ingredient.confirmed === action.confirmed,
        )
      ) {
        return state;
      }
      return invalidateGeneratedContent(
        state,
        state.ingredients.map((ingredient) =>
          ingredient.source === "pantry_suggestion"
            ? { ...ingredient, confirmed: action.confirmed }
            : ingredient,
        ),
      );
    }

    case "set-pantry-staples": {
      const pantryStaples = normalizePantryStaples(action.names);
      const shouldReconcile =
        state.view !== "upload" ||
        state.ingredients.some(
          (ingredient) => ingredient.source === "pantry_suggestion",
        ) ||
        hasGeneratedContent(state);
      if (!shouldReconcile) {
        return arraysEqual(state.pantryStaples, pantryStaples)
          ? state
          : { ...state, pantryStaples };
      }

      const ingredients = reconcilePantryIngredients(state.ingredients, pantryStaples);
      if (
        arraysEqual(state.pantryStaples, pantryStaples) &&
        ingredientsEqual(state.ingredients, ingredients)
      ) {
        return state;
      }
      return {
        ...invalidateGeneratedContent(state, ingredients),
        pantryStaples,
      };
    }

    case "toggle-option": {
      if (!state.options.some((option) => option.id === action.id)) return state;
      const isSelected = state.selectedOptionIds.includes(action.id);
      if (!isSelected && state.selectedOptionIds.length >= MAX_RECIPE_SELECTIONS) {
        return {
          ...state,
          error: {
            title: "Choose up to 6 recipes",
            message: "Deselect one recipe before choosing another.",
            retryable: false,
          },
        };
      }
      return {
        ...state,
        selectedOptionIds: isSelected
          ? state.selectedOptionIds.filter((id) => id !== action.id)
          : [...state.selectedOptionIds, action.id],
        error: null,
      };
    }

    case "toggle-recipe-step": {
      if (action.stepNumber < 1) return state;
      const current = state.completedSteps[action.optionId] ?? [];
      const isComplete = current.includes(action.stepNumber);
      const next = isComplete
        ? current.filter((stepNumber) => stepNumber !== action.stepNumber)
        : [...current, action.stepNumber].sort((left, right) => left - right);
      const completedSteps = { ...state.completedSteps };
      if (next.length === 0) delete completedSteps[action.optionId];
      else completedSteps[action.optionId] = next;
      return { ...state, completedSteps };
    }

    case "reset":
      return createInitialCookSessionState(state.pantryStaples);
  }
}
