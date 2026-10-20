# ADM-001d: The admin panel, part 4 — the review fixes, `new_module.py`, and ADM-001's last acceptance tests

Owner: T2 (DeepSeek Flash) · Reviewer: T1 · Size: **small, 2 parts** · Repo: `process-lens-admin` (branch
`adm-001d`, from `origin/adm-001c`) · Parent: `internal/agent/tickets/ADM-001.md`. Read
`internal/agent/reports/ADM-001c-review.md` first.

## Part 1: the two review fixes
1. **The last-owner race.**
   - In both `change_role` and the access route, lock the tenant's active owner rows **inside the same
     transaction**, before counting them: `SELECT id FROM users WHERE tenant_id = %s AND role = 'owner' AND
     deactivated_at IS NULL FOR UPDATE`.
   - A second request that arrives at the same moment then waits, recounts, and gets the 409.
   - **Test:** two connections, each in its own transaction, try to demote the other owner of a two-owner
     company. The first commits, and the second ends in a 409 (or blocks, then refuses). The company always keeps
     at least one active owner.
2. **The headline and "Running now" must agree.**
   - Both count the same statuses: `running` and `paused`.
   - A `queued` run is shown separately in "Running now" as "Waiting for Chrome", with no progress figure.
   - The headline says "2 runs going" only for running and paused runs. **Test:** a company with only queued runs
     reads "All quiet." plus "1 run waiting for Chrome".
- **Commit, push and update `PROGRESS.md`.**

## Part 2: `scripts/new_module.py` and ADM-001's acceptance tests
1. **`python scripts/new_module.py <id>`** scaffolds a module:
   - `api/modules/<id>/manifest.py` and `router.py`, with one `GET /api/<id>` returning `{"ok": true}` that is
     tenant-filtered through `current_admin`;
   - `web/src/modules/<id>/` with its manifest and a page.

   It refuses an id that exists or isn't `[a-z_]+`. **Test:** run it in a temp copy of the modules folders with
   id `demo`; the registry discovers and mounts `demo`, and the generated route answers 200. Then delete it.
   **Never leave `demo` in the repo.**
2. **"A write to `runs` is refused":** as `processlens_admin`, `UPDATE runs ...` and `INSERT INTO runs ...` each
   raise `InsufficientPrivilege`.
3. **"Every manifest's `writes` matches the role's actual grants."**
   - Read the column-level `UPDATE` / `INSERT` grants of `processlens_admin` from
     `information_schema.column_privileges` and `table_privileges`.
   - Assert that every `table.column` in any manifest's `writes` is granted.
   - Assert that every granted write is declared by some manifest.
   - If manifests have no `writes` field yet, add it: `people` writes `users.role` and `users.deactivated_at`;
     `overview` and `people` write `admin_audit`; the others write nothing.
- **Commit, push and update `PROGRESS.md`.**

## Limits (non-negotiable)
- **No sub-agents.** Commit and push after each part. **Never run migrations, and never write to `process-lens`.**
- **Commands:** every command gets a 10-minute timeout or less. Stop after 2 identical failures.
- **Time box: 60 minutes.**
- **On Windows:** never use `2>&1` or the PowerShell `&` call operator. Scratch files go only in `.cache/`.
- **Git:** conventional commits with no AI attribution. Never push to `main`.
- **When done:** write `REPORT-ADM-001d.md` with the passed/skipped counts, push, and stop.
