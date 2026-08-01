import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { IngredientView, PreferenceView } from "../model/cook-session-state";
import { ConfirmScreen } from "./ConfirmScreen";

const preferences: PreferenceView = {
  diet: "vegetarian",
  dietStyle: null,
  dietAddOns: [],
  servings: 2,
  optionCount: 4,
  allergens: [],
  spiceLevel: "medium",
  specialInstructions: "",
};

const ingredients: IngredientView[] = [
  {
    id: "high",
    name: "Tomato",
    source: "detected",
    confidence: 0.8,
    confirmed: true,
  },
  {
    id: "medium",
    name: "Spinach",
    source: "detected",
    confidence: 0.6,
    confirmed: true,
  },
  {
    id: "low",
    name: "Coriander",
    source: "detected",
    confidence: 0.59,
    confirmed: true,
  },
];

function renderConfirmScreen({
  ingredientValues = ingredients,
  preferenceValues = preferences,
  pantryStaples = ["Salt"],
  generatedContentExists = false,
  onPantryStaplesChange = vi.fn(() => true),
}: {
  ingredientValues?: IngredientView[];
  preferenceValues?: PreferenceView;
  pantryStaples?: string[];
  generatedContentExists?: boolean;
  onPantryStaplesChange?: (names: string[]) => boolean;
} = {}) {
  render(
    <ConfirmScreen
      ingredients={ingredientValues}
      ingredientNameErrors={{}}
      preferences={preferenceValues}
      weakDetection={false}
      pantryStaples={pantryStaples}
      generatedContentExists={generatedContentExists}
      onAddIngredient={vi.fn()}
      onRenameIngredient={vi.fn()}
      onRemoveIngredient={vi.fn()}
      onToggleIngredient={vi.fn()}
      onToggleAllPantry={vi.fn()}
      onPantryStaplesChange={onPantryStaplesChange}
      onPreferenceChange={vi.fn()}
      onRetake={vi.fn()}
      onGenerate={vi.fn()}
    />,
  );
}

function PreferenceHarness() {
  const [preferenceValues, setPreferenceValues] = useState(preferences);

  return (
    <ConfirmScreen
      ingredients={ingredients}
      ingredientNameErrors={{}}
      preferences={preferenceValues}
      weakDetection={false}
      pantryStaples={["Salt"]}
      onAddIngredient={vi.fn()}
      onRenameIngredient={vi.fn()}
      onRemoveIngredient={vi.fn()}
      onToggleIngredient={vi.fn()}
      onToggleAllPantry={vi.fn()}
      onPantryStaplesChange={() => true}
      onPreferenceChange={(key, value) =>
        setPreferenceValues((current) => ({
          ...current,
          [key]: value,
          ...(key === "diet" ? { dietStyle: null } : {}),
        }))
      }
      onRetake={vi.fn()}
      onGenerate={vi.fn()}
    />
  );
}

describe("ConfirmScreen confidence indicators", () => {
  afterEach(() => cleanup());

  it("uses the exact high, medium, and low confidence thresholds", () => {
    renderConfirmScreen();

    expect(
      screen.getByRole("button", { name: "Confidence: high, 80 percent" }),
    ).toHaveClass("confidence-high");
    expect(
      screen.getByRole("button", { name: "Confidence: medium, 60 percent" }),
    ).toHaveClass("confidence-medium");
    expect(
      screen.getByRole("button", { name: "Confidence: low, 59 percent" }),
    ).toHaveClass("confidence-low");
    expect(document.querySelector('[data-signal="filled"]')).toBeInTheDocument();
    expect(document.querySelector('[data-signal="half"]')).toBeInTheDocument();
    expect(document.querySelector('[data-signal="hollow"]')).toBeInTheDocument();
  });

  it("toggles the confidence details and dismisses them with Escape", async () => {
    const user = userEvent.setup();
    renderConfirmScreen();
    const high = screen.getByRole("button", {
      name: "Confidence: high, 80 percent",
    });

    await user.click(high);
    expect(high).toHaveAttribute("aria-expanded", "true");
    const tooltip = screen.getByRole("tooltip");
    expect(tooltip).toHaveTextContent(
      "Identified with high confidence (80%). Very likely correct.",
    );
    expect(high).toHaveAttribute("aria-describedby", tooltip.id);
    expect(high).toHaveAttribute("aria-controls", tooltip.id);

    await user.click(high);
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();

    await user.click(high);
    await user.keyboard("{Escape}");
    expect(high).toHaveAttribute("aria-expanded", "false");
    expect(high).not.toHaveAttribute("aria-describedby");
    expect(high).not.toHaveAttribute("aria-controls");
    expect(high).toHaveFocus();
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
  });

  it("dismisses confidence details after an outside press", async () => {
    const user = userEvent.setup();
    renderConfirmScreen();
    const low = screen.getByRole("button", {
      name: "Confidence: low, 59 percent",
    });

    await user.click(low);
    expect(screen.getByRole("tooltip")).toHaveTextContent(
      "Low confidence (59%). Verify or remove this item.",
    );

    await user.click(screen.getByRole("heading", { name: "Check what we found" }));
    expect(low).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
  });

  it("flips the popover above when the sticky action bar limits space below", async () => {
    const rect = (top: number, bottom: number): DOMRect =>
      ({
        x: 0,
        y: top,
        top,
        right: 300,
        bottom,
        left: 250,
        width: 50,
        height: bottom - top,
        toJSON: () => ({}),
      }) as DOMRect;
    const rectSpy = vi
      .spyOn(HTMLElement.prototype, "getBoundingClientRect")
      .mockImplementation(function (this: HTMLElement) {
        if (this.classList.contains("confidence-button")) return rect(500, 544);
        if (this.classList.contains("confidence-popover")) return rect(540, 620);
        if (this.classList.contains("sticky-action-bar")) return rect(560, 680);
        return rect(0, 0);
      });
    const user = userEvent.setup();

    try {
      renderConfirmScreen();
      await user.click(
        screen.getByRole("button", { name: "Confidence: low, 59 percent" }),
      );
      expect(screen.getByRole("tooltip")).toHaveAttribute("data-placement", "above");
    } finally {
      rectSpy.mockRestore();
    }
  });
});

describe("ConfirmScreen layout and preferences", () => {
  afterEach(() => cleanup());

  it("keeps only ingredients and pantry in the two-column grid", () => {
    renderConfirmScreen();
    const grid = document.querySelector<HTMLElement>(".confirm-grid");
    const preferencesHeading = screen.getByRole("heading", { name: "Preferences" });

    expect(grid).not.toBeNull();
    expect(
      within(grid!).getByRole("heading", { name: "Detected in your photo" }),
    ).toBeVisible();
    expect(
      within(grid!).getByRole("heading", { name: "Pantry staples" }),
    ).toBeVisible();
    expect(grid).not.toContainElement(preferencesHeading);
    expect(grid!.nextElementSibling).toContainElement(preferencesHeading);
  });

  it("differentiates removable custom allergens from toggle chips", () => {
    renderConfirmScreen({
      preferenceValues: { ...preferences, allergens: ["Mustard"] },
    });

    expect(screen.getByRole("button", { name: "Dairy" })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
    const custom = screen.getByRole("button", {
      name: "Remove Mustard allergen",
    });
    expect(custom).toHaveClass("preference-chip-custom");
    expect(custom).not.toHaveAttribute("aria-pressed");
  });

  it("keeps diet style single-select and add-ons selected across a diet switch", async () => {
    const user = userEvent.setup();
    render(<PreferenceHarness />);

    expect(screen.getByRole("radio", { name: "Vegetarian" })).toBeChecked();
    expect(screen.getByRole("button", { name: "Keto" })).toHaveAttribute(
      "aria-pressed",
      "false",
    );

    await user.click(screen.getByRole("radio", { name: "Vegan" }));
    await user.click(screen.getByRole("radio", { name: "Eggetarian" }));
    expect(screen.getByRole("radio", { name: "Vegan" })).not.toBeChecked();
    expect(screen.getByRole("radio", { name: "Eggetarian" })).toBeChecked();

    await user.click(screen.getByRole("button", { name: "Keto" }));
    await user.click(screen.getByRole("radio", { name: "Non-Veg" }));
    expect(screen.getByRole("radio", { name: "Any" })).toBeChecked();
    expect(screen.getByRole("button", { name: "Keto" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });
});

describe("ConfirmScreen pantry editor focus", () => {
  afterEach(() => cleanup());

  it("moves focus into the editor, follows removals, and returns it on cancel", async () => {
    const user = userEvent.setup();
    renderConfirmScreen({ pantryStaples: ["Salt", "Pepper powder"] });
    const editButton = screen.getByRole("button", { name: "Edit defaults" });

    expect(editButton).not.toHaveAttribute("aria-controls");
    await user.click(editButton);
    expect(screen.getByLabelText("Pantry staple 1")).toHaveFocus();
    expect(editButton).toHaveAttribute("aria-controls", "pantry-defaults-editor");

    await user.click(screen.getByRole("button", { name: "Remove Pepper powder" }));
    expect(screen.getByLabelText("Pantry staple 1")).toHaveFocus();

    await user.click(screen.getByRole("button", { name: "Remove Salt" }));
    expect(screen.getByLabelText("Add a pantry staple")).toHaveFocus();
    expect(document.body).not.toHaveFocus();

    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(editButton).toHaveFocus();
    expect(editButton).not.toHaveAttribute("aria-controls");
  });

  it("returns focus to Edit defaults after saving", async () => {
    const user = userEvent.setup();
    const onPantryStaplesChange = vi.fn(() => true);
    renderConfirmScreen({ onPantryStaplesChange });
    const editButton = screen.getByRole("button", { name: "Edit defaults" });

    await user.click(editButton);
    await user.click(screen.getByRole("button", { name: "Save defaults" }));

    expect(onPantryStaplesChange).toHaveBeenCalledWith(["Salt"]);
    expect(editButton).toHaveFocus();
  });

  it("warns that saving current defaults clears generated content", async () => {
    const user = userEvent.setup();
    renderConfirmScreen({ generatedContentExists: true });

    await user.click(screen.getByRole("button", { name: "Edit defaults" }));

    expect(
      screen.getByText(/Saving replaces the current pantry list immediately/),
    ).toBeVisible();
    expect(
      screen.getByText(
        /Saving now clears your generated ideas, recipes, and checked steps/,
      ),
    ).toBeVisible();
  });

  it("shows a visit-only note when pantry defaults cannot be persisted", async () => {
    const user = userEvent.setup();
    renderConfirmScreen({ onPantryStaplesChange: vi.fn(() => false) });

    await user.click(screen.getByRole("button", { name: "Edit defaults" }));
    await user.click(screen.getByRole("button", { name: "Save defaults" }));

    expect(
      screen.getByText(
        "Pantry defaults could not be saved on this device. They apply to this visit only.",
      ),
    ).toHaveAttribute("role", "status");
  });
});
