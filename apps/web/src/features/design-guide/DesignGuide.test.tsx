import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { DesignGuide } from "./DesignGuide";

const globalStyles = readFileSync(
  resolve(process.cwd(), "src/app/globals.css"),
  "utf8",
);

function declarationsFor(selector: string): string {
  const escapedSelector = selector.replaceAll(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const match = globalStyles.match(new RegExp(`${escapedSelector}\\s*\\{([^}]*)\\}`));
  expect(match, `Missing CSS rule for ${selector}`).not.toBeNull();
  return match?.[1] ?? "";
}

describe("DesignGuide", () => {
  afterEach(() => cleanup());

  it("renders the complete visual reference for the shared guide route", () => {
    render(<DesignGuide />);

    expect(
      screen.getByRole("heading", {
        level: 1,
        name: "Honest, structural, flush left.",
      }),
    ).toBeInTheDocument();
    const typography = screen.getByRole("region", { name: "Typography" });
    expect(within(typography).getByText("Screen title")).toBeInTheDocument();
    expect(within(typography).getByText("Recipe name")).toBeInTheDocument();

    const color = screen.getByRole("region", { name: "Color, light and dark" });
    expect(
      within(color).getByRole("heading", { name: "Light theme" }),
    ).toBeInTheDocument();
    expect(
      within(color).getByRole("heading", { name: "Dark theme" }),
    ).toBeInTheDocument();
    expect(within(color).getAllByText("Ground")).toHaveLength(2);

    const icons = screen.getByRole("region", { name: "Icons" });
    expect(within(icons).getByText("camera")).toBeInTheDocument();
    expect(within(icons).getByText("warning")).toBeInTheDocument();
    expect(within(icons).getByText("theme")).toBeInTheDocument();

    const patterns = screen.getByRole("region", {
      name: "Reusable product patterns",
    });
    expect(
      within(patterns).getByRole("button", { name: "Generate recipe ideas" }),
    ).toBeInTheDocument();
    expect(
      within(patterns).getByRole("textbox", { name: "Allergens to avoid" }),
    ).toBeInTheDocument();
    expect(within(patterns).getByText("added by you")).toBeInTheDocument();
    expect(
      within(patterns).getByText("Detection was weak on this photo."),
    ).toBeInTheDocument();
    const optionHeading = within(patterns).getByRole("heading", {
      level: 3,
      name: "Palak Paneer",
    });
    expect(optionHeading).toBeInTheDocument();
    const optionCard = optionHeading.closest(".option-card");
    expect(optionCard).not.toBeNull();
    expect(optionCard?.querySelector(".option-card-masthead")).not.toBeNull();
    expect(optionCard?.querySelector(".option-card-index")).toHaveTextContent(
      "01batch 01",
    );
    expect(within(patterns).queryByText("AI image")).toBeNull();
    expect(patterns.querySelector(".option-card-media")).toBeNull();
    expect(within(patterns).queryByLabelText("Nutrition estimate")).toBeNull();
    expect(
      within(patterns).queryByText("Estimates only — not medical advice."),
    ).toBeNull();

    const layout = screen.getByRole("region", {
      name: "Layout and responsiveness",
    });
    expect(layout).toBeInTheDocument();
    expect(
      within(layout).getByText(/completed-dish previews in their own bordered block/i),
    ).toBeVisible();
    const accessibility = screen.getByRole("region", {
      name: "Accessibility and interaction",
    });
    expect(accessibility).toBeInTheDocument();
    expect(
      within(accessibility).getByText(/label every generated preview as AI image/i),
    ).toBeVisible();
  });

  it("keeps generated text and the bordered preview inside narrow layouts", () => {
    for (const selector of [".job-content", ".option-card-content", ".recipe-title"]) {
      expect(declarationsFor(selector)).toMatch(/overflow-wrap:\s*anywhere\s*;/);
    }

    expect(declarationsFor(".recipe-preview-figure figcaption")).toMatch(
      /margin-top:\s*0\s*;/,
    );
  });
});
