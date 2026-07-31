import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { RecipeOptionView } from "../model/cook-session-state";
import { OptionsScreen } from "./OptionsScreen";

const option: RecipeOptionView = {
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
  previewArtifactId: null,
  previewLabel: null,
  warnings: ["The model could not verify the exact lentil variety."],
  batchNumber: 1,
};

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
        previewUrl={(artifactId) => artifactId}
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
        previewUrl={(artifactId) => artifactId}
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
        previewUrl={(artifactId) => artifactId}
        onToggleOption={vi.fn()}
        onMoreIdeas={vi.fn()}
        onEditIngredients={vi.fn()}
        onCreateRecipes={vi.fn()}
      />,
    );

    expect(document.querySelector(".option-card-summary")).toBeNull();
    expect(screen.getByRole("button", { name: /Seasonal Dal/ })).toBeVisible();
  });

  it("keeps backend warnings visible on the relevant option", () => {
    render(
      <OptionsScreen
        options={[option]}
        selectedOptionIds={[]}
        ingredientCount={1}
        ideasExhausted={false}
        previewUrl={(artifactId) => artifactId}
        onToggleOption={vi.fn()}
        onMoreIdeas={vi.fn()}
        onEditIngredients={vi.fn()}
        onCreateRecipes={vi.fn()}
      />,
    );

    expect(
      screen.getByText("The model could not verify the exact lentil variety."),
    ).toBeVisible();
  });

  it("does not label the placeholder as an AI illustration when no preview exists", () => {
    render(
      <OptionsScreen
        options={[option]}
        selectedOptionIds={[]}
        ingredientCount={1}
        ideasExhausted={false}
        previewUrl={(artifactId) => artifactId}
        onToggleOption={vi.fn()}
        onMoreIdeas={vi.fn()}
        onEditIngredients={vi.fn()}
        onCreateRecipes={vi.fn()}
      />,
    );

    expect(screen.queryByText("AI illustration")).toBeNull();
  });

  it("labels a real generated preview as an AI illustration", () => {
    render(
      <OptionsScreen
        options={[{ ...option, previewArtifactId: "artifact-1" }]}
        selectedOptionIds={[]}
        ingredientCount={1}
        ideasExhausted={false}
        previewUrl={(artifactId) => `/artifacts/${artifactId}`}
        onToggleOption={vi.fn()}
        onMoreIdeas={vi.fn()}
        onEditIngredients={vi.fn()}
        onCreateRecipes={vi.fn()}
      />,
    );

    expect(screen.getByText("AI illustration")).toBeVisible();
  });

  it("replaces a failed generated preview and removes its illustration label", () => {
    render(
      <OptionsScreen
        options={[{ ...option, previewArtifactId: "artifact-1" }]}
        selectedOptionIds={[]}
        ingredientCount={1}
        ideasExhausted={false}
        previewUrl={(artifactId) => `/artifacts/${artifactId}`}
        onToggleOption={vi.fn()}
        onMoreIdeas={vi.fn()}
        onEditIngredients={vi.fn()}
        onCreateRecipes={vi.fn()}
      />,
    );

    fireEvent.error(
      screen.getByRole("img", {
        name: "Seasonal Dal, AI-generated illustration",
      }),
    );

    expect(
      screen.queryByRole("img", {
        name: "Seasonal Dal, AI-generated illustration",
      }),
    ).toBeNull();
    expect(screen.queryByText("AI illustration")).toBeNull();
  });

  it("disables more ideas with an explanation when the API stage cannot accept it", () => {
    render(
      <OptionsScreen
        options={[option]}
        selectedOptionIds={[]}
        ingredientCount={1}
        ideasExhausted={false}
        moreIdeasUnavailableReason="Edit your ingredients to start a fresh photo-backed idea session."
        previewUrl={(artifactId) => artifactId}
        onToggleOption={vi.fn()}
        onMoreIdeas={vi.fn()}
        onEditIngredients={vi.fn()}
        onCreateRecipes={vi.fn()}
      />,
    );

    expect(screen.getByRole("button", { name: "Start fresh for more" })).toBeDisabled();
    expect(
      screen.getByText(
        "Edit your ingredients to start a fresh photo-backed idea session.",
      ),
    ).toBeVisible();
  });
});
