"use client";

import { AlertTriangle, Check, ChevronDown, Clock3, UsersRound } from "lucide-react";
import { useState, type ReactNode } from "react";

import type {
  CompleteRecipeView,
  IngredientView,
  NutritionView,
  RecipeIngredientView,
  RecipeStepView,
} from "../model/cook-session-state";

export interface RecipeDetailProps {
  recipe: CompleteRecipeView;
  confirmedIngredients?: readonly IngredientView[];
  nutrition?: NutritionView | null;
  completedSteps?: readonly number[];
  onToggleStep?: (stepNumber: number) => void;
  actions?: ReactNode;
  idPrefix?: string;
  showHeader?: boolean;
}

export function RecipeDetail({
  recipe,
  confirmedIngredients = [],
  nutrition = recipe.nutrition,
  completedSteps = [],
  onToggleStep,
  actions,
  idPrefix = `recipe-${recipe.optionId}`,
  showHeader = true,
}: RecipeDetailProps) {
  const [ingredientsExpanded, setIngredientsExpanded] = useState(false);
  const ingredientListId = `${idPrefix}-ingredients-list`;
  const ingredientsHeadingId = `${idPrefix}-ingredients-heading`;
  const stepsAreInteractive = onToggleStep !== undefined;

  return (
    <>
      {showHeader ? <hr className="section-rule" /> : null}
      {showHeader ? (
        <div className="recipe-title-row">
          <h2 className="recipe-title">{recipe.name}</h2>
          <div className="recipe-meta">
            <span className="tag tag-accent">{recipe.cuisine}</span>
            <span className="meta-with-icon">
              <Clock3 aria-hidden="true" size={15} />
              {recipe.totalMinutes} min
            </span>
            <span className="meta-with-icon">
              <UsersRound aria-hidden="true" size={15} />
              serves {recipe.servings}
            </span>
          </div>
        </div>
      ) : null}
      {actions}
      {recipe.assumptions.length ? (
        <p className="recipe-assumptions">Assumes: {recipe.assumptions.join("; ")}</p>
      ) : null}
      {recipe.warnings.length ? (
        <div
          aria-label="Recipe warnings"
          className="notice notice-accent recipe-warnings"
          role="status"
        >
          <AlertTriangle className="notice-icon" aria-hidden="true" />
          <div className="recipe-warning-body">
            <strong className="recipe-warning-title">Before you cook</strong>
            <ul className="recipe-warning-list">
              {recipe.warnings.map((warning, index) => (
                <li key={`${index}-${warning}`}>{warning}</li>
              ))}
            </ul>
          </div>
        </div>
      ) : null}

      <div className="recipe-layout">
        <aside className="recipe-ingredients" aria-labelledby={ingredientsHeadingId}>
          <h3
            className="panel-heading recipe-ingredients-heading"
            id={ingredientsHeadingId}
          >
            <span className="recipe-ingredients-heading-static">Ingredients</span>
            <button
              className="recipe-ingredients-disclosure"
              type="button"
              aria-controls={ingredientListId}
              aria-expanded={ingredientsExpanded}
              onClick={() => setIngredientsExpanded((expanded) => !expanded)}
            >
              <span>Ingredients ({recipe.ingredients.length})</span>
              <ChevronDown aria-hidden="true" size={18} />
            </button>
          </h3>
          <div
            className="recipe-ingredient-list"
            id={ingredientListId}
            hidden={!ingredientsExpanded}
          >
            {recipe.ingredients.map((ingredient, index) => {
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

          {nutrition ? (
            <div className="recipe-nutrition">
              <h3 className="panel-heading">Per serving — estimate</h3>
              <div className="nutrition-strip">
                <NutritionItem value={nutrition.caloriesKcal} label="kcal" />
                <NutritionItem value={`${nutrition.proteinG}g`} label="protein" />
                <NutritionItem value={`${nutrition.carbohydratesG}g`} label="carbs" />
                <NutritionItem value={`${nutrition.fatG}g`} label="fat" />
              </div>
            </div>
          ) : null}
          <div className="recipe-notices">
            <p className="nutrition-warning">{recipe.allergenNotice}</p>
            <p className="nutrition-disclaimer">{recipe.nutritionNotice}</p>
          </div>
        </aside>

        <div className="recipe-method">
          <div className="method-heading">
            <h3 className="panel-heading">
              {stepsAreInteractive ? "Method — tap a step when done" : "Method"}
            </h3>
            {stepsAreInteractive ? (
              <span className="tag tag-accent">
                {completedSteps.length} / {recipe.steps.length} done
              </span>
            ) : null}
          </div>
          <div className="method-list">
            {recipe.steps.map((step) => {
              const isDone = completedSteps.includes(step.number);
              const content = <MethodStepContent step={step} isDone={isDone} />;

              return onToggleStep ? (
                <button
                  className="method-step"
                  type="button"
                  aria-pressed={isDone}
                  onClick={() => onToggleStep(step.number)}
                  key={step.number}
                >
                  {content}
                </button>
              ) : (
                <div className="method-step method-step-readonly" key={step.number}>
                  {content}
                </div>
              );
            })}
          </div>

          <div className="recipe-notes-grid">
            {recipe.tips.length ? (
              <div className="recipe-note recipe-note-accent">
                <h3 className="panel-heading">Tips</h3>
                {recipe.tips.map((tip, index) => (
                  <p key={`${index}-${tip}`}>{tip}</p>
                ))}
              </div>
            ) : null}
            {recipe.substitutions.length ? (
              <div className="recipe-note">
                <h3 className="panel-heading">Substitutions</h3>
                {recipe.substitutions.map((substitution, index) => (
                  <p key={`${index}-${substitution}`}>{substitution}</p>
                ))}
              </div>
            ) : null}
          </div>
        </div>
      </div>
    </>
  );
}

function MethodStepContent({
  step,
  isDone,
}: {
  step: RecipeStepView;
  isDone: boolean;
}) {
  return (
    <>
      <span className="method-step-number" aria-hidden="true">
        {isDone ? <Check size={15} /> : step.number}
      </span>
      <span className="method-step-copy">
        <span className="method-step-text">{step.instruction}</span>
        {step.doneWhen ? (
          <span className="method-step-done-when">Done when — {step.doneWhen}</span>
        ) : null}
        {step.durationMinutes !== null || step.heatLevel ? (
          <span className="method-step-meta">
            {step.durationMinutes !== null ? (
              <span className="method-step-duration">
                <Clock3 aria-hidden="true" size={13} />
                {step.durationMinutes} min
              </span>
            ) : null}
            {step.heatLevel ? (
              <span className="method-step-heat">{step.heatLevel} heat</span>
            ) : null}
          </span>
        ) : null}
      </span>
    </>
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

function ingredientStatus(
  ingredient: RecipeIngredientView,
  confirmedIngredients: readonly IngredientView[],
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
