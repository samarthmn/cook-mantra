# Folder Structure

Cook Mantra uses a monorepo with two deployable applications: a Next.js web app and a backend API. Keeping both applications in one repository makes local development and API changes easier while preserving a clear deployment boundary.

```text
cook-mantra/
├── apps/
│   ├── web/                       # Next.js frontend
│   │   ├── src/
│   │   │   ├── app/               # Routes and layouts
│   │   │   ├── components/        # Reusable UI components
│   │   │   ├── features/          # Ingredient and recipe features
│   │   │   ├── lib/api/           # Backend API client
│   │   │   └── types/             # Frontend-only types
│   │   ├── public/                 # Static assets
│   │   └── tests/
│   │
│   └── api/                        # Backend service
│       ├── src/
│       │   ├── api/routes/         # HTTP endpoints
│       │   ├── orchestration/      # Multi-agent workflows
│       │   ├── agents/             # Individual AI agents
│       │   ├── domain/             # Ingredients and recipe rules
│       │   ├── services/           # AI, storage, and database adapters
│       │   ├── schemas/            # Request and response validation
│       │   ├── repositories/       # Database access
│       │   └── core/               # Configuration, logging, and errors
│       └── tests/
│           ├── unit/
│           └── integration/
│
├── packages/
│   └── api-client/                 # Generated API client and shared types
├── docs/                           # Product and architecture documents
├── scripts/                        # Development and deployment scripts
├── example.env                     # Safe environment-variable reference
└── README.md
```

## Backend Boundaries

- `api/routes/` accepts HTTP requests and delegates work.
- `orchestration/` coordinates complete workflows across agents.
- `agents/` contains the Ingredient Extraction, Master Chef, Nutrition, Image, and Specialized Recipe agents.
- `domain/` enforces business rules, including ingredient source and confirmation requirements.
- `services/` and `repositories/` isolate external AI providers, storage, and database code.

Start with `apps/web`, `apps/api`, and `docs`. Add `packages/api-client` when the backend contract exists; avoid creating other shared packages until repeated code justifies them.
