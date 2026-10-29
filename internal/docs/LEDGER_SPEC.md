# Ledger specification

The ledger is `run_records`: one row per input record per run. The authority is the trigger function
`guard_record_transition()`, defined in [0001_init.sql](../../db/migrations/0001_init.sql) and replaced by
[0002_ledger_hardening.sql](../../db/migrations/0002_ledger_hardening.sql). If this page and the migrations
ever disagree, the migrations win and this page has a bug. Every query on the ledger has one approved shape, listed in
[sql-patterns.md](sql-patterns.md). The design rationale is in [SYSTEM_DESIGN.md](SYSTEM_DESIGN.md#the-run-ledger).

## States

| State | Meaning | Kind |
| --- | --- | --- |
| `pending` | Waiting to be claimed | start |
| `claimed` | A worker owns the record and is running the precheck (input rules, destination search) | working |
| `prechecked` | Precheck finished; the result decides skip, park, or fill | working |
| `filling` | Running non-commit steps (navigate, fill, select, read) | working |
| `submitting` | Write-ahead marker: the commit is about to be dispatched. It is durable **before** the click is sent | commit window |
| `submitted_unverified` | The executor acknowledged the commit; `assert_after` has not passed yet | commit window |
| `reconciling` | Commit outcome unknown; the destination must be checked before anything else happens | commit window |
| `verified` | Committed and confirmed, or a read-only path completed | exit, final |
| `skipped` | Deliberately not processed: already in the destination, or already committed by an earlier run | exit, final |
| `parked` | Needs a person: an ambiguous match, a missing input, an unresolvable locator | exit, re-queueable |
| `failed` | Stopped with evidence: attempts exhausted, or a provable rejection | exit, re-queueable |

## Transitions (exactly as in `guard_record_transition()`)

There are 32 legal transitions between different states (28 in 0001; 0002 added `parked → skipped`,
`failed → reconciling`, and `parked → reconciling`; 0003 added `filling → skipped`). An update that
keeps the same state is always accepted. Any other pair raises `illegal ledger transition`.
`pending → claimed` increments `attempts`, and `CHECK (attempts <= max_attempts)` stops a fourth claim. A release back to `pending` because the run paused (a pause, a lost connection, a hidden window, an admin freeze) gives that claim's attempt back: the runner marks the update with the transaction-local `processlens.refund_attempt`, and the guard decrements `attempts` (migration 0009). Crash recovery does not mark its releases, so they still count.

Two further rules ride on those transitions, for a record that may already have saved (its
`commit_attempted` is true, because it has entered `submitting`):

- It may not be dismissed as **skipped**. `filling → skipped` is refused outright, and
  `prechecked → skipped` and `parked → skipped` are refused unless the same `UPDATE` records a fresh
  destination check (`destination_checked_by` and `destination_checked_at`, both non-null and fresh). A
  person who has confirmed the destination does not have the record may dismiss it.
- It may not be re-queued to **pending** (`failed → pending` or `parked → pending`) unless the same
  `UPDATE` sets that fresh destination check. Its retry path is normally
  `failed|parked → reconciling → pending`.

The two destination-check columns are tightly held. `commit_attempted` only ever changes `false → true`,
and only by the guard when a record enters `submitting`. Entering `submitting` also clears
`destination_checked_by` and `destination_checked_at`, so a check of one commit never carries over to the
next. The columns may otherwise change only in a `failed|parked → pending` re-queue or a
`parked|prechecked → skipped` dismissal that sets both non-null with a fresh `destination_checked_at`
(within the last 5 minutes and not in the future). Any other change raises.

| From | To | Used when |
| --- | --- | --- |
| `pending` | `claimed` | The claim query picks the record |
| `pending` | `skipped` | Its idempotency key is already in `committed_keys` (pre-batch check; creates only since PL-128, an update is decided by the site) |
| `pending` | `failed` | Close-out: `attempts >= max_attempts` |
| `claimed` | `prechecked` | Precheck finished |
| `claimed` | `pending` | Released: worker shutdown, or recovery of an abandoned claim |
| `claimed` | `parked` | Precheck needs a person (input rule, locator for the search not found) |
| `claimed` | `failed` | Unrecoverable precheck error |
| `prechecked` | `filling` | Nothing blocks the record |
| `prechecked` | `skipped` | The search found the record already in the destination (a commit-attempted record needs a fresh destination check in the same update) |
| `prechecked` | `parked` | The search was ambiguous (more than one match) |
| `prechecked` | `pending` | Released before filling (recovery) |
| `filling` | `submitting` | The next step is the commit step |
| `filling` | `verified` | Read-only path finished with no commit step |
| `filling` | `parked` | Locator unresolvable or ambiguous, an unknown popup, or a value needs review |
| `filling` | `failed` | A non-commit step failed with evidence |
| `filling` | `pending` | Released before the commit (recovery, pause) |
| `filling` | `skipped` | The browser search found the record already in the destination, before any commit step (only when `commit_attempted` is false) |
| `submitting` | `submitted_unverified` | The executor acknowledged the dispatch |
| `submitting` | `reconciling` | Crash or lost connection after the write-ahead: dispatch status unknown |
| `submitting` | `failed` | The same worker knows the command was never sent (see rule L3) |
| `submitted_unverified` | `verified` | `assert_after` passed; `committed_keys` row written in the same transaction |
| `submitted_unverified` | `failed` | The page shows a positive rejection (see rule L2) |
| `submitted_unverified` | `reconciling` | Crash, timeout, or anything short of proof |
| `reconciling` | `verified` | The destination check finds exactly this commit |
| `reconciling` | `pending` | The destination check finds nothing: retry, consuming an attempt |
| `reconciling` | `parked` | The destination check is ambiguous |
| `reconciling` | `failed` | The destination check cannot be performed (for example, the search itself keeps erroring) |
| `parked` | `pending` | A person corrected the record and re-queued it (a commit-attempted record needs `destination_checked_by`) |
| `parked` | `reconciling` | The commit outcome is unknown for a parked record: reconcile before requeue |
| `parked` | `skipped` | A person dismisses a parked record (a commit-attempted record needs a fresh destination check in the same update) |
| `failed` | `pending` | A person re-queued it (a commit-attempted record needs `destination_checked_by`) |
| `failed` | `reconciling` | The commit outcome is unknown: reconcile before requeue |

## Write-ahead sequence around a commit

The SQL is in [sql-patterns.md § Write-ahead](sql-patterns.md#write-ahead-around-a-commit-step). The rule: **the ledger
row is durable before the destination can change, and `verified` plus its `committed_keys` row commit together.**

| Crash after… | Row is in | Recovery moves it to | Why that is safe |
| --- | --- | --- | --- |
| Filling, before step 1 commits | `filling` | `pending` | No commit command was sent |
| Step 1 commits, before the click is sent | `submitting` | `reconciling` | Nobody can prove the command was not sent |
| The click is sent, before the acknowledgement | `submitting` | `reconciling` | The destination may have the record |
| The acknowledgement, before the assertion | `submitted_unverified` | `reconciling` | Same |
| The assertion passes, before step 4 commits | `submitted_unverified` | `reconciling` | Reconcile finds it and writes `verified` and the key |
| Step 4 commits | `verified` | (none) | Done |

## Recovery procedure

Runs when a worker claims a run, and after every successful takeover (on a fresh run it is a no-op).

1. Take ownership of the run: `claim_run` for a queued one, or `take_over_run` for one whose heartbeat has
   lapsed, which increments `lease_epoch`. Every state change is then fenced by `claimed_by` and the live
   `lease_epoch` (`FOR SHARE`), so a stale worker cannot write (H7).
2. Apply [sql-patterns.md § Recovery](sql-patterns.md#recovery-when-a-worker-starts-or-resumes-a-run): the
   commit window (`submitting`, `submitted_unverified`, and records already `reconciling`) → `reconciling`,
   reassigned to the new worker; `claimed`, `prechecked`, and `filling` → `pending`.
3. Reconcile every `reconciling` record before claiming any `pending` one. Check the destination using the recipe's
   reconcile check: exactly one match for this commit → `verified` with its `committed_keys` row; none → `pending`;
   several or unreadable → `parked`. For create workflows, "match" means the record key is found. For update
   workflows it means the new value is present. Finding the record is not enough (P-001).
4. Close out exhausted records ([§ Close out](sql-patterns.md#close-out-records-that-used-all-their-attempts)), then
   resume normal claims.

## Application rules the database does not enforce yet

P-001 proposed migration 0002 for each of these; L1, L4, L5, and L6 are now enforced by the database
(`db/migrations/0002_ledger_hardening.sql`). L2 and L3 remain application rules.

| Rule | Statement | Enforced by |
| --- | --- | --- |
| L1 | A record that has ever entered `submitting` never goes `failed → pending` or `parked → pending` unless a person confirms the destination does not have it. Its retry path goes through reconcile | **Database**: `commit_attempted` + the guard's `destination_checked_by`/`_at` rule |
| L2 | `submitted_unverified → failed` requires a positive rejection signal on the page. A timeout is not proof; a timeout goes to `reconciling` | Code (the runner) |
| L3 | `submitting → failed` only by the worker that wrote `submitting`, before it sent the command. Recovery never uses it | Code (the runner) |
| L4 | `run_records` rows are inserted only as `pending` and are never deleted; `attempts` and `max_attempts` are never written by application code | **Database**: the insert guard, the delete guard, and the attempts/`max_attempts` checks in the guard |
| L5 | A `committed_keys` insert that hits the conflict (0 rows) in the verify transaction is an incident: the run pauses with reason `duplicate_detected` | **Database**: the unique constraint; the runner reads the row count |
| L6 | Each state change writes its audit row in the same transaction (sql-patterns rule) | **Database**: the `run_record_transitions` trigger |

## Invariants and the tests that prove them

| # | Invariant | Enforced by | Test (layer, owner) |
| --- | --- | --- | --- |
| I1 | Only the 32 transitions above are accepted | Guard trigger | `tests/db`: all 121 ordered state pairs; 32 pass, 89 raise (DB, PL-012, PL-013) |
| I2 | `attempts` rises only on `pending → claimed`, and falls by one only on a marked pause release (0009); never more than `max_attempts` | Trigger and CHECK | `tests/db` attempts cap (DB, PL-002) |
| I3 | One ledger row per (run, record key) and per (run, input record) | UNIQUE | `tests/db` duplicate insert (DB, PL-002) |
| I4 | A key is committed at most once per tenant and destination | UNIQUE on `committed_keys` | `tests/db` ON CONFLICT inserts 0 rows (DB, PL-002) |
| I5 | Each record is claimed by exactly one worker | Claim pattern, `SKIP LOCKED` | Two-worker race over 50 records, 10 times (DB, PL-002) |
| I6 | No `submitting → pending`; a crash in the commit window passes through `reconciling` | Guard | `tests/db` illegal-transition case; kill test (Phase 3) |
| I7 | Zero duplicates in the destination after crashes | Write-ahead and reconcile | Kill test: 20 kills mid-save, then count by key through the demo test API (`tests/`, Phase 3) |
| I8 | A re-run of the same file submits nothing new (a create; PL-128: an update is set again and read back, since the site can be changed after a run) | `committed_keys` skip pattern | End-to-end re-run on the demo CRM (Phase 3) |
| I9 | `verified` after a commit implies a `committed_keys` row | Verify transaction | Integration: verified commit records = keys per run |
| I10 | Ledger, recipe versions, and audit rows cannot be edited | Append-only triggers | `tests/db` UPDATE/DELETE rejected (DB, PL-002) |
| I11 | L1 to L6 above | Database (L1, L4, L5, L6) and code (L2, L3) | `tests/db/test_ledger_hardening.py` for L1/L4/L6; `tests/breakit/` (T1) for L2/L3 |
| I14 | A commit-attempted record re-queues only through reconcile or a recorded destination check | `commit_attempted` + guard rule | `tests/db/test_ledger_hardening.py` G1 (DB, PL-012) |
| I15 | One active run per tenant and destination; a run carries `destination_key` and a `lease_epoch` | Partial unique index, insert guard | `tests/db/test_ledger_hardening.py` G7/G8 (DB, PL-012) |
| I16 | Every state change writes a `run_record_transitions` row, including bulk updates | Audit trigger | `tests/db/test_ledger_hardening.py` G6 (DB, PL-012) |
| I17 | The append-only tables reject `TRUNCATE`, and `run_records` rejects `DELETE` | Statement/row triggers | `tests/db/test_ledger_hardening.py` G5 (DB, PL-012) |
| I18 | Who approved a commit is recorded | `approvals` (append-only) | `tests/db/test_ledger_hardening.py` G10 (DB, PL-012) |
| I19 | `commit_attempted` only changes `false → true`; a destination check does not carry over to the next commit, and is set only in the requeue that uses it | Guard trigger | `tests/db/test_ledger_breakit.py` H1–H3 (DB, T1) |
| I20 | The transition-audit table is append-only | `forbid_mutation` trigger | `tests/db/test_ledger_breakit.py` H4 (DB, T1) |
| I21 | An empty `idempotency_key` or `record_key` is rejected; `runs.destination_key` never changes | Insert guard, immutability trigger | `tests/db/test_ledger_breakit.py` H8, H9 (DB, T1) |
| I22 | A stale lease cannot write while a takeover is in flight (SQL half) | `FOR SHARE` lease check | `tests/db/test_ledger_breakit.py` H7 (DB, T1) |
| I23 | `filling → skipped` is refused once the record has entered the commit window | Guard trigger | `tests/db/test_run_orchestration_migration.py` R1 (DB, PL-013) |
| I24 | An approval request resolves at most once; every other change, a DELETE, and a TRUNCATE are refused | `approval_requests` triggers | `tests/db/test_run_orchestration_migration.py` (DB, PL-013) |
| I25 | A destination check is fresh: within the last 5 minutes and not in the future | Guard trigger | `tests/db/test_run_orchestration_migration.py` (DB, PL-013) |
| I26 | `parked → skipped` and `prechecked → skipped` are refused for a commit-attempted record unless the same update records a fresh destination check | Guard trigger | `tests/db/test_run_orchestration_migration.py` item 2 (DB, PL-013) |
| I27 | A claim increments `lease_epoch`, and resuming or cancelling a paused run only re-queues it when no live worker holds it | `packages/ledger/runs.py` | `tests/runner/test_pause_resume.py` (DB, PL-013 review) |
| I28 | A guard refusal during a record never crashes the worker: the record is parked, or the run pauses `internal_error` (a `StaleLease` still stops the worker, which is correct) | `services/runner/run_loop.py` | `tests/runner/test_refused_transition.py` (DB, PL-013 review) |
| I12 | `run_summary` counts match the ledger | View | `tests/db` (DB, PL-002) |
| I13 | `packages/ledger` mirrors the guard exactly | Contract test | Compare its table with the 32 pairs read from the database |

PL-067: the runner can pause a run with `pause_reason = 'site_daily_cap'` before claiming a row, when the tenant's `site_daily_caps` setting has a limit for the recipe's origin and this tenant's rows that entered `submitting` today (UTC) have reached it. The record stays `pending`; nothing reaches the commit window, so every invariant above is untouched.
