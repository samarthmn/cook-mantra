"use client";

import Image from "next/image";
import { useState } from "react";
import {
  AlertTriangle,
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
  previewUrl: (artifactId: string) => string;
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
  previewUrl,
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
            : "Pick one or more. Every image is an AI illustration — your dish may look different. Nutrition is an estimate, not medical advice."
        }
      />

      {options.length ? (
        <div className="option-grid">
          {options.map((option) => (
            <OptionCard
              key={option.id}
              option={option}
              selected={selectedOptionIds.includes(option.id)}
              ingredientCount={ingredientCount}
              previewUrl={previewUrl}
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
          ← Edit ingredients
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
                  ? "Start fresh for more"
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
          : "Your ingredients are fine, but nothing came back that respects every preference. Relax a preference — more time, a wider diet, fewer allergen exclusions — or add another ingredient, then try again."}
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
  selected: boolean;
  ingredientCount: number;
  previewUrl: (artifactId: string) => string;
  onToggle: () => void;
}

function OptionCard({
  option,
  selected,
  ingredientCount,
  previewUrl,
  onToggle,
}: OptionCardProps) {
  const nutrition = option.nutrition;
  const [failedPreviewId, setFailedPreviewId] = useState<string | null>(null);
  const previewArtifactId = option.previewArtifactId;
  const previewAvailable =
    previewArtifactId !== null && previewArtifactId !== failedPreviewId;

  return (
    <button
      className="option-card"
      type="button"
      aria-pressed={selected}
      onClick={onToggle}
    >
      <span className="option-card-media">
        {previewAvailable && previewArtifactId ? (
          <Image
            className="option-card-image grayscale-media"
            src={previewUrl(previewArtifactId)}
            alt={`${option.name}, AI-generated illustration`}
            fill
            sizes="(max-width: 640px) 100vw, (max-width: 1024px) 50vw, 33vw"
            unoptimized
            onError={() => setFailedPreviewId(previewArtifactId)}
          />
        ) : (
          <UtensilsCrossed className="option-card-placeholder" aria-hidden="true" />
        )}
        {previewAvailable && previewArtifactId ? (
          <span className="tag tag-neutral option-card-ai-label">AI illustration</span>
        ) : null}
        <span className="option-card-selected-mark" aria-hidden="true">
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
        {nutrition ? (
          <span
            className="nutrition-strip"
            role="group"
            aria-label="Estimated nutrition"
          >
            <NutritionItem value={nutrition.caloriesKcal} label="kcal" />
            <NutritionItem value={`${nutrition.proteinG}g`} label="protein" />
            <NutritionItem value={`${nutrition.carbohydratesG}g`} label="carbs" />
            <NutritionItem value={`${nutrition.fatG}g`} label="fat" />
          </span>
        ) : (
          <span className="option-card-honesty text-muted">
            Nutrition estimate unavailable
          </span>
        )}
        <span className="option-card-tags">
          <span className="tag tag-neutral">
            uses {Math.min(option.usedIngredients.length, ingredientCount)} of{" "}
            {ingredientCount}
          </span>
          {nutrition?.dietTags.map((tag, index) => (
            <span className="tag tag-neutral" key={`${index}-${tag}`}>
              {tag}
            </span>
          ))}
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
        {nutrition?.allergenWarnings.length ? (
          <span className="option-card-honesty option-card-allergens">
            <AlertTriangle aria-hidden="true" size={14} />
            Contains {nutrition.allergenWarnings.join(" and ")}
          </span>
        ) : null}
        {option.warnings.length ? (
          <span className="option-card-honesty option-card-warnings">
            <AlertTriangle aria-hidden="true" size={14} />
            {option.warnings.join(" ")}
          </span>
        ) : null}
      </span>
    </button>
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
