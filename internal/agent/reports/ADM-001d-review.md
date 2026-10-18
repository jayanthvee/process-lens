# ADM-001d review (T1): approved, with two small fixes (ADM-001e)

**Reviewed** the diff `origin/adm-001c..origin/adm-001d` in `process-lens-admin`, and read the report.

- **The last-owner race.** It is closed correctly: the person is re-read and the owners are counted under `FOR UPDATE` inside the change's own transaction. The two-connection test proves the second request blocks and then answers 409, with no audit row.
- **The headline and "Running now".** Both count `running` and `paused`. Queued runs are listed separately as "waiting for Chrome".
- **`new_module.py`.** It validates the id and refuses an existing module or a foreign root. The test runs it against a `.cache/` copy and leaves no `demo` behind.
- **The acceptance tests.** Writes to `runs` are refused, and the manifest `writes` are checked against the real grants.

**Fixes (ADM-001e):**
1. **The deadlock risk.** `_lock_active_owners` takes `FOR UPDATE` with no `ORDER BY`. Two transactions can lock the same owner rows in different orders and deadlock. psycopg then raises `DeadlockDetected`, and the person sees "did not save". Add `ORDER BY id`.
2. **The grant test's tolerance is no longer needed.** The dev database is at `0008_admin_grant_tidy.sql`, applied 2026-10-18 00:46 UTC: `processlens_admin` has no `UPDATE` grant left on `tenants`. Make the check strict.

**Left as is.** The stale `Race firm b80f568f04` tenant in the dev database is pinned by an append-only audit row. It is harmless dev data; don't disable the trigger to remove it.
