export const PANTRY_STAPLES: readonly string[] = Object.freeze([
  "Salt",
  "Pepper powder",
  "Oil or ghee",
  "Chilli powder",
  "Onion",
  "Garlic",
  "Ginger",
]);

export const PANTRY_STAPLES_STORAGE_KEY = "cook-mantra:pantry-staples";

const MAX_PANTRY_STAPLE_LENGTH = 80;

function normalizeName(name: string): string {
  return name.trim().replace(/\s+/g, " ");
}

function isValidName(name: unknown): name is string {
  if (typeof name !== "string") {
    return false;
  }

  const normalized = normalizeName(name);
  return normalized.length > 0 && normalized.length <= MAX_PANTRY_STAPLE_LENGTH;
}

export function normalizePantryStaples(names: readonly string[]): string[] {
  const normalizedNames: string[] = [];
  const seenNames = new Set<string>();

  for (const name of names) {
    if (!isValidName(name)) {
      continue;
    }

    const normalized = normalizeName(name);
    const comparisonName = normalized.toLowerCase();
    if (seenNames.has(comparisonName)) {
      continue;
    }

    seenNames.add(comparisonName);
    normalizedNames.push(normalized);
  }

  return normalizedNames.length > 0 ? normalizedNames : [...PANTRY_STAPLES];
}

function parseStoredPantryStaples(storedValue: string | null): string[] | null {
  if (storedValue === null) {
    return null;
  }

  const parsedValue: unknown = JSON.parse(storedValue);
  if (
    !Array.isArray(parsedValue) ||
    parsedValue.length === 0 ||
    !parsedValue.every(isValidName)
  ) {
    return null;
  }

  return normalizePantryStaples(parsedValue);
}

export function loadPantryStaples(): string[] {
  if (typeof window === "undefined") {
    return [...PANTRY_STAPLES];
  }

  try {
    return (
      parseStoredPantryStaples(
        window.localStorage.getItem(PANTRY_STAPLES_STORAGE_KEY),
      ) ?? [...PANTRY_STAPLES]
    );
  } catch {
    return [...PANTRY_STAPLES];
  }
}

export function savePantryStaples(names: readonly string[]): boolean {
  if (
    typeof window === "undefined" ||
    names.length === 0 ||
    !names.every(isValidName)
  ) {
    return false;
  }

  try {
    window.localStorage.setItem(
      PANTRY_STAPLES_STORAGE_KEY,
      JSON.stringify(normalizePantryStaples(names)),
    );
    return true;
  } catch {
    return false;
  }
}
