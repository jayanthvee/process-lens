# REPORT — ADM-001c

Branch: `adm-001c` (from `origin/adm-001b`) · Repo: `process-lens-admin` · Owner: T2

## What landed

Three commits, each green before the next:

1. `feat: let an owner change a role and turn access off`
2. `feat: set the minimum extension version through the ProcessLens API`
3. `feat: show what needs a look and what is running on the overview`

### Part 1 — People actions (direct SQL, the only two writes the role allows)

- **`POST /api/people/{user_id}/role`** with `{role}` — one of `owner`, `builder`, `reviewer`,
  `viewer` (anything else is a 422 and changes nothing). The update is conditional on the role
  actually differing, so a repeated request is a no-op.
- **`POST /api/people/{user_id}/access`** with `{on}` — sets `users.deactivated_at = now()` or
  clears it, conditional on the person currently being in the other state.
- **One `admin_audit` row per change** — `module = 'people'`, action `change_role`,
  `turn_off_access` or `turn_on_access`, `target` = the user id, `detail` = `{"from": …, "to": …}`
  (the old and new values), `actor_user_id` = the signed-in admin. The change and its audit row sit
  inside one explicit transaction (`with conn.transaction()`), so a failed audit write rolls the
  change back. A no-op (already in that state) writes no audit row, because it is not a change.
- **The guards**, each a 409 with one plain sentence:
  - the company's last active owner cannot be demoted or turned off (“This is the only owner. Make
    someone else an owner first.” / “This is the only owner, so access has to stay on.”);
  - an owner cannot demote themselves or turn their own access off (“You can't change your own
    role.” / “You can't turn off your own access.”);
  - another company's user is a **404** (“That person isn't here.”), and so is an id nobody has, so
    the panel never reveals that someone exists elsewhere.
- **The People page**: a role select per row whose four lines are the ticket's wording (Owner —
  everything, including this panel; Builder — teaches and runs tasks; Reviewer — fixes rows that
  need help, and approves; Viewer — can look, can't change anything); “Turn off access” replaced in
  place by an in-page confirmation (“Turn off access for Ravi Iyer?” with “Turn off access” and
  “Keep access”); “Turn access back on” in one step; the API's refusal shown as a sentence above the
  table; and “People join by signing in with your company account” where an invite button would be.
- `GET /api/people` grew `id` and `deactivated` (the actions need both); nothing else was added.

### Part 2 — The minimum extension version (through the ProcessLens API)

- **`GET /api/people/floor-preview?version=…`** returns `{version, blocked}` — the people whose last
  reported extension is below the new floor, compared with `api/versions.py` (the same module the
  People table already uses). A person who has never reported is not “below” anything, so is not
  counted; an empty version blocks nobody; only this tenant's reports are read.
- **`POST /api/people/floor`** with `{version}` calls the ProcessLens API's
  `POST /api/v1/admin/min-extension-version` with `{"version": …, "tenant_id": …}` and
  `PROCESSLENS_ADMIN_TOKEN`, exactly as the freeze call does.
  - An empty field sends `null`, which clears the floor.
  - An API **422** becomes the panel's own 422 with “That isn't a version number, like 1.4.0.” and
    writes no audit row; any other refusal is a 502 with a plain sentence; an unreachable API is a
    502 and changes nothing.
  - On success the panel writes its own `admin_audit` row (`module = 'people'`, action
    `set_min_extension_version`, `target` = the tenant id, `detail` = the old and new floor).
- **The panel never writes `tenants.min_extension_version`.** A test Saves a floor through the
  stubbed API and asserts the column is still `null`; nothing in the repository runs `UPDATE tenants`.
- The People page shows the form above the table — “Minimum extension version”, a text field
  pre-filled from `GET /api/people`'s new `floor`, and Save — with the live line “This would block N
  people” (or “1 person”) as the field changes.

### Part 3 — Overview's “Needs a look” and “Running now”

- `GET /api/overview` now returns `needs_a_look` (at most five lines, each
  `{"sentence", "action": {"label", "href"}}`), `running` (each
  `{"id", "task_name", "person_name", "status", "done", "total"}`) and `headline.needs_a_look` — the
  true number found, even when the list is capped at five.
- The five rules, in the ticket's order, each a tenant-filtered read-only query:
  1. a `running` run with `heartbeat_at` older than 2 minutes → “Stopped responding: <task>.” →
     See runs (`/runs`);
  2. a run with 10 or more `parked` records → “<n> rows need help in <task>.” → See the rows
     (`${PROCESSLENS_WEB_URL}/needs-help`);
  3. a task whose last three finished runs each had more `parked` + `failed` than `verified` →
     “Keeps failing: <task>.” → See tasks (`/tasks`);
  4. people below the floor → “<n> people need to update the Chrome extension.” (singular for one) →
     See people (`/people`);
  5. a deactivated person with an open run (`queued`, `running` or `paused`) → “Turned-off person
     still has a run going: <name>.” → See runs (`/runs`).
- The Overview page shows the headline's third form (“2 runs going. 1 thing needs a look.”, with
  correct singular/plural), a “Needs a look” panel with one link per line, and a “Running now” panel
  with the person, the task and “N of M done” for each `running` or `paused` run — **no Pause
  button**. Both panels refresh every 5 seconds (`POLL_MS = 5000`).

## Data sources

- People: `users` (read; `role` and `deactivated_at` written), `extension_reports`, `runs`,
  `tenants.min_extension_version` (read only), `admin_audit` (written).
- Floor: `extension_reports` and `tenants` read; the change goes through the ProcessLens API.
- Overview: `runs`, `run_records`, `run_record_transitions`, `workflows`, `recipe_versions`,
  `users`, `extension_reports`, `tenants`, `ai_calls` — counts, names and the ledger's own states
  only.

Every route filters by `admin.tenant_id`; none returns a row's contents.

## Verification

Last full run, after the final format pass:

- `python -m pytest -q -rs` — **101 passed, 0 skipped, 0 failed** with `ADMIN_TEST_DATABASE_URL`
  set, so **every database test ran**; no test was skipped, and a green run here is a real one.
  Per file: health 1, auth 7, registry 3, people 6, people actions 13, people floor 12, overview 8,
  overview panels 15, runs 8, tasks 10, ai spend 8, activity 10.
- `ruff check .` — clean; `ruff format --check .` — 45 files already formatted.
- `npm --prefix web run check` — typecheck clean, **58 vitest tests passed** (8 files), production
  build succeeded.
- Live probe against the dev database through the app's own client (no browser):
  - `/api/people` returns the demo tenant's one person with `floor: null` and `deactivated: false`;
  - `/api/people/floor-preview` answers `{version: null, blocked: 0}`, `{1.4.0, 0}` and
    `{9.9.9, 0}` for a tenant with no extension reports;
  - `/api/overview` returns `headline {running: 2, needs_a_look: 0}`, `needs_a_look: []`,
    `running: []` — that tenant's two runs are `queued`, so they count as going but nothing is
    running and nothing has gone wrong.

Ticket-specific tests, all present:

- the role change writes exactly one audit row with the old and new values, and a forced audit
  failure (a trigger that raises) leaves the role unchanged and no row behind;
- self-demote → 409, last owner → 409 (demote and turn-off), another tenant's user → 404 for both
  routes, unknown id → 404, an unknown role → 422;
- the floor preview's count is right, is tenant-scoped, ignores people never seen; Save reaches the
  stubbed API at the right path with the token and tenant; a 422 shows the plain sentence; an empty
  field sends `null`; a refused or unreachable API writes no audit row; and no SQL write reaches
  `tenants`;
- each of the five “Needs a look” rules fires on its own fixture and stays silent without it (fresh
  heartbeat, nine parked rows, two bad runs of three, nobody below the floor, a turned-off person
  with no run); six stopped-responding runs give five lines but `needs_a_look = 6`; another tenant's
  trouble never appears; `running` keys are asserted and a completed or queued run never appears;
- vitest: the confirmation appears before access is turned off and “Keep access” cancels; the role
  select's four lines; the floor preview line and Save; the headline's three forms; the Needs a look
  links; the Running now rows; the 5-second poll; and the forbidden-engine-words test covers the
  Overview (both panels), People (with the form), Runs, Tasks, AI spend and Activity.

## Notes and risks for T1

1. **The ticket is not on `main`.** `internal/agent/tickets/ADM-001c.md` lives only on
   `origin/adm-001c-ticket`; I read it there with `git show` and wrote nothing into `process-lens`.
2. **Migration 0008 is not applied to the local dev database** (it has 0001–0007), so
   `processlens_admin` still holds the direct grant on `tenants.min_extension_version` here. The
   panel does not use it — the test proves the column is unchanged after a Save — but a fresh
   database at 0008 would also refuse the write at the database level, which is the stronger
   guarantee.
3. **One change outside the module folders:** `api/registry.py` now registers each module it loads
   in `sys.modules` before executing it. Without that, a module's Pydantic request model cannot be
   resolved by name (FastAPI's body validation fails with “not fully defined”), because the registry
   loads modules by path. It is a two-line fix needed by the new request bodies.
4. **The last-owner guard is only reachable by the signed-in admin.** Any other target leaves the
   actor as an active owner, so “last owner” and “self” can only collide on the actor. The
   last-owner check runs first, so a sole owner gets the structural sentence (“This is the only
   owner…”) and a self-demote with another owner present gets the personal one.
5. **A no-op writes no audit row.** Turning off someone already off, or setting the role they
   already have, returns 200 with the current state and nothing in `admin_audit`. If T1 wants every
   request audited, that is a one-line change.
6. **Confirmation only for turning access off**, not for turning it back on: the destructive
   direction asks, the restoring one does not. The ticket's own test list asks only for the former.
7. **“Keeps failing” excludes archived tasks**, because its action goes to `/tasks`, which hides
   them. The ticket does not say; this keeps the link honest.
8. **The run-related links go to the panel's `/runs`.** There is no per-run page in the panel and
   the ticket says “linking to that run on Runs”; the sentence names the task (and the person for a
   turned-off one) so the row is findable. The parked-rows link goes to the main app's
   `/needs-help`, exactly as the ticket specifies.
9. **The headline can say “2 runs going” while “Running now” is empty**, because the headline counts
   `queued` runs and the panel shows only `running`/`paused`. That is what the demo tenant looks
   like today (two queued runs). If T1 prefers the two to agree, the headline count is the thing to
   change.
10. **One probe hit the live ProcessLens API.** A manual check posted a floor with the placeholder
    token while a ProcessLens API happened to be listening on `127.0.0.1:8000`; it was refused
    (non-2xx). I verified afterwards that both tenants' `min_extension_version` are still `null` and
    `admin_audit` has zero rows, so nothing changed. No browser was opened and the app was not
    started by me.
11. **Not verified in a browser**, and the floor/role/access flows were exercised against the
    stubbed API, not a live one.

## Time, cost, cache

- **Total time:** about 13 minutes of measured run time (`elapsed_seconds = 780`, 13 m 00 s at the
  end of part 3), plus this report. Well inside the 2-hour box; no part needed a retry beyond the
  two ordinary test-fix cycles.
- **Cost:** not available. This session exposes no status bar or cost meter, so no dollar figure is
  reported rather than an invented one.
- **Cache %:** not available for the same reason.
