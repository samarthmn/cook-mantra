# Cook Mantra API

Cook Mantra is a local FastAPI backend that turns an ingredient photo into
reviewable ingredients, text-only recipe suggestions, and complete recipes.
After a complete recipe is written, the optional image role may add one generated
dish preview. Agent roles use a provider-neutral runtime;
native Ollama and OpenRouter text/vision transports are supported without
provider or model fallback. The Codex transport is an experimental,
disabled-by-default preview that uses only the official local `codex app-server`
and requires an explicit provider-level risk opt-in.

## Local setup

Prerequisites:

- Python 3.13 or newer
- [`uv`](https://docs.astral.sh/uv/)
- [Ollama](https://ollama.com/) available to the API for roles that select it
- An OpenRouter API key for roles that select OpenRouter
- The local `codex` CLI with an existing CLI login for roles that select Codex

The primary development command starts both services from the repository root:

```bash
pnpm install
pnpm dev
```

The API listens on `http://127.0.0.1:8000`; the web app listens on
`http://localhost:3000`. Keep the individual commands available for diagnosis:

```bash
pnpm api:dev
pnpm web:dev
```

For API-only development, install the locked dependencies and create local
configuration from `apps/api`:

```bash
uv sync
cp ../../example.env ../../.env
```

Model runtime configuration lives in `../../config/cook-mantra.yaml`. It owns
role-to-provider/model selection, declared capabilities, editable static
instructions, safe tuning, and non-secret connection metadata. Use
`COOK_MANTRA_CONFIG_PATH` to select another file. See
`../../config/cook-mantra.example.yaml` for all supported providers. Ollama and
OpenRouter are available for structured text and vision roles. The Codex
adapter implements structured text and vision. Its
`allow_unverified_tool_boundary` setting defaults to `false`, which keeps Codex
roles capability-unavailable. Setting it to `true` enables the functional
preview on a trusted personal machine. The runtime never falls back to another
provider or model. Restart the API after changing the YAML because configuration
and provider discovery are cached for the process lifetime.

Provider credentials never belong in YAML. A provider may only name the
environment variable that contains its secret (for example,
`api_key_env: OPENROUTER_API_KEY`). The standard `OPENROUTER_API_KEY` name may
be set in the project `.env`; a custom `api_key_env` name must be exported in
the API process environment.

OpenRouter is a remote service. When selected for ingredient extraction, the
validated ingredient image is encoded and sent to OpenRouter along with the
prompt; selected text roles likewise send their prompts and outputs remotely.
The browser refreshes `/api/v1/runtime-status` before every photo upload. An
Ollama ingredient role is admitted only when its endpoint uses `localhost` or a
numeric loopback address, and OpenRouter media is admitted only at
`https://openrouter.ai/api/v1`. OpenRouter and Codex media trigger a disclosure
that names the exact provider and model. The browser stores a versioned
acknowledgement for that destination and asks again when either value changes.
It creates no `FormData`, object URL, or server session until the user accepts.
Declining or choosing manual ingredient entry sends no photo.

Codex uses one API-owned `codex app-server` stdio child for the FastAPI
process. The child starts lazily for readiness or invocation, reuses the
existing Codex-managed sign-in, and receives no API key from Cook Mantra. Discovery calls
only initialization, account status, the paginated model catalog, and provider
capabilities; it creates no thread or turn. Each implemented invocation uses
one temporary thread that is explicitly deleted, exact YAML model selection,
an API-owned empty working
directory, read-only sandboxing, disabled turn network access, no Cook Mantra
dynamic tools or external environments, and mandatory cleanup. The installed
read-only policy does not expose a readable-root restriction. Vision uses one
single-use local copy and removes it after terminal cleanup.

This integration is not a generic ChatGPT proxy. Cook Mantra never requests,
reads, or stores ChatGPT credentials; authentication remains managed by the
official Codex CLI.

The child inherits only an explicit CLI allowlist (`HOME`, optional
`CODEX_HOME`, `PATH`, locale/platform values) plus an API-owned temporary
directory; OpenRouter, LangSmith, OpenAI, and other API-process secrets
are not forwarded. Readiness, serialized queueing, startup, discovery, replay,
turn I/O, cleanup, and shutdown are bounded. A child that does not exit after
terminate is killed and reaped within the cleanup grace.

The adapter rejects every server request, model reroute, tool/file/shell/web/
MCP/collaboration/reasoning/image event, malformed frame, mismatched ID, and
invalid final output. This detection cannot prevent a read-only built-in tool
from acting before its event arrives. With the opt-in `false`, readiness reports
`model_capability_missing`. With it `true`, an exact matching Codex text or
vision model can become ready, and the user accepts that event rejection is not
pre-execution prevention. Codex image output remains unavailable because the
schema has no documented request-to-raster handoff.

The app-server may initialize MCP servers already present in the user's Codex
configuration when a thread starts. Cook Mantra validates and ignores only
their startup-status notifications; any observed MCP tool call remains forbidden
and terminates the child. The opt-in also accepts this inherited configuration
boundary.

Install and authenticate the local `codex` CLI before selecting a Codex role,
then configure `command: [codex, app-server]`, set
`allow_unverified_tool_boundary: true`, and select the exact model
`model: gpt-5.6-sol`. Keep the image role disabled. The runtime verifies that
exact model against the signed-in account's app-server catalog. Restart the API
after changing the setting. Do not start the child manually.

Prompts, accepted photos, and responses for Codex roles may be sent to OpenAI
through the Codex-managed sign-in. Cook Mantra does not read or store ChatGPT
credentials. The functional preview is intended for a trusted personal machine,
not a production or multi-user service.

Each Codex turn forces `effort: none` and `summary: none` so reasoning items are
not emitted across the adapter boundary. Keep Codex role tuning at
`reasoning_effort: none`; other configured reasoning values are ignored for this
preview.

The optional image role supports native Ollama or OpenRouter adapters. An
OpenRouter image selection also needs one exact discovered `provider_tag` in
YAML. Discovery binds one configured provider, model, endpoint, and tuning set;
the runtime never changes them or falls back. Codex image generation remains
capability-unavailable and creates no app-server child by itself.

Recipe options are text-only and never invoke the image role. After each Recipe
Writer Agent succeeds, the complete-recipe workflow may make at most one preview
attempt for that recipe. Writer failure makes no image call. Preview failure
keeps the complete recipe, stores no invalid artifact, and adds the fixed warning
`Dish preview unavailable.` A disabled image role performs no discovery or
generation and adds no warning.

Image readiness fails closed unless the provider can prove every configured
width, height, quality, and output-format value. The audited Ollama 0.32.5 API
cannot request quality or output format, and the current OpenRouter image
catalog does not describe exact pixel size. Those combinations make no image
generation request. A future compatible endpoint must pass the same discovery
gate before one request can run.

Before storage, the central image boundary decodes one raw-base64 result and
verifies its type, byte limit, dimensions, format, frame count, and complete
Pillow decode. It accepts only static PNG, JPEG, or WebP data. It never fetches
provider-hosted image URLs, resizes output, or transcodes it.

Pull the models selected for enabled Ollama roles in `config/cook-mantra.yaml`
before starting the API. The tracked default uses:

```bash
ollama pull qwen3.5:9b
ollama pull gpt-oss:20b
```

Start only the API on the loopback interface:

```bash
uv run uvicorn main:app --host 127.0.0.1 --port 8000
```

Useful local URLs:

- Process health: `http://127.0.0.1:8000/api/v1/health`
- Browser-safe model status: `http://127.0.0.1:8000/api/v1/runtime-status`
- Required model-role readiness: `http://127.0.0.1:8000/api/v1/ready`
- Interactive OpenAPI docs: `http://127.0.0.1:8000/docs`
- OpenAPI JSON: `http://127.0.0.1:8000/openapi.json`

`/health` checks only the process. `/runtime-status` returns HTTP 200 with
`Cache-Control: no-store`, a safe per-role snapshot, and the immutable
`runtime_revision` for this API process. Its top-level status is `attention`
when any enabled role needs attention, including the optional image role.
`model_runtime.ready` and `/ready` cover required roles, so image failure does
not make `/ready` fail.

Browser photo uploads must return the current revision in
`X-Cook-Mantra-Runtime-Revision`. A missing or stale browser revision returns
`409 runtime_status_stale` before multipart parsing consumes the request body.
The revision is concurrency metadata, not consent. Origin-less direct clients
may omit it, but those clients own their media-disclosure flow.

The default CORS allowlist accepts only the local browser origins in `.env`.
The host and port come from the `uvicorn` flags above; keep `--host 127.0.0.1`
unless you intentionally want a wider network exposure.

## Postman collection

Import
[`postman/Cook-Mantra.postman_collection.json`](postman/Cook-Mantra.postman_collection.json)
into Postman as a collection. Start the API, open request **04 Create Session**,
select an ingredient image, and then send the numbered requests in order.

Request **02 Runtime Status** stores the process revision used by the upload.
That value is not consent; the Postman operator remains responsible for media
disclosure. The collection stores session, job, and recipe-option IDs
automatically. After complete recipes are ready, it stores the first available
preview-artifact ID. Model operations run as background jobs, so repeat each numbered
**Poll** request until its response status is `succeeded` before continuing.
Collection variables let you change how many extracted ingredients are
confirmed. After request **11** or **15**, copy only the recipe option IDs you
want into the `selected_option_ids_json` collection variable. Request **16**
generates complete recipes only for that explicit selection and refuses to run
while the selection is empty.

## Verification and development

The default suite is deterministic and makes no Ollama or internet calls:

```bash
uv run pytest -m "not live" -W error
uv run ruff format --check src tests
uv run ruff check src tests
uv lock --check
uv run python -c "from main import app; app.openapi()"
uv run langgraph validate
```

The complete fake-service API journey is marked `e2e`:

```bash
uv run pytest -m e2e tests/e2e/test_complete_journey.py -q
```

To inspect the three development graphs in LangGraph Studio:

```bash
uv run langgraph dev
```

`langgraph.json` points Studio at the ingredient-extraction, recipe-option, and
complete-recipe factories. Studio is a development view; FastAPI remains the
public API and owns runtime state.

## Opt-in live checks

Provider live checks were not run for this documentation update. They remain
skipped by default. The `COOK_MANTRA_RUN_LIVE` tests target the tracked Ollama
text and vision setup; the deterministic suite never starts Codex or calls a
remote provider:

```bash
COOK_MANTRA_RUN_LIVE=1 uv run pytest -m live tests/live -q
```

This includes the full live API journey and can take several minutes because it
runs multiple structured model calls. Provider image adapters use deterministic
contract tests instead of a billable live-generation test.

LangSmith has a separate opt-in smoke check:

```bash
COOK_MANTRA_RUN_LANGSMITH_LIVE=1 uv run pytest -m live tests/live/test_langsmith_live.py -q
```

For that smoke check, `LANGSMITH_TRACING=true` and a valid
`LANGSMITH_API_KEY` must also be present. Never commit the local `.env` or
credentials.

## Runtime and privacy

- Sessions and jobs live only in process memory. Restarting the API removes
  them.
- Uploaded and generated images are temporary artifacts under the repository's
  `tmp/cook-mantra-api` namespace by default. Expired session, job, and artifact
  data is periodically removed according to `SESSION_TTL_SECONDS`; the artifact
  namespace is cleared when a new API process starts.
- A selected Codex role starts one supervised local child only when readiness
  needs discovery. FastAPI shutdown interrupts active work, deletes confirmed
  temporary threads, and terminates and reaps the child before artifact
  storage closes. Provider frames, account data, prompts, outputs, paths,
  media, stderr, and raw errors never enter logs or tracing.
- `MAX_CONCURRENT_JOBS`, `MAX_QUEUED_JOBS`, and
  `MAX_CONCURRENT_MODEL_CALLS` bound local work. A full queue returns
  `503 service_busy` instead of growing without limit. These and the other
  service limits default from constants in `apps/api/src/core/config.py`.
  Provider/model tuning belongs to `config/cook-mantra.yaml`, not `.env`.
- Logs are structured JSON with request, session, and job correlation IDs.
  Secret-like fields, binary image data, and base64-looking values are
  redacted.
- LangSmith tracing is disabled unless explicitly enabled with credentials.
  When enabled, it receives only bounded role/provider/model identity, job
  correlation, aggregate token usage, and bounded vision summaries: media type,
  byte count, detected count, and warning count. LangSmith never receives
  prompts, model outputs, raw provider responses, or ingredient image
  bytes/base64.

## Troubleshooting

- **Provider unavailable:** inspect the role-based `model_runtime` result from
  `/api/v1/ready`, then check the selected provider connection in the YAML.
- **Invalid model configuration:** `/health` remains live, while `/ready` and
  model-dependent routes return `model_configuration_invalid`. Fix the YAML
  reported by `COOK_MANTRA_CONFIG_PATH` and restart the process.
- **Missing capability/model:** `/api/v1/ready` identifies the affected role
  without exposing provider-specific details. Correct its authoritative
  capability list, install the selected Ollama model, or select an OpenRouter
  model that advertises the required modalities and structured outputs, then
  restart.
- **Codex role unavailable:** confirm the CLI is installed and signed in, the
  exact `gpt-5.6-sol` model appears in its catalog, and the API was restarted.
  The safe default `allow_unverified_tool_boundary: false` deliberately blocks
  the role. Set it to `true` only if the documented preview boundary is
  acceptable; no provider fallback occurs automatically.
- **Operation timed out:** a provider exceeded the bounded timeout selected in
  YAML. Confirm the provider is responsive before increasing that role's
  timeout.
- **Image role unavailable:** verify the exact model, configured
  `image_output` capability, tuning values, endpoint, credentials, and (for
  OpenRouter) `provider_tag`. Optional image failure does not make required
  text/vision readiness fail.
- **Port already in use:** stop the conflicting process or pass a different
  `--port` to `uvicorn`.
- **`service_busy`:** wait for active jobs to finish or carefully adjust the
  bounded job settings for the machine's available memory.
- **Live test skipped:** set the exact opt-in flag shown above. LangSmith's
  smoke additionally needs tracing enabled and credentials; Ollama checks use
  `COOK_MANTRA_RUN_LIVE=1`.
