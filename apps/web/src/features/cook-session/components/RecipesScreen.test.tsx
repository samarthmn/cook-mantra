import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type {
  CompleteRecipeView,
  IngredientView,
  RecipeOptionView,
} from "../model/cook-session-state";
import { RecipesScreen } from "./RecipesScreen";

const recipe: CompleteRecipeView = {
  optionId: "option-1",
  name: "Test Curry",
  cuisine: "Indian",
  servings: 2,
  totalMinutes: 20,
  ingredients: [
    {
      name: "salt",
      quantity: "to taste",
      availability: "available",
      substitution: null,
    },
  ],
  steps: [{ number: 1, instruction: "Season the curry.", durationMinutes: null }],
  tips: [],
  substitutions: [],
  nutrition: null,
  nutritionNotice: "Estimated values; not medical advice.",
  allergenNotice: "Check ingredient labels for allergens.",
  assumptions: [],
  warnings: [],
};

const confirmedIngredients: IngredientView[] = [
  {
    id: "pantry-salt",
    name: "Salt",
    source: "pantry_suggestion",
    confidence: null,
    confirmed: true,
  },
];

function optionWithNutrition(
  nutrition: NonNullable<RecipeOptionView["nutrition"]>,
): RecipeOptionView {
  return {
    id: recipe.optionId,
    name: recipe.name,
    summary: "",
    cuisine: recipe.cuisine,
    totalMinutes: recipe.totalMinutes,
    difficulty: "easy",
    usedIngredients: [],
    missingIngredients: [],
    optionalIngredients: [],
    nutrition,
    previewArtifactId: null,
    previewLabel: null,
    warnings: [],
    batchNumber: 1,
  };
}

afterEach(cleanup);

function renderRecipe(
  recipeView: CompleteRecipeView,
  {
    ingredients = confirmedIngredients,
    options = [],
  }: { ingredients?: IngredientView[]; options?: RecipeOptionView[] } = {},
) {
  return render(
    <RecipesScreen
      recipes={{ [recipeView.optionId]: recipeView }}
      failures={{}}
      options={options}
      confirmedIngredients={ingredients}
      activeRecipeId={recipeView.optionId}
      completedSteps={{}}
      onSetActiveRecipe={vi.fn()}
      onToggleStep={vi.fn()}
      onRetryFailed={vi.fn()}
      onBack={vi.fn()}
      onReset={vi.fn()}
    />,
  );
}

describe("RecipesScreen", () => {
  it("keeps a confirmed pantry ingredient visibly sourced as pantry", () => {
    renderRecipe(recipe);

    const ingredientRow = screen.getByText("salt").closest(".recipe-ingredient-row");
    expect(ingredientRow).not.toBeNull();
    expect(
      within(ingredientRow as HTMLElement).getByText("pantry"),
    ).toBeInTheDocument();
  });

  it("shows ingredient-level substitution guidance", async () => {
    const user = userEvent.setup();
    renderRecipe({
      ...recipe,
      ingredients: [
        {
          ...recipe.ingredients[0],
          substitution: "lime juice and a pinch of salt",
        },
      ],
    });

    await user.click(screen.getByRole("button", { name: "Ingredients (1)" }));
    expect(
      screen.getByText("Substitute: lime juice and a pinch of salt"),
    ).toBeVisible();
  });

  it("collapses only the ingredient list and exposes its count", async () => {
    const user = userEvent.setup();
    const recipeWithNutrition = {
      ...recipe,
      ingredients: [
        ...recipe.ingredients,
        {
          name: "carrot whole",
          quantity: "2 medium carrots, peeled and sliced 1/4-inch thick",
          availability: "available" as const,
          substitution: null,
        },
      ],
    };
    const option = optionWithNutrition({
      caloriesKcal: 420,
      proteinG: 12,
      carbohydratesG: 54,
      fatG: 16,
      dietTags: [],
      allergenWarnings: [],
      disclaimer: "Estimated values; not medical advice.",
    });

    renderRecipe(recipeWithNutrition, { options: [option] });

    const disclosure = screen.getByRole("button", { name: "Ingredients (2)" });
    const list = document.getElementById(
      disclosure.getAttribute("aria-controls") as string,
    );
    expect(disclosure).toHaveAttribute("aria-expanded", "false");
    expect(list).not.toBeVisible();
    expect(screen.getByText("420")).toBeVisible();
    expect(screen.getByText("Per serving — estimate")).toBeVisible();

    await user.click(disclosure);
    expect(disclosure).toHaveAttribute("aria-expanded", "true");
    expect(list).toBeVisible();

    await user.click(disclosure);
    expect(disclosure).toHaveAttribute("aria-expanded", "false");
    expect(list).not.toBeVisible();
    expect(screen.getByText("420")).toBeVisible();
  });

  it("prefers recipe-stage nutrition over the option estimate", () => {
    const option = optionWithNutrition({
      caloriesKcal: 420,
      proteinG: 12,
      carbohydratesG: 54,
      fatG: 16,
      dietTags: [],
      allergenWarnings: [],
      disclaimer: "Estimated values; not medical advice.",
    });

    renderRecipe(
      {
        ...recipe,
        nutrition: {
          caloriesKcal: 515,
          proteinG: 19,
          carbohydratesG: 61,
          fatG: 21,
          dietTags: [],
          allergenWarnings: [],
          disclaimer: "Estimated values; not medical advice.",
        },
      },
      { options: [option] },
    );

    expect(screen.getByText("515")).toBeVisible();
    expect(screen.queryByText("420")).not.toBeInTheDocument();
  });

  it("falls back to option nutrition when recipe nutrition is absent", () => {
    const option = optionWithNutrition({
      caloriesKcal: 420,
      proteinG: 12,
      carbohydratesG: 54,
      fatG: 16,
      dietTags: [],
      allergenWarnings: [],
      disclaimer: "Estimated values; not medical advice.",
    });

    renderRecipe({ ...recipe, nutrition: null }, { options: [option] });

    expect(screen.getByText("420")).toBeVisible();
    expect(screen.getByText("Per serving — estimate")).toBeVisible();
  });

  it("keeps long quantities, ingredient names, and source tags in separate row areas", async () => {
    const user = userEvent.setup();
    const quantity = "2 medium carrots, peeled and sliced 1/4-inch thick";
    renderRecipe(
      {
        ...recipe,
        ingredients: [
          {
            name: "carrot whole",
            quantity,
            availability: "available",
            substitution: null,
          },
        ],
      },
      {
        ingredients: [
          ...confirmedIngredients,
          {
            id: "detected-carrot",
            name: "carrot whole",
            source: "detected",
            confidence: 0.94,
            confirmed: true,
          },
        ],
      },
    );

    await user.click(screen.getByRole("button", { name: "Ingredients (1)" }));
    const row = screen.getByText(quantity).closest(".recipe-ingredient-row");
    expect(row).not.toBeNull();
    expect(within(row as HTMLElement).getByText(quantity)).toHaveClass(
      "recipe-ingredient-quantity",
    );
    expect(within(row as HTMLElement).getByText("carrot whole")).toHaveClass(
      "recipe-ingredient-name",
    );
    expect(within(row as HTMLElement).getByText("detected")).toHaveClass("tag-neutral");
  });

  it("shows the duration supplied for a method step", () => {
    renderRecipe({
      ...recipe,
      steps: [{ ...recipe.steps[0], durationMinutes: 3 }],
    });

    const methodStep = screen.getByRole("button", { name: /Season the curry/ });
    expect(within(methodStep).getByText("3 min")).toBeVisible();
  });

  it("shows a doneness cue and heat level in the method step metadata", () => {
    renderRecipe({
      ...recipe,
      steps: [
        {
          ...recipe.steps[0],
          durationMinutes: 3,
          doneWhen: "the oil shimmers but does not smoke",
          heatLevel: "medium-high",
        },
      ],
    });

    const methodStep = screen.getByRole("button", { name: /Season the curry/ });
    const doneWhen = within(methodStep).getByText(
      "Done when — the oil shimmers but does not smoke",
    );
    const duration = within(methodStep).getByText("3 min");
    const heatLevel = within(methodStep).getByText("medium-high heat");

    expect(doneWhen).toBeVisible();
    expect(doneWhen).toHaveClass("method-step-done-when");
    expect(duration.parentElement).toBe(heatLevel.parentElement);
    expect(duration.parentElement).toHaveClass("method-step-meta");
  });

  it("omits doneness and heat metadata when a method step has neither", () => {
    renderRecipe(recipe);

    const methodStep = screen.getByRole("button", { name: /Season the curry/ });
    expect(within(methodStep).queryByText(/^Done when/)).not.toBeInTheDocument();
    expect(within(methodStep).queryByText(/ heat$/)).not.toBeInTheDocument();
    expect(methodStep.querySelector(".method-step-meta")).toBeNull();
  });

  it("exposes recipe warnings as a labelled status list", () => {
    renderRecipe({
      ...recipe,
      warnings: ["Salt is not confirmed.", "Taste before serving."],
    });

    const warningStatus = screen.getByRole("status", { name: "Recipe warnings" });
    expect(within(warningStatus).getByText("Before you cook")).toBeVisible();
    expect(within(warningStatus).getAllByRole("listitem")).toHaveLength(2);
    expect(within(warningStatus).getByText("Salt is not confirmed.")).toBeVisible();
  });

  it("removes duplicate generated notes when switching recipes", () => {
    const firstRecipe = {
      ...recipe,
      warnings: ["Watch the heat.", "Watch the heat."],
      tips: ["Rest the dish.", "Rest the dish."],
      substitutions: ["Use lime.", "Use lime."],
    };
    const secondRecipe = {
      ...recipe,
      optionId: "option-2",
      name: "Second Curry",
      warnings: ["A different warning."],
      tips: ["A different tip."],
      substitutions: ["Use lemon."],
    };
    const recipes = {
      [firstRecipe.optionId]: firstRecipe,
      [secondRecipe.optionId]: secondRecipe,
    };
    const props = {
      recipes,
      failures: {},
      options: [],
      confirmedIngredients,
      completedSteps: {},
      onSetActiveRecipe: vi.fn(),
      onToggleStep: vi.fn(),
      onRetryFailed: vi.fn(),
      onBack: vi.fn(),
      onReset: vi.fn(),
    };
    const { rerender } = render(
      <RecipesScreen {...props} activeRecipeId={firstRecipe.optionId} />,
    );

    expect(screen.getAllByText("Watch the heat.")).toHaveLength(2);
    rerender(<RecipesScreen {...props} activeRecipeId={secondRecipe.optionId} />);

    expect(screen.queryByText("Watch the heat.")).toBeNull();
    expect(screen.queryByText("Rest the dish.")).toBeNull();
    expect(screen.queryByText("Use lime.")).toBeNull();
    expect(screen.getByText("A different warning.")).toBeVisible();
    expect(screen.getByText("A different tip.")).toBeVisible();
    expect(screen.getByText("Use lemon.")).toBeVisible();
  });

  it("shows detected, pantry, user-added, and unmatched availability provenance", async () => {
    const user = userEvent.setup();
    render(
      <RecipesScreen
        recipes={{
          [recipe.optionId]: {
            ...recipe,
            ingredients: [
              { ...recipe.ingredients[0], name: "Tomato" },
              { ...recipe.ingredients[0], name: "Salt" },
              { ...recipe.ingredients[0], name: "Basil" },
              { ...recipe.ingredients[0], name: "Water" },
            ],
          },
        }}
        failures={{}}
        options={[]}
        confirmedIngredients={[
          ...confirmedIngredients,
          {
            id: "detected-tomato",
            name: "Tomato",
            source: "detected",
            confidence: 0.9,
            confirmed: true,
          },
          {
            id: "user-basil",
            name: "Basil",
            source: "user_added",
            confidence: null,
            confirmed: true,
          },
        ]}
        activeRecipeId={recipe.optionId}
        completedSteps={{}}
        onSetActiveRecipe={vi.fn()}
        onToggleStep={vi.fn()}
        onRetryFailed={vi.fn()}
        onBack={vi.fn()}
        onReset={vi.fn()}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Ingredients (4)" }));
    for (const [name, provenance] of [
      ["Tomato", "detected"],
      ["Salt", "pantry"],
      ["Basil", "added by you"],
      ["Water", "available"],
    ]) {
      const row = screen.getByText(name).closest(".recipe-ingredient-row");
      expect(within(row as HTMLElement).getByText(provenance)).toBeVisible();
    }
  });

  it("lists failed dishes by name and offers retry only for retryable failures", async () => {
    const user = userEvent.setup();
    const onRetryFailed = vi.fn();
    render(
      <RecipesScreen
        recipes={{ [recipe.optionId]: recipe }}
        failures={{
          "option-2": {
            optionId: "option-2",
            code: "model_output_invalid",
            message: "  Invalid\nrecipe output.  ",
            retryable: true,
          },
        }}
        options={[
          {
            id: "option-2",
            name: "Tomato Rasam",
            summary: "",
            cuisine: "Indian",
            totalMinutes: 20,
            difficulty: "easy",
            usedIngredients: [],
            missingIngredients: [],
            optionalIngredients: [],
            nutrition: null,
            previewArtifactId: null,
            previewLabel: null,
            warnings: [],
            batchNumber: 1,
          },
        ]}
        confirmedIngredients={confirmedIngredients}
        activeRecipeId={recipe.optionId}
        completedSteps={{}}
        onSetActiveRecipe={vi.fn()}
        onToggleStep={vi.fn()}
        onRetryFailed={onRetryFailed}
        onBack={vi.fn()}
        onReset={vi.fn()}
      />,
    );

    expect(screen.getByText("Tomato Rasam:")).toBeVisible();
    expect(screen.getByText("Invalid recipe output.")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Retry failed recipes" }));
    expect(onRetryFailed).toHaveBeenCalledOnce();
  });

  it("confirms start over, defaults focus to Cancel, and restores trigger focus", async () => {
    const user = userEvent.setup();
    const onReset = vi.fn();
    render(
      <RecipesScreen
        recipes={{ [recipe.optionId]: recipe }}
        failures={{}}
        options={[]}
        confirmedIngredients={confirmedIngredients}
        activeRecipeId={recipe.optionId}
        completedSteps={{ [recipe.optionId]: [1] }}
        onSetActiveRecipe={vi.fn()}
        onToggleStep={vi.fn()}
        onRetryFailed={vi.fn()}
        onBack={vi.fn()}
        onReset={onReset}
      />,
    );
    const trigger = screen.getByRole("button", {
      name: "Start over with a new photo",
    });

    await user.click(trigger);
    const dialog = screen.getByRole("dialog", {
      name: "Start over with a new photo?",
    });
    expect(
      within(dialog).getByText(/clears all recipes, chosen ideas, and checked steps/),
    ).toBeVisible();
    expect(within(dialog).getByText(/pantry defaults are kept/)).toBeVisible();
    expect(within(dialog).getByRole("button", { name: "Cancel" })).toHaveFocus();

    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(trigger).toHaveFocus();
    expect(onReset).not.toHaveBeenCalled();

    await user.click(trigger);
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(trigger).toHaveFocus();

    await user.click(trigger);
    await user.click(screen.getByRole("button", { name: "Start over" }));
    expect(onReset).toHaveBeenCalledOnce();
  });
});
