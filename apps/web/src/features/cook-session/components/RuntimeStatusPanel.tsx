import type {
  ModelProvider,
  ModelRole,
  ModelRoleReadiness,
  RuntimeStatusResponse,
} from "@/types/api";

export type RuntimeStatusState =
  | { phase: "loading" }
  | { phase: "available"; snapshot: RuntimeStatusResponse }
  | { phase: "unavailable" };

interface RuntimeStatusPanelProps {
  state: RuntimeStatusState;
  onRefresh: () => void;
}

const ROLE_ROWS: ReadonlyArray<{ role: ModelRole; label: string }> = [
  { role: "ingredient_extractor", label: "Ingredient recognition" },
  { role: "master_chef", label: "Recipe ideas" },
  { role: "recipe_writer", label: "Recipe writing" },
  { role: "image_generator", label: "Dish preview" },
];

const PROVIDER_LABELS: Record<ModelProvider, string> = {
  ollama: "Ollama",
  openrouter: "OpenRouter",
  codex: "Codex",
};

export function RuntimeStatusPanel({ state, onRefresh }: RuntimeStatusPanelProps) {
  return (
    <section className="runtime-status-panel" aria-labelledby="runtime-status-title">
      <div className="runtime-status-header">
        <div>
          <p className="eyebrow">Local setup</p>
          <h2 id="runtime-status-title">Local model status</h2>
        </div>
        <button className="btn btn-ghost" type="button" onClick={onRefresh}>
          Refresh local status
        </button>
      </div>

      <div role="status" aria-live="polite" aria-atomic="true">
        {state.phase === "loading" ? (
          <p className="runtime-ledger-caption">Checking local model status…</p>
        ) : null}

        {state.phase === "unavailable" ? (
          <p className="runtime-status-warning">
            Could not read local model status. Check that the local API is running
            before sending a photo.
          </p>
        ) : null}

        {state.phase === "available" ? (
          <RuntimeStatusDetails snapshot={state.snapshot} />
        ) : null}
      </div>
    </section>
  );
}

function RuntimeStatusDetails({ snapshot }: { snapshot: RuntimeStatusResponse }) {
  const ingredientRole = snapshot.model_runtime.roles.ingredient_extractor;
  const imageRole = snapshot.model_runtime.roles.image_generator;
  const recipeRoleNeedsAttention = [
    snapshot.model_runtime.roles.master_chef,
    snapshot.model_runtime.roles.recipe_writer,
  ].some((role) => !role.ready);

  return (
    <>
      <ol className="runtime-ledger">
        {ROLE_ROWS.map(({ role, label }) => {
          const readiness = snapshot.model_runtime.roles[role];
          const status = roleStatus(readiness);
          return (
            <li className="runtime-ledger-row" key={role}>
              <div>
                <h3>{label}</h3>
                <p className="runtime-ledger-caption">
                  <ProviderModelCaption readiness={readiness} />
                </p>
              </div>
              <span className={`tag ${status.className}`}>{status.label}</span>
            </li>
          );
        })}
      </ol>

      <div className="runtime-status-guidance">
        {!ingredientRole.ready ? (
          <p>
            Ingredient recognition needs attention. Type ingredients instead until it is
            ready.
          </p>
        ) : null}
        {recipeRoleNeedsAttention ? (
          <p>Recipe generation needs attention before it can run.</p>
        ) : null}
        {imageRole.enabled && !imageRole.ready ? (
          <p>Dish previews are unavailable; recipes can still be created.</p>
        ) : null}
        <p className="runtime-ledger-caption">
          Model setting changes take effect after the local API restarts.
        </p>
      </div>
    </>
  );
}

function roleStatus(readiness: ModelRoleReadiness): {
  label: "Ready" | "Needs attention" | "Disabled";
  className: "tag-accent" | "tag-outline" | "tag-neutral";
} {
  if (!readiness.enabled) {
    return { label: "Disabled", className: "tag-neutral" };
  }
  if (readiness.ready) {
    return { label: "Ready", className: "tag-accent" };
  }
  return { label: "Needs attention", className: "tag-outline" };
}

function ProviderModelCaption({ readiness }: { readiness: ModelRoleReadiness }) {
  if (readiness.provider === null && readiness.model === null) {
    return <>Provider and model not configured</>;
  }

  const provider = readiness.provider
    ? PROVIDER_LABELS[readiness.provider]
    : "Provider not configured";
  const model = readiness.model ?? "Model not configured";
  return (
    <>
      <bdi>{provider}</bdi> · <bdi>{model}</bdi>
    </>
  );
}
