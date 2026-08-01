"use client";

import {
  useCallback,
  useEffect,
  useMemo,
  useReducer,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";

import { AppHeader } from "@/components/layout/AppHeader";
import { ErrorAlert } from "@/components/layout/ErrorAlert";
import { ProgressStepper } from "@/components/layout/ProgressStepper";
import {
  ApiError,
  ApiNetworkError,
  ApiTimeoutError,
  CookMantraClient,
  DEFAULT_API_BASE_URL,
} from "@/lib/api/cook-mantra-client";
import type { JobErrorResponse, SessionStage, TerminalJobResponse } from "@/types/api";

import { ConfirmScreen } from "./components/ConfirmScreen";
import { JobScreen } from "./components/JobScreen";
import { OptionsScreen } from "./components/OptionsScreen";
import { RecipesScreen } from "./components/RecipesScreen";
import { SavedRecipesScreen } from "./components/SavedRecipesScreen";
import { UploadScreen } from "./components/UploadScreen";
import {
  ingredientsFromApi,
  ingredientsToRebasedReviewRequest,
  ingredientsToReviewRequest,
  optionFromApi,
  preferencesToApi,
  recipeFromApi,
} from "./model/api-mappers";
import {
  confirmedIngredientCount,
  confirmedIngredientNames,
  cookSessionReducer,
  createInitialCookSessionState,
  type AppErrorView,
  type AppView,
  type CookSessionView,
  type CookSessionState,
  type JobView,
  type PreferenceView,
  type RecipeFailureView,
} from "./model/cook-session-state";
import {
  createDemoOptions,
  createDemoRecipes,
  demoDetectedIngredients,
  demoPantryIngredients,
} from "./model/demo-data";
import { loadPantryStaples, savePantryStaples } from "./model/pantry-staples";
import {
  loadSavedRecipes,
  SAVED_RECIPES_STORAGE_KEY,
  type SavedRecipeEntry,
} from "./model/saved-recipes";

const ACCEPTED_IMAGE_TYPES = new Set(["image/jpeg", "image/png", "image/webp"]);
const MAX_IMAGE_BYTES = 10 * 1024 * 1024;
const EMPTY_SAVED_RECIPE_ENTRIES: SavedRecipeEntry[] = [];

export interface CookMantraAppProps {
  demoJobDurationMs?: number;
  apiClient?: CookMantraClient;
  devControls?: boolean;
}

interface PendingOptionJob {
  kind: "ideas" | "more-ideas";
  returnView: "confirm" | "options";
  sessionId: string;
  jobId: string;
  append: boolean;
  failureStage: "ingredients_confirmed" | "options_ready";
}

interface PendingRecipeJob {
  sessionId: string;
  jobId: string;
  optionIds: string[];
  selectedNames: string[];
  entryStage: "options_ready" | "recipes_ready";
  merge: boolean;
  returnView: "options" | "recipes";
}

class JobRunError extends Error {
  readonly retryable: boolean;
  readonly jobError: JobErrorResponse | null;

  constructor(error: JobErrorResponse | null) {
    super(error?.message ?? "The agents could not finish this step.");
    this.name = "JobRunError";
    this.retryable = error?.retryable ?? true;
    this.jobError = error;
  }
}

export function CookMantraApp({
  demoJobDurationMs = 900,
  apiClient,
  devControls = false,
}: CookMantraAppProps) {
  const configuredBaseUrl = process.env.NEXT_PUBLIC_COOK_MANTRA_API_URL?.trim() || null;
  const missingProductionConfiguration =
    !apiClient && process.env.NODE_ENV === "production" && configuredBaseUrl === null;
  const client = useMemo(() => {
    if (apiClient) return apiClient;
    if (missingProductionConfiguration) return null;
    return new CookMantraClient({
      baseUrl: configuredBaseUrl ?? DEFAULT_API_BASE_URL,
    });
  }, [apiClient, configuredBaseUrl, missingProductionConfiguration]);

  if (!client) return <ApiConfigurationNotice />;

  return (
    <ConnectedCookMantraApp
      client={client}
      demoJobDurationMs={demoJobDurationMs}
      devControls={devControls}
    />
  );
}

function ConnectedCookMantraApp({
  client,
  demoJobDurationMs,
  devControls,
}: {
  client: CookMantraClient;
  demoJobDurationMs: number;
  devControls: boolean;
}) {
  const [state, dispatch] = useReducer(
    cookSessionReducer,
    undefined,
    createInitialCookSessionState,
  );
  const [savedRecipes, setSavedRecipes] = useSavedRecipeEntries();
  const [savedViewOpen, setSavedViewOpen] = useState(false);
  const stateRef = useRef(state);
  stateRef.current = state;
  const activeController = useRef<AbortController | null>(null);
  const activeJobMode = useRef<"api" | "demo" | null>(null);
  const apiSessionStage = useRef<SessionStage | null>(null);
  const uploadedPhotoFile = useRef<File | null>(null);
  const manualEntry = useRef(false);
  const photoPreviewUrl = useRef<string | null>(null);
  const lastRetry = useRef<(() => void) | null>(null);
  const savedReturnScrollY = useRef(0);
  const activeAppView: AppView = savedViewOpen ? "saved" : state.view;
  const previousView = useRef<AppView>(activeAppView);
  useEffect(() => {
    dispatch({ type: "set-pantry-staples", names: loadPantryStaples() });
  }, []);

  useEffect(
    () => () => {
      activeController.current?.abort();
      if (photoPreviewUrl.current) URL.revokeObjectURL(photoPreviewUrl.current);
    },
    [],
  );

  useEffect(() => {
    const returningFromSaved = previousView.current === "saved";
    window.scrollTo({
      top: returningFromSaved ? savedReturnScrollY.current : 0,
      left: 0,
      behavior: "auto",
    });
    if (previousView.current !== activeAppView) {
      const viewSelector =
        activeAppView === "saved" ? ".saved-view-shell" : ".cook-session-view";
      document
        .querySelector<HTMLElement>(`${viewSelector} h1`)
        ?.focus({ preventScroll: true });
      previousView.current = activeAppView;
    }
  }, [activeAppView]);

  const beginOperation = useCallback(() => {
    activeController.current?.abort();
    const controller = new AbortController();
    activeController.current = controller;
    return controller;
  }, []);

  const finishOperation = useCallback((controller: AbortController) => {
    if (activeController.current === controller) activeController.current = null;
  }, []);

  const handlePantryStaplesChange = useCallback((names: string[]) => {
    const persisted = savePantryStaples(names);
    dispatch({ type: "set-pantry-staples", names });
    return persisted;
  }, []);

  const runDemoJob = useCallback(
    (
      kind: JobView["kind"],
      returnView: JobView["returnView"],
      selectedNames: string[],
      onComplete: () => void,
    ) => {
      const controller = beginOperation();
      activeJobMode.current = "demo";
      dispatch({ type: "start-job", kind, returnView, selectedNames });

      void (async () => {
        try {
          const progressPoints = [18, 52, 82, 100];
          const interval = demoJobDurationMs > 0 ? demoJobDurationMs / 4 : 0;
          for (const progress of progressPoints) {
            await abortableDelay(interval, controller.signal);
            dispatch({ type: "update-job-progress", progress });
          }
          await Promise.resolve();
          if (!controller.signal.aborted) onComplete();
        } catch (error) {
          if (!isAbortError(error)) {
            dispatch({ type: "fail-job", error: toAppError(error) });
          }
        } finally {
          finishOperation(controller);
        }
      })();
    },
    [beginOperation, demoJobDurationMs, finishOperation],
  );

  function replacePhotoPreview(nextUrl: string | null) {
    if (photoPreviewUrl.current && photoPreviewUrl.current !== nextUrl) {
      URL.revokeObjectURL(photoPreviewUrl.current);
    }
    photoPreviewUrl.current = nextUrl;
  }

  function failCurrentOperation(error: unknown) {
    if (isAbortError(error)) return;
    logOperationFailure(error, stateRef.current);
    dispatch({ type: "fail-job", error: toAppError(error) });
  }

  function showLifecycleError(
    kind: JobView["kind"],
    returnView: JobView["returnView"],
    error: AppErrorView,
  ) {
    activeJobMode.current = null;
    lastRetry.current = null;
    dispatch({ type: "start-job", kind, returnView, selectedNames: [] });
    dispatch({ type: "fail-job", error });
  }

  async function receiveOptionJob(
    controller: AbortController,
    pending: PendingOptionJob,
  ) {
    const terminal = await client.pollJob(pending.jobId, {
      intervalMs: 750,
      signal: controller.signal,
      onProgress: (job) =>
        dispatch({ type: "update-job-progress", progress: job.progress }),
    });
    if (terminal.status === "failed") {
      apiSessionStage.current = pending.failureStage;
      if (
        pending.kind === "more-ideas" &&
        terminal.error?.code === "recipe_duplicate"
      ) {
        lastRetry.current = null;
        dispatch({ type: "mark-ideas-exhausted" });
        return;
      }
      lastRetry.current =
        pending.kind === "more-ideas" ? handleMoreIdeas : handleGenerateOptions;
    }
    assertSuccessfulJob(terminal);
    apiSessionStage.current = "options_ready";
    const session = await client.getSession(pending.sessionId, {
      signal: controller.signal,
    });
    apiSessionStage.current = session.stage;
    dispatch({
      type: "receive-options",
      options: session.recipe_options.map((option) =>
        optionFromApi(option, session.option_batch_number),
      ),
      append: pending.append,
    });
  }

  function resumeOptionJob(pending: PendingOptionJob) {
    const controller = beginOperation();
    activeJobMode.current = "api";
    apiSessionStage.current = "generating_options";
    lastRetry.current = () => resumeOptionJob(pending);
    dispatch({
      type: "start-job",
      kind: pending.kind,
      returnView: pending.returnView,
      selectedNames: [],
    });
    void receiveOptionJob(controller, pending)
      .catch(failCurrentOperation)
      .finally(() => finishOperation(controller));
  }

  async function receiveRecipeJob(
    controller: AbortController,
    pending: PendingRecipeJob,
  ) {
    const terminal = await client.pollJob(pending.jobId, {
      intervalMs: 750,
      signal: controller.signal,
      onProgress: (job) =>
        dispatch({ type: "update-job-progress", progress: job.progress }),
    });
    if (terminal.status === "failed") {
      apiSessionStage.current = pending.entryStage;
      lastRetry.current = pending.merge
        ? handleRetryFailedRecipes
        : handleCreateRecipes;
    }
    assertSuccessfulJob(terminal);
    apiSessionStage.current = "recipes_ready";
    const session = await client.getSession(pending.sessionId, {
      signal: controller.signal,
    });
    apiSessionStage.current = session.stage;
    const requestedIds = new Set(pending.optionIds);
    const recipes = Object.fromEntries(
      Object.entries(session.complete_recipes)
        .filter(([optionId]) => !pending.merge || requestedIds.has(optionId))
        .map(([optionId, recipe]) => [optionId, recipeFromApi(recipe)]),
    );
    const failures = Object.fromEntries(
      Object.entries(session.recipe_failures)
        .filter(([optionId]) => !pending.merge || requestedIds.has(optionId))
        .map(([optionId, failure]) => [
          optionId,
          {
            optionId: failure.option_id,
            code: failure.code,
            message: failure.message,
            retryable: failure.retryable,
          } satisfies RecipeFailureView,
        ]),
    );
    dispatch({
      type: "receive-recipes",
      recipes,
      failures,
      merge: pending.merge,
    });
  }

  function resumeRecipeJob(pending: PendingRecipeJob) {
    const controller = beginOperation();
    activeJobMode.current = "api";
    apiSessionStage.current = "generating_recipes";
    lastRetry.current = () => resumeRecipeJob(pending);
    dispatch({
      type: "start-job",
      kind: "recipes",
      returnView: pending.returnView,
      selectedNames: pending.selectedNames,
    });
    void receiveRecipeJob(controller, pending)
      .catch(failCurrentOperation)
      .finally(() => finishOperation(controller));
  }

  function handleUpload(file: File) {
    if (!ACCEPTED_IMAGE_TYPES.has(file.type)) {
      lastRetry.current = null;
      dispatch({
        type: "fail-job",
        error: {
          title: "Choose a supported photo",
          message: "Use a JPEG, PNG, or WebP image.",
          retryable: false,
        },
      });
      return;
    }
    if (file.size > MAX_IMAGE_BYTES) {
      lastRetry.current = null;
      dispatch({
        type: "fail-job",
        error: {
          title: "That photo is too large",
          message: "Choose an image under 10 MB.",
          retryable: false,
        },
      });
      return;
    }

    const preview = URL.createObjectURL(file);
    replacePhotoPreview(preview);
    uploadedPhotoFile.current = file;
    manualEntry.current = false;
    apiSessionStage.current = null;
    lastRetry.current = () => handleUpload(file);
    const controller = beginOperation();
    activeJobMode.current = "api";
    dispatch({
      type: "start-job",
      kind: "extraction",
      returnView: "upload",
      selectedNames: [],
    });

    void (async () => {
      try {
        const queued = await client.createSession(file, { signal: controller.signal });
        apiSessionStage.current = "extracting";
        // The photo is on the server now; nudge progress off zero so the job
        // screen marks the upload line done and shows the agent as running.
        dispatch({ type: "update-job-progress", progress: 2 });
        const terminal = await client.pollJob(queued.job_id, {
          intervalMs: 750,
          signal: controller.signal,
          onProgress: (job) =>
            dispatch({ type: "update-job-progress", progress: job.progress }),
        });
        assertSuccessfulJob(terminal);
        apiSessionStage.current = "reviewing_ingredients";
        const session = await client.getSession(queued.session_id, {
          signal: controller.signal,
        });
        apiSessionStage.current = session.stage;
        const detected = session.ingredients.filter(
          (ingredient) => ingredient.source === "detected",
        );
        const weakDetection =
          detected.length === 0 ||
          detected.every((ingredient) => (ingredient.confidence ?? 0) < 0.7);
        dispatch({
          type: "receive-ingredients",
          mode: "api",
          sessionId: session.id,
          photoPreviewUrl: preview,
          weakDetection,
          ingredients: ingredientsFromApi(session.ingredients),
        });
      } catch (error) {
        failCurrentOperation(error);
      } finally {
        finishOperation(controller);
      }
    })();
  }

  function handleWeakDetection() {
    uploadedPhotoFile.current = null;
    manualEntry.current = false;
    apiSessionStage.current = null;
    lastRetry.current = handleWeakDetection;
    runDemoJob("extraction", "upload", [], () => {
      dispatch({
        type: "receive-ingredients",
        mode: "demo",
        sessionId: null,
        photoPreviewUrl: null,
        weakDetection: true,
        ingredients: [
          ...demoDetectedIngredients(true),
          ...demoPantryIngredients(stateRef.current.pantryStaples),
        ],
      });
    });
  }

  function handleManualEntry() {
    activeController.current?.abort();
    activeJobMode.current = null;
    uploadedPhotoFile.current = null;
    manualEntry.current = true;
    apiSessionStage.current = null;
    replacePhotoPreview(null);
    lastRetry.current = null;
    dispatch({ type: "start-manual-entry" });
  }

  // Options generation always starts from a session the server still has open
  // for review. A typed-ingredient session is cheap to reopen; a photo session
  // can only be reopened from the original upload.
  async function openReviewSession(
    controller: AbortController,
    ingredients: CookSessionState["ingredients"],
  ) {
    if (manualEntry.current) {
      const names = confirmedIngredientNames(ingredients);
      if (!names.length) {
        throw new CapabilityError(
          "Confirm an ingredient first",
          "Confirm at least one ingredient before generating ideas.",
        );
      }
      apiSessionStage.current = "reviewing_ingredients";
      return client.createManualSession(
        { ingredients: names },
        { signal: controller.signal },
      );
    }

    const sourcePhoto = uploadedPhotoFile.current;
    if (!sourcePhoto) {
      throw new CapabilityError(
        "Start over with a photo",
        "Editing these ingredients needs the original photo. Start over with a new photo to create another set of ideas.",
      );
    }

    const extraction = await client.createSession(sourcePhoto, {
      signal: controller.signal,
    });
    apiSessionStage.current = "extracting";
    const extractionTerminal = await client.pollJob(extraction.job_id, {
      intervalMs: 750,
      signal: controller.signal,
      onProgress: (job) =>
        dispatch({ type: "update-job-progress", progress: job.progress }),
    });
    assertSuccessfulJob(extractionTerminal);
    apiSessionStage.current = "reviewing_ingredients";
    return client.getSession(extraction.session_id, { signal: controller.signal });
  }

  function handleGenerateOptions() {
    const currentState = stateRef.current;
    if (Object.keys(currentState.ingredientNameErrors).length > 0) return;
    lastRetry.current = handleGenerateOptions;
    if (currentState.mode === "demo") {
      runDemoJob("ideas", "confirm", [], () => {
        const options = createDemoOptions({
          preferences: currentState.preferences,
          batchNumber: 1,
          excludedIds: [],
          ingredients: currentState.ingredients,
        });
        dispatch({ type: "receive-options", options, append: false });
      });
      return;
    }

    const sessionId = currentState.sessionId;
    const controller = beginOperation();
    activeJobMode.current = "api";
    dispatch({
      type: "start-job",
      kind: "ideas",
      returnView: "confirm",
      selectedNames: [],
    });
    void (async () => {
      let pending: PendingOptionJob | null = null;
      try {
        let reviewSessionId = sessionId;
        let review = ingredientsToReviewRequest(currentState.ingredients);

        if (
          reviewSessionId === null ||
          apiSessionStage.current !== "reviewing_ingredients"
        ) {
          const freshSession = await openReviewSession(
            controller,
            currentState.ingredients,
          );
          apiSessionStage.current = freshSession.stage;
          reviewSessionId = freshSession.id;
          review = ingredientsToRebasedReviewRequest(
            currentState.ingredients,
            freshSession.ingredients,
          );
          dispatch({ type: "replace-session-id", sessionId: freshSession.id });
        }

        const reviewedSession = await client.updateIngredients(
          reviewSessionId,
          review,
          { signal: controller.signal },
        );
        apiSessionStage.current = reviewedSession.stage;
        apiSessionStage.current = null;
        const confirmedSession = await client.confirmIngredients(reviewSessionId, {
          signal: controller.signal,
        });
        apiSessionStage.current = confirmedSession.stage;
        apiSessionStage.current = null;
        const queued = await client.generateRecipeOptions(
          reviewSessionId,
          preferencesToApi(currentState.preferences),
          { signal: controller.signal },
        );
        const queuedJob: PendingOptionJob = {
          kind: "ideas",
          returnView: "confirm",
          sessionId: reviewSessionId,
          jobId: queued.job_id,
          append: false,
          failureStage: "ingredients_confirmed",
        };
        pending = queuedJob;
        apiSessionStage.current = "generating_options";
        lastRetry.current = () => resumeOptionJob(queuedJob);
        await receiveOptionJob(controller, queuedJob);
      } catch (error) {
        if (!pending) apiSessionStage.current = null;
        failCurrentOperation(error);
      } finally {
        finishOperation(controller);
      }
    })();
  }

  function handleMoreIdeas() {
    const currentState = stateRef.current;
    lastRetry.current = handleMoreIdeas;
    if (currentState.mode === "demo" || !currentState.sessionId) {
      runDemoJob("more-ideas", "options", [], () => {
        const options = createDemoOptions({
          preferences: currentState.preferences,
          batchNumber:
            Math.max(0, ...currentState.options.map((option) => option.batchNumber)) +
            1,
          excludedIds: currentState.options.map((option) => option.id),
          ingredients: currentState.ingredients,
        });
        dispatch({ type: "receive-options", options, append: true });
      });
      return;
    }

    if (apiSessionStage.current !== "options_ready") {
      showLifecycleError("more-ideas", "options", {
        title: "More ideas need a fresh start",
        message:
          "This batch of ideas closed when your recipes were created. Edit your ingredients and Cook Mantra will re-read your photo for new ideas — the recipes you already created are safe.",
        retryable: false,
      });
      return;
    }

    const sessionId = currentState.sessionId;
    const controller = beginOperation();
    activeJobMode.current = "api";
    dispatch({
      type: "start-job",
      kind: "more-ideas",
      returnView: "options",
      selectedNames: [],
    });
    void (async () => {
      let pending: PendingOptionJob | null = null;
      try {
        apiSessionStage.current = null;
        const queued = await client.generateMoreRecipeOptions(
          sessionId,
          preferencesToApi(currentState.preferences),
          { signal: controller.signal },
        );
        const queuedJob: PendingOptionJob = {
          kind: "more-ideas",
          returnView: "options",
          sessionId,
          jobId: queued.job_id,
          append: true,
          failureStage: "options_ready",
        };
        pending = queuedJob;
        apiSessionStage.current = "generating_options";
        lastRetry.current = () => resumeOptionJob(queuedJob);
        await receiveOptionJob(controller, queuedJob);
      } catch (error) {
        if (!pending) apiSessionStage.current = "options_ready";
        failCurrentOperation(error);
      } finally {
        finishOperation(controller);
      }
    })();
  }

  function handleCreateRecipes() {
    const currentState = stateRef.current;
    const selectedOptions = currentState.options.filter((option) =>
      currentState.selectedOptionIds.includes(option.id),
    );
    const selectedNames = selectedOptions.map((option) => option.name);
    if (!selectedOptions.length) return;
    lastRetry.current = handleCreateRecipes;

    if (currentState.mode === "demo" || !currentState.sessionId) {
      runDemoJob("recipes", "options", selectedNames, () => {
        dispatch({
          type: "receive-recipes",
          recipes: createDemoRecipes(
            selectedOptions,
            currentState.ingredients,
            currentState.preferences.servings,
          ),
          failures: {},
          merge: false,
        });
      });
      return;
    }

    if (
      apiSessionStage.current !== "options_ready" &&
      apiSessionStage.current !== "recipes_ready"
    ) {
      showLifecycleError("recipes", "options", {
        title: "Still working on this step",
        message:
          "Wait a moment for the current step to finish, then create recipes from these ideas.",
        retryable: false,
      });
      return;
    }

    const sessionId = currentState.sessionId;
    const recipeEntryStage = apiSessionStage.current;
    const controller = beginOperation();
    activeJobMode.current = "api";
    dispatch({
      type: "start-job",
      kind: "recipes",
      returnView: "options",
      selectedNames,
    });
    void (async () => {
      let pending: PendingRecipeJob | null = null;
      try {
        apiSessionStage.current = null;
        const queued = await client.generateRecipes(
          sessionId,
          { option_ids: currentState.selectedOptionIds },
          { signal: controller.signal },
        );
        const queuedJob: PendingRecipeJob = {
          sessionId,
          jobId: queued.job_id,
          optionIds: currentState.selectedOptionIds,
          selectedNames,
          entryStage: recipeEntryStage,
          merge: false,
          returnView: "options",
        };
        pending = queuedJob;
        apiSessionStage.current = "generating_recipes";
        lastRetry.current = () => resumeRecipeJob(queuedJob);
        await receiveRecipeJob(controller, queuedJob);
      } catch (error) {
        if (!pending) apiSessionStage.current = recipeEntryStage;
        failCurrentOperation(error);
      } finally {
        finishOperation(controller);
      }
    })();
  }

  function handleRetryFailedRecipes() {
    const currentState = stateRef.current;
    const retryableIds = Object.values(currentState.recipeFailures)
      .filter((failure) => failure.retryable)
      .map((failure) => failure.optionId)
      .filter((optionId) =>
        currentState.options.some((option) => option.id === optionId),
      );
    if (!retryableIds.length || !currentState.sessionId) return;

    const retryableIdSet = new Set(retryableIds);
    const selectedNames = currentState.options
      .filter((option) => retryableIdSet.has(option.id))
      .map((option) => option.name);
    lastRetry.current = handleRetryFailedRecipes;

    if (apiSessionStage.current !== "recipes_ready") {
      showLifecycleError("recipes", "recipes", {
        title: "Failed recipes cannot be retried yet",
        message:
          "This session is not ready to retry recipes. Return to your recipe ideas and create them again.",
        retryable: false,
      });
      return;
    }

    const sessionId = currentState.sessionId;
    const controller = beginOperation();
    activeJobMode.current = "api";
    dispatch({
      type: "start-job",
      kind: "recipes",
      returnView: "recipes",
      selectedNames,
    });
    void (async () => {
      let pending: PendingRecipeJob | null = null;
      try {
        apiSessionStage.current = null;
        const queued = await client.generateRecipes(
          sessionId,
          { option_ids: retryableIds },
          { signal: controller.signal },
        );
        const queuedJob: PendingRecipeJob = {
          sessionId,
          jobId: queued.job_id,
          optionIds: retryableIds,
          selectedNames,
          entryStage: "recipes_ready",
          merge: true,
          returnView: "recipes",
        };
        pending = queuedJob;
        apiSessionStage.current = "generating_recipes";
        lastRetry.current = () => resumeRecipeJob(queuedJob);
        await receiveRecipeJob(controller, queuedJob);
      } catch (error) {
        if (!pending) apiSessionStage.current = "recipes_ready";
        failCurrentOperation(error);
      } finally {
        finishOperation(controller);
      }
    })();
  }

  function handleCancelJob() {
    if (activeJobMode.current === "api") return;
    activeController.current?.abort();
    activeController.current = null;
    dispatch({ type: "cancel-job" });
  }

  function handleSimulateFailure() {
    if (activeJobMode.current === "api") return;
    activeController.current?.abort();
    activeController.current = null;
    dispatch({
      type: "fail-job",
      error: {
        title: "That took too long",
        message:
          "The model timed out. Nothing was lost — your work is exactly as you left it.",
        retryable: true,
      },
    });
  }

  function handleReset() {
    activeController.current?.abort();
    activeController.current = null;
    activeJobMode.current = null;
    uploadedPhotoFile.current = null;
    manualEntry.current = false;
    apiSessionStage.current = null;
    replacePhotoPreview(null);
    lastRetry.current = null;
    dispatch({ type: "reset" });
  }

  function handleOpenSavedRecipes() {
    if (savedViewOpen) return;
    savedReturnScrollY.current = window.scrollY;
    setSavedViewOpen(true);
  }

  function handleCloseSavedRecipes() {
    setSavedViewOpen(false);
  }

  function handleStepChange(index: number) {
    const views: Array<Exclude<CookSessionView, "job">> = [
      "upload",
      "confirm",
      "options",
      "recipes",
    ];
    const view = views[index];
    if (view) dispatch({ type: "navigate", view });
  }

  const currentStepIndex = viewStepIndex(state.view, state.job);
  const generatedContentExists =
    state.options.length > 0 ||
    Object.keys(state.completeRecipes).length > 0 ||
    Object.keys(state.recipeFailures).length > 0;

  return (
    <div className="app-shell">
      <AppHeader
        savedActive={savedViewOpen}
        savedCount={savedRecipes.length}
        onOpenSaved={handleOpenSavedRecipes}
      />
      {savedViewOpen ? null : (
        <ProgressStepper
          currentIndex={currentStepIndex}
          maxReachedIndex={state.maxReached - 1}
          locked={state.view === "job"}
          onStepChange={handleStepChange}
        />
      )}
      <main className="page-main">
        <div className="cook-session-view" hidden={savedViewOpen}>
          {state.error ? (
            <ErrorAlert
              title={state.error.title}
              message={state.error.message}
              onRetry={
                state.error.retryable && lastRetry.current
                  ? () => lastRetry.current?.()
                  : undefined
              }
              onDismiss={() => dispatch({ type: "dismiss-error" })}
            />
          ) : null}

          {state.view === "upload" ? (
            <UploadScreen
              onUpload={handleUpload}
              onManualEntry={handleManualEntry}
              onWeakDetection={handleWeakDetection}
              showWeakDetection={devControls}
            />
          ) : null}
          {state.view === "job" && state.job ? (
            <JobScreen
              job={state.job}
              allowInterruption={activeJobMode.current !== "api"}
              onCancel={handleCancelJob}
              onSimulateFailure={handleSimulateFailure}
              showSimulateFailure={devControls}
            />
          ) : null}
          {state.view === "confirm" ? (
            <ConfirmScreen
              ingredients={state.ingredients}
              ingredientNameErrors={state.ingredientNameErrors}
              preferences={state.preferences}
              weakDetection={state.weakDetection}
              manualEntry={state.mode === "api" && state.photoPreviewUrl === null}
              onAddIngredient={(name) => dispatch({ type: "add-ingredient", name })}
              onRenameIngredient={(id, name) =>
                dispatch({ type: "rename-ingredient", id, name })
              }
              onRemoveIngredient={(id) => dispatch({ type: "remove-ingredient", id })}
              onToggleIngredient={(id) => dispatch({ type: "toggle-ingredient", id })}
              onToggleAllPantry={(confirmed) =>
                dispatch({ type: "toggle-all-pantry", confirmed })
              }
              pantryStaples={state.pantryStaples}
              generatedContentExists={generatedContentExists}
              onPantryStaplesChange={handlePantryStaplesChange}
              onPreferenceChange={<Key extends keyof PreferenceView>(
                key: Key,
                value: PreferenceView[Key],
              ) => dispatch({ type: "set-preference", key, value })}
              onRetake={handleReset}
              onGenerate={handleGenerateOptions}
            />
          ) : null}
          {state.view === "options" ? (
            <OptionsScreen
              options={state.options}
              selectedOptionIds={state.selectedOptionIds}
              ingredientCount={confirmedIngredientCount(state.ingredients)}
              ideasExhausted={state.ideasExhausted}
              moreIdeasUnavailableReason={
                state.mode === "api" && apiSessionStage.current !== "options_ready"
                  ? "To get more ideas, edit your ingredients — Cook Mantra will re-read your photo and start fresh. You can still create recipes from the ideas already shown."
                  : null
              }
              previewUrl={(artifactId) => client.artifactUrl(artifactId)}
              onToggleOption={(id) => dispatch({ type: "toggle-option", id })}
              onMoreIdeas={handleMoreIdeas}
              onEditIngredients={() => dispatch({ type: "navigate", view: "confirm" })}
              onCreateRecipes={handleCreateRecipes}
            />
          ) : null}
          {state.view === "recipes" ? (
            <RecipesScreen
              recipes={state.completeRecipes}
              failures={state.recipeFailures}
              options={state.options}
              confirmedIngredients={state.ingredients}
              activeRecipeId={state.activeRecipeId}
              completedSteps={state.completedSteps}
              savedRecipes={savedRecipes}
              onSetActiveRecipe={(optionId) =>
                dispatch({ type: "set-active-recipe", optionId })
              }
              onToggleStep={(optionId, stepNumber) =>
                dispatch({ type: "toggle-recipe-step", optionId, stepNumber })
              }
              onRetryFailed={handleRetryFailedRecipes}
              onSavedRecipesChange={setSavedRecipes}
              onBack={() => dispatch({ type: "navigate", view: "options" })}
              onReset={handleReset}
            />
          ) : null}
        </div>
        {savedViewOpen ? (
          <div className="saved-view-shell">
            <SavedRecipesScreen
              entries={savedRecipes}
              onBack={handleCloseSavedRecipes}
              onEntriesChange={setSavedRecipes}
            />
          </div>
        ) : null}
      </main>
    </div>
  );
}

function ApiConfigurationNotice() {
  const [savedRecipes, setSavedRecipes] = useSavedRecipeEntries();
  const [savedViewOpen, setSavedViewOpen] = useState(false);

  return (
    <div className="app-shell">
      <AppHeader
        savedActive={savedViewOpen}
        savedCount={savedRecipes.length}
        onOpenSaved={() => setSavedViewOpen(true)}
      />
      <main className={`page-main${savedViewOpen ? "" : " configuration-main"}`}>
        {savedViewOpen ? (
          <div className="saved-view-shell">
            <SavedRecipesScreen
              entries={savedRecipes}
              onBack={() => setSavedViewOpen(false)}
              onEntriesChange={setSavedRecipes}
            />
          </div>
        ) : (
          <section className="configuration-notice" aria-labelledby="config-title">
            <p className="kicker">Configuration required</p>
            <h1 className="screen-title" id="config-title" tabIndex={-1}>
              Cook Mantra is not connected to its kitchen
            </h1>
            <p className="screen-intro">
              Set <code>NEXT_PUBLIC_COOK_MANTRA_API_URL</code> to the deployed Cook
              Mantra API URL, then rebuild and redeploy this site.
            </p>
          </section>
        )}
      </main>
    </div>
  );
}

function useSavedRecipeEntries() {
  const snapshotRef = useRef<SavedRecipeEntry[] | null>(null);
  const listenerRef = useRef<(() => void) | null>(null);
  const getSnapshot = useCallback(() => {
    snapshotRef.current ??= loadSavedRecipes();
    return snapshotRef.current;
  }, []);
  const subscribe = useCallback((listener: () => void) => {
    listenerRef.current = listener;

    function handleStorage(event: StorageEvent) {
      if (event.key !== null && event.key !== SAVED_RECIPES_STORAGE_KEY) {
        return;
      }
      snapshotRef.current = loadSavedRecipes();
      listener();
    }

    window.addEventListener("storage", handleStorage);
    return () => {
      if (listenerRef.current === listener) listenerRef.current = null;
      window.removeEventListener("storage", handleStorage);
    };
  }, []);
  const entries = useSyncExternalStore(
    subscribe,
    getSnapshot,
    getEmptySavedRecipeSnapshot,
  );
  const setEntries = useCallback((nextEntries: SavedRecipeEntry[]) => {
    snapshotRef.current = nextEntries;
    listenerRef.current?.();
  }, []);

  return [entries, setEntries] as const;
}

function getEmptySavedRecipeSnapshot(): SavedRecipeEntry[] {
  return EMPTY_SAVED_RECIPE_ENTRIES;
}

function viewStepIndex(view: CookSessionView, job: JobView | null): number {
  if (view === "job") {
    if (job?.kind === "extraction") return 0;
    if (job?.kind === "recipes") return 3;
    return 2;
  }
  return { upload: 0, confirm: 1, options: 2, recipes: 3 }[view];
}

function assertSuccessfulJob(job: TerminalJobResponse) {
  if (job.status === "failed") throw new JobRunError(job.error);
}

class CapabilityError extends Error {
  readonly title: string;

  constructor(title: string, message: string) {
    super(message);
    this.name = "CapabilityError";
    this.title = title;
  }
}

// Diagnostic instrumentation: the UI only ever shows a sanitised title and
// message, so log the full failure shape for copy-paste bug reports.
function logOperationFailure(error: unknown, state: CookSessionState): void {
  const diagnostic: Record<string, unknown> = {
    step: state.job?.kind ?? state.view,
    mode: state.mode,
    sessionId: state.sessionId,
    errorName: error instanceof Error ? error.name : typeof error,
    message: error instanceof Error ? error.message : String(error),
  };

  if (error instanceof ApiError) {
    Object.assign(diagnostic, {
      source: "http",
      status: error.status,
      code: error.code,
      details: error.details,
      retryable: error.retryable,
      requestId: error.requestId,
      apiSessionId: error.sessionId,
      apiJobId: error.jobId,
    });
  } else if (error instanceof JobRunError) {
    Object.assign(diagnostic, {
      source: "job",
      code: error.jobError?.code ?? null,
      details: error.jobError?.details ?? null,
      retryable: error.retryable,
    });
  } else if (error instanceof ApiNetworkError) {
    Object.assign(diagnostic, {
      source: "network",
      url: error.url,
      pageOrigin: typeof window !== "undefined" ? window.location.origin : null,
    });
  }

  console.error("[cook-mantra] operation failed", diagnostic);
  if (error instanceof Error) console.error(error);
}

function toAppError(error: unknown): AppErrorView {
  if (error instanceof ApiNetworkError) {
    return {
      title: "Cook Mantra could not reach the server",
      message: "Check your connection and try again.",
      retryable: true,
    };
  }
  if (error instanceof ApiTimeoutError) {
    return {
      title:
        error.kind === "poll"
          ? "This step stopped responding"
          : "The server took too long to respond",
      message: error.message,
      retryable: true,
    };
  }
  if (error instanceof ApiError) {
    return {
      title: apiErrorTitle(error),
      message: apiErrorMessage(error),
      retryable: error.retryable,
    };
  }
  if (error instanceof JobRunError) {
    return {
      title: "The agent stopped early",
      message: error.message,
      retryable: error.retryable,
    };
  }
  if (error instanceof CapabilityError) {
    return {
      title: error.title,
      message: error.message,
      retryable: false,
    };
  }
  return {
    title: "Cook Mantra could not finish",
    message:
      error instanceof Error
        ? error.message
        : "Something unexpected interrupted this step.",
    retryable: true,
  };
}

// Offline codes carry server-internal wording ("Ollama is unavailable."), so
// they get a user-facing message instead of the raw one.
function apiErrorMessage(error: ApiError): string {
  if (error.code === "ollama_unavailable" || error.code === "model_not_found") {
    return "The recipe agents are not reachable right now. Try again in a few minutes.";
  }
  return error.message;
}

function apiErrorTitle(error: ApiError): string {
  if (error.code === "ollama_unavailable" || error.code === "model_not_found") {
    return "The cooking agents are offline";
  }
  if (error.code === "service_busy") return "The cooking agents are busy";
  if (error.code === "operation_timed_out") return "This step took too long";
  if (error.code === "invalid_session_transition") return "This session changed";
  return "Cook Mantra could not finish";
}

function isAbortError(error: unknown): boolean {
  return (
    (error instanceof DOMException && error.name === "AbortError") ||
    (error instanceof Error && error.name === "AbortError")
  );
}

function abortableDelay(milliseconds: number, signal: AbortSignal): Promise<void> {
  if (signal.aborted) {
    return Promise.reject(
      signal.reason ?? new DOMException("The operation was aborted.", "AbortError"),
    );
  }
  if (milliseconds <= 0) return Promise.resolve();

  return new Promise((resolve, reject) => {
    const timeout = window.setTimeout(finish, milliseconds);

    function finish() {
      signal.removeEventListener("abort", abort);
      resolve();
    }

    function abort() {
      window.clearTimeout(timeout);
      signal.removeEventListener("abort", abort);
      reject(
        signal.reason ?? new DOMException("The operation was aborted.", "AbortError"),
      );
    }

    signal.addEventListener("abort", abort, { once: true });
  });
}
