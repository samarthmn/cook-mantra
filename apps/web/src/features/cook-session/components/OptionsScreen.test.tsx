import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { RecipeOptionView } from "../model/cook-session-state";
import { OptionsScreen } from "./OptionsScreen";

const option = {
  id: "seasonal-dal",
  name: "Seasonal Dal",
  summary: "A simple lentil dish.",
  cuisine: "Indian",
  totalMinutes: 30,
  difficulty: "easy",
  usedIngredients: ["Lentils"],
  missingIngredients: [],
  optionalIngredients: [],
  nutrition: null,
  // Deliberately retained as stale runtime data until the production mapper is
  // changed. The screen must ignore fields from the retired option-preview shape.
  warnings: [],
  batchNumber: 1,
} as unknown as RecipeOptionView;

describe("OptionsScreen", () => {
  afterEach(() => cleanup());

  it("asks for more ingredients when a short list produced no ideas", () => {
    const onEditIngredients = vi.fn();
    render(
      <OptionsScreen
        options={[]}
        selectedOptionIds={[]}
        ingredientCount={2}
        ideasExhausted={false}
        onToggleOption={vi.fn()}
        onMoreIdeas={vi.fn()}
        onEditIngredients={onEditIngredients}
        onCreateRecipes={vi.fn()}
      />,
    );

    expect(
      screen.getByRole("heading", { name: "No ideas from your 2 ingredients" }),
    ).toBeVisible();
    expect(screen.getByText(/Two ingredients is a thin pantry/)).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Add more ingredients" }));
    expect(onEditIngredients).toHaveBeenCalledTimes(1);
  });

  it("points at the preferences when a full list produced no ideas", () => {
    render(
      <OptionsScreen
        options={[]}
        selectedOptionIds={[]}
        ingredientCount={11}
        ideasExhausted={false}
        onToggleOption={vi.fn()}
        onMoreIdeas={vi.fn()}
        onEditIngredients={vi.fn()}
        onCreateRecipes={vi.fn()}
      />,
    );

    expect(screen.getByText(/relax a preference/)).toBeVisible();
    expect(screen.getByRole("button", { name: /Try again/ })).toBeEnabled();
  });

  it("omits the summary line when the agent returned no summary", () => {
    render(
      <OptionsScreen
        options={[{ ...option, summary: "" }]}
        selectedOptionIds={[]}
        ingredientCount={1}
        ideasExhausted={false}
        onToggleOption={vi.fn()}
        onMoreIdeas={vi.fn()}
        onEditIngredients={vi.fn()}
        onCreateRecipes={vi.fn()}
      />,
    );

    expect(document.querySelector(".option-card-summary")).toBeNull();
    expect(screen.getByRole("button", { name: /Seasonal Dal/ })).toBeVisible();
  });

  it("does not render deprecated nutrition-derived allergen warnings", () => {
    render(
      <OptionsScreen
        options={[
          {
            ...option,
            nutrition: {
              caloriesKcal: 300,
              proteinG: 12,
              carbohydratesG: 40,
              fatG: 10,
              dietTags: [],
              allergenWarnings: ["dairy", "soy"],
              disclaimer: "Estimated values; not medical advice.",
            },
          },
        ]}
        selectedOptionIds={[]}
        ingredientCount={1}
        ideasExhausted={false}
        onToggleOption={vi.fn()}
        onMoreIdeas={vi.fn()}
        onEditIngredients={vi.fn()}
        onCreateRecipes={vi.fn()}
      />,
    );

    expect(screen.queryByText("May contain dairy and soy")).toBeNull();
    expect(screen.queryByText(/Contains dairy/)).toBeNull();
  });

  it("explains that previews belong to completed recipes", () => {
    render(
      <OptionsScreen
        options={[option]}
        selectedOptionIds={[]}
        ingredientCount={1}
        ideasExhausted={false}
        onToggleOption={vi.fn()}
        onMoreIdeas={vi.fn()}
        onEditIngredients={vi.fn()}
        onCreateRecipes={vi.fn()}
      />,
    );

    expect(
      screen.getByText(
        "Pick one or more. After a recipe is written, Cook Mantra may create an AI image of the finished dish.",
      ),
    ).toBeVisible();
    expect(screen.queryByText("AI image")).toBeNull();
  });

  it("ignores stale option previews and image-only warnings", () => {
    const staleOption = {
      ...option,
      previewArtifactId: "process-private-preview",
      previewLabel: "AI-generated image",
      warnings: ["Dish preview unavailable."],
    } as RecipeOptionView;
    const staleCompatibilityProps = {
      previewUrl: (artifactId: string) => `/artifacts/${artifactId}`,
    };

    render(
      <OptionsScreen
        {...staleCompatibilityProps}
        options={[staleOption]}
        selectedOptionIds={[]}
        ingredientCount={1}
        ideasExhausted={false}
        onToggleOption={vi.fn()}
        onMoreIdeas={vi.fn()}
        onEditIngredients={vi.fn()}
        onCreateRecipes={vi.fn()}
      />,
    );

    expect(screen.queryByRole("img")).toBeNull();
    expect(screen.queryByText("AI image")).toBeNull();
    expect(screen.queryByText("Dish preview unavailable.")).toBeNull();
    expect(document.querySelector(".option-card-media")).toBeNull();
  });

  it("renders an indexed text-first folio without an image surface", () => {
    render(
      <OptionsScreen
        options={[option]}
        selectedOptionIds={[]}
        ingredientCount={1}
        ideasExhausted={false}
        onToggleOption={vi.fn()}
        onMoreIdeas={vi.fn()}
        onEditIngredients={vi.fn()}
        onCreateRecipes={vi.fn()}
      />,
    );

    const index = document.querySelector(".option-card-index");
    expect(index).toHaveTextContent("01");
    expect(index).toHaveTextContent("batch 01");
    expect(screen.queryByRole("img")).toBeNull();
    expect(screen.queryByText("AI image")).toBeNull();
  });

  it("does not depend on the Next.js image optimizer", () => {
    const source = readFileSync(
      resolve(process.cwd(), "src/features/cook-session/components/OptionsScreen.tsx"),
      "utf8",
    );

    expect(source).not.toContain("next/image");
  });

  it("disables more ideas with an explanation when the API stage cannot accept it", () => {
    render(
      <OptionsScreen
        options={[option]}
        selectedOptionIds={[]}
        ingredientCount={1}
        ideasExhausted={false}
        moreIdeasUnavailableReason="Edit your ingredients to start a fresh photo-backed idea session."
        onToggleOption={vi.fn()}
        onMoreIdeas={vi.fn()}
        onEditIngredients={vi.fn()}
        onCreateRecipes={vi.fn()}
      />,
    );

    expect(
      screen.getByRole("button", { name: "More ideas unavailable" }),
    ).toBeDisabled();
    expect(
      screen.getByText(
        "Edit your ingredients to start a fresh photo-backed idea session.",
      ),
    ).toBeVisible();
  });
});
