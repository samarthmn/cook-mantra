import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { JobScreen } from "./JobScreen";

afterEach(cleanup);

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

  it("describes the image agent as generating AI photos", () => {
    render(
      <JobScreen
        job={{
          kind: "ideas",
          returnView: "confirm",
          progress: 40,
          selectedNames: [],
        }}
        onCancel={vi.fn()}
        onSimulateFailure={vi.fn()}
      />,
    );

    const imageRow = screen.getByText("Image Agent").closest<HTMLElement>(".job-row");
    expect(imageRow).not.toBeNull();
    expect(
      within(imageRow!).getByText("Generating a photo of each dish (AI-generated)"),
    ).toBeVisible();
  });
});
