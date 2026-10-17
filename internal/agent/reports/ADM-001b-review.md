# ADM-001b review (T1)

**Verdict: approved.** Branch `adm-001b` in `process-lens-admin`, HEAD `2ab2969`.

## What I checked

- `pytest -q -rs api/tests` against the local database gave **61 passed, 0 skipped**.
- **Tenant filters.** Every branch of the activity union filters by `tenant_id`: `admin_audit`, `approvals`, runs
  started, runs finished, and re-queues. So do the person list, runs, tasks and ai_spend.
- **No row contents.** Nothing selects `run_records.data`, `approvals.shown` or `ai_calls.request` /
  `ai_calls.response`.
- **Pause and Cancel are left out,** as the ticket said.

## Notes (none blocking)

- **A re-queue line says "Someone"** when the ledger's `actor` text names no user. The CSV leaves that cell empty.
  This is fine until the runner records the person behind a re-queue.
