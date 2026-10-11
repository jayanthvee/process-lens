# Review: PL-013 Step 0 plan

Status: approved with required changes · Reviewer: T1 · Reviewed commit: 81e1b3d (branch `pl-013`)

The plan is well structured: the module layout, the separation of `packages/ledger`, the fake executor, the crash
hooks, and a named test for every acceptance criterion. The ticket on the branch is identical to the one T1 wrote.
Implementation may start once the plan is amended with the changes below. **R1 to R6 change the design**; without
them several acceptance tests cannot pass honestly.

## Answers to the plan's four questions

1. **Reconcile definition.** Yes, option 1: `meta.reconcile` plus `reject_when`. T1 drafts the proposal; it needs
   the owner's decision. Build against the `Reconciler` interface.
2. **`end_record skipped` after browser steps: don't refuse it.** Our own `packages/recipe/examples/create-lead.recipe.json`
   does exactly this: it fills and clicks the search, then ends `skipped` if the lead exists. Refusing it would reject
   use case 1. See R1.
3. **Read queries in `packages/ledger/reads.py`.** Yes, that is the intent.
4. **`approval_requests`.** Yes, on the TRUNCATE list, and the one-time resolve trigger as written.

## Required changes

**R1. Add `filling -> skipped` (owner sign-off: changes a ledger transition).**
- A duplicate found by the browser search is found while the record is in `filling`, and the guard has no exit to
  `skipped` from there.
- In `0003`, add `filling -> skipped`, allowed only when `commit_attempted = false`. A record that may already have
  saved is never "skipped".
- Update `LEDGER_SPEC.md` (32 transitions) and the guard's matrix test.

**R2. Resolve the target before the write-ahead.**
- As written, a commit step whose locator fails is already in `submitting`. It then goes to `reconciling`, then
  `pending`, and is retried until attempts run out.
- The canary test ("locator failure on first commit pauses `canary_failed`") can't pass: `pending` isn't a failure
  outcome.
- Split the executor protocol for commit steps:
  - `prepare(command)`: resolves and hit-tests the target, with **no input sent**, while the record is still in
    `filling`. A locator failure goes `filling -> parked`; the record is not commit-attempted, and the canary
    catches it.
  - Then `begin_commit` (write-ahead), then `dispatch(command)`.
  - If `dispatch` reports `not_dispatched` (the prepared target vanished before any input event), go
    `submitting -> failed` (rule L3). Everything else short of proof goes to `reconciling`.
- PL-014 carries the same two messages over the WebSocket.

**R3. Approval re-entry.**
- As written, the record waits in `filling` with the run paused. On resume, recovery releases `filling -> pending`.
  The record is re-run from the start, reaches the gate again, and opens a **new** request. The approval of the old
  request never applies, so it loops.
- Fix: when the runner reaches a gate (an `approval_gate` step, or a commit the policy gates), it first looks for an
  **approved** request for the same `run_record_id` and `step_id` whose `shown` equals the values it is about to
  submit, byte for byte. If one exists, it passes. Otherwise it opens a new request and pauses.
- This makes the re-run idempotent, and an approval can never cover values the person didn't see.
- Also show the **policy** gate (`first_record`, `every_record`, `first_n`, and preview) explicitly in the pseudocode,
  immediately before `begin_commit`. Right now only `approval_gate` steps appear.

**R4. Status checks, pause, cancel, and resume.**
- **Re-read status.** `while run.status == 'running': if run.status == 'cancelled'` is unreachable. Re-read
  `runs.status` from the database at every step boundary, not only between records.
- **Pause at a step boundary.**
  - Outside the commit window: release the record `filling -> pending` with reason "paused", then stop.
  - Inside the commit window: finish the commit protocol first. Never stop between `begin_commit` and verify or
    reconcile.
- **Cancel.** Add `runs.cancel_requested_at` in `0003`. The API sets it, and also sets the status `paused -> queued`
  if no worker holds the run.
- **The worker finalizes a cancel:**
  - reconcile the commit-window records;
  - release the working records;
  - move `pending -> skipped` with reason "run cancelled";
  - set `status = 'cancelled'`.
- **Resume** sets `paused -> queued`.
- **Recovery always runs.** Run `recover_run` after **every** claim or takeover, not only takeovers. On a fresh run it
  is a no-op; on a resumed run it releases the stranded records. This works with R3.

**R5. Close out exhausted records before completing.**
- A record that reconcile returns to `pending` with `attempts = max_attempts` is never claimed again, because the
  claim requires `attempts < max_attempts`. The loop ends, and the run is marked completed with records still
  `pending`.
- At loop end: run `close_out_exhausted`, then mark `completed` **only** if every record is in an exit state.
  Otherwise investigate; never paper over it.

**R6. Heartbeat on its own thread, and abort on a stale lease.**
- Executor calls block, for assertion timeouts and approval waits. A heartbeat only between records goes stale
  mid-step, another worker takes over, and both act.
- Heartbeat from a separate thread. Make `heartbeat_timeout` well above the longest step (at least 3× the maximum
  step timeout plus dispatch).
- On any `StaleLease`, the worker stops at once and sends no further commands. `begin_commit` is fenced, so a stale
  worker can never write-ahead and click; keep it that way.

## Smaller fixes

- **S1. Order of checks.** Check "acknowledged" **before** "rejected". As written, a rejection without an
  acknowledgement calls `submitted_unverified -> failed` while the record is still in `submitting`, which raises
  `StateConflict`.
- **S2. Reconcile error.** The transition table says reconcile error goes to `failed`, but the ticket says `parked`.
  Use `parked`.
- **S3. Separation test scope.** As planned, it scans `tests/`, and `tests/db/*` and `seed.py` legitimately contain
  ledger SQL, so the test would fail on day one. Scan `services/` and `packages/` (excluding `packages/ledger`) only.
- **S4. `step_events`.** Write a `step_events` row for every executor step: timing, rung, observed, outcome, error.
  Do it in the same transaction as any state change that accompanies it. The plan writes one only in
  `verify_commit`, and the results screen needs step timings.
- **S5. Run creation consistency.**
  - Refuse unless the recipe version belongs to the workflow, the input batch belongs to the workflow, and all three
    share one tenant. The schema doesn't enforce this; T1 showed earlier that a run can pin another workflow's version.
  - Statically refuse an `end_record` outcome that is impossible on its path, for example `verified` before any step
    has run.
- **S6. Kill-test timing.** The takeover pattern compares `heartbeat_at` with the database's `now()`, so the
  `FrozenClock` doesn't affect it. In tests, make the dead worker's heartbeat stale by backdating `heartbeat_at`
  directly in test SQL, or with a zero timeout. Don't add a test-only branch to production code.

## Next

Amend the plan (a new section, "Changes from T1 review", covering R1–R6 and S1–S6). Commit, then proceed straight to
implementation; no second stop is needed. R1 needs the owner's yes, so wait for that answer before writing the
`filling -> skipped` part of 0003.
