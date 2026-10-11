-- ProcessLens schema v3: run orchestration.
--
-- Adds what the run orchestrator (services/runner) needs on top of 0002, before it writes to the
-- ledger:
--   R1  a duplicate found by the recipe's own browser search is found while the record is `filling`,
--       and the guard had no exit to `skipped` from there. `filling -> skipped` is added, allowed
--       only when `commit_attempted = false`: a record whose save may already have reached the
--       destination is never reported as skipped. This is the 32nd legal transition.
--   R3  `approval_requests` records the exact values a person was shown, and `approvals.request_id`
--       points a decision at the request it answered, so a re-run cannot ask twice for the same
--       values and an approval can never cover values the person did not see.
--   R4  `runs.cancel_requested_at` lets the API ask a run to stop; the worker that holds the run
--       finalises the cancel at a step boundary, after reconciling any commit-window record.
--   PL-012 follow-up 1: the re-queue that sets `destination_checked_*` requires a fresh
--       `destination_checked_at` (within the last 5 minutes and not in the future), so a check
--       cannot be backdated to open a retry long after it was made.
--
-- 0001_init.sql and 0002_ledger_hardening.sql are not modified. This file is append-only history.

BEGIN;

-- ---------------------------------------------------------------------------
-- R4. runs: a requested cancel the worker notices at the next step boundary
-- ---------------------------------------------------------------------------

ALTER TABLE runs ADD COLUMN cancel_requested_at timestamptz;

-- ---------------------------------------------------------------------------
-- R3. approval_requests: the exact values a person was shown (G10 continues)
-- ---------------------------------------------------------------------------

-- Created before `approvals.request_id` so the two foreign keys can point at each other.
CREATE TABLE approval_requests (
  id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id               uuid NOT NULL REFERENCES tenants(id),
  run_id                  uuid NOT NULL REFERENCES runs(id),
  run_record_id           uuid NOT NULL REFERENCES run_records(id),
  step_id                 text NOT NULL,
  kind                    approval_kind NOT NULL,
  shown                   jsonb NOT NULL DEFAULT '{}',  -- the exact values the person saw
  created_at              timestamptz NOT NULL DEFAULT now(),
  resolved_by_approval_id uuid REFERENCES approvals(id)  -- the decision that resolved it, once
);
CREATE INDEX approval_requests_open_idx
  ON approval_requests (run_id, run_record_id, step_id)
  WHERE resolved_by_approval_id IS NULL;

-- A decision points at the exact request and values it approved.
ALTER TABLE approvals ADD COLUMN request_id uuid REFERENCES approval_requests(id);

-- The only update allowed is setting `resolved_by_approval_id` from NULL to non-NULL, once. Every
-- other change, a second resolve, a DELETE, and a TRUNCATE are rejected.
CREATE FUNCTION guard_approval_request_update() RETURNS trigger AS $$
BEGIN
  IF NEW.id IS DISTINCT FROM OLD.id
     OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
     OR NEW.run_id IS DISTINCT FROM OLD.run_id
     OR NEW.run_record_id IS DISTINCT FROM OLD.run_record_id
     OR NEW.step_id IS DISTINCT FROM OLD.step_id
     OR NEW.kind IS DISTINCT FROM OLD.kind
     OR NEW.shown IS DISTINCT FROM OLD.shown
     OR NEW.created_at IS DISTINCT FROM OLD.created_at
  THEN
    RAISE EXCEPTION
      'approval_requests is immutable except for a one-time resolve (request %)', OLD.id;
  END IF;

  IF NEW.resolved_by_approval_id IS DISTINCT FROM OLD.resolved_by_approval_id
     AND NOT (OLD.resolved_by_approval_id IS NULL AND NEW.resolved_by_approval_id IS NOT NULL)
  THEN
    RAISE EXCEPTION
      'approval_requests is immutable except for a one-time resolve (request %)', OLD.id;
  END IF;

  IF OLD.resolved_by_approval_id IS NOT NULL THEN
    RAISE EXCEPTION 'approval request % is already resolved', OLD.id;
  END IF;

  IF NEW.resolved_by_approval_id IS NULL THEN
    RAISE EXCEPTION
      'the only allowed update to approval_requests sets resolved_by_approval_id (request %)', OLD.id;
  END IF;

  RETURN NEW;
END $$ LANGUAGE plpgsql;

CREATE TRIGGER approval_requests_resolve_once
  BEFORE UPDATE ON approval_requests
  FOR EACH ROW EXECUTE FUNCTION guard_approval_request_update();

CREATE TRIGGER approval_requests_no_delete
  BEFORE DELETE ON approval_requests
  FOR EACH ROW EXECUTE FUNCTION forbid_mutation();

CREATE TRIGGER approval_requests_no_truncate
  BEFORE TRUNCATE ON approval_requests
  FOR EACH STATEMENT EXECUTE FUNCTION forbid_truncate();

-- ---------------------------------------------------------------------------
-- R1 + PL-012 follow-up 1. Replace the transition guard
--
-- The whole function is replaced, exactly as 0002 did to 0001: only the changed clauses differ.
--   * `filling > skipped` joins the legal set, with the `commit_attempted = false` condition.
--   * the re-queue that sets `destination_checked_*` now requires a fresh, non-future check.
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION guard_record_transition() RETURNS trigger AS $$
BEGIN
  -- max_attempts is fixed at insert (G3).
  IF NEW.max_attempts <> OLD.max_attempts THEN
    RAISE EXCEPTION 'max_attempts is set at insert and never changed (run_record %)', OLD.id;
  END IF;

  -- attempts moves only by the trigger's own increment on pending -> claimed (G3).
  IF NEW.attempts <> OLD.attempts
     AND NOT (OLD.state = 'pending' AND NEW.state = 'claimed' AND NEW.attempts = OLD.attempts + 1)
  THEN
    RAISE EXCEPTION 'attempts is written only by the trigger (run_record %: % -> %)',
      OLD.id, OLD.attempts, NEW.attempts;
  END IF;

  -- Entering the commit window marks the record commit-attempted and clears any earlier destination
  -- check, so a person's check of one commit never carries over to the next one (H2).
  IF NEW.state = 'submitting' AND OLD.state <> 'submitting' THEN
    NEW.commit_attempted := true;
    NEW.destination_checked_by := NULL;
    NEW.destination_checked_at := NULL;
  END IF;

  -- commit_attempted never changes back: only false -> true, and only on entering submitting (H1).
  IF NEW.commit_attempted IS DISTINCT FROM OLD.commit_attempted
     AND NOT (NEW.state = 'submitting' AND OLD.commit_attempted = false)
  THEN
    RAISE EXCEPTION
      'commit_attempted changes only false -> true on entering submitting (run_record %)', OLD.id;
  END IF;

  -- destination_checked_* change only when the guard clears them on entering the commit window, or
  -- when the requeue that uses them sets both non-null with a FRESH timestamp (H3, PL-012 follow-up 1).
  IF NEW.destination_checked_by IS DISTINCT FROM OLD.destination_checked_by
     OR NEW.destination_checked_at IS DISTINCT FROM OLD.destination_checked_at
  THEN
    IF NEW.state = 'submitting'
       AND NEW.destination_checked_by IS NULL
       AND NEW.destination_checked_at IS NULL
    THEN
      NULL;  -- cleared on entering the commit window (H2)
    ELSIF OLD.state IN ('failed', 'parked')
          AND NEW.state = 'pending'
          AND NEW.destination_checked_by IS NOT NULL
          AND NEW.destination_checked_at IS NOT NULL
          AND NEW.destination_checked_at IS DISTINCT FROM OLD.destination_checked_at
          AND NEW.destination_checked_at >= now() - interval '5 minutes'
          AND NEW.destination_checked_at <= now()
    THEN
      NULL;  -- a fresh person's check, recorded in the requeue itself (H3 + follow-up 1)
    ELSE
      RAISE EXCEPTION
        'destination_checked_* change only by clearing on entering submitting or by a '
        'failed|parked -> pending requeue that sets both with a fresh timestamp (run_record %)',
        OLD.id;
    END IF;
  END IF;

  IF NEW.state = OLD.state THEN
    RETURN NEW;
  END IF;

  -- The 32 legal transitions: the 31 from 0002, plus filling -> skipped (R1).
  IF NOT (OLD.state::text || '>' || NEW.state::text = ANY (ARRAY[
    'pending>claimed', 'pending>skipped', 'pending>failed',
    'claimed>prechecked', 'claimed>pending', 'claimed>parked', 'claimed>failed',
    'prechecked>filling', 'prechecked>skipped', 'prechecked>parked', 'prechecked>pending',
    'filling>submitting', 'filling>verified', 'filling>parked', 'filling>failed', 'filling>pending',
    'filling>skipped',
    'submitting>submitted_unverified', 'submitting>reconciling', 'submitting>failed',
    'submitted_unverified>verified', 'submitted_unverified>failed', 'submitted_unverified>reconciling',
    'reconciling>verified', 'reconciling>pending', 'reconciling>parked', 'reconciling>failed',
    'parked>pending', 'parked>reconciling', 'parked>skipped',
    'failed>pending', 'failed>reconciling'
  ])) THEN
    RAISE EXCEPTION 'illegal ledger transition % -> % (run_record %)', OLD.state, NEW.state, OLD.id;
  END IF;

  -- R1: `filling -> skipped` says "the destination already has this record". A record that has ever
  -- entered the commit window may already have saved, so it is never reported as skipped.
  IF OLD.state = 'filling' AND NEW.state = 'skipped' AND OLD.commit_attempted THEN
    RAISE EXCEPTION
      'run_record % was commit-attempted; it cannot end skipped from filling', OLD.id;
  END IF;

  -- A record whose save may already have reached the destination never returns to pending without a
  -- person confirming the destination does not have it. Its retry path is through reconciling (G1).
  IF OLD.commit_attempted
     AND NEW.state = 'pending'
     AND OLD.state IN ('failed', 'parked')
     AND NEW.destination_checked_by IS NULL
  THEN
    RAISE EXCEPTION
      'run_record % was commit-attempted; re-queue to pending requires destination_checked_by',
      OLD.id;
  END IF;

  IF OLD.state = 'pending' AND NEW.state = 'claimed' THEN
    NEW.attempts := OLD.attempts + 1;
  END IF;

  RETURN NEW;
END $$ LANGUAGE plpgsql;

COMMIT;
