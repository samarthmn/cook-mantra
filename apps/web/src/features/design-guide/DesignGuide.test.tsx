import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { DesignGuide } from "./DesignGuide";

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
    expect(
      within(patterns).getByRole("heading", { level: 3, name: "Palak Paneer" }),
    ).toBeInTheDocument();
    expect(within(patterns).getByText("410")).toBeInTheDocument();
    expect(within(patterns).getByText("kcal")).toBeInTheDocument();

    expect(
      screen.getByRole("region", { name: "Layout and responsiveness" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("region", { name: "Accessibility and interaction" }),
    ).toBeInTheDocument();
  });
});
