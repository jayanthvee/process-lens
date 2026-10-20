# ADM-001c: The admin panel, part 3 — People actions, the extension floor, and Overview's live panels

Owner: T2 (DeepSeek Flash) · Reviewer: T1 · Size: **medium, 3 parts** · Repo: `process-lens-admin` (branch
`adm-001c`, from `origin/adm-001b`) · Parent: `internal/agent/tickets/ADM-001.md` requirements 2, 5 and 6

Copy ADM-001a's and ADM-001b's patterns. **Build only what this file lists.**

## Part 1: People actions (direct SQL, the only writes the role allows)
- **Change a role.** A select with one plain line per role, worded as in ADM-001 requirement 6:
  - Owner: everything, including this panel;
  - Builder: teaches and runs tasks;
  - Reviewer: fixes rows that need help, and approves;
  - Viewer: can look, can't change anything.

  The route is `POST /api/people/{user_id}/role` with `{role}`.
- **Turn off access / Turn access back on**, with an in-page confirmation. The route is
  `POST /api/people/{user_id}/access` with `{on: bool}`, and it sets or clears `users.deactivated_at`.
- **The guards (409, with a plain sentence):**
  - an owner can't demote themselves or turn their own access off;
  - nobody can demote or turn off **the last active owner** of the company;
  - a user from another tenant is a **404**, never a 403. It must not reveal that the user exists.
- **Each change writes one `admin_audit` row** (`module = 'people'`, action `change_role` or `turn_off_access` /
  `turn_on_access`, `target` = the user id, and `detail` = the old and new values). The change and its audit row
  go **in one transaction**, through the request connection that ADM-001a's `get_connection` already commits.
- **Show "People join by signing in with your company account"** where an invite button would be.
- **Commit, push and update `PROGRESS.md`.**

## Part 2: The minimum extension version (through the ProcessLens API)
- **A small form above the People table:** "Minimum extension version", a text field, and **Save**.
  - Before saving, it shows "This would block N people", counting people whose last reported version is below
    the new floor.
  - The count comes from `GET /api/people/floor-preview?version=1.2.0`. It reuses `api/versions.py`.
- **Save** calls the ProcessLens API's `POST /api/v1/admin/min-extension-version` with
  `{"version": ..., "tenant_id": ...}` and the token header, exactly as the freeze call does.
  - An empty field sends `null`, which clears the floor.
  - A 422 from the API shows "That isn't a version number, like 1.4.0".
  - Write the panel's own `admin_audit` row, as freeze does.
- **Never write `tenants.min_extension_version` directly.** The role can't anyway (PL-034, migration 0008).
- **Commit, push and update `PROGRESS.md`.**

## Part 3: Overview's "Needs a look" and "Running now"
- **"Needs a look":** at most 5 lines, each with one action link. All are read-only queries filtered by tenant:
  - a run that is `running`, with `heartbeat_at` older than 2 minutes → "Stopped responding", linking to that run
    on Runs;
  - a run with 10 or more records `parked` → "N rows need help", linking to `${PROCESSLENS_WEB_URL}/needs-help`;
  - a task whose last 3 finished runs each had more `parked` + `failed` than `verified` → "Keeps failing",
    linking to Tasks;
  - people below the floor → "N people need to update the Chrome extension", linking to People;
  - a deactivated person with an open run → "Turned-off person still has a run going", linking to Runs.
- **"Running now":** the person, the task, and "N of M done" for each `running` or `paused` run. The page polls
  every 5 seconds. **No Pause button**: there's no admin route for it yet.
- **The headline's third form:** "2 runs going. 1 thing needs a look."
- **Commit, push and update `PROGRESS.md`.**

## Tests (write each part's tests first, with `ADMIN_TEST_DATABASE_URL` set; report the passed/skipped counts)
- **The role change:**
  - writes exactly one audit row, in the same transaction;
  - when the audit insert is forced to fail, the role is unchanged.
- **Guards:** self-demote → 409, last owner → 409, another tenant's user → 404.
- **The floor:** the preview's count is right; Save reaches the stubbed API with the token and the tenant; a 422
  shows the plain sentence; and no SQL write to `tenants` happens.
- **Needs a look:** each of the five rules fires on its own fixture and stays silent without it; another tenant's
  rows never appear.
- **Running now:** its keys are asserted (no row contents).
- **vitest:**
  - the confirmation appears before access is turned off;
  - the role select's lines;
  - the floor preview line;
  - Needs a look's links;
  - the forbidden-engine-words test covers every page.

## Limits (non-negotiable)
- **No sub-agents.** Commit and push after each part. **Never run migrations, and never write to `process-lens`.**
- **Commands:** every command gets a 10-minute timeout or less. Stop after 2 identical failures.
- **Time box: 2 hours.**
- **On Windows:** never use `2>&1` or the PowerShell `&` call operator. Scratch files go only in `.cache/`.
- **Git:** conventional commits with no AI attribution. Never push to `main`.
- **When done:** write `REPORT-ADM-001c.md`, push, and stop.
