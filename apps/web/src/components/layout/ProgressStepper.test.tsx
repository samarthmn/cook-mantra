import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ProgressStepper } from "./ProgressStepper";

describe("ProgressStepper", () => {
  afterEach(() => cleanup());

  it("marks the current step and only enables completed earlier steps", async () => {
    const user = userEvent.setup();
    const onStepChange = vi.fn();
    render(<ProgressStepper currentIndex={2} onStepChange={onStepChange} />);

    const progress = screen.getByRole("navigation", { name: "Progress" });
    const photo = screen.getByRole("button", { name: /Photo/ });
    const confirm = screen.getByRole("button", { name: /Confirm/ });
    const choose = screen.getByRole("button", { name: /Choose/ });
    const cook = screen.getByRole("button", { name: /Cook/ });

    expect(progress).toHaveClass("progress-stepper");
    expect(photo).toBeEnabled();
    expect(photo).toHaveClass("progress-step", "is-complete");
    expect(confirm).toBeEnabled();
    expect(confirm).toHaveClass("is-complete");
    expect(choose).toBeDisabled();
    expect(choose).toHaveClass("is-current");
    expect(choose).toHaveAttribute("aria-current", "step");
    expect(cook).toBeDisabled();
    expect(cook).toHaveClass("is-locked");

    await user.click(confirm);
    expect(onStepChange).toHaveBeenCalledOnce();
    expect(onStepChange).toHaveBeenCalledWith(1);
  });

  it("locks every step while a job is running", async () => {
    const user = userEvent.setup();
    const onStepChange = vi.fn();
    render(<ProgressStepper currentIndex={2} locked onStepChange={onStepChange} />);

    const buttons = screen.getAllByRole("button");
    expect(buttons).toHaveLength(4);
    for (const button of buttons) {
      expect(button).toBeDisabled();
      expect(button).toHaveClass("is-locked");
    }

    await user.click(screen.getByRole("button", { name: /Photo/ }));
    expect(onStepChange).not.toHaveBeenCalled();
  });

  it("lets people return to any previously reached step", async () => {
    const user = userEvent.setup();
    const onStepChange = vi.fn();
    render(
      <ProgressStepper
        currentIndex={2}
        maxReachedIndex={3}
        onStepChange={onStepChange}
      />,
    );

    const cook = screen.getByRole("button", { name: /Cook/ });
    expect(cook).toBeEnabled();
    expect(cook).toHaveClass("is-complete");

    await user.click(cook);
    expect(onStepChange).toHaveBeenCalledWith(3);
  });
});
