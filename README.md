# Cook Mantra

Cook Mantra turns a photo of the ingredients you already have at home into complete, step-by-step recipes.

## About the project

A lot of people stand in front of the kitchen wondering what to cook with the handful of vegetables and staples they have left. Cook Mantra answers that question: you take a photo of your ingredients, a vision model detects what is visible, and the app suggests dishes you can actually make — each with a short summary, a nutrition estimate, and an AI-generated preview image. Pick one or more dishes and a dedicated recipe agent writes out each one in full, with quantities, numbered steps, cooking time, tips, and substitutions.

The product has one rule it never breaks: **nothing is treated as available unless the user confirms it.** Detected ingredients, suggested pantry staples (salt, oil, onion, garlic, and so on), and manually added items are all shown for review, and only the confirmed list feeds recipe generation. Anything a recipe needs beyond that list is clearly marked as missing, optional, or replaceable.

It is built local-first: the agents run against a local Ollama instance, state lives in memory, and there is no database, no auth, and no mandatory cloud dependency. Dish-preview image generation and LangSmith tracing are optional external services.

### How it works

1. **Photo → ingredients.** The Ingredient Extraction Agent detects ingredients from the uploaded photo; common pantry staples are proposed separately.
2. **Human confirmation.** The user adds, edits, or removes items and explicitly confirms the final ingredient list.
3. **Recipe options.** The Master Chef Agent proposes dishes; each is enriched in parallel with a nutrition estimate and a preview image. Asking for more ideas never repeats dishes already shown.
4. **Complete recipes.** The user selects 1–6 dishes, and one Specialized Recipe Agent per selection runs in parallel to produce the full recipes.

## Tech stack

- **Web:** Next.js / React / TypeScript, tested with Vitest (`apps/web`)
- **API:** Python 3.13 / FastAPI / LangGraph, with agents served by Ollama (`apps/api`)
- **Tooling:** pnpm workspace for the frontend, uv for the backend

## Getting started

Prerequisites: Node.js with pnpm, Python 3.13 with [uv](https://docs.astral.sh/uv/), and a local [Ollama](https://ollama.com) instance for the agents.

```bash
pnpm install

# API on http://127.0.0.1:8000
pnpm api:dev

# Web app on http://localhost:3000
pnpm web:dev
```

Configuration lives in environment variables — see `example.env` for every supported variable and its documentation. `OLLAMA_BASE_URL` is required; the rest are optional or have sensible defaults.

## Tests and checks

```bash
# Web
pnpm web:test
pnpm web:typecheck
pnpm web:lint

# API
cd apps/api && uv run pytest -m "not live" && uv run ruff check .
```

## Documentation

- `docs/prd.md` — product requirements and the user/agent flow
- `docs/architecture-doc.md` — system architecture, session state machine, and agent workflows
- `docs/folder-structure.md` — repository layout
- `docs/ui-style-guide.md` — UI conventions
