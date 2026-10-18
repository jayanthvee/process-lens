# REPORT — ADM-001b

Branch: `adm-001b` (from `origin/adm-001a`) · Repo: `process-lens-admin` · Owner: T2

## What landed

Four commits, each green before the next:

1. `feat: add the runs and tasks modules`
2. `feat: add the ai spend module`
3. `feat: add the activity module`
4. `refactor: give the activity export only the parameters it uses`

### Part 1 — `runs` and `tasks`

- **`runs` (`/runs`)** — every run in the signed-in admin's company, newest first by `created_at`,
  50 per page (`page` query parameter, `has_more` from a 51st row). Columns: task name (from the
  pinned recipe's own name, with the workflow name as fallback, exactly as the main app does), the
  person who started it, a relative start time (`started_at`, falling back to `created_at` for a run
  that has not started), the status, and "N of M done" where N is `run_records.state = 'verified'`.
  Two filters, `person` and `status`, as query parameters. A row's link opens
  `${PROCESSLENS_WEB_URL}/runs/<id>` in a new tab; `PROCESSLENS_WEB_URL` is in `.env.example` with
  the default `http://127.0.0.1:8000` and is read by the API, not baked into the bundle.
  Statuses are drawn in plain words: Waiting, Running, Paused, Done, Cancelled, Stopped.
  **No Pause or Cancel buttons** — the ticket says status only.
- **`tasks` (`/tasks`)** — read-only: task name, who taught it (`workflows.created_by` →
  `users.name`), the apps it uses (the hosts of `allowed_origins`, so `localhost:8069` and not the
  full URL), runs in the last 30 days, and the share of rows done (`verified ÷ all` as a percentage,
  `null` — drawn as an em dash — when those runs have no rows). Archived workflows are left out.
  The name is the latest recipe version's own name, with the workflow name as fallback.
- Sidebar order 20 and 30; People moved to 40. Both modules register through the same
  `manifest.py` / `manifest.ts` + registry pattern as `overview` and `people`.

### Part 2 — `ai_spend` (`/ai`)

- This month's total, a per-day bar chart and "Where it went".
- The chart is one inline `<svg>` with `<rect>`s filled `var(--gold)`, drawn to scale against the
  month's biggest day, with a y axis labelling `$0` and the maximum. No chart library.
- The day series is built in SQL (`generate_series` from the first of the month to today), so a
  zero-spend day is still a day on the axis, and the total is computed as the sum of the days drawn
  — the figure and the chart cannot disagree.
- "Where it went" is a table by `ai_calls.task` with plain names: `normalize_fields` → Cleaning
  spreadsheet values, `extract_document` → Reading documents, `propose_repair` → Fixing broken
  steps, anything else → Other. Each row carries calls, spend and share.
- The model name is never visible on the page: it reaches the DOM only as the row's `title`
  attribute (a vitest test asserts the model string is absent from the rendered text).

### Part 3 — `activity` (`/activity`)

- One list, newest first, 50 per page, merged in one SQL union from four sources:
  - `admin_audit` (admin actions: freeze, unfreeze, role change, deactivation, pause, cancel, the
    extension floor);
  - `approvals` (`decided_by`, `decided_at`, the decision and the task);
  - runs started (`runs.started_at`) and runs finished (`runs.finished_at`, with "N of M done");
  - re-queues (`run_record_transitions` with `to_state = 'pending'` and
    `from_state IN ('parked', 'failed')`).
- Each line is a plain sentence, a person and a time: "Jay paused all runs", "Priya approved a row
  in Add leads", "Add leads finished: 18 of 20 done".
- A person filter applies across every source. A re-queue's person is the transition's `actor` when
  it names a user; otherwise the sentence says "Someone", because the ledger does not always record
  who re-queued a row.
- `GET /api/activity.csv` exports exactly the filtered lines, unpaged, with the header
  `time,person,sentence` and no row contents.

## Data sources

- `runs`: `runs`, `run_records`, `recipe_versions`, `workflows`, `users` — counts only.
- `tasks`: `workflows`, `recipe_versions`, `users`, `runs`, `run_records` — names and counts only.
- `ai_spend`: `ai_calls` — task name, model name, `cost_usd`, `created_at`; never `request` or
  `response`.
- `activity`: `admin_audit`, `approvals`, `runs`, `run_record_transitions`, `run_records`,
  `workflows`, `recipe_versions`, `users`; never `approvals.shown`, `run_records.data` or any AI
  request/response.

Every route filters by `admin.tenant_id`, uses only read statements, and writes nothing.

## Verification

Last full run, after the final refactor:

- `python -m pytest -q -rs` — **61 passed, 0 skipped, 0 failed** (1 warning: a Starlette
  `BlockingPortal` deprecation from the test client, not our code). The suite ran against the Docker
  Postgres with `process-lens`'s migrations applied; `ADMIN_TEST_DATABASE_URL` was set, so **no
  database test skipped** — a green run here is a real green run. Per file: health 1, auth 7,
  registry 3, people 5, overview 9, runs 8, tasks 10, ai_spend 8, activity 10.
- `ruff check .` — clean. `ruff format --check .` — 42 files already formatted.
- `npm --prefix web run check` — typecheck clean, **41 vitest tests passed** (8 files), production
  build succeeded, exit code 0.
- Live probe against the real dev database through the app's own client (no browser):
  - `/api/modules` lists all six in sidebar order (overview, runs, tasks, people, ai_spend,
    activity);
  - `/api/runs` returned only the signed-in tenant's two runs (the other tenant's four stayed
    hidden) and correct `done/total` counts;
  - `/api/tasks` returned the tenant's two live tasks with hosts, run counts and shares;
  - `/api/ai` returned `0.00` for a month with no calls and 17 day entries (the empty state the page
    draws);
  - `/api/activity` returned 0 lines and `/api/activity.csv` the header alone — correct, because
    that tenant's runs are `queued` and have no `started_at`/`finished_at`, so nothing happened yet.

Ticket-specific tests, all present:

- every module: another tenant's rows never appear; the response keys are asserted exactly; an empty
  company is an empty state, not an error;
- `runs`: both filters, "done of total" counting verified only, newest first, 50 to a page;
- `tasks`: archived hidden, the share is `null` with no runs, the 30-day window, host parsing;
- `ai_spend`: the total equals the sum of the days, an unknown task folds into "Other", the three
  plain names, shares add to 100, models listed for the hover title;
- `activity`: the four sources merge newest first, a page holds 50, the CSV header is exactly
  `time,person,sentence`, the CSV honours the filter, no engine words in a sentence;
- vitest: every new page renders from a stub, the forbidden-engine-words test covers runs, tasks,
  ai spend and activity, and the bar heights scale to the maximum.

## Notes and risks for T1

1. **The ticket is not on `main`.** `internal/agent/tickets/ADM-001b.md` exists only on
   `origin/tickets-0017`; `git pull` on `main` found nothing. I read it there with
   `git show origin/tickets-0017:...` and wrote nothing into `process-lens`.
2. **Pause and Cancel are not on the Runs page**, by the ticket's own "Not in this ticket": the
   ProcessLens API has no admin-token route for them yet.
3. **Two runs in the dev database are `queued` with no `started_at`**, so they appear in `/runs`
   (started falls back to `created_at`) but not in `/activity` (nothing has happened). That is the
   intended reading of "runs started", but it is worth confirming with T1.
4. **A re-queue's person can be unknown.** The ledger records `actor` as text and does not always
   set it; when it does not, the line says "Someone" and `person` is `null`. The CSV leaves that cell
   empty.
5. **The AI chart covers this month to date**, not the whole calendar month: drawing 31 bars with
   three weeks of trailing zeros would misread as "spend collapsed".
6. **Not verified in a browser and not run against a live ProcessLens API.** The ticket allows no
   browser run; no route here calls the ProcessLens API.
7. **`runs` and `tasks` use `coalesce(recipe.meta.name, workflow.name)`** exactly as the main app's
   task list does, so a run is named as it was pinned even after the task is renamed.

## Time, cost, cache

- **Total time:** about 12 minutes of measured run time (the host's elapsed meter read 9 min at the
  end of part 3, plus the report and the final gates). No part came close to the 2-hour box.
- **Cost:** not available. This session exposes no status bar or cost meter, so no dollar figure is
  reported rather than an invented one.
- **Cache %:** not available for the same reason.
