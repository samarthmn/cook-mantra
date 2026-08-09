import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { CompleteRecipeView } from "../model/cook-session-state";
import {
  SAVED_RECIPES_STORAGE_KEY,
  type SavedRecipeEntry,
} from "../model/saved-recipes";
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
    {
      number: 2,
      instruction: "Serve while hot.",
      durationMinutes: null,
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
  previewArtifactId: null,
  previewLabel: null,
};

const secondRecipe: CompleteRecipeView = {
  ...recipe,
  optionId: "option-2",
  name: "Tomato Rasam",
  cuisine: "South Indian",
  totalMinutes: 25,
};

const thirdRecipe: CompleteRecipeView = {
  ...recipe,
  optionId: "option-3",
  name: "Lemon Rice",
  cuisine: "South Indian",
  totalMinutes: 30,
};

const photo = "data:image/jpeg;base64,c25hcHNob3Q=";

const savedAtFormatOptions: Intl.DateTimeFormatOptions = {
  day: "numeric",
  month: "short",
  year: "numeric",
  hour: "numeric",
  minute: "2-digit",
  second: "2-digit",
};

function formatSavedAt(savedAt: string) {
  return new Intl.DateTimeFormat(undefined, savedAtFormatOptions).format(
    new Date(savedAt),
  );
}

function snapshotLabel(entry: SavedRecipeEntry) {
  return `${entry.recipe.name}, saved ${formatSavedAt(entry.savedAt)}`;
}

const entries: SavedRecipeEntry[] = [
  {
    id: "saved-option-1",
    savedAt: "2026-01-02T12:00:00.000Z",
    recipe,
    photo,
    progress: {
      doneStepNumbers: [1],
      ingredientsExpanded: true,
    },
    ingredientSources: { salt: "user_added" },
  },
  {
    id: "saved-option-2",
    savedAt: "2026-01-03T12:00:00.000Z",
    recipe: secondRecipe,
    photo: null,
    progress: null,
    ingredientSources: null,
  },
];

const thirdEntry: SavedRecipeEntry = {
  id: "saved-option-3",
  savedAt: "2026-01-04T12:00:00.000Z",
  recipe: thirdRecipe,
  photo: null,
  progress: null,
  ingredientSources: null,
};

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
  initialEntries = entries,
}: {
  initialEntries?: SavedRecipeEntry[];
}) {
  const [savedEntries, setSavedEntries] = useState<SavedRecipeEntry[]>(initialEntries);

  return (
    <SavedRecipesScreen
      entries={savedEntries}
      onBack={vi.fn()}
      onEntriesChange={setSavedEntries}
    />
  );
}

function renderRemovalHarness(initialEntries = entries) {
  window.localStorage.setItem(
    SAVED_RECIPES_STORAGE_KEY,
    JSON.stringify({ version: 3, entries: initialEntries }),
  );
  return render(<RemovalHarness initialEntries={initialEntries} />);
}

describe("SavedRecipesScreen", () => {
  it("renders saved recipe summaries with time and servings but no nutrition", () => {
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
    expect(screen.getByText(formatSavedAt(entries[0].savedAt))).toBeVisible();
    expect(screen.getAllByText("2 servings")).toHaveLength(2);
    expect(screen.queryByText("420 kcal · 12g protein")).toBeNull();
  });

  it("renders entries newest-first without changing the input array", () => {
    const input = [...entries];

    render(
      <SavedRecipesScreen entries={input} onBack={vi.fn()} onEntriesChange={vi.fn()} />,
    );

    const displayedNames = screen
      .getAllByRole("article")
      .map((card) => within(card).getByRole("heading", { level: 2 }).textContent);
    expect(displayedNames).toEqual(["Tomato Rasam", "Test Curry"]);
    expect(input).toEqual(entries);
  });

  it("uses append order as the newest-first tie-breaker", () => {
    const sameMomentEntries = [
      entries[0],
      { ...entries[1], savedAt: entries[0].savedAt },
    ];

    render(
      <SavedRecipesScreen
        entries={sameMomentEntries}
        onBack={vi.fn()}
        onEntriesChange={vi.fn()}
      />,
    );

    const displayedNames = screen
      .getAllByRole("article")
      .map((card) => within(card).getByRole("heading", { level: 2 }).textContent);
    expect(displayedNames).toEqual(["Tomato Rasam", "Test Curry"]);
  });

  it("renders a saved thumbnail, its AI label, and captured progress", () => {
    render(
      <SavedRecipesScreen
        entries={[entries[0]]}
        onBack={vi.fn()}
        onEntriesChange={vi.fn()}
      />,
    );

    expect(
      screen.getByRole("img", { name: "Test Curry, AI-generated image" }),
    ).toHaveAttribute("src", photo);
    expect(screen.getByText("AI image")).toBeVisible();
    expect(screen.getByText("1 of 2 steps done")).toBeVisible();
    expect(document.querySelector('img[src*="/artifacts/"]')).toBeNull();
  });

  it("keeps separate snapshots of the same recipe distinguishable by time", () => {
    const firstSnapshot = entries[0];
    const secondSnapshot: SavedRecipeEntry = {
      ...firstSnapshot,
      id: "saved-option-1-later",
      savedAt: "2026-01-02T18:30:00.000Z",
      photo: null,
    };

    render(
      <SavedRecipesScreen
        entries={[firstSnapshot, secondSnapshot]}
        onBack={vi.fn()}
        onEntriesChange={vi.fn()}
      />,
    );

    expect(
      screen.getAllByRole("heading", { name: "Test Curry", level: 2 }),
    ).toHaveLength(2);
    expect(screen.getByText(formatSavedAt(secondSnapshot.savedAt))).toBeVisible();
    expect(screen.getByText(formatSavedAt(firstSnapshot.savedAt))).toBeVisible();
    expect(
      screen.getByRole("article", { name: snapshotLabel(firstSnapshot) }),
    ).toBeVisible();
    expect(
      screen.getByRole("article", { name: snapshotLabel(secondSnapshot) }),
    ).toBeVisible();
    expect(
      screen.getByRole("button", {
        name: `Open full recipe: ${snapshotLabel(firstSnapshot)}`,
      }),
    ).toBeVisible();
    expect(
      screen.getByRole("button", {
        name: `Download ${snapshotLabel(secondSnapshot)}`,
      }),
    ).toBeVisible();
    expect(
      screen.getByRole("button", {
        name: `Remove ${snapshotLabel(firstSnapshot)}`,
      }),
    ).toBeVisible();
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

  it("restores captured ingredient and read-only step state in full detail", async () => {
    const user = userEvent.setup();
    render(
      <SavedRecipesScreen
        entries={[entries[0]]}
        onBack={vi.fn()}
        onEntriesChange={vi.fn()}
      />,
    );

    await user.click(
      screen.getByRole("button", {
        name: `Open full recipe: ${snapshotLabel(entries[0])}`,
      }),
    );

    const detail = screen.getByRole("region", { name: "Full recipe: Test Curry" });
    expect(
      within(detail).getByRole("button", { name: "Ingredients (1)" }),
    ).toHaveAttribute("aria-expanded", "true");
    expect(within(detail).getByText("salt")).toBeVisible();
    expect(within(detail).getByText("added by you")).toBeVisible();
    const completedStep = within(detail)
      .getByText("Season the curry.")
      .closest(".method-step");
    const remainingStep = within(detail)
      .getByText("Serve while hot.")
      .closest(".method-step");
    expect(completedStep).toHaveClass("method-step-readonly", "is-done");
    expect(completedStep?.querySelector(".method-step-number svg")).not.toBeNull();
    expect(
      within(completedStep as HTMLElement).getByText("Completed step."),
    ).toHaveClass("visually-hidden");
    expect(remainingStep).toHaveClass("method-step-readonly");
    expect(remainingStep).not.toHaveClass("is-done");
    expect(
      within(detail).queryByRole("button", { name: /Season the curry/ }),
    ).toBeNull();
    expect(
      screen.getByRole("button", {
        name: `Close full recipe: ${snapshotLabel(entries[0])}`,
      }),
    ).toHaveAttribute("aria-expanded", "true");
  });

  it("removes an entry only after inline confirmation", async () => {
    const user = userEvent.setup();
    renderRemovalHarness();

    await user.click(
      screen.getByRole("button", {
        name: `Remove ${snapshotLabel(entries[0])}`,
      }),
    );
    const confirmation = screen.getByRole("group", {
      name: `Confirm removal of ${snapshotLabel(entries[0])}`,
    });
    expect(
      within(confirmation).getByText(/Remove Test Curry from your saved recipes/),
    ).toBeVisible();
    expect(
      within(confirmation).getByRole("button", {
        name: `Confirm remove ${snapshotLabel(entries[0])}`,
      }),
    ).toHaveFocus();
    expect(screen.getByRole("heading", { name: "Test Curry", level: 2 })).toBeVisible();

    await user.click(
      within(confirmation).getByRole("button", {
        name: `Confirm remove ${snapshotLabel(entries[0])}`,
      }),
    );

    expect(screen.queryByRole("heading", { name: "Test Curry", level: 2 })).toBeNull();
    expect(
      screen.getByRole("heading", { name: "Tomato Rasam", level: 2 }),
    ).toBeVisible();
    expect(
      screen.getByRole("button", {
        name: `Remove ${snapshotLabel(entries[1])}`,
      }),
    ).toHaveFocus();
  });

  it("returns focus to the remove button after cancellation", async () => {
    const user = userEvent.setup();
    renderRemovalHarness();

    await user.click(
      screen.getByRole("button", {
        name: `Remove ${snapshotLabel(entries[0])}`,
      }),
    );
    const confirmation = screen.getByRole("group", {
      name: `Confirm removal of ${snapshotLabel(entries[0])}`,
    });

    await user.click(within(confirmation).getByRole("button", { name: "Keep it" }));

    expect(
      screen.getByRole("button", {
        name: `Remove ${snapshotLabel(entries[0])}`,
      }),
    ).toHaveFocus();
  });

  it("moves focus to the saved recipes heading after removing the final entry", async () => {
    const user = userEvent.setup();
    renderRemovalHarness([entries[0]]);

    await user.click(
      screen.getByRole("button", {
        name: `Remove ${snapshotLabel(entries[0])}`,
      }),
    );
    await user.click(
      screen.getByRole("button", {
        name: `Confirm remove ${snapshotLabel(entries[0])}`,
      }),
    );

    expect(screen.getByText("No recipes saved yet.")).toBeVisible();
    expect(
      screen.getByRole("heading", { name: "Saved recipes", level: 1 }),
    ).toHaveFocus();
  });

  it("moves focus to the next card in newest-first visual order", async () => {
    const user = userEvent.setup();
    renderRemovalHarness([...entries, thirdEntry]);

    await user.click(
      screen.getByRole("button", {
        name: `Remove ${snapshotLabel(entries[1])}`,
      }),
    );
    await user.click(
      screen.getByRole("button", {
        name: `Confirm remove ${snapshotLabel(entries[1])}`,
      }),
    );

    expect(
      screen.getByRole("button", {
        name: `Remove ${snapshotLabel(entries[0])}`,
      }),
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

    await user.click(
      screen.getByRole("button", {
        name: `Download ${snapshotLabel(entries[0])}`,
      }),
    );

    expect(downloadCapture.markdown).toContain("# Test Curry");
    expect(downloadCapture.markdown).toContain("## Ingredients");
    expect(downloadCapture.markdown).toContain("salt");
  });
});
