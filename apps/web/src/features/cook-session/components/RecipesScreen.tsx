"use client";

import {
  AlertTriangle,
  ArrowLeft,
  Check,
  ChevronDown,
  Clock3,
  RefreshCw,
  UsersRound,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";

import type {
  CompleteRecipeView,
  IngredientView,
  RecipeFailureView,
  RecipeIngredientView,
  RecipeOptionView,
} from "../model/cook-session-state";
import { ScreenHeader } from "./ScreenHeader";

interface RecipesScreenProps {
  recipes: Record<string, CompleteRecipeView>;
  failures: Record<string, RecipeFailureView>;
  options: RecipeOptionView[];
  confirmedIngredients: IngredientView[];
  activeRecipeId: string | null;
  completedSteps: Record<string, number[]>;
  onSetActiveRecipe: (optionId: string) => void;
  onToggleStep: (optionId: string, stepNumber: number) => void;
  onRetryFailed: () => void;
  onBack: () => void;
  onReset: () => void;
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
  onSetActiveRecipe,
  onToggleStep,
  onRetryFailed,
  onBack,
  onReset,
}: RecipesScreenProps) {
  const [ingredientsExpanded, setIngredientsExpanded] = useState(false);
  const [resetDialogOpen, setResetDialogOpen] = useState(false);
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
  const activeNutrition = activeRecipe.nutrition ?? activeOption?.nutrition;
  const completeSteps = completedSteps[activeRecipe.optionId] ?? [];
  const ingredientListId = `recipe-ingredients-list-${activeRecipe.optionId}`;

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
        <hr className="section-rule" />
        <div className="recipe-title-row">
          <h2 className="recipe-title">{activeRecipe.name}</h2>
          <div className="recipe-meta">
            <span className="tag tag-accent">{activeRecipe.cuisine}</span>
            <span className="meta-with-icon">
              <Clock3 aria-hidden="true" size={15} />
              {activeRecipe.totalMinutes} min
            </span>
            <span className="meta-with-icon">
              <UsersRound aria-hidden="true" size={15} />
              serves {activeRecipe.servings}
            </span>
          </div>
        </div>
        {activeRecipe.assumptions.length ? (
          <p className="recipe-assumptions">
            Assumes: {activeRecipe.assumptions.join("; ")}
          </p>
        ) : null}
        {activeRecipe.warnings.length ? (
          <div
            aria-label="Recipe warnings"
            className="notice notice-accent recipe-warnings"
            role="status"
          >
            <AlertTriangle className="notice-icon" aria-hidden="true" />
            <div className="recipe-warning-body">
              <strong className="recipe-warning-title">Before you cook</strong>
              <ul className="recipe-warning-list">
                {activeRecipe.warnings.map((warning, index) => (
                  <li key={`${index}-${warning}`}>{warning}</li>
                ))}
              </ul>
            </div>
          </div>
        ) : null}

        <div className="recipe-layout">
          <aside className="recipe-ingredients" aria-labelledby="ingredients-heading">
            <h3
              className="panel-heading recipe-ingredients-heading"
              id="ingredients-heading"
            >
              <span className="recipe-ingredients-heading-static">Ingredients</span>
              <button
                className="recipe-ingredients-disclosure"
                type="button"
                aria-controls={ingredientListId}
                aria-expanded={ingredientsExpanded}
                onClick={() => setIngredientsExpanded((expanded) => !expanded)}
              >
                <span>Ingredients ({activeRecipe.ingredients.length})</span>
                <ChevronDown aria-hidden="true" size={18} />
              </button>
            </h3>
            <div
              className="recipe-ingredient-list"
              id={ingredientListId}
              hidden={!ingredientsExpanded}
            >
              {activeRecipe.ingredients.map((ingredient, index) => {
                const status = ingredientStatus(ingredient, confirmedIngredients);
                return (
                  <div
                    className="recipe-ingredient-row"
                    key={`${ingredient.name}-${index}`}
                  >
                    <span className="recipe-ingredient-quantity">
                      {ingredient.quantity}
                    </span>
                    <span className="recipe-ingredient-copy">
                      <span className="recipe-ingredient-name">{ingredient.name}</span>
                      {ingredient.substitution ? (
                        <span className="recipe-ingredient-substitution">
                          Substitute: {ingredient.substitution}
                        </span>
                      ) : null}
                    </span>
                    <span className={`tag ${status.className}`}>{status.label}</span>
                  </div>
                );
              })}
            </div>

            {activeNutrition ? (
              <div className="recipe-nutrition">
                <h3 className="panel-heading">Per serving — estimate</h3>
                <div className="nutrition-strip">
                  <NutritionItem value={activeNutrition.caloriesKcal} label="kcal" />
                  <NutritionItem
                    value={`${activeNutrition.proteinG}g`}
                    label="protein"
                  />
                  <NutritionItem
                    value={`${activeNutrition.carbohydratesG}g`}
                    label="carbs"
                  />
                  <NutritionItem value={`${activeNutrition.fatG}g`} label="fat" />
                </div>
              </div>
            ) : null}
            <div className="recipe-notices">
              <p className="nutrition-warning">{activeRecipe.allergenNotice}</p>
              <p className="nutrition-disclaimer">{activeRecipe.nutritionNotice}</p>
            </div>
          </aside>

          <div className="recipe-method">
            <div className="method-heading">
              <h3 className="panel-heading">Method — tap a step when done</h3>
              <span className="tag tag-accent">
                {completeSteps.length} / {activeRecipe.steps.length} done
              </span>
            </div>
            <div className="method-list">
              {activeRecipe.steps.map((step) => {
                const isDone = completeSteps.includes(step.number);
                return (
                  <button
                    className="method-step"
                    type="button"
                    aria-pressed={isDone}
                    onClick={() => onToggleStep(activeRecipe.optionId, step.number)}
                    key={step.number}
                  >
                    <span className="method-step-number" aria-hidden="true">
                      {isDone ? <Check size={15} /> : step.number}
                    </span>
                    <span className="method-step-copy">
                      <span className="method-step-text">{step.instruction}</span>
                      {step.doneWhen ? (
                        <span className="method-step-done-when">
                          Done when — {step.doneWhen}
                        </span>
                      ) : null}
                      {step.durationMinutes || step.heatLevel ? (
                        <span className="method-step-meta">
                          {step.durationMinutes ? (
                            <span className="method-step-duration">
                              <Clock3 aria-hidden="true" size={13} />
                              {step.durationMinutes} min
                            </span>
                          ) : null}
                          {step.heatLevel ? (
                            <span className="method-step-heat">
                              {step.heatLevel} heat
                            </span>
                          ) : null}
                        </span>
                      ) : null}
                    </span>
                  </button>
                );
              })}
            </div>

            <div className="recipe-notes-grid">
              {activeRecipe.tips.length ? (
                <div className="recipe-note recipe-note-accent">
                  <h3 className="panel-heading">Tips</h3>
                  {activeRecipe.tips.map((tip, index) => (
                    <p key={`${index}-${tip}`}>{tip}</p>
                  ))}
                </div>
              ) : null}
              {activeRecipe.substitutions.length ? (
                <div className="recipe-note">
                  <h3 className="panel-heading">Substitutions</h3>
                  {activeRecipe.substitutions.map((substitution, index) => (
                    <p key={`${index}-${substitution}`}>{substitution}</p>
                  ))}
                </div>
              ) : null}
            </div>
          </div>
        </div>
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

function NutritionItem({ value, label }: { value: number | string; label: string }) {
  return (
    <span>
      <span className="nutrition-value">{value}</span>
      <span className="nutrition-label">{label}</span>
    </span>
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

function ingredientStatus(
  ingredient: RecipeIngredientView,
  confirmedIngredients: IngredientView[],
) {
  if (ingredient.availability === "optional") {
    return { label: "optional", className: "tag-accent-2" };
  }
  if (ingredient.availability === "missing") {
    return { label: "missing", className: "tag-outline" };
  }

  const recipeName = normalizeIngredientName(ingredient.name);
  const source = confirmedIngredients.find((confirmed) => {
    if (!confirmed.confirmed) return false;
    const confirmedName = normalizeIngredientName(confirmed.name);
    return confirmedName.includes(recipeName) || recipeName.includes(confirmedName);
  })?.source;

  if (source === "detected") {
    return { label: "detected", className: "tag-neutral" };
  }
  if (source === "pantry_suggestion") {
    return { label: "pantry", className: "tag-neutral" };
  }
  if (source === "user_added") {
    return { label: "added by you", className: "tag-neutral" };
  }
  return { label: "available", className: "tag-neutral" };
}

function normalizeIngredientName(name: string) {
  return name
    .toLocaleLowerCase()
    .split(",")[0]
    .replaceAll(/[^a-z ]/g, "")
    .trim();
}
