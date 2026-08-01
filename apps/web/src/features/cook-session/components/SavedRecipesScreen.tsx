"use client";

import {
  ArrowLeft,
  ChevronDown,
  ChevronUp,
  Clock3,
  Download,
  Trash2,
} from "lucide-react";
import { useCallback, useEffect, useId, useRef, useState } from "react";

import { downloadRecipeMarkdown } from "../model/recipe-markdown";
import { removeSavedRecipe, type SavedRecipeEntry } from "../model/saved-recipes";
import { RecipeDetail } from "./RecipeDetail";
import { ScreenHeader } from "./ScreenHeader";

export interface SavedRecipesScreenProps {
  entries: SavedRecipeEntry[];
  onEntriesChange: (entries: SavedRecipeEntry[]) => void;
  onBack: () => void;
}

const savedDateFormatter = new Intl.DateTimeFormat("en", {
  day: "numeric",
  month: "short",
  year: "numeric",
  timeZone: "UTC",
});

type FocusTarget =
  | { kind: "confirm"; entryId: string }
  | { kind: "remove"; entryId: string }
  | { kind: "heading" };

export function SavedRecipesScreen({
  entries,
  onEntriesChange,
  onBack,
}: SavedRecipesScreenProps) {
  const [expandedEntryId, setExpandedEntryId] = useState<string | null>(null);
  const [pendingRemovalId, setPendingRemovalId] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const headingRef = useRef<HTMLHeadingElement | null>(null);
  const removeButtonRefs = useRef(new Map<string, HTMLButtonElement>());
  const confirmButtonRefs = useRef(new Map<string, HTMLButtonElement>());
  const focusTargetRef = useRef<FocusTarget | null>(null);

  const setHeadingTitleRef = useCallback((node: HTMLSpanElement | null) => {
    headingRef.current = node?.closest("h1") ?? null;
  }, []);

  const registerRemoveButton = useCallback(
    (entryId: string, node: HTMLButtonElement | null) => {
      if (node) {
        removeButtonRefs.current.set(entryId, node);
      } else {
        removeButtonRefs.current.delete(entryId);
      }
    },
    [],
  );

  const registerConfirmButton = useCallback(
    (entryId: string, node: HTMLButtonElement | null) => {
      if (node) {
        confirmButtonRefs.current.set(entryId, node);
      } else {
        confirmButtonRefs.current.delete(entryId);
      }
    },
    [],
  );

  useEffect(() => {
    const target = focusTargetRef.current;
    if (!target) return;

    const targetElement =
      target.kind === "heading"
        ? headingRef.current
        : target.kind === "confirm"
          ? confirmButtonRefs.current.get(target.entryId)
          : removeButtonRefs.current.get(target.entryId);

    if (!targetElement) return;

    targetElement.focus();
    focusTargetRef.current = null;
  }, [entries, pendingRemovalId]);

  function removeEntry(entry: SavedRecipeEntry) {
    const result = removeSavedRecipe(entry.id);

    if (!result.ok) {
      onEntriesChange(result.entries);
      setNotice(
        `Couldn’t remove ${entry.recipe.name}. Check browser storage and try again.`,
      );
      return;
    }

    const removedIndex = entries.findIndex((candidate) => candidate.id === entry.id);
    const nextEntry =
      result.entries[removedIndex] ?? result.entries[removedIndex - 1] ?? null;
    focusTargetRef.current = nextEntry
      ? { kind: "remove", entryId: nextEntry.id }
      : { kind: "heading" };

    onEntriesChange(result.entries);
    setNotice(null);
    setPendingRemovalId(null);
    if (expandedEntryId === entry.id) setExpandedEntryId(null);
  }

  function downloadEntry(entry: SavedRecipeEntry) {
    setNotice(
      downloadRecipeMarkdown(entry.recipe)
        ? null
        : `Couldn’t download ${entry.recipe.name}. Try again in a browser window.`,
    );
  }

  return (
    <section className="saved-recipes-screen" aria-labelledby="saved-recipes-title">
      <div className="saved-recipes-header-actions">
        <button className="btn btn-secondary" type="button" onClick={onBack}>
          <ArrowLeft aria-hidden="true" size={16} />
          Back
        </button>
      </div>
      <ScreenHeader
        kicker="Your cookbook"
        title={
          <span id="saved-recipes-title" ref={setHeadingTitleRef}>
            Saved recipes
          </span>
        }
        intro="Keep the recipes you want to make again, all in one place."
      />

      {notice ? (
        <p className="recipe-action-notice" role="status">
          {notice}
        </p>
      ) : null}

      {entries.length === 0 ? (
        <div className="saved-recipes-empty">
          <h2>No recipes saved yet.</h2>
          <p>Save one when something looks worth making again.</p>
        </div>
      ) : (
        <div className="saved-recipes-list">
          {entries.map((entry) => (
            <SavedRecipeCard
              entry={entry}
              expanded={expandedEntryId === entry.id}
              confirmingRemoval={pendingRemovalId === entry.id}
              onToggleExpanded={() => {
                setExpandedEntryId((current) =>
                  current === entry.id ? null : entry.id,
                );
                setPendingRemovalId(null);
                setNotice(null);
              }}
              onDownload={() => downloadEntry(entry)}
              onRequestRemove={() => {
                focusTargetRef.current = { kind: "confirm", entryId: entry.id };
                setPendingRemovalId(entry.id);
                setNotice(null);
              }}
              onCancelRemove={() => {
                focusTargetRef.current = { kind: "remove", entryId: entry.id };
                setPendingRemovalId(null);
              }}
              onConfirmRemove={() => removeEntry(entry)}
              registerRemoveButton={registerRemoveButton}
              registerConfirmButton={registerConfirmButton}
              key={entry.id}
            />
          ))}
        </div>
      )}
    </section>
  );
}

function SavedRecipeCard({
  entry,
  expanded,
  confirmingRemoval,
  onToggleExpanded,
  onDownload,
  onRequestRemove,
  onCancelRemove,
  onConfirmRemove,
  registerRemoveButton,
  registerConfirmButton,
}: {
  entry: SavedRecipeEntry;
  expanded: boolean;
  confirmingRemoval: boolean;
  onToggleExpanded: () => void;
  onDownload: () => void;
  onRequestRemove: () => void;
  onCancelRemove: () => void;
  onConfirmRemove: () => void;
  registerRemoveButton: (entryId: string, node: HTMLButtonElement | null) => void;
  registerConfirmButton: (entryId: string, node: HTMLButtonElement | null) => void;
}) {
  const generatedId = useId().replaceAll(":", "");
  const detailId = `saved-recipe-detail-${generatedId}`;
  const { recipe } = entry;
  const setRemoveButtonRef = useCallback(
    (node: HTMLButtonElement | null) => registerRemoveButton(entry.id, node),
    [entry.id, registerRemoveButton],
  );
  const setConfirmButtonRef = useCallback(
    (node: HTMLButtonElement | null) => registerConfirmButton(entry.id, node),
    [entry.id, registerConfirmButton],
  );

  return (
    <article className="saved-recipe-card" aria-labelledby={`${detailId}-title`}>
      <div className="saved-recipe-summary">
        <div>
          <h2 className="saved-recipe-heading" id={`${detailId}-title`}>
            {recipe.name}
          </h2>
          <div className="saved-recipe-meta">
            <span className="tag tag-accent">{recipe.cuisine}</span>
            <span className="meta-with-icon">
              <Clock3 aria-hidden="true" size={15} />
              {recipe.totalMinutes} min
            </span>
            <span className="text-muted">
              {recipe.servings} {recipe.servings === 1 ? "serving" : "servings"}
            </span>
            <span className="saved-recipe-saved-at">
              Saved{" "}
              <time dateTime={entry.savedAt}>
                {savedDateFormatter.format(new Date(entry.savedAt))}
              </time>
            </span>
            {recipe.nutrition ? (
              <span className="text-muted">
                {recipe.nutrition.caloriesKcal} kcal · {recipe.nutrition.proteinG}g
                protein
              </span>
            ) : null}
          </div>
        </div>
        <div className="saved-recipe-actions">
          <button
            className="btn btn-secondary"
            type="button"
            aria-controls={detailId}
            aria-expanded={expanded}
            aria-label={`${expanded ? "Close" : "Open"} full recipe: ${recipe.name}`}
            onClick={onToggleExpanded}
          >
            {expanded ? (
              <ChevronUp aria-hidden="true" size={16} />
            ) : (
              <ChevronDown aria-hidden="true" size={16} />
            )}
            {expanded ? "Close full recipe" : "Open full recipe"}
          </button>
          <button
            className="btn btn-secondary"
            type="button"
            aria-label={`Download ${recipe.name}`}
            onClick={onDownload}
          >
            <Download aria-hidden="true" size={16} />
            Download
          </button>
          {!confirmingRemoval ? (
            <button
              className="btn btn-ghost"
              type="button"
              aria-label={`Remove ${recipe.name}`}
              onClick={onRequestRemove}
              ref={setRemoveButtonRef}
            >
              <Trash2 aria-hidden="true" size={16} />
              Remove
            </button>
          ) : null}
        </div>
      </div>

      {confirmingRemoval ? (
        <div
          className="saved-recipe-remove-confirm"
          role="group"
          aria-label={`Confirm removal of ${recipe.name}`}
        >
          <p className="saved-recipe-remove-copy">
            Remove {recipe.name} from your saved recipes?
          </p>
          <button className="btn btn-secondary" type="button" onClick={onCancelRemove}>
            Keep it
          </button>
          <button
            className="btn btn-primary"
            type="button"
            aria-label={`Confirm remove ${recipe.name}`}
            onClick={onConfirmRemove}
            ref={setConfirmButtonRef}
          >
            Remove
          </button>
        </div>
      ) : null}

      {expanded ? (
        <div
          className="saved-recipe-detail"
          id={detailId}
          role="region"
          aria-label={`Full recipe: ${recipe.name}`}
        >
          <RecipeDetail recipe={recipe} idPrefix={detailId} showHeader={false} />
        </div>
      ) : null}
    </article>
  );
}
