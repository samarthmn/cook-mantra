import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { StickyActionBar } from "./StickyActionBar";

describe("StickyActionBar", () => {
  afterEach(() => cleanup());

  it("places summary, hint, and actions in their layout slots", () => {
    render(
      <StickyActionBar
        summary="2 of 4 selected"
        hint="Each recipe is written in parallel"
        actions={
          <>
            <button type="button">More ideas</button>
            <button type="button">Create 2 recipes</button>
          </>
        }
      />,
    );

    const bar = screen.getByRole("complementary", { name: "Page actions" });
    const summary = within(bar).getByText("2 of 4 selected");
    const hint = within(bar).getByText("Each recipe is written in parallel");
    const actions = within(bar).getByRole("button", {
      name: "Create 2 recipes",
    }).parentElement;

    expect(bar).toHaveClass("sticky-action-bar");
    expect(summary).toHaveClass("sticky-action-count");
    expect(summary.parentElement).toHaveClass("sticky-action-summary");
    expect(hint).toHaveClass("sticky-action-hint");
    expect(actions).toHaveClass("sticky-action-actions");
  });
});
