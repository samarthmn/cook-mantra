import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { JobScreen } from "./JobScreen";

afterEach(cleanup);

interface ImageRoleStatus {
  enabled: boolean;
  ready: boolean;
}

function renderRecipeJob(imageRoleStatus?: ImageRoleStatus, progress = 40) {
  return render(
    <JobScreen
      job={{
        kind: "recipes",
        returnView: "options",
        progress,
        selectedNames: ["Seasonal Dal", "Tomato Rice"],
      }}
      imageRoleStatus={imageRoleStatus}
      onCancel={vi.fn()}
      onSimulateFailure={vi.fn()}
    />,
  );
}

describe("JobScreen", () => {
  it("exposes named overall progress and textual status for every step", () => {
    render(
      <JobScreen
        job={{
          kind: "extraction",
          returnView: "upload",
          progress: 40,
          selectedNames: [],
        }}
        onCancel={vi.fn()}
        onSimulateFailure={vi.fn()}
      />,
    );

    expect(
      screen.getByRole("progressbar", { name: "Overall generation progress" }),
    ).toHaveAttribute("value", "40");
    const rows = screen.getAllByRole("listitem");
    expect(within(rows[0]).getByText("Photo upload: done")).toBeInTheDocument();
    expect(within(rows[0]).getByText("Sent after the destination check")).toBeVisible();
    expect(within(rows[0]).queryByText(/session only/i)).toBeNull();
    expect(
      within(rows[1]).getByText("Ingredient Extraction Agent: running"),
    ).toBeInTheDocument();
    expect(
      within(rows[2]).getByText("Scoring confidence: pending"),
    ).toBeInTheDocument();
  });

  it("runs the upload line only until the server reports progress", () => {
    render(
      <JobScreen
        job={{
          kind: "extraction",
          returnView: "upload",
          progress: 0,
          selectedNames: [],
        }}
        onCancel={vi.fn()}
        onSimulateFailure={vi.fn()}
      />,
    );

    const rows = screen.getAllByRole("listitem");
    expect(within(rows[0]).getByText("Photo upload: running")).toBeInTheDocument();
    expect(
      within(rows[1]).getByText("Ingredient Extraction Agent: pending"),
    ).toBeInTheDocument();
  });

  it("shows the extraction agent identifying from the first progress tick", () => {
    render(
      <JobScreen
        job={{
          kind: "extraction",
          returnView: "upload",
          progress: 5,
          selectedNames: [],
        }}
        onCancel={vi.fn()}
        onSimulateFailure={vi.fn()}
      />,
    );

    const rows = screen.getAllByRole("listitem");
    expect(within(rows[0]).getByText("Photo upload: done")).toBeInTheDocument();
    expect(
      within(rows[1]).getByText("Ingredient Extraction Agent: running"),
    ).toBeInTheDocument();
  });

  it.each(["ideas", "more-ideas"] as const)(
    "keeps %s option work text-only even when dish previews are ready",
    (kind) => {
      render(
        <JobScreen
          job={{
            kind,
            returnView: kind === "ideas" ? "confirm" : "options",
            progress: 40,
            selectedNames: [],
          }}
          imageRoleStatus={{ enabled: true, ready: true }}
          onCancel={vi.fn()}
          onSimulateFailure={vi.fn()}
        />,
      );

      expect(screen.getByText("Master Chef Agent")).toBeVisible();
      expect(screen.getByText("Assembling options")).toBeVisible();
      expect(screen.queryByText(/image|preview/i)).toBeNull();
    },
  );

  it("names each recipe writer and starts its ready dish preview only after writing", () => {
    renderRecipeJob({ enabled: true, ready: true });

    expect(screen.getByText("Recipe Writer Agent — Seasonal Dal")).toBeVisible();
    expect(screen.getByText("Recipe Writer Agent — Tomato Rice")).toBeVisible();

    const seasonalPreview = screen
      .getByText("Dish preview — Seasonal Dal")
      .closest<HTMLElement>('[role="listitem"]');
    const tomatoPreview = screen
      .getByText("Dish preview — Tomato Rice")
      .closest<HTMLElement>('[role="listitem"]');
    expect(seasonalPreview).not.toBeNull();
    expect(tomatoPreview).not.toBeNull();
    expect(
      within(seasonalPreview!).getByText("Starts after this recipe is written"),
    ).toBeVisible();
    expect(
      within(seasonalPreview!).getByText("Dish preview — Seasonal Dal: pending"),
    ).toBeInTheDocument();
    expect(
      within(tomatoPreview!).getByText("Starts after this recipe is written"),
    ).toBeVisible();
  });

  it.each([
    ["status is absent", undefined],
    ["the image role is disabled", { enabled: false, ready: true }],
    ["the image role needs attention", { enabled: true, ready: false }],
  ] as const)("does not claim preview work when %s", (_case, imageRoleStatus) => {
    renderRecipeJob(imageRoleStatus);

    expect(screen.getByText("Recipe Writer Agent — Seasonal Dal")).toBeVisible();
    expect(screen.getByText("Recipe Writer Agent — Tomato Rice")).toBeVisible();
    expect(screen.queryByText(/dish preview/i)).toBeNull();
    expect(screen.queryByText(/image generation/i)).toBeNull();
  });

  it.each([
    [10, "running", "pending", "pending"],
    [89, "running", "pending", "pending"],
    [90, "done", "done", "running"],
    [100, "done", "done", "done"],
  ] as const)(
    "keeps recipe and preview status truthful at %i percent worker progress",
    (progress, writerStatus, previewStatus, collectingStatus) => {
      renderRecipeJob({ enabled: true, ready: true }, progress);

      expect(
        screen.getByText(`Recipe Writer Agent — Seasonal Dal: ${writerStatus}`),
      ).toBeInTheDocument();
      expect(
        screen.getByText(`Dish preview — Seasonal Dal: ${previewStatus}`),
      ).toBeInTheDocument();
      expect(
        screen.getByText(`Collecting results: ${collectingStatus}`),
      ).toBeInTheDocument();
      expect(screen.queryByText(/Dish preview .+: running/)).toBeNull();
    },
  );
});
