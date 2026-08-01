import type {
  CompleteRecipeView,
  NutritionView,
  RecipeIngredientView,
  RecipeStepView,
} from "./cook-session-state";

export const SAVED_RECIPES_STORAGE_KEY = "cook-mantra:saved-recipes:v1";

const SAVED_RECIPES_VERSION = 1;

export interface SavedRecipeEntry {
  id: string;
  savedAt: string;
  recipe: CompleteRecipeView;
}

export type SavedRecipesMutationResult =
  | { ok: true; entries: SavedRecipeEntry[] }
  | { ok: false; entries: SavedRecipeEntry[] };

interface SavedRecipesPayload {
  version: typeof SAVED_RECIPES_VERSION;
  entries: SavedRecipeEntry[];
}

interface StorageReadResult {
  ok: boolean;
  entries: SavedRecipeEntry[];
}

const INGREDIENT_AVAILABILITIES = new Set(["available", "missing", "optional"]);
const HEAT_LEVELS = new Set(["low", "medium", "medium-high", "high"]);

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === "string");
}

function isRecipeIngredient(value: unknown): value is RecipeIngredientView {
  if (!isRecord(value)) {
    return false;
  }

  return (
    typeof value.name === "string" &&
    typeof value.quantity === "string" &&
    typeof value.availability === "string" &&
    INGREDIENT_AVAILABILITIES.has(value.availability) &&
    (value.substitution === null || typeof value.substitution === "string")
  );
}

function isRecipeStep(value: unknown): value is RecipeStepView {
  if (!isRecord(value)) {
    return false;
  }

  return (
    isFiniteNumber(value.number) &&
    Number.isInteger(value.number) &&
    typeof value.instruction === "string" &&
    (value.durationMinutes === null ||
      (isFiniteNumber(value.durationMinutes) && value.durationMinutes > 0)) &&
    (value.doneWhen === undefined || typeof value.doneWhen === "string") &&
    (value.heatLevel === undefined ||
      (typeof value.heatLevel === "string" && HEAT_LEVELS.has(value.heatLevel)))
  );
}

function isNutrition(value: unknown): value is NutritionView {
  if (!isRecord(value)) {
    return false;
  }

  return (
    isFiniteNumber(value.caloriesKcal) &&
    isFiniteNumber(value.proteinG) &&
    isFiniteNumber(value.carbohydratesG) &&
    isFiniteNumber(value.fatG) &&
    isStringArray(value.dietTags) &&
    isStringArray(value.allergenWarnings) &&
    typeof value.disclaimer === "string"
  );
}

function isCompleteRecipe(value: unknown): value is CompleteRecipeView {
  if (!isRecord(value)) {
    return false;
  }

  return (
    typeof value.optionId === "string" &&
    value.optionId.trim().length > 0 &&
    typeof value.name === "string" &&
    value.name.trim().length > 0 &&
    typeof value.cuisine === "string" &&
    isFiniteNumber(value.servings) &&
    Number.isInteger(value.servings) &&
    value.servings > 0 &&
    isFiniteNumber(value.totalMinutes) &&
    value.totalMinutes > 0 &&
    Array.isArray(value.ingredients) &&
    value.ingredients.length > 0 &&
    value.ingredients.every(isRecipeIngredient) &&
    Array.isArray(value.steps) &&
    value.steps.length > 0 &&
    value.steps.every(isRecipeStep) &&
    isStringArray(value.tips) &&
    isStringArray(value.substitutions) &&
    (value.nutrition === null || isNutrition(value.nutrition)) &&
    typeof value.nutritionNotice === "string" &&
    typeof value.allergenNotice === "string" &&
    isStringArray(value.assumptions) &&
    isStringArray(value.warnings)
  );
}

function isIsoDate(value: unknown): value is string {
  if (typeof value !== "string") {
    return false;
  }

  const timestamp = Date.parse(value);
  return Number.isFinite(timestamp) && new Date(timestamp).toISOString() === value;
}

function isSavedRecipeEntry(value: unknown): value is SavedRecipeEntry {
  return (
    isRecord(value) &&
    typeof value.id === "string" &&
    value.id.trim().length > 0 &&
    isIsoDate(value.savedAt) &&
    isCompleteRecipe(value.recipe)
  );
}

function browserStorage(): Storage | null {
  if (typeof window === "undefined") {
    return null;
  }

  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

function readSavedRecipes(storage: Storage): StorageReadResult {
  let serialized: string | null;
  try {
    serialized = storage.getItem(SAVED_RECIPES_STORAGE_KEY);
  } catch {
    return { ok: false, entries: [] };
  }

  if (serialized === null) {
    return { ok: true, entries: [] };
  }

  try {
    const payload: unknown = JSON.parse(serialized);
    if (
      !isRecord(payload) ||
      payload.version !== SAVED_RECIPES_VERSION ||
      !Array.isArray(payload.entries)
    ) {
      return { ok: true, entries: [] };
    }

    return {
      ok: true,
      entries: payload.entries.filter(isSavedRecipeEntry),
    };
  } catch {
    return { ok: true, entries: [] };
  }
}

function writeSavedRecipes(
  storage: Storage,
  entries: SavedRecipeEntry[],
  previousEntries: SavedRecipeEntry[],
): SavedRecipesMutationResult {
  const payload: SavedRecipesPayload = {
    version: SAVED_RECIPES_VERSION,
    entries,
  };

  try {
    storage.setItem(SAVED_RECIPES_STORAGE_KEY, JSON.stringify(payload));
    return { ok: true, entries };
  } catch {
    return { ok: false, entries: previousEntries };
  }
}

function recipesHaveSameIdentity(
  first: CompleteRecipeView,
  second: CompleteRecipeView,
): boolean {
  return first.optionId === second.optionId;
}

function nextEntryId(entries: readonly SavedRecipeEntry[], recipe: CompleteRecipeView) {
  const usedIds = new Set(entries.map((entry) => entry.id));
  const baseId = recipe.optionId.trim();
  let id = baseId;
  let suffix = 2;

  while (usedIds.has(id)) {
    id = `${baseId}-${suffix}`;
    suffix += 1;
  }

  return id;
}

export function loadSavedRecipes(): SavedRecipeEntry[] {
  const storage = browserStorage();
  if (storage === null) {
    return [];
  }

  return readSavedRecipes(storage).entries;
}

export function saveRecipe(recipe: CompleteRecipeView): SavedRecipesMutationResult {
  const storage = browserStorage();
  if (storage === null) {
    return { ok: false, entries: [] };
  }

  const current = readSavedRecipes(storage);
  if (!current.ok || !isCompleteRecipe(recipe)) {
    return { ok: false, entries: current.entries };
  }

  const matchingIndexes = current.entries
    .map((entry, index) => (recipesHaveSameIdentity(entry.recipe, recipe) ? index : -1))
    .filter((index) => index >= 0);
  const firstMatchingIndex = matchingIndexes[0];
  const entry: SavedRecipeEntry = {
    id:
      firstMatchingIndex === undefined
        ? nextEntryId(current.entries, recipe)
        : current.entries[firstMatchingIndex].id,
    savedAt: new Date().toISOString(),
    recipe,
  };

  if (firstMatchingIndex === undefined) {
    return writeSavedRecipes(storage, [...current.entries, entry], current.entries);
  }

  const nextEntries = current.entries.flatMap((existing, index) => {
    if (index === firstMatchingIndex) {
      return [entry];
    }
    return matchingIndexes.includes(index) ? [] : [existing];
  });
  return writeSavedRecipes(storage, nextEntries, current.entries);
}

export function removeSavedRecipe(
  idOrRecipe: string | CompleteRecipeView,
): SavedRecipesMutationResult {
  const storage = browserStorage();
  if (storage === null) {
    return { ok: false, entries: [] };
  }

  const current = readSavedRecipes(storage);
  if (!current.ok) {
    return { ok: false, entries: current.entries };
  }

  const removeById = typeof idOrRecipe === "string";
  if (!removeById && !isCompleteRecipe(idOrRecipe)) {
    return { ok: false, entries: current.entries };
  }

  const nextEntries = current.entries.filter((entry) =>
    removeById
      ? entry.id !== idOrRecipe
      : !recipesHaveSameIdentity(entry.recipe, idOrRecipe),
  );

  if (nextEntries.length === current.entries.length) {
    return { ok: true, entries: current.entries };
  }

  return writeSavedRecipes(storage, nextEntries, current.entries);
}

export function isRecipeSaved(
  entries: readonly SavedRecipeEntry[],
  recipe: CompleteRecipeView,
): boolean {
  return (
    isCompleteRecipe(recipe) &&
    entries.some(
      (entry) =>
        isSavedRecipeEntry(entry) && recipesHaveSameIdentity(entry.recipe, recipe),
    )
  );
}
