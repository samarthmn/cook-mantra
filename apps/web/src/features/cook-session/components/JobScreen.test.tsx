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
    expect(within(rows[0]).getByText("Status: done")).toBeInTheDocument();
    expect(within(rows[1]).getByText("Status: running")).toBeInTheDocument();
    expect(within(rows[2]).getByText("Status: pending")).toBeInTheDocument();
  });
});
