import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  REMOTE_MEDIA_DISCLOSURE_STORAGE_KEY,
  hasMatchingRemoteMediaAcknowledgement,
  persistRemoteMediaAcknowledgement,
  type RemoteMediaDisclosureTarget,
} from "./remote-media-disclosure";

const openRouterTarget: RemoteMediaDisclosureTarget = {
  provider: "openrouter",
  model: "google/gemini-2.5-flash",
};
const codexTarget: RemoteMediaDisclosureTarget = {
  provider: "codex",
  model: "gpt-5.3-codex",
};

describe("remote media disclosure acknowledgements", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.useRealTimers();
  });

  it("uses a versioned storage key so copy changes cannot inherit old consent", () => {
    expect(REMOTE_MEDIA_DISCLOSURE_STORAGE_KEY).toMatch(/v1$/);
  });

  it("persists and independently matches exact OpenRouter and Codex models", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-06T10:00:00.000Z"));

    expect(persistRemoteMediaAcknowledgement(localStorage, openRouterTarget)).toBe(
      true,
    );
    expect(persistRemoteMediaAcknowledgement(localStorage, codexTarget)).toBe(true);

    expect(hasMatchingRemoteMediaAcknowledgement(localStorage, openRouterTarget)).toBe(
      true,
    );
    expect(hasMatchingRemoteMediaAcknowledgement(localStorage, codexTarget)).toBe(true);
    expect(
      hasMatchingRemoteMediaAcknowledgement(localStorage, {
        ...openRouterTarget,
        model: "google/gemini-2.5-pro",
      }),
    ).toBe(false);

    expect(
      JSON.parse(localStorage.getItem(REMOTE_MEDIA_DISCLOSURE_STORAGE_KEY)!),
    ).toEqual({
      version: 1,
      records: {
        openrouter: {
          provider: "openrouter",
          model: openRouterTarget.model,
          disclosureVersion: 1,
          acceptedAt: "2026-08-06T10:00:00.000Z",
        },
        codex: {
          provider: "codex",
          model: codexTarget.model,
          disclosureVersion: 1,
          acceptedAt: "2026-08-06T10:00:00.000Z",
        },
      },
    });
  });

  it.each([
    "not-json",
    "[]",
    JSON.stringify({ version: 1, records: {}, extra: true }),
    JSON.stringify({ version: 2, records: {} }),
    JSON.stringify({ version: 1, records: { unexpected: {} } }),
    JSON.stringify({
      version: 1,
      records: {
        openrouter: {
          provider: "openrouter",
          model: openRouterTarget.model,
          disclosureVersion: 1,
          acceptedAt: "0",
        },
      },
    }),
    JSON.stringify({
      version: 1,
      records: {
        openrouter: {
          provider: "openrouter",
          model: openRouterTarget.model,
          disclosureVersion: 1,
          acceptedAt: "2026-08-06",
        },
      },
    }),
    JSON.stringify({
      version: 1,
      records: {
        openrouter: {
          provider: "codex",
          model: openRouterTarget.model,
          disclosureVersion: 1,
          acceptedAt: "2026-08-06T10:00:00.000Z",
        },
      },
    }),
    JSON.stringify({
      version: 1,
      records: {
        openrouter: {
          provider: "openrouter",
          model: openRouterTarget.model,
          disclosureVersion: 1,
          acceptedAt: "not-a-date",
        },
      },
    }),
    JSON.stringify({
      version: 1,
      records: {
        openrouter: {
          provider: "openrouter",
          model: openRouterTarget.model,
          disclosureVersion: 1,
          acceptedAt: "2026-08-06T10:00:00.000Z",
          extra: true,
        },
      },
    }),
  ])("rejects malformed or non-versioned storage: %s", (stored) => {
    localStorage.setItem(REMOTE_MEDIA_DISCLOSURE_STORAGE_KEY, stored);

    expect(hasMatchingRemoteMediaAcknowledgement(localStorage, openRouterTarget)).toBe(
      false,
    );
  });

  it("treats unavailable, quota-failed, or non-round-trippable storage as no consent", () => {
    const unavailable = storageDouble({
      getItem: () => {
        throw new DOMException("Blocked", "SecurityError");
      },
    });
    const quotaFailed = storageDouble({
      setItem: () => {
        throw new DOMException("Full", "QuotaExceededError");
      },
    });
    const dropsWrites = storageDouble({ setItem: () => undefined });

    expect(hasMatchingRemoteMediaAcknowledgement(unavailable, openRouterTarget)).toBe(
      false,
    );
    expect(persistRemoteMediaAcknowledgement(quotaFailed, openRouterTarget)).toBe(
      false,
    );
    expect(persistRemoteMediaAcknowledgement(dropsWrites, openRouterTarget)).toBe(
      false,
    );
  });

  it("repairs malformed storage but trusts the write only after exact read-back", () => {
    localStorage.setItem(REMOTE_MEDIA_DISCLOSURE_STORAGE_KEY, "malformed");

    expect(persistRemoteMediaAcknowledgement(localStorage, openRouterTarget)).toBe(
      true,
    );
    expect(hasMatchingRemoteMediaAcknowledgement(localStorage, openRouterTarget)).toBe(
      true,
    );

    const tampered = storageDouble({
      getItem: () =>
        JSON.stringify({
          version: 1,
          records: {
            openrouter: {
              provider: "openrouter",
              model: "tampered-model",
              disclosureVersion: 1,
              acceptedAt: "2026-08-06T10:00:00.000Z",
            },
          },
        }),
    });
    expect(persistRemoteMediaAcknowledgement(tampered, openRouterTarget)).toBe(false);
  });

  it("rejects a write that loses another provider's valid acknowledgement", () => {
    let stored = JSON.stringify({
      version: 1,
      records: {
        openrouter: {
          provider: "openrouter",
          model: openRouterTarget.model,
          disclosureVersion: 1,
          acceptedAt: "2026-08-06T10:00:00.000Z",
        },
      },
    });
    const losesExistingRecord = storageDouble({
      getItem: () => stored,
      setItem: (_key, value) => {
        const parsed = JSON.parse(value) as {
          records: Partial<Record<"openrouter" | "codex", unknown>>;
        };
        delete parsed.records.openrouter;
        stored = JSON.stringify(parsed);
      },
    });

    expect(persistRemoteMediaAcknowledgement(losesExistingRecord, codexTarget)).toBe(
      false,
    );
    expect(
      hasMatchingRemoteMediaAcknowledgement(losesExistingRecord, openRouterTarget),
    ).toBe(false);
  });

  it("rejects blank target models before reading or writing storage", () => {
    const getItem = vi.fn<Storage["getItem"]>();
    const setItem = vi.fn<Storage["setItem"]>();
    const storage = storageDouble({ getItem, setItem });

    expect(
      hasMatchingRemoteMediaAcknowledgement(storage, {
        provider: "openrouter",
        model: "   ",
      }),
    ).toBe(false);
    expect(
      persistRemoteMediaAcknowledgement(storage, {
        provider: "openrouter",
        model: "   ",
      }),
    ).toBe(false);
    expect(getItem).not.toHaveBeenCalled();
    expect(setItem).not.toHaveBeenCalled();
  });
});

function storageDouble(
  overrides: Partial<Pick<Storage, "getItem" | "setItem">>,
): Storage {
  const values = new Map<string, string>();
  return {
    get length() {
      return values.size;
    },
    clear: () => values.clear(),
    getItem: overrides.getItem ?? ((key) => values.get(key) ?? null),
    key: (index) => [...values.keys()][index] ?? null,
    removeItem: (key) => values.delete(key),
    setItem:
      overrides.setItem ??
      ((key, value) => {
        values.set(key, value);
      }),
  };
}
