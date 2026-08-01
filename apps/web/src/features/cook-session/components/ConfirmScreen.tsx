"use client";

import {
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
  type FormEvent,
} from "react";
import { AlertTriangle, ArrowRight, Check, Info, Minus, Plus, X } from "lucide-react";

import { StickyActionBar } from "@/components/layout/StickyActionBar";

import {
  confirmedIngredientCount,
  MAX_CUISINE_PREFERENCES,
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
  onToggleAllPantry: (confirmed: boolean) => void;
  pantryStaples: string[];
  generatedContentExists?: boolean;
  onPantryStaplesChange: (names: string[]) => boolean;
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

const DIET_OPTIONS = [
  ["vegetarian", "Veg"],
  ["non-vegetarian", "Non-Veg"],
] as const;

const DIET_STYLE_OPTIONS: Record<
  PreferenceView["diet"],
  ReadonlyArray<readonly [value: string, label: string]>
> = {
  vegetarian: [
    ["", "Vegetarian"],
    ["vegan", "Vegan"],
    ["eggetarian", "Eggetarian"],
    ["jain", "Jain"],
  ],
  "non-vegetarian": [
    ["", "Any"],
    ["halal", "Halal"],
    ["kosher", "Kosher"],
  ],
};

const DIET_ADD_ONS = ["keto", "no onion or garlic"] as const;

const DIET_ADD_ON_LABELS: Record<(typeof DIET_ADD_ONS)[number], string> = {
  keto: "Keto",
  "no onion or garlic": "No onion or garlic",
};

const BASE_ALLERGENS = [
  "Dairy",
  "Eggs",
  "Peanuts",
  "Tree nuts",
  "Gluten",
  "Soy",
  "Shellfish",
  "Fish",
  "Sesame",
] as const;

const BASE_CUISINES = [
  "Indian",
  "Chinese",
  "American",
  "Mediterranean",
  "Mexican",
  "Italian",
  "Thai",
  "Japanese",
  "Korean",
  "Middle Eastern",
] as const;

const SERVING_OPTIONS = [
  ["1", "1"],
  ["2", "2"],
  ["4", "4"],
] as const;

const OPTION_COUNT_OPTIONS = [
  ["3", "3"],
  ["4", "4"],
  ["6", "6"],
] as const;

const SPICE_OPTIONS = [
  ["mild", "Mild"],
  ["medium", "Medium"],
  ["hot", "Hot"],
  ["extra-hot", "Extra hot"],
] as const;

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
  onToggleAllPantry,
  pantryStaples,
  generatedContentExists = false,
  onPantryStaplesChange,
  onPreferenceChange,
  onRetake,
  onGenerate,
}: ConfirmScreenProps) {
  const [newIngredient, setNewIngredient] = useState("");
  const [customAllergen, setCustomAllergen] = useState("");
  const [customCuisine, setCustomCuisine] = useState("");
  const [pantryEditorOpen, setPantryEditorOpen] = useState(false);
  const [pantryDraft, setPantryDraft] = useState<string[]>([]);
  const [newPantryStaple, setNewPantryStaple] = useState("");
  const [pantryEditorError, setPantryEditorError] = useState<string | null>(null);
  const [pantrySaveFailed, setPantrySaveFailed] = useState(false);
  const selectAllPantryRef = useRef<HTMLInputElement>(null);
  const pantryEditButtonRef = useRef<HTMLButtonElement>(null);
  const pantryDraftInputRefs = useRef<Array<HTMLInputElement | null>>([]);
  const newPantryStapleRef = useRef<HTMLInputElement>(null);
  const pantryEditorWasOpenRef = useRef(false);
  const pantryRemovalFocusRef = useRef<number | "add" | null>(null);
  const listedIngredients = ingredients.filter(
    (ingredient) => ingredient.source !== "pantry_suggestion",
  );
  const pantryIngredients = ingredients.filter(
    (ingredient) => ingredient.source === "pantry_suggestion",
  );
  const confirmedCount = confirmedIngredientCount(ingredients);
  const hasNameErrors = Object.keys(ingredientNameErrors).length > 0;
  const confirmedPantryCount = pantryIngredients.filter(
    (ingredient) => ingredient.confirmed,
  ).length;
  const allPantryConfirmed =
    pantryIngredients.length > 0 && confirmedPantryCount === pantryIngredients.length;
  const somePantryConfirmed = confirmedPantryCount > 0 && !allPantryConfirmed;
  const customAllergens = preferences.allergens.filter(
    (allergen) =>
      !BASE_ALLERGENS.some(
        (baseAllergen) =>
          baseAllergen.toLocaleLowerCase() === allergen.toLocaleLowerCase(),
      ),
  );
  const customCuisines = preferences.cuisines.filter(
    (cuisine) =>
      !BASE_CUISINES.some(
        (baseCuisine) =>
          baseCuisine.toLocaleLowerCase() === cuisine.toLocaleLowerCase(),
      ),
  );
  const cuisineLimitReached = preferences.cuisines.length >= MAX_CUISINE_PREFERENCES;

  useEffect(() => {
    if (selectAllPantryRef.current) {
      selectAllPantryRef.current.indeterminate = somePantryConfirmed;
    }
  }, [somePantryConfirmed]);

  useLayoutEffect(() => {
    if (pantryEditorOpen) {
      const removalTarget = pantryRemovalFocusRef.current;
      if (removalTarget !== null) {
        if (removalTarget === "add") {
          newPantryStapleRef.current?.focus();
        } else {
          pantryDraftInputRefs.current[removalTarget]?.focus();
        }
        pantryRemovalFocusRef.current = null;
      } else if (!pantryEditorWasOpenRef.current) {
        (pantryDraftInputRefs.current[0] ?? newPantryStapleRef.current)?.focus();
      }
    } else if (pantryEditorWasOpenRef.current) {
      pantryEditButtonRef.current?.focus();
    }

    pantryEditorWasOpenRef.current = pantryEditorOpen;
  }, [pantryDraft, pantryEditorOpen]);

  function submitIngredient(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!newIngredient.trim()) return;
    onAddIngredient(newIngredient);
    setNewIngredient("");
  }

  function toggleListPreference(
    key: "dietAddOns" | "allergens" | "cuisines",
    value: string,
  ) {
    const current = preferences[key];
    const normalizedValue = value.toLocaleLowerCase();
    const existingIndex = current.findIndex(
      (item) => item.toLocaleLowerCase() === normalizedValue,
    );
    if (
      existingIndex === -1 &&
      key === "cuisines" &&
      current.length >= MAX_CUISINE_PREFERENCES
    ) {
      return;
    }
    const next =
      existingIndex === -1
        ? [...current, value]
        : current.filter((_, index) => index !== existingIndex);
    onPreferenceChange(key, next);
  }

  function submitCustomAllergen(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const allergen = customAllergen.trim().replaceAll(/\s+/g, " ");
    if (!allergen) return;
    const alreadySelected = preferences.allergens.some(
      (selected) => selected.toLocaleLowerCase() === allergen.toLocaleLowerCase(),
    );
    if (!alreadySelected) {
      onPreferenceChange("allergens", [...preferences.allergens, allergen]);
    }
    setCustomAllergen("");
  }

  function submitCustomCuisine(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const cuisine = customCuisine.trim().replaceAll(/\s+/g, " ");
    if (!cuisine) return;
    const alreadySelected = preferences.cuisines.some(
      (selected) => selected.toLocaleLowerCase() === cuisine.toLocaleLowerCase(),
    );
    if (!alreadySelected && !cuisineLimitReached) {
      onPreferenceChange("cuisines", [...preferences.cuisines, cuisine]);
    }
    setCustomCuisine("");
  }

  function openPantryEditor() {
    setPantryDraft([...pantryStaples]);
    setNewPantryStaple("");
    setPantryEditorError(null);
    setPantryEditorOpen(true);
  }

  function closePantryEditor() {
    setPantryEditorOpen(false);
    setPantryEditorError(null);
  }

  function submitNewPantryStaple(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const staple = newPantryStaple.trim().replaceAll(/\s+/g, " ");
    if (!staple) return;
    if (
      pantryDraft.some(
        (name) => name.trim().toLocaleLowerCase() === staple.toLocaleLowerCase(),
      )
    ) {
      setPantryEditorError("Each pantry staple needs a different name.");
      return;
    }
    setPantryDraft((current) => [...current, staple]);
    setNewPantryStaple("");
    setPantryEditorError(null);
  }

  function removePantryStaple(index: number) {
    pantryRemovalFocusRef.current = index > 0 ? index - 1 : "add";
    setPantryDraft((current) => current.filter((_, itemIndex) => itemIndex !== index));
  }

  function savePantryDefaults() {
    const normalized = pantryDraft.map((name) => name.trim().replaceAll(/\s+/g, " "));
    if (normalized.some((name) => !name)) {
      setPantryEditorError("Pantry staple names cannot be empty.");
      return;
    }
    if (normalized.length === 0) {
      setPantryEditorError("Keep at least one pantry staple.");
      return;
    }
    const uniqueNames = new Set(normalized.map((name) => name.toLocaleLowerCase()));
    if (uniqueNames.size !== normalized.length) {
      setPantryEditorError("Each pantry staple needs a different name.");
      return;
    }
    setPantrySaveFailed(!onPantryStaplesChange(normalized));
    closePantryEditor();
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
                      <ConfidenceIndicator confidence={ingredient.confidence} />
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
          <div className="panel-heading-row">
            <h2 className="panel-heading">Pantry staples</h2>
            <button
              ref={pantryEditButtonRef}
              className="btn btn-ghost pantry-edit-button"
              type="button"
              aria-expanded={pantryEditorOpen}
              aria-controls={pantryEditorOpen ? "pantry-defaults-editor" : undefined}
              onClick={pantryEditorOpen ? closePantryEditor : openPantryEditor}
            >
              {pantryEditorOpen ? "Close editor" : "Edit defaults"}
            </button>
          </div>
          <p className="panel-note">
            {manualEntry
              ? "Tick what you actually have. Nothing is assumed."
              : "These weren't in your photo. Tick what you actually have; nothing is assumed."}
          </p>

          {pantryEditorOpen ? (
            <section
              className="pantry-editor"
              id="pantry-defaults-editor"
              aria-labelledby="pantry-editor-title"
            >
              <div className="pantry-editor-heading">
                <div>
                  <h3 id="pantry-editor-title">Edit pantry defaults</h3>
                  <p className="panel-note">
                    Saving replaces the current pantry list immediately and uses these
                    defaults for future ingredient checks on this device.
                  </p>
                </div>
              </div>
              {generatedContentExists ? (
                <div className="notice notice-accent" role="status">
                  <AlertTriangle className="notice-icon" aria-hidden="true" />
                  Saving now clears your generated ideas, recipes, and checked steps
                  because the available ingredients change.
                </div>
              ) : null}
              <div className="pantry-editor-list">
                {pantryDraft.map((name, index) => (
                  <div className="pantry-editor-row" key={index}>
                    <input
                      ref={(element) => {
                        pantryDraftInputRefs.current[index] = element;
                      }}
                      className="input"
                      aria-label={`Pantry staple ${index + 1}`}
                      value={name}
                      maxLength={80}
                      onChange={(event) =>
                        setPantryDraft((current) =>
                          current.map((item, itemIndex) =>
                            itemIndex === index ? event.target.value : item,
                          ),
                        )
                      }
                    />
                    <button
                      className="btn btn-secondary btn-icon"
                      type="button"
                      aria-label={`Remove ${name || `pantry staple ${index + 1}`}`}
                      onClick={() => removePantryStaple(index)}
                    >
                      <X aria-hidden="true" size={16} />
                    </button>
                  </div>
                ))}
              </div>
              <form className="pantry-editor-add" onSubmit={submitNewPantryStaple}>
                <label className="visually-hidden" htmlFor="new-pantry-staple">
                  Add a pantry staple
                </label>
                <input
                  ref={newPantryStapleRef}
                  className="input"
                  id="new-pantry-staple"
                  aria-label="Add a pantry staple"
                  placeholder="Add a staple…"
                  value={newPantryStaple}
                  maxLength={80}
                  onChange={(event) => setNewPantryStaple(event.target.value)}
                />
                <button className="btn btn-secondary" type="submit">
                  <Plus aria-hidden="true" size={16} />
                  Add
                </button>
              </form>
              {pantryEditorError ? (
                <p className="field-error" role="alert">
                  {pantryEditorError}
                </p>
              ) : null}
              <div className="pantry-editor-actions">
                <button
                  className="btn btn-primary"
                  type="button"
                  onClick={savePantryDefaults}
                >
                  Save defaults
                </button>
                <button
                  className="btn btn-secondary"
                  type="button"
                  onClick={closePantryEditor}
                >
                  Cancel
                </button>
              </div>
            </section>
          ) : null}

          {pantrySaveFailed ? (
            <div className="notice notice-plain" role="status">
              Pantry defaults could not be saved on this device. They apply to this
              visit only.
            </div>
          ) : null}

          <div className="pantry-list">
            <label className="pantry-row pantry-select-all">
              <input
                ref={selectAllPantryRef}
                className="pantry-input"
                type="checkbox"
                aria-label="Select all pantry staples"
                checked={allPantryConfirmed}
                onChange={() => onToggleAllPantry(!allPantryConfirmed)}
              />
              <span className="pantry-checkbox" aria-hidden="true">
                {somePantryConfirmed ? <Minus /> : <Check />}
              </span>
              <span className="pantry-label">Select all</span>
              <span className="tag tag-neutral">
                {confirmedPantryCount} of {pantryIngredients.length}
              </span>
            </label>
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
        </div>
      </div>

      <section className="preferences" aria-labelledby="preferences-title">
        <div className="preferences-header">
          <h2 className="preferences-title" id="preferences-title">
            Preferences
          </h2>
          <p className="preferences-note">
            Optional details that help tailor the recipe ideas.
          </p>
        </div>
        <div className="preferences-panel">
          <div className="preferences-grid">
            <section
              className="preference-group preference-diet"
              aria-labelledby="preference-diet-title"
            >
              <h3 className="preference-group-title" id="preference-diet-title">
                Diet
              </h3>
              <PreferenceSegment
                label="Diet preference"
                name="diet"
                value={preferences.diet}
                options={DIET_OPTIONS}
                onChange={(value) =>
                  onPreferenceChange("diet", value as PreferenceView["diet"])
                }
              />
              <PreferenceSegment
                label="Style"
                name="diet-style"
                value={preferences.dietStyle ?? ""}
                options={DIET_STYLE_OPTIONS[preferences.diet]}
                onChange={(value) =>
                  onPreferenceChange(
                    "dietStyle",
                    (value || null) as PreferenceView["dietStyle"],
                  )
                }
              />
              <div className="field">
                <span className="field-label" id="diet-add-ons-label">
                  Also apply
                </span>
                <div
                  className="preference-chips"
                  role="group"
                  aria-labelledby="diet-add-ons-label"
                >
                  {DIET_ADD_ONS.map((addOn) => {
                    const selected = preferences.dietAddOns.includes(addOn);
                    return (
                      <button
                        className="preference-chip"
                        data-selected={selected ? "true" : undefined}
                        type="button"
                        aria-pressed={selected}
                        key={addOn}
                        onClick={() => toggleListPreference("dietAddOns", addOn)}
                      >
                        {DIET_ADD_ON_LABELS[addOn]}
                      </button>
                    );
                  })}
                </div>
              </div>
            </section>

            <section
              className="preference-group"
              aria-labelledby="preference-cuisines-title"
            >
              <h3 className="preference-group-title" id="preference-cuisines-title">
                Cuisines
              </h3>
              <div className="field">
                <span className="field-label" id="cuisines-label">
                  Optional — choose up to {MAX_CUISINE_PREFERENCES}
                </span>
                <div
                  className="preference-chips"
                  role="group"
                  aria-labelledby="cuisines-label"
                >
                  {BASE_CUISINES.map((cuisine) => {
                    const selected = preferences.cuisines.some(
                      (value) =>
                        value.toLocaleLowerCase() === cuisine.toLocaleLowerCase(),
                    );
                    return (
                      <button
                        className="preference-chip"
                        data-selected={selected ? "true" : undefined}
                        type="button"
                        aria-pressed={selected}
                        disabled={!selected && cuisineLimitReached}
                        key={cuisine}
                        onClick={() => toggleListPreference("cuisines", cuisine)}
                      >
                        {cuisine}
                      </button>
                    );
                  })}
                </div>
                <form className="preference-add-form" onSubmit={submitCustomCuisine}>
                  <label className="visually-hidden" htmlFor="custom-cuisine">
                    Custom cuisine
                  </label>
                  <input
                    className="input"
                    id="custom-cuisine"
                    aria-label="Custom cuisine"
                    placeholder="Add another cuisine…"
                    value={customCuisine}
                    maxLength={80}
                    disabled={cuisineLimitReached}
                    onChange={(event) => setCustomCuisine(event.target.value)}
                  />
                  <button
                    className="btn btn-secondary"
                    type="submit"
                    disabled={cuisineLimitReached}
                  >
                    <Plus aria-hidden="true" size={16} />
                    Add
                  </button>
                </form>
                {customCuisines.length > 0 ? (
                  <div className="custom-preference-list" aria-label="Custom cuisines">
                    {customCuisines.map((cuisine) => (
                      <button
                        className="preference-chip preference-chip-custom"
                        type="button"
                        aria-label={`Remove ${cuisine} cuisine`}
                        key={cuisine.toLocaleLowerCase()}
                        onClick={() => toggleListPreference("cuisines", cuisine)}
                      >
                        {cuisine}
                        <X aria-hidden="true" size={14} />
                      </button>
                    ))}
                  </div>
                ) : null}
              </div>
            </section>

            <section
              className="preference-group"
              aria-labelledby="preference-spice-title"
            >
              <h3 className="preference-group-title" id="preference-spice-title">
                Spice
              </h3>
              <PreferenceSegment
                label="Spice level"
                name="spice-level"
                value={preferences.spiceLevel}
                options={SPICE_OPTIONS}
                onChange={(value) =>
                  onPreferenceChange(
                    "spiceLevel",
                    value as PreferenceView["spiceLevel"],
                  )
                }
              />
            </section>

            <section
              className="preference-group preference-serving-group"
              aria-labelledby="preference-serving-title"
            >
              <h3 className="preference-group-title" id="preference-serving-title">
                Servings &amp; ideas
              </h3>
              <div className="preference-pair">
                <PreferenceSegment
                  label="Servings"
                  name="servings"
                  value={String(preferences.servings)}
                  options={SERVING_OPTIONS}
                  onChange={(value) =>
                    onPreferenceChange(
                      "servings",
                      Number(value) as PreferenceView["servings"],
                    )
                  }
                />
                <PreferenceSegment
                  label="Number of ideas"
                  name="option-count"
                  value={String(preferences.optionCount)}
                  options={OPTION_COUNT_OPTIONS}
                  onChange={(value) =>
                    onPreferenceChange(
                      "optionCount",
                      Number(value) as PreferenceView["optionCount"],
                    )
                  }
                />
              </div>
            </section>

            <section
              className="preference-group"
              aria-labelledby="preference-allergens-title"
            >
              <h3 className="preference-group-title" id="preference-allergens-title">
                Allergens
              </h3>
              <div className="field">
                <span className="field-label" id="allergens-label">
                  Select any to avoid
                </span>
                <div
                  className="preference-chips"
                  role="group"
                  aria-labelledby="allergens-label"
                >
                  {BASE_ALLERGENS.map((allergen) => {
                    const selected = preferences.allergens.some(
                      (value) =>
                        value.toLocaleLowerCase() === allergen.toLocaleLowerCase(),
                    );
                    return (
                      <button
                        className="preference-chip"
                        data-selected={selected ? "true" : undefined}
                        type="button"
                        aria-pressed={selected}
                        key={allergen}
                        onClick={() => toggleListPreference("allergens", allergen)}
                      >
                        {allergen}
                      </button>
                    );
                  })}
                </div>
                <form className="preference-add-form" onSubmit={submitCustomAllergen}>
                  <label className="visually-hidden" htmlFor="custom-allergen">
                    Custom allergen
                  </label>
                  <input
                    className="input"
                    id="custom-allergen"
                    aria-label="Custom allergen"
                    placeholder="Add another allergen…"
                    value={customAllergen}
                    maxLength={80}
                    onChange={(event) => setCustomAllergen(event.target.value)}
                  />
                  <button className="btn btn-secondary" type="submit">
                    <Plus aria-hidden="true" size={16} />
                    Add
                  </button>
                </form>
                {customAllergens.length > 0 ? (
                  <div className="custom-preference-list" aria-label="Custom allergens">
                    {customAllergens.map((allergen) => (
                      <button
                        className="preference-chip preference-chip-custom"
                        type="button"
                        aria-label={`Remove ${allergen} allergen`}
                        key={allergen.toLocaleLowerCase()}
                        onClick={() => toggleListPreference("allergens", allergen)}
                      >
                        {allergen}
                        <X aria-hidden="true" size={14} />
                      </button>
                    ))}
                  </div>
                ) : null}
              </div>
            </section>

            <section
              className="preference-group preference-group-wide"
              aria-labelledby="preference-instructions-title"
            >
              <h3 className="preference-group-title" id="preference-instructions-title">
                Special instructions
              </h3>
              <div className="field">
                <label className="visually-hidden" htmlFor="special-instructions">
                  Special instructions
                </label>
                <textarea
                  className="input"
                  id="special-instructions"
                  value={preferences.specialInstructions}
                  maxLength={500}
                  aria-describedby="special-instructions-count"
                  placeholder="Anything else the agents should honor — kid-friendly, low oil, pressure cooker only…"
                  onChange={(event) =>
                    onPreferenceChange("specialInstructions", event.target.value)
                  }
                />
                <span className="character-count" id="special-instructions-count">
                  {preferences.specialInstructions.length} / 500
                </span>
              </div>
            </section>
          </div>
        </div>
      </section>

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

type ConfidenceLevel = "high" | "medium" | "low";

function confidenceLevel(confidence: number): ConfidenceLevel {
  if (confidence >= 0.8) return "high";
  if (confidence >= 0.6) return "medium";
  return "low";
}

function confidenceMessage(level: ConfidenceLevel, percentage: number): string {
  if (level === "high") {
    return `Identified with high confidence (${percentage}%). Very likely correct.`;
  }
  if (level === "medium") {
    return `Identified with medium confidence (${percentage}%). Worth a quick check.`;
  }
  return `Low confidence (${percentage}%). Verify or remove this item.`;
}

function ConfidenceIndicator({ confidence }: { confidence: number }) {
  const [open, setOpen] = useState(false);
  const [placement, setPlacement] = useState<"above" | "below">("below");
  const containerRef = useRef<HTMLSpanElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const popoverRef = useRef<HTMLSpanElement>(null);
  const popoverId = useId();
  const level = confidenceLevel(confidence);
  const percentage = Math.round(confidence * 100);

  useEffect(() => {
    if (!open) return;

    function dismissOnOutsidePress(event: PointerEvent) {
      if (
        event.target instanceof Node &&
        !containerRef.current?.contains(event.target)
      ) {
        setOpen(false);
      }
    }

    function dismissOnEscape(event: KeyboardEvent) {
      if (event.key === "Escape") setOpen(false);
    }

    document.addEventListener("pointerdown", dismissOnOutsidePress);
    document.addEventListener("keydown", dismissOnEscape);
    return () => {
      document.removeEventListener("pointerdown", dismissOnOutsidePress);
      document.removeEventListener("keydown", dismissOnEscape);
    };
  }, [open]);

  useLayoutEffect(() => {
    if (!open || !buttonRef.current || !popoverRef.current) return;

    const buttonRect = buttonRef.current.getBoundingClientRect();
    const popoverHeight = popoverRef.current.getBoundingClientRect().height;
    const stickyBarTop = document
      .querySelector<HTMLElement>(".sticky-action-bar")
      ?.getBoundingClientRect().top;
    const lowerBoundary = Math.min(
      window.innerHeight,
      stickyBarTop ?? window.innerHeight,
    );
    const spaceBelow = lowerBoundary - buttonRect.bottom;
    const spaceAbove = buttonRect.top;
    const nextPlacement =
      spaceBelow < popoverHeight + 8 && spaceAbove > spaceBelow ? "above" : "below";
    setPlacement(nextPlacement);
  }, [open]);

  return (
    <span className="confidence-indicator" ref={containerRef}>
      <button
        ref={buttonRef}
        className={`confidence-button confidence-${level}`}
        type="button"
        aria-label={`Confidence: ${level}, ${percentage} percent`}
        aria-expanded={open}
        aria-controls={open ? popoverId : undefined}
        aria-describedby={open ? popoverId : undefined}
        onClick={() => setOpen((current) => !current)}
      >
        <span
          className="confidence-dot"
          data-signal={
            level === "high" ? "filled" : level === "medium" ? "half" : "hollow"
          }
          aria-hidden="true"
        />
      </button>
      {open ? (
        <span
          ref={popoverRef}
          className="confidence-popover"
          data-placement={placement}
          id={popoverId}
          role="tooltip"
        >
          {confidenceMessage(level, percentage)}
        </span>
      ) : null}
    </span>
  );
}

interface PreferenceSegmentProps {
  label: string;
  name: string;
  value: string;
  options: ReadonlyArray<readonly [value: string, label: string]>;
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
