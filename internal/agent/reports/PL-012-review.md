# Review PL-012

Status: changes requested · Reviewer: T1 · Reviewed commit: c0a28d9 (branch `pl-012`)

## Verdict

Not ready to merge. The structure is right, and most of G1–G10 is genuinely closed: the insert/delete guards,
attempts/max_attempts, `parked → skipped`, TRUNCATE, the approvals table, the idempotency key, and one active run per
destination. But break-it tests found **11 ways around the new guards**. The two most serious reopen G1, the
blind-resubmit path this ticket exists to close, and one of them happens in normal operation with no rule-breaking
code. The fixes are small.

## Gates (run on c0a28d9)

```
$ python -m db.migrate        (run twice)
nothing to apply: the database is up to date
nothing to apply: the database is up to date
(fresh-database apply of 0001 + 0002 is covered by test_migration_runner, which passes)

$ python -m pytest -q
349 passed, 1 warning          (tests/db/test_ledger_hardening.py: 32 passed)

$ ruff check .                 All checks passed!
$ ruff format --check .        73 files already formatted
```

## The two forced test changes: both strengthen the harness

- **`test_migration_runner.py`.** Adding a migration necessarily changes the "which files shipped" assertion. It still
  pins the exact list and both hashes. No assertion is weakened.
- **`seed.py`.** Rows are now born `pending` and walked to their state through the real guard, and attempts are
  reached by real claims. Previously a test could create states the database would reject in production; now it
  cannot. This is stronger. The one trade-off is acceptable: `verified` uses the read-only path, so seeded verified
  rows are never commit-attempted. Tests that need a commit-attempted row walk it explicitly.

## Break-it results

The test file is `tests/db/test_ledger_breakit.py`. It is uncommitted, and each test asserts the safe behaviour.
Result: **11 failed**.

### Must fix before merge

| # | Hole | Proof | Fix |
| --- | --- | --- | --- |
| H1 | `commit_attempted` can be cleared by a standalone `UPDATE`, because the guard fires only on `state, attempts, max_attempts`. Then `failed → pending` passes, reopening G1 | `test_commit_attempted_cannot_be_cleared_by_a_standalone_update`, `test_clearing_commit_attempted_does_not_open_a_blind_requeue` | Add `commit_attempted` to the trigger's column list. Reject any change except `false → true` on entering `submitting` |
| H2 | **A destination check carries over to the next commit, in normal operation.** Commit fails → a person checks → `pending` → the record is clicked again and fails again → `failed → pending` now passes with no new check, because `destination_checked_by` is still set from last time | `test_a_destination_check_does_not_carry_over_to_the_next_commit` | On entering `submitting`, the guard sets `destination_checked_by` and `destination_checked_at` to NULL |
| H3 | The destination check can be set in an earlier, separate `UPDATE` and then used by a plain re-queue. The ticket says "the same UPDATE" | `test_a_destination_check_must_be_recorded_in_the_requeue_itself` | Add both columns to the trigger. They may change only in a `failed|parked → pending` transition, both non-null, `at = now()`; otherwise raise. Require `NEW.destination_checked_at IS DISTINCT FROM OLD.destination_checked_at` for the re-queue |
| H4 | `run_record_transitions` rows can be updated and deleted (confirmed: 1 row updated, 1 deleted). The ticket calls it append-only, and `sql-patterns.md` claims "triggers will reject it anyway" | `test_transition_audit_rows_cannot_be_edited`, `..._deleted` | `CREATE TRIGGER ... BEFORE UPDATE OR DELETE ON run_record_transitions FOR EACH ROW EXECUTE FUNCTION forbid_mutation();` |
| H5 | **The recovery pattern strands records.** It moves commit-window records to `reconciling` but leaves `claimed_by` as the dead worker. The new state-change pattern requires `claimed_by = $worker`, so the new worker can never move them | `test_recovery_leaves_reconciling_records_movable_by_the_new_worker` | `sql-patterns.md` recovery: `SET state = 'reconciling', claimed_by = $worker, claimed_at = now()`, only after a successful takeover |
| H6 | **The run-claim pattern crashes on a busy destination.** It picks a queued run whose destination already has an active run, and the update raises `UniqueViolation` on `runs_one_active_per_destination` instead of skipping. That would crash the runner's loop | `test_claiming_a_queued_run_skips_a_busy_destination` | Claim pattern: add `AND NOT EXISTS (SELECT 1 FROM runs a WHERE a.tenant_id = runs.tenant_id AND a.destination_key = runs.destination_key AND a.status IN ('running','paused'))`, and treat a `23505` from a race as "nothing to claim" |

### Should fix (in this ticket if cheap)

| # | Hole | Proof | Fix |
| --- | --- | --- | --- |
| H7 | **Lease race.** While a takeover is in flight (the epoch is bumped but not yet committed), the old worker's patterned write still lands, because the `EXISTS` subquery reads the old committed epoch without locking | `test_a_stale_lease_write_does_not_succeed_while_a_takeover_is_in_flight` (the stale write returned 1 row) | Make the lease check `... lease_epoch = $epoch FOR SHARE`, so the stale write waits for the takeover and then fails the epoch check. This closes the database half only: PL-013 must also put `lease_epoch` on every executor command and have the extension refuse old epochs, or a stale worker can still click |
| H8 | An empty `idempotency_key` (`''`) is accepted | `test_an_empty_idempotency_key_is_rejected` | Insert guard: reject `''` for `idempotency_key` and `record_key` |
| H9 | `runs.destination_key` can be changed on an active run, which moves it out from under the one-active-run index | `test_an_active_run_cannot_move_to_another_destination` | Reject any change to `destination_key` after insert |

Minor, no test: the audit row's `reason` is `NEW.reason`, which can be the previous transition's reason when the
update doesn't set one. Document it, or have the patterns always set `reason`.

## Instructions for T2

1. **Amend `0002_ledger_hardening.sql` in place.** It is not merged, so no shared database depends on it. Recreate
   your local database afterwards, because the migration runner rightly refuses a changed hash.
2. **Update `sql-patterns.md`** for H5, H6 and H7, and update `LEDGER_SPEC.md` if the rules text changes.
3. **Commit `tests/db/test_ledger_breakit.py` with the fix.** Every test in it must pass without changing any
   assertion. The only allowed edits are to setup, and only if a fix changes a legitimate path.
4. **Re-run** the gates and `python -m db.migrate` twice, update the report, and push. T1 re-reviews before merge.

## What is correct and stays

The 31-transition guard matches `LEDGER_SPEC.md` (the matrix test reads the spec). The insert guard (`pending`,
`attempts = 0`, not commit-attempted, key required) and the delete ban are correct. So are the attempts and
max_attempts lock, the TRUNCATE ban on all seven tables, the approvals table (append-only, `decided_by` required),
the idempotency-key skip join, the `committed_keys` zero-row incident rule, and the takeover pattern. The report is
accurate, and its two flagged test changes were the right call.
