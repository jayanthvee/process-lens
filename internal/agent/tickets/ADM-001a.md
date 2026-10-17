# ADM-001a: The admin panel, part 1 — skeleton, sign-in, module registry, Overview and People (read-only)

Owner: T2 (DeepSeek Flash) · Reviewer: T1 · Size: **medium, split into 3 parts** · Repo: `process-lens-admin`
(private, empty) · Parent ticket: `internal/agent/tickets/ADM-001.md`

This is the first slice of ADM-001. Read ADM-001's "Who it is for", "Modular by design", "The look" and
"Requirements" 1–4, 5 and 6. **Build only what this file lists.** Runs, Tasks, AI spend, Activity, every write
action except the freeze switch, and `new_module.py` are later parts.

## Setup (the owner does this once, before T2 starts)
```
cd C:\Users\vasup
git clone https://github.com/jayanthvee/process-lens-admin.git
```
T2 works only in `C:\Users\vasup\process-lens-admin`. It reads `C:\Users\vasup\process-lens` (DESIGN.md,
ADMIN-MODULES.md, the migrations, `internal/docs/design/admin-mock.html`) but **never writes there**.

## Part 1: the skeleton (stop and report when green)
- **The layout,** exactly as ADM-001 requirement 1 sets it:
  - `web/`: Vite, React 18, React Router and TypeScript, with plain CSS and `@fontsource/public-sans`;
  - `api/`: FastAPI and psycopg 3;
  - `AGENTS.md`, `README.md`, `.env.example` and `.gitignore`.
- **Pin every dependency to an exact version.**
- **The gates:** `npm --prefix web run check` (typecheck, vitest, build), `python -m pytest -q`, `ruff check .` and
  `ruff format --check .`. All green on an empty app.
- **Commit and push** to branch `adm-001a`, and write `PROGRESS.md` ("part 1 done").

## Part 2: sign-in, registry, shell
- **`api/auth.py`, `current_admin()`:** the dev provider from ADM-001 requirement 3.
  - It reads `ADMIN_DEV_EMAIL`.
  - Refuse anyone who is not `owner`, or who is deactivated.
  - The API binds to `127.0.0.1` only.
- **`api/registry.py`:**
  - load each `api/modules/<id>/manifest.py`;
  - read `admin_modules` at start-up;
  - a module whose `requires` row is missing is not mounted;
  - `GET /api/modules` lists every module with `on: true/false`.
- **`web/src/registry.ts`, and the shell built from it:**
  - a 216 px dark-fog sidebar;
  - a header with the page title;
  - a module that is off shows "Needs a ProcessLens update".
- **The dark-fog tokens** come from `internal/docs/design/admin-mock.html`. Dark only.
- **Commit, push and update `PROGRESS.md`.**

## Part 3: the `overview` and `people` modules
- **`overview`:**
  - The headline: "All quiet." / "2 runs going. Nothing needs you."
  - Three figures: rows done this week, time saved this week (gold), and AI spend this month.
  - **Pause everything:** an outlined `--stop` button that confirms in the page, then calls the ProcessLens API's
    freeze route with `PROCESSLENS_ADMIN_TOKEN`. When runs are frozen, show the full-width banner with **Let runs
    start again**.
- **`people`:** a **read-only** table of name, email, role, Chrome extension version ("Out of date" / "Not seen
  yet") and last active. No role changes or deactivation yet.
- **Every query filters by the admin's `tenant_id`.** No row contents in any response.

## Tests (write each part's tests first)
API tests run against the Docker Postgres with `process-lens`'s migrations applied, connected as
`processlens_admin`. They prove:
- **auth:** a non-owner is refused, and so is a deactivated owner;
- **registry:** with `core` deleted (inside a test transaction), the app starts, `overview` and `people` report
  `on: false`, and the API answers 200;
- **tenant isolation:** another tenant's people never appear;
- **no row contents:** the response keys are asserted;
- **freeze:** the overview's freeze call reaches the stubbed API with the token, and one `admin_audit` row is
  written.
- **vitest:** the shell renders the registry's modules, an off module shows "Needs a ProcessLens update", and no
  engine words appear.

## Limits (non-negotiable)
- **No sub-agents.** One part at a time, and **commit and push after each part**, so a stop loses nothing.
- **Never run migrations, and never write to `process-lens`.**
- Every command gets a **10-minute** timeout or less. **Stop after 2 identical failures** and report.
- **Time box: 2 hours in total.** If part 3 isn't done by then, stop at the last green part and report what is left.
- On Windows, never use `2>&1` or the PowerShell `&` call operator. Scratch files go only in `.cache/`.
- Conventional commits, no AI attribution. Never push to `main`; work on `adm-001a`.
- **When done:** write the report as `process-lens-admin/REPORT-ADM-001a.md` (total time, cost and cache %, from
  the status bar), push, and stop. T1 copies it into `process-lens`.
