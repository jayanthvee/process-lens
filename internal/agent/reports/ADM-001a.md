# REPORT — ADM-001a

Branch: `adm-001a` · Repo: `process-lens-admin` · Owner: T2

## What landed

Three commits, each green before the next:

1. `feat: add the admin panel skeleton`
2. `feat: add sign-in, the module registry and the shell`
3. `feat: add the overview and people modules`

### Part 1 — skeleton

- `web/`: Vite 5.4.11, React 18.3.1, React Router 6.28.0, TypeScript 5.7.2, plain CSS,
  `@fontsource/public-sans` 5.1.1.
- `api/`: Python 3.12, FastAPI 0.115.6, psycopg 3.2.3, pinned in `api/requirements.txt` (dev tools
  in `api/dev-requirements.txt`: pytest 8.3.4, ruff 0.8.4).
- `AGENTS.md`, `README.md`, `.env.example`, `.gitignore`; `.env` is ignored and was never committed.
- `scripts/README.md` marks the folder; `new_module.py` is not part of ADM-001a.

### Part 2 — sign-in, registry, shell

- `api/auth.py`: `current_admin()` is the one interface. The dev provider reads `ADMIN_DEV_EMAIL`,
  looks the address up in `users`, and refuses anyone who is not `owner` or who has
  `deactivated_at`. An unset/unknown address is a 403, never a guess.
- `api/__main__.py` runs uvicorn on `127.0.0.1`; the README's command does the same.
- `api/registry.py`: discovers every `api/modules/<id>/manifest.py`, reads `admin_modules` at
  start-up in a savepoint, and mounts a module's router only when on. A missing `admin_modules`
  table means nothing is on and the panel still answers.
- `GET /api/modules` lists every module with `on`, `requires` and its nav entries.
- `web/src/registry.ts` + `web/src/Shell.tsx`: 216 px dark-fog sidebar, page-title header, Settings
  ("Coming soon"), "Back to ProcessLens" (`VITE_PROCESSLENS_APP_URL`, default
  `http://127.0.0.1:5173`). An off module says "Needs a ProcessLens update" in the sidebar and on
  its page. Tokens are the mock's dark values in `web/src/styles/tokens.css`.

### Part 3 — overview and people

- `overview` (`/`):
  - headline "All quiet." or "N runs going. Nothing needs you." — the two variants ADM-001a lists;
    the "needs a look" variant belongs to the later runs/tasks modules;
  - three figures: rows done this week, time saved this week (gold, PL-023's exact formula over
    finished runs only: `max(0, round(done * 3 - seconds / 60))`), AI spend this month;
  - "Pause everything": outlined `--stop` button, in-page confirmation, then
    `POST /api/overview/freeze` → ProcessLens `POST /api/v1/admin/freeze` with
    `x-processlens-admin-token`, then one `admin_audit` row (`module = 'overview'`,
    `action = 'freeze_runs'`);
  - the frozen banner reads `tenants.runs_frozen_at` / `runs_frozen_by` and shows "All runs are
    paused. Paused by <first name> at HH:MM." with "Let runs start again" → `/unfreeze`.
- `people` (`/people`): read-only table of name, email, role, Chrome extension and last active.
  "Out of date" in `--help` below `tenants.min_extension_version`, "Not seen yet" with no report.
  No role select, no deactivation.
- Every `GET /api/overview` figure and every `GET /api/people` row is filtered by the signed-in
  admin's `tenant_id`. The response keys are asserted exactly:
  - overview: `headline.running`, `figures.{rows_done,minutes_saved,ai_spend_usd}`,
    `frozen.{frozen,at,by_name}`;
  - person: `name,email,role,extension_version,extension_state,last_active`.
  Nothing about a row's contents appears in either response.

## Data sources

- `overview` figures: `run_record_transitions` (done rows), `runs` (finished run seconds and the
  open-run count), `ai_calls.cost_usd` (this month), `tenants` (freeze state).
- `people`: `users`, `extension_reports`, `tenants.min_extension_version`, `runs.started_at` for
  last activity.
- Freeze/unfreeze: the ProcessLens API, never a direct ledger write. Audit: `admin_audit`.

## Verification

All four gates, last run after the final edit:

- `python -m pytest -q` — **25 passed** (health 1, auth 7, registry 3, people 5, overview 9).
- `ruff check .` — clean; `ruff format --check .` — 25 files formatted.
- `npm --prefix web run check` — typecheck clean, **16 vitest tests passed**, production build
  succeeded.
- Live probe against the real dev database (no browser): `/api/health`, `/api/modules`,
  `/api/overview` and `/api/people` all answered 200; both modules `on: true`; the demo tenant's
  two open runs produced `headline.running = 2`; the people list showed its one owner.

Ticket-specific tests:

- a builder, a deactivated owner, an unknown address and no address are all refused;
- with `core` deleted inside the transaction the app starts, both `core` modules are `on: false`
  and the API answers 200; with the `admin_modules` table renamed it still starts;
- another tenant's people and figures never appear;
- the freeze call reaches the stub at `/api/v1/admin/freeze` with the token and the tenant id, and
  writes exactly one `admin_audit` row; unfreeze reaches `/api/v1/admin/unfreeze`; a refused API
  call writes none;
- the shell renders the registry, shows the off notice, and no page carries an engine word.

## How the API tests seed data (please read before running them)

`processlens_admin` can read everything but may only insert `admin_audit` — it cannot create tenants,
users or runs. The tests therefore open **one transaction as the local owner** using
`ADMIN_TEST_DATABASE_URL`, seed it, then `SET ROLE processlens_admin` before the app answers a
single request. Every panel query in a test runs as the panel's real role, and the transaction is
rolled back at the end. No migration is ever run, and nothing is committed.

If `ADMIN_TEST_DATABASE_URL` is unset the API tests **skip**, not fail. That is deliberate (a CI
without a database should not report false failures) but it means a green `pytest` without the
variable proves nothing. The local `.env` (ignored) points it at the Docker Postgres owner.

## Notes and risks for T1

1. **Two audit rows per freeze in production.** The panel writes its own `overview` row as the
   ticket requires, and the ProcessLens API's `/admin/freeze` already writes a `core` row in the
   same database. The stub test sees exactly one (the panel's). If one line per action in the
   Activity module matters later, decide which layer owns the row.
2. **The API token is not held in the client object.** A test can override the HTTP client, so the
   endpoint sends the header itself from `PROCESSLENS_ADMIN_TOKEN`; that is what the freeze test
   asserts. An unset token closes the surface with a 503 before any call.
3. **Not built (later parts, by design):** Runs, Tasks, AI spend, Activity, the Overview "Needs a
   look" and "Running now" panels, role changes, deactivation, the minimum-extension form,
   `new_module.py`, and the company name/avatar in the header.
4. **Not verified:** no browser run and no call to a live ProcessLens API (the ticket forbids the
   browser; the freeze tests use a stub). The lock in `package-lock.json` is exact.
5. **Last active** is the later of the extension report and the newest run the person started; with
   neither it shows "No activity yet" (the extension cell keeps "Not seen yet").
6. `.env` is untracked; the report and `PROGRESS.md` are the only docs added beyond the README.

## Time, cost, cache

- **Total time:** about 15 minutes of measured runtime (`elapsed_seconds = 889`, 14 m 49 s).
- **Cost:** not available. This session exposes no status bar or cost meter, so no dollar figure is
  reported rather than an invented one.
- **Cache %:** not available for the same reason.
