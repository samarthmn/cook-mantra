import { afterEach, describe, expect, it, vi } from "vitest";

import type {
  IngredientReviewRequest,
  JobResponse,
  RecipePreferences,
  RecipeSelectionRequest,
  SessionResponse,
} from "@/types/api";
import {
  ApiError,
  ApiTimeoutError,
  CookMantraClient,
  DEFAULT_API_BASE_URL,
} from "./cook-mantra-client";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function sessionResponse(): SessionResponse {
  return {
    id: "session-123",
    stage: "reviewing_ingredients",
    image_artifact_id: "upload-123",
    ingredients: [
      {
        id: "ingredient-123",
        name: "Tomato",
        source: "detected",
        confidence: 0.94,
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
}

function jobResponse(status: JobResponse["status"]): JobResponse {
  return {
    id: "job-123",
    operation: "extract_ingredients",
    session_id: "session-123",
    status,
    progress: status === "queued" ? 0 : status === "running" ? 50 : 100,
    result: status === "succeeded" ? { ingredient_count: 1 } : null,
    warnings: [],
    error:
      status === "failed"
        ? {
            code: "model_output_invalid",
            message: "Ingredient extraction failed.",
            details: {},
            retryable: false,
          }
        : null,
    created_at: "2026-07-31T08:00:00Z",
    updated_at: "2026-07-31T08:00:01Z",
  };
}

function requestBody(init: RequestInit | undefined): unknown {
  return JSON.parse(String(init?.body));
}

describe("CookMantraClient", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("uses the loopback API by default and normalizes a configured HTTP base URL", () => {
    const fetchImpl = vi.fn<typeof fetch>();

    expect(new CookMantraClient({ fetch: fetchImpl }).baseUrl).toBe(
      DEFAULT_API_BASE_URL,
    );
    expect(
      new CookMantraClient({
        baseUrl: "https://api.example.test/api/v1/",
        fetch: fetchImpl,
      }).baseUrl,
    ).toBe("https://api.example.test/api/v1");
  });

  it("rejects a configured base URL with a non-HTTP protocol", () => {
    expect(
      () =>
        new CookMantraClient({
          baseUrl: "javascript:alert(1)",
          fetch: vi.fn<typeof fetch>(),
        }),
    ).toThrow(/HTTP/i);
  });

  it("creates a session by uploading the image multipart field", async () => {
    const fetchImpl = vi
      .fn<typeof fetch>()
      .mockResolvedValue(
        jsonResponse({ session_id: "session-123", job_id: "job-123" }, 202),
      );
    const client = new CookMantraClient({ fetch: fetchImpl });
    const image = new File(["image-bytes"], "ingredients.jpg", {
      type: "image/jpeg",
    });
    const controller = new AbortController();

    await expect(
      client.createSession(image, { signal: controller.signal }),
    ).resolves.toEqual({ session_id: "session-123", job_id: "job-123" });

    const [url, init] = fetchImpl.mock.calls[0] ?? [];
    expect(url).toBe(`${DEFAULT_API_BASE_URL}/sessions`);
    expect(init?.method).toBe("POST");
    expect(init?.signal).toBeInstanceOf(AbortSignal);
    expect(init?.signal).not.toBe(controller.signal);
    controller.abort();
    expect(init?.signal?.aborted).toBe(true);
    expect(init?.body).toBeInstanceOf(FormData);
    expect((init?.body as FormData).get("image")).toBe(image);
    expect(new Headers(init?.headers).has("Content-Type")).toBe(false);
  });

  it("gets a session and a background job by encoded identifier", async () => {
    const session = sessionResponse();
    const job = jobResponse("running");
    const fetchImpl = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse(session))
      .mockResolvedValueOnce(jsonResponse(job));
    const client = new CookMantraClient({ fetch: fetchImpl });

    await expect(client.getSession("session/123")).resolves.toEqual(session);
    await expect(client.getJob("job/123")).resolves.toEqual(job);

    expect(fetchImpl.mock.calls.map(([url]) => url)).toEqual([
      `${DEFAULT_API_BASE_URL}/sessions/session%2F123`,
      `${DEFAULT_API_BASE_URL}/jobs/job%2F123`,
    ]);
  });

  it("creates a manual session from typed ingredient names", async () => {
    const session = sessionResponse();
    const fetchImpl = vi
      .fn<typeof fetch>()
      .mockResolvedValue(jsonResponse(session, 201));
    const client = new CookMantraClient({ fetch: fetchImpl });

    await expect(
      client.createManualSession({ ingredients: ["Paneer", "Potato"] }),
    ).resolves.toEqual(session);

    const [url, init] = fetchImpl.mock.calls[0] ?? [];
    expect(url).toBe(`${DEFAULT_API_BASE_URL}/sessions/manual`);
    expect(init?.method).toBe("POST");
    expect(requestBody(init)).toEqual({ ingredients: ["Paneer", "Potato"] });
    expect(new Headers(init?.headers).get("Content-Type")).toBe("application/json");
  });

  it("updates and confirms the ingredient review with the contract request bodies", async () => {
    const session = sessionResponse();
    const fetchImpl = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse(session))
      .mockResolvedValueOnce(
        jsonResponse({ ...session, stage: "ingredients_confirmed" }),
      );
    const client = new CookMantraClient({ fetch: fetchImpl });
    const review: IngredientReviewRequest = {
      ingredients: [
        { id: "ingredient-123", name: "Cherry tomato", confirmed: true },
        { id: null, name: "Fresh basil", confirmed: true },
      ],
    };

    await client.updateIngredients("session-123", review);
    await client.confirmIngredients("session-123");

    const [, updateInit] = fetchImpl.mock.calls[0] ?? [];
    const [confirmUrl, confirmInit] = fetchImpl.mock.calls[1] ?? [];
    expect(updateInit?.method).toBe("PUT");
    expect(requestBody(updateInit)).toEqual(review);
    expect(new Headers(updateInit?.headers).get("Content-Type")).toBe(
      "application/json",
    );
    expect(confirmUrl).toBe(
      `${DEFAULT_API_BASE_URL}/sessions/session-123/ingredients/confirm`,
    );
    expect(confirmInit?.method).toBe("POST");
    expect(confirmInit?.body).toBeUndefined();
  });

  it("queues initial and more recipe options with the supplied preferences", async () => {
    const queued = { session_id: "session-123", job_id: "job-123" };
    const fetchImpl = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse(queued, 202))
      .mockResolvedValueOnce(jsonResponse(queued, 202));
    const client = new CookMantraClient({ fetch: fetchImpl });
    const preferences: RecipePreferences = {
      dietary_preferences: ["vegetarian"],
      allergens: ["peanut"],
      preferred_cuisines: ["Indian"],
      max_total_minutes: 45,
      servings: 2,
      option_count: 4,
    };

    await client.generateRecipeOptions("session-123", preferences);
    await client.generateMoreRecipeOptions("session-123", preferences);

    expect(fetchImpl.mock.calls.map(([url]) => url)).toEqual([
      `${DEFAULT_API_BASE_URL}/sessions/session-123/recipe-options`,
      `${DEFAULT_API_BASE_URL}/sessions/session-123/recipe-options/more`,
    ]);
    expect(fetchImpl.mock.calls.map(([, init]) => requestBody(init))).toEqual([
      preferences,
      preferences,
    ]);
  });

  it("queues complete recipes for the explicit option selection", async () => {
    const fetchImpl = vi
      .fn<typeof fetch>()
      .mockResolvedValue(
        jsonResponse({ session_id: "session-123", job_id: "job-456" }, 202),
      );
    const client = new CookMantraClient({ fetch: fetchImpl });
    const selection: RecipeSelectionRequest = {
      option_ids: ["option-123", "option-456"],
    };

    await client.generateRecipes("session-123", selection);

    const [url, init] = fetchImpl.mock.calls[0] ?? [];
    expect(url).toBe(`${DEFAULT_API_BASE_URL}/sessions/session-123/recipes`);
    expect(init?.method).toBe("POST");
    expect(requestBody(init)).toEqual(selection);
  });

  it("builds an encoded generated-preview artifact URL", () => {
    const client = new CookMantraClient({
      baseUrl: "https://api.example.test/api/v1/",
      fetch: vi.fn<typeof fetch>(),
    });

    expect(client.artifactUrl("preview/id 123")).toBe(
      "https://api.example.test/api/v1/artifacts/preview%2Fid%20123",
    );
  });

  it("normalizes the stable error envelope into ApiError", async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(
      jsonResponse(
        {
          error: {
            code: "invalid_session_transition",
            message: "Confirm ingredients before generating recipes.",
            details: { stage: "reviewing_ingredients" },
            retryable: false,
            request_id: "request-123",
            session_id: "session-123",
            job_id: null,
          },
        },
        409,
      ),
    );
    const client = new CookMantraClient({ fetch: fetchImpl });

    const error = await client.getSession("session-123").catch((value) => value);

    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({
      name: "ApiError",
      message: "Confirm ingredients before generating recipes.",
      status: 409,
      code: "invalid_session_transition",
      details: { stage: "reviewing_ingredients" },
      retryable: false,
      requestId: "request-123",
      sessionId: "session-123",
      jobId: null,
    });
  });

  it("normalizes a malformed non-success response without exposing response text", async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(
      new Response("upstream stack trace", {
        status: 502,
        headers: { "Content-Type": "text/plain" },
      }),
    );
    const client = new CookMantraClient({ fetch: fetchImpl });

    const error = await client.getJob("job-123").catch((value) => value);

    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({
      message: "The API request failed.",
      status: 502,
      code: "internal_error",
      details: {},
      retryable: true,
      requestId: null,
      sessionId: null,
      jobId: null,
    });
  });

  it("polls at a bounded interval until a job succeeds", async () => {
    vi.useFakeTimers();
    const fetchImpl = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse(jobResponse("queued")))
      .mockResolvedValueOnce(jsonResponse(jobResponse("running")))
      .mockResolvedValueOnce(jsonResponse(jobResponse("succeeded")));
    const client = new CookMantraClient({ fetch: fetchImpl });

    const terminalJob = client.pollJob("job-123", { intervalMs: 250 });
    await vi.runAllTimersAsync();

    await expect(terminalJob).resolves.toMatchObject({ status: "succeeded" });
    expect(fetchImpl).toHaveBeenCalledTimes(3);
  });

  it("reports each observed job state while polling", async () => {
    vi.useFakeTimers();
    const queued = jobResponse("queued");
    const running = jobResponse("running");
    const succeeded = jobResponse("succeeded");
    const fetchImpl = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse(queued))
      .mockResolvedValueOnce(jsonResponse(running))
      .mockResolvedValueOnce(jsonResponse(succeeded));
    const observed: JobResponse[] = [];
    const client = new CookMantraClient({ fetch: fetchImpl });

    const terminalJob = client.pollJob("job-123", {
      intervalMs: 250,
      onProgress: (job) => observed.push(job),
    });
    await vi.runAllTimersAsync();
    await terminalJob;

    expect(observed).toEqual([queued, running, succeeded]);
  });

  it("backs off polling intervals up to the configured maximum", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(0);
    const requestTimes: number[] = [];
    const statuses: JobResponse["status"][] = [
      "queued",
      "running",
      "running",
      "succeeded",
    ];
    const fetchImpl = vi.fn<typeof fetch>(async () => {
      requestTimes.push(Date.now());
      return jsonResponse(jobResponse(statuses.shift() ?? "succeeded"));
    });
    const client = new CookMantraClient({ fetch: fetchImpl });

    const terminalJob = client.pollJob("job-123", {
      intervalMs: 250,
      maxIntervalMs: 1_000,
    });
    await vi.runAllTimersAsync();

    await expect(terminalJob).resolves.toMatchObject({ status: "succeeded" });
    expect(requestTimes).toEqual([0, 250, 750, 1_750]);
  });

  it("stops polling at the wall-clock deadline with a typed timeout", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(0);
    const fetchImpl = vi.fn<typeof fetch>(async () =>
      jsonResponse(jobResponse("running")),
    );
    const client = new CookMantraClient({ fetch: fetchImpl });

    const terminalJob = client.pollJob("job-123", {
      intervalMs: 250,
      maxIntervalMs: 500,
      deadlineMs: 750,
    });
    const rejection = expect(terminalJob).rejects.toMatchObject({
      name: "ApiTimeoutError",
      kind: "poll",
      retryable: true,
    });
    await vi.runAllTimersAsync();

    await rejection;
    expect(fetchImpl).toHaveBeenCalledTimes(2);
  });

  it("stops polling when a job fails", async () => {
    const failed = jobResponse("failed");
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(failed));
    const client = new CookMantraClient({ fetch: fetchImpl });

    await expect(client.pollJob("job-123")).resolves.toEqual(failed);
    expect(fetchImpl).toHaveBeenCalledTimes(1);
  });

  it("rejects polling intervals outside the safe bounds before requesting", async () => {
    const fetchImpl = vi.fn<typeof fetch>();
    const client = new CookMantraClient({ fetch: fetchImpl });

    await expect(client.pollJob("job-123", { intervalMs: 249 })).rejects.toBeInstanceOf(
      RangeError,
    );
    await expect(
      client.pollJob("job-123", { intervalMs: 30_001 }),
    ).rejects.toBeInstanceOf(RangeError);
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it("stops polling when the caller aborts between requests", async () => {
    const controller = new AbortController();
    const fetchImpl = vi.fn<typeof fetch>().mockImplementation(async () => {
      controller.abort(new DOMException("Cancelled", "AbortError"));
      return jsonResponse(jobResponse("queued"));
    });
    const client = new CookMantraClient({ fetch: fetchImpl });

    await expect(
      client.pollJob("job-123", {
        intervalMs: 250,
        signal: controller.signal,
      }),
    ).rejects.toMatchObject({ name: "AbortError" });
    expect(fetchImpl).toHaveBeenCalledTimes(1);
  });

  it("aborts a request that does not respond and reports a typed timeout", async () => {
    const fetchImpl = vi.fn<typeof fetch>(
      (_url, init) =>
        new Promise((_resolve, reject) => {
          init?.signal?.addEventListener("abort", () => reject(init.signal?.reason), {
            once: true,
          });
        }),
    );
    const client = new CookMantraClient({ fetch: fetchImpl, requestTimeoutMs: 10 });

    const error = await client.getJob("job-123").catch((caught) => caught);

    expect(error).toBeInstanceOf(ApiTimeoutError);
    expect(error).toMatchObject({
      kind: "request",
      retryable: true,
    });
  });
});
