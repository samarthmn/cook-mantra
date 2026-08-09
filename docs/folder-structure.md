# Folder Structure

Cook Mantra uses a monorepo with two locally run applications: a Next.js web app and a FastAPI backend. Keeping both in one repository makes source installation and coordinated API changes straightforward.

```text
cook-mantra/
├── apps/
│   ├── web/                       # Next.js frontend
│   │   ├── src/
│   │   │   ├── app/               # Routes and layouts
│   │   │   ├── components/        # Reusable UI components
│   │   │   ├── features/          # Cook-session and design-guide features
│   │   │   ├── lib/api/           # Backend API client
│   │   │   └── types/             # Frontend-only types
│   │   ├── public/                 # Static assets
│   │   └── tests/
│   │       └── setup.ts            # Shared Vitest setup
│   │
│   └── api/                        # Backend service
│       ├── src/
│       │   ├── api/routes/         # HTTP endpoints
│       │   ├── orchestration/      # Multi-agent workflows
│       │   ├── agents/             # Individual AI agents
│       │   ├── domain/             # Ingredients and recipe rules
│       │   ├── services/           # Providers, tracing, jobs, and artifacts
│       │   ├── schemas/            # Request and response validation
│       │   ├── repositories/       # In-memory session and job stores
│       │   └── core/               # Configuration, logging, and errors
│       └── tests/
│           ├── unit/
│           ├── integration/
│           ├── e2e/
│           └── live/               # Explicitly opt-in provider smoke checks
│
├── config/                          # Provider/model/agent-role YAML
├── docs/                           # Product and architecture documents
├── design_handoff_cook_mantra_ui/  # Superseded prototype reference
├── tmp/                            # Ignored task/runtime scratch data
├── example.env                     # Safe environment-variable reference
├── LICENSE                         # MIT license
├── package.json                    # Root development and verification commands
└── README.md
```

## Backend Boundaries

- `api/routes/` accepts HTTP requests and delegates work.
- `orchestration/` coordinates complete workflows across agents.
- `agents/` contains the Ingredient Extraction, Master Chef, and Recipe Writer agents.
- `config/cook-mantra.yaml` selects providers and models for every runtime role; secrets remain in environment variables.
- `domain/` enforces business rules, including ingredient source and confirmation requirements.
- `services/` isolates external model providers, tracing, job execution, and temporary artifact storage.
- `repositories/` owns the in-memory session and job stores; there is no database in the local product.

Start with `apps/web/src/features`, `apps/api/src`, `config`, and `docs`. Web behavior tests stay beside their source, while `apps/web/tests/setup.ts` owns shared Vitest setup. Backend tests live under `apps/api/tests/unit`, `apps/api/tests/integration`, `apps/api/tests/e2e`, and the explicitly opt-in `apps/api/tests/live` suite. Add a shared package only when repeated production code justifies one.
