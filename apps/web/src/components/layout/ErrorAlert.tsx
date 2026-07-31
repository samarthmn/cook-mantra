import { TriangleAlert } from "lucide-react";
import type { ReactNode } from "react";

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

  return (
    <div className="alert" role="alert">
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
