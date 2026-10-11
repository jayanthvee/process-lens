# Review PL-013

Status: changes requested · Reviewer: T1 · Reviewed commit: bd7c7dc (branch `pl-013`)

## Verdict

Close, but not mergeable yet. This is solid work. All of R1–R6 and S1–S6 are implemented faithfully, the six holes
T2's own reviewers found were real and are fixed, and the run-creation and tenant checks are thorough. But two
break-it tests fail:
- **B1** submits the same record twice in a normal operator flow: pause, then resume, during a save.
- **B2** crashes the worker on a realistic retry path and strands the record.

Both fixes are contained.

## Gates (run on bd7c7dc)

```
$ python -m pytest -q          487 passed, 1 warning
$ ruff check .                 All checks passed!
$ ruff format --check .        127 files already formatted
$ python -m db.migrate         nothing to apply: the database is up to date   (run twice)
```

The three existing test files changed only as required: the transition count 31 → 32, `filling → skipped` added to
the guard's list, and `0003` added to the migration list. `0001` and `0002` are untouched.

## Verified as correct

- **The prepare → policy gate → `begin_commit` → dispatch order (R2, R3).** A locator failure parks the record
  before the write-ahead, so the canary catches it.
- **Approval re-entry.** The gate passes only on an approved request with identical `shown` values, and the
  newest decision wins, so a later approval overrides an old rejection.
- **Recovery and status re-reads (R4).** `recover_run` runs after every claim or takeover, and status is re-read at
  every step boundary.
- **Close-out (R5).** Exhausted records are closed out before completion; the run completes only when every
  record is in an exit state, and otherwise pauses with `records_not_exit`.
- **The heartbeat (R6)** runs on its own thread, the 3× headroom is enforced, and a `StaleLease` stops the worker.
- **Commit outcomes.** Acknowledgement is checked before rejection (S1). Reconcile errors park (S2). `not_dispatched`
  → `failed` (L3). Anything short of proof → `reconciling`. Zero rows from the `committed_keys` insert pauses the run
  `duplicate_detected`.
- **Run creation** refuses a version or batch from another workflow, and refuses a cross-tenant pin (S5). The API
  scopes every query to its tenant, with tests.

## Break-it results

The test file is `tests/runner/test_breakit_pl013.py`, uncommitted. Result: **2 failed**.

### B1. Pause then resume during a save submits the record twice (must fix)

**What happens**, as `test_pause_then_resume_during_a_save_never_double_submits` reproduces it (a second
submission is recorded):
1. Worker A acknowledges a click; the record is `submitted_unverified`.
2. The operator pauses and immediately resumes. `resume_run` moves the run `paused → queued` even though A still
   holds it (fresh heartbeat, `worker_id = A`).
3. Worker B claims it. `claim_run` does **not** bump `lease_epoch`, so B runs with the same epoch as A.
4. B's recovery moves A's record to `reconciling`. Reconcile searches before A's save has landed, finds nothing,
   and returns it to `pending`. B re-claims it and saves it.
5. Then A's save lands. In a real CRM, that's two leads.

**Fix (both parts):**
1. **`claim_run` increments `lease_epoch`**, so every claim fences out any previous holder. This is defence in depth.
2. **Hand-back.** A worker that stops (pause, awaiting approval, or shutdown) releases the run as its last act:
   `UPDATE runs SET worker_id = NULL WHERE id = $run AND worker_id = $me AND status = 'paused'`. If that updates
   0 rows because the run was resumed in the meantime, the worker keeps going instead of leaving.
3. **`resume_run` and `request_cancel` move `paused → queued` only if the run has been released**
   (`worker_id IS NULL`) or its heartbeat is stale. Otherwise they move it `paused → running`, so the current holder
   simply carries on.

The acceptance test is B1 passing, plus a test that the holder continues after a quick resume.

### B2. A retried record whose earlier save landed crashes the worker (must fix)

**What happens**, as `test_a_retried_record_whose_earlier_save_landed_is_not_stranded` reproduces it (the worker
raises the guard's exception):
1. The save lands, but its confirmation is missed, so the record goes to `reconciling`. The destination's search
   lags, so reconcile finds nothing and the record goes back to `pending`.
2. On the retry, the recipe's duplicate search finds the lead and the recipe says `end_record: skipped`.
3. The record is commit-attempted, so the database correctly refuses `filling → skipped` (R1).
4. The runner doesn't handle the refusal. The worker crashes, and the record is stranded in `filling`. Every
   restart repeats this until attempts run out, and then the record ends `failed` although the lead exists.

**Fix:**
- In `end_record`, if the outcome is `skipped` and the record is commit-attempted, move `filling → parked` with the
  reason "found in the destination after an earlier save attempt: confirm it is ours". It is not lost, and nothing
  is submitted twice.
- More generally, a refused transition (`IllegalTransition` or `StateConflict`) during a record must never crash the
  worker. Park the record with the error if that is legal. Otherwise pause the run with `internal_error` and the
  details, and log it. Add a test for that path.
- Optional later improvement (owner): a migration allowing `filling → reconciling` for commit-attempted records, so
  this case ends `verified` with its key, through reconcile.

## Answer to T2's open question

**Should `parked → skipped` carry the R1 rule? Yes.**
- Allow `parked → skipped` only when `commit_attempted = false`, or when the same update records a fresh
  destination check (`destination_checked_by` and `destination_checked_at`, as for re-queue).
- A record that may already have saved must not be dismissed as "skipped" without someone confirming it.
- This needs a migration (`0004`). It can go in with these fixes, since nothing is merged yet. Alternatively,
  amend `0003` in place and recreate local databases, as with PL-012.

## Instructions for T2

1. Fix B1 and B2 as described. Commit `tests/runner/test_breakit_pl013.py` with the fix: both tests must pass
   without changing their assertions.
2. Add the `parked → skipped` rule (amend `0003`; it is unmerged). Update `LEDGER_SPEC.md` and the matrix test.
3. Re-run the gates, add a "Review fixes" section to the report, commit, push, and stop.

---

## Re-review: approved

Reviewed commit: 4507ad8 (branch `pl-013`) · Verdict: **approved for merge**

```
$ python -m pytest -q                                   516 passed, 1 warning
$ python -m pytest -q tests/runner/test_breakit_pl013.py  2 passed   (both failed on bd7c7dc)
$ ruff check .                                          All checks passed!
$ ruff format --check .                                 131 files already formatted
$ python -m db.migrate                                  nothing to apply   (run twice)
```

**B1 is fixed correctly.**
- `claim_run` bumps `lease_epoch`.
- A stopping worker releases the run only while it is still paused. If the release matches 0 rows, the run was
  resumed, so the worker keeps going.
- `resume_run` and `request_cancel` queue a paused run only when it was released or its heartbeat lapsed. Otherwise
  they hand it back to the live holder as `running`.

**B2 is fixed correctly.**
- A commit-attempted `end_record skipped` parks the record with the agreed reason.
- `_handle_refused` re-reads the state and parks through the fenced `change_state`, so it cannot trample another
  worker's record. A `StaleLease` still stops the worker, and anything that cannot be parked pauses the run with
  `internal_error`.

**The `parked -> skipped` rule** is in the guard, the spec and the tests.

**The `prechecked -> skipped` extension is accepted.** It is the same owner-approved principle: a record that may
already have saved is never silently skipped. Its only effect is that an input-rule skip on a retried,
commit-attempted record parks the record instead of skipping it.

**Tests.** My break-it file is committed with its assertions unchanged. The four lease-race edits follow the new
epoch-on-claim rule and are no weaker.

### Follow-ups (not blocking)

1. **Hand back on the `internal_error` pause.** That path (`_handle_refused` returns `None`) exits the loop
   without `_hand_back`. The other pauses do hand back: `duplicate_detected`, `awaiting_approval` and `canary_failed`
   through the loop's status check, and `records_not_exit` at the end of the run. If a person resumes within the heartbeat timeout, the
   run goes to `running` with no live holder, and it stalls until the stale-run takeover (about 6 minutes). It never
   double-submits, but it is a needless stall. Release on every pause path.
2. **`docker-compose.yml`** (your uncommitted MinIO image change): T2's reviewer noted the MinIO healthcheck may not
   work with the Chainguard image. Check it before committing that change.
