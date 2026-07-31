import type { ReactNode } from "react";

export interface StickyActionBarProps {
  summary: ReactNode;
  hint?: ReactNode;
  actions: ReactNode;
}

export function StickyActionBar({ summary, hint, actions }: StickyActionBarProps) {
  return (
    <aside aria-label="Page actions" className="sticky-action-bar">
      <div className="sticky-action-inner">
        <div className="sticky-action-summary">
          <div className="sticky-action-count">{summary}</div>
          {hint !== undefined && hint !== null ? (
            <div className="sticky-action-hint">{hint}</div>
          ) : null}
        </div>
        <div className="sticky-action-actions">{actions}</div>
      </div>
    </aside>
  );
}
