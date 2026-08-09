"use client";

import { useCallback, useEffect, useId, useRef, type RefObject } from "react";

import type { RemoteMediaProvider } from "../model/remote-media-disclosure";

interface RemoteMediaDisclosureDialogProps {
  provider: RemoteMediaProvider;
  model: string;
  returnFocusRef: RefObject<HTMLElement | null>;
  errorMessage?: string | null;
  onAccept: () => void;
  onDecline: () => void;
  onManualEntry: () => void;
}

const PROVIDER_LABELS: Record<RemoteMediaProvider, string> = {
  openrouter: "OpenRouter",
  codex: "Codex",
};

export function RemoteMediaDisclosureDialog({
  provider,
  model,
  returnFocusRef,
  errorMessage = null,
  onAccept,
  onDecline,
  onManualEntry,
}: RemoteMediaDisclosureDialogProps) {
  const dialogRef = useRef<HTMLDivElement>(null);
  const keepLocalButtonRef = useRef<HTMLButtonElement>(null);
  const titleId = useId();
  const bodyId = useId();

  const decline = useCallback(() => {
    returnFocusRef.current?.focus({ preventScroll: true });
    onDecline();
  }, [onDecline, returnFocusRef]);

  useEffect(() => {
    keepLocalButtonRef.current?.focus();
  }, []);

  useEffect(() => {
    function handleKeyDown(event: globalThis.KeyboardEvent) {
      if (event.key === "Escape") {
        event.preventDefault();
        decline();
        return;
      }
      if (event.key !== "Tab") return;

      const dialog = dialogRef.current;
      if (!dialog) return;
      const actions = Array.from(
        dialog.querySelectorAll<HTMLButtonElement>("button:not([disabled])"),
      );
      const first = actions[0];
      const last = actions.at(-1);
      if (!first || !last) return;

      if (!dialog.contains(document.activeElement)) {
        event.preventDefault();
        first.focus();
      } else if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }

    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [decline]);

  const providerLabel = PROVIDER_LABELS[provider];

  return (
    <div
      className="dialog-backdrop"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) {
          event.preventDefault();
          decline();
        }
      }}
    >
      <div
        ref={dialogRef}
        className="dialog privacy-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={bodyId}
      >
        <h2 className="dialog-title" id={titleId}>
          This photo will leave your device
        </h2>
        <p className="dialog-body" id={bodyId}>
          Cook Mantra will send this photo to the provider and model below to identify
          ingredients for this cooking session. Nothing is sent until you choose
          Continue and send photo.
        </p>

        <dl className="privacy-dialog-destination">
          <div>
            <dt>Provider</dt>
            <dd className="privacy-dialog-provider">
              <bdi className="privacy-dialog-provider">{providerLabel}</bdi>
            </dd>
          </div>
          <div>
            <dt>Model</dt>
            <dd className="privacy-dialog-provider">
              <bdi className="privacy-dialog-provider">{model}</bdi>
            </dd>
          </div>
        </dl>

        {errorMessage ? <p role="alert">{errorMessage}</p> : null}

        <div className="dialog-actions">
          <button
            ref={keepLocalButtonRef}
            className="btn btn-secondary"
            type="button"
            onClick={decline}
          >
            Keep photo on this device
          </button>
          <button className="btn btn-primary" type="button" onClick={onAccept}>
            Continue and send photo
          </button>
          <button className="btn btn-ghost" type="button" onClick={onManualEntry}>
            Type ingredients instead
          </button>
        </div>
      </div>
    </div>
  );
}
