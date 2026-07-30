# Cook Mantra Backend Design

**Date:** 2026-07-30
**Status:** Approved for implementation planning

## Objective

Build the complete Cook Mantra backend for local use. FastAPI exposes the
public HTTP API, LangGraph coordinates the AI workflows, and Ollama runs every
text, vision, and image model.

Implementation proceeds in vertical stages. Each stage adds a working part of
the user journey. After verifying a stage, work stops so the user can review
what changed and steer the next stage.

## Scope

The backend will:

- Accept an ingredient photo.
- Detect ingredients and report confidence.
- Suggest pantry ingredients separately.
- Let a caller edit and confirm the final ingredient list.
- Generate recipe options from confirmed ingredients.
- Add nutrition estimates, allergen warnings, and dish preview images.
- Generate more options without repeating previously shown recipes.
- Generate complete recipes for multiple selected options in parallel.
- Expose long-running operations as background jobs.
- Keep sessions, jobs, and artifacts temporarily on the local machine.
- Provide deterministic automated tests and optional live service checks.
- Support optional LangSmith tracing.

## Non-goals

This pass will not build:

- Frontend code.
- User accounts or authentication.
- A database or state that survives a backend restart.
- Cloud deployment, cloud object storage, or a remote model provider.
- Payments, analytics, sharing, or saved recipe collections.

## Runtime Constraints

- The API binds to `127.0.0.1` by default.
- Ollama is the only model runtime.
- Sessions and jobs live in memory and disappear on restart.
- Uploaded and generated files live under the repository's ignored `tmp/`
  directory.
- Temporary sessions and artifacts expire after six hours of inactivity by
  default.
- AI calls use configurable timeouts and concurrency limits. The default
  maximum is two simultaneous model calls.
- The image service uses Ollama's experimental image-generation API. The
  service boundary will isolate API changes from the rest of the application.

## Architecture

The backend has five layers.

### API routes

FastAPI routes validate HTTP input, call application services, and serialize
responses. Routes contain no prompts or workflow logic.

### Application state

An in-memory session store holds the user's current ingredients, recipe
options, exclusions, preferences, and complete recipes. An in-memory job store
holds job state, progress, results, and errors. Both stores provide atomic
updates so concurrent background tasks cannot overwrite each other.

### Orchestration

LangGraph coordinates the order and parallel execution of agents. Graph nodes
accept and return typed state. Graphs call services through injected
interfaces, which keeps tests independent of Ollama.

`langgraph.json` and the LangGraph CLI remain development tools for inspecting
graphs in LangGraph Studio. FastAPI remains the runtime HTTP server, and the CLI
belongs in the development dependency group.

### Agents and domain rules

Each AI agent performs one task and returns a Pydantic model. Domain services
enforce ingredient confirmation, source tracking, session transitions, recipe
exclusions, and selection rules without relying on model behavior.

### External services

Services wrap Ollama chat, Ollama image generation, temporary artifact storage,
logging, and optional LangSmith tracing. Service failures become application
errors rather than leaking provider exceptions through the API.

## Request Flow

A long-running operation follows this sequence:

1. A FastAPI route validates the request.
2. The route creates or updates a session.
3. The job manager creates a queued job and returns HTTP `202`.
4. An in-process background task marks the job as running.
5. A LangGraph workflow executes the required agents.
6. The workflow commits valid results to the session store.
7. The job becomes `succeeded` or `failed`.
8. The caller polls the job or session endpoint for progress and results.

A failed job leaves the session at its last valid state.

## Session and Job Models

### Session stages

A session moves through these stages:

1. `extracting`
2. `reviewing_ingredients`
3. `ingredients_confirmed`
4. `generating_options`
5. `options_ready`
6. `generating_recipes`
7. `recipes_ready`

Generating more options starts from `options_ready` and returns to
`options_ready`. A failed operation restores the stage that preceded the job.
Routes reject invalid transitions with HTTP `409`.

### Job states

A job has one of four states:

- `queued`
- `running`
- `succeeded`
- `failed`

Each job includes its ID, operation, session ID, timestamps, progress, result
reference, warnings, and safe error information.

## Domain Models

### Ingredient

Each ingredient includes:

- `id`
- `name`
- `source`: `detected`, `pantry_suggestion`, or `user_added`
- `confidence`: required for detected ingredients and absent otherwise
- `confirmed`

Renaming an ingredient preserves its source. A manually created ingredient
uses `user_added`. Only confirmed ingredients become available to recipe
agents. Confirmation requires at least one ingredient.

### Preferences

Recipe generation accepts optional:

- Dietary preferences
- Allergens to avoid
- Preferred cuisines
- Maximum total cooking time
- Servings
- Requested option count

The default option count is four, with a maximum of six.

### Recipe option

Each option includes:

- ID, name, summary, and cuisine
- Total time and difficulty
- Used, missing, and optional ingredients
- Suggested substitutions
- Nutrition estimate, diet tags, and allergen warnings
- Optional dish image artifact
- Warnings for failed enrichment steps

### Complete recipe

Each complete recipe includes:

- Option ID, name, cuisine, servings, and total time
- Ingredients with quantities and availability status
- Numbered instructions
- Tips and substitutions
- Nutrition and allergen notices
- Assumptions and warnings

## HTTP API

All routes use the `/api/v1` prefix.

| Method | Route | Purpose |
| --- | --- | --- |
| `GET` | `/health` | Confirm that the API process is running. |
| `GET` | `/ready` | Check Ollama connectivity and configured model availability. |
| `POST` | `/sessions` | Upload an image, create a session, and queue extraction. |
| `GET` | `/jobs/{job_id}` | Read job state, progress, result, warnings, or error. |
| `GET` | `/sessions/{session_id}` | Read the current session state. |
| `PUT` | `/sessions/{session_id}/ingredients` | Replace the editable ingredient review list. |
| `POST` | `/sessions/{session_id}/ingredients/confirm` | Confirm the available ingredients. |
| `POST` | `/sessions/{session_id}/recipe-options` | Queue the first recipe-option workflow. |
| `POST` | `/sessions/{session_id}/recipe-options/more` | Queue fresh options with prior names excluded. |
| `POST` | `/sessions/{session_id}/recipes` | Queue complete recipes for selected option IDs. |
| `GET` | `/artifacts/{artifact_id}` | Return a generated dish image. |

The session response exposes one ingredient list with source metadata. A caller
can group detected, pantry, and user-added items without maintaining separate
copies.

## Workflows

### Ingredient extraction

1. Validate the uploaded file as JPEG, PNG, or WebP with a maximum size of
   10 MiB.
2. Save it under a generated artifact name.
3. Ask the Ingredient Extraction Agent for ingredient names and confidence.
4. Normalize duplicate names.
5. Add unconfirmed pantry suggestions with their own source.
6. Move the session to `reviewing_ingredients`.

Weak recognition may return an empty detected list with a warning. The user can
still add ingredients manually.

### Recipe options

1. Verify that the session has confirmed ingredients.
2. Ask the Master Chef Agent for recipe options.
3. Reject names that match the session's exclusion set.
4. Enrich each option with Nutrition Agent and Image Service calls.
5. Store every shown recipe name in the exclusion set.
6. Return the session to `options_ready`.

Nutrition and image calls may run concurrently within the configured model-call
limit. Failed enrichment adds a warning but does not discard a valid option.

### More options

The workflow passes every previously shown recipe name to the Master Chef Agent
and validates the returned names again. If the model repeats a recipe, the
workflow retries with the expanded exclusion list. Exhausted retries fail the
job without deleting earlier options.

### Complete recipes

1. Validate every selected option ID.
2. Start one Specialized Recipe Agent task per option.
3. Limit concurrent model calls to protect local hardware.
4. Validate each structured recipe independently.
5. Collect successful recipes and per-option failures.
6. Move the session to `recipes_ready` when at least one recipe succeeds.

A single failed recipe does not discard successful recipes.

## Ollama Services

The chat service uses `ChatOllama` and structured Pydantic output for text and
vision agents. It applies a model-specific prompt, temperature, reasoning
setting, timeout, and retry policy.

The image service calls Ollama's `/api/generate` endpoint directly with the
configured image model, prompt, width, height, and step count. It decodes the
final base64 image, validates the result, and stores it as a temporary artifact.
Progress events update the owning background job when the installed Ollama
version provides them.

Readiness checks report unreachable Ollama servers and missing models without
attempting to pull models automatically.

## Background Jobs and Cleanup

FastAPI's application lifespan owns an in-process job runner. The runner:

- Tracks active `asyncio` tasks.
- Applies job and model-call concurrency limits.
- Records progress without blocking HTTP requests.
- Cancels active tasks during graceful shutdown.
- Removes expired sessions, jobs, uploads, and images.
- Clears files left in the runtime artifact namespace when the API starts.

The design does not use Celery, Redis, or another process. Restarting the API
clears all work.

## Error Handling

Every API error uses this shape:

```json
{
  "error": {
    "code": "ingredients_not_confirmed",
    "message": "Confirm the ingredient list before generating recipes.",
    "details": {},
    "retryable": false,
    "request_id": "request-id",
    "session_id": "session-id",
    "job_id": null
  }
}
```

Stable error codes cover validation, missing resources, invalid session
transitions, unavailable Ollama, missing models, invalid model output,
timeouts, artifact failures, and internal errors. Logs retain technical
details; API responses omit stack traces and secrets.

## Observability

Standard application logs always record request IDs, job IDs, session IDs,
durations, transitions, and errors.

LangSmith tracing is optional. The backend enables it only when
`LANGSMITH_TRACING=true` and a `LANGSMITH_API_KEY` is configured. Traces include
workflow steps, agent and model names, timing, errors, retries, job IDs, and
session IDs. The backend never attaches raw uploaded images or generated image
bytes. Text prompts and model outputs may leave the local machine when tracing
is enabled, and the setup documentation will state this clearly.

The backend runs normally without LangSmith credentials.

## Testing

### Unit tests

Unit tests cover:

- Ingredient source and confirmation rules
- Session transition rules
- Job state and progress updates
- Recipe exclusion and duplicate handling
- Concurrency limits and expiration
- Error mapping
- Artifact validation and cleanup

### API and workflow tests

FastAPI tests use injected fake workflows. LangGraph tests use fake model and
image services with complete, realistic responses. Tests never require a
running Ollama server, external network access, local environment files, or
LangSmith.

### Live smoke tests

Tests marked `live` are excluded from the default suite. They verify:

- Ollama readiness and installed model names
- Structured text output
- Vision input
- Image generation and decoding
- One complete backend journey

An additional optional smoke test verifies that a configured LangSmith project
receives a trace.

## Implementation Stages

### Stage 1: Backend foundation

Build the FastAPI app, settings, error format, health endpoints, in-memory
stores, background runner, temporary artifact store, and base tests. Fold the
current LangGraph prototype into the new structure, and keep the LangGraph CLI
as a development-only tool.

### Stage 2: Ingredient extraction

Build image upload validation, the structured extraction agent, pantry
suggestions, extraction workflow, and session retrieval.

### Stage 3: Ingredient confirmation

Build ingredient editing, source preservation, confirmation rules, and session
transition guards.

### Stage 4: Recipe suggestions

Build preferences, Master Chef output, nutrition enrichment, exclusions, and
the first and "More" option workflows.

### Stage 5: Dish preview images

Build Ollama image generation, artifact serving, progress reporting, and
partial-failure handling.

### Stage 6: Complete selected recipes

Build selected-option validation, concurrent Specialized Recipe Agents, result
collection, and partial-success responses.

### Stage 7: Hardening and final verification

Complete cleanup, timeouts, concurrency behavior, documentation, OpenAPI
examples, deterministic end-to-end tests, live Ollama smoke tests, and optional
LangSmith verification.

## Stage Handoff

After each stage, implementation stops before the next stage. The handoff
explains:

- What changed
- How it works in plain language
- Which endpoints are available
- Which commands passed
- Which decisions remain adjustable

The user reviews the stage and explicitly approves continuation. A stage
handoff does not modify frontend code or push commits.

## Completion Criteria

The backend is complete when:

- Every scoped endpoint follows the documented contract.
- Every user flow in the PRD works through the HTTP API.
- Only confirmed ingredients reach recipe agents.
- "More" excludes all recipes already shown in the session.
- Multiple selected recipes run concurrently within the configured limit.
- Nutrition and image failures preserve valid recipe options.
- Sessions and temporary artifacts expire as designed.
- Default tests, formatting, linting, and OpenAPI generation pass.
- Live smoke tests validate the configured Ollama installation.
- LangSmith remains optional and its data behavior is documented.
- No frontend, database, authentication, or cloud-provider code is introduced.
