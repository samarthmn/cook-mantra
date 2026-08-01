import { afterEach, describe, expect, it, vi } from "vitest";

import type { CompleteRecipeView } from "./cook-session-state";
import {
  downloadRecipeMarkdown,
  recipeMarkdownFilename,
  recipeToMarkdown,
} from "./recipe-markdown";

const originalCreateObjectUrl = Object.getOwnPropertyDescriptor(URL, "createObjectURL");
const originalRevokeObjectUrl = Object.getOwnPropertyDescriptor(URL, "revokeObjectURL");

const fullRecipe: CompleteRecipeView = {
  optionId: "option-paneer",
  name: "Tomato & Paneer Skillet",
  cuisine: "Indian",
  servings: 2,
  totalMinutes: 35,
  ingredients: [
    {
      name: "Paneer",
      quantity: "200 g",
      availability: "available",
      substitution: "firm tofu",
    },
    {
      name: "Tomato",
      quantity: "2",
      availability: "missing",
      substitution: null,
    },
  ],
  steps: [
    {
      number: 1,
      instruction: "Warm the pan.",
      durationMinutes: 2,
      doneWhen: "The pan feels hot.",
      heatLevel: "medium",
    },
    {
      number: 2,
      instruction: "Add the tomatoes.",
      durationMinutes: null,
    },
  ],
  tips: ["Keep the paneer tender."],
  substitutions: ["Paneer → firm tofu."],
  nutrition: {
    caloriesKcal: 510,
    proteinG: 28,
    carbohydratesG: 20,
    fatG: 35,
    dietTags: ["vegetarian", "gluten-free"],
    allergenWarnings: ["milk"],
    disclaimer: "Values are estimates per serving.",
  },
  nutritionNotice: "Estimated values; not medical advice.",
  allergenNotice: "Check ingredient labels for allergens.",
  assumptions: ["A wide skillet is available."],
  warnings: ["Paneer contains milk."],
};

afterEach(() => {
  vi.unstubAllGlobals();
  restoreUrlProperty("createObjectURL", originalCreateObjectUrl);
  restoreUrlProperty("revokeObjectURL", originalRevokeObjectUrl);
});

function restoreUrlProperty(
  property: "createObjectURL" | "revokeObjectURL",
  descriptor: PropertyDescriptor | undefined,
): void {
  if (descriptor) {
    Object.defineProperty(URL, property, descriptor);
  } else {
    Reflect.deleteProperty(URL, property);
  }
}

describe("recipeToMarkdown", () => {
  it("renders the complete recipe in a stable, readable order", () => {
    expect(recipeToMarkdown(fullRecipe)).toBe(`# Tomato & Paneer Skillet

- **Cuisine:** Indian
- **Servings:** 2
- **Total time:** 35 minutes

## Ingredients

- **Paneer:** 200 g _(available)_
  - Substitution: firm tofu
- **Tomato:** 2 _(missing)_

## Steps

1. Warm the pan.
   - Duration: 2 minutes
   - Done when: The pan feels hot.
   - Heat level: medium
2. Add the tomatoes.

## Tips

- Keep the paneer tender.

## Substitutions

- Paneer → firm tofu.

## Assumptions

- A wide skillet is available.

## Warnings

- Paneer contains milk.

## Nutrition

- **Calories:** 510 kcal
- **Protein:** 28 g
- **Carbohydrates:** 20 g
- **Fat:** 35 g
- **Diet tags:** vegetarian, gluten-free
- **Allergen warnings:** milk
- **Disclaimer:** Values are estimates per serving.

> **Nutrition notice:** Estimated values; not medical advice.
>
> **Allergen notice:** Check ingredient labels for allergens.
`);
  });

  it("omits absent optional fields and empty sections cleanly", () => {
    const minimalRecipe: CompleteRecipeView = {
      ...fullRecipe,
      optionId: "option-rice",
      name: "Lemon Rice",
      cuisine: "South Indian",
      servings: 1,
      totalMinutes: 1,
      ingredients: [
        {
          name: "Rice",
          quantity: "1 cup",
          availability: "available",
          substitution: null,
        },
      ],
      steps: [
        {
          number: 1,
          instruction: "Steam the rice.",
          durationMinutes: null,
          doneWhen: undefined,
          heatLevel: undefined,
        },
      ],
      tips: [],
      substitutions: [],
      nutrition: null,
      assumptions: [],
      warnings: [],
    };

    expect(recipeToMarkdown(minimalRecipe)).toBe(`# Lemon Rice

- **Cuisine:** South Indian
- **Servings:** 1
- **Total time:** 1 minute

## Ingredients

- **Rice:** 1 cup _(available)_

## Steps

1. Steam the rice.

> **Nutrition notice:** Estimated values; not medical advice.
>
> **Allergen notice:** Check ingredient labels for allergens.
`);
  });

  it("collapses whitespace in interpolated text to keep every value on one line", () => {
    const multilineRecipe: CompleteRecipeView = {
      ...fullRecipe,
      name: "  Tomato\n\tPaneer  Skillet ",
      cuisine: " North\n  Indian ",
      ingredients: [
        {
          name: " Paneer\n cubes ",
          quantity: " 200\n g ",
          availability: "available",
          substitution: " firm\n\ttofu ",
        },
      ],
      steps: [
        {
          number: 1,
          instruction: " Warm\n  the pan. ",
          durationMinutes: null,
          doneWhen: " the oil\n shimmers ",
          heatLevel: "medium-high",
        },
      ],
      tips: [" Keep\n the paneer tender. "],
      substitutions: [" Paneer\n → tofu. "],
      assumptions: [" A wide\n skillet is available. "],
      warnings: [" Handle\n the hot pan carefully. "],
      nutrition: {
        ...fullRecipe.nutrition!,
        dietTags: [" high\n protein "],
        allergenWarnings: [" tree\n nuts "],
        disclaimer: " Values are\n estimates. ",
      },
      nutritionNotice: " Estimated values;\n not medical advice. ",
      allergenNotice: " Check ingredient\n labels for allergens. ",
    };

    expect(recipeToMarkdown(multilineRecipe)).toBe(`# Tomato Paneer Skillet

- **Cuisine:** North Indian
- **Servings:** 2
- **Total time:** 35 minutes

## Ingredients

- **Paneer cubes:** 200 g _(available)_
  - Substitution: firm tofu

## Steps

1. Warm the pan.
   - Done when: the oil shimmers
   - Heat level: medium-high

## Tips

- Keep the paneer tender.

## Substitutions

- Paneer → tofu.

## Assumptions

- A wide skillet is available.

## Warnings

- Handle the hot pan carefully.

## Nutrition

- **Calories:** 510 kcal
- **Protein:** 28 g
- **Carbohydrates:** 20 g
- **Fat:** 35 g
- **Diet tags:** high protein
- **Allergen warnings:** tree nuts
- **Disclaimer:** Values are estimates.

> **Nutrition notice:** Estimated values; not medical advice.
>
> **Allergen notice:** Check ingredient labels for allergens.
`);
  });
});

describe("recipeMarkdownFilename", () => {
  it("keeps Unicode letters and numbers in a kebab-cased Markdown filename", () => {
    expect(recipeMarkdownFilename("  Crème brûlée / Deluxe!  ")).toBe(
      "crème-brûlée-deluxe.md",
    );
    expect(recipeMarkdownFilename("पालक पनीर")).toBe("पालक-पनीर.md");
    expect(recipeMarkdownFilename("!!!")).toBe("recipe.md");
  });
});

describe("downloadRecipeMarkdown", () => {
  it("downloads the generated Markdown with the recipe filename", () => {
    let downloadedAs = "";
    const click = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(function (this: HTMLAnchorElement) {
        downloadedAs = this.download;
      });
    const createObjectURL = vi.fn<(blob: Blob) => string>(() => "blob:recipe-markdown");
    const revokeObjectURL = vi.fn();
    Object.defineProperty(URL, "createObjectURL", {
      configurable: true,
      value: createObjectURL,
    });
    Object.defineProperty(URL, "revokeObjectURL", {
      configurable: true,
      value: revokeObjectURL,
    });

    expect(downloadRecipeMarkdown(fullRecipe)).toBe(true);
    expect(createObjectURL).toHaveBeenCalledOnce();
    expect(createObjectURL.mock.calls[0]?.[0]).toBeInstanceOf(Blob);
    expect(downloadedAs).toBe("tomato-paneer-skillet.md");
    expect(click).toHaveBeenCalledOnce();
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:recipe-markdown");
  });

  it("does nothing when browser download APIs are unavailable", () => {
    vi.stubGlobal("document", undefined);

    expect(downloadRecipeMarkdown(fullRecipe)).toBe(false);
  });
});
