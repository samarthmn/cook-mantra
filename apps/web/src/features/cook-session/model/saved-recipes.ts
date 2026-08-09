import type {
  CompleteRecipeView,
  IngredientSource,
  NutritionView,
  RecipeIngredientView,
  RecipeStepView,
} from "./cook-session-state";

export const SAVED_RECIPES_STORAGE_KEY = "cook-mantra:saved-recipes:v3";

const V2_SAVED_RECIPES_STORAGE_KEY = "cook-mantra:saved-recipes:v2";
const V1_SAVED_RECIPES_STORAGE_KEY = "cook-mantra:saved-recipes:v1";
const V1_SAVED_RECIPES_VERSION = 1;
const V2_SAVED_RECIPES_VERSION = 2;
const SAVED_RECIPES_VERSION = 3;
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

type CompleteRecipeCore = Omit<
  CompleteRecipeView,
  "previewArtifactId" | "previewLabel"
>;

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

function hasCompleteRecipeCore(
  value: unknown,
): value is Record<string, unknown> & CompleteRecipeCore {
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

function hasRuntimePreview(value: Record<string, unknown>): boolean {
  return (
    (value.previewArtifactId === null && value.previewLabel === null) ||
    (typeof value.previewArtifactId === "string" &&
      value.previewArtifactId.trim().length > 0 &&
      value.previewLabel === "AI-generated image")
  );
}

function isCompleteRecipe(value: unknown): value is CompleteRecipeView {
  return hasCompleteRecipeCore(value) && hasRuntimePreview(value);
}

function isStoredCompleteRecipe(value: unknown): value is CompleteRecipeView {
  return (
    hasCompleteRecipeCore(value) &&
    value.previewArtifactId === null &&
    value.previewLabel === null
  );
}

function projectRecipeForStorage(recipe: CompleteRecipeCore): CompleteRecipeView {
  return {
    optionId: recipe.optionId,
    name: recipe.name,
    cuisine: recipe.cuisine,
    servings: recipe.servings,
    totalMinutes: recipe.totalMinutes,
    ingredients: recipe.ingredients.map((ingredient) => ({
      name: ingredient.name,
      quantity: ingredient.quantity,
      availability: ingredient.availability,
      substitution: ingredient.substitution,
    })),
    steps: recipe.steps.map((step) => ({
      number: step.number,
      instruction: step.instruction,
      durationMinutes: step.durationMinutes,
      ...(step.doneWhen === undefined ? {} : { doneWhen: step.doneWhen }),
      ...(step.heatLevel === undefined ? {} : { heatLevel: step.heatLevel }),
    })),
    tips: [...recipe.tips],
    substitutions: [...recipe.substitutions],
    nutrition:
      recipe.nutrition === null
        ? null
        : {
            caloriesKcal: recipe.nutrition.caloriesKcal,
            proteinG: recipe.nutrition.proteinG,
            carbohydratesG: recipe.nutrition.carbohydratesG,
            fatG: recipe.nutrition.fatG,
            dietTags: [...recipe.nutrition.dietTags],
            allergenWarnings: [...recipe.nutrition.allergenWarnings],
            disclaimer: recipe.nutrition.disclaimer,
          },
    nutritionNotice: recipe.nutritionNotice,
    allergenNotice: recipe.allergenNotice,
    assumptions: [...recipe.assumptions],
    warnings: [...recipe.warnings],
    previewArtifactId: null,
    previewLabel: null,
  };
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

function hasValidEntryMetadata(value: unknown): value is Record<string, unknown> & {
  id: string;
  savedAt: string;
  recipe: unknown;
} {
  return (
    isRecord(value) &&
    typeof value.id === "string" &&
    value.id.trim().length > 0 &&
    isIsoDate(value.savedAt)
  );
}

function migrateV1Entry(value: unknown): SavedRecipeEntry | null {
  if (!hasValidEntryMetadata(value) || !hasCompleteRecipeCore(value.recipe)) {
    return null;
  }

  const recipe = projectRecipeForStorage(value.recipe);

  return {
    id: value.id,
    savedAt: value.savedAt,
    recipe,
    photo: null,
    progress: null,
    ingredientSources: null,
  };
}

function migrateV2Entry(value: unknown): SavedRecipeEntry | null {
  if (!hasValidEntryMetadata(value) || !hasCompleteRecipeCore(value.recipe)) {
    return null;
  }

  const recipe = projectRecipeForStorage(value.recipe);

  return {
    id: value.id,
    savedAt: value.savedAt,
    recipe,
    photo: normalizePhoto(value.photo),
    progress: normalizeProgress(value.progress, recipe),
    ingredientSources: normalizeIngredientSources(value.ingredientSources),
  };
}

function normalizeSavedRecipeEntry(value: unknown): SavedRecipeEntry | null {
  if (!hasValidEntryMetadata(value) || !isStoredCompleteRecipe(value.recipe)) {
    return null;
  }

  const recipe = projectRecipeForStorage(value.recipe);

  return {
    id: value.id,
    savedAt: value.savedAt,
    recipe,
    photo: normalizePhoto(value.photo),
    progress: normalizeProgress(value.progress, recipe),
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

function parseV2Payload(serialized: string): StorageReadResult {
  try {
    const payload: unknown = JSON.parse(serialized);
    if (
      !isRecord(payload) ||
      payload.version !== V2_SAVED_RECIPES_VERSION ||
      !Array.isArray(payload.entries)
    ) {
      return { ok: false, entries: [] };
    }

    return {
      ok: true,
      entries: payload.entries
        .map(migrateV2Entry)
        .filter((entry): entry is SavedRecipeEntry => entry !== null),
    };
  } catch {
    return { ok: false, entries: [] };
  }
}

function parseV1Payload(serialized: string): StorageReadResult {
  try {
    const payload: unknown = JSON.parse(serialized);
    if (!isRecord(payload) || !Array.isArray(payload.entries)) {
      return { ok: false, entries: [] };
    }

    if (payload.version === V1_SAVED_RECIPES_VERSION) {
      return {
        ok: true,
        entries: payload.entries
          .map(migrateV1Entry)
          .filter((entry): entry is SavedRecipeEntry => entry !== null),
      };
    }

    // Early v2 builds wrote the upgraded payload under the v1 key. Hydrate
    // those snapshots without mutating storage during a read.
    if (payload.version === V2_SAVED_RECIPES_VERSION) {
      return {
        ok: true,
        entries: payload.entries
          .map(migrateV2Entry)
          .filter((entry): entry is SavedRecipeEntry => entry !== null),
      };
    }

    return { ok: false, entries: [] };
  } catch {
    return { ok: false, entries: [] };
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

  const v2 = readStorageItem(storage, V2_SAVED_RECIPES_STORAGE_KEY);
  if (!v2.ok) {
    return { ok: false, entries: [] };
  }

  if (v2.serialized !== null) {
    return parseV2Payload(v2.serialized);
  }

  const v1 = readStorageItem(storage, V1_SAVED_RECIPES_STORAGE_KEY);
  if (!v1.ok) {
    return { ok: false, entries: [] };
  }

  return v1.serialized === null
    ? { ok: true, entries: [] }
    : parseV1Payload(v1.serialized);
}

function removeLegacyPayloads(storage: Storage): void {
  try {
    storage.removeItem(V2_SAVED_RECIPES_STORAGE_KEY);
  } catch {
    // The v3 write already succeeded, so stale legacy data is safe to ignore.
  }

  try {
    storage.removeItem(V1_SAVED_RECIPES_STORAGE_KEY);
  } catch {
    // The v3 write already succeeded, so stale legacy data is safe to ignore.
  }
}

function writeSavedRecipes(storage: Storage, entries: SavedRecipeEntry[]): boolean {
  const payload: SavedRecipesPayload = {
    version: SAVED_RECIPES_VERSION,
    entries,
  };

  try {
    storage.setItem(SAVED_RECIPES_STORAGE_KEY, JSON.stringify(payload));
    removeLegacyPayloads(storage);
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
  if (!current.ok || !isCompleteRecipe(recipe)) {
    return { ok: false, entries: current.entries };
  }

  const storedRecipe = projectRecipeForStorage(recipe);
  const normalizedSnapshot = normalizeSnapshot(snapshot, storedRecipe);
  if (normalizedSnapshot === null) {
    return { ok: false, entries: current.entries };
  }

  const entry: SavedRecipeEntry = {
    id: nextEntryId(current.entries, storedRecipe),
    savedAt: new Date().toISOString(),
    recipe: storedRecipe,
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
