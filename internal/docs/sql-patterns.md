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

Check the affected row count. Zero rows means someone else moved the record; stop and re-read.

```sql
UPDATE run_records
SET state = $new_state, reason = $reason, last_error = $error
WHERE id = $id AND state = $expected_state;
```

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

INSERT INTO committed_keys (tenant_id, destination_key, idempotency_key, workflow_id, run_record_id, destination_ref)
VALUES ($tenant, $destination, $idempotency_key, $workflow, $id, $ref)
ON CONFLICT (tenant_id, destination_key, idempotency_key) DO NOTHING;

INSERT INTO step_events (tenant_id, run_record_id, step_id, attempt, started_at, ended_at, outcome, locator_rung, observed)
VALUES (...);
COMMIT;
```

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

```sql
UPDATE run_records r
SET state = 'skipped', reason = 'already committed in an earlier run'
FROM committed_keys k
WHERE r.run_id = $1 AND r.state = 'pending'
  AND k.tenant_id = r.tenant_id AND k.destination_key = $destination
  AND k.idempotency_key = $key_expression_for_r;   -- computed by the app from the recipe template
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

- Never `UPDATE` or `DELETE` on `recipe_versions`, `committed_keys`, `step_events`, `ai_calls`. Triggers will reject it anyway.
- Never set `attempts` from application code.
- Every state change is conditional on the expected current state.
- A state change and its `step_events` row are written in the same transaction.
- Every one of these patterns has a concurrency test with two workers racing.
