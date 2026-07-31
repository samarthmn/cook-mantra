import type {
  CompleteRecipeView,
  IngredientAvailability,
  IngredientView,
  PreferenceView,
  RecipeOptionView,
} from "./cook-session-state";

const pantryNames = [
  "Salt",
  "Pepper powder",
  "Oil or ghee",
  "Chilli powder",
  "Onion",
  "Garlic",
  "Ginger",
] as const;

export function demoPantryIngredients(): IngredientView[] {
  return pantryNames.map((name, index) => ({
    id: `pantry-${index + 1}`,
    name,
    source: "pantry_suggestion",
    confidence: null,
    confirmed: false,
  }));
}

export function demoDetectedIngredients(weak = false): IngredientView[] {
  const detected = weak
    ? [{ name: "Tomatoes", confidence: 0.41 }]
    : [
        { name: "Tomatoes", confidence: 0.96 },
        { name: "Paneer", confidence: 0.91 },
        { name: "Spinach", confidence: 0.88 },
        { name: "Green chillies", confidence: 0.74 },
        { name: "Curd", confidence: 0.63 },
        { name: "Coriander", confidence: 0.58 },
      ];

  return detected.map((ingredient, index) => ({
    id: `detected-${index + 1}`,
    name: ingredient.name,
    source: "detected",
    confidence: ingredient.confidence,
    confirmed: true,
  }));
}

interface DemoOptionFixture {
  id: string;
  name: string;
  cuisine: string;
  summary: string;
  totalMinutes: number;
  difficulty: "easy" | "medium";
  caloriesKcal: number;
  proteinG: number;
  carbohydratesG: number;
  fatG: number;
  usedIngredients: string[];
  dietTags: string[];
  missing: string[];
  optional: string[];
  allergens: string[];
}

const optionFixtures: DemoOptionFixture[] = [
  {
    id: "o1",
    name: "Palak Paneer",
    cuisine: "North Indian",
    summary:
      "Silky spinach gravy with soft paneer cubes — a beginner-friendly classic.",
    totalMinutes: 35,
    difficulty: "easy",
    caloriesKcal: 410,
    proteinG: 19,
    carbohydratesG: 14,
    fatG: 31,
    usedIngredients: [
      "Spinach",
      "Paneer",
      "Tomatoes",
      "Green chillies",
      "Curd",
      "Coriander",
    ],
    dietTags: ["Vegetarian", "Gluten-free"],
    missing: ["cream"],
    optional: ["coriander"],
    allergens: ["dairy"],
  },
  {
    id: "o2",
    name: "Paneer Bhurji",
    cuisine: "North Indian",
    summary:
      "Scrambled paneer with tomato, onion and green chilli. Fast weeknight food.",
    totalMinutes: 20,
    difficulty: "easy",
    caloriesKcal: 360,
    proteinG: 21,
    carbohydratesG: 9,
    fatG: 27,
    usedIngredients: ["Paneer", "Tomatoes", "Green chillies", "Coriander"],
    dietTags: ["Vegetarian", "Gluten-free"],
    missing: [],
    optional: ["coriander"],
    allergens: ["dairy"],
  },
  {
    id: "o3",
    name: "Tomato Rasam",
    cuisine: "South Indian",
    summary: "A light, peppery tomato broth. Drink it or pour it over rice.",
    totalMinutes: 25,
    difficulty: "medium",
    caloriesKcal: 120,
    proteinG: 4,
    carbohydratesG: 18,
    fatG: 4,
    usedIngredients: ["Tomatoes", "Green chillies"],
    dietTags: ["Vegan", "Gluten-free"],
    missing: ["tamarind"],
    optional: [],
    allergens: [],
  },
  {
    id: "o4",
    name: "Chilli Paneer",
    cuisine: "Indo-Chinese",
    summary: "Crisp paneer tossed in a hot-sweet chilli glaze with peppers and onion.",
    totalMinutes: 30,
    difficulty: "medium",
    caloriesKcal: 450,
    proteinG: 20,
    carbohydratesG: 26,
    fatG: 30,
    usedIngredients: ["Paneer", "Green chillies"],
    dietTags: ["Vegetarian"],
    missing: ["soy sauce", "cornflour"],
    optional: [],
    allergens: ["dairy", "soy"],
  },
  {
    id: "o5",
    name: "Spinach Tomato Dal",
    cuisine: "North Indian",
    summary: "Comforting lentils simmered with spinach and tomato tempering.",
    totalMinutes: 30,
    difficulty: "easy",
    caloriesKcal: 260,
    proteinG: 13,
    carbohydratesG: 34,
    fatG: 8,
    usedIngredients: ["Spinach", "Tomatoes", "Green chillies", "Curd"],
    dietTags: ["Vegan", "Gluten-free"],
    missing: ["toor dal"],
    optional: ["curd"],
    allergens: [],
  },
  {
    id: "o6",
    name: "Paneer-Stuffed Tomatoes",
    cuisine: "Continental",
    summary: "Whole tomatoes baked with a spiced paneer-spinach filling.",
    totalMinutes: 40,
    difficulty: "medium",
    caloriesKcal: 330,
    proteinG: 16,
    carbohydratesG: 15,
    fatG: 24,
    usedIngredients: ["Tomatoes", "Paneer", "Spinach"],
    dietTags: ["Vegetarian", "Gluten-free"],
    missing: [],
    optional: ["cheese"],
    allergens: ["dairy"],
  },
];

interface CreateDemoOptionsInput {
  preferences: PreferenceView;
  batchNumber: number;
  excludedIds: string[];
  ingredients: IngredientView[];
}

function preferenceAllergens(value: string): string[] {
  return value
    .split(",")
    .map((allergen) => allergen.trim().toLocaleLowerCase())
    .filter(Boolean);
}

export function createDemoOptions({
  preferences,
  batchNumber,
  excludedIds,
  ingredients,
}: CreateDemoOptionsInput): RecipeOptionView[] {
  const excluded = new Set(excludedIds);
  const avoidedAllergens = preferenceAllergens(preferences.allergens);

  return optionFixtures
    .filter((option) => !excluded.has(option.id))
    .filter((option) => option.totalMinutes <= preferences.maxMinutes)
    .filter((option) => {
      if (preferences.diet === "none") return true;
      if (preferences.diet === "vegan") return option.dietTags.includes("Vegan");
      return (
        option.dietTags.includes("Vegetarian") || option.dietTags.includes("Vegan")
      );
    })
    .filter(
      (option) =>
        !option.allergens.some((allergen) =>
          avoidedAllergens.includes(allergen.toLocaleLowerCase()),
        ),
    )
    .filter((option) =>
      option.usedIngredients.some((name) => ingredientIsConfirmed(name, ingredients)),
    )
    .slice(0, preferences.optionCount)
    .map((option) => ({
      id: option.id,
      name: option.name,
      summary: option.summary,
      cuisine: option.cuisine,
      totalMinutes: option.totalMinutes,
      difficulty: option.difficulty,
      usedIngredients: option.usedIngredients.filter((name) =>
        ingredientIsConfirmed(name, ingredients),
      ),
      missingIngredients: missingIngredientNames(option, ingredients).map((name) => ({
        name,
        reason: "Needed to complete the intended dish.",
        substitution: null,
      })),
      optionalIngredients: option.optional.map((name) => ({
        name,
        reason: "Adds a useful finishing touch.",
        substitution: null,
      })),
      nutrition: {
        caloriesKcal: option.caloriesKcal,
        proteinG: option.proteinG,
        carbohydratesG: option.carbohydratesG,
        fatG: option.fatG,
        dietTags: option.dietTags,
        allergenWarnings: option.allergens,
        disclaimer: "Estimated values; not medical advice.",
      },
      previewArtifactId: null,
      previewLabel: "AI-generated illustration",
      warnings: [],
      batchNumber,
    }));
}

type RawAvailability = "have" | "pantry" | "missing" | "optional";

interface DemoRecipeFixture {
  name: string;
  cuisine: string;
  totalMinutes: number;
  assumptions: string[];
  ingredients: Array<[quantity: string, name: string, availability: RawAvailability]>;
  steps: string[];
  tips: string[];
  substitutions: string[];
}

const recipeFixtures: Record<string, DemoRecipeFixture> = {
  o1: {
    name: "Palak Paneer",
    cuisine: "North Indian",
    totalMinutes: 35,
    assumptions: ["medium spice level", "fresh spinach, not frozen"],
    ingredients: [
      ["250 g", "spinach", "have"],
      ["200 g", "paneer", "have"],
      ["2", "tomatoes", "have"],
      ["1", "green chilli", "have"],
      ["1", "onion, chopped", "pantry"],
      ["4 cloves", "garlic", "pantry"],
      ["2 tbsp", "oil or ghee", "pantry"],
      ["1 tsp", "chilli powder", "pantry"],
      ["to taste", "salt", "pantry"],
      ["3 tbsp", "cream", "missing"],
    ],
    steps: [
      "Blanch spinach in boiling water for 90 seconds, then move it straight into cold water.",
      "Blend the spinach with the green chilli into a smooth purée.",
      "Heat oil, then soften the onion and garlic over medium heat, about 5 minutes.",
      "Add chopped tomatoes, chilli powder and salt; cook until the oil separates.",
      "Pour in the spinach purée and simmer gently for 6–8 minutes.",
      "Fold in paneer cubes and cook 3 more minutes without boiling hard.",
      "Finish with cream — or whisked curd, since cream is missing.",
    ],
    tips: [
      "Cold water after blanching keeps the gravy bright green.",
      "Soak paneer in warm salted water for 10 minutes to keep it soft.",
    ],
    substitutions: [
      "Cream → whisked curd (you confirmed curd).",
      "Paneer → firm tofu for a vegan version.",
    ],
  },
  o2: {
    name: "Paneer Bhurji",
    cuisine: "North Indian",
    totalMinutes: 20,
    assumptions: ["crumbled, not cubed paneer"],
    ingredients: [
      ["200 g", "paneer, crumbled", "have"],
      ["2", "tomatoes, chopped", "have"],
      ["1", "green chilli, sliced", "have"],
      ["1", "onion, diced", "pantry"],
      ["2 tbsp", "oil or ghee", "pantry"],
      ["1 tsp", "chilli powder", "pantry"],
      ["to taste", "salt", "pantry"],
      ["a handful", "coriander", "optional"],
    ],
    steps: [
      "Heat oil in a wide pan over medium-high heat.",
      "Fry the onion and green chilli until the onion edges brown.",
      "Add tomatoes, chilli powder and salt; cook until jammy, about 6 minutes.",
      "Crumble in the paneer and toss for 2–3 minutes — no longer, or it toughens.",
      "Taste, adjust salt, and finish with coriander if you have it.",
    ],
    tips: [
      "High heat and a wide pan stop the bhurji going watery.",
      "Great stuffed into a toasted sandwich the next day.",
    ],
    substitutions: ["Paneer → scrambled tofu.", "Coriander is optional."],
  },
  o3: {
    name: "Tomato Rasam",
    cuisine: "South Indian",
    totalMinutes: 25,
    assumptions: ["no rasam powder — a simple pepper-cumin base instead"],
    ingredients: [
      ["4", "ripe tomatoes", "have"],
      ["1", "green chilli, slit", "have"],
      ["4 cloves", "garlic, crushed", "pantry"],
      ["1 tsp", "pepper powder", "pantry"],
      ["1 tbsp", "oil or ghee", "pantry"],
      ["to taste", "salt", "pantry"],
      ["small ball", "tamarind", "missing"],
    ],
    steps: [
      "Simmer chopped tomatoes in 500 ml water with salt for 10 minutes.",
      "Mash the tomatoes roughly in the pot to release their pulp.",
      "Add crushed garlic, slit chilli and pepper powder; simmer 5 minutes.",
      "No tamarind? Cook the tomatoes 5 minutes longer to deepen the sourness.",
      "Heat oil, bloom a pinch of pepper in it, and pour over the rasam.",
      "Rest 5 minutes before serving so the flavours settle.",
    ],
    tips: ["Rasam should stay thin — resist thickening it."],
    substitutions: ["Tamarind → extra-ripe tomatoes cooked longer."],
  },
  o4: {
    name: "Chilli Paneer",
    cuisine: "Indo-Chinese",
    totalMinutes: 30,
    assumptions: ["a restaurant-style dry version, not gravy"],
    ingredients: [
      ["200 g", "paneer, cubed", "have"],
      ["1", "green chilli, sliced", "have"],
      ["1", "onion, in petals", "pantry"],
      ["4 cloves", "garlic, minced", "pantry"],
      ["3 tbsp", "oil", "pantry"],
      ["1 tsp", "chilli powder", "pantry"],
      ["2 tbsp", "soy sauce", "missing"],
      ["2 tbsp", "cornflour", "missing"],
    ],
    steps: [
      "Toss paneer cubes in cornflour, salt and chilli powder.",
      "Shallow-fry the cubes until golden on all sides; set aside.",
      "Stir-fry garlic, green chilli and onion petals on high heat for 2 minutes.",
      "Add soy sauce and a splash of water to make a quick glaze.",
      "Return the paneer, toss to coat, and cook 1 more minute.",
      "Serve hot — the coating softens as it sits.",
    ],
    tips: ["Keep the flame high; this dish hates a crowded, cool pan."],
    substitutions: ["Cornflour → rice flour or plain flour."],
  },
  o5: {
    name: "Spinach Tomato Dal",
    cuisine: "North Indian",
    totalMinutes: 30,
    assumptions: ["pressure cooker or instant pot available"],
    ingredients: [
      ["150 g", "toor dal", "missing"],
      ["150 g", "spinach, chopped", "have"],
      ["2", "tomatoes", "have"],
      ["1", "green chilli", "have"],
      ["4 cloves", "garlic", "pantry"],
      ["1 tbsp", "oil or ghee", "pantry"],
      ["1 tsp", "chilli powder", "pantry"],
      ["to taste", "salt", "pantry"],
    ],
    steps: [
      "Pressure-cook the dal with salt and 400 ml water until soft.",
      "Sauté garlic and green chilli in oil until fragrant.",
      "Add tomatoes and chilli powder; cook down to a thick masala.",
      "Stir in spinach and wilt it for 2 minutes.",
      "Combine with the cooked dal and simmer 5 minutes.",
      "Adjust salt and consistency with hot water.",
    ],
    tips: ["A spoon of ghee at the end transforms this dal."],
    substitutions: ["Toor dal → red lentils."],
  },
  o6: {
    name: "Paneer-Stuffed Tomatoes",
    cuisine: "Continental",
    totalMinutes: 40,
    assumptions: ["an oven or air fryer available", "large, firm tomatoes"],
    ingredients: [
      ["4 large", "tomatoes", "have"],
      ["150 g", "paneer, crumbled", "have"],
      ["80 g", "spinach, chopped", "have"],
      ["1", "onion, minced", "pantry"],
      ["2 cloves", "garlic", "pantry"],
      ["1 tbsp", "oil", "pantry"],
      ["to taste", "salt", "pantry"],
      ["30 g", "cheese", "optional"],
    ],
    steps: [
      "Slice the tops off the tomatoes and scoop out the pulp; save it.",
      "Sauté onion and garlic in oil, then add the pulp and cook it down.",
      "Stir in spinach, crumbled paneer and salt; cook 3 minutes.",
      "Fill the tomato shells with the paneer mixture.",
      "Bake at 190 °C for 18–20 minutes until the shells wrinkle.",
      "Rest 5 minutes — the filling sets as it cools.",
    ],
    tips: ["Drain the salted tomato shells upside down before filling."],
    substitutions: ["Cheese topping is optional."],
  },
};

function normalizeIngredientName(name: string): string {
  return name
    .toLocaleLowerCase()
    .split(",")[0]
    .replaceAll(/[^a-z ]/g, "")
    .trim();
}

function ingredientIsConfirmed(
  name: string,
  ingredients: IngredientView[],
  source?: IngredientView["source"],
): boolean {
  const normalized = normalizeIngredientName(name);
  return ingredients.some((ingredient) => {
    if ((source && ingredient.source !== source) || !ingredient.confirmed) {
      return false;
    }
    const confirmedName = normalizeIngredientName(ingredient.name);
    return confirmedName.includes(normalized) || normalized.includes(confirmedName);
  });
}

function resolveAvailability(
  rawAvailability: RawAvailability,
  name: string,
  ingredients: IngredientView[],
): IngredientAvailability {
  if (rawAvailability === "optional") return "optional";
  if (rawAvailability === "missing") {
    return ingredientIsConfirmed(name, ingredients) ? "available" : "missing";
  }
  if (rawAvailability === "pantry") {
    return ingredientIsConfirmed(name, ingredients, "pantry_suggestion")
      ? "available"
      : "missing";
  }
  return ingredientIsConfirmed(name, ingredients) ? "available" : "missing";
}

function missingIngredientNames(
  option: DemoOptionFixture,
  ingredients: IngredientView[],
): string[] {
  const fixture = recipeFixtures[option.id];
  const requiredNames = fixture
    ? fixture.ingredients
        .filter(([, , availability]) => availability !== "optional")
        .filter(
          ([, name, availability]) =>
            resolveAvailability(availability, name, ingredients) === "missing",
        )
        .map(([, name]) => name)
    : option.missing;
  const explicitMissingNames = option.missing.filter(
    (name) => !ingredientIsConfirmed(name, ingredients),
  );
  const seen = new Set<string>();

  return [...requiredNames, ...explicitMissingNames].filter((name) => {
    const normalized = normalizeIngredientName(name);
    if (seen.has(normalized)) return false;
    seen.add(normalized);
    return true;
  });
}

function contextualizeSubstitution(
  substitution: string,
  ingredients: IngredientView[],
): string {
  const claim = substitution.match(/\(you confirmed ([^)]+)\)\./i);
  const claimedIngredient = claim?.[1];
  if (!claimedIngredient || ingredientIsConfirmed(claimedIngredient, ingredients)) {
    return substitution;
  }
  return substitution.replace(
    /\(you confirmed [^)]+\)\./i,
    `(${claimedIngredient} is not confirmed).`,
  );
}

export function createDemoRecipes(
  options: RecipeOptionView[],
  ingredients: IngredientView[],
  servings: number,
): Record<string, CompleteRecipeView> {
  return Object.fromEntries(
    options.flatMap((option) => {
      const fixture = recipeFixtures[option.id];
      if (!fixture) return [];
      const recipe: CompleteRecipeView = {
        optionId: option.id,
        name: fixture.name,
        cuisine: fixture.cuisine,
        servings,
        totalMinutes: fixture.totalMinutes,
        ingredients: fixture.ingredients.map(([quantity, name, availability]) => ({
          name,
          quantity,
          availability: resolveAvailability(availability, name, ingredients),
          substitution: null,
        })),
        steps: fixture.steps.map((instruction, index) => ({
          number: index + 1,
          instruction,
          durationMinutes: null,
        })),
        tips: fixture.tips,
        substitutions: fixture.substitutions.map((substitution) =>
          contextualizeSubstitution(substitution, ingredients),
        ),
        nutritionNotice: "Estimated values; not medical advice.",
        allergenNotice: option.nutrition?.allergenWarnings.length
          ? `Contains ${option.nutrition.allergenWarnings.join(" and ")}.`
          : "Check ingredient labels for allergens.",
        assumptions: fixture.assumptions,
        warnings: fixture.ingredients
          .filter(
            ([, name, availability]) =>
              resolveAvailability(availability, name, ingredients) === "missing",
          )
          .map(([, name]) => `${name} is not confirmed.`),
      };
      return [[option.id, recipe] as const];
    }),
  );
}
