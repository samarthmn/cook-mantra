"use client";

import {
  ArrowLeft,
  ArrowRight,
  Check,
  Clock3,
  RefreshCw,
  UtensilsCrossed,
} from "lucide-react";

import { StickyActionBar } from "@/components/layout/StickyActionBar";

import type { RecipeOptionView } from "../model/cook-session-state";
import { ScreenHeader } from "./ScreenHeader";

interface OptionsScreenProps {
  options: RecipeOptionView[];
  selectedOptionIds: string[];
  ingredientCount: number;
  ideasExhausted: boolean;
  moreIdeasUnavailableReason?: string | null;
  onToggleOption: (id: string) => void;
  onMoreIdeas: () => void;
  onEditIngredients: () => void;
  onCreateRecipes: () => void;
}

function plural(value: number, singular: string, pluralForm = `${singular}s`) {
  return value === 1 ? singular : pluralForm;
}

export function OptionsScreen({
  options,
  selectedOptionIds,
  ingredientCount,
  ideasExhausted,
  moreIdeasUnavailableReason = null,
  onToggleOption,
  onMoreIdeas,
  onEditIngredients,
  onCreateRecipes,
}: OptionsScreenProps) {
  const selectedCount = selectedOptionIds.length;
  const isEmpty = options.length === 0;

  return (
    <section aria-labelledby="options-title">
      <ScreenHeader
        kicker="Step 3 — Choose"
        title={
          <span id="options-title">
            {isEmpty ? "No" : options.length} {plural(options.length, "idea")} from your{" "}
            {ingredientCount} {plural(ingredientCount, "ingredient")}
          </span>
        }
        intro={
          isEmpty
            ? "Nothing here yet. A short ingredient list is the usual reason — the agents only suggest dishes they can actually see you cooking."
            : "Pick one or more. After a recipe is written, Cook Mantra may create an AI image of the finished dish."
        }
      />

      {options.length ? (
        <div className="option-grid">
          {options.map((option, index) => (
            <OptionCard
              key={option.id}
              option={option}
              index={index}
              selected={selectedOptionIds.includes(option.id)}
              ingredientCount={ingredientCount}
              onToggle={() => onToggleOption(option.id)}
            />
          ))}
        </div>
      ) : (
        <NoIdeasState
          ingredientCount={ingredientCount}
          onEditIngredients={onEditIngredients}
        />
      )}

      {ideasExhausted ? (
        <div className="notice notice-plain" role="status">
          No fresh ideas left for these ingredients — recipes already shown are never
          repeated. Try editing your ingredient list.
        </div>
      ) : null}

      {moreIdeasUnavailableReason ? (
        <div className="notice notice-plain" role="status">
          {moreIdeasUnavailableReason}
        </div>
      ) : null}

      <div className="screen-secondary-actions">
        <button className="btn btn-ghost" type="button" onClick={onEditIngredients}>
          <ArrowLeft aria-hidden="true" size={16} />
          Edit ingredients
        </button>
      </div>

      <StickyActionBar
        summary={
          isEmpty ? "No ideas yet" : `${selectedCount} of ${options.length} selected`
        }
        hint={
          isEmpty
            ? "Add ingredients, or ask the agents for another pass"
            : selectedCount === 0
              ? "Pick at least one dish"
              : "Each recipe is written by its own agent, in parallel"
        }
        actions={
          <>
            <button
              className="btn btn-secondary btn-lg"
              type="button"
              disabled={ideasExhausted || Boolean(moreIdeasUnavailableReason)}
              onClick={onMoreIdeas}
            >
              <RefreshCw aria-hidden="true" size={16} />
              {ideasExhausted
                ? "No more ideas"
                : moreIdeasUnavailableReason
                  ? "More ideas unavailable"
                  : isEmpty
                    ? "Try again"
                    : "More ideas"}
            </button>
            {isEmpty ? null : (
              <button
                className="btn btn-primary btn-lg"
                type="button"
                disabled={selectedCount === 0}
                onClick={onCreateRecipes}
              >
                Create {selectedCount} {plural(selectedCount, "recipe")}
                <ArrowRight aria-hidden="true" size={16} />
              </button>
            )}
          </>
        }
      />
    </section>
  );
}

// A thin ingredient list is by far the most common reason a batch comes back
// empty, so the copy leads with that and only blames preferences when the
// pantry is clearly not the problem.
const THIN_PANTRY_LIMIT = 5;

function NoIdeasState({
  ingredientCount,
  onEditIngredients,
}: {
  ingredientCount: number;
  onEditIngredients: () => void;
}) {
  const thinPantry = ingredientCount <= THIN_PANTRY_LIMIT;

  return (
    <div className="empty-state">
      <UtensilsCrossed className="empty-state-icon" aria-hidden="true" />
      <h2 className="empty-state-title">
        {thinPantry ? "Not enough to cook with yet" : "Nothing matched this time"}
      </h2>
      <p className="empty-state-body">
        {thinPantry
          ? `${ingredientCount === 2 ? "Two ingredients is a thin pantry" : `${ingredientCount} ${plural(ingredientCount, "ingredient")} is a thin pantry`} — there is no honest dish to build from it. Add a few more and the ideas come back.`
          : "Your ingredients are fine, but nothing came back that respects every preference. Relax a preference — a wider diet, fewer allergen exclusions, a different spice level — or add another ingredient, then try again."}
      </p>
      {thinPantry ? (
        <ul className="empty-state-hints">
          <li>A base — rice, dal, pasta, bread, potatoes</li>
          <li>A vegetable or two</li>
          <li>A protein — eggs, paneer, chicken, beans</li>
        </ul>
      ) : (
        <p className="empty-state-body">
          You can also relax a preference and try again with the same ingredients.
        </p>
      )}
      <button
        className="btn btn-primary btn-lg"
        type="button"
        onClick={onEditIngredients}
      >
        Add more ingredients
        <ArrowRight aria-hidden="true" size={16} />
      </button>
    </div>
  );
}

interface OptionCardProps {
  option: RecipeOptionView;
  index: number;
  selected: boolean;
  ingredientCount: number;
  onToggle: () => void;
}

function OptionCard({
  option,
  index,
  selected,
  ingredientCount,
  onToggle,
}: OptionCardProps) {
  const displayIndex = String(index + 1).padStart(2, "0");
  const displayBatch = String(option.batchNumber).padStart(2, "0");

  return (
    <button
      className="option-card"
      type="button"
      aria-pressed={selected}
      onClick={onToggle}
    >
      <span className="option-card-masthead" aria-hidden="true">
        <UtensilsCrossed className="option-card-masthead-icon" />
        <span className="option-card-index">
          {displayIndex}
          <small>batch {displayBatch}</small>
        </span>
        <span className="option-card-selected-mark">
          <Check />
        </span>
      </span>
      <span className="option-card-content">
        <span className="option-card-heading">
          <span className="option-card-cuisine">{option.cuisine}</span>
          {option.batchNumber > 1 ? (
            <span className="tag tag-accent">new batch</span>
          ) : null}
        </span>
        <span className="option-card-title">{option.name}</span>
        {option.summary ? (
          <span className="option-card-summary">{option.summary}</span>
        ) : null}
        <span className="option-card-meta">
          <span className="meta-with-icon">
            <Clock3 aria-hidden="true" size={14} />
            {option.totalMinutes} min
          </span>
          <span>{option.difficulty}</span>
        </span>
        <span className="option-card-tags">
          <span className="tag tag-neutral">
            uses {Math.min(option.usedIngredients.length, ingredientCount)} of{" "}
            {ingredientCount}
          </span>
        </span>
        {option.missingIngredients.length ? (
          <span className="option-card-honesty option-card-missing">
            <strong>Missing:</strong>{" "}
            {option.missingIngredients.map((ingredient) => ingredient.name).join(", ")}
          </span>
        ) : null}
        {option.optionalIngredients.length ? (
          <span className="option-card-honesty option-card-optional">
            Optional:{" "}
            {option.optionalIngredients.map((ingredient) => ingredient.name).join(", ")}
          </span>
        ) : null}
      </span>
    </button>
  );
}
