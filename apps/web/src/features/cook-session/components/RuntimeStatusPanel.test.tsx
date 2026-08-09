import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type {
  ModelProvider,
  ModelRole,
  ModelRoleReadiness,
  RuntimeStatusResponse,
} from "@/types/api";
import { RuntimeStatusPanel } from "./RuntimeStatusPanel";

afterEach(cleanup);

function readyRole(
  role: ModelRole,
  provider: ModelProvider,
  model: string,
): ModelRoleReadiness {
  const requiredCapabilities =
    role === "ingredient_extractor"
      ? (["structured_output", "vision"] as const)
      : role === "image_generator"
        ? (["image_output"] as const)
        : (["structured_output", "text"] as const);
  return {
    role,
    provider,
    model,
    enabled: true,
    ready: true,
    required_capabilities: [...requiredCapabilities],
    available_capabilities: [...requiredCapabilities],
    error: null,
  };
}

function runtimeStatus(): RuntimeStatusResponse {
  return {
    status: "ok",
    runtime_revision: "runtime-revision-1234567890",
    model_runtime: {
      ready: true,
      roles: {
        ingredient_extractor: readyRole("ingredient_extractor", "ollama", "qwen3.5:9b"),
        master_chef: readyRole(
          "master_chef",
          "openrouter",
          "anthropic/claude-sonnet-4",
        ),
        recipe_writer: readyRole("recipe_writer", "codex", "gpt-5.3-codex"),
        image_generator: readyRole(
          "image_generator",
          "openrouter",
          "black-forest-labs/flux.1",
        ),
      },
    },
  };
}

describe("RuntimeStatusPanel", () => {
  it("announces loading without stale details while keeping refresh recoverable", async () => {
    const onRefresh = vi.fn();
    const user = userEvent.setup();
    render(<RuntimeStatusPanel state={{ phase: "loading" }} onRefresh={onRefresh} />);

    const panel = screen.getByRole("region", { name: "Local model status" });
    expect(within(panel).getByRole("status")).toHaveTextContent(
      "Checking local model status",
    );
    const refresh = within(panel).getByRole("button", {
      name: "Refresh local status",
    });
    expect(refresh).toBeEnabled();
    await user.click(refresh);
    expect(onRefresh).toHaveBeenCalledOnce();
    expect(within(panel).queryByRole("list")).not.toBeInTheDocument();
  });

  it("offers a retry when the local status endpoint is unavailable", async () => {
    const onRefresh = vi.fn();
    const user = userEvent.setup();
    render(
      <RuntimeStatusPanel state={{ phase: "unavailable" }} onRefresh={onRefresh} />,
    );

    expect(screen.getByRole("status")).toHaveTextContent(
      "Could not read local model status. Check that the local API is running before sending a photo.",
    );
    await user.click(screen.getByRole("button", { name: "Refresh local status" }));
    expect(onRefresh).toHaveBeenCalledOnce();
  });

  it("shows the four role-safe rows in cooking order", () => {
    render(
      <RuntimeStatusPanel
        state={{ phase: "available", snapshot: runtimeStatus() }}
        onRefresh={vi.fn()}
      />,
    );

    const rows = screen.getAllByRole("listitem");
    expect(
      rows.map((row) => within(row).getByRole("heading", { level: 3 }).textContent),
    ).toEqual([
      "Ingredient recognition",
      "Recipe ideas",
      "Recipe writing",
      "Dish preview",
    ]);
    expect(rows).toHaveLength(4);
    const ingredientCaption = rows[0].querySelector<HTMLElement>(
      ".runtime-ledger-caption",
    );
    expect(ingredientCaption).toHaveTextContent("Ollama · qwen3.5:9b");
    expect(
      within(ingredientCaption!)
        .getAllByText(/Ollama|qwen3\.5:9b/)
        .map((element) => element.tagName),
    ).toEqual(["BDI", "BDI"]);
    expect(rows[1].querySelector(".runtime-ledger-caption")).toHaveTextContent(
      "OpenRouter · anthropic/claude-sonnet-4",
    );
    expect(rows[2].querySelector(".runtime-ledger-caption")).toHaveTextContent(
      "Codex · gpt-5.3-codex",
    );
    expect(rows[3].querySelector(".runtime-ledger-caption")).toHaveTextContent(
      "OpenRouter · black-forest-labs/flux.1",
    );
    expect(screen.getAllByText("Ready")).toHaveLength(4);
    expect(
      screen.getByText(
        "Model setting changes take effect after the local API restarts.",
      ),
    ).toBeVisible();
  });

  it("distinguishes a blocking ingredient failure from an optional preview failure", () => {
    const snapshot = runtimeStatus();
    snapshot.status = "attention";
    snapshot.model_runtime.ready = false;
    snapshot.model_runtime.roles.ingredient_extractor = {
      ...snapshot.model_runtime.roles.ingredient_extractor,
      ready: false,
      error: "unavailable",
    };
    snapshot.model_runtime.roles.image_generator = {
      ...snapshot.model_runtime.roles.image_generator,
      ready: false,
      error: "capability_missing",
    };

    render(
      <RuntimeStatusPanel
        state={{ phase: "available", snapshot }}
        onRefresh={vi.fn()}
      />,
    );

    expect(screen.getByText(/Type ingredients instead/)).toHaveTextContent(
      "Ingredient recognition needs attention. Type ingredients instead until it is ready.",
    );
    expect(
      screen.getByText("Dish previews are unavailable; recipes can still be created."),
    ).toBeVisible();
    expect(screen.getAllByText("Needs attention")).toHaveLength(2);
  });

  it("labels disabled roles and renders provider model text without interpreting markup", () => {
    const snapshot = runtimeStatus();
    const hostileModel = `<img src=x onerror="alert('no')">${"x".repeat(120)}`;
    snapshot.model_runtime.roles.image_generator = {
      ...snapshot.model_runtime.roles.image_generator,
      enabled: false,
      ready: false,
      provider: null,
      model: null,
      error: null,
    };
    snapshot.model_runtime.roles.recipe_writer = {
      ...snapshot.model_runtime.roles.recipe_writer,
      model: hostileModel,
    };

    const { container } = render(
      <RuntimeStatusPanel
        state={{ phase: "available", snapshot }}
        onRefresh={vi.fn()}
      />,
    );

    expect(screen.getByText("Disabled")).toBeVisible();
    expect(screen.getByText("Provider and model not configured")).toBeVisible();
    const hostileIdentifier = screen.getByText(hostileModel, { selector: "bdi" });
    expect(hostileIdentifier.closest(".runtime-ledger-caption")).toHaveTextContent(
      `Codex · ${hostileModel}`,
    );
    expect(container.querySelector("img")).toBeNull();
  });

  it("keeps required-role guidance visible for a disabled non-image role", () => {
    const snapshot = runtimeStatus();
    snapshot.status = "attention";
    snapshot.model_runtime.ready = false;
    snapshot.model_runtime.roles.master_chef = {
      ...snapshot.model_runtime.roles.master_chef,
      enabled: false,
      ready: false,
      error: null,
    };

    render(
      <RuntimeStatusPanel
        state={{ phase: "available", snapshot }}
        onRefresh={vi.fn()}
      />,
    );

    expect(
      screen.getByText("Recipe generation needs attention before it can run."),
    ).toBeVisible();
  });
});
