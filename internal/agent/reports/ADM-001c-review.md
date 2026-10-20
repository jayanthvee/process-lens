# ADM-001c review (T1)

**Verdict: approved.** Branch `adm-001c` in `process-lens-admin`, HEAD `11ede6b`.

## What I checked

- `pytest -q -rs api/tests` against the local database gave **101 passed, 0 skipped**.
- **Role and access changes:**
  - They look the user up with `id AND tenant_id`, so another tenant's user is a 404.
  - The guards come before the write, and each write and its `admin_audit` row share one transaction.
- **The extension floor goes only through the ProcessLens API.** Nothing in the repo runs `UPDATE tenants`.
- **The `api/registry.py` change** (registering loaded modules in `sys.modules`) is needed for Pydantic request
  bodies in modules loaded by path, and it is harmless.

## Notes (none blocking)

1. **The last-owner guard has a race.** It counts the active owners without locking them. Two owners demoting each
   other at the same moment could both pass and leave the company with no owner. The fix, before SaaS, is
   `SELECT ... FOR UPDATE` on the tenant's owner rows inside the transaction.
2. **The headline and "Running now" disagree.** The headline counts `queued` runs, but "Running now" shows only
   `running` and `paused`. So the demo reads "2 runs going" next to "Nothing is running". Make them use the same
   statuses.
3. **Migration 0008 hasn't been applied to the local dev database yet.** Run `python -m db.migrate` (or the demo
   reset) after PL-034 lands.
