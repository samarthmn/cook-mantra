# Cook Mantra API

Cook Mantra is a local FastAPI backend that turns an ingredient photo into
reviewable ingredients, recipe suggestions with nutrition and generated dish
previews, and complete recipes. Ollama runs text agents, and the Beast API
generates dish previews.

For a plain-language walkthrough of every implemented stage, read
[`../../docs/what-was-built.md`](../../docs/what-was-built.md).

## Local setup

Prerequisites:

- Python 3.13 or newer
- [`uv`](https://docs.astral.sh/uv/)
- [Ollama](https://ollama.com/) available to the API
- Beast API access when dish previews are enabled

From `apps/api`, install the locked dependencies and create local configuration:

```bash
uv sync
cp ../../example.env ../../.env
```

`OLLAMA_BASE_URL` is required. The API reads it from the environment or the
repository-root `.env` file and refuses to start when it is missing or invalid.

Dish previews use the Beast API described at
`http://192.168.29.16:4900/guide`. When `DISH_PREVIEWS_ENABLED=true`, both
`BEAST_BASE_URL` and `BEAST_API_KEY` are required. The API submits an image job,
polls it to completion, downloads the first output, and verifies its size,
SHA-256 digest, and image data before storing it as a temporary artifact.

The four configured Ollama text models are fixed in `src/core/config.py`. Pull
them on the `OLLAMA_BASE_URL` host before starting the API:

```bash
ollama pull qwen3.5:27b
ollama pull qwen3.5:9b
ollama pull gemma4:26b
ollama pull gpt-oss:20b
```

### Beast output-download route

Completed jobs are downloaded through this contract, which the Beast server
implements; the Cook Mantra client keeps its route construction in one method
so the path can be changed in one line if the server ever moves it:

```text
GET {base}/v1/jobs/{job_id}/outputs/{index}
Authorization: Bearer <key>
→ 200, Content-Type: <mime_type>, body = raw image bytes
→ 404 for unknown job or index
```

When Beast cannot generate (for example the image model is unavailable on its
host), preview generation fails cleanly and recipe options keep
`preview: null` with a `Dish preview unavailable.` warning.

Start the API on the loopback interface:

```bash
uv run uvicorn main:app --host 127.0.0.1 --port 8000
```

Useful local URLs:

- Process health: `http://127.0.0.1:8000/api/v1/health`
- External-service readiness: `http://127.0.0.1:8000/api/v1/ready`
- Interactive OpenAPI docs: `http://127.0.0.1:8000/docs`
- OpenAPI JSON: `http://127.0.0.1:8000/openapi.json`

The default CORS allowlist accepts only the local browser origins in `.env`.
The host and port come from the `uvicorn` flags above; keep `--host 127.0.0.1`
unless you intentionally want a wider network exposure.

## Postman collection

Import
[`postman/Cook-Mantra.postman_collection.json`](postman/Cook-Mantra.postman_collection.json)
into Postman as a collection. Start the API, open request **03 Create Session**,
select an ingredient image, and then send the numbered requests in order.

The collection stores session, job, recipe-option, and preview-artifact IDs
automatically. Model operations run as background jobs, so repeat each numbered
**Poll** request until its response status is `succeeded` before continuing.
Collection variables let you change how many extracted ingredients are
confirmed. After request **10** or **14**, copy only the recipe option IDs you
want into the `selected_option_ids_json` collection variable. Request **15**
generates complete recipes only for that explicit selection and refuses to run
while the selection is empty.

## Verification and development

The default suite is deterministic and makes no Ollama or internet calls:

```bash
uv run pytest -W error
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

Live checks are skipped by default. They call the configured Ollama and Beast
services:

```bash
COOK_MANTRA_RUN_LIVE=1 uv run pytest -m live tests/live -q
```

This includes the full live API journey. It can take several minutes because
image generation and multiple structured model calls are involved. The Beast
image test needs the Beast host to report the image model as available.

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
- `MAX_CONCURRENT_JOBS`, `MAX_QUEUED_JOBS`, and
  `MAX_CONCURRENT_MODEL_CALLS` bound local work. A full queue returns
  `503 service_busy` instead of growing without limit. These and the other
  tuning values default from constants in `apps/api/src/core/config.py`, so
  `example.env` does not list them; set the environment variable only to
  override a default on a specific machine.
- Logs are structured JSON with request, session, and job correlation IDs.
  Secret-like fields, binary image data, and base64-looking values are
  redacted.
- LangSmith tracing is disabled unless explicitly enabled with credentials.
  When enabled, text-agent prompts and outputs leave the machine for LangSmith.
  Ingredient image bytes/base64 remain local: vision traces contain only safe
  summaries such as media type, byte count, and result counts.

## Troubleshooting

- **Ollama unavailable:** start Ollama, check `OLLAMA_BASE_URL`, and then open
  `/api/v1/ready`.
- **Beast unavailable:** when previews are enabled, check `BEAST_BASE_URL`,
  confirm Beast's public `/health` route is reachable, and verify
  `BEAST_API_KEY`. Readiness reports Beast's public `status` string verbatim;
  `degraded` remains ready, while an unreachable service fails readiness.
- **Missing model:** `/api/v1/ready` lists missing model names. Run the matching
  `ollama pull` command above exactly.
- **Operation timed out:** local inference exceeded `LLM_TIMEOUT_SECONDS` or
  `IMAGE_TIMEOUT_SECONDS`. Confirm the corresponding provider is responsive
  before increasing the relevant bounded timeout.
- **Dish preview unavailable:** inspect the terminal Beast job's `error`. A
  `model_unavailable` code (and a `degraded` `/health` status) means the image
  model cannot run on the Beast host — check `./beast status` there. Recipe
  generation still succeeds with `preview: null`.
- **Port already in use:** stop the conflicting process or pass a different
  `--port` to `uvicorn`.
- **`service_busy`:** wait for active jobs to finish or carefully adjust the
  bounded job settings for the machine's available memory.
- **Live test skipped:** set the exact opt-in flag shown above. LangSmith's
  smoke additionally needs tracing enabled and credentials; normal live
  Ollama and Beast checks use `COOK_MANTRA_RUN_LIVE=1`.
