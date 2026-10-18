# ADM-001e: The admin panel, part 5 — two review fixes, then the series is done

Owner: T2 (DeepSeek Flash) · Reviewer: T1 · Size: **tiny, 1 part** · Repo: `process-lens-admin`, branch `adm-001e` from `origin/adm-001d`. Read `internal/agent/reports/ADM-001d-review.md` first (in `process-lens`, branch `adm-001c-ticket`).

## The fixes
1. **A fixed lock order.**
   - In `api/modules/people/router.py`, `_lock_active_owners` becomes `SELECT id FROM users WHERE tenant_id = %s AND role = 'owner' AND deactivated_at IS NULL ORDER BY id FOR UPDATE`.
   - Add one sentence to its docstring saying why: the same order in every transaction, so two changes can't deadlock.
   - **Test:** two connections each take the lock for a three-owner company in opposite request orders, and neither raises `DeadlockDetected`. A simpler assertion is acceptable: the executed SQL contains `ORDER BY id`, plus the existing race test stays green.
2. **The strict grant check.**
   - In `api/tests/test_acceptance.py`, remove the `min_extension_version` tolerance and the rolled-back `REVOKE` re-check.
   - "Every granted write is declared" must now hold with **no** exception. The dev database is at migration 0008.
   - If it fails, **stop and report the extra grant.** Don't add a tolerance back.

## When done
- `python -m pytest -q -rs`: everything passed, nothing skipped.
- `ruff check .` and `ruff format --check .`.
- `npm --prefix web run check`.
- Update `PROGRESS.md`, write `REPORT-ADM-001e.md` with the counts, commit, push, and stop.

## Limits (non-negotiable)
- **No sub-agents.** Never run migrations, and never write to `process-lens`.
- **Commands:** every command gets a 10-minute timeout or less. Stop after 2 identical failures.
- **Time box:** 25 minutes.
- **On Windows:** never use `2>&1` or the PowerShell `&` call operator. Scratch files go only in `.cache/`.
- **Git:** conventional commits with no AI attribution. Never push to `main`.
