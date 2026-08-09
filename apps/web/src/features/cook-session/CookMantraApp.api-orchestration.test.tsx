import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ApiError,
  ApiTimeoutError,
  CookMantraClient,
} from "@/lib/api/cook-mantra-client";
import type {
  JobResponse,
  RuntimeStatusResponse,
  SessionResponse,
  TerminalJobResponse,
} from "@/types/api";

import { CookMantraApp } from "./CookMantraApp";
import {
  persistRemoteMediaAcknowledgement,
  type RemoteMediaDisclosureTarget,
} from "./model/remote-media-disclosure";

const originalCreateObjectUrl = Object.getOwnPropertyDescriptor(URL, "createObjectURL");
const originalRevokeObjectUrl = Object.getOwnPropertyDescriptor(URL, "revokeObjectURL");

const extractedSession: SessionResponse = {
  id: "session-api-123",
  stage: "reviewing_ingredients",
  image_artifact_id: "artifact-upload-123",
  ingredients: [
    {
      id: "ingredient-tomato",
      name: "Tomato",
      source: "detected",
      confidence: 0.94,
      confirmed: false,
    },
    {
      id: "ingredient-salt",
      name: "Salt",
      source: "pantry_suggestion",
      confidence: null,
      confirmed: false,
    },
  ],
  preferences: {
    dietary_preferences: [],
    allergens: [],
    preferred_cuisines: [],
    max_total_minutes: null,
    servings: 2,
    option_count: 4,
    spice_level: null,
    special_instructions: "",
  },
  recipe_options: [],
  complete_recipes: {},
  recipe_failures: {},
  excluded_recipe_names: [],
  option_batch_number: 0,
  warnings: [],
  created_at: "2026-07-31T08:00:00Z",
  updated_at: "2026-07-31T08:00:01Z",
};

const optionsSession: SessionResponse = {
  ...extractedSession,
  stage: "options_ready",
  ingredients: [
    { ...extractedSession.ingredients[0], confirmed: true },
    extractedSession.ingredients[1],
  ],
  preferences: {
    dietary_preferences: ["vegetarian"],
    allergens: [],
    preferred_cuisines: [],
    max_total_minutes: null,
    servings: 2,
    option_count: 4,
    spice_level: "medium",
    special_instructions: "",
  },
  recipe_options: [
    {
      id: "option-tomato-skillet",
      name: "Tomato Skillet",
      summary: "A quick tomato-forward skillet meal.",
      cuisine: "Indian",
      total_minutes: 25,
      difficulty: "easy",
      used_ingredients: ["Tomato"],
      missing_ingredients: [],
      optional_ingredients: [
        {
          name: "Coriander",
          reason: "Adds a fresh finish.",
          substitution: null,
        },
      ],
      nutrition: {
        calories_kcal: 280,
        protein_g: 8,
        carbohydrates_g: 32,
        fat_g: 12,
        diet_tags: ["vegetarian"],
        allergen_warnings: [],
        disclaimer: "Estimated values; not medical advice.",
      },
    },
  ],
  excluded_recipe_names: ["Tomato Skillet"],
  option_batch_number: 1,
  updated_at: "2026-07-31T08:00:05Z",
};

const freshExtractedSession: SessionResponse = {
  ...extractedSession,
  id: "session-api-456",
  image_artifact_id: "artifact-upload-456",
  ingredients: [
    { ...extractedSession.ingredients[0], id: "fresh-ingredient-tomato" },
    { ...extractedSession.ingredients[1], id: "fresh-ingredient-salt" },
  ],
  updated_at: "2026-07-31T08:01:01Z",
};

const freshOptionsSession: SessionResponse = {
  ...optionsSession,
  id: "session-api-456",
  ingredients: [
    {
      id: "fresh-added-cherry-tomato",
      name: "Cherry Tomato",
      source: "user_added",
      confidence: null,
      confirmed: true,
    },
    { ...freshExtractedSession.ingredients[1], confirmed: false },
  ],
  recipe_options: [
    {
      ...optionsSession.recipe_options[0],
      id: "option-cherry-tomato-skillet",
      name: "Cherry Tomato Skillet",
      used_ingredients: ["Cherry Tomato"],
    },
  ],
  excluded_recipe_names: ["Cherry Tomato Skillet"],
  updated_at: "2026-07-31T08:01:05Z",
};

const MANUAL_PANTRY_NAMES = [
  "Salt",
  "Pepper powder",
  "Oil or ghee",
  "Chilli powder",
  "Onion",
  "Garlic",
  "Ginger",
];

const recipesSession: SessionResponse = {
  ...optionsSession,
  stage: "recipes_ready",
  complete_recipes: {
    "option-tomato-skillet": {
      option_id: "option-tomato-skillet",
      name: "Tomato Skillet",
      cuisine: "Indian",
      servings: 2,
      total_minutes: 25,
      ingredients: [
        {
          name: "Tomato",
          quantity: "2",
          availability: "available",
          substitution: null,
        },
      ],
      steps: [
        {
          number: 1,
          instruction: "Cook the tomatoes until soft.",
          duration_minutes: 10,
        },
      ],
      tips: [],
      substitutions: [],
      nutrition_notice: "Estimated values; not medical advice.",
      allergen_notice: "Check ingredient labels for allergens.",
      assumptions: [],
      warnings: [],
      preview: null,
    },
  },
  updated_at: "2026-07-31T08:00:09Z",
};

const RUNTIME_REVISION_A = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA";
const RUNTIME_REVISION_B = "BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB";

function runtimeStatus(
  provider: "ollama" | "openrouter" | "codex" = "ollama",
  model = provider === "ollama" ? "qwen3.5:9b" : "openai/gpt-5.2",
  runtimeRevision = RUNTIME_REVISION_A,
  ingredientReady = true,
): RuntimeStatusResponse {
  return {
    status: ingredientReady ? "ok" : "attention",
    runtime_revision: runtimeRevision,
    model_runtime: {
      ready: ingredientReady,
      roles: {
        ingredient_extractor: {
          role: "ingredient_extractor",
          provider,
          model,
          enabled: true,
          ready: ingredientReady,
          required_capabilities: ["structured_output", "vision"],
          available_capabilities: ingredientReady
            ? ["structured_output", "vision"]
            : [],
          error: ingredientReady ? null : "unavailable",
        },
        master_chef: {
          role: "master_chef",
          provider: "ollama",
          model: "qwen3.5:9b",
          enabled: true,
          ready: true,
          required_capabilities: ["structured_output", "text"],
          available_capabilities: ["structured_output", "text"],
          error: null,
        },
        recipe_writer: {
          role: "recipe_writer",
          provider: "ollama",
          model: "qwen3.5:9b",
          enabled: true,
          ready: true,
          required_capabilities: ["structured_output", "text"],
          available_capabilities: ["structured_output", "text"],
          error: null,
        },
        image_generator: {
          role: "image_generator",
          provider: "ollama",
          model: "x/z-image-turbo:latest",
          enabled: false,
          ready: true,
          required_capabilities: ["image_output"],
          available_capabilities: [],
          error: null,
        },
      },
    },
  };
}

function persistRemoteTarget(target: RemoteMediaDisclosureTarget) {
  expect(persistRemoteMediaAcknowledgement(localStorage, target)).toBe(true);
}

function deferred<Value>() {
  let resolve!: (value: Value) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<Value>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

function job(
  operation: JobResponse["operation"],
  status: JobResponse["status"],
  progress: number,
  result: Record<string, unknown> | null,
): JobResponse {
  return {
    id: operation === "extract_ingredients" ? "job-extract" : "job-options",
    operation,
    session_id: "session-api-123",
    status,
    progress,
    result,
    warnings: [],
    error: null,
    created_at: "2026-07-31T08:00:00Z",
    updated_at: "2026-07-31T08:00:01Z",
  };
}

describe("CookMantraApp API orchestration", () => {
  beforeEach(() => {
    localStorage.clear();
    document.documentElement.removeAttribute("data-theme");
    vi.spyOn(CookMantraClient.prototype, "getRuntimeStatus").mockResolvedValue(
      runtimeStatus(),
    );
    Object.defineProperty(URL, "createObjectURL", {
      configurable: true,
      value: vi.fn(() => "blob:cook-mantra-ingredients"),
    });
    Object.defineProperty(URL, "revokeObjectURL", {
      configurable: true,
      value: vi.fn(),
    });
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    restoreUrlProperty("createObjectURL", originalCreateObjectUrl);
    restoreUrlProperty("revokeObjectURL", originalRevokeObjectUrl);
  });

  it("creates no browser or server upload state until a fresh local status passes", async () => {
    const client = new CookMantraClient({ fetch: vi.fn<typeof fetch>() });
    const selectedStatus = deferred<RuntimeStatusResponse>();
    const getRuntimeStatus = vi
      .spyOn(client, "getRuntimeStatus")
      .mockResolvedValueOnce(runtimeStatus())
      .mockReturnValueOnce(selectedStatus.promise);
    const createSession = vi.spyOn(client, "createSession").mockResolvedValue({
      session_id: "session-api-123",
      job_id: "job-extract",
    });
    vi.spyOn(client, "pollJob").mockImplementation(() => new Promise(() => {}));
    const user = userEvent.setup();
    const { container } = render(<CookMantraApp apiClient={client} />);
    await screen.findByText("Ingredient recognition");
    const image = new File(["verified-image-bytes"], "ingredients.jpg", {
      type: "image/jpeg",
    });

    await user.upload(
      container.querySelector<HTMLInputElement>('input[type="file"]:not([capture])')!,
      image,
    );

    expect(getRuntimeStatus).toHaveBeenCalledTimes(2);
    expect(URL.createObjectURL).not.toHaveBeenCalled();
    expect(createSession).not.toHaveBeenCalled();

    await act(async () => selectedStatus.resolve(runtimeStatus()));

    await waitFor(() =>
      expect(createSession).toHaveBeenCalledWith(
        image,
        expect.objectContaining({
          runtimeRevision: RUNTIME_REVISION_A,
          signal: expect.any(AbortSignal),
        }),
      ),
    );
    expect(URL.createObjectURL).toHaveBeenCalledTimes(1);
  });

  it("holds remote media in memory until the user accepts the exact disclosure", async () => {
    const selectedRuntime = runtimeStatus(
      "openrouter",
      "openai/gpt-5.2",
      RUNTIME_REVISION_A,
    );
    const client = new CookMantraClient({ fetch: vi.fn<typeof fetch>() });
    vi.spyOn(client, "getRuntimeStatus").mockResolvedValue(selectedRuntime);
    const createSession = vi.spyOn(client, "createSession").mockResolvedValue({
      session_id: "session-api-123",
      job_id: "job-extract",
    });
    vi.spyOn(client, "pollJob").mockImplementation(() => new Promise(() => {}));
    const user = userEvent.setup();
    const { container } = render(<CookMantraApp apiClient={client} />);
    const galleryButton = screen.getByRole("button", { name: "Choose from gallery" });
    await user.click(galleryButton);
    const image = new File(["verified-image-bytes"], "ingredients.jpg", {
      type: "image/jpeg",
    });

    await user.upload(
      container.querySelector<HTMLInputElement>('input[type="file"]:not([capture])')!,
      image,
    );

    const dialog = await screen.findByRole("dialog", {
      name: "This photo will leave your device",
    });
    expect(dialog).toHaveTextContent("OpenRouter");
    expect(dialog).toHaveTextContent("openai/gpt-5.2");
    expect(
      screen.getByRole("button", { name: "Keep photo on this device" }),
    ).toHaveFocus();
    expect(URL.createObjectURL).not.toHaveBeenCalled();
    expect(createSession).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "Continue and send photo" }));

    await waitFor(() => expect(createSession).toHaveBeenCalledTimes(1));
    expect(createSession).toHaveBeenCalledWith(
      image,
      expect.objectContaining({ runtimeRevision: RUNTIME_REVISION_A }),
    );
  });

  it("declining remote media restores focus and sends no photo", async () => {
    const client = new CookMantraClient({ fetch: vi.fn<typeof fetch>() });
    vi.spyOn(client, "getRuntimeStatus").mockResolvedValue(
      runtimeStatus("codex", "gpt-5.3-codex"),
    );
    const createSession = vi.spyOn(client, "createSession");
    const user = userEvent.setup();
    const { container } = render(<CookMantraApp apiClient={client} />);
    const galleryButton = screen.getByRole("button", { name: "Choose from gallery" });
    await user.click(galleryButton);

    await user.upload(
      container.querySelector<HTMLInputElement>('input[type="file"]:not([capture])')!,
      new File(["verified-image-bytes"], "ingredients.jpg", { type: "image/jpeg" }),
    );
    await screen.findByRole("dialog", { name: "This photo will leave your device" });
    await user.click(screen.getByRole("button", { name: "Keep photo on this device" }));

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(galleryButton).toHaveFocus();
    expect(URL.createObjectURL).not.toHaveBeenCalled();
    expect(createSession).not.toHaveBeenCalled();
  });

  it("cannot submit the same retained remote file twice from repeated acceptance", async () => {
    const client = new CookMantraClient({ fetch: vi.fn<typeof fetch>() });
    vi.spyOn(client, "getRuntimeStatus").mockResolvedValue(
      runtimeStatus("openrouter", "openai/gpt-5.2"),
    );
    const createSession = vi
      .spyOn(client, "createSession")
      .mockImplementation(() => new Promise(() => {}));
    const user = userEvent.setup();
    const { container } = render(<CookMantraApp apiClient={client} />);

    await user.upload(
      container.querySelector<HTMLInputElement>('input[type="file"]:not([capture])')!,
      new File(["verified-image-bytes"], "ingredients.jpg", { type: "image/jpeg" }),
    );
    const continueButton = await screen.findByRole("button", {
      name: "Continue and send photo",
    });

    act(() => {
      fireEvent.click(continueButton);
      fireEvent.click(continueButton);
    });

    await waitFor(() => expect(createSession).toHaveBeenCalledTimes(1));
    expect(URL.createObjectURL).toHaveBeenCalledTimes(1);
  });

  it("keeps manual entry available when remote disclosure persistence fails", async () => {
    const client = new CookMantraClient({ fetch: vi.fn<typeof fetch>() });
    vi.spyOn(client, "getRuntimeStatus").mockResolvedValue(
      runtimeStatus("openrouter", "openai/gpt-5.2"),
    );
    const createSession = vi.spyOn(client, "createSession");
    const user = userEvent.setup();
    const { container } = render(<CookMantraApp apiClient={client} />);

    await user.upload(
      container.querySelector<HTMLInputElement>('input[type="file"]:not([capture])')!,
      new File(["verified-image-bytes"], "ingredients.jpg", { type: "image/jpeg" }),
    );
    await screen.findByRole("dialog", { name: "This photo will leave your device" });
    vi.spyOn(window.localStorage, "setItem").mockImplementation(() => {
      throw new DOMException("Quota exceeded", "QuotaExceededError");
    });

    await user.click(screen.getByRole("button", { name: "Continue and send photo" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Could not save your photo-sharing choice",
    );
    expect(
      within(screen.getByRole("dialog")).getByRole("button", {
        name: "Type ingredients instead",
      }),
    ).toBeEnabled();
    expect(URL.createObjectURL).not.toHaveBeenCalled();
    expect(createSession).not.toHaveBeenCalled();
  });

  it("blocks a photo when ingredient recognition is not ready but leaves manual entry usable", async () => {
    const client = new CookMantraClient({ fetch: vi.fn<typeof fetch>() });
    vi.spyOn(client, "getRuntimeStatus").mockResolvedValue(
      runtimeStatus("ollama", "qwen3.5:9b", RUNTIME_REVISION_A, false),
    );
    const createSession = vi.spyOn(client, "createSession");
    const user = userEvent.setup();
    const { container } = render(<CookMantraApp apiClient={client} />);

    await user.upload(
      container.querySelector<HTMLInputElement>('input[type="file"]:not([capture])')!,
      new File(["verified-image-bytes"], "ingredients.jpg", { type: "image/jpeg" }),
    );

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Ingredient recognition needs attention",
    );
    expect(URL.createObjectURL).not.toHaveBeenCalled();
    expect(createSession).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Type ingredients instead" }));
    expect(
      await screen.findByRole("heading", { name: "Check what we found" }),
    ).toBeInTheDocument();
  });

  it("blocks photo submission when another required cooking role is not ready", async () => {
    const requiredFailure = runtimeStatus();
    requiredFailure.status = "attention";
    requiredFailure.model_runtime.ready = false;
    requiredFailure.model_runtime.roles.recipe_writer = {
      ...requiredFailure.model_runtime.roles.recipe_writer,
      ready: false,
      available_capabilities: [],
      error: "unavailable",
    };
    const client = new CookMantraClient({ fetch: vi.fn<typeof fetch>() });
    vi.spyOn(client, "getRuntimeStatus").mockResolvedValue(requiredFailure);
    const createSession = vi.spyOn(client, "createSession");
    const user = userEvent.setup();
    const { container } = render(<CookMantraApp apiClient={client} />);

    await user.upload(
      container.querySelector<HTMLInputElement>('input[type="file"]:not([capture])')!,
      new File(["verified-image-bytes"], "ingredients.jpg", { type: "image/jpeg" }),
    );

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Cooking models need attention",
    );
    expect(URL.createObjectURL).not.toHaveBeenCalled();
    expect(createSession).not.toHaveBeenCalled();
  });

  it("rejects a ready ingredient role with a blank model identifier", async () => {
    const blankModel = runtimeStatus();
    blankModel.model_runtime.roles.ingredient_extractor = {
      ...blankModel.model_runtime.roles.ingredient_extractor,
      model: "   ",
    };
    const client = new CookMantraClient({ fetch: vi.fn<typeof fetch>() });
    vi.spyOn(client, "getRuntimeStatus").mockResolvedValue(blankModel);
    const createSession = vi.spyOn(client, "createSession");
    const user = userEvent.setup();
    const { container } = render(<CookMantraApp apiClient={client} />);

    await user.upload(
      container.querySelector<HTMLInputElement>('input[type="file"]:not([capture])')!,
      new File(["verified-image-bytes"], "ingredients.jpg", {
        type: "image/jpeg",
      }),
    );

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Ingredient recognition needs attention",
    );
    expect(createSession).not.toHaveBeenCalled();
  });

  it("allows only the latest selected file to cross an out-of-order status gate", async () => {
    const firstStatus = deferred<RuntimeStatusResponse>();
    const secondStatus = deferred<RuntimeStatusResponse>();
    const client = new CookMantraClient({ fetch: vi.fn<typeof fetch>() });
    vi.spyOn(client, "getRuntimeStatus")
      .mockResolvedValueOnce(runtimeStatus())
      .mockReturnValueOnce(firstStatus.promise)
      .mockReturnValueOnce(secondStatus.promise);
    const createSession = vi.spyOn(client, "createSession");
    const user = userEvent.setup();
    const { container } = render(<CookMantraApp apiClient={client} />);
    await screen.findByText("Ingredient recognition");
    const galleryInput = container.querySelector<HTMLInputElement>(
      'input[type="file"]:not([capture])',
    )!;
    const first = new File(["first"], "first.jpg", { type: "image/jpeg" });
    const second = new File(["second"], "second.jpg", { type: "image/jpeg" });

    await user.upload(galleryInput, first);
    await user.upload(galleryInput, second);
    await act(async () =>
      secondStatus.resolve(runtimeStatus("openrouter", "provider/new-model")),
    );
    expect(
      await screen.findByText("provider/new-model", {
        selector: ".privacy-dialog-provider",
      }),
    ).toBeInTheDocument();

    await act(async () =>
      firstStatus.resolve(runtimeStatus("openrouter", "provider/old-model")),
    );

    expect(screen.queryByText("provider/old-model")).not.toBeInTheDocument();
    expect(URL.createObjectURL).not.toHaveBeenCalled();
    expect(createSession).not.toHaveBeenCalled();
  });

  it("cancels a status-gated photo when progress navigation leaves the photo step", async () => {
    const pendingStatus = deferred<RuntimeStatusResponse>();
    const client = new CookMantraClient({ fetch: vi.fn<typeof fetch>() });
    const getRuntimeStatus = vi
      .spyOn(client, "getRuntimeStatus")
      .mockResolvedValue(runtimeStatus());
    const createSession = vi.spyOn(client, "createSession").mockResolvedValue({
      session_id: "session-api-123",
      job_id: "job-extract",
    });
    vi.spyOn(client, "pollJob").mockResolvedValue(
      job("extract_ingredients", "succeeded", 100, {
        ingredient_count: 2,
      }) as TerminalJobResponse,
    );
    vi.spyOn(client, "getSession").mockResolvedValue(extractedSession);
    const user = userEvent.setup();
    const { container } = render(<CookMantraApp apiClient={client} />);

    await user.upload(
      container.querySelector<HTMLInputElement>('input[type="file"]:not([capture])')!,
      new File(["first"], "first.jpg", { type: "image/jpeg" }),
    );
    await screen.findByRole("heading", { name: "Check what we found" });
    const progress = screen.getByRole("navigation", { name: "Progress" });
    await user.click(within(progress).getByRole("button", { name: /Photo/ }));

    getRuntimeStatus.mockReturnValueOnce(pendingStatus.promise);
    await user.upload(
      container.querySelector<HTMLInputElement>('input[type="file"]:not([capture])')!,
      new File(["second"], "second.jpg", { type: "image/jpeg" }),
    );
    await user.click(within(progress).getByRole("button", { name: /Confirm/ }));
    await act(async () => pendingStatus.resolve(runtimeStatus()));

    expect(createSession).toHaveBeenCalledTimes(1);
    expect(URL.createObjectURL).toHaveBeenCalledTimes(1);
    expect(URL.revokeObjectURL).not.toHaveBeenCalled();
    expect(screen.getByRole("heading", { name: "Check what we found" })).toBeVisible();
  });

  it("keeps an accepted upload running while saved recipes are open", async () => {
    const firstCreate = deferred<{ session_id: string; job_id: string }>();
    const client = new CookMantraClient({ fetch: vi.fn<typeof fetch>() });
    const createSession = vi
      .spyOn(client, "createSession")
      .mockReturnValue(firstCreate.promise);
    vi.spyOn(client, "pollJob").mockResolvedValue(
      job("extract_ingredients", "succeeded", 100, {
        ingredient_count: 2,
      }) as TerminalJobResponse,
    );
    const getSession = vi
      .spyOn(client, "getSession")
      .mockResolvedValue(extractedSession);
    const user = userEvent.setup();
    const { container } = render(<CookMantraApp apiClient={client} />);
    const firstInput = container.querySelector<HTMLInputElement>(
      'input[type="file"]:not([capture])',
    )!;

    await user.upload(
      firstInput,
      new File(["first"], "first.jpg", { type: "image/jpeg" }),
    );
    await waitFor(() => expect(createSession).toHaveBeenCalledTimes(1));
    const uploadSignal = createSession.mock.calls[0]?.[1]?.signal;
    await user.click(screen.getByRole("button", { name: "Saved recipes" }));
    expect(uploadSignal?.aborted).toBe(false);

    await act(async () =>
      firstCreate.resolve({ session_id: "session-old", job_id: "job-old" }),
    );
    await waitFor(() => expect(getSession).toHaveBeenCalledOnce());
    await user.click(screen.getByRole("button", { name: "Back" }));

    expect(screen.getByRole("heading", { name: "Check what we found" })).toBeVisible();
  });

  it("reports an accepted upload failure after returning from saved recipes", async () => {
    const firstCreate = deferred<{ session_id: string; job_id: string }>();
    const client = new CookMantraClient({ fetch: vi.fn<typeof fetch>() });
    const createSession = vi
      .spyOn(client, "createSession")
      .mockReturnValue(firstCreate.promise);
    const user = userEvent.setup();
    const { container } = render(<CookMantraApp apiClient={client} />);

    await user.upload(
      container.querySelector<HTMLInputElement>('input[type="file"]:not([capture])')!,
      new File(["first"], "first.jpg", { type: "image/jpeg" }),
    );
    await waitFor(() => expect(createSession).toHaveBeenCalledTimes(1));
    await user.click(screen.getByRole("button", { name: "Saved recipes" }));
    await act(async () => firstCreate.reject(new Error("accepted upload failed")));
    await user.click(screen.getByRole("button", { name: "Back" }));

    expect(screen.getByRole("alert")).toHaveTextContent("accepted upload failed");
  });

  it("aborts an explicit runtime-status refresh when the app unmounts", async () => {
    const refresh = deferred<RuntimeStatusResponse>();
    const client = new CookMantraClient({ fetch: vi.fn<typeof fetch>() });
    const getRuntimeStatus = vi
      .spyOn(client, "getRuntimeStatus")
      .mockResolvedValueOnce(runtimeStatus())
      .mockReturnValueOnce(refresh.promise);
    const user = userEvent.setup();
    const { unmount } = render(<CookMantraApp apiClient={client} />);
    await screen.findByText("Ingredient recognition");

    await user.click(screen.getByRole("button", { name: "Refresh local status" }));
    const refreshSignal = getRuntimeStatus.mock.calls[1]?.[0]?.signal;
    expect(refreshSignal?.aborted).toBe(false);

    unmount();

    expect(refreshSignal?.aborted).toBe(true);
  });

  it("aborts and revokes an uncommitted photo submission on unmount", async () => {
    const client = new CookMantraClient({ fetch: vi.fn<typeof fetch>() });
    const createSession = vi
      .spyOn(client, "createSession")
      .mockImplementation(() => new Promise(() => {}));
    const user = userEvent.setup();
    const { container, unmount } = render(<CookMantraApp apiClient={client} />);

    await user.upload(
      container.querySelector<HTMLInputElement>('input[type="file"]:not([capture])')!,
      new File(["candidate"], "candidate.jpg", { type: "image/jpeg" }),
    );
    await waitFor(() => expect(createSession).toHaveBeenCalledOnce());
    const signal = createSession.mock.calls[0]?.[1]?.signal;

    unmount();

    expect(signal?.aborted).toBe(true);
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:cook-mantra-ingredients");
  });

  it("rechecks a stale runtime revision before a same-target retry", async () => {
    const target = { provider: "openrouter", model: "openai/gpt-5.2" } as const;
    persistRemoteTarget(target);
    const client = new CookMantraClient({ fetch: vi.fn<typeof fetch>() });
    const getRuntimeStatus = vi
      .spyOn(client, "getRuntimeStatus")
      .mockResolvedValueOnce(runtimeStatus(target.provider, target.model))
      .mockResolvedValueOnce(runtimeStatus(target.provider, target.model))
      .mockResolvedValueOnce(
        runtimeStatus(target.provider, target.model, RUNTIME_REVISION_B),
      );
    const createSession = vi
      .spyOn(client, "createSession")
      .mockRejectedValueOnce(
        new ApiError({
          status: 409,
          code: "runtime_status_stale",
          message: "Refresh local model status before sending this photo.",
          details: {},
          retryable: true,
          requestId: "request-stale",
          sessionId: null,
          jobId: null,
        }),
      )
      .mockImplementationOnce(() => new Promise(() => {}));
    const user = userEvent.setup();
    const { container } = render(<CookMantraApp apiClient={client} />);

    await user.upload(
      container.querySelector<HTMLInputElement>('input[type="file"]:not([capture])')!,
      new File(["verified-image-bytes"], "ingredients.jpg", { type: "image/jpeg" }),
    );

    await waitFor(() => expect(createSession).toHaveBeenCalledTimes(2));
    expect(getRuntimeStatus).toHaveBeenCalledTimes(3);
    expect(createSession.mock.calls[0]?.[1]).toEqual(
      expect.objectContaining({ runtimeRevision: RUNTIME_REVISION_A }),
    );
    expect(createSession.mock.calls[1]?.[1]).toEqual(
      expect.objectContaining({ runtimeRevision: RUNTIME_REVISION_B }),
    );
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:cook-mantra-ingredients");
  });

  it("reopens disclosure instead of replaying when stale status selects a new model", async () => {
    const accepted = { provider: "openrouter", model: "openai/gpt-5.2" } as const;
    persistRemoteTarget(accepted);
    const client = new CookMantraClient({ fetch: vi.fn<typeof fetch>() });
    vi.spyOn(client, "getRuntimeStatus")
      .mockResolvedValueOnce(runtimeStatus(accepted.provider, accepted.model))
      .mockResolvedValueOnce(runtimeStatus(accepted.provider, accepted.model))
      .mockResolvedValueOnce(
        runtimeStatus("openrouter", "anthropic/claude-sonnet", RUNTIME_REVISION_B),
      );
    const createSession = vi.spyOn(client, "createSession").mockRejectedValueOnce(
      new ApiError({
        status: 409,
        code: "runtime_status_stale",
        message: "Refresh local model status before sending this photo.",
        details: {},
        retryable: true,
        requestId: "request-stale",
        sessionId: null,
        jobId: null,
      }),
    );
    const user = userEvent.setup();
    const { container } = render(<CookMantraApp apiClient={client} />);

    await user.upload(
      container.querySelector<HTMLInputElement>('input[type="file"]:not([capture])')!,
      new File(["verified-image-bytes"], "ingredients.jpg", { type: "image/jpeg" }),
    );

    expect(
      await screen.findByText("anthropic/claude-sonnet", {
        selector: ".privacy-dialog-provider",
      }),
    ).toBeInTheDocument();
    expect(createSession).toHaveBeenCalledTimes(1);
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:cook-mantra-ingredients");
  });

  it("uses the injected client from image upload through rendered recipe options", async () => {
    const calls: string[] = [];
    const fetchImpl = vi.fn<typeof fetch>(async () => {
      throw new Error("Unexpected network request");
    });
    const client = new CookMantraClient({ fetch: fetchImpl });
    const extractionDone = job("extract_ingredients", "succeeded", 100, {
      ingredient_count: 2,
    }) as TerminalJobResponse;
    const optionsDone = job("generate_options", "succeeded", 100, {
      option_ids: ["option-tomato-skillet"],
      batch_number: 1,
    }) as TerminalJobResponse;
    let sessionReadCount = 0;

    vi.spyOn(client, "createSession").mockImplementation(async () => {
      calls.push("create-session");
      return { session_id: "session-api-123", job_id: "job-extract" };
    });
    vi.spyOn(client, "pollJob").mockImplementation(async (jobId, options) => {
      calls.push(`poll:${jobId}`);
      if (jobId === "job-extract") {
        options?.onProgress?.(job("extract_ingredients", "running", 55, null));
        return extractionDone;
      }
      if (jobId === "job-options") {
        options?.onProgress?.(job("generate_options", "running", 60, null));
        return optionsDone;
      }
      throw new Error(`Unexpected job: ${jobId}`);
    });
    vi.spyOn(client, "getSession").mockImplementation(async () => {
      sessionReadCount += 1;
      calls.push(sessionReadCount === 1 ? "get-extraction" : "get-options");
      return sessionReadCount === 1 ? extractedSession : optionsSession;
    });
    const updateIngredients = vi
      .spyOn(client, "updateIngredients")
      .mockImplementation(async () => {
        calls.push("update-ingredients");
        return { ...extractedSession, ingredients: optionsSession.ingredients };
      });
    vi.spyOn(client, "confirmIngredients").mockImplementation(async () => {
      calls.push("confirm-ingredients");
      return {
        ...extractedSession,
        stage: "ingredients_confirmed",
        ingredients: optionsSession.ingredients,
      };
    });
    const generateOptions = vi
      .spyOn(client, "generateRecipeOptions")
      .mockImplementation(async () => {
        calls.push("generate-options");
        return { session_id: "session-api-123", job_id: "job-options" };
      });

    const user = userEvent.setup();
    const { container } = render(<CookMantraApp apiClient={client} />);
    const galleryInput = container.querySelector<HTMLInputElement>(
      'input[type="file"]:not([capture])',
    );
    const image = new File(["verified-image-bytes"], "ingredients.jpg", {
      type: "image/jpeg",
    });

    expect(galleryInput).not.toBeNull();
    await user.upload(galleryInput!, image);

    expect(
      await screen.findByRole("heading", { name: "Check what we found" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Ingredient Tomato" })).toHaveValue(
      "Tomato",
    );
    expect(screen.getByRole("checkbox", { name: "Salt pantry" })).not.toBeChecked();

    await user.click(screen.getByRole("button", { name: "Generate recipe ideas" }));

    expect(
      await screen.findByRole("heading", {
        name: "1 idea from your 1 ingredient",
      }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Tomato Skillet/ })).toBeInTheDocument();
    expect(fetchImpl).not.toHaveBeenCalled();
    expect(calls).toEqual([
      "create-session",
      "poll:job-extract",
      "get-extraction",
      "update-ingredients",
      "confirm-ingredients",
      "generate-options",
      "poll:job-options",
      "get-options",
    ]);
    expect(updateIngredients).toHaveBeenCalledWith(
      "session-api-123",
      {
        ingredients: [
          { id: "ingredient-tomato", name: "Tomato", confirmed: true },
          { id: "ingredient-salt", name: "Salt", confirmed: false },
          { id: null, name: "Pepper powder", confirmed: false },
          { id: null, name: "Oil or ghee", confirmed: false },
          { id: null, name: "Chilli powder", confirmed: false },
          { id: null, name: "Onion", confirmed: false },
          { id: null, name: "Garlic", confirmed: false },
          { id: null, name: "Ginger", confirmed: false },
        ],
      },
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
    expect(generateOptions).toHaveBeenCalledWith(
      "session-api-123",
      {
        dietary_preferences: ["vegetarian"],
        allergens: [],
        preferred_cuisines: [],
        max_total_minutes: null,
        servings: 2,
        option_count: 4,
        spice_level: "medium",
        special_instructions: "",
      },
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
  });

  it("does not offer local cancel or failure simulation for a real backend job", async () => {
    const client = new CookMantraClient({
      fetch: vi.fn<typeof fetch>(async () => {
        throw new Error("Unexpected network request");
      }),
    });

    vi.spyOn(client, "createSession").mockResolvedValue({
      session_id: "session-api-123",
      job_id: "job-extract",
    });
    vi.spyOn(client, "pollJob").mockImplementation(
      (_jobId, options) =>
        new Promise<TerminalJobResponse>((_resolve, reject) => {
          options?.signal?.addEventListener(
            "abort",
            () => reject(new DOMException("The operation was aborted.", "AbortError")),
            { once: true },
          );
        }),
    );

    const user = userEvent.setup();
    const { container } = render(<CookMantraApp apiClient={client} />);
    const galleryInput = container.querySelector<HTMLInputElement>(
      'input[type="file"]:not([capture])',
    );

    await user.upload(
      galleryInput!,
      new File(["verified-image-bytes"], "ingredients.jpg", {
        type: "image/jpeg",
      }),
    );

    expect(
      await screen.findByRole("heading", { name: "Looking at your ingredients" }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Cancel/ })).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Simulate a failure" }),
    ).not.toBeInTheDocument();
    expect(screen.getByText(/cannot be cancelled once it starts/i)).toBeInTheDocument();
  });

  it("returns a stalled API job to its prior screen with a retryable advisory", async () => {
    const client = new CookMantraClient({ fetch: vi.fn<typeof fetch>() });
    vi.spyOn(client, "createSession").mockResolvedValue({
      session_id: "session-api-123",
      job_id: "job-extract",
    });
    vi.spyOn(client, "pollJob").mockRejectedValue(new ApiTimeoutError("poll"));
    const user = userEvent.setup();
    const { container } = render(<CookMantraApp apiClient={client} />);

    await user.upload(
      container.querySelector<HTMLInputElement>('input[type="file"]:not([capture])')!,
      new File(["verified-image-bytes"], "ingredients.jpg", {
        type: "image/jpeg",
      }),
    );

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "This step stopped responding",
    );
    expect(screen.getByRole("alert")).toHaveTextContent(
      "It may still be running; try checking it again.",
    );
    expect(screen.getByRole("button", { name: "Try again" })).toBeEnabled();
  });

  it("shows normalized retryable copy when fetch cannot reach the server", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    const client = new CookMantraClient({
      fetch: vi.fn<typeof fetch>().mockRejectedValue(new TypeError("Load failed")),
    });
    const user = userEvent.setup();
    const { container } = render(<CookMantraApp apiClient={client} />);

    await user.upload(
      container.querySelector<HTMLInputElement>('input[type="file"]:not([capture])')!,
      new File(["verified-image-bytes"], "ingredients.jpg", {
        type: "image/jpeg",
      }),
    );

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Cook Mantra could not reach the server");
    expect(alert).toHaveTextContent("Check your connection and try again.");
    expect(alert).not.toHaveTextContent("Load failed");
    expect(screen.getByRole("button", { name: "Try again" })).toBeEnabled();
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:cook-mantra-ingredients");
  });

  it("shows provider-neutral copy for model runtime configuration failures", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    const client = new CookMantraClient({ fetch: vi.fn<typeof fetch>() });
    vi.spyOn(client, "createSession").mockRejectedValue(
      new ApiError({
        status: 503,
        code: "model_configuration_invalid",
        message: "The model runtime configuration is invalid.",
        details: {},
        retryable: false,
        requestId: "request-runtime",
        sessionId: null,
        jobId: null,
      }),
    );
    const user = userEvent.setup();
    const { container } = render(<CookMantraApp apiClient={client} />);

    await user.upload(
      container.querySelector<HTMLInputElement>('input[type="file"]:not([capture])')!,
      new File(["verified-image-bytes"], "ingredients.jpg", { type: "image/jpeg" }),
    );

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("The cooking agents need configuration");
    expect(alert).toHaveTextContent(
      "Check the local model runtime settings and restart Cook Mantra.",
    );
  });

  it("treats repeated-recipe failure after more-ideas retries as exhaustion", async () => {
    const client = new CookMantraClient({ fetch: vi.fn<typeof fetch>() });
    const extractionDone = job("extract_ingredients", "succeeded", 100, {
      ingredient_count: 2,
    }) as TerminalJobResponse;
    const optionsDone = job("generate_options", "succeeded", 100, {
      option_ids: ["option-tomato-skillet"],
      batch_number: 1,
    }) as TerminalJobResponse;
    const exhausted = {
      ...job("generate_more_options", "failed", 100, null),
      id: "job-more",
      error: {
        code: "recipe_duplicate",
        message: "Generated options contain a repeated recipe name.",
        details: {},
        retryable: true,
      },
    } as TerminalJobResponse;
    let sessionReadCount = 0;

    vi.spyOn(client, "createSession").mockResolvedValue({
      session_id: "session-api-123",
      job_id: "job-extract",
    });
    vi.spyOn(client, "pollJob").mockImplementation(async (jobId) => {
      if (jobId === "job-extract") return extractionDone;
      if (jobId === "job-options") return optionsDone;
      if (jobId === "job-more") return exhausted;
      throw new Error(`Unexpected job: ${jobId}`);
    });
    vi.spyOn(client, "getSession").mockImplementation(async () => {
      sessionReadCount += 1;
      return sessionReadCount === 1 ? extractedSession : optionsSession;
    });
    vi.spyOn(client, "updateIngredients").mockResolvedValue(extractedSession);
    vi.spyOn(client, "confirmIngredients").mockResolvedValue({
      ...extractedSession,
      stage: "ingredients_confirmed",
    });
    vi.spyOn(client, "generateRecipeOptions").mockResolvedValue({
      session_id: "session-api-123",
      job_id: "job-options",
    });
    vi.spyOn(client, "generateMoreRecipeOptions").mockResolvedValue({
      session_id: "session-api-123",
      job_id: "job-more",
    });
    const user = userEvent.setup();
    const { container } = render(<CookMantraApp apiClient={client} />);

    await user.upload(
      container.querySelector<HTMLInputElement>('input[type="file"]:not([capture])')!,
      new File(["verified-image-bytes"], "ingredients.jpg", {
        type: "image/jpeg",
      }),
    );
    await screen.findByRole("heading", { name: "Check what we found" });
    await user.click(screen.getByRole("button", { name: "Generate recipe ideas" }));
    await screen.findByRole("button", { name: /Tomato Skillet/ });
    await user.click(screen.getByRole("button", { name: "More ideas" }));

    expect(
      await screen.findByText(/No fresh ideas left for these ingredients/),
    ).toBeVisible();
    expect(screen.getByRole("button", { name: "No more ideas" })).toBeDisabled();
    expect(screen.queryByText("The agent stopped early")).toBeNull();
  });

  it("retains the source photo through status refresh and refreshes a stale revision while rebasing edits", async () => {
    const client = new CookMantraClient({
      fetch: vi.fn<typeof fetch>(async () => {
        throw new Error("Unexpected network request");
      }),
    });
    const image = new File(["verified-image-bytes"], "ingredients.jpg", {
      type: "image/jpeg",
    });
    const acceptedRebase = deferred<{ session_id: string; job_id: string }>();
    const createSession = vi
      .spyOn(client, "createSession")
      .mockResolvedValueOnce({
        session_id: "session-api-123",
        job_id: "job-extract",
      })
      .mockRejectedValueOnce(
        new ApiError({
          status: 409,
          code: "runtime_status_stale",
          message: "Refresh local model status before sending this photo.",
          details: {},
          retryable: true,
          requestId: "request-stale-rebase",
          sessionId: null,
          jobId: null,
        }),
      )
      .mockReturnValueOnce(acceptedRebase.promise);
    const getRuntimeStatus = vi
      .spyOn(client, "getRuntimeStatus")
      .mockResolvedValue(runtimeStatus());
    vi.spyOn(client, "pollJob").mockImplementation(async (jobId) => {
      if (jobId === "job-extract" || jobId === "job-extract-fresh") {
        return {
          ...job("extract_ingredients", "succeeded", 100, {
            ingredient_count: 2,
          }),
          id: jobId,
          session_id: jobId === "job-extract" ? "session-api-123" : "session-api-456",
        } as TerminalJobResponse;
      }
      return {
        ...job("generate_options", "succeeded", 100, {
          option_ids: [
            jobId === "job-options"
              ? "option-tomato-skillet"
              : "option-cherry-tomato-skillet",
          ],
          batch_number: 1,
        }),
        id: jobId,
        session_id: jobId === "job-options" ? "session-api-123" : "session-api-456",
      } as TerminalJobResponse;
    });
    vi.spyOn(client, "getSession")
      .mockResolvedValueOnce(extractedSession)
      .mockResolvedValueOnce(optionsSession)
      .mockResolvedValueOnce(freshExtractedSession)
      .mockResolvedValueOnce(freshOptionsSession);
    const updateIngredients = vi
      .spyOn(client, "updateIngredients")
      .mockImplementation(async (sessionId) =>
        sessionId === "session-api-123"
          ? { ...extractedSession, ingredients: optionsSession.ingredients }
          : { ...freshExtractedSession, ingredients: freshOptionsSession.ingredients },
      );
    vi.spyOn(client, "confirmIngredients").mockImplementation(async (sessionId) => ({
      ...(sessionId === "session-api-123" ? extractedSession : freshExtractedSession),
      stage: "ingredients_confirmed",
    }));
    vi.spyOn(client, "generateRecipeOptions")
      .mockResolvedValueOnce({
        session_id: "session-api-123",
        job_id: "job-options",
      })
      .mockResolvedValueOnce({
        session_id: "session-api-456",
        job_id: "job-options-fresh",
      });
    const user = userEvent.setup();

    const { container } = render(<CookMantraApp apiClient={client} />);
    await user.upload(
      container.querySelector<HTMLInputElement>('input[type="file"]:not([capture])')!,
      image,
    );
    await screen.findByRole("heading", { name: "Check what we found" });
    await user.click(screen.getByRole("button", { name: "Generate recipe ideas" }));
    await screen.findByRole("button", { name: /Tomato Skillet/ });

    const progress = screen.getByRole("navigation", { name: "Progress" });
    await user.click(within(progress).getByRole("button", { name: /Photo/ }));
    getRuntimeStatus.mockResolvedValueOnce(
      runtimeStatus("openrouter", "openai/replacement-model"),
    );
    await user.upload(
      container.querySelector<HTMLInputElement>('input[type="file"]:not([capture])')!,
      new File(["replacement"], "replacement.jpg", { type: "image/jpeg" }),
    );
    await screen.findByRole("dialog", { name: "This photo will leave your device" });
    await user.click(screen.getByRole("button", { name: "Keep photo on this device" }));
    expect(URL.revokeObjectURL).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Refresh local status" }));
    await screen.findByText("Ingredient recognition");
    await user.click(within(progress).getByRole("button", { name: /Choose/ }));

    await user.click(screen.getByRole("button", { name: /Edit ingredients/ }));
    const tomato = screen.getByRole("textbox", { name: "Ingredient Tomato" });
    await user.clear(tomato);
    await user.type(tomato, "Cherry Tomato");
    getRuntimeStatus
      .mockResolvedValueOnce(runtimeStatus())
      .mockResolvedValueOnce(
        runtimeStatus("openrouter", "openai/rebased-model", RUNTIME_REVISION_B),
      );
    await user.click(screen.getByRole("button", { name: "Generate recipe ideas" }));
    const disclosure = await screen.findByRole("dialog", {
      name: "This photo will leave your device",
    });
    expect(disclosure).toHaveTextContent("openai/rebased-model");
    expect(createSession).toHaveBeenCalledTimes(2);
    await user.click(screen.getByRole("button", { name: "Saved recipes" }));
    expect(disclosure).toBeVisible();
    expect(
      screen.queryByRole("heading", { name: "Saved recipes" }),
    ).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Continue and send photo" }));
    await waitFor(() =>
      expect(
        screen.getByRole("heading", { name: "Cooking up suggestions" }),
      ).toHaveFocus(),
    );
    await act(async () =>
      acceptedRebase.resolve({
        session_id: "session-api-456",
        job_id: "job-extract-fresh",
      }),
    );

    expect(
      await screen.findByRole("button", { name: /Cherry Tomato Skillet/ }),
    ).toBeInTheDocument();
    expect(createSession).toHaveBeenCalledTimes(3);
    expect(createSession).toHaveBeenLastCalledWith(
      image,
      expect.objectContaining({
        runtimeRevision: RUNTIME_REVISION_B,
        signal: expect.any(AbortSignal),
      }),
    );
    expect(updateIngredients).toHaveBeenNthCalledWith(
      2,
      "session-api-456",
      {
        ingredients: [
          { id: null, name: "Cherry Tomato", confirmed: true },
          { id: "fresh-ingredient-salt", name: "Salt", confirmed: false },
          { id: null, name: "Pepper powder", confirmed: false },
          { id: null, name: "Oil or ghee", confirmed: false },
          { id: null, name: "Chilli powder", confirmed: false },
          { id: null, name: "Onion", confirmed: false },
          { id: null, name: "Garlic", confirmed: false },
          { id: null, name: "Ginger", confirmed: false },
        ],
      },
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
  });

  it("keeps reviewed ingredients but invalidates prior ideas when a rebase disclosure switches to manual entry", async () => {
    const client = new CookMantraClient({ fetch: vi.fn<typeof fetch>() });
    const getRuntimeStatus = vi
      .spyOn(client, "getRuntimeStatus")
      .mockResolvedValue(runtimeStatus());
    const createSession = vi
      .spyOn(client, "createSession")
      .mockResolvedValueOnce({
        session_id: "session-api-123",
        job_id: "job-extract",
      })
      .mockRejectedValueOnce(
        new ApiError({
          status: 409,
          code: "runtime_status_stale",
          message: "Refresh local model status before sending this photo.",
          details: {},
          retryable: false,
          requestId: "request-manual-rebase",
          sessionId: null,
          jobId: null,
        }),
      );
    const manualSession: SessionResponse = {
      ...extractedSession,
      id: "session-manual-rebase",
      image_artifact_id: null,
      ingredients: [
        {
          ...extractedSession.ingredients[0],
          id: "manual-tomato",
          source: "user_added",
          confirmed: true,
        },
        extractedSession.ingredients[1],
      ],
    };
    const createManualSession = vi
      .spyOn(client, "createManualSession")
      .mockResolvedValue(manualSession);
    vi.spyOn(client, "pollJob").mockImplementation(async (jobId) =>
      jobId === "job-extract"
        ? (job("extract_ingredients", "succeeded", 100, {
            ingredient_count: 2,
          }) as TerminalJobResponse)
        : (job("generate_options", "succeeded", 100, {
            option_ids: ["option-tomato-skillet"],
            batch_number: 1,
          }) as TerminalJobResponse),
    );
    vi.spyOn(client, "getSession")
      .mockResolvedValueOnce(extractedSession)
      .mockResolvedValueOnce(optionsSession)
      .mockResolvedValueOnce({
        ...optionsSession,
        id: manualSession.id,
        image_artifact_id: null,
        ingredients: manualSession.ingredients,
      });
    vi.spyOn(client, "updateIngredients").mockResolvedValue({
      ...extractedSession,
      ingredients: optionsSession.ingredients,
    });
    vi.spyOn(client, "confirmIngredients").mockResolvedValue({
      ...extractedSession,
      stage: "ingredients_confirmed",
      ingredients: optionsSession.ingredients,
    });
    vi.spyOn(client, "generateRecipeOptions").mockResolvedValue({
      session_id: "session-api-123",
      job_id: "job-options",
    });
    const user = userEvent.setup();
    const { container } = render(<CookMantraApp apiClient={client} />);

    await user.upload(
      container.querySelector<HTMLInputElement>('input[type="file"]:not([capture])')!,
      new File(["original"], "original.jpg", { type: "image/jpeg" }),
    );
    await screen.findByRole("heading", { name: "Check what we found" });
    await user.click(screen.getByRole("button", { name: "Generate recipe ideas" }));
    await screen.findByRole("button", { name: /Tomato Skillet/ });
    await user.click(screen.getByRole("button", { name: /Edit ingredients/ }));

    getRuntimeStatus
      .mockResolvedValueOnce(runtimeStatus())
      .mockResolvedValueOnce(
        runtimeStatus("openrouter", "openai/manual-rebase", RUNTIME_REVISION_B),
      );
    await user.click(screen.getByRole("button", { name: "Generate recipe ideas" }));
    await screen.findByText("openai/manual-rebase", {
      selector: ".privacy-dialog-provider",
    });
    await user.click(screen.getByRole("button", { name: "Type ingredients instead" }));

    expect(
      await screen.findByRole("textbox", { name: "Ingredient Tomato" }),
    ).toHaveValue("Tomato");
    const choose = within(
      screen.getByRole("navigation", { name: "Progress" }),
    ).getByRole("button", { name: /Choose/ });
    expect(choose).toBeDisabled();
    expect(screen.queryByRole("button", { name: /Tomato Skillet/ })).toBeNull();
    expect(createSession).toHaveBeenCalledTimes(2);

    await user.click(screen.getByRole("button", { name: "Generate recipe ideas" }));
    await screen.findByRole("button", { name: /Tomato Skillet/ });
    expect(createManualSession).toHaveBeenCalledWith(
      { ingredients: ["Tomato"] },
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
    expect(createSession).toHaveBeenCalledTimes(2);
  });

  it("starts another fresh session when a rebased ingredient update needs retrying", async () => {
    const client = new CookMantraClient({
      fetch: vi.fn<typeof fetch>(async () => {
        throw new Error("Unexpected network request");
      }),
    });
    const retryExtractedSession: SessionResponse = {
      ...freshExtractedSession,
      id: "session-api-789",
      ingredients: [
        { ...freshExtractedSession.ingredients[0], id: "retry-ingredient-tomato" },
        { ...freshExtractedSession.ingredients[1], id: "retry-ingredient-salt" },
      ],
      updated_at: "2026-07-31T08:02:01Z",
    };
    const retryOptionsSession: SessionResponse = {
      ...freshOptionsSession,
      id: "session-api-789",
      ingredients: [
        {
          id: "retry-added-cherry-tomato",
          name: "Cherry Tomato",
          source: "user_added",
          confidence: null,
          confirmed: true,
        },
        { ...retryExtractedSession.ingredients[1], confirmed: false },
      ],
      updated_at: "2026-07-31T08:02:05Z",
    };
    const image = new File(["verified-image-bytes"], "ingredients.jpg", {
      type: "image/jpeg",
    });
    const createSession = vi
      .spyOn(client, "createSession")
      .mockResolvedValueOnce({
        session_id: "session-api-123",
        job_id: "job-extract",
      })
      .mockResolvedValueOnce({
        session_id: "session-api-456",
        job_id: "job-extract-fresh",
      })
      .mockResolvedValueOnce({
        session_id: "session-api-789",
        job_id: "job-extract-retry",
      });
    vi.spyOn(client, "pollJob").mockImplementation(async (jobId) => {
      if (jobId.startsWith("job-extract")) {
        return {
          ...job("extract_ingredients", "succeeded", 100, {
            ingredient_count: 2,
          }),
          id: jobId,
        } as TerminalJobResponse;
      }
      return {
        ...job("generate_options", "succeeded", 100, {
          option_ids: [
            jobId === "job-options"
              ? "option-tomato-skillet"
              : "option-cherry-tomato-skillet",
          ],
          batch_number: 1,
        }),
        id: jobId,
      } as TerminalJobResponse;
    });
    vi.spyOn(client, "getSession")
      .mockResolvedValueOnce(extractedSession)
      .mockResolvedValueOnce(optionsSession)
      .mockResolvedValueOnce(freshExtractedSession)
      .mockResolvedValueOnce(retryExtractedSession)
      .mockResolvedValueOnce(retryOptionsSession);
    const updateIngredients = vi
      .spyOn(client, "updateIngredients")
      .mockResolvedValueOnce({
        ...extractedSession,
        ingredients: optionsSession.ingredients,
      })
      .mockRejectedValueOnce(new Error("Fresh review interrupted"))
      .mockResolvedValueOnce({
        ...retryExtractedSession,
        ingredients: retryOptionsSession.ingredients,
      });
    vi.spyOn(client, "confirmIngredients").mockImplementation(async (sessionId) => ({
      ...(sessionId === "session-api-123" ? extractedSession : retryExtractedSession),
      stage: "ingredients_confirmed",
    }));
    const generateOptions = vi
      .spyOn(client, "generateRecipeOptions")
      .mockResolvedValueOnce({
        session_id: "session-api-123",
        job_id: "job-options",
      })
      .mockResolvedValueOnce({
        session_id: "session-api-789",
        job_id: "job-options-retry",
      });
    const user = userEvent.setup();

    const { container } = render(<CookMantraApp apiClient={client} />);
    await user.upload(
      container.querySelector<HTMLInputElement>('input[type="file"]:not([capture])')!,
      image,
    );
    await screen.findByRole("heading", { name: "Check what we found" });
    await user.click(screen.getByRole("button", { name: "Generate recipe ideas" }));
    await screen.findByRole("button", { name: /Tomato Skillet/ });
    await user.click(screen.getByRole("button", { name: /Edit ingredients/ }));
    const tomato = screen.getByRole("textbox", { name: "Ingredient Tomato" });
    await user.clear(tomato);
    await user.type(tomato, "Cherry Tomato");

    await user.click(screen.getByRole("button", { name: "Generate recipe ideas" }));
    expect(await screen.findByText("Fresh review interrupted")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Try again" }));

    expect(
      await screen.findByRole("button", { name: /Cherry Tomato Skillet/ }),
    ).toBeVisible();
    expect(createSession).toHaveBeenCalledTimes(3);
    expect(updateIngredients).toHaveBeenNthCalledWith(
      3,
      "session-api-789",
      {
        ingredients: [
          { id: null, name: "Cherry Tomato", confirmed: true },
          { id: "retry-ingredient-salt", name: "Salt", confirmed: false },
          { id: null, name: "Pepper powder", confirmed: false },
          { id: null, name: "Oil or ghee", confirmed: false },
          { id: null, name: "Chilli powder", confirmed: false },
          { id: null, name: "Onion", confirmed: false },
          { id: null, name: "Garlic", confirmed: false },
          { id: null, name: "Ginger", confirmed: false },
        ],
      },
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
    expect(generateOptions).toHaveBeenLastCalledWith(
      "session-api-789",
      expect.any(Object),
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
  });

  it("does not request more options after the backend has moved to recipes ready", async () => {
    const client = new CookMantraClient({
      fetch: vi.fn<typeof fetch>(async () => {
        throw new Error("Unexpected network request");
      }),
    });
    vi.spyOn(client, "createSession").mockResolvedValue({
      session_id: "session-api-123",
      job_id: "job-extract",
    });
    const readyStatus = runtimeStatus();
    readyStatus.model_runtime.roles.image_generator = {
      ...readyStatus.model_runtime.roles.image_generator,
      enabled: true,
      ready: true,
      available_capabilities: ["image_output"],
    };
    vi.spyOn(client, "getRuntimeStatus").mockResolvedValue(readyStatus);
    const recipeJob = deferred<TerminalJobResponse>();
    vi.spyOn(client, "pollJob").mockImplementation(async (jobId) => {
      if (jobId === "job-recipes") return recipeJob.promise;
      const operation =
        jobId === "job-extract"
          ? "extract_ingredients"
          : jobId === "job-options"
            ? "generate_options"
            : "generate_recipes";
      return {
        ...job(operation, "succeeded", 100, {}),
        id: jobId,
      } as TerminalJobResponse;
    });
    vi.spyOn(client, "getSession")
      .mockResolvedValueOnce(extractedSession)
      .mockResolvedValueOnce(optionsSession)
      .mockResolvedValueOnce(recipesSession);
    vi.spyOn(client, "updateIngredients").mockResolvedValue({
      ...extractedSession,
      ingredients: optionsSession.ingredients,
    });
    vi.spyOn(client, "confirmIngredients").mockResolvedValue({
      ...extractedSession,
      stage: "ingredients_confirmed",
      ingredients: optionsSession.ingredients,
    });
    vi.spyOn(client, "generateRecipeOptions").mockResolvedValue({
      session_id: "session-api-123",
      job_id: "job-options",
    });
    vi.spyOn(client, "generateRecipes").mockResolvedValue({
      session_id: "session-api-123",
      job_id: "job-recipes",
    });
    const moreIdeas = vi.spyOn(client, "generateMoreRecipeOptions");
    const user = userEvent.setup();

    const { container } = render(<CookMantraApp apiClient={client} />);
    await user.upload(
      container.querySelector<HTMLInputElement>('input[type="file"]:not([capture])')!,
      new File(["verified-image-bytes"], "ingredients.jpg", {
        type: "image/jpeg",
      }),
    );
    await screen.findByRole("heading", { name: "Check what we found" });
    await user.click(screen.getByRole("button", { name: "Generate recipe ideas" }));
    await user.click(await screen.findByRole("button", { name: /Tomato Skillet/ }));
    await user.click(screen.getByRole("button", { name: "Create 1 recipe" }));
    expect(
      await screen.findByText("Recipe Writer Agent — Tomato Skillet"),
    ).toBeVisible();
    expect(screen.getByText("Dish preview — Tomato Skillet")).toBeVisible();
    await act(async () =>
      recipeJob.resolve({
        ...job("generate_recipes", "succeeded", 100, {}),
        id: "job-recipes",
      } as TerminalJobResponse),
    );
    await screen.findByRole("heading", { name: "Your recipe, ready" });
    await user.click(screen.getByRole("button", { name: /Back to recipe ideas/ }));
    expect(
      screen.getByRole("button", { name: "More ideas unavailable" }),
    ).toBeDisabled();
    expect(
      screen.getByText(/edit your ingredients — Cook Mantra will re-read your photo/i),
    ).toBeVisible();
    expect(moreIdeas).not.toHaveBeenCalled();
  });

  it("does not advertise ready API dish previews during a demo recipe job", async () => {
    const client = new CookMantraClient({ fetch: vi.fn<typeof fetch>() });
    const readyStatus = runtimeStatus();
    readyStatus.model_runtime.roles.image_generator = {
      ...readyStatus.model_runtime.roles.image_generator,
      enabled: true,
      ready: true,
      available_capabilities: ["image_output"],
    };
    vi.spyOn(client, "getRuntimeStatus").mockResolvedValue(readyStatus);
    const user = userEvent.setup();
    const view = render(
      <CookMantraApp apiClient={client} devControls demoJobDurationMs={0} />,
    );
    expect(await screen.findAllByText("Ready")).toHaveLength(4);

    await user.click(screen.getByRole("button", { name: "Simulate weak detection" }));
    await screen.findByRole("heading", { name: "Check what we found" });
    await user.click(screen.getByRole("button", { name: "Generate recipe ideas" }));
    await screen.findByRole("heading", { name: /ideas from your 1 ingredient/ });
    await user.click(screen.getByRole("button", { name: /Tomato Rasam/ }));

    view.rerender(
      <CookMantraApp apiClient={client} devControls demoJobDurationMs={60_000} />,
    );
    await user.click(screen.getByRole("button", { name: "Create 1 recipe" }));

    expect(await screen.findByText("Recipe Writer Agent — Tomato Rasam")).toBeVisible();
    expect(screen.queryByText(/Dish preview — Tomato Rasam/)).toBeNull();
  });

  it("retries only failed recipes and merges a later success", async () => {
    const client = new CookMantraClient({ fetch: vi.fn<typeof fetch>() });
    const secondOption = {
      ...optionsSession.recipe_options[0],
      id: "option-tomato-rasam",
      name: "Tomato Rasam",
    };
    const optionsWithTwo: SessionResponse = {
      ...optionsSession,
      recipe_options: [...optionsSession.recipe_options, secondOption],
    };
    const partialRecipes: SessionResponse = {
      ...recipesSession,
      recipe_failures: {
        "option-tomato-rasam": {
          option_id: "option-tomato-rasam",
          code: "model_output_invalid",
          message: "  The recipe output was invalid.\n",
          retryable: true,
        },
      },
    };
    const recoveredRecipes: SessionResponse = {
      ...partialRecipes,
      complete_recipes: {
        ...partialRecipes.complete_recipes,
        "option-tomato-rasam": {
          ...recipesSession.complete_recipes["option-tomato-skillet"],
          option_id: "option-tomato-rasam",
          name: "Tomato Rasam",
        },
      },
      recipe_failures: {},
    };
    let sessionReadCount = 0;

    vi.spyOn(client, "createSession").mockResolvedValue({
      session_id: "session-api-123",
      job_id: "job-extract",
    });
    vi.spyOn(client, "pollJob").mockImplementation(async (jobId) => {
      const operation =
        jobId === "job-extract"
          ? "extract_ingredients"
          : jobId === "job-options"
            ? "generate_options"
            : "generate_recipes";
      return {
        ...job(operation, "succeeded", 100, {}),
        id: jobId,
      } as TerminalJobResponse;
    });
    vi.spyOn(client, "getSession").mockImplementation(async () => {
      sessionReadCount += 1;
      if (sessionReadCount === 1) return extractedSession;
      if (sessionReadCount === 2) return optionsWithTwo;
      if (sessionReadCount === 3) return partialRecipes;
      return recoveredRecipes;
    });
    vi.spyOn(client, "updateIngredients").mockResolvedValue({
      ...extractedSession,
      ingredients: optionsSession.ingredients,
    });
    vi.spyOn(client, "confirmIngredients").mockResolvedValue({
      ...extractedSession,
      stage: "ingredients_confirmed",
      ingredients: optionsSession.ingredients,
    });
    vi.spyOn(client, "generateRecipeOptions").mockResolvedValue({
      session_id: "session-api-123",
      job_id: "job-options",
    });
    const generateRecipes = vi
      .spyOn(client, "generateRecipes")
      .mockResolvedValueOnce({
        session_id: "session-api-123",
        job_id: "job-recipes",
      })
      .mockResolvedValueOnce({
        session_id: "session-api-123",
        job_id: "job-recipes-retry",
      });
    const user = userEvent.setup();
    const { container } = render(<CookMantraApp apiClient={client} />);

    await user.upload(
      container.querySelector<HTMLInputElement>('input[type="file"]:not([capture])')!,
      new File(["verified-image-bytes"], "ingredients.jpg", {
        type: "image/jpeg",
      }),
    );
    await screen.findByRole("heading", { name: "Check what we found" });
    await user.click(screen.getByRole("button", { name: "Generate recipe ideas" }));
    await user.click(await screen.findByRole("button", { name: /Tomato Skillet/ }));
    await user.click(screen.getByRole("button", { name: /Tomato Rasam/ }));
    await user.click(screen.getByRole("button", { name: "Create 2 recipes" }));

    expect(await screen.findByText("Tomato Rasam:")).toBeVisible();
    expect(screen.getByText("The recipe output was invalid.")).toBeVisible();
    const completedStep = screen.getByRole("button", {
      name: /Cook the tomatoes until soft/,
    });
    await user.click(completedStep);
    await user.click(screen.getByRole("button", { name: "Retry failed recipes" }));

    expect(
      await screen.findByRole("heading", { name: "Your 2 recipes, ready" }),
    ).toBeVisible();
    expect(screen.queryByRole("button", { name: "Retry failed recipes" })).toBeNull();
    expect(
      screen.getByRole("button", { name: /Cook the tomatoes until soft/ }),
    ).toHaveAttribute("aria-pressed", "true");
    expect(generateRecipes).toHaveBeenNthCalledWith(
      2,
      "session-api-123",
      { option_ids: ["option-tomato-rasam"] },
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
  });

  it("resumes the accepted recipe job instead of submitting it twice", async () => {
    const client = new CookMantraClient({
      fetch: vi.fn<typeof fetch>(async () => {
        throw new Error("Unexpected network request");
      }),
    });
    let sessionReadCount = 0;
    let recipePollCount = 0;

    vi.spyOn(client, "createSession").mockResolvedValue({
      session_id: "session-api-123",
      job_id: "job-extract",
    });
    const pollJob = vi.spyOn(client, "pollJob").mockImplementation(async (jobId) => {
      if (jobId === "job-extract") {
        return job("extract_ingredients", "succeeded", 100, {
          ingredient_count: 2,
        }) as TerminalJobResponse;
      }
      if (jobId === "job-options") {
        return job("generate_options", "succeeded", 100, {
          option_ids: ["option-tomato-skillet"],
          batch_number: 1,
        }) as TerminalJobResponse;
      }
      if (jobId === "job-recipes") {
        recipePollCount += 1;
        if (recipePollCount === 1) throw new Error("Recipe polling interrupted");
        return {
          ...job("generate_recipes", "succeeded", 100, {
            selected_option_ids: ["option-tomato-skillet"],
          }),
          id: "job-recipes",
        } as TerminalJobResponse;
      }
      throw new Error(`Unexpected job: ${jobId}`);
    });
    vi.spyOn(client, "getSession").mockImplementation(async () => {
      sessionReadCount += 1;
      if (sessionReadCount === 1) return extractedSession;
      if (sessionReadCount === 2) return optionsSession;
      return recipesSession;
    });
    vi.spyOn(client, "updateIngredients").mockResolvedValue({
      ...extractedSession,
      ingredients: optionsSession.ingredients,
    });
    vi.spyOn(client, "confirmIngredients").mockResolvedValue({
      ...extractedSession,
      stage: "ingredients_confirmed",
      ingredients: optionsSession.ingredients,
    });
    vi.spyOn(client, "generateRecipeOptions").mockResolvedValue({
      session_id: "session-api-123",
      job_id: "job-options",
    });
    const generateRecipes = vi.spyOn(client, "generateRecipes").mockResolvedValue({
      session_id: "session-api-123",
      job_id: "job-recipes",
    });
    const user = userEvent.setup();

    const { container } = render(<CookMantraApp apiClient={client} />);
    await user.upload(
      container.querySelector<HTMLInputElement>('input[type="file"]:not([capture])')!,
      new File(["verified-image-bytes"], "ingredients.jpg", {
        type: "image/jpeg",
      }),
    );
    await screen.findByRole("heading", { name: "Check what we found" });
    await user.click(screen.getByRole("button", { name: "Generate recipe ideas" }));
    await user.click(await screen.findByRole("button", { name: /Tomato Skillet/ }));
    await user.click(screen.getByRole("button", { name: "Create 1 recipe" }));

    expect(await screen.findByText("Recipe polling interrupted")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Try again" }));

    expect(
      await screen.findByRole("heading", { name: "Your recipe, ready" }),
    ).toBeVisible();
    expect(generateRecipes).toHaveBeenCalledTimes(1);
    expect(
      pollJob.mock.calls.filter(([jobId]) => jobId === "job-recipes"),
    ).toHaveLength(2);
  });

  it("restores a rejected more-ideas queue and resumes an accepted job after polling drops", async () => {
    const client = new CookMantraClient({
      fetch: vi.fn<typeof fetch>(async () => {
        throw new Error("Unexpected network request");
      }),
    });
    const extractionDone = job("extract_ingredients", "succeeded", 100, {
      ingredient_count: 2,
    }) as TerminalJobResponse;
    const optionsDone = job("generate_options", "succeeded", 100, {
      option_ids: ["option-tomato-skillet"],
      batch_number: 1,
    }) as TerminalJobResponse;
    const moreDone = {
      ...job("generate_more_options", "succeeded", 100, {
        option_ids: [],
        batch_number: 2,
      }),
      id: "job-more",
    } as TerminalJobResponse;
    let sessionReadCount = 0;
    let morePollCount = 0;

    vi.spyOn(client, "createSession").mockResolvedValue({
      session_id: "session-api-123",
      job_id: "job-extract",
    });
    const pollJob = vi.spyOn(client, "pollJob").mockImplementation(async (jobId) => {
      if (jobId === "job-extract") return extractionDone;
      if (jobId === "job-options") return optionsDone;
      if (jobId === "job-more") {
        morePollCount += 1;
        if (morePollCount === 1) throw new Error("Polling interrupted");
        return moreDone;
      }
      throw new Error(`Unexpected job: ${jobId}`);
    });
    vi.spyOn(client, "getSession").mockImplementation(async () => {
      sessionReadCount += 1;
      return sessionReadCount === 1 ? extractedSession : optionsSession;
    });
    vi.spyOn(client, "updateIngredients").mockResolvedValue({
      ...extractedSession,
      ingredients: optionsSession.ingredients,
    });
    vi.spyOn(client, "confirmIngredients").mockResolvedValue({
      ...extractedSession,
      stage: "ingredients_confirmed",
      ingredients: optionsSession.ingredients,
    });
    vi.spyOn(client, "generateRecipeOptions").mockResolvedValue({
      session_id: "session-api-123",
      job_id: "job-options",
    });
    const generateMore = vi
      .spyOn(client, "generateMoreRecipeOptions")
      .mockRejectedValueOnce(new Error("Queue temporarily unavailable"))
      .mockResolvedValueOnce({
        session_id: "session-api-123",
        job_id: "job-more",
      });
    const user = userEvent.setup();

    const { container } = render(<CookMantraApp apiClient={client} />);
    await user.upload(
      container.querySelector<HTMLInputElement>('input[type="file"]:not([capture])')!,
      new File(["verified-image-bytes"], "ingredients.jpg", {
        type: "image/jpeg",
      }),
    );
    await screen.findByRole("heading", { name: "Check what we found" });
    await user.click(screen.getByRole("button", { name: "Generate recipe ideas" }));
    await screen.findByRole("button", { name: /Tomato Skillet/ });

    await user.click(screen.getByRole("button", { name: "More ideas" }));
    expect(await screen.findByText("Queue temporarily unavailable")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("Polling interrupted")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Try again" }));

    expect(
      await screen.findByText(/No fresh ideas left for these ingredients/),
    ).toBeVisible();
    expect(generateMore).toHaveBeenCalledTimes(2);
    expect(pollJob.mock.calls.filter(([jobId]) => jobId === "job-more")).toHaveLength(
      2,
    );
  });
  it("sends typed ingredients to the manual session endpoint and renders ideas", async () => {
    const calls: string[] = [];
    const client = new CookMantraClient({
      fetch: vi.fn<typeof fetch>(async () => {
        throw new Error("Unexpected network request");
      }),
    });
    const manualSession: SessionResponse = {
      ...extractedSession,
      id: "session-manual-1",
      image_artifact_id: null,
      ingredients: [
        {
          id: "manual-paneer",
          name: "Paneer",
          source: "user_added",
          confidence: null,
          confirmed: true,
        },
        ...MANUAL_PANTRY_NAMES.map((name) => ({
          id: `manual-${name}`,
          name,
          source: "pantry_suggestion" as const,
          confidence: null,
          confirmed: false,
        })),
      ],
    };
    const createManualSession = vi
      .spyOn(client, "createManualSession")
      .mockImplementation(async () => {
        calls.push("create-manual-session");
        return manualSession;
      });
    const updateIngredients = vi
      .spyOn(client, "updateIngredients")
      .mockImplementation(async () => {
        calls.push("update-ingredients");
        return manualSession;
      });
    vi.spyOn(client, "confirmIngredients").mockImplementation(async () => {
      calls.push("confirm-ingredients");
      return { ...manualSession, stage: "ingredients_confirmed" };
    });
    vi.spyOn(client, "generateRecipeOptions").mockImplementation(async () => {
      calls.push("generate-options");
      return { session_id: "session-manual-1", job_id: "job-options" };
    });
    vi.spyOn(client, "pollJob").mockImplementation(async () => {
      calls.push("poll:job-options");
      return job("generate_options", "succeeded", 100, {
        option_ids: ["option-paneer"],
        batch_number: 1,
      }) as TerminalJobResponse;
    });
    vi.spyOn(client, "getSession").mockImplementation(async () => {
      calls.push("get-options");
      return {
        ...manualSession,
        stage: "options_ready",
        option_batch_number: 1,
        recipe_options: [
          {
            ...optionsSession.recipe_options[0],
            id: "option-paneer",
            name: "Paneer Fry",
            used_ingredients: ["Paneer"],
          },
        ],
      };
    });

    const user = userEvent.setup();
    render(<CookMantraApp apiClient={client} />);

    await user.click(screen.getByRole("button", { name: /Type ingredients instead/ }));
    await user.type(
      screen.getByRole("textbox", { name: "Add an ingredient" }),
      "Paneer",
    );
    await user.click(screen.getByRole("button", { name: "Add ingredient" }));
    await user.click(screen.getByRole("button", { name: "Generate recipe ideas" }));

    expect(
      await screen.findByRole("heading", { name: /1 idea from your 1 ingredient/ }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Paneer Fry/ })).toBeInTheDocument();
    expect(createManualSession).toHaveBeenCalledWith(
      { ingredients: ["Paneer"] },
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
    expect(updateIngredients).toHaveBeenCalledWith(
      "session-manual-1",
      {
        ingredients: [
          ...MANUAL_PANTRY_NAMES.map((name) => ({
            id: `manual-${name}`,
            name,
            confirmed: false,
          })),
          { id: "manual-paneer", name: "Paneer", confirmed: true },
        ],
      },
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
    expect(calls).toEqual([
      "create-manual-session",
      "update-ingredients",
      "confirm-ingredients",
      "generate-options",
      "poll:job-options",
      "get-options",
    ]);
  });
});

function restoreUrlProperty(
  property: "createObjectURL" | "revokeObjectURL",
  descriptor: PropertyDescriptor | undefined,
): void {
  if (descriptor) {
    Object.defineProperty(URL, property, descriptor);
  } else {
    Reflect.deleteProperty(URL, property);
  }
}
