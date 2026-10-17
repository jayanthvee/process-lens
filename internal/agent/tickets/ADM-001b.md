# ADM-001b: The admin panel, part 2 — Runs, Tasks, AI spend and Activity (read-only)

Owner: T2 (DeepSeek Flash) · Reviewer: T1 · Size: **medium, split into 3 parts** · Repo: `process-lens-admin`
(branch `adm-001b`, from `adm-001a`) · Parent ticket: `internal/agent/tickets/ADM-001.md`

Read ADM-001's requirements 2 (database rules), 7–11, and ADM-001a's code: it is the pattern to copy. **Build only
what this file lists.**

## Not in this ticket
- **The Pause and Cancel buttons on Runs.** The ProcessLens API has no admin-token route for them yet. Show the
  status only.
- The "Needs a look" and "Running now" panels on Overview.
- Role changes, deactivation, the minimum-version form, and `scripts/new_module.py`.

## Setup
- Branch `adm-001b` from `origin/adm-001a`.
- Each new module is `api/modules/<id>/` (`manifest.py` and `router.py`) plus `web/src/modules/<id>/`, exactly like
  `overview` and `people`.
- `requires` is `"core"` for every module here. Sidebar order:
  1. Overview
  2. Runs
  3. Tasks
  4. People
  5. AI spend
  6. Activity

## Part 1: `runs` and `tasks`
- **`runs` (`/runs`)** lists every run in the admin's company, newest first, 50 per page.
  - The columns:
    - task name;
    - the person who started it;
    - started (a relative time);
    - status, in plain words: queued → "Waiting", running → "Running", paused → "Paused", completed → "Done",
      cancelled → "Cancelled", failed → "Stopped";
    - "11 of 20 done", where done means verified records out of all records.
  - Two filters: person and status, as query parameters.
  - Clicking a row opens `${PROCESSLENS_WEB_URL}/runs/<id>` in a new tab. `PROCESSLENS_WEB_URL` goes in
    `.env.example` with the default `http://127.0.0.1:8000`.
- **`tasks` (`/tasks`)**, read-only. The columns:
  - task name;
  - who taught it (`workflows.created_by` → `users.name`);
  - the apps it uses (the hosts of `allowed_origins`, so `localhost:8069`, not the full URL);
  - runs in the last 30 days;
  - the share of rows done in those runs (verified ÷ all, shown as "84%", or "—" when there are none).
  - Archived workflows are left out.
- **Commit, push and update `PROGRESS.md`.**

## Part 2: `ai_spend` (`/ai`)
- **The total for this month.**
- **A per-day bar chart** for this month:
  - an inline SVG with `--gold` bars;
  - drawn to scale, with a y axis showing 0 and the maximum;
  - no chart library.
- **"Where it went"**, a table by `ai_calls.task`, with calls, spend and share. Plain names:
  - `normalize_fields` → **Cleaning spreadsheet values**;
  - `extract_document` → **Reading documents**;
  - `propose_repair` → **Fixing broken steps**;
  - anything else → **Other**.
- **The model name** appears only in a `title` attribute.
- **Commit, push and update `PROGRESS.md`.**

## Part 3: `activity` (`/activity`)
- **One list, newest first, 50 per page, merged from four sources:**
  - `admin_audit`;
  - `approvals`;
  - runs started (`runs.started_at`) and finished (`runs.finished_at`);
  - re-queues (`run_record_transitions` whose `to_state = 'pending'` and `from_state IN ('parked', 'failed')`).
- **Each line is a plain sentence plus a person and a time.** For example:
  - "Jay paused all runs";
  - "Priya approved a row in Add leads";
  - "Add leads finished: 18 of 20 done".
- **Filter by person.**
- **Export CSV:** the filtered lines, with the columns time, person and sentence, and no row contents. This is
  `GET /api/activity.csv`.
- **Commit, push and update `PROGRESS.md`.**

## Rules that hold for every route
- **Every query filters by `admin.tenant_id`.** Follow `../process-lens/internal/docs/sql-patterns.md`.
- **No row contents in any response.** Never return `run_records.data`, `approvals.shown`, `ai_calls.request`
  or `ai_calls.response`. Assert the response keys in tests.
- **Read-only.** None of these modules writes anything.

## Tests (write each part's tests first, and run them with `ADMIN_TEST_DATABASE_URL` set)
- **For each module:**
  - another tenant's rows never appear;
  - the response keys are exactly the ones the page needs;
  - an empty company gives an empty state, not an error.
- **`runs`:** the filters work, and "done of total" counts only verified records.
- **`tasks`:** archived workflows are hidden, and the share is "—" with no runs.
- **`ai_spend`:** the total equals the sum of the days, and an unknown task maps to "Other".
- **`activity`:**
  - the four sources merge newest first;
  - a page holds 50 lines;
  - the CSV has exactly the header `time,person,sentence`.
- **vitest:**
  - each page renders from a stub;
  - the forbidden-engine-words test covers the new pages;
  - the bar heights scale to the maximum.
- **A green run that skipped the database tests proves nothing.** The report must give the passed/skipped counts.

## Limits (non-negotiable)
- **No sub-agents.** Work one part at a time, and **commit and push after each part**.
- **Never run migrations, and never write to `process-lens`.**
- **Commands:** every command gets a **10-minute** timeout or less. **Stop after 2 identical failures** and
  report.
- **Time box: 2 hours in total.** If part 3 isn't done by then, stop at the last green part and report what is
  left.
- **On Windows:** never use `2>&1` or the PowerShell `&` call operator. Scratch files go only in `.cache/`.
- **Git:** conventional commits with no AI attribution. Never push to `main`; work on `adm-001b`.
- **When done:** write `process-lens-admin/REPORT-ADM-001b.md` with the total time, the cost and the cache %, from
  the status bar. Push, and stop.
