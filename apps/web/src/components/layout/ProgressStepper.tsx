const STEPS = ["Photo", "Confirm", "Choose", "Cook"] as const;

export interface ProgressStepperProps {
  currentIndex: number;
  maxReachedIndex?: number;
  locked?: boolean;
  onStepChange?: (index: number) => void;
}

export function ProgressStepper({
  currentIndex,
  maxReachedIndex = currentIndex,
  locked = false,
  onStepChange,
}: ProgressStepperProps) {
  return (
    <nav aria-label="Progress" className="progress-stepper">
      {STEPS.map((label, index) => {
        const isComplete = index <= maxReachedIndex && index !== currentIndex;
        const isCurrent = index === currentIndex;
        const isLocked = locked || index > maxReachedIndex;
        const canNavigate = isComplete && !locked && onStepChange !== undefined;
        const className = [
          "progress-step",
          isComplete && "is-complete",
          isCurrent && "is-current",
          isLocked && "is-locked",
        ]
          .filter(Boolean)
          .join(" ");

        return (
          <button
            aria-current={isCurrent ? "step" : undefined}
            className={className}
            disabled={!canNavigate}
            key={label}
            onClick={() => onStepChange?.(index)}
            type="button"
          >
            <span aria-hidden="true" className="progress-step-number">
              {index + 1}
            </span>
            <span>{label}</span>
          </button>
        );
      })}
    </nav>
  );
}
