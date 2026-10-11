# SQL patterns

Read this before writing any query that touches `runs`, `run_records`, `committed_keys`, or `jobs`.
These are the only approved shapes for those operations. If you need a new one, add it here first.

## Claim the next queued run (run worker)

```sql
UPDATE runs
SET status = 'running', worker_id = $1, started_at = coalesce(started_at, now()), heartbeat_at = now()
WHERE id = (
  SELECT id FROM runs
  WHERE status = 'queued'
  ORDER BY created_at
  FOR UPDATE SKIP LOCKED
  LIMIT 1
)
RETURNING *;
```

## Claim the next record in a run

`attempts` is incremented by the database trigger, never by application code.

```sql
WITH next AS (
  SELECT id FROM run_records
  WHERE run_id = $1 AND state = 'pending' AND attempts < max_attempts
  ORDER BY created_at, id
  FOR UPDATE SKIP LOCKED
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
  AND EXISTS (SELECT 1 FROM runs WHERE id = run_records.run_id AND lease_epoch = $epoch);
```

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

Records that might have been submitted go to `reconciling`. Records that never reached a commit go back to `pending`.

```sql
UPDATE run_records SET state = 'reconciling'
WHERE run_id = $1 AND state IN ('submitting', 'submitted_unverified');

UPDATE run_records SET state = 'pending', claimed_by = NULL, claimed_at = NULL
WHERE run_id = $1 AND state IN ('claimed', 'prechecked', 'filling');
```

Then process every `reconciling` record first: search the destination by record key.
Found → `verified` (plus `committed_keys`). Not found → `pending`. Ambiguous → `parked`.

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

## Rules

- Never `UPDATE` or `DELETE` on `recipe_versions`, `committed_keys`, `step_events`, `ai_calls`, `approvals`, `run_record_transitions`. Triggers will reject it anyway.
- Never `DELETE` or `TRUNCATE` `run_records`; a row is inserted only as `pending` with an `idempotency_key`.
- Never set `attempts` or `max_attempts` from application code. `attempts` moves only on `pending → claimed`; `max_attempts` is fixed at insert.
- Never set `commit_attempted` from application code; the guard sets it when a record enters `submitting`.
- Every state change is conditional on the expected current state, the claiming worker, and a live `lease_epoch`.
- A state change and its `step_events` row are written in the same transaction; the `run_record_transitions` row is written by the database.
- One active run per tenant and destination; a takeover increments `lease_epoch` before recovery runs.
- Every one of these patterns has a concurrency test with two workers racing.
