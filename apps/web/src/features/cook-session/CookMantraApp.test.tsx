import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CookMantraApp } from "./CookMantraApp";

describe("CookMantraApp", () => {
  beforeEach(() => {
    localStorage.clear();
    document.documentElement.removeAttribute("data-theme");
  });

  afterEach(() => cleanup());

  it("completes the local ingredient-to-recipe journey", async () => {
    const user = userEvent.setup();
    render(<CookMantraApp demoJobDurationMs={0} />);

    await user.click(screen.getByRole("button", { name: "Simulate weak detection" }));
    expect(
      await screen.findByRole("heading", { name: "Check what we found" }),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Remove Tomatoes" }));
    expect(
      screen.getByRole("button", { name: "Generate recipe ideas" }),
    ).toBeDisabled();
    expect(
      screen.getByText("Add at least one ingredient to continue"),
    ).toBeInTheDocument();

    await user.type(
      screen.getByRole("textbox", { name: "Add an ingredient" }),
      "Spinach",
    );
    await user.click(screen.getByRole("button", { name: "Add ingredient" }));

    expect(screen.getByText("1 ingredient confirmed")).toBeInTheDocument();
    const generateButton = screen.getByRole("button", {
      name: "Generate recipe ideas",
    });
    expect(generateButton).toBeEnabled();
    await user.click(generateButton);

    expect(
      await screen.findByRole("heading", { name: "3 ideas from your 1 ingredient" }),
    ).toBeInTheDocument();
    // Demo options carry no generated image, so the placeholder must not claim to be one.
    expect(screen.queryAllByText("AI illustration")).toHaveLength(0);
    expect(screen.getByText("Pick at least one dish")).toBeInTheDocument();
    const palakPaneer = screen.getByRole("button", { name: /Palak Paneer/ });
    expect(palakPaneer).toHaveAttribute("aria-pressed", "false");
    await user.click(palakPaneer);
    expect(palakPaneer).toHaveAttribute("aria-pressed", "true");

    await user.click(screen.getByRole("button", { name: "Create 1 recipe" }));
    expect(
      await screen.findByRole("heading", { name: "Your recipe, ready" }),
    ).toBeInTheDocument();
    expect(screen.getAllByRole("separator")).toHaveLength(1);

    const firstStep = screen.getByRole("button", {
      name: /Blanch spinach in boiling water/,
    });
    await user.click(firstStep);
    expect(firstStep).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText("1 / 7 done")).toBeInTheDocument();
  });

  it("shows the weak-detection recovery state without assuming pantry staples", async () => {
    const user = userEvent.setup();
    render(<CookMantraApp demoJobDurationMs={0} />);

    await user.click(screen.getByRole("button", { name: "Simulate weak detection" }));

    expect(
      await screen.findByRole("heading", { name: "Check what we found" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Detection was weak on this photo.")).toBeInTheDocument();
    expect(screen.getByText("41%").parentElement).not.toHaveAttribute("aria-hidden");
    expect(screen.getByRole("checkbox", { name: "Salt pantry" })).not.toBeChecked();
    expect(screen.getByRole("button", { name: "Remove Tomatoes" })).toBeInTheDocument();
  });

  it("blocks generation and explains duplicate ingredient names inline", async () => {
    const user = userEvent.setup();
    render(<CookMantraApp demoJobDurationMs={0} />);

    await user.click(screen.getByRole("button", { name: "Type ingredients instead" }));
    await user.type(
      screen.getByRole("textbox", { name: "Add an ingredient" }),
      "Onions",
    );
    await user.click(screen.getByRole("button", { name: "Add ingredient" }));
    const ingredientInput = screen.getByRole("textbox", {
      name: "Ingredient Onions",
    });
    await user.clear(ingredientInput);
    await user.type(ingredientInput, "Onion");

    expect(ingredientInput).toHaveAttribute("aria-invalid", "true");
    expect(
      screen.getByText(
        "Ingredient names must be unique. Rename or remove the duplicate.",
      ),
    ).toBeVisible();
    expect(
      screen.getByRole("button", { name: "Generate recipe ideas" }),
    ).toBeDisabled();
    expect(screen.getByText("Give every ingredient a different name")).toBeVisible();
  });

  it("persists a manual theme choice", async () => {
    const user = userEvent.setup();
    render(<CookMantraApp demoJobDurationMs={0} />);

    await user.click(screen.getByRole("button", { name: "Switch to dark theme" }));

    await waitFor(() => {
      expect(document.documentElement).toHaveAttribute("data-theme", "dark");
    });
    expect(localStorage.getItem("cm-theme")).toBe("dark");
  });

  it("returns the viewport to the top when the active screen changes", async () => {
    const user = userEvent.setup();
    const scrollTo = vi.spyOn(window, "scrollTo").mockImplementation(() => {});
    const focus = vi.spyOn(HTMLElement.prototype, "focus");
    render(<CookMantraApp demoJobDurationMs={0} />);
    scrollTo.mockClear();

    await user.click(screen.getByRole("button", { name: "Type ingredients instead" }));

    expect(scrollTo).toHaveBeenCalledWith({
      top: 0,
      left: 0,
      behavior: "auto",
    });
    expect(screen.getByRole("heading", { name: "Check what we found" })).toHaveFocus();
    expect(focus).toHaveBeenCalledWith({ preventScroll: true });
  });

  it("returns to a previously generated recipe from the progress navigation", async () => {
    const user = userEvent.setup();
    render(<CookMantraApp demoJobDurationMs={0} />);

    await user.click(screen.getByRole("button", { name: "Simulate weak detection" }));
    await user.click(await screen.findByRole("button", { name: "Remove Tomatoes" }));
    await user.type(
      screen.getByRole("textbox", { name: "Add an ingredient" }),
      "Spinach",
    );
    await user.click(screen.getByRole("button", { name: "Add ingredient" }));
    await user.click(screen.getByRole("button", { name: "Generate recipe ideas" }));
    await screen.findByRole("heading", { name: "3 ideas from your 1 ingredient" });
    await user.click(screen.getByRole("button", { name: /Palak Paneer/ }));
    await user.click(screen.getByRole("button", { name: "Create 1 recipe" }));
    await screen.findByRole("heading", { name: "Your recipe, ready" });

    await user.click(screen.getByRole("button", { name: /Choose/ }));
    expect(
      screen.getByRole("heading", { name: "3 ideas from your 1 ingredient" }),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /Cook/ }));

    expect(
      screen.getByRole("heading", { name: "Your recipe, ready" }),
    ).toBeInTheDocument();
  });

  it("moves between completed recipe tabs with the arrow keys", async () => {
    const user = userEvent.setup();
    render(<CookMantraApp demoJobDurationMs={0} />);

    await user.click(screen.getByRole("button", { name: "Simulate weak detection" }));
    await user.click(await screen.findByRole("button", { name: "Remove Tomatoes" }));
    await user.type(
      screen.getByRole("textbox", { name: "Add an ingredient" }),
      "Spinach",
    );
    await user.click(screen.getByRole("button", { name: "Add ingredient" }));
    await user.click(screen.getByRole("button", { name: "Generate recipe ideas" }));
    await screen.findByRole("heading", {
      name: "3 ideas from your 1 ingredient",
    });
    await user.click(screen.getByRole("button", { name: /Palak Paneer/ }));
    await user.click(screen.getByRole("button", { name: /Spinach Tomato Dal/ }));
    await user.click(screen.getByRole("button", { name: "Create 2 recipes" }));
    await screen.findByRole("heading", { name: "Your 2 recipes, ready" });

    const tabs = screen.getAllByRole("tab");
    tabs[0].focus();
    await user.keyboard("{ArrowRight}");

    expect(tabs[1]).toHaveFocus();
    expect(tabs[1]).toHaveAttribute("aria-selected", "true");
    expect(
      screen.getByRole("heading", { name: "Spinach Tomato Dal", level: 2 }),
    ).toBeInTheDocument();
  });

  it("explains when no unseen overlapping ideas remain", async () => {
    const user = userEvent.setup();
    render(<CookMantraApp demoJobDurationMs={0} />);

    await user.click(screen.getByRole("button", { name: "Simulate weak detection" }));
    await user.click(await screen.findByRole("button", { name: "Remove Tomatoes" }));
    await user.type(
      screen.getByRole("textbox", { name: "Add an ingredient" }),
      "Spinach",
    );
    await user.click(screen.getByRole("button", { name: "Add ingredient" }));
    await user.click(screen.getByRole("button", { name: "Generate recipe ideas" }));
    await screen.findByRole("heading", {
      name: "3 ideas from your 1 ingredient",
    });

    await user.click(screen.getByRole("button", { name: "More ideas" }));
    expect(
      await screen.findByText(
        "No fresh ideas left for these ingredients — recipes already shown are never repeated. Try editing your ingredient list.",
      ),
    ).toBeInTheDocument();
  });

  it("returns a simulated job failure to the prior screen with retry intact", async () => {
    const user = userEvent.setup();
    render(<CookMantraApp demoJobDurationMs={5_000} />);

    await user.click(screen.getByRole("button", { name: "Simulate weak detection" }));
    await user.click(screen.getByRole("button", { name: "Simulate a failure" }));

    expect(screen.getByRole("alert")).toHaveTextContent("That took too long");
    expect(screen.getByRole("button", { name: "Try again" })).toBeEnabled();
  });
});
