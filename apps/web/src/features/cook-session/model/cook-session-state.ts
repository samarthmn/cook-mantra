export type AppView = "upload" | "job" | "confirm" | "options" | "recipes";
export type SessionMode = "demo" | "api";
export type IngredientSource = "detected" | "pantry_suggestion" | "user_added";
export type DietPreference = "none" | "vegetarian" | "vegan";
export type RecipeDifficulty = "easy" | "medium" | "hard";
export type IngredientAvailability = "available" | "missing" | "optional";

export interface IngredientView {
  id: string;
  name: string;
  source: IngredientSource;
  confidence: number | null;
  confirmed: boolean;
}

export interface PreferenceView {
  diet: DietPreference;
  servings: 1 | 2 | 4;
  maxMinutes: 30 | 45 | 60;
  optionCount: 3 | 4 | 6;
  allergens: string;
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
  previewArtifactId: string | null;
  previewLabel: string | null;
  warnings: string[];
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
  nutritionNotice: string;
  allergenNotice: string;
  assumptions: string[];
  warnings: string[];
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
  returnView: Exclude<AppView, "job">;
  progress: number;
  selectedNames: string[];
}

export interface CookSessionState {
  view: AppView;
  maxReached: 1 | 2 | 3 | 4;
  mode: SessionMode;
  sessionId: string | null;
  photoPreviewUrl: string | null;
  weakDetection: boolean;
  ingredients: IngredientView[];
  ingredientNameErrors: Record<string, string>;
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

export type CookSessionAction =
  | { type: "start-manual-entry" }
  | {
      type: "receive-ingredients";
      mode: SessionMode;
      sessionId: string | null;
      photoPreviewUrl: string | null;
      weakDetection: boolean;
      ingredients: IngredientView[];
    }
  | { type: "replace-session-id"; sessionId: string }
  | {
      type: "set-preference";
      key: keyof PreferenceView;
      value: PreferenceView[keyof PreferenceView];
    }
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
    }
  | { type: "navigate"; view: Exclude<AppView, "job"> }
  | { type: "add-ingredient"; name: string }
  | { type: "rename-ingredient"; id: string; name: string }
  | { type: "remove-ingredient"; id: string }
  | { type: "toggle-ingredient"; id: string }
  | { type: "toggle-option"; id: string }
  | { type: "toggle-recipe-step"; optionId: string; stepNumber: number }
  | { type: "reset" };

const MAX_RECIPE_SELECTIONS = 6;

const PANTRY_STAPLES = [
  "Salt",
  "Pepper powder",
  "Oil or ghee",
  "Chilli powder",
  "Onion",
  "Garlic",
  "Ginger",
] as const;

function pantryIngredients(): IngredientView[] {
  return PANTRY_STAPLES.map((name) => ({
    id: `pantry-${name.toLowerCase().replaceAll(" ", "-")}`,
    name,
    source: "pantry_suggestion",
    confidence: null,
    confirmed: false,
  }));
}

export function createInitialCookSessionState(): CookSessionState {
  return {
    view: "upload",
    maxReached: 1,
    mode: "demo",
    sessionId: null,
    photoPreviewUrl: null,
    weakDetection: false,
    ingredients: [],
    ingredientNameErrors: {},
    preferences: {
      diet: "vegetarian",
      servings: 2,
      maxMinutes: 45,
      optionCount: 4,
      allergens: "",
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
        ...createInitialCookSessionState(),
        view: "confirm",
        maxReached: 2,
        // Typed ingredients open a real server session on Generate, so this is
        // an API session that simply does not have its id yet.
        mode: "api",
        ingredients: pantryIngredients(),
      };

    case "receive-ingredients":
      return {
        ...state,
        view: "confirm",
        maxReached: 2,
        mode: action.mode,
        sessionId: action.sessionId,
        photoPreviewUrl: action.photoPreviewUrl,
        weakDetection: action.weakDetection,
        ingredients: action.ingredients,
        ingredientNameErrors: findIngredientNameErrors(action.ingredients),
        job: null,
        error: null,
      };

    case "replace-session-id":
      return { ...state, sessionId: action.sessionId, error: null };

    case "set-preference": {
      if (state.preferences[action.key] === action.value) return state;
      const invalidated = invalidateGeneratedContent(state, state.ingredients);
      return {
        ...invalidated,
        preferences: {
          ...state.preferences,
          [action.key]: action.value,
        } as PreferenceView,
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
          progress: Math.max(0, Math.min(100, action.progress)),
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
      const activeRecipeId =
        state.selectedOptionIds.find((id) => action.recipes[id] !== undefined) ??
        Object.keys(action.recipes)[0] ??
        null;
      return {
        ...state,
        view: "recipes",
        maxReached: 4,
        completeRecipes: action.recipes,
        recipeFailures: action.failures,
        activeRecipeId,
        job: null,
        error: null,
      };
    }

    case "navigate": {
      if (state.view === "job") return state;
      const ranks: Record<Exclude<AppView, "job">, number> = {
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
          ingredient.name.toLocaleLowerCase() === name.toLocaleLowerCase(),
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

      const ingredients = [
        ...state.ingredients,
        {
          id: `user-${state.nextLocalId}`,
          name,
          source: "user_added" as const,
          confidence: null,
          confirmed: true,
        },
      ];
      return {
        ...invalidateGeneratedContent(state, ingredients),
        nextLocalId: state.nextLocalId + 1,
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
      return createInitialCookSessionState();
  }
}
