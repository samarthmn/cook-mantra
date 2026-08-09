import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useRef } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { RemoteMediaProvider } from "../model/remote-media-disclosure";
import { RemoteMediaDisclosureDialog } from "./RemoteMediaDisclosureDialog";

afterEach(cleanup);

interface HarnessProps {
  provider?: RemoteMediaProvider;
  model?: string;
  errorMessage?: string | null;
  onAccept?: () => void;
  onDecline?: () => void;
  onManualEntry?: () => void;
}

function DisclosureHarness({
  provider = "openrouter",
  model = "openai/gpt-5.2",
  errorMessage,
  onAccept = vi.fn(),
  onDecline = vi.fn(),
  onManualEntry = vi.fn(),
}: HarnessProps) {
  const returnFocusRef = useRef<HTMLButtonElement>(null);
  return (
    <>
      <button ref={returnFocusRef} type="button">
        Choose from gallery
      </button>
      <RemoteMediaDisclosureDialog
        provider={provider}
        model={model}
        returnFocusRef={returnFocusRef}
        errorMessage={errorMessage}
        onAccept={onAccept}
        onDecline={onDecline}
        onManualEntry={onManualEntry}
      />
    </>
  );
}

describe("RemoteMediaDisclosureDialog", () => {
  it("names the exact remote destination and focuses the safe secondary action", () => {
    render(<DisclosureHarness />);

    const dialog = screen.getByRole("dialog", {
      name: "This photo will leave your device",
    });
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(within(dialog).getByText("OpenRouter", { selector: "bdi" })).toHaveClass(
      "privacy-dialog-provider",
    );
    expect(within(dialog).getByText("openai/gpt-5.2", { selector: "bdi" })).toHaveClass(
      "privacy-dialog-provider",
    );
    expect(dialog).toHaveTextContent(/identify ingredients for this cooking session/i);
    expect(dialog).not.toHaveTextContent(/delete|retention|retained by|stored by/i);
    expect(
      within(dialog).getByRole("button", { name: "Keep photo on this device" }),
    ).toHaveFocus();
  });

  it("never treats Enter on the safe initial action as acceptance", async () => {
    const onAccept = vi.fn();
    const onDecline = vi.fn();
    const user = userEvent.setup();
    render(<DisclosureHarness onAccept={onAccept} onDecline={onDecline} />);

    await user.keyboard("{Enter}");

    expect(onDecline).toHaveBeenCalledOnce();
    expect(onAccept).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Choose from gallery" })).toHaveFocus();
  });

  it("declines on Escape or backdrop and restores the supplied focus target", async () => {
    const onDecline = vi.fn();
    const user = userEvent.setup();
    const { container } = render(<DisclosureHarness onDecline={onDecline} />);

    await user.keyboard("{Escape}");
    expect(onDecline).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "Choose from gallery" })).toHaveFocus();

    const backdrop = container.querySelector<HTMLElement>(".dialog-backdrop");
    expect(backdrop).not.toBeNull();
    await user.pointer({ target: backdrop!, keys: "[MouseLeft]" });
    expect(onDecline).toHaveBeenCalledTimes(2);
    expect(screen.getByRole("button", { name: "Choose from gallery" })).toHaveFocus();
  });

  it("traps Tab in all three disclosure actions", async () => {
    const user = userEvent.setup();
    render(<DisclosureHarness />);
    const dialog = screen.getByRole("dialog");
    const keepLocal = within(dialog).getByRole("button", {
      name: "Keep photo on this device",
    });
    const manual = within(dialog).getByRole("button", {
      name: "Type ingredients instead",
    });

    expect(keepLocal).toHaveFocus();
    await user.tab({ shift: true });
    expect(manual).toHaveFocus();
    await user.tab();
    expect(keepLocal).toHaveFocus();
  });

  it("routes the explicit primary and manual actions without declining", async () => {
    const onAccept = vi.fn();
    const onDecline = vi.fn();
    const onManualEntry = vi.fn();
    const user = userEvent.setup();
    render(
      <DisclosureHarness
        provider="codex"
        model="gpt-5.3-codex"
        onAccept={onAccept}
        onDecline={onDecline}
        onManualEntry={onManualEntry}
      />,
    );

    expect(
      screen.getByText("Codex", { selector: ".privacy-dialog-provider" }),
    ).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Continue and send photo" }));
    await user.click(screen.getByRole("button", { name: "Type ingredients instead" }));

    expect(onAccept).toHaveBeenCalledOnce();
    expect(onManualEntry).toHaveBeenCalledOnce();
    expect(onDecline).not.toHaveBeenCalled();
  });

  it("announces persistence failures and renders the model as inert text", () => {
    const hostileModel = `<script>alert("no")</script>${"x".repeat(120)}`;
    const { container } = render(
      <DisclosureHarness
        model={hostileModel}
        errorMessage="Could not save your photo-sharing choice."
      />,
    );

    expect(screen.getByRole("alert")).toHaveTextContent(
      "Could not save your photo-sharing choice.",
    );
    expect(screen.getByText(hostileModel)).toHaveClass("privacy-dialog-provider");
    expect(container.querySelector("script")).toBeNull();
  });
});
