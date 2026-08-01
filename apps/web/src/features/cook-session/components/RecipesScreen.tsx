"use client";

import { AlertTriangle, ArrowLeft, Bookmark, Download, RefreshCw } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";

import { captureDishPhotoThumbnail } from "../model/dish-photo";
import { downloadRecipeMarkdown } from "../model/recipe-markdown";
import { saveRecipe, type SavedRecipeEntry } from "../model/saved-recipes";
import type {
  CompleteRecipeView,
  IngredientView,
  RecipeFailureView,
  RecipeOptionView,
} from "../model/cook-session-state";
import { captureIngredientSources, RecipeDetail } from "./RecipeDetail";
import { ScreenHeader } from "./ScreenHeader";

interface RecipesScreenProps {
  recipes: Record<string, CompleteRecipeView>;
  failures: Record<string, RecipeFailureView>;
  options: RecipeOptionView[];
  confirmedIngredients: IngredientView[];
  activeRecipeId: string | null;
  completedSteps: Record<string, number[]>;
  previewUrl: (artifactId: string) => string;
  onSetActiveRecipe: (optionId: string) => void;
  onToggleStep: (optionId: string, stepNumber: number) => void;
  onRetryFailed: () => void;
  onBack: () => void;
  onReset: () => void;
  onSavedRecipesChange: (entries: SavedRecipeEntry[]) => void;
}

function plural(value: number, singular: string, pluralForm = `${singular}s`) {
  return value === 1 ? singular : pluralForm;
}

export function RecipesScreen({
  recipes,
  failures,
  options,
  confirmedIngredients,
  activeRecipeId,
  completedSteps,
  previewUrl,
  onSetActiveRecipe,
  onToggleStep,
  onRetryFailed,
  onBack,
  onReset,
  onSavedRecipesChange,
}: RecipesScreenProps) {
  const [ingredientsExpanded, setIngredientsExpanded] = useState(false);
  const [resetDialogOpen, setResetDialogOpen] = useState(false);
  const [saveNotice, setSaveNotice] = useState<{
    optionId: string;
    message: string;
    kind: "pending" | "success" | "error";
  } | null>(null);
  const [savingOptionId, setSavingOptionId] = useState<string | null>(null);
  const captureAbortControllerRef = useRef<AbortController | null>(null);
  const saveInFlightRef = useRef(false);
  const mountedRef = useRef(true);
  const saveConfirmationTimeoutRef = useRef<number | null>(null);
  const startOverButtonRef = useRef<HTMLButtonElement>(null);
  const cancelResetButtonRef = useRef<HTMLButtonElement>(null);
  const confirmResetButtonRef = useRef<HTMLButtonElement>(null);
  const recipeList = Object.values(recipes);
  const failureList = Object.values(failures);
  const optionNames = useMemo(
    () => new Map(options.map((option) => [option.id, option.name])),
    [options],
  );
  const activeRecipe =
    (activeRecipeId ? recipes[activeRecipeId] : undefined) ?? recipeList[0];

  useEffect(() => {
    if (!resetDialogOpen) return;

    cancelResetButtonRef.current?.focus();
    function handleDialogKeyDown(event: globalThis.KeyboardEvent) {
      if (event.key === "Escape") {
        event.preventDefault();
        cancelReset();
        return;
      }
      if (event.key !== "Tab") return;

      const first = cancelResetButtonRef.current;
      const last = confirmResetButtonRef.current;
      if (!first || !last) return;
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }

    document.addEventListener("keydown", handleDialogKeyDown);
    return () => document.removeEventListener("keydown", handleDialogKeyDown);
  }, [resetDialogOpen]);

  useEffect(() => {
    mountedRef.current = true;

    return () => {
      mountedRef.current = false;
      captureAbortControllerRef.current?.abort();
      captureAbortControllerRef.current = null;
      if (saveConfirmationTimeoutRef.current !== null) {
        window.clearTimeout(saveConfirmationTimeoutRef.current);
      }
    };
  }, []);

  function cancelReset() {
    setResetDialogOpen(false);
    startOverButtonRef.current?.focus({ preventScroll: true });
  }

  if (!activeRecipe) {
    return (
      <section aria-labelledby="recipes-title">
        <ScreenHeader
          kicker="Step 4 — Cook"
          title={<span id="recipes-title">No recipes returned</span>}
          intro="The recipe agents did not return a usable recipe. Your selected ideas are still here, so you can go back and try again."
        />
        <RecipeFailuresNotice
          completedCount={0}
          failures={failureList}
          optionNames={optionNames}
          onRetryFailed={onRetryFailed}
        />
        <div className="recipe-footer">
          <button className="btn btn-secondary btn-lg" type="button" onClick={onBack}>
            <ArrowLeft aria-hidden="true" size={16} />
            Back to recipe ideas
          </button>
        </div>
      </section>
    );
  }

  const activeOption = options.find((option) => option.id === activeRecipe.optionId);
  const activeNutrition = activeRecipe.nutrition ?? activeOption?.nutrition ?? null;
  const displayedRecipe =
    activeRecipe.nutrition === activeNutrition
      ? activeRecipe
      : { ...activeRecipe, nutrition: activeNutrition };
  const completeSteps = completedSteps[activeRecipe.optionId] ?? [];

  async function saveSnapshot() {
    if (saveInFlightRef.current) return;
    saveInFlightRef.current = true;

    const snapshotRecipe = displayedRecipe;
    const snapshotProgress = {
      doneStepNumbers: [...completeSteps],
      ingredientsExpanded,
    };
    const snapshotIngredientSources = captureIngredientSources(
      snapshotRecipe.ingredients,
      confirmedIngredients,
    );
    const previewArtifactId = activeOption?.previewArtifactId ?? null;
    if (saveConfirmationTimeoutRef.current !== null) {
      window.clearTimeout(saveConfirmationTimeoutRef.current);
      saveConfirmationTimeoutRef.current = null;
    }
    setSaveNotice({
      optionId: snapshotRecipe.optionId,
      kind: "pending",
      message: "Saving recipe…",
    });
    setSavingOptionId(snapshotRecipe.optionId);

    let captureController: AbortController | null = null;
    let photo: string | null = null;
    try {
      if (previewArtifactId) {
        captureController = new AbortController();
        captureAbortControllerRef.current = captureController;
        try {
          photo = await captureDishPhotoThumbnail(
            previewUrl(previewArtifactId),
            captureController.signal,
          );
        } catch {
          photo = null;
        }

        if (captureController.signal.aborted || !mountedRef.current) return;
      }

      const result = saveRecipe(snapshotRecipe, {
        photo,
        progress: snapshotProgress,
        ingredientSources: snapshotIngredientSources,
      });
      if (!mountedRef.current) return;
      onSavedRecipesChange(result.entries);

      if (!result.ok) {
        setSaveNotice({
          optionId: snapshotRecipe.optionId,
          kind: "error",
          message: "Couldn’t save this recipe. Check browser storage and try again.",
        });
        return;
      }

      setSaveNotice({
        optionId: snapshotRecipe.optionId,
        kind: "success",
        message: "Saved",
      });
      saveConfirmationTimeoutRef.current = window.setTimeout(() => {
        setSaveNotice((current) =>
          current?.optionId === snapshotRecipe.optionId && current.kind === "success"
            ? null
            : current,
        );
        saveConfirmationTimeoutRef.current = null;
      }, 2500);
    } finally {
      if (captureAbortControllerRef.current === captureController) {
        captureAbortControllerRef.current = null;
      }
      saveInFlightRef.current = false;
      if (mountedRef.current) setSavingOptionId(null);
    }
  }

  function downloadActiveRecipe() {
    if (downloadRecipeMarkdown(displayedRecipe)) {
      setSaveNotice(null);
      return;
    }

    setSaveNotice({
      optionId: activeRecipe.optionId,
      kind: "error",
      message: "Couldn’t download this recipe. Try again in a browser window.",
    });
  }

  function moveRecipeTab(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    let nextIndex: number | null = null;
    if (event.key === "ArrowRight") nextIndex = (index + 1) % recipeList.length;
    if (event.key === "ArrowLeft") {
      nextIndex = (index - 1 + recipeList.length) % recipeList.length;
    }
    if (event.key === "Home") nextIndex = 0;
    if (event.key === "End") nextIndex = recipeList.length - 1;
    if (nextIndex === null) return;

    event.preventDefault();
    const nextRecipe = recipeList[nextIndex];
    onSetActiveRecipe(nextRecipe.optionId);
    document.getElementById(`recipe-tab-${nextRecipe.optionId}`)?.focus();
  }

  return (
    <section aria-labelledby="recipes-title">
      <ScreenHeader
        kicker="Step 4 — Cook"
        showRule={false}
        title={
          <span id="recipes-title">
            {recipeList.length === 1
              ? "Your recipe, ready"
              : `Your ${recipeList.length} recipes, ready`}
          </span>
        }
      />

      {failureList.length ? (
        <RecipeFailuresNotice
          completedCount={recipeList.length}
          failures={failureList}
          optionNames={optionNames}
          onRetryFailed={onRetryFailed}
        />
      ) : null}

      {recipeList.length > 1 ? (
        <div className="recipe-tabs" role="tablist" aria-label="Completed recipes">
          {recipeList.map((recipe, index) => (
            <button
              className="recipe-tab"
              id={`recipe-tab-${recipe.optionId}`}
              type="button"
              role="tab"
              aria-selected={activeRecipe.optionId === recipe.optionId}
              aria-controls={`recipe-panel-${recipe.optionId}`}
              tabIndex={activeRecipe.optionId === recipe.optionId ? 0 : -1}
              onClick={() => onSetActiveRecipe(recipe.optionId)}
              onKeyDown={(event) => moveRecipeTab(event, index)}
              key={recipe.optionId}
            >
              {recipe.name}
            </button>
          ))}
        </div>
      ) : null}

      <div
        id={`recipe-panel-${activeRecipe.optionId}`}
        role={recipeList.length > 1 ? "tabpanel" : undefined}
        aria-labelledby={
          recipeList.length > 1 ? `recipe-tab-${activeRecipe.optionId}` : undefined
        }
      >
        <RecipeDetail
          recipe={displayedRecipe}
          confirmedIngredients={confirmedIngredients}
          completedSteps={completeSteps}
          onIngredientsExpandedChange={setIngredientsExpanded}
          onToggleStep={(stepNumber) => onToggleStep(activeRecipe.optionId, stepNumber)}
          actions={
            <>
              <div
                className="recipe-actions"
                role="group"
                aria-label={`${activeRecipe.name} actions`}
              >
                <button
                  className={`btn btn-secondary${savingOptionId !== null ? " is-pending" : ""}`}
                  type="button"
                  aria-busy={savingOptionId !== null}
                  disabled={savingOptionId !== null}
                  onClick={() => void saveSnapshot()}
                >
                  <Bookmark aria-hidden="true" size={16} />
                  {savingOptionId !== null ? "Saving…" : "Save recipe"}
                </button>
                <button
                  className="btn btn-secondary"
                  type="button"
                  onClick={downloadActiveRecipe}
                >
                  <Download aria-hidden="true" size={16} />
                  Download
                </button>
              </div>
              {saveNotice?.optionId === activeRecipe.optionId ? (
                <p
                  className={`recipe-action-notice${saveNotice.kind === "success" ? " is-success" : ""}`}
                  role={saveNotice.kind === "error" ? "alert" : "status"}
                  aria-live={saveNotice.kind === "error" ? undefined : "polite"}
                >
                  {saveNotice.message}
                </p>
              ) : null}
            </>
          }
        />
      </div>

      <div className="recipe-footer">
        <button className="btn btn-secondary btn-lg" type="button" onClick={onBack}>
          <ArrowLeft aria-hidden="true" size={16} />
          Back to recipe ideas
        </button>
        <button
          ref={startOverButtonRef}
          className="btn btn-ghost btn-lg"
          type="button"
          aria-haspopup="dialog"
          aria-expanded={resetDialogOpen}
          onClick={() => setResetDialogOpen(true)}
        >
          Start over with a new photo
        </button>
      </div>

      {resetDialogOpen ? (
        <div
          className="dialog-backdrop"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) cancelReset();
          }}
        >
          <div
            className="dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="start-over-dialog-title"
            aria-describedby="start-over-dialog-body"
          >
            <h2 className="dialog-title" id="start-over-dialog-title">
              Start over with a new photo?
            </h2>
            <p className="dialog-body" id="start-over-dialog-body">
              Starting over clears all recipes, chosen ideas, and checked steps. Your
              pantry defaults are kept.
            </p>
            <div className="dialog-actions">
              <button
                ref={cancelResetButtonRef}
                className="btn btn-primary"
                type="button"
                onClick={cancelReset}
              >
                Cancel
              </button>
              <button
                ref={confirmResetButtonRef}
                className="btn btn-secondary"
                type="button"
                onClick={onReset}
              >
                Start over
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </section>
  );
}

function RecipeFailuresNotice({
  completedCount,
  failures,
  optionNames,
  onRetryFailed,
}: {
  completedCount: number;
  failures: RecipeFailureView[];
  optionNames: Map<string, string>;
  onRetryFailed: () => void;
}) {
  if (failures.length === 0) return null;
  const hasRetryableFailure = failures.some((failure) => failure.retryable);

  return (
    <div className="notice notice-accent recipe-failures" role="status">
      <AlertTriangle className="notice-icon" aria-hidden="true" />
      <div className="recipe-failure-body">
        <strong>
          {completedCount > 0
            ? `${completedCount} ${plural(completedCount, "recipe")} completed; ${failures.length} ${plural(failures.length, "agent")} could not finish.`
            : `${failures.length} ${plural(failures.length, "recipe agent")} could not finish.`}
        </strong>
        {completedCount > 0 ? <p>Your successful recipes are ready below.</p> : null}
        <ul className="recipe-failure-list">
          {failures.map((failure) => (
            <li key={failure.optionId}>
              <strong>{optionNames.get(failure.optionId) ?? failure.optionId}:</strong>{" "}
              {safeFailureMessage(failure.message)}
            </li>
          ))}
        </ul>
        {hasRetryableFailure ? (
          <button className="btn btn-secondary" type="button" onClick={onRetryFailed}>
            <RefreshCw aria-hidden="true" size={16} />
            Retry failed recipes
          </button>
        ) : null}
      </div>
    </div>
  );
}

function safeFailureMessage(message: string): string {
  const normalized = message.replaceAll(/\p{C}/gu, " ").trim().replaceAll(/\s+/g, " ");
  if (!normalized) return "The recipe agent did not return a usable recipe.";
  return normalized.length > 240 ? `${normalized.slice(0, 239)}…` : normalized;
}
