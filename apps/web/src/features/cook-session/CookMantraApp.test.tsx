import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CookMantraClient } from "@/lib/api/cook-mantra-client";

import { CookMantraApp } from "./CookMantraApp";

describe("CookMantraApp", () => {
  beforeEach(() => {
    localStorage.clear();
    document.documentElement.removeAttribute("data-theme");
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.unstubAllEnvs();
  });

  it("shows a controlled configuration notice in a production build without an API URL", () => {
    vi.stubEnv("NODE_ENV", "production");
    vi.stubEnv("NEXT_PUBLIC_COOK_MANTRA_API_URL", "");

    render(<CookMantraApp />);

    expect(
      screen.getByRole("heading", {
        name: "Cook Mantra is not connected to its kitchen",
      }),
    ).toBeVisible();
    expect(screen.getByText("NEXT_PUBLIC_COOK_MANTRA_API_URL")).toBeVisible();
    expect(
      screen.queryByRole("button", { name: "Type ingredients instead" }),
    ).toBeNull();
    expect(screen.getByRole("banner")).toBeVisible();
  });

  it("does not show the weak-detection simulator by default", () => {
    render(<CookMantraApp />);

    expect(
      screen.queryByRole("button", { name: "Simulate weak detection" }),
    ).not.toBeInTheDocument();
  });

  it("does not show the failure simulator by default", async () => {
    const client = new CookMantraClient({ fetch: vi.fn<typeof fetch>() });
    vi.spyOn(client, "createSession").mockImplementation(() => new Promise(() => {}));
    const user = userEvent.setup();
    const { container } = render(<CookMantraApp apiClient={client} />);

    await user.upload(
      container.querySelector<HTMLInputElement>('input[type="file"]:not([capture])')!,
      new File(["verified-image-bytes"], "ingredients.jpg", {
        type: "image/jpeg",
      }),
    );

    expect(
      screen.queryByRole("button", { name: "Simulate a failure" }),
    ).not.toBeInTheDocument();
  });

  it("completes the local ingredient-to-recipe journey", async () => {
    const user = userEvent.setup();
    render(<CookMantraApp devControls demoJobDurationMs={0} />);

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
    expect(screen.queryAllByText("AI image")).toHaveLength(0);
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

    await user.click(screen.getByRole("button", { name: "Save recipe" }));
    expect(
      screen.getByRole("button", { name: "Saved recipes, 1 saved" }),
    ).toBeVisible();
  });

  it("opens saved recipes and returns to the untouched session view", async () => {
    const user = userEvent.setup();
    render(<CookMantraApp demoJobDurationMs={0} />);

    await user.click(screen.getByRole("button", { name: "Type ingredients instead" }));
    const instructions = screen.getByRole("textbox", {
      name: "Special instructions",
    });
    await user.type(instructions, "Keep the pan on low heat");

    await user.click(screen.getByRole("button", { name: "Saved recipes" }));
    expect(screen.getByRole("heading", { name: "Saved recipes" })).toBeVisible();
    expect(instructions).not.toBeVisible();

    await user.click(screen.getByRole("button", { name: "Back" }));
    expect(screen.getByRole("heading", { name: "Check what we found" })).toBeVisible();
    expect(instructions).toHaveValue("Keep the pan on low heat");
  });

  it("shows the weak-detection recovery state without assuming pantry staples", async () => {
    const user = userEvent.setup();
    render(<CookMantraApp devControls demoJobDurationMs={0} />);

    await user.click(screen.getByRole("button", { name: "Simulate weak detection" }));

    expect(
      await screen.findByRole("heading", { name: "Check what we found" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Detection was weak on this photo.")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Confidence: low, 41 percent" }),
    ).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByRole("checkbox", { name: "Salt pantry" })).not.toBeChecked();
    expect(screen.getByRole("button", { name: "Remove Tomatoes" })).toBeInTheDocument();
  });

  it("keeps diet style single-select, preserves add-ons, and normalizes allergens", async () => {
    const user = userEvent.setup();
    render(<CookMantraApp demoJobDurationMs={0} />);

    await user.click(screen.getByRole("button", { name: "Type ingredients instead" }));

    expect(screen.getByRole("heading", { name: "Preferences" })).toBeVisible();
    expect(screen.getByRole("radio", { name: "Veg" })).toBeChecked();
    expect(screen.getByRole("radio", { name: "Vegetarian" })).toBeChecked();
    expect(screen.getByRole("radio", { name: "Medium" })).toBeChecked();

    await user.click(screen.getByRole("radio", { name: "Vegan" }));
    await user.click(screen.getByRole("radio", { name: "Eggetarian" }));
    await user.click(screen.getByRole("button", { name: "Keto" }));
    expect(screen.getByRole("radio", { name: "Vegan" })).not.toBeChecked();
    expect(screen.getByRole("radio", { name: "Eggetarian" })).toBeChecked();

    await user.click(screen.getByRole("radio", { name: "Non-Veg" }));
    expect(screen.queryByRole("radio", { name: "Vegan" })).not.toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "Any" })).toBeChecked();
    expect(screen.getByRole("button", { name: "Keto" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );

    await user.click(screen.getByRole("radio", { name: "Veg" }));
    expect(screen.getByRole("radio", { name: "Vegetarian" })).toBeChecked();
    expect(screen.getByRole("radio", { name: "Vegan" })).not.toBeChecked();

    const dairy = screen.getByRole("button", { name: "Dairy" });
    await user.click(dairy);
    expect(dairy).toHaveAttribute("aria-pressed", "true");

    const customAllergen = screen.getByRole("textbox", {
      name: "Custom allergen",
    });
    await user.type(customAllergen, "Mustard{Enter}");
    await user.type(customAllergen, "mustard{Enter}");
    await user.type(customAllergen, "dairy{Enter}");
    expect(
      screen.getAllByRole("button", { name: "Remove Mustard allergen" }),
    ).toHaveLength(1);
    expect(dairy).toHaveAttribute("aria-pressed", "true");

    await user.click(screen.getByRole("radio", { name: "Extra hot" }));
    expect(screen.getByRole("radio", { name: "Extra hot" })).toBeChecked();
    await user.type(
      screen.getByRole("textbox", { name: "Special instructions" }),
      "Kid friendly and low oil",
    );
    expect(screen.getByRole("textbox", { name: "Special instructions" })).toHaveValue(
      "Kid friendly and low oil",
    );
  });

  it("selects, partially selects, and clears every pantry staple", async () => {
    const user = userEvent.setup();
    render(<CookMantraApp demoJobDurationMs={0} />);

    await user.click(screen.getByRole("button", { name: "Type ingredients instead" }));
    const selectAll = screen.getByRole("checkbox", {
      name: "Select all pantry staples",
    });
    const pantryItems = screen.getAllByRole("checkbox", { name: / pantry$/ });

    expect(selectAll).not.toBeChecked();
    expect(selectAll).not.toBePartiallyChecked();
    await user.click(screen.getByRole("checkbox", { name: "Salt pantry" }));
    expect(selectAll).toBePartiallyChecked();

    await user.click(selectAll);
    expect(selectAll).toBeChecked();
    expect(selectAll).not.toBePartiallyChecked();
    pantryItems.forEach((checkbox) => expect(checkbox).toBeChecked());

    await user.click(selectAll);
    expect(selectAll).not.toBeChecked();
    expect(selectAll).not.toBePartiallyChecked();
    pantryItems.forEach((checkbox) => expect(checkbox).not.toBeChecked());
  });

  it("persists edited pantry defaults and applies them immediately and on remount", async () => {
    const user = userEvent.setup();
    const firstRender = render(<CookMantraApp demoJobDurationMs={0} />);

    await user.click(screen.getByRole("button", { name: "Type ingredients instead" }));
    await user.click(screen.getByRole("button", { name: "Edit defaults" }));
    const editor = screen.getByRole("region", { name: "Edit pantry defaults" });
    const firstStaple = within(editor).getByRole("textbox", {
      name: "Pantry staple 1",
    });
    await user.clear(firstStaple);
    await user.type(firstStaple, "Sea salt");
    await user.click(
      within(editor).getByRole("button", { name: "Remove Pepper powder" }),
    );
    await user.type(
      within(editor).getByRole("textbox", { name: "Add a pantry staple" }),
      "Cumin seeds{Enter}",
    );
    await user.click(within(editor).getByRole("button", { name: "Save defaults" }));

    expect(screen.getByRole("checkbox", { name: "Sea salt pantry" })).toBeVisible();
    expect(
      screen.queryByRole("checkbox", { name: "Salt pantry" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("checkbox", { name: "Pepper powder pantry" }),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "Cumin seeds pantry" })).toBeVisible();
    expect(localStorage.getItem("cook-mantra:pantry-staples")).toBe(
      JSON.stringify([
        "Sea salt",
        "Oil or ghee",
        "Chilli powder",
        "Onion",
        "Garlic",
        "Ginger",
        "Cumin seeds",
      ]),
    );

    firstRender.unmount();
    render(<CookMantraApp demoJobDurationMs={0} />);
    await user.click(screen.getByRole("button", { name: "Type ingredients instead" }));
    expect(
      await screen.findByRole("checkbox", { name: "Sea salt pantry" }),
    ).toBeVisible();
    expect(screen.getByRole("checkbox", { name: "Cumin seeds pantry" })).toBeVisible();
  });

  it("keeps pantry edits for the visit when device persistence fails", async () => {
    const user = userEvent.setup();
    render(<CookMantraApp demoJobDurationMs={0} />);

    await user.click(screen.getByRole("button", { name: "Type ingredients instead" }));
    await user.click(screen.getByRole("button", { name: "Edit defaults" }));
    const firstStaple = screen.getByRole("textbox", { name: "Pantry staple 1" });
    await user.clear(firstStaple);
    await user.type(firstStaple, "Sea salt");
    vi.spyOn(window.localStorage, "setItem").mockImplementation(() => {
      throw new DOMException("Storage blocked", "SecurityError");
    });

    await user.click(screen.getByRole("button", { name: "Save defaults" }));

    expect(screen.getByRole("checkbox", { name: "Sea salt pantry" })).toBeVisible();
    expect(
      screen.getByText(/defaults could not be saved on this device/i),
    ).toBeVisible();
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
    render(<CookMantraApp devControls demoJobDurationMs={0} />);

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
    render(<CookMantraApp devControls demoJobDurationMs={0} />);

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
    render(<CookMantraApp devControls demoJobDurationMs={0} />);

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
    render(<CookMantraApp devControls demoJobDurationMs={5_000} />);

    await user.click(screen.getByRole("button", { name: "Simulate weak detection" }));
    await user.click(screen.getByRole("button", { name: "Simulate a failure" }));

    expect(screen.getByRole("alert")).toHaveTextContent("That took too long");
    expect(screen.getByRole("button", { name: "Try again" })).toBeEnabled();
  });
});
