# Cook Mantra Backend: What Was Built

This guide explains what the backend does, how the pieces fit together, and how
to run the complete workflow without reading the source code.

## The result

Cook Mantra now provides a local HTTP API that turns an ingredient photo into:

1. An editable list of detected and suggested ingredients.
2. Recipe options with nutrition estimates and optional dish previews.
3. Complete recipes for only the options the user selects.

FastAPI exposes the API, LangGraph coordinates the AI workflows, Ollama runs
the text agents, and the Beast API generates dish previews. Long operations run
as background jobs so clients can poll for progress instead of holding one
request open.

## What each stage added

### Stage 1: Backend foundation

- A FastAPI application with versioned `/api/v1` routes.
- Environment-based configuration with startup validation.
- `OLLAMA_BASE_URL` as a required setting. The application reads it from the
  process environment or the repository-root `.env` file.
- Health and Ollama readiness endpoints.
- Structured logs, CORS restrictions, safe error responses, concurrency limits,
  and graceful startup and shutdown.
- In-memory session and job stores.

### Stage 2: Ingredient extraction

- JPEG, PNG, and WebP uploads with file-size and content validation.
- Temporary, private artifact storage for uploaded images.
- Vision-based ingredient detection with confidence values.
- Separate pantry suggestions that remain unconfirmed until the user reviews
  them.
- A background extraction job with progress and safe failure reporting.

### Stage 3: Ingredient review

- An editable review list before recipe generation.
- Support for renaming, adding, removing, and confirming ingredients.
- Source tracking for detected, suggested, and user-added ingredients.
- Session-stage rules that prevent later operations from running too early.

### Stage 4: Recipe options and nutrition

- Recipe suggestions based only on confirmed ingredients and user preferences.
- Nutrition estimates, diet tags, allergen warnings, substitutions, and
  missing-ingredient information.
- A "more options" workflow that excludes recipes already shown.
- Independent enrichment failures: a nutrition or preview failure adds a
  warning without discarding an otherwise valid option.
- GPT-OSS nutrition calls use the supported `low` reasoning level, which fixes
  the empty responses produced by disabling reasoning.

### Stage 5: Dish previews

- A dedicated Beast API image-generation service.
- Generated images stored as temporary artifacts and returned through an
  artifact endpoint.
- Graceful fallback to `preview: null` when Beast cannot return a valid image.

Cook Mantra submits asynchronous jobs to Beast, polls their progress, and
downloads the first completed output. It verifies the declared byte length,
SHA-256 digest, and decoded image before storing the artifact. Beast's
authenticated `GET /v1/jobs/{job_id}/outputs/{index}` route is pending
server-side implementation, so current live generation fails safely and recipe
options report `Dish preview unavailable.` without discarding the recipe.
When previews are enabled, readiness also calls Beast's public `/health` route.
It reports the returned status verbatim, accepts `degraded` as ready, and fails
readiness only when Beast is unreachable or returns invalid health data.

### Stage 6: Complete recipes

- Explicit option selection: the request must contain one to six option IDs.
- The backend generates only those selected options. It never expands the
  request to every available option.
- Selected recipes can generate in parallel within the configured model limit.
- The model returns only recipe content. The server owns and merges trusted
  fields such as the option ID, recipe name, cuisine, servings, nutrition
  notice, and allergen notice.
- Larger output and context budgets for detailed Gemma recipes.
- Validation for exact selected-option ingredients, consecutive instruction
  numbering, unsupported fields, and ingredient availability.
- Ingredient availability is derived from the confirmed session ingredients
  instead of trusting the model.
- Partial success is preserved: one failed selected recipe does not erase
  another successful recipe.

### Stage 7: Hardening and verification

- Deterministic unit, integration, workflow, and end-to-end tests.
- Optional live Ollama, Beast, and LangSmith checks.
- Queue limits, timeouts, cleanup, request correlation IDs, redaction, and
  rollback to the last valid session state after a failed job.
- OpenAPI generation and three valid LangGraph development graphs.
- A Postman collection for the complete workflow.

## Model assignments

| Task | Provider and model |
| --- | --- |
| Ingredient extraction | `qwen3.5:9b` |
| Recipe options | `qwen3.5:27b` |
| Nutrition estimates | `gpt-oss:20b` |
| Dish previews | Beast API `z-image-turbo` |
| Complete recipes | `gemma4:26b` |

The Ollama assignments and Beast model default live in
`apps/api/src/core/config.py`, not in `.env`.

## Run the API

From `apps/api`:

```bash
uv sync
cp ../../example.env ../../.env
```

Set `OLLAMA_BASE_URL` in the repository-root `.env` to the Ollama server that
the API should use. When previews are enabled, also set `BEAST_BASE_URL` and
`BEAST_API_KEY`. Then start the API:

```bash
uv run uvicorn main:app --host 127.0.0.1 --port 8000
```

Useful checks:

- API health: `http://127.0.0.1:8000/api/v1/health`
- Ollama, model, and Beast readiness: `http://127.0.0.1:8000/api/v1/ready`
- Interactive API documentation: `http://127.0.0.1:8000/docs`

Restart Uvicorn after changing Python code or configuration. Sessions and jobs
are kept in memory, so an API restart removes them.

## Run the workflow in Postman

Import:

`apps/api/postman/Cook-Mantra.postman_collection.json`

Then:

1. Send **01 Health** and **02 Ollama Readiness**.
2. Open **03 Create Session - Select Image First**, choose an ingredient image,
   and send the request.
3. Repeat **04 Poll Ingredient Extraction Job** until `status` is `succeeded`.
4. Use **05 Get Extracted Ingredients**, **06 Update Ingredient Review**, and
   **07 Confirm Ingredients** to review and confirm the ingredient list.
5. Send **08 Generate Recipe Options** and repeat **09 Poll Recipe Options Job**
   until it succeeds.
6. Send **10 Get Options and Capture IDs**. The Postman console prints the
   available option IDs and names.
7. Set the collection variable `selected_option_ids_json` to a JSON array
   containing only the IDs the user chose. For example:

   ```json
   ["first-selected-option-id", "second-selected-option-id"]
   ```

8. Send **15 Generate Complete Recipes**. Its pre-request check refuses to send
   when the selection is empty, invalid, or contains an unavailable option.
9. Repeat **16 Poll Complete Recipes Job** until it succeeds.
10. Send **17 Get Final Session and Recipes** to read the completed recipes.

Requests **12–14** optionally generate and list more choices. They update the
available IDs but do not overwrite the user's explicit selection.

## Why polling previously took so long

Polling was not the slow operation. The job waited while local models produced
invalid or incomplete structured output and the retry layer tried again.

The fixes address the specific causes:

- GPT-OSS now receives a supported reasoning level.
- Gemma receives enough context and output space for a complete recipe.
- The recipe schema excludes server-owned fields, reducing unnecessary model
  output.
- Step numbering and structured-output errors retry inside the shared retry
  boundary.
- Successful output no longer pays for avoidable invalid attempts.

Text inference time still depends on the Ollama host, model size, and current
model queue. Preview time also depends on Beast's job queue.

## Verification performed

- The deterministic suite passes: **651 passed, 6 skipped**.
- Formatting, linting, dependency-lock validation, OpenAPI generation,
  LangGraph validation, Postman JSON validation, and whitespace checks pass.
- A live nutrition probe succeeds with GPT-OSS.
- A live complete-recipe probe for the previously failing session succeeds for
  the selected option, producing 17 ingredients and 7 numbered steps.
- Beast preview failures remain non-fatal and produce `preview: null` plus a
  warning.
- Claude CLI reviewed the complete change set. Its actionable findings were
  fixed, followed by a final independent code and test review.

## Operational boundaries

- Data is temporary and process-local; there is no database.
- Restarting the API removes sessions and jobs.
- Uploaded and generated images expire and are cleaned from the repository's
  ignored `tmp/cook-mantra-api` area.
- The backend has no user accounts, frontend, cloud deployment, or persistent
  recipe library.
- Live model tests are opt-in because they require the configured Ollama and
  Beast services and can take several minutes.
