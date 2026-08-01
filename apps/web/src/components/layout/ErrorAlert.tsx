"use client";

import { TriangleAlert } from "lucide-react";
import { useEffect, useRef, type ReactNode } from "react";

export interface ErrorAlertProps {
  title: ReactNode;
  message: ReactNode;
  onRetry?: () => void;
  onDismiss?: () => void;
  retryLabel?: string;
  dismissLabel?: string;
}

export function ErrorAlert({
  title,
  message,
  onRetry,
  onDismiss,
  retryLabel = "Try again",
  dismissLabel = "Dismiss",
}: ErrorAlertProps) {
  const hasActions = onRetry !== undefined || onDismiss !== undefined;
  const alertRef = useRef<HTMLDivElement>(null);

  // Errors can be triggered from far down the page (e.g. selecting a seventh
  // recipe idea); bring the alert into view so it is never missed.
  useEffect(() => {
    const alert = alertRef.current;
    if (!alert) return;

    alert.focus({ preventScroll: true });
    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    alert.scrollIntoView?.({
      block: "nearest",
      behavior: reducedMotion ? "auto" : "smooth",
    });
  }, []);

  return (
    <div className="alert" role="alert" ref={alertRef} tabIndex={-1}>
      <TriangleAlert
        aria-hidden="true"
        className="alert-icon"
        size={20}
        strokeWidth={2}
      />
      <div className="alert-content">
        <div className="alert-title">{title}</div>
        <div className="alert-body">{message}</div>
      </div>
      {hasActions ? (
        <div className="alert-actions">
          {onRetry ? (
            <button className="btn btn-primary" onClick={onRetry} type="button">
              {retryLabel}
            </button>
          ) : null}
          {onDismiss ? (
            <button className="btn btn-ghost" onClick={onDismiss} type="button">
              {dismissLabel}
            </button>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
