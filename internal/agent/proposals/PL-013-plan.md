# PL-013 Step 0 plan: run orchestrator and runs API

Status: for T1 review · Author: T2 · Date: 2026-10-11 · Ticket: `internal/agent/tickets/PL-013.md`
Depends on: PL-012 (merged)

This is a plan only. No implementation code is written until T1 approves it.

## 0. Preconditions, verified

- `pl-013` already exists, branched from an up-to-date `main` (`main == origin/main`, HEAD `0df7362`),
  with the ticket committed as `d2fbe26` and pushed (`pl-013 == origin/pl-013`). So "create the branch
  and commit the ticket" is already satisfied; nothing was re-created or duplicated.
- `packages/ledger/` and `services/runner/` contain only `.gitkeep`. `services/api/` has `main.py`,
  `db.py`, `recipes.py`. Migrations are `0001`, `0002`; `0003` does not exist yet.
- The ledger authority is `guard_record_transition()` (replaced by 0002). 11 record states, 31 legal
  transitions. `runs.status` is `queued|running|paused|completed|cancelled|failed`; there is no
  `running` record state.

## 1. Module layout and public names

### 1.1 `packages/ledger/` — the only place ledger SQL lives

One typed function per pattern in `internal/docs/sql-patterns.md`, grouped by concern. Every function
takes a `psycopg.Connection`; none opens its own connection or transaction (the caller owns the
transaction boundary, because the write-ahead pattern depends on it).

- `errors.py`
  - `LedgerError(Exception)` — base.
  - `StaleLease(LedgerError)` — a state change matched 0 rows because `claimed_by`/`lease_epoch` were stale.
  - `StateConflict(LedgerError)` — a state change matched 0 rows because the state was not the expected one.
  - `IllegalTransition(LedgerError)` — the guard raised (caught and re-raised typed).
- `models.py` — frozen dataclasses: `RunClaim`, `RecordClaim`, `VerifyResult(key_inserted: bool,
  destination_ref)`, `RecoveryCounts(reconciled: int, released: int)`, `ApprovalRequestRow`.
- `runs.py`
  - `claim_run(conn, worker_id, *, heartbeat_timeout) -> RunClaim | None`
  - `take_over_run(conn, run_id, worker_id, *, heartbeat_timeout) -> int | None` (returns the new `lease_epoch`, else `None`)
  - `heartbeat(conn, run_id, worker_id, epoch) -> None` (raises `StaleLease` on 0 rows)
  - `set_run_status(conn, run_id, status, *, pause_reason=None) -> None`
- `records.py`
  - `claim_record(conn, run_id, worker_id, epoch) -> RecordClaim | None`
  - `change_state(conn, record_id, *, worker, epoch, expected, new, reason, error=None) -> None`
    (state change pattern; raises `StaleLease`/`StateConflict`)
  - `requeue_record(conn, record_id, *, reason, destination_checked_by=None) -> Literal["pending","reconciling"]`
    (both variants from `sql-patterns.md`; picks by `commit_attempted`)
- `commits.py`
  - `begin_commit(conn, record_id, *, worker, epoch) -> None` (write-ahead `filling -> submitting`)
  - `mark_submitted_unverified(conn, record_id, *, worker, epoch) -> None` (`submitting -> submitted_unverified`)
  - `verify_commit(conn, record_id, *, worker, epoch, destination_ref, step_event) -> VerifyResult`
    (one transaction: `-> verified` + `committed_keys` insert + `step_events`; reports `key_inserted`)
  - `skip_already_committed(conn, run_id, destination_key) -> int` (`pending -> skipped`)
- `recovery.py`
  - `recover_run(conn, run_id, worker) -> RecoveryCounts` (commit window → `reconciling` reassigned to `worker`; `claimed|prechecked|filling` → `pending`)
  - `close_out_exhausted(conn, run_id) -> int` (`pending -> failed`)
- `approvals.py`
  - `open_approval_request(conn, *, tenant_id, run_id, run_record_id, step_id, kind, shown) -> UUID`
  - `record_approval(conn, *, request_id, decision, decided_by, reason) -> UUID` (insert `approvals` + set `approval_requests.resolved_by_approval_id`)
- `jobs.py`
  - `claim_job(conn, worker_id) -> JobClaim | None`
- `reads.py` — read-only shapes the API needs (no writes, so no pattern): `get_run`, `run_summary`,
  `list_run_records`, `list_transitions(after_id)`, `open_approval_for_run`, `rows_for_results`.
- `__init__.py` re-exports the public names.

**Separation rule.** Every SQL statement that names `runs`, `run_records`, `committed_keys`, or `jobs`
lives in `packages/ledger/`. `tests/runner/test_sql_separation.py` greps the whole source tree and
fails if any of those four table names appears in a SQL string outside `packages/ledger/`. The scan
covers `services/`, `packages/` (minus `packages/ledger`), `tests/`, and excludes `db/migrations/`
(migrations are the schema, not application SQL) and `internal/`.

### 1.2 `services/runner/`

- `templates.py` — `render(template, *, row, variables) -> str`; filters `lower`, `upper`, `trim`;
  `TemplateError` on unknown filter, missing column, or missing variable.
- `executor.py` — the browser seam:
  - `StepCommand` (frozen dataclass): `command_id, run_id, run_record_id, lease_epoch, step (values rendered), commit: bool, expires_at`.
  - `StepResult`: `status: Literal["ok","rejected","locator_failed","timeout","lost"]`, `observed`, `error`.
  - `Executor(Protocol)`: `perform(command, on_dispatched) -> StepResult`; `check_condition(condition, *, timeout_ms) -> ConditionResult`.
- `conditions.py` — `condition_timeout(condition, fallback)`; thin helper over the executor's check.
- `reconcile.py` — `Reconciler(Protocol)`: `search(record_key/idempotency_key) -> ReconciledDiff`;
  `outcome(...) -> "verified" | "pending" | "parked"`. `NoopReconciler` for tests that never reach it.
- `creation.py` — `create_run(conn, *, workflow_id, recipe_version_id, input_batch_id, mode) -> UUID`.
  Validates the recipe, refuses unsupported actions, renders keys per row, inserts `runs` +
  one `run_records` row per `ready` `input_records` row (through `packages/ledger`).
- `approvals.py` — `evaluate_policy(policy, *, run, commits_so_far, mode) -> bool` and the gate flow.
- `worker.py` — `Worker(worker_id, executor, reconciler, clock)`; `run_once()` claims/takes over a run
  and processes it; `main(argv)` for `python -m services.runner --worker-id <id>`.
- `run_loop.py` — `process_run(...)` and `process_record(...)`, the record loop (section 3).
- `__main__.py` — argparse entry point.
- `testing.py` — `FakeExecutor`, `FakeDestination`, `CrashHooks`, `ScriptedRun` (section 4). **Production
  modules never import `testing`**; a test asserts that.

### 1.3 `services/api/`

- `runs.py` — router `prefix="/api/v1"`: `POST /runs`, `GET /runs/{id}`, `GET /runs/{id}/records`,
  `GET /runs/{id}/transitions`, `POST /runs/{id}/pause|/resume|/cancel`,
  `POST /records/{id}/requeue`, `GET /runs/{id}/results.csv`.
- `approvals.py` — `POST /api/v1/approvals`.
- `main.py` — `app.include_router(runs_router)` and `app.include_router(approvals_router)`.

Endpoints read and write only through `packages/ledger` (the separation test covers this). Every query
is scoped by the `tenant_id` taken from the run/workflow/record itself; a client-supplied tenant id is
never trusted.

### 1.4 Tests

- `tests/runner/` (new): `conftest.py`, `test_templates.py`, `test_creation.py`, `test_happy_path.py`,
  `test_commit_window.py`, `test_reconcile.py`, `test_approval.py`, `test_canary.py`,
  `test_kill_points.py`, `test_rerun.py`, `test_lease_race.py`, `test_cancel.py`,
  `test_sql_separation.py`, `test_no_sleeps.py`.
- `tests/db/` (new files only): `test_runs_api.py`, `test_run_orchestration_migration.py`,
  `test_ledger_package.py`. `tests/db/seed.py` gains helpers only (e.g. `new_run(status=...)` already
  exists; add `linked_recipe`/records as needed) — no existing assertion changes.

## 2. Migration `0003_run_orchestration.sql`

1. **Fresh destination check.** `CREATE OR REPLACE FUNCTION guard_record_transition()` copied from 0002
   with one added clause: in the `failed|parked → pending` re-queue that sets `destination_checked_by`,
   also require `NEW.destination_checked_at >= now() - interval '5 minutes'` and `<= now()`. Backdated or
   future checks raise. (PL-012 review follow-up 1.)
2. **`approval_requests` table**: `id uuid pk`, `tenant_id`, `run_id` → `runs`, `run_record_id` →
   `run_records`, `step_id text`, `kind approval_kind`, `shown jsonb`, `created_at`, and
   `resolved_by_approval_id uuid` → `approvals` (nullable). Trigger: a `BEFORE UPDATE` allows only a
   change that sets `resolved_by_approval_id` from NULL to non-NULL, once; any other change raises.
   `BEFORE DELETE` raises; a `BEFORE TRUNCATE` statement trigger raises. (TRUNCATE set grows to include
   `approval_requests`.)
3. **`approvals.request_id`**: `alter table approvals add column request_id uuid references approval_requests(id)`.

## 3. The worker and record loop (pseudocode, every transition named)

```
run_once(worker_id):
    run = ledger.claim_run(worker_id)                     # queued -> running (or skip a busy destination)
    if run is None:
        epoch = ledger.take_over_run(stale run, worker_id) # running|paused -> same, lease_epoch + 1
        if epoch: recovered = ledger.recover_run(run, worker_id)
    process_run(run, epoch)

process_run(run, epoch):
    ledger.skip_already_committed(run)                    # pending -> skipped
    ledger.close_out_exhausted(run)                       # pending -> failed
    while run.status == 'running':
        if run.status == 'cancelled':                     # read between steps
            reconcile_commit_window(run); return
        if run.status == 'paused': return
        record = ledger.claim_record(run, worker, epoch)  # pending -> claimed  (attempts = attempts + 1)
        if record is None: return
        outcome = process_record(record, run, epoch)
        if is_canary(record) and outcome in FAILURE_OUTCOMES:
            ledger.set_run_status(run, 'paused', pause_reason='canary_failed'); return
    mark run completed (runs.status only; records already carry the exit states)
```

```
process_record(record, run, epoch):
    row = record.clean

    # 1. prechecks (meta.input_schema.rules: predicate -> skip|park|fail)
    for rule in rules:
        if predicate_holds(rule.when, row):
            ledger.change_state(record, expected='claimed', new=rule_then_state(rule.then), reason=rule.reason)
            return rule_then_state(rule.then)             # claimed -> skipped | parked | failed
    ledger.change_state(record, expected='claimed', new='prechecked', reason='precheck passed')
                                                          # claimed -> prechecked

    # 2. steps, in order; ledger state tracked as `state`
    state = 'prechecked'
    for step in recipe.steps:
        if step.action == 'branch':
            ok = executor.check_condition(step.when)      # executor check
            sub = step['then'] if ok else step.get('else', [])
            terminal = run_steps(sub, row, state)          # recursive; may end the record
            if terminal: return terminal
        elif step.action == 'approval_gate':
            if step.when and not executor.check_condition(step.when): continue
            ledger.open_approval_request(record, step, shown=rendered_values(step))
            ledger.set_run_status(run, 'paused', pause_reason='awaiting_approval')
            return 'awaiting_approval'                     # no transition; record stays filling
        elif step.action == 'end_record':
            outcome = step.outcome                          # verified | skipped | parked (never failed)
            ledger.change_state(record, expected=state, new=outcome, reason=rendered(step.reason))
            return outcome
        else:
            if state == 'prechecked':
                ledger.change_state(record, expected='prechecked', new='filling', reason='steps begin')
                state = 'filling'                          # prechecked -> filling
            if step.commit:
                ledger.begin_commit(record, worker, epoch)  # filling -> submitting (committed)
                crash_hook('after_submitting')
                result = executor.perform(command(step), on_dispatched=lambda: ack(record))
                # on_dispatched: submitting -> submitted_unverified (committed by the callback)
                crash_hook('after_dispatch')
                if result.status == 'rejected':
                    ledger.change_state(record, 'submitted_unverified' -> 'failed', reason=result.error)
                    return 'failed'                        # submitted_unverified -> failed
                if not acknowledged:
                    ledger.change_state(record, 'submitting' -> 'reconciling', reason='no dispatch proof')
                    return reconcile(record)               # submitting -> reconciling
                ok = executor.check_condition(step.assert_after, timeout_ms=step.timeout_ms)
                crash_hook('after_assert')
                if ok:
                    vr = ledger.verify_commit(record, destination_ref=result.observed['ref'], ...)
                    # submitted_unverified -> verified  (+ committed_keys, + step_events)
                    if not vr.key_inserted:
                        ledger.set_run_status(run, 'paused', pause_reason='duplicate_detected')
                    return 'verified'
                ledger.change_state(record, 'submitted_unverified' -> 'reconciling', reason='assertion timeout')
                return reconcile(record)                   # submitted_unverified -> reconciling
            else:
                result = executor.perform(command(step), on_dispatched=None)
                if result.status == 'ok': continue
                fail_to = 'failed' if step.on_failure == 'fail' else 'parked'
                ledger.change_state(record, expected='filling', new=fail_to, reason=result.error)
                return fail_to                            # filling -> parked | failed

    # steps exhausted without end_record
    if state == 'prechecked':
        ledger.change_state(record, 'prechecked' -> 'parked', reason='no terminal step')  # defensive
        return 'parked'
    ledger.change_state(record, 'filling' -> 'verified', reason='steps completed')        # read-only path
    return 'verified'
```

```
reconcile(record, run, epoch):                             # Reconciler interface (fake in this ticket)
    outcome = reconciler.outcome(record)
    if outcome == 'verified':  ledger.verify_commit(...)             # reconciling -> verified
    elif outcome == 'pending': ledger.change_state('reconciling' -> 'pending', reason='not found; retry')
    elif outcome == 'parked':  ledger.change_state('reconciling' -> 'parked', reason=...)
    return outcome
```

**Every ledger transition the runner uses**, by name:

| Stage | Transition |
| --- | --- |
| `claim_run` | `queued → running` (runs.status) |
| precheck skip / park / fail | `claimed → skipped` / `claimed → parked` / `claimed → failed` |
| precheck pass | `claimed → prechecked` |
| first executor step | `prechecked → filling` |
| `end_record` skipped before filling | `prechecked → skipped` |
| `end_record` parked | `claimed` / `prechecked` / `filling → parked` |
| `end_record` verified after read-only steps | `filling → verified` |
| non-commit failure, park / fail | `filling → parked` / `filling → failed` |
| commit write-ahead | `filling → submitting` |
| dispatch acknowledged | `submitting → submitted_unverified` |
| dispatch never sent (L3) | `submitting → failed` |
| lost / short of proof | `submitting → reconciling` |
| assertion passed | `submitted_unverified → verified` (+ `committed_keys`) |
| positive rejection | `submitted_unverified → failed` |
| assertion timeout / lost | `submitted_unverified → reconciling` |
| reconcile found / none / ambiguous / error | `reconciling → verified` / `→ pending` / `→ parked` / `→ failed` |
| cancel before commit | `filling → pending` |
| cancel in commit window | `submitting` / `submitted_unverified → reconciling`, then reconcile |
| release on shutdown (`recover_run`) | `claimed` / `prechecked` / `filling → pending` |
| already committed, pre-batch | `pending → skipped` |
| exhausted close-out | `pending → failed` |
| person re-queue | `parked` / `failed → pending`, or `→ reconciling` when `commit_attempted` |

`end_record outcome=verified` when the record is already `verified` is a same-state update (accepted).
`end_record outcome=skipped` is only legal before `filling`; the runner treats an impossible outcome as
a run-creation error where it can be checked statically, and otherwise as a `StateConflict` at runtime.

## 4. Fake executor and fake destination

Both live in `services/runner/testing.py` and are never imported by production modules.

**`FakeDestination`** — an in-memory stand-in for the downstream system:
- `commit(key, payload) -> ref | DUPLICATE | REJECTED`; stores one row per `key` (idempotency key), so a
  second commit of the same key returns `DUPLICATE` and writes nothing.
- `search(key) -> 0 | 1 | many` for the `Reconciler`.
- Scriptable per key: `ok`, `duplicate`, `slow_save`, `validation_rejection`, `locator_failure`.
- Counts commits and rejects, and exposes `all_keys()` for the zero-duplicate assertions.

**`FakeExecutor`** — implements `Executor`:
- Scriptable plan: a dict from `command_id` or `(record_key, step_id)` to a `StepResult`.
- Drives `FakeDestination` on a commit command, so a `click` commit either lands (`ok`), duplicates, or
  is rejected according to the script.
- Calls `on_dispatched()` for a commit command unless the script says the dispatch was lost.
- `check_condition` returns scripted booleans (or evaluates simple conditions against a tiny page model).

**`CrashHooks`** — `crash_hook(point)` raises `SimulatedCrash(point)` for the four write-ahead points:
`after_submitting`, `after_dispatch`, `after_ack`, `after_assert`. Injected into the run loop. A test
catches `SimulatedCrash` to simulate a worker dying, then a fresh worker takes over and recovers.

**Determinism.** The tests use a `FrozenClock` and an explicit `sleep`/timer seam, so nothing waits on
real time and there are no fixed sleeps.

## 5. Acceptance criteria → tests

- **Plan committed and T1-approved** — this file; process gate, no test.
- **A ledger function per pattern; SQL nowhere else** — `tests/db/test_ledger_package.py::test_every_sql_pattern_has_a_ledger_function`; `tests/runner/test_sql_separation.py::test_ledger_table_sql_lives_only_in_packages_ledger`.
- **Happy path, 20 records** — `tests/runner/test_happy_path.py::test_twenty_record_batch_verifies_every_record` (every record `verified`/`skipped`/`parked` with a reason; each verified key exactly once).
- **Kill test, deterministic** — `tests/runner/test_kill_points.py::test_crash_at_each_write_ahead_point_leaves_no_duplicates` (4 points × 4 repetitions; new worker recovers; zero duplicates; every record in an exit state).
- **Re-run skips everything** — `tests/runner/test_rerun.py::test_second_run_skips_every_committed_key`.
- **Two workers race** — `tests/runner/test_lease_race.py::test_only_one_worker_holds_the_run` and `...::test_stale_worker_state_change_raises_stale_lease`.
- **Canary** — `tests/runner/test_canary.py::test_locator_failure_on_first_commit_pauses_canary_failed` (and no other record claimed).
- **Approval** — `tests/runner/test_approval.py::test_first_record_gate_stops_without_preview`, `...::test_approval_resumes_the_run`, `...::test_rejection_parks_with_reviewer_reason`, `...::test_approval_row_references_its_request`.
- **Assertion timeout → reconciling; reconcile outcomes** — `tests/runner/test_commit_window.py::test_assertion_timeout_goes_to_reconciling_not_failed`; `tests/runner/test_reconcile.py::test_reconcile_one_match_verifies`, `...::test_reconcile_no_match_returns_to_pending`, `...::test_reconcile_ambiguous_parks`, `...::test_reconcile_error_parks`.
- **Cancel reconciles the commit window** — `tests/runner/test_cancel.py::test_cancel_during_commit_window_reconciles_before_stopping`.
- **Creation refuses bad input** — `tests/runner/test_creation.py::test_refuses_unsupported_action`, `...::test_refuses_unknown_template_filter`, `...::test_refuses_missing_column`, and `...::test_rendering_failure_lists_every_failing_row`.
- **Every API endpoint + tenant scoping** — `tests/db/test_runs_api.py` (one test per endpoint, plus `::test_tenant_scoping_hides_other_tenants_runs`).
- **Migration 0003** — `tests/db/test_run_orchestration_migration.py::test_backdated_destination_check_is_rejected`, `...::test_future_destination_check_is_rejected`, `...::test_approval_request_resolves_once`, `...::test_approval_request_other_change_is_rejected`, `...::test_approval_requests_rejects_delete_and_truncate`.
- **No fixed sleeps / no model SDK** — `tests/runner/test_no_sleeps.py::test_runner_has_no_fixed_sleep_or_time_sleep`, `...::test_no_model_sdk_imported_under_runner_or_ledger`.
- **Gates** — `python -m pytest -q`, `ruff check .`, `ruff format --check .`, `python -m db.migrate` twice.
- **Report** — `internal/agent/reports/PL-013.md`.

## 6. Invariants and guardrails this plan holds

- D-017: zero model calls in the batch loop; `end_record` never ends `failed`; no AI steps.
- D-004: the database ledger is the only workflow state; the worker re-reads `runs.status` between steps.
- Write-ahead before every commit; reconcile before any retry of a record that reached `submitting`.
- Waits are conditions with timeouts; no fixed sleeps anywhere under `services/runner`.
- Every state change sets `reason`; every change is fenced by `claimed_by` and `lease_epoch FOR SHARE`.

## 7. Commit sequence and gates

1. `docs(runner): add the PL-013 step 0 plan` — this file. Push `pl-013`. **Stop for T1.**
2. After approval: migration, then `packages/ledger`, then `services/runner`, then the API, then tests,
   each in its own conventional commit, running the gates at the end.

## 8. Risks and open questions for T1

1. **Reconcile definition** (ticket's open decision). The plan builds against `Reconciler`; T1's option-1
   proposal (`meta.reconcile` + `reject_when`) is not needed to finish this ticket. Confirm option 1.
2. **`end_record` outcome vs. ledger state.** `skipped` is legal only from `pending`/`prechecked`, and
   `verified` only from `filling`. A recipe that ends `skipped` after a browser step has no legal
   transition. Proposed: reject such a recipe at run creation. Confirm.
3. **Read queries in `packages/ledger`.** The separation rule implies the API's reads go through the
   package too, so `reads.py` is included even though `sql-patterns.md` lists only writes. Confirm that
   is the intent (it makes the grep test meaningful).
4. **`approval_requests` on the TRUNCATE list** and the one-time resolve trigger — the update-allowance
   is narrow; confirm the exact wording is acceptable.
