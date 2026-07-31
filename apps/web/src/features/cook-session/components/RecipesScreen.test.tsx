import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { CompleteRecipeView, IngredientView } from "../model/cook-session-state";
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

afterEach(cleanup);

function renderRecipe(recipeView: CompleteRecipeView) {
  return render(
    <RecipesScreen
      recipes={{ [recipeView.optionId]: recipeView }}
      failures={{}}
      options={[]}
      confirmedIngredients={confirmedIngredients}
      activeRecipeId={recipeView.optionId}
      completedSteps={{}}
      onSetActiveRecipe={vi.fn()}
      onToggleStep={vi.fn()}
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

  it("shows ingredient-level substitution guidance", () => {
    renderRecipe({
      ...recipe,
      ingredients: [
        {
          ...recipe.ingredients[0],
          substitution: "lime juice and a pinch of salt",
        },
      ],
    });

    expect(
      screen.getByText("Substitute: lime juice and a pinch of salt"),
    ).toBeVisible();
  });

  it("shows the duration supplied for a method step", () => {
    renderRecipe({
      ...recipe,
      steps: [{ ...recipe.steps[0], durationMinutes: 3 }],
    });

    const methodStep = screen.getByRole("button", { name: /Season the curry/ });
    expect(within(methodStep).getByText("3 min")).toBeVisible();
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
});
