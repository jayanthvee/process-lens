# SQL patterns

Read this before writing any query that touches `runs`, `run_records`, `committed_keys`, or `jobs`.
These are the only approved shapes for those operations. If you need a new one, add it here first.

## Claim the next queued run (run worker)

The query skips a run whose destination already has an active run, so it does not compete for the
`runs_one_active_per_destination` slot and crash the loop (H6). If a race still produces a `23505`
on that index, treat it as "nothing to claim" and move on to the next loop.

```sql
UPDATE runs
SET status = 'running', worker_id = $1, started_at = coalesce(started_at, now()), heartbeat_at = now()
WHERE id = (
  SELECT id FROM runs
  WHERE status = 'queued'
    AND NOT EXISTS (
      SELECT 1 FROM runs active
      WHERE active.tenant_id = runs.tenant_id
        AND active.destination_key = runs.destination_key
        AND active.status IN ('running', 'paused')
    )
  ORDER BY created_at
  FOR UPDATE SKIP LOCKED
  LIMIT 1
)
RETURNING *;
```

## Claim the next record in a run

`attempts` is incremented by the database trigger, never by application code.

The order is the input row's `row_index`, not `created_at`: a batch's records are inserted
together, so `created_at` ties and the random uuid tie-break makes the claim order -- and the
canary, the first record to reach a commit -- non-deterministic. `row_index` is the person's row
order, so `row 0` is claimed first and the canary is knowable in advance.

```sql
WITH next AS (
  SELECT rr.id FROM run_records rr
  JOIN input_records ir ON ir.id = rr.input_record_id
  WHERE rr.run_id = $1 AND rr.state = 'pending' AND rr.attempts < rr.max_attempts
  ORDER BY ir.row_index, rr.id
  FOR UPDATE OF rr SKIP LOCKED
  LIMIT 1
)
UPDATE run_records r
SET state = 'claimed', claimed_by = $2, claimed_at = now()
FROM next
WHERE r.id = next.id
RETURNING r.*;
```

## Change state (always conditional on the state you expect)

Check the affected row count. Zero rows means someone else moved the record, or the worker's lease is
stale; stop and re-read. The update is scoped to the worker that claimed the record and to the run's
current `lease_epoch`, so a stale worker cannot overwrite a live one (G8).

```sql
UPDATE run_records
SET state = $new_state, reason = $reason, last_error = $error
WHERE id = $id
  AND state = $expected_state
  AND claimed_by = $worker
  AND EXISTS (
    SELECT 1 FROM runs
    WHERE id = run_records.run_id AND lease_epoch = $epoch
    FOR SHARE
  );
```

The lease check locks the `runs` row `FOR SHARE`, so a write whose lease is being taken over waits
for the takeover to commit and then fails the `lease_epoch` check, instead of reading the old
committed epoch and landing (H7).

## Take over a run (a worker whose lease is stale)

Recovery runs only after a successful takeover. The heartbeat timeout is a configuration value.

```sql
UPDATE runs
SET worker_id = $worker,
    lease_epoch = lease_epoch + 1,
    heartbeat_at = now()
WHERE id = $run
  AND status IN ('running', 'paused')
  AND heartbeat_at < now() - $heartbeat_timeout
RETURNING lease_epoch;
```

Zero rows means another worker holds the run or it is already done; do not run recovery.

## Write-ahead around a commit step

1. `UPDATE ... SET state = 'submitting' WHERE id = $id AND state = 'filling'` and COMMIT the transaction.
2. Only then send the click to the extension.
3. On click acknowledgment: `state = 'submitted_unverified'`, COMMIT.
4. On assertion pass, in ONE transaction:

```sql
BEGIN;
UPDATE run_records
SET state = 'verified', destination_ref = $ref
WHERE id = $id AND state IN ('submitted_unverified', 'reconciling');

WITH written AS (
  INSERT INTO committed_keys (tenant_id, destination_key, idempotency_key, workflow_id, run_record_id, destination_ref)
  VALUES ($tenant, $destination, $idempotency_key, $workflow, $id, $ref)
  ON CONFLICT (tenant_id, destination_key, idempotency_key) DO NOTHING
  RETURNING id
)
SELECT count(*) FROM written;   -- 0 rows: another record already committed this key

INSERT INTO step_events (tenant_id, run_record_id, step_id, attempt, started_at, ended_at, outcome, locator_rung, observed)
VALUES (...);
COMMIT;
```

The `SELECT count(*)` from the `committed_keys` insert is the L5 check: **0 rows means another record
already committed this key.** Pause the run with `pause_reason = 'duplicate_detected'`; never treat it
as success.

## Recovery when a worker starts or resumes a run

Recovery runs **after every claim or takeover** (on a fresh run it is a no-op), and it reassigns the
records it moves to the new worker, so the new worker can act on them under the worker-scoped state
change (H5).

Records that might have been submitted go to `reconciling`, including records that were already
`reconciling` under the previous worker's claim, so the new worker can reconcile every one of them.
Records that never reached a commit go back to `pending`.

```sql
UPDATE run_records
SET state = 'reconciling', claimed_by = $worker, claimed_at = now()
WHERE run_id = $1 AND state IN ('submitting', 'submitted_unverified', 'reconciling');

UPDATE run_records
SET state = 'pending', claimed_by = NULL, claimed_at = NULL
WHERE run_id = $1 AND state IN ('claimed', 'prechecked', 'filling');
```

Then reconcile **every** `reconciling` record **before claiming any** `pending` one: search the
destination by record key. Found → `verified` (plus `committed_keys`). Not found → `pending`.
Ambiguous, or a search error → `parked`.

## Close out records that used all their attempts

```sql
UPDATE run_records
SET state = 'failed', reason = 'attempts exhausted'
WHERE run_id = $1 AND state = 'pending' AND attempts >= max_attempts;
```

## Skip keys already committed by earlier runs (before a batch starts)

The match is on the record's own `idempotency_key`, not a single bind parameter, so it works for a
batch (G9). A matched record ends `skipped`; it is never `verified` (there is no `pending → verified`
transition).

```sql
UPDATE run_records r
SET state = 'skipped', reason = 'already committed in an earlier run'
FROM committed_keys k
WHERE r.run_id = $1 AND r.state = 'pending'
  AND k.tenant_id = r.tenant_id AND k.destination_key = $destination
  AND k.idempotency_key = r.idempotency_key;
```

## Re-queue a record after a person looks at it

A record that never reached a commit goes straight back to `pending`.

```sql
UPDATE run_records
SET state = 'pending', reason = $reason, claimed_by = NULL, claimed_at = NULL
WHERE id = $id AND state IN ('parked', 'failed') AND commit_attempted = false;
```

A commit-attempted record goes through `reconciling` first, so the destination is checked before any
retry. It reaches `pending` directly only when the same `UPDATE` records who checked the destination
(the guard rejects it otherwise).

```sql
-- preferred: reconcile first
UPDATE run_records
SET state = 'reconciling', reason = $reason
WHERE id = $id AND state IN ('parked', 'failed') AND commit_attempted = true;

-- a person confirmed the destination does not have it
UPDATE run_records
SET state = 'pending',
    reason = $reason,
    destination_checked_by = $user,
    destination_checked_at = now()
WHERE id = $id AND state IN ('parked', 'failed') AND commit_attempted = true;
```

## Dismiss a parked record as skipped

A person dismisses a parked record. A record that never reached a commit goes straight to `skipped`.
A commit-attempted record (it entered `submitting`) is only dismissed when the same `UPDATE` records a
fresh destination check, so a record that may already have saved is never reported as skipped without
someone confirming the destination does not have it. The same rule guards `prechecked → skipped`.

```sql
-- never commit-attempted
UPDATE run_records
SET state = 'skipped', reason = 'dismissed by a person'
WHERE id = $id AND state IN ('parked', 'prechecked') AND commit_attempted = false;

-- commit-attempted: a person confirmed the destination does not have it
UPDATE run_records
SET state = 'skipped',
    reason = 'dismissed by a person',
    destination_checked_by = $user,
    destination_checked_at = now()
WHERE id = $id AND state IN ('parked', 'prechecked') AND commit_attempted = true;
```

## Claim a background job

```sql
UPDATE jobs
SET status = 'running', locked_by = $1, locked_at = now(), attempts = attempts + 1
WHERE id = (
  SELECT id FROM jobs
  WHERE status = 'queued' AND run_after <= now() AND attempts < max_attempts
  ORDER BY run_after
  FOR UPDATE SKIP LOCKED
  LIMIT 1
)
RETURNING *;
```

## Create a run and its ledger rows

A run is born `queued` carrying its destination, approval policy and pace; its ledger rows are born
`pending` with an `idempotency_key` (the insert guard requires it). Creation refuses unless the recipe
version, the input batch and the workflow belong together under one tenant, and only input rows whose
`status = 'ready'` become ledger rows.

The two pacing columns (PL-080) are the person's choice on the run's start screen. `NULL` is unset,
which the runner reads as 0; the `runs_pacing_range` CHECK holds both to 0..3600.

```sql
INSERT INTO runs
  (tenant_id, workflow_id, recipe_version_id, input_batch_id, mode, status,
   destination_key, approval_policy, min_seconds_between_rows, min_seconds_between_steps)
VALUES ($tenant, $workflow, $recipe, $batch, $mode, 'queued', $destination, $policy,
        $rows_pace, $steps_pace)
RETURNING id;

INSERT INTO run_records
  (tenant_id, run_id, input_record_id, record_key, idempotency_key, state, max_attempts)
VALUES ($tenant, $run, $input, $record_key, $idempotency_key, 'pending', 3)
RETURNING id;
```

## Replace a tenant's daily limits per site

`tenants.settings` holds other keys besides `site_daily_caps`, so the caps are replaced with one
`jsonb_set` on that one path (PL-080): every other setting is left exactly as it was. The whole map is
written at once, because the Settings page saves one list -- a per-row write would leave caps the
person removed still standing. `create_if_missing` makes the first save work on a tenant that has
never had a cap, and `settings` is `NOT NULL`, so no separate insert is needed.

```sql
UPDATE tenants
SET settings = jsonb_set(settings, '{site_daily_caps}', $caps::jsonb, true)
WHERE id = $tenant;
```

## Open and resolve an approval request

The request records the **exact** values a person is shown; the decision points back at the request it
answered. The runner looks up a decision by `(run_record_id, step_id, shown)` before it opens a new
one, so a resumed run never re-asks, and an approval can never cover values the person did not see.

```sql
INSERT INTO approval_requests (tenant_id, run_id, run_record_id, step_id, kind, shown)
VALUES ($tenant, $run, $record, $step, $kind, $shown)
RETURNING id;

INSERT INTO approvals
  (tenant_id, run_id, run_record_id, step_id, kind, shown, decision, decided_by, request_id, reason)
SELECT tenant_id, run_id, run_record_id, step_id, kind, shown, $decision, $user, id, $reason
FROM approval_requests WHERE id = $request
RETURNING id;

UPDATE approval_requests SET resolved_by_approval_id = $approval
WHERE id = $request AND resolved_by_approval_id IS NULL;   -- 0 rows: already resolved
```

## Request a cancel, and resume a run

A cancel sets `runs.cancel_requested_at`; a paused run also goes back to `queued` so a worker picks it
up and finalises the cancel. The worker that holds the run reconciles the commit window, releases the
working records, ends the remaining `pending` records `skipped` with reason `run cancelled`, and sets
`status = 'cancelled'` — every one of those writes fenced by the lease check in the same transaction.

```sql
UPDATE runs
SET cancel_requested_at = now(),
    status = CASE WHEN status = 'paused' THEN 'queued' ELSE status END
WHERE id = $run AND status IN ('queued', 'running', 'paused');

UPDATE runs SET status = 'queued', pause_reason = NULL WHERE id = $run AND status = 'paused';

-- The worker's owns writes, after the lease check:
--   reconcile the window, release the working records,
UPDATE run_records SET state = 'skipped', reason = 'run cancelled'
WHERE run_id = $run AND state = 'pending';
UPDATE runs SET status = 'cancelled' WHERE id = $run;
```

## The Home screen's week, help list and task list (read-only)

Three read shapes for PL-023, all in ``packages/ledger/reads.py``. The week's counts come from the
append-only audit table, not from the run's own timestamps: a row is counted when it *entered*
``verified`` or ``skipped`` inside the rolling window.

```sql
-- the week's finished rows (the join to run_records is only the tenant filter)
SELECT count(*) FILTER (WHERE t.to_state = 'verified') AS done,
       count(*) FILTER (WHERE t.to_state = 'skipped')  AS already_there
FROM run_record_transitions t
JOIN run_records rr ON rr.id = t.run_record_id
WHERE t.at >= now() - make_interval(days => $1)
  AND ($2::uuid IS NULL OR rr.tenant_id = $2::uuid);

-- the wall clock the window's runs took (an unclaimed run falls back to its creation time)
SELECT coalesce(sum(extract(epoch FROM (coalesce(finished_at, now())
                                          - coalesce(started_at, created_at)))), 0)
FROM runs
WHERE coalesce(started_at, created_at) >= now() - make_interval(days => $1)
  AND ($2::uuid IS NULL OR tenant_id = $2::uuid);
```

The needs-help set is every record in ``parked`` or ``failed`` across all runs, newest first by the
audit row that put it in that state; the running set is every run in ``queued``/``running``/
``paused`` with its per-state counts; and the task list is the latest version of each non-archived
workflow with its newest run. Their SQL is in the same module, next to these two.

## Create an input batch and its rows

One upload is one batch and one row per spreadsheet row, written together or not at all: the
caller opens the transaction. ``row_count`` is the number of rows given, and every row is born
``ready`` with a ``record_key`` (``0001_init``'s check requires one for a ready row) in the person's
row order, ``raw`` exactly as uploaded and ``clean`` the values a run uses. The check that follows
an upload moves the rows it cannot accept to ``needs_review`` with a plain reason each
(``set_row_verdict``), and the mapping a person confirms is stored on the batch
(``input_batches.column_mapping``) -- never on the recipe. ``packages/ledger/batches.py`` owns these
statements, including ``get_batch`` and ``list_batch_rows`` (rows come back in ``row_index`` order).

```sql
INSERT INTO input_batches
  (tenant_id, workflow_id, file_id, kind, row_count, created_by, column_mapping)
VALUES ($tenant, $workflow, $file, $kind, $row_count, $created_by, $mapping)
RETURNING id;

INSERT INTO input_records
  (tenant_id, batch_id, row_index, raw, clean, record_key, status, issues)
VALUES ($tenant, $batch, $row_index, $raw, $clean, $record_key, $status, $issues);
```

## Rules

- Never `UPDATE` or `DELETE` on `recipe_versions`, `committed_keys`, `step_events`, `ai_calls`, `approvals`, `run_record_transitions`. Triggers will reject it anyway.
- A record that may already have saved (`commit_attempted`) is never dismissed as `skipped` — `filling → skipped` is refused, and `parked → skipped` needs a fresh destination check in the same update.
- Never `DELETE` or `TRUNCATE` `run_records`; a row is inserted only as `pending` with an `idempotency_key`.
- Never set `attempts` or `max_attempts` from application code. `attempts` moves only on `pending → claimed`; `max_attempts` is fixed at insert.
- Never set `commit_attempted` from application code; the guard sets it when a record enters `submitting`.
- Every state change is conditional on the expected current state, the claiming worker, and a live `lease_epoch`. Where a statement cannot express that in its own `WHERE` clause — the record claim, and the bulk recovery, close-out, skip and release writes — it runs the lease check (`require_lease` in `packages/ledger`) first, in the same transaction, and raises `StaleLease` on zero rows.
- A state change and its `step_events` row are written in the same transaction; the `run_record_transitions` row is written by the database.
- Always set `reason` in a state change. The audit row records `NEW.reason`, so an update that omits it carries the previous transition's reason.
- One active run per tenant and destination; a takeover increments `lease_epoch` before recovery runs.
- Every one of these patterns has a concurrency test with two workers racing.
