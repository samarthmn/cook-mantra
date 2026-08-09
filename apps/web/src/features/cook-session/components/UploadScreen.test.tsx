import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { UploadScreen } from "./UploadScreen";

afterEach(cleanup);

describe("UploadScreen", () => {
  it("names the drop target and describes the destination check without retention promises", () => {
    render(
      <UploadScreen
        onUpload={vi.fn()}
        onManualEntry={vi.fn()}
        onWeakDetection={vi.fn()}
        runtimeStatus={<div>Runtime destination</div>}
      />,
    );

    expect(
      screen.getByRole("group", { name: "Ingredient photo drop area" }),
    ).toBeVisible();
    expect(screen.getByText(/destination shown before upload/i)).toBeVisible();
    expect(screen.queryByText(/used only/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/processed for this session/i)).not.toBeInTheDocument();
  });
});
