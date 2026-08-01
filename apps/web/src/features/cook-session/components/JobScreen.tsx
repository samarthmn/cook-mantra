import { ArrowLeft, Check } from "lucide-react";

import type { JobView } from "../model/cook-session-state";
import { ScreenHeader } from "./ScreenHeader";

interface JobLine {
  label: string;
  detail: string;
  agent: boolean;
}

interface JobCopy {
  kicker: string;
  title: string;
  intro: string;
  lines: JobLine[];
  /** Leading lines already finished before the job starts reporting progress. */
  preCompletedLines?: number;
}

interface JobScreenProps {
  job: JobView;
  allowInterruption?: boolean;
  onCancel: () => void;
  onSimulateFailure: () => void;
  showSimulateFailure?: boolean;
}

const extractionLines: JobLine[] = [
  { label: "Photo upload", detail: "Sent for this session only", agent: false },
  {
    label: "Ingredient Extraction Agent",
    detail: "Identifying the ingredients in your photo",
    agent: true,
  },
  {
    label: "Scoring confidence",
    detail: "Uncertain items get flagged for you",
    agent: false,
  },
];

const ideaLines: JobLine[] = [
  {
    label: "Master Chef Agent",
    detail: "Drafting dishes from your confirmed list",
    agent: true,
  },
  {
    label: "Nutrition Agent",
    detail: "Estimating calories, macros and diet tags",
    agent: true,
  },
  {
    label: "Image Agent",
    detail: "Generating a photo of each dish (AI-generated)",
    agent: true,
  },
  {
    label: "Assembling options",
    detail: "Combining ideas, nutrition and images",
    agent: false,
  },
];

function jobCopy(job: JobView): JobCopy {
  if (job.kind === "extraction") {
    return {
      kicker: "Step 1 — Identifying ingredients",
      title: "Looking at your ingredients",
      intro:
        "The Ingredient Extraction Agent lists what it sees and how sure it is. You check everything next.",
      lines: extractionLines,
      // The upload finishes before the server reports any progress, so the
      // upload line runs only until the first progress tick; from then on the
      // agent line is what the user watches.
      preCompletedLines: 1,
    };
  }

  if (job.kind === "recipes") {
    return {
      kicker: "Step 4 — Writing your recipes",
      title:
        job.selectedNames.length > 1
          ? `${job.selectedNames.length} recipes, in parallel`
          : "Writing your recipe",
      intro:
        "One Specialized Recipe Agent per dish, each an expert in its cuisine. They run at the same time.",
      lines: [
        ...job.selectedNames.map((name) => ({
          label: `Specialized Recipe Agent — ${name}`,
          detail: "Quantities, numbered steps, tips, substitutions",
          agent: true,
        })),
        {
          label: "Collecting results",
          detail: "All successful recipes return together",
          agent: false,
        },
      ],
    };
  }

  return {
    kicker: "Step 3 — Generating ideas",
    title: job.kind === "more-ideas" ? "A fresh batch" : "Cooking up suggestions",
    intro:
      job.kind === "more-ideas"
        ? "Recipes you have already seen are remembered and excluded."
        : "Only your confirmed ingredients are used. Nothing else is assumed.",
    lines: ideaLines,
  };
}

function lineStatus(
  index: number,
  lineCount: number,
  progress: number,
  parallel: boolean,
  preCompleted = 0,
): "pending" | "running" | "done" {
  if (progress >= 100) return "done";
  if (parallel) {
    const collecting = index === lineCount - 1;
    if (collecting) return progress >= 80 ? "running" : "pending";
    return progress > 0 ? (progress >= 80 ? "done" : "running") : "pending";
  }
  if (index < preCompleted) {
    if (progress > 0) return "done";
    return index === preCompleted - 1 ? "running" : "done";
  }
  if (preCompleted > 0 && progress <= 0) return "pending";
  const segment = 100 / (lineCount - preCompleted);
  const position = index - preCompleted;
  if (progress >= (position + 1) * segment) return "done";
  if (progress >= position * segment) return "running";
  return "pending";
}

export function JobScreen({
  job,
  allowInterruption = true,
  onCancel,
  onSimulateFailure,
  showSimulateFailure = false,
}: JobScreenProps) {
  const copy = jobCopy(job);
  const parallel = job.kind === "recipes";

  return (
    <section className="job-shell" aria-labelledby="job-title">
      <ScreenHeader
        kicker={copy.kicker}
        title={<span id="job-title">{copy.title}</span>}
        titleClassName="screen-title-job"
        intro={copy.intro}
      />
      <progress
        className="visually-hidden"
        aria-label="Overall generation progress"
        max={100}
        value={job.progress}
      >
        {job.progress}%
      </progress>
      <div
        className="job-list"
        role="list"
        aria-label="Generation progress"
        aria-live="polite"
      >
        {copy.lines.map((line, index) => {
          const status = lineStatus(
            index,
            copy.lines.length,
            job.progress,
            parallel,
            copy.preCompletedLines ?? 0,
          );
          return (
            <div
              className="job-row"
              data-status={status}
              role="listitem"
              key={line.label}
            >
              <span className={`job-status job-status-${status}`}>
                {status === "done" ? <Check aria-hidden="true" /> : null}
                <span className="visually-hidden">
                  {line.label}: {status}
                </span>
              </span>
              <div className="job-content">
                <div className="job-label">{line.label}</div>
                <div className="job-detail">{line.detail}</div>
              </div>
              {line.agent ? <span className="tag tag-neutral">agent</span> : null}
            </div>
          );
        })}
      </div>
      {allowInterruption ? (
        <div className="job-actions">
          <button className="btn btn-ghost" type="button" onClick={onCancel}>
            <ArrowLeft aria-hidden="true" size={16} />
            Cancel
          </button>
          {showSimulateFailure ? (
            <button
              className="btn btn-ghost text-muted"
              type="button"
              onClick={onSimulateFailure}
            >
              Simulate a failure
            </button>
          ) : null}
        </div>
      ) : (
        <p className="job-actions text-muted" role="status">
          This server job cannot be cancelled once it starts. Keep this page open while
          it finishes. If it stops responding, Cook Mantra will return you to this step
          with a message.
        </p>
      )}
    </section>
  );
}
