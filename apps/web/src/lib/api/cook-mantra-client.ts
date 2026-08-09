import type {
  ApiErrorCode,
  ApiErrorEnvelope,
  IngredientReviewRequest,
  JobResponse,
  ManualSessionRequest,
  QueuedJobResponse,
  RecipePreferences,
  RecipeSelectionRequest,
  RuntimeStatusResponse,
  SessionResponse,
  TerminalJobResponse,
} from "@/types/api";

export const DEFAULT_API_BASE_URL = "http://127.0.0.1:8000/api/v1";

const DEFAULT_POLL_INTERVAL_MS = 1_000;
const DEFAULT_MAX_POLL_INTERVAL_MS = 5_000;
const DEFAULT_POLL_DEADLINE_MS = 15 * 60 * 1_000;
const DEFAULT_REQUEST_TIMEOUT_MS = 30_000;
const MIN_POLL_INTERVAL_MS = 250;
const MAX_POLL_INTERVAL_MS = 30_000;

const API_ERROR_CODES = new Set<ApiErrorCode>([
  "invalid_request",
  "resource_not_found",
  "invalid_session_transition",
  "ingredients_not_confirmed",
  "recipe_duplicate",
  "runtime_status_stale",
  "model_configuration_invalid",
  "provider_unavailable",
  "provider_authentication_failed",
  "provider_rate_limited",
  "provider_payment_required",
  "model_capability_missing",
  "provider_protocol_error",
  "ollama_unavailable",
  "image_provider_unavailable",
  "nutrition_provider_unavailable",
  "model_not_found",
  "model_output_invalid",
  "operation_timed_out",
  "service_busy",
  "artifact_failure",
  "internal_error",
]);

export interface CookMantraClientOptions {
  baseUrl?: string;
  fetch?: typeof fetch;
  requestTimeoutMs?: number;
}

export interface ApiRequestOptions {
  signal?: AbortSignal;
}

export interface CreateSessionOptions extends ApiRequestOptions {
  runtimeRevision: string;
}

export interface PollJobOptions extends ApiRequestOptions {
  deadlineMs?: number;
  intervalMs?: number;
  maxIntervalMs?: number;
  onProgress?: (job: JobResponse) => void;
}

interface ApiErrorOptions {
  status: number;
  code: ApiErrorCode;
  message: string;
  details: Record<string, unknown>;
  retryable: boolean;
  requestId: string | null;
  sessionId: string | null;
  jobId: string | null;
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: ApiErrorCode;
  readonly details: Record<string, unknown>;
  readonly retryable: boolean;
  readonly requestId: string | null;
  readonly sessionId: string | null;
  readonly jobId: string | null;

  constructor(options: ApiErrorOptions) {
    super(options.message);
    this.name = "ApiError";
    this.status = options.status;
    this.code = options.code;
    this.details = options.details;
    this.retryable = options.retryable;
    this.requestId = options.requestId;
    this.sessionId = options.sessionId;
    this.jobId = options.jobId;
  }
}

export class ApiTimeoutError extends Error {
  readonly kind: "request" | "poll";
  readonly retryable = true;

  constructor(kind: "request" | "poll") {
    super(
      kind === "request"
        ? "The server did not respond in time. Your work is still here; try this step again."
        : "The server job stopped reporting a result. It may still be running; try checking it again.",
    );
    this.name = "ApiTimeoutError";
    this.kind = kind;
  }
}

export class ApiNetworkError extends Error {
  readonly retryable = true;
  readonly url: string | null;

  constructor(url: string | null = null) {
    super("Check your connection and try again.");
    this.name = "ApiNetworkError";
    this.url = url;
  }
}

export class CookMantraClient {
  readonly baseUrl: string;
  private readonly fetch: typeof fetch;
  private readonly requestTimeoutMs: number;

  constructor(options: CookMantraClientOptions = {}) {
    this.baseUrl = normalizeBaseUrl(options.baseUrl ?? DEFAULT_API_BASE_URL);
    this.fetch = options.fetch ?? globalThis.fetch.bind(globalThis);
    this.requestTimeoutMs = options.requestTimeoutMs ?? DEFAULT_REQUEST_TIMEOUT_MS;
    assertPositiveDuration(this.requestTimeoutMs, "Request timeout");
  }

  async createSession(
    image: Blob,
    options: CreateSessionOptions,
  ): Promise<QueuedJobResponse> {
    const body = new FormData();
    body.append("image", image);
    return this.request<QueuedJobResponse>(["sessions"], {
      method: "POST",
      headers: {
        Accept: "application/json",
        "X-Cook-Mantra-Runtime-Revision": options.runtimeRevision,
      },
      body,
      signal: options.signal,
    });
  }

  async getRuntimeStatus(
    options: ApiRequestOptions = {},
  ): Promise<RuntimeStatusResponse> {
    return this.request<RuntimeStatusResponse>(["runtime-status"], {
      method: "GET",
      headers: { Accept: "application/json" },
      cache: "no-store",
      signal: options.signal,
    });
  }

  async createManualSession(
    request: ManualSessionRequest,
    options: ApiRequestOptions = {},
  ): Promise<SessionResponse> {
    return this.request<SessionResponse>(
      ["sessions", "manual"],
      jsonRequest("POST", request, options.signal),
    );
  }

  async getSession(
    sessionId: string,
    options: ApiRequestOptions = {},
  ): Promise<SessionResponse> {
    return this.request<SessionResponse>(["sessions", sessionId], {
      method: "GET",
      headers: { Accept: "application/json" },
      signal: options.signal,
    });
  }

  async getJob(jobId: string, options: ApiRequestOptions = {}): Promise<JobResponse> {
    return this.request<JobResponse>(["jobs", jobId], {
      method: "GET",
      headers: { Accept: "application/json" },
      signal: options.signal,
    });
  }

  async updateIngredients(
    sessionId: string,
    review: IngredientReviewRequest,
    options: ApiRequestOptions = {},
  ): Promise<SessionResponse> {
    return this.request<SessionResponse>(
      ["sessions", sessionId, "ingredients"],
      jsonRequest("PUT", review, options.signal),
    );
  }

  async confirmIngredients(
    sessionId: string,
    options: ApiRequestOptions = {},
  ): Promise<SessionResponse> {
    return this.request<SessionResponse>(
      ["sessions", sessionId, "ingredients", "confirm"],
      {
        method: "POST",
        headers: { Accept: "application/json" },
        signal: options.signal,
      },
    );
  }

  async generateRecipeOptions(
    sessionId: string,
    preferences: RecipePreferences,
    options: ApiRequestOptions = {},
  ): Promise<QueuedJobResponse> {
    return this.request<QueuedJobResponse>(
      ["sessions", sessionId, "recipe-options"],
      jsonRequest("POST", preferences, options.signal),
    );
  }

  async generateMoreRecipeOptions(
    sessionId: string,
    preferences: RecipePreferences,
    options: ApiRequestOptions = {},
  ): Promise<QueuedJobResponse> {
    return this.request<QueuedJobResponse>(
      ["sessions", sessionId, "recipe-options", "more"],
      jsonRequest("POST", preferences, options.signal),
    );
  }

  async generateRecipes(
    sessionId: string,
    selection: RecipeSelectionRequest,
    options: ApiRequestOptions = {},
  ): Promise<QueuedJobResponse> {
    return this.request<QueuedJobResponse>(
      ["sessions", sessionId, "recipes"],
      jsonRequest("POST", selection, options.signal),
    );
  }

  artifactUrl(artifactId: string): string {
    return this.endpoint(["artifacts", artifactId]);
  }

  async pollJob(
    jobId: string,
    options: PollJobOptions = {},
  ): Promise<TerminalJobResponse> {
    const intervalMs = options.intervalMs ?? DEFAULT_POLL_INTERVAL_MS;
    const maxIntervalMs = options.maxIntervalMs ?? DEFAULT_MAX_POLL_INTERVAL_MS;
    const deadlineMs = options.deadlineMs ?? DEFAULT_POLL_DEADLINE_MS;
    assertPollingInterval(intervalMs);
    assertPollingInterval(maxIntervalMs);
    if (maxIntervalMs < intervalMs) {
      throw new RangeError(
        "Maximum polling interval must not be shorter than the initial interval.",
      );
    }
    assertPositiveDuration(deadlineMs, "Polling deadline");

    const deadlineAt = Date.now() + deadlineMs;
    let nextIntervalMs = intervalMs;

    while (true) {
      throwIfAborted(options.signal);
      if (Date.now() >= deadlineAt) throw new ApiTimeoutError("poll");
      const job = await this.getJob(jobId, { signal: options.signal });
      options.onProgress?.(job);
      if (job.status === "succeeded" || job.status === "failed") {
        return job as TerminalJobResponse;
      }
      const remainingMs = deadlineAt - Date.now();
      await waitForNextPoll(Math.min(nextIntervalMs, remainingMs), options.signal);
      nextIntervalMs = Math.min(nextIntervalMs * 2, maxIntervalMs);
    }
  }

  private endpoint(segments: string[]): string {
    return `${this.baseUrl}/${segments.map(encodeURIComponent).join("/")}`;
  }

  private async request<ResponseBody>(
    segments: string[],
    init: RequestInit,
  ): Promise<ResponseBody> {
    const callerSignal = init.signal ?? undefined;
    const timeoutSignal = AbortSignal.timeout(this.requestTimeoutMs);
    const signal = callerSignal
      ? AbortSignal.any([callerSignal, timeoutSignal])
      : timeoutSignal;
    let response: Response;
    try {
      response = await this.fetch(this.endpoint(segments), { ...init, signal });
    } catch (error) {
      if (timeoutSignal.aborted && !callerSignal?.aborted) {
        throw new ApiTimeoutError("request");
      }
      if (callerSignal?.aborted) {
        throw callerSignal.reason ?? error;
      }
      if (error instanceof TypeError) {
        throw new ApiNetworkError(this.endpoint(segments));
      }
      throw error;
    }
    if (!response.ok) {
      throw await normalizeApiError(response);
    }
    return (await response.json()) as ResponseBody;
  }
}

function normalizeBaseUrl(value: string): string {
  const candidate = value.trim();
  let url: URL;
  try {
    url = new URL(candidate);
  } catch {
    throw new TypeError("The API base URL must be an absolute HTTP URL.");
  }

  if (url.protocol !== "http:" && url.protocol !== "https:") {
    throw new TypeError("The API base URL must use HTTP or HTTPS.");
  }
  if (url.username || url.password || url.search || url.hash) {
    throw new TypeError(
      "The API base URL must not include credentials, a query, or a fragment.",
    );
  }

  url.pathname = url.pathname.replace(/\/+$/u, "");
  return url.toString().replace(/\/$/u, "");
}

function jsonRequest(
  method: "POST" | "PUT",
  body: unknown,
  signal: AbortSignal | undefined,
): RequestInit {
  return {
    method,
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
    signal,
  };
}

async function normalizeApiError(response: Response): Promise<ApiError> {
  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }

  if (isApiErrorEnvelope(payload)) {
    return new ApiError({
      status: response.status,
      code: payload.error.code,
      message: payload.error.message,
      details: payload.error.details,
      retryable: payload.error.retryable,
      requestId: payload.error.request_id,
      sessionId: payload.error.session_id,
      jobId: payload.error.job_id,
    });
  }

  return new ApiError({
    status: response.status,
    code: "internal_error",
    message: "The API request failed.",
    details: {},
    retryable: response.status >= 500,
    requestId: response.headers.get("X-Request-ID"),
    sessionId: null,
    jobId: null,
  });
}

function isApiErrorEnvelope(value: unknown): value is ApiErrorEnvelope {
  if (!isRecord(value) || !isRecord(value.error)) {
    return false;
  }
  const error = value.error;
  return (
    typeof error.code === "string" &&
    API_ERROR_CODES.has(error.code as ApiErrorCode) &&
    typeof error.message === "string" &&
    isRecord(error.details) &&
    typeof error.retryable === "boolean" &&
    typeof error.request_id === "string" &&
    isNullableString(error.session_id) &&
    isNullableString(error.job_id)
  );
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isNullableString(value: unknown): value is string | null {
  return value === null || typeof value === "string";
}

function assertPollingInterval(intervalMs: number): void {
  if (
    !Number.isFinite(intervalMs) ||
    intervalMs < MIN_POLL_INTERVAL_MS ||
    intervalMs > MAX_POLL_INTERVAL_MS
  ) {
    throw new RangeError(
      `Polling interval must be between ${MIN_POLL_INTERVAL_MS} and ${MAX_POLL_INTERVAL_MS} milliseconds.`,
    );
  }
}

function assertPositiveDuration(durationMs: number, label: string): void {
  if (!Number.isFinite(durationMs) || durationMs <= 0) {
    throw new RangeError(`${label} must be a positive number of milliseconds.`);
  }
}

function throwIfAborted(signal: AbortSignal | undefined): void {
  if (signal?.aborted) {
    throw signal.reason ?? new DOMException("The operation was aborted.", "AbortError");
  }
}

function waitForNextPoll(
  intervalMs: number,
  signal: AbortSignal | undefined,
): Promise<void> {
  throwIfAborted(signal);

  return new Promise((resolve, reject) => {
    const timeout = globalThis.setTimeout(finish, intervalMs);

    function finish(): void {
      signal?.removeEventListener("abort", abort);
      resolve();
    }

    function abort(): void {
      globalThis.clearTimeout(timeout);
      signal?.removeEventListener("abort", abort);
      reject(
        signal?.reason ?? new DOMException("The operation was aborted.", "AbortError"),
      );
    }

    signal?.addEventListener("abort", abort, { once: true });
    if (signal?.aborted) {
      abort();
    }
  });
}
