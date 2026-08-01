import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ErrorAlert } from "./ErrorAlert";

describe("ErrorAlert", () => {
  afterEach(() => cleanup());

  it("announces the error and offers retry and dismiss actions", async () => {
    const user = userEvent.setup();
    const onRetry = vi.fn();
    const onDismiss = vi.fn();
    const scrollIntoView = vi.fn();
    Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
      configurable: true,
      value: scrollIntoView,
    });
    render(
      <ErrorAlert
        title="That took too long"
        message="Nothing was lost — your ingredients are exactly as you left them."
        onRetry={onRetry}
        onDismiss={onDismiss}
      />,
    );

    const alert = screen.getByRole("alert");
    const retry = within(alert).getByRole("button", { name: "Try again" });
    const dismiss = within(alert).getByRole("button", { name: "Dismiss" });

    expect(alert).toHaveClass("alert");
    expect(alert).toHaveAttribute("tabindex", "-1");
    expect(alert).toHaveFocus();
    expect(scrollIntoView).toHaveBeenCalledWith({
      block: "nearest",
      behavior: "smooth",
    });
    expect(alert.querySelector("svg.alert-icon")).toHaveAttribute(
      "aria-hidden",
      "true",
    );
    expect(within(alert).getByText("That took too long")).toHaveClass("alert-title");
    expect(
      within(alert).getByText(
        "Nothing was lost — your ingredients are exactly as you left them.",
      ),
    ).toHaveClass("alert-body");
    expect(retry).toHaveClass("btn", "btn-primary");
    expect(dismiss).toHaveClass("btn", "btn-ghost");

    await user.click(retry);
    await user.click(dismiss);
    expect(onRetry).toHaveBeenCalledOnce();
    expect(onDismiss).toHaveBeenCalledOnce();
  });

  it("uses non-animated scrolling when reduced motion is requested", () => {
    vi.spyOn(window, "matchMedia").mockImplementation(
      (query) =>
        ({
          matches: query === "(prefers-reduced-motion: reduce)",
          media: query,
          onchange: null,
          addEventListener() {},
          removeEventListener() {},
          addListener() {},
          removeListener() {},
          dispatchEvent: () => false,
        }) as MediaQueryList,
    );
    const scrollIntoView = vi.fn();
    Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
      configurable: true,
      value: scrollIntoView,
    });

    render(<ErrorAlert title="Offline" message="Try again later." />);

    expect(scrollIntoView).toHaveBeenCalledWith({
      block: "nearest",
      behavior: "auto",
    });
  });
});
