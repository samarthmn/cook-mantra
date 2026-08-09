export type RemoteMediaProvider = "openrouter" | "codex";

export interface RemoteMediaDisclosureTarget {
  provider: RemoteMediaProvider;
  model: string;
}

export interface RemoteMediaAcknowledgement {
  provider: RemoteMediaProvider;
  model: string;
  disclosureVersion: typeof REMOTE_MEDIA_DISCLOSURE_VERSION;
  acceptedAt: string;
}

interface StoredRemoteMediaAcknowledgements {
  version: typeof REMOTE_MEDIA_DISCLOSURE_VERSION;
  records: Partial<Record<RemoteMediaProvider, RemoteMediaAcknowledgement>>;
}

export const REMOTE_MEDIA_DISCLOSURE_VERSION = 1;
export const REMOTE_MEDIA_DISCLOSURE_STORAGE_KEY =
  "cook-mantra:remote-media-disclosures:v1";

const REMOTE_MEDIA_PROVIDERS = ["openrouter", "codex"] as const;
const ENVELOPE_KEYS = ["records", "version"] as const;
const ACKNOWLEDGEMENT_KEYS = [
  "acceptedAt",
  "disclosureVersion",
  "model",
  "provider",
] as const;

export function hasMatchingRemoteMediaAcknowledgement(
  storage: Storage,
  target: RemoteMediaDisclosureTarget,
): boolean {
  if (!hasValidTargetModel(target)) return false;

  try {
    const stored = parseStoredAcknowledgements(
      storage.getItem(REMOTE_MEDIA_DISCLOSURE_STORAGE_KEY),
    );
    return acknowledgementMatches(stored?.records[target.provider], target);
  } catch {
    return false;
  }
}

export function persistRemoteMediaAcknowledgement(
  storage: Storage,
  target: RemoteMediaDisclosureTarget,
): boolean {
  if (!hasValidTargetModel(target)) return false;

  try {
    const current = parseStoredAcknowledgements(
      storage.getItem(REMOTE_MEDIA_DISCLOSURE_STORAGE_KEY),
    );
    const acknowledgement: RemoteMediaAcknowledgement = {
      provider: target.provider,
      model: target.model,
      disclosureVersion: REMOTE_MEDIA_DISCLOSURE_VERSION,
      acceptedAt: new Date().toISOString(),
    };
    const next: StoredRemoteMediaAcknowledgements = {
      version: REMOTE_MEDIA_DISCLOSURE_VERSION,
      records: {
        ...(current?.records ?? {}),
        [target.provider]: acknowledgement,
      },
    };

    const serialized = JSON.stringify(next);
    storage.setItem(REMOTE_MEDIA_DISCLOSURE_STORAGE_KEY, serialized);

    const persisted = storage.getItem(REMOTE_MEDIA_DISCLOSURE_STORAGE_KEY);
    if (persisted !== serialized) return false;
    const verified = parseStoredAcknowledgements(persisted);
    const verifiedRecord = verified?.records[target.provider];
    return (
      acknowledgementMatches(verifiedRecord, target) &&
      verifiedRecord?.acceptedAt === acknowledgement.acceptedAt
    );
  } catch {
    return false;
  }
}

function hasValidTargetModel(target: RemoteMediaDisclosureTarget): boolean {
  return target.model.trim().length > 0;
}

function acknowledgementMatches(
  acknowledgement: RemoteMediaAcknowledgement | undefined,
  target: RemoteMediaDisclosureTarget,
): boolean {
  return (
    acknowledgement?.provider === target.provider &&
    acknowledgement.model === target.model &&
    acknowledgement.disclosureVersion === REMOTE_MEDIA_DISCLOSURE_VERSION
  );
}

function parseStoredAcknowledgements(
  value: string | null,
): StoredRemoteMediaAcknowledgements | null {
  if (value === null) return null;

  let parsed: unknown;
  try {
    parsed = JSON.parse(value);
  } catch {
    return null;
  }

  if (!isPlainRecord(parsed) || !hasExactKeys(parsed, ENVELOPE_KEYS)) {
    return null;
  }
  if (
    parsed.version !== REMOTE_MEDIA_DISCLOSURE_VERSION ||
    !isPlainRecord(parsed.records)
  ) {
    return null;
  }

  const recordKeys = Object.keys(parsed.records);
  if (!recordKeys.every(isRemoteMediaProvider)) return null;

  const records: StoredRemoteMediaAcknowledgements["records"] = {};
  for (const provider of REMOTE_MEDIA_PROVIDERS) {
    const candidate = parsed.records[provider];
    if (candidate === undefined) continue;
    if (!isAcknowledgement(candidate, provider)) return null;
    records[provider] = candidate;
  }

  return { version: REMOTE_MEDIA_DISCLOSURE_VERSION, records };
}

function isAcknowledgement(
  value: unknown,
  expectedProvider: RemoteMediaProvider,
): value is RemoteMediaAcknowledgement {
  return (
    isPlainRecord(value) &&
    hasExactKeys(value, ACKNOWLEDGEMENT_KEYS) &&
    value.provider === expectedProvider &&
    typeof value.model === "string" &&
    value.model.trim().length > 0 &&
    value.disclosureVersion === REMOTE_MEDIA_DISCLOSURE_VERSION &&
    typeof value.acceptedAt === "string" &&
    isCanonicalTimestamp(value.acceptedAt)
  );
}

function isCanonicalTimestamp(value: string): boolean {
  const timestamp = Date.parse(value);
  return Number.isFinite(timestamp) && new Date(timestamp).toISOString() === value;
}

function isRemoteMediaProvider(value: string): value is RemoteMediaProvider {
  return (REMOTE_MEDIA_PROVIDERS as readonly string[]).includes(value);
}

function isPlainRecord(value: unknown): value is Record<string, unknown> {
  return (
    typeof value === "object" &&
    value !== null &&
    !Array.isArray(value) &&
    Object.getPrototypeOf(value) === Object.prototype
  );
}

function hasExactKeys(
  value: Record<string, unknown>,
  expectedKeys: readonly string[],
): boolean {
  const keys = Object.keys(value).sort();
  const expected = [...expectedKeys].sort();
  return (
    keys.length === expected.length &&
    keys.every((key, index) => key === expected[index])
  );
}
