# ADM-001e review (T1): approved. The ADM-001 series is done.

- **`_lock_active_owners`** locks `ORDER BY id`, so two changes take the owner locks in one order and can't deadlock. The SQL-capture test failed before the fix, and the two-thread test checks for no `DeadlockDetected`.
- **The grant check is strict:** `granted - declared == set()`, with no tolerance. The dev database is at 0008.
- **The report's counts:** pytest 113 passed, none skipped; vitest 60; ruff clean.
