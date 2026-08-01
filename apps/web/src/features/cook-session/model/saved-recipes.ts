import type {
  CompleteRecipeView,
  IngredientSource,
  NutritionView,
  RecipeIngredientView,
  RecipeStepView,
} from "./cook-session-state";

export const SAVED_RECIPES_STORAGE_KEY = "cook-mantra:saved-recipes:v2";

const LEGACY_SAVED_RECIPES_STORAGE_KEY = "cook-mantra:saved-recipes:v1";
const LEGACY_SAVED_RECIPES_VERSION = 1;
const SAVED_RECIPES_VERSION = 2;
const MAX_PHOTO_DATA_URL_LENGTH = 400 * 1024;

export interface SavedRecipeProgress {
  doneStepNumbers: number[];
  ingredientsExpanded: boolean;
}

export interface SaveRecipeSnapshot {
  photo: string | null;
  progress: SavedRecipeProgress | null;
  ingredientSources: Record<string, IngredientSource> | null;
}

export interface SavedRecipeEntry {
  id: string;
  savedAt: string;
  recipe: CompleteRecipeView;
  photo: string | null;
  progress: SavedRecipeProgress | null;
  ingredientSources: Record<string, IngredientSource> | null;
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
const INGREDIENT_SOURCES = new Set<IngredientSource>([
  "detected",
  "pantry_suggestion",
  "user_added",
]);
const HEAT_LEVELS = new Set(["low", "medium", "medium-high", "high"]);
const DATA_IMAGE_URL_PATTERN = /^data:image\/[a-z0-9.+-]+(?:;[^,\r\n]*)?,[^\r\n]*$/i;

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

function isSavedRecipeProgress(value: unknown): value is SavedRecipeProgress {
  return (
    isRecord(value) &&
    Array.isArray(value.doneStepNumbers) &&
    value.doneStepNumbers.every(
      (stepNumber) => typeof stepNumber === "number" && Number.isInteger(stepNumber),
    ) &&
    typeof value.ingredientsExpanded === "boolean"
  );
}

function normalizeProgress(
  value: unknown,
  recipe: CompleteRecipeView,
): SavedRecipeProgress | null {
  if (!isSavedRecipeProgress(value)) {
    return null;
  }

  const recipeStepNumbers = new Set(recipe.steps.map((step) => step.number));
  const seenStepNumbers = new Set<number>();
  const doneStepNumbers = value.doneStepNumbers.filter((stepNumber) => {
    if (!recipeStepNumbers.has(stepNumber) || seenStepNumbers.has(stepNumber)) {
      return false;
    }

    seenStepNumbers.add(stepNumber);
    return true;
  });

  return {
    doneStepNumbers,
    ingredientsExpanded: value.ingredientsExpanded,
  };
}

function normalizeIngredientSources(
  value: unknown,
): Record<string, IngredientSource> | null {
  if (!isRecord(value)) {
    return null;
  }

  const entries = Object.entries(value);
  if (
    entries.some(
      ([name, source]) =>
        name.trim().length === 0 ||
        typeof source !== "string" ||
        !INGREDIENT_SOURCES.has(source as IngredientSource),
    )
  ) {
    return null;
  }

  return Object.fromEntries(entries) as Record<string, IngredientSource>;
}

function normalizePhoto(value: unknown): string | null {
  return typeof value === "string" &&
    value.length <= MAX_PHOTO_DATA_URL_LENGTH &&
    DATA_IMAGE_URL_PATTERN.test(value)
    ? value
    : null;
}

function hasValidEntryCore(value: unknown): value is Record<string, unknown> & {
  id: string;
  savedAt: string;
  recipe: CompleteRecipeView;
} {
  return (
    isRecord(value) &&
    typeof value.id === "string" &&
    value.id.trim().length > 0 &&
    isIsoDate(value.savedAt) &&
    isCompleteRecipe(value.recipe)
  );
}

function migrateLegacyEntry(value: unknown): SavedRecipeEntry | null {
  if (!hasValidEntryCore(value)) {
    return null;
  }

  return {
    id: value.id,
    savedAt: value.savedAt,
    recipe: value.recipe,
    photo: null,
    progress: null,
    ingredientSources: null,
  };
}

function normalizeSavedRecipeEntry(value: unknown): SavedRecipeEntry | null {
  if (!hasValidEntryCore(value)) {
    return null;
  }

  return {
    id: value.id,
    savedAt: value.savedAt,
    recipe: value.recipe,
    photo: normalizePhoto(value.photo),
    progress: normalizeProgress(value.progress, value.recipe),
    ingredientSources: normalizeIngredientSources(value.ingredientSources),
  };
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

function readStorageItem(
  storage: Storage,
  key: string,
): { ok: true; serialized: string | null } | { ok: false } {
  try {
    return { ok: true, serialized: storage.getItem(key) };
  } catch {
    return { ok: false };
  }
}

function parseCurrentPayload(serialized: string): StorageReadResult {
  try {
    const payload: unknown = JSON.parse(serialized);
    if (
      !isRecord(payload) ||
      payload.version !== SAVED_RECIPES_VERSION ||
      !Array.isArray(payload.entries)
    ) {
      return { ok: false, entries: [] };
    }

    return {
      ok: true,
      entries: payload.entries
        .map(normalizeSavedRecipeEntry)
        .filter((entry): entry is SavedRecipeEntry => entry !== null),
    };
  } catch {
    return { ok: false, entries: [] };
  }
}

function parseLegacyPayload(serialized: string): SavedRecipeEntry[] {
  try {
    const payload: unknown = JSON.parse(serialized);
    if (!isRecord(payload) || !Array.isArray(payload.entries)) {
      return [];
    }

    if (payload.version === LEGACY_SAVED_RECIPES_VERSION) {
      return payload.entries
        .map(migrateLegacyEntry)
        .filter((entry): entry is SavedRecipeEntry => entry !== null);
    }

    if (payload.version === SAVED_RECIPES_VERSION) {
      return payload.entries
        .map(normalizeSavedRecipeEntry)
        .filter((entry): entry is SavedRecipeEntry => entry !== null);
    }

    return [];
  } catch {
    return [];
  }
}

function readSavedRecipes(storage: Storage): StorageReadResult {
  const current = readStorageItem(storage, SAVED_RECIPES_STORAGE_KEY);
  if (!current.ok) {
    return { ok: false, entries: [] };
  }

  if (current.serialized !== null) {
    return parseCurrentPayload(current.serialized);
  }

  const legacy = readStorageItem(storage, LEGACY_SAVED_RECIPES_STORAGE_KEY);
  if (!legacy.ok) {
    return { ok: false, entries: [] };
  }

  return {
    ok: true,
    entries: legacy.serialized === null ? [] : parseLegacyPayload(legacy.serialized),
  };
}

function removeLegacyPayload(storage: Storage): void {
  try {
    storage.removeItem(LEGACY_SAVED_RECIPES_STORAGE_KEY);
  } catch {
    // The v2 write already succeeded, so stale legacy data is safe to ignore.
  }
}

function writeSavedRecipes(storage: Storage, entries: SavedRecipeEntry[]): boolean {
  const payload: SavedRecipesPayload = {
    version: SAVED_RECIPES_VERSION,
    entries,
  };

  try {
    storage.setItem(SAVED_RECIPES_STORAGE_KEY, JSON.stringify(payload));
    removeLegacyPayload(storage);
    return true;
  } catch {
    return false;
  }
}

function nextEntryId(
  entries: readonly SavedRecipeEntry[],
  recipe: CompleteRecipeView,
): string {
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

function normalizeSnapshot(
  snapshot: SaveRecipeSnapshot,
  recipe: CompleteRecipeView,
): SaveRecipeSnapshot | null {
  if (!isRecord(snapshot)) {
    return null;
  }

  return {
    photo: normalizePhoto(snapshot.photo),
    progress: normalizeProgress(snapshot.progress, recipe),
    ingredientSources: normalizeIngredientSources(snapshot.ingredientSources),
  };
}

export function loadSavedRecipes(): SavedRecipeEntry[] {
  const storage = browserStorage();
  if (storage === null) {
    return [];
  }

  return readSavedRecipes(storage).entries;
}

export function saveRecipe(
  recipe: CompleteRecipeView,
  snapshot: SaveRecipeSnapshot,
): SavedRecipesMutationResult {
  const storage = browserStorage();
  if (storage === null) {
    return { ok: false, entries: [] };
  }

  const current = readSavedRecipes(storage);
  const normalizedSnapshot = isCompleteRecipe(recipe)
    ? normalizeSnapshot(snapshot, recipe)
    : null;
  if (!current.ok || !isCompleteRecipe(recipe) || normalizedSnapshot === null) {
    return { ok: false, entries: current.entries };
  }

  const entry: SavedRecipeEntry = {
    id: nextEntryId(current.entries, recipe),
    savedAt: new Date().toISOString(),
    recipe,
    photo: normalizedSnapshot.photo,
    progress: normalizedSnapshot.progress,
    ingredientSources: normalizedSnapshot.ingredientSources,
  };
  const nextEntries = [...current.entries, entry];

  if (writeSavedRecipes(storage, nextEntries)) {
    return { ok: true, entries: nextEntries };
  }

  if (entry.photo !== null) {
    const entryWithoutPhoto = { ...entry, photo: null };
    const entriesWithoutNewPhoto = [...current.entries, entryWithoutPhoto];
    if (writeSavedRecipes(storage, entriesWithoutNewPhoto)) {
      return { ok: true, entries: entriesWithoutNewPhoto };
    }
  }

  return { ok: false, entries: current.entries };
}

export function removeSavedRecipe(id: string): SavedRecipesMutationResult {
  const storage = browserStorage();
  if (storage === null) {
    return { ok: false, entries: [] };
  }

  const current = readSavedRecipes(storage);
  if (!current.ok) {
    return { ok: false, entries: current.entries };
  }

  const nextEntries = current.entries.filter((entry) => entry.id !== id);

  if (nextEntries.length === current.entries.length) {
    return { ok: true, entries: current.entries };
  }

  return writeSavedRecipes(storage, nextEntries)
    ? { ok: true, entries: nextEntries }
    : { ok: false, entries: current.entries };
}
