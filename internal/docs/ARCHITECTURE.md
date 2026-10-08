# Architecture

Component contract for the repository map in [AGENTS.md](../AGENTS.md#repository-map). The design rationale lives in
[SYSTEM_DESIGN.md](SYSTEM_DESIGN.md#architecture-overview) and is not repeated here. The schema is
[0001_init.sql](../../db/migrations/0001_init.sql). PL-005 copies the YAML block below verbatim into the public
`tools/guards/boundaries.yaml` and turns it into CI checks.

## Tiers and runtime paths

| Tier | Components |
| --- | --- |
| Browser | `apps/web` (product UI), `apps/extension` (recorder and executor), `apps/demo-*` (test targets only) |
| Backend | `services/api` (HTTP API and WebSocket hub), `services/runner` (run worker), `services/ai`, `services/documents` |
| Data | Postgres (state, ledger, job queue), object storage (MinIO: files, screenshots, grounded document structure) |

Runtime paths. Nothing else talks to anything else.

- `apps/web` ↔ `services/api`: HTTP and WebSocket. The UI holds no workflow state.
- `apps/extension` ↔ `services/api` hub: one WebSocket per browser session. The hub relays messages between the
  runner and the extension. It never interprets, reorders, buffers, or replays step commands.
- `services/runner` → hub → extension: one step command at a time. The extension performs the step and returns
  an observation, and the runner checks that observation against the recipe.
- `services/api`, `services/runner`, `services/documents` → `services/ai`: in-process calls to its public module only.
- `services/ai` → model providers: the only outbound path to any model.
- All backend services → Postgres and object storage. The extension and web app never touch either directly.

**Executor** means the component that performs browser actions: `apps/extension` in the attended runner and a
future cloud executor in the unattended runner. The **runner** orchestrates. Constitution rule 4 binds both.

## What each component owns

"Writes" lists the tables a component is the only writer of. A table written by two components names the split.

| Component | Owns | Writes |
| --- | --- | --- |
| `apps/web` | Editor, input review, preview approval, results, and repair approval screens | nothing (through the API only) |
| `apps/extension` | Recorder: fingerprints, password masking, event stream. Executor: one step per command, the locator uniqueness rule, origin and destructive-control checks, observations | nothing (it owns no workflow state; rule 10) |
| `apps/demo-*` | Synthetic demo sites with drift switches. Test targets only | their own demo databases |
| `services/api` | Auth, tenants, workflows, the compiler, creating recipe versions, uploads, input review decisions, approvals, the WebSocket hub, enqueuing runs | `tenants`, `users`, `workflows`, `recipe_versions`, `recordings`, `input_batches`, `input_records`, `value_mappings`, `files`; `runs` on insert; decisions on `field_proposals` and `heal_proposals` |
| `services/runner` | Run orchestration, claims, canary, approval gates, recovery and reconcile, data steps (`transform`, `match`, `aggregate`) in plain code | `runs` after the claim (status, heartbeat), `run_records`, `committed_keys`, `step_events`; `heal_proposals` on insert |
| `services/ai` | API keys, router, redaction, the six typed call types, output schema validation, response cache | `ai_calls` |
| `services/documents` | Ingest, hashing, text layer or OCR, grounded structure, code validators, the document job consumer | `documents`; `field_proposals` on insert; `jobs` it claims |
| `packages/recipe` | Recipe JSON Schema (protected), generated Python and TypeScript types, the validator. Pure, no I/O | none |
| `packages/ledger` | The ledger state table mirrored from the migration, plus the only implementations of the queries in [sql-patterns.md](sql-patterns.md) | none (callers execute its queries) |
| `packages/shared` | Utilities with no product logic: ids, time, logging, error types | none |
| `db/` | Migrations (append-only) and SQL tests | schema |
| `fixtures/`, `tests/`, `tools/guards/` | Synthetic data and golden sets; cross-cutting and break-it tests; CI guards | none |

Two rules follow from the table.

1. **Ledger SQL lives in one place.** Statements that read or write `runs`, `run_records`, `committed_keys`, or
   `jobs` live only in `packages/ledger`, so AGENTS.md rule 7 ("match a pattern in sql-patterns.md") can be checked
   by tooling. Migrations and tests are exempt.
2. **AI has one door.** `services/ai` exposes a public module with exactly the six typed call types in
   [AI_BOUNDARIES.md](AI_BOUNDARIES.md). It exposes no generic prompt or completion function. Its other modules are private.

## Allowed dependencies

A component may import itself and the components it lists. Anything not listed is forbidden. Paths are
repository-relative. PL-005 maps them to Python packages and TypeScript paths once PL-002 fixes the package names.

```yaml
version: 1

components:
  apps/web:            { lang: ts,     may_import: [packages/recipe, packages/shared] }
  apps/extension:      { lang: ts,     may_import: [packages/recipe, packages/shared] }
  apps/demo-*:         { lang: [ts, py], may_import: [] }
  services/api:        { lang: py,     may_import: [services/ai, packages/recipe, packages/ledger, packages/shared] }
  services/runner:     { lang: py,     may_import: [services/ai, packages/recipe, packages/ledger, packages/shared] }
  services/documents:  { lang: py,     may_import: [services/ai, packages/recipe, packages/ledger, packages/shared] }
  services/ai:         { lang: py,     may_import: [packages/recipe, packages/shared] }
  packages/recipe:     { lang: [ts, py], may_import: [packages/shared] }
  packages/ledger:     { lang: py,     may_import: [packages/shared] }
  packages/shared:     { lang: [ts, py], may_import: [] }

# Importers of services/ai may use only this module; every other module in services/ai is private.
public_modules:
  services/ai: [services/ai/calls]

# Third-party packages restricted to specific components.
restricted_packages:
  model_sdks:
    allowed_in: [services/ai]
    python: [anthropic, openai, google-genai, google-generativeai, google-cloud-aiplatform, deepseek,
             litellm, langchain, llama-index, ollama, transformers]
    npm: ["@anthropic-ai/sdk", openai, "@google/genai", "@google/generative-ai", ai, "@langchain/core", ollama]
  browser_automation:
    allowed_in: [apps/extension, apps/demo-*, tests]
    python: [playwright, pyppeteer, selenium]
    npm: [puppeteer, puppeteer-core, playwright, "@playwright/test", selenium-webdriver]
  database_drivers:
    allowed_in: [services/api, services/runner, services/documents, services/ai, packages/ledger, db, tests]
    python: [psycopg, psycopg2, psycopg-binary, asyncpg, sqlalchemy]
    npm: [pg, postgres]

# SQL that touches these tables may appear only in the listed paths.
sql_owners:
  tables: [runs, run_records, committed_keys, jobs]
  allowed_in: [packages/ledger, db, tests]

# Test and tooling code may import any component. Product code may never import these.
exempt: [tests, tools, fixtures, db/tests]
never_imported: [apps/demo-*, tests, tools, fixtures]
```

Notes for PL-005:

- `openai` is restricted because several providers, DeepSeek among them, are called through the OpenAI client.
- Nothing in `apps/` may import `services/` or another app. The browser reaches the backend over the network only.
- `services/runner` never imports a browser-automation library. It sends commands and never drives a browser
  itself. `apps/extension` is the only product component allowed one: `puppeteer-core` over `ExtensionTransport` (D-006).

## Open items (owner or a later ticket)

- **Runner ↔ hub transport** (Postgres LISTEN/NOTIFY or internal HTTP). It must carry command ids and must not
  replay commands. See P-001.
- **Cloud executor (phase 2)** becomes a new component with the same command protocol. Adding it is an
  architecture change under AGENTS.md rule 4.
- **Python package names** are fixed by PL-002. The YAML stays path-based.
