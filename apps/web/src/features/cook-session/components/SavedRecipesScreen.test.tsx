import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { CompleteRecipeView } from "../model/cook-session-state";
import { saveRecipe, type SavedRecipeEntry } from "../model/saved-recipes";
import { SavedRecipesScreen } from "./SavedRecipesScreen";

const downloadCapture = vi.hoisted(() => ({ markdown: "" }));

vi.mock("../model/recipe-markdown", async () => {
  const actual = await vi.importActual<typeof import("../model/recipe-markdown")>(
    "../model/recipe-markdown",
  );

  return {
    ...actual,
    downloadRecipeMarkdown: vi.fn((recipeView: CompleteRecipeView) => {
      downloadCapture.markdown = actual.recipeToMarkdown(recipeView);
      return true;
    }),
  };
});

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
  steps: [
    {
      number: 1,
      instruction: "Season the curry.",
      durationMinutes: 2,
      doneWhen: "the seasoning tastes balanced",
      heatLevel: "low",
    },
  ],
  tips: ["Taste before serving."],
  substitutions: [],
  nutrition: {
    caloriesKcal: 420,
    proteinG: 12,
    carbohydratesG: 54,
    fatG: 16,
    dietTags: ["Vegetarian"],
    allergenWarnings: [],
    disclaimer: "Estimated values; not medical advice.",
  },
  nutritionNotice: "Estimated values; not medical advice.",
  allergenNotice: "Check ingredient labels for allergens.",
  assumptions: [],
  warnings: [],
};

const secondRecipe: CompleteRecipeView = {
  ...recipe,
  optionId: "option-2",
  name: "Tomato Rasam",
  cuisine: "South Indian",
  totalMinutes: 25,
};

const entries: SavedRecipeEntry[] = [
  {
    id: "saved-option-1",
    savedAt: "2026-01-02T12:00:00.000Z",
    recipe,
  },
  {
    id: "saved-option-2",
    savedAt: "2026-01-03T12:00:00.000Z",
    recipe: secondRecipe,
  },
];

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  cleanup();
  downloadCapture.markdown = "";
  window.localStorage.clear();
  vi.restoreAllMocks();
});

function RemovalHarness({
  initialRecipes = [recipe, secondRecipe],
}: {
  initialRecipes?: CompleteRecipeView[];
}) {
  const [savedEntries, setSavedEntries] = useState<SavedRecipeEntry[]>(() => {
    let nextEntries: SavedRecipeEntry[] = [];

    for (const recipeView of initialRecipes) {
      const result = saveRecipe(recipeView);
      if (!result.ok) return [];
      nextEntries = result.entries;
    }

    return nextEntries;
  });

  return (
    <SavedRecipesScreen
      entries={savedEntries}
      onBack={vi.fn()}
      onEntriesChange={setSavedEntries}
    />
  );
}

describe("SavedRecipesScreen", () => {
  it("renders saved recipe summaries with date and nutrition", () => {
    render(
      <SavedRecipesScreen
        entries={entries}
        onBack={vi.fn()}
        onEntriesChange={vi.fn()}
      />,
    );

    expect(
      screen.getByRole("heading", { name: "Saved recipes", level: 1 }),
    ).toBeVisible();
    expect(screen.getByRole("heading", { name: "Test Curry", level: 2 })).toBeVisible();
    expect(
      screen.getByRole("heading", { name: "Tomato Rasam", level: 2 }),
    ).toBeVisible();
    expect(screen.getByText("Jan 2, 2026")).toBeVisible();
    expect(screen.getAllByText("2 servings")).toHaveLength(2);
    expect(screen.getAllByText("420 kcal · 12g protein")).toHaveLength(2);
  });

  it("renders a friendly empty state", () => {
    render(
      <SavedRecipesScreen entries={[]} onBack={vi.fn()} onEntriesChange={vi.fn()} />,
    );

    expect(screen.getByText("No recipes saved yet.")).toBeVisible();
    expect(
      screen.getByText(/Save one when something looks worth making again/),
    ).toBeVisible();
  });

  it("expands a saved recipe into the shared full detail", async () => {
    const user = userEvent.setup();
    render(
      <SavedRecipesScreen
        entries={[entries[0]]}
        onBack={vi.fn()}
        onEntriesChange={vi.fn()}
      />,
    );

    await user.click(
      screen.getByRole("button", { name: "Open full recipe: Test Curry" }),
    );

    const detail = screen.getByRole("region", { name: "Full recipe: Test Curry" });
    await user.click(within(detail).getByRole("button", { name: "Ingredients (1)" }));
    expect(within(detail).getByText("salt")).toBeVisible();
    expect(within(detail).getByText("Season the curry.")).toBeVisible();
    expect(
      screen.getByRole("button", { name: "Close full recipe: Test Curry" }),
    ).toHaveAttribute("aria-expanded", "true");
  });

  it("removes an entry only after inline confirmation", async () => {
    const user = userEvent.setup();
    render(<RemovalHarness />);

    await user.click(screen.getByRole("button", { name: "Remove Test Curry" }));
    const confirmation = screen.getByRole("group", {
      name: "Confirm removal of Test Curry",
    });
    expect(
      within(confirmation).getByText(/Remove Test Curry from your saved recipes/),
    ).toBeVisible();
    expect(
      within(confirmation).getByRole("button", {
        name: "Confirm remove Test Curry",
      }),
    ).toHaveFocus();
    expect(screen.getByRole("heading", { name: "Test Curry", level: 2 })).toBeVisible();

    await user.click(
      within(confirmation).getByRole("button", {
        name: "Confirm remove Test Curry",
      }),
    );

    expect(screen.queryByRole("heading", { name: "Test Curry", level: 2 })).toBeNull();
    expect(
      screen.getByRole("heading", { name: "Tomato Rasam", level: 2 }),
    ).toBeVisible();
    expect(screen.getByRole("button", { name: "Remove Tomato Rasam" })).toHaveFocus();
  });

  it("returns focus to the remove button after cancellation", async () => {
    const user = userEvent.setup();
    render(<RemovalHarness />);

    await user.click(screen.getByRole("button", { name: "Remove Test Curry" }));
    const confirmation = screen.getByRole("group", {
      name: "Confirm removal of Test Curry",
    });

    await user.click(within(confirmation).getByRole("button", { name: "Keep it" }));

    expect(screen.getByRole("button", { name: "Remove Test Curry" })).toHaveFocus();
  });

  it("moves focus to the saved recipes heading after removing the final entry", async () => {
    const user = userEvent.setup();
    render(<RemovalHarness initialRecipes={[recipe]} />);

    await user.click(screen.getByRole("button", { name: "Remove Test Curry" }));
    await user.click(screen.getByRole("button", { name: "Confirm remove Test Curry" }));

    expect(screen.getByText("No recipes saved yet.")).toBeVisible();
    expect(
      screen.getByRole("heading", { name: "Saved recipes", level: 1 }),
    ).toHaveFocus();
  });

  it("downloads the selected entry with its Markdown content", async () => {
    const user = userEvent.setup();
    render(
      <SavedRecipesScreen
        entries={[entries[0]]}
        onBack={vi.fn()}
        onEntriesChange={vi.fn()}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Download Test Curry" }));

    expect(downloadCapture.markdown).toContain("# Test Curry");
    expect(downloadCapture.markdown).toContain("## Ingredients");
    expect(downloadCapture.markdown).toContain("salt");
  });
});
