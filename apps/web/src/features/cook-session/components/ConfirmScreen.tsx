"use client";

import { useState, type FormEvent } from "react";
import {
  AlertTriangle,
  ArrowRight,
  Check,
  ChevronDown,
  Info,
  Plus,
  X,
} from "lucide-react";

import { StickyActionBar } from "@/components/layout/StickyActionBar";

import {
  confirmedIngredientCount,
  type IngredientView,
  type PreferenceView,
} from "../model/cook-session-state";
import { ScreenHeader } from "./ScreenHeader";

interface ConfirmScreenProps {
  ingredients: IngredientView[];
  ingredientNameErrors: Record<string, string>;
  preferences: PreferenceView;
  weakDetection: boolean;
  manualEntry?: boolean;
  onAddIngredient: (name: string) => void;
  onRenameIngredient: (id: string, name: string) => void;
  onRemoveIngredient: (id: string) => void;
  onToggleIngredient: (id: string) => void;
  onPreferenceChange: <Key extends keyof PreferenceView>(
    key: Key,
    value: PreferenceView[Key],
  ) => void;
  onRetake: () => void;
  onGenerate: () => void;
}

// Below this the agents have too little to work with, so the action bar says so
// before the user spends a generation round finding out.
const THIN_PANTRY_LIMIT = 5;

function plural(value: number, singular: string, pluralForm = `${singular}s`) {
  return value === 1 ? singular : pluralForm;
}

export function ConfirmScreen({
  ingredients,
  ingredientNameErrors,
  preferences,
  weakDetection,
  manualEntry = false,
  onAddIngredient,
  onRenameIngredient,
  onRemoveIngredient,
  onToggleIngredient,
  onPreferenceChange,
  onRetake,
  onGenerate,
}: ConfirmScreenProps) {
  const [newIngredient, setNewIngredient] = useState("");
  const [preferencesOpen, setPreferencesOpen] = useState(false);
  const listedIngredients = ingredients.filter(
    (ingredient) => ingredient.source !== "pantry_suggestion",
  );
  const pantryIngredients = ingredients.filter(
    (ingredient) => ingredient.source === "pantry_suggestion",
  );
  const confirmedCount = confirmedIngredientCount(ingredients);
  const hasNameErrors = Object.keys(ingredientNameErrors).length > 0;

  function submitIngredient(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!newIngredient.trim()) return;
    onAddIngredient(newIngredient);
    setNewIngredient("");
  }

  return (
    <section aria-labelledby="confirm-title">
      <ScreenHeader
        kicker="Step 2 — Confirm"
        title={<span id="confirm-title">Check what we found</span>}
        intro="Rename, remove, or add anything. Only ingredients you confirm here are used in recipes."
      />

      {manualEntry ? (
        <div className="notice notice-plain" role="status">
          <Info className="notice-icon" aria-hidden="true" />
          <div>
            <strong>List everything you actually have.</strong>
            <br />
            The more you list, the more the agents can suggest. Three or four items
            rarely make a dish worth cooking.
          </div>
        </div>
      ) : null}

      {weakDetection ? (
        <div className="notice notice-accent" role="status">
          <AlertTriangle className="notice-icon" aria-hidden="true" />
          <div>
            <strong>Detection was weak on this photo.</strong>
            <br />
            Check the item below, add anything missed, or{" "}
            <button
              className="btn btn-ghost btn-inline"
              type="button"
              onClick={onRetake}
            >
              retake the photo
            </button>
            .
          </div>
        </div>
      ) : null}

      <div className="confirm-grid">
        <div>
          <h2 className="panel-heading">
            {manualEntry ? "Your ingredients" : "Detected in your photo"}
          </h2>
          <p className="panel-note">
            {manualEntry
              ? "Add everything you have on hand. Edit or remove any item."
              : "Edit the name directly or remove an incorrect item."}
          </p>
          <div className="ingredient-list">
            {listedIngredients.length ? (
              listedIngredients.map((ingredient) => (
                <div className="ingredient-row" key={ingredient.id}>
                  <div className="ingredient-name-field">
                    <input
                      className="input ingredient-name-input"
                      aria-label={`Ingredient ${ingredient.name}`}
                      aria-describedby={
                        ingredientNameErrors[ingredient.id]
                          ? `ingredient-error-${ingredient.id}`
                          : undefined
                      }
                      aria-invalid={Boolean(ingredientNameErrors[ingredient.id])}
                      value={ingredient.name}
                      maxLength={80}
                      onChange={(event) =>
                        onRenameIngredient(ingredient.id, event.target.value)
                      }
                    />
                    {ingredientNameErrors[ingredient.id] ? (
                      <span
                        className="ingredient-field-error"
                        id={`ingredient-error-${ingredient.id}`}
                      >
                        {ingredientNameErrors[ingredient.id]}
                      </span>
                    ) : null}
                  </div>
                  <div className="ingredient-row-tags">
                    {ingredient.source === "user_added" ? (
                      <span className="tag tag-accent">added by you</span>
                    ) : null}
                    {ingredient.confidence !== null ? (
                      <>
                        {ingredient.confidence < 0.7 ? (
                          <span className="tag tag-outline">check this</span>
                        ) : null}
                        <span className="tag tag-neutral">
                          {Math.round(ingredient.confidence * 100)}%
                        </span>
                      </>
                    ) : null}
                  </div>
                  <button
                    className="btn btn-secondary btn-icon"
                    type="button"
                    aria-label={`Remove ${ingredient.name}`}
                    onClick={() => onRemoveIngredient(ingredient.id)}
                  >
                    <X aria-hidden="true" size={16} />
                  </button>
                </div>
              ))
            ) : (
              <div className="ingredient-empty">
                {manualEntry
                  ? "Nothing added yet — type your ingredients below."
                  : "Nothing detected — add ingredients below."}
              </div>
            )}
          </div>
          <form className="ingredient-add-form" onSubmit={submitIngredient}>
            <label className="visually-hidden" htmlFor="new-ingredient">
              Add an ingredient
            </label>
            <input
              className="input"
              id="new-ingredient"
              aria-label="Add an ingredient"
              placeholder="Add an ingredient…"
              value={newIngredient}
              maxLength={80}
              onChange={(event) => setNewIngredient(event.target.value)}
            />
            <button
              className="btn btn-secondary"
              type="submit"
              aria-label="Add ingredient"
            >
              <Plus aria-hidden="true" size={16} />
              Add
            </button>
          </form>
        </div>

        <div>
          <h2 className="panel-heading">
            Pantry staples — tick what you actually have
          </h2>
          <p className="panel-note">
            {manualEntry
              ? "These are common in most kitchens. Nothing is assumed until you tick it."
              : "These are common in most kitchens but weren't in your photo. Nothing is assumed until you tick it."}
          </p>
          <div className="pantry-list">
            {pantryIngredients.map((ingredient) => (
              <label className="pantry-row" key={ingredient.id}>
                <input
                  className="pantry-input"
                  type="checkbox"
                  aria-label={`${ingredient.name} pantry`}
                  checked={ingredient.confirmed}
                  onChange={() => onToggleIngredient(ingredient.id)}
                />
                <span className="pantry-checkbox" aria-hidden="true">
                  <Check />
                </span>
                <span className="pantry-label">{ingredient.name}</span>
                <span className="tag tag-neutral">pantry</span>
              </label>
            ))}
          </div>

          <div className="preferences">
            <button
              className="preferences-trigger"
              type="button"
              aria-expanded={preferencesOpen}
              aria-controls="preferences-panel"
              onClick={() => setPreferencesOpen((open) => !open)}
            >
              <span className="preferences-title">Preferences — optional</span>
              <span className="preferences-summary">
                {preferences.diet === "none"
                  ? "Any diet"
                  : preferences.diet === "vegetarian"
                    ? "Veg"
                    : "Vegan"}
                {` · serves ${preferences.servings} · ≤ ${preferences.maxMinutes} min · ${preferences.optionCount} ideas`}
              </span>
              <ChevronDown className="preferences-chevron" aria-hidden="true" />
            </button>
            {preferencesOpen ? (
              <div className="preferences-panel" id="preferences-panel">
                <div className="preferences-grid">
                  <PreferenceSegment
                    label="Diet"
                    name="diet"
                    value={preferences.diet}
                    options={[
                      ["none", "Any"],
                      ["vegetarian", "Veg"],
                      ["vegan", "Vegan"],
                    ]}
                    onChange={(value) =>
                      onPreferenceChange("diet", value as PreferenceView["diet"])
                    }
                  />
                  <PreferenceSegment
                    label="Servings"
                    name="servings"
                    value={String(preferences.servings)}
                    options={[
                      ["1", "1"],
                      ["2", "2"],
                      ["4", "4"],
                    ]}
                    onChange={(value) =>
                      onPreferenceChange(
                        "servings",
                        Number(value) as PreferenceView["servings"],
                      )
                    }
                  />
                  <PreferenceSegment
                    label="Max time"
                    name="max-time"
                    value={String(preferences.maxMinutes)}
                    options={[
                      ["30", "30 min"],
                      ["45", "45 min"],
                      ["60", "60 min"],
                    ]}
                    onChange={(value) =>
                      onPreferenceChange(
                        "maxMinutes",
                        Number(value) as PreferenceView["maxMinutes"],
                      )
                    }
                  />
                  <PreferenceSegment
                    label="Number of ideas"
                    name="option-count"
                    value={String(preferences.optionCount)}
                    options={[
                      ["3", "3"],
                      ["4", "4"],
                      ["6", "6"],
                    ]}
                    onChange={(value) =>
                      onPreferenceChange(
                        "optionCount",
                        Number(value) as PreferenceView["optionCount"],
                      )
                    }
                  />
                  <div className="field">
                    <label htmlFor="allergens">Avoid allergens</label>
                    <input
                      className="input"
                      id="allergens"
                      value={preferences.allergens}
                      placeholder="e.g. dairy, soy"
                      onChange={(event) =>
                        onPreferenceChange("allergens", event.target.value)
                      }
                    />
                  </div>
                </div>
              </div>
            ) : null}
          </div>
        </div>
      </div>

      <StickyActionBar
        summary={`${confirmedCount} ${plural(confirmedCount, "ingredient")} confirmed`}
        hint={
          confirmedCount === 0
            ? "Add at least one ingredient to continue"
            : hasNameErrors
              ? "Give every ingredient a different name"
              : confirmedCount <= THIN_PANTRY_LIMIT
                ? "Add a few more — short lists rarely make a dish"
                : "Detected + pantry + added by you"
        }
        actions={
          <button
            className="btn btn-primary btn-lg"
            type="button"
            disabled={confirmedCount === 0 || hasNameErrors}
            onClick={onGenerate}
          >
            Generate recipe ideas
            <ArrowRight aria-hidden="true" size={16} />
          </button>
        }
      />
    </section>
  );
}

interface PreferenceSegmentProps {
  label: string;
  name: string;
  value: string;
  options: Array<[value: string, label: string]>;
  onChange: (value: string) => void;
}

function PreferenceSegment({
  label,
  name,
  value,
  options,
  onChange,
}: PreferenceSegmentProps) {
  return (
    <fieldset className="field">
      <legend className="field-label">{label}</legend>
      <div className="seg">
        {options.map(([optionValue, optionLabel]) => (
          <label className="seg-opt" key={optionValue}>
            <input
              type="radio"
              name={name}
              value={optionValue}
              checked={value === optionValue}
              onChange={() => onChange(optionValue)}
            />
            {optionLabel}
          </label>
        ))}
      </div>
    </fieldset>
  );
}
