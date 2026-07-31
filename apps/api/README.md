# Cook Mantra API

Cook Mantra is a local FastAPI backend that turns an ingredient photo into
reviewable ingredients, recipe suggestions with nutrition and generated dish
previews, and complete recipes. Ollama is the only model provider.

## Local setup

Prerequisites:

- Python 3.13 or newer
- [`uv`](https://docs.astral.sh/uv/)
- [Ollama](https://ollama.com/) running locally

From `apps/api`, install the locked dependencies and create local configuration:

```bash
uv sync
cp ../../example.env .env
```

The five configured Ollama models are fixed in `src/core/config.py`. Pull all of
them before starting the API:

```bash
ollama pull qwen3.5:27b
ollama pull qwen3.5:9b
ollama pull gemma4:26b
ollama pull gpt-oss:20b
ollama pull x/z-image-turbo:fp8
```

Start the API on the loopback interface:

```bash
uv run uvicorn main:app --host 127.0.0.1 --port 8000
```

Useful local URLs:

- Process health: `http://127.0.0.1:8000/api/v1/health`
- Ollama/model readiness: `http://127.0.0.1:8000/api/v1/ready`
- Interactive OpenAPI docs: `http://127.0.0.1:8000/docs`
- OpenAPI JSON: `http://127.0.0.1:8000/openapi.json`

The default CORS allowlist accepts only the local browser origins in `.env`.
Keep `API_HOST=127.0.0.1` unless you intentionally want a wider network
exposure.

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

Live checks are skipped by default. They call the Ollama URL configured in the
process environment:

```bash
COOK_MANTRA_RUN_LIVE=1 uv run pytest -m live tests/live -q
```

This includes the full live API journey. It can take several minutes because
image generation and multiple structured model calls are involved.

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
  `503 service_busy` instead of growing without limit.
- Logs are structured JSON with request, session, and job correlation IDs.
  Secret-like fields, binary image data, and base64-looking values are
  redacted.
- LangSmith tracing is disabled unless explicitly enabled with credentials.
  When enabled, text-agent prompts and outputs leave the machine for LangSmith.
  Ingredient image bytes/base64 remain local: vision traces contain only safe
  summaries such as media type, byte count, and result counts.

## Troubleshooting

- **Ollama unavailable:** start Ollama and check `OLLAMA_BASE_URL`, then open
  `/api/v1/ready`.
- **Missing model:** `/api/v1/ready` lists missing model names. Run the matching
  `ollama pull` command above exactly.
- **Operation timed out:** local inference exceeded `LLM_TIMEOUT_SECONDS` or
  `IMAGE_TIMEOUT_SECONDS`. Confirm Ollama is responsive before increasing the
  relevant bounded timeout.
- **Image generation unsupported:** the installed Ollama build or image model
  does not support the expected generation response. Update Ollama, verify
  `x/z-image-turbo:fp8`, and run the focused image live test.
- **Port already in use:** stop the conflicting process or change `API_PORT` and
  the `--port` value together.
- **`service_busy`:** wait for active jobs to finish or carefully adjust the
  bounded job settings for the machine's available memory.
- **Live test skipped:** set the exact opt-in flag shown above. LangSmith's
  smoke additionally needs tracing enabled and credentials; normal live Ollama
  checks use `COOK_MANTRA_RUN_LIVE=1`.
