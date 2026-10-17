# PL-034 review (T1)

**Verdict: approved, with one T1 fix.**

## What I checked

- **The tests:** `tests/demo`, `tests/db/test_admin_migration.py` and `tests/db/test_migration_runner.py` gave
  44 passed. `ruff check` and `ruff format --check` are clean.
- **Migration 0008** revokes only `UPDATE (min_extension_version)` from `processlens_admin`. The API connects as
  the owner, so its own write still works. The test proves the admin role gets `InsufficientPrivilege` and that the
  value is unchanged.
- **`status_lines` is a pure function,** and it speaks only when something changes. The waiting line names Chrome,
  which is what a person can fix.

## The T1 fix

- **The runner's log path is back.** The old line `started a worker (log: ...)` was replaced, and the path went
  with it. It's what we read when a demo run misbehaves, so the window now prints `(its log: ...)` under "Started a
  runner".

## Notes (none blocking)

- **A missed "finished" line.** If a runner exits and a new one starts in the same 2-second tick, `last_exit` is
  reset before the state is built, so that "The runner finished." line is never printed. This is cosmetic.
