-- ProcessLens schema v2: ledger hardening.
--
-- Makes the database enforce the ledger rules that 0001 left to code review, before the runner
-- writes to the ledger. Each gap it closes was confirmed on PostgreSQL 16 against 0001 alone:
--   G1  a commit-attempted record could be re-clicked with no reconcile (a duplicate)
--   G2  a run_records row could be inserted directly as 'verified', or a 'submitting' row deleted
--   G3  attempts and max_attempts were writable by application code
--   G4  parked -> skipped was rejected, so a dismissed record stayed parked forever
--   G5  TRUNCATE emptied the append-only tables; the row triggers do not fire on TRUNCATE
--   G6  a state change wrote no audit row, including the bulk recovery updates
--   G7  two runs on one destination could both be active and both verify the same key
--   G8  the conditional state change did not check the owner or a lease
--   G9  run_records had no idempotency key, so the skip pattern could not work for a batch
--   G10 no table recorded who approved a commit
--
-- 0001_init.sql is not modified. This file is append-only history: corrections are new files.

BEGIN;

-- ---------------------------------------------------------------------------
-- a + e. run_records: commit-attempted flag, destination check, idempotency key (G1, G9)
-- ---------------------------------------------------------------------------

ALTER TABLE run_records ADD COLUMN commit_attempted boolean NOT NULL DEFAULT false;
ALTER TABLE run_records ADD COLUMN destination_checked_by uuid REFERENCES users(id);
ALTER TABLE run_records ADD COLUMN destination_checked_at timestamptz;

-- Nullable so existing development rows survive; the insert guard requires it on every new row.
ALTER TABLE run_records ADD COLUMN idempotency_key text;
CREATE INDEX run_records_idempotency_idx ON run_records (tenant_id, idempotency_key);

-- ---------------------------------------------------------------------------
-- f. runs: destination key and lease epoch (G7, G8)
-- ---------------------------------------------------------------------------

ALTER TABLE runs ADD COLUMN destination_key text;              -- copied from the workflow at creation
ALTER TABLE runs ADD COLUMN lease_epoch integer NOT NULL DEFAULT 0;  -- incremented on every takeover

-- One active run per tenant and destination. Existing rows may carry a NULL destination_key, and
-- NULLs are distinct in a unique index, so they do not collide.
CREATE UNIQUE INDEX runs_one_active_per_destination
  ON runs (tenant_id, destination_key)
  WHERE status IN ('running', 'paused');

-- ---------------------------------------------------------------------------
-- b. Replace the transition guard (G1, G3, G4)
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
  -- when the requeue that uses them sets both non-null with a fresh timestamp (H3).
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
    THEN
      NULL;  -- a person's check, recorded in the requeue itself (H3)
    ELSE
      RAISE EXCEPTION
        'destination_checked_* change only by clearing on entering submitting or by a '
        'failed|parked -> pending requeue that sets both (run_record %)', OLD.id;
    END IF;
  END IF;

  IF NEW.state = OLD.state THEN
    RETURN NEW;
  END IF;

  -- The 31 legal transitions: the 28 from 0001, plus parked -> skipped (G4),
  -- failed -> reconciling and parked -> reconciling (G1).
  IF NOT (OLD.state::text || '>' || NEW.state::text = ANY (ARRAY[
    'pending>claimed', 'pending>skipped', 'pending>failed',
    'claimed>prechecked', 'claimed>pending', 'claimed>parked', 'claimed>failed',
    'prechecked>filling', 'prechecked>skipped', 'prechecked>parked', 'prechecked>pending',
    'filling>submitting', 'filling>verified', 'filling>parked', 'filling>failed', 'filling>pending',
    'submitting>submitted_unverified', 'submitting>reconciling', 'submitting>failed',
    'submitted_unverified>verified', 'submitted_unverified>failed', 'submitted_unverified>reconciling',
    'reconciling>verified', 'reconciling>pending', 'reconciling>parked', 'reconciling>failed',
    'parked>pending', 'parked>reconciling', 'parked>skipped',
    'failed>pending', 'failed>reconciling'
  ])) THEN
    RAISE EXCEPTION 'illegal ledger transition % -> % (run_record %)', OLD.state, NEW.state, OLD.id;
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

-- Fire on every locked column, so a standalone write to commit_attempted or destination_checked_*
-- is caught by the guard above (H1, H2, H3).
DROP TRIGGER run_records_guard ON run_records;
CREATE TRIGGER run_records_guard
  BEFORE UPDATE OF state, attempts, max_attempts, commit_attempted, destination_checked_by,
    destination_checked_at
  ON run_records
  FOR EACH ROW EXECUTE FUNCTION guard_record_transition();

-- ---------------------------------------------------------------------------
-- c. Insert and delete guards (G2)
-- ---------------------------------------------------------------------------

-- A ledger row is born pending, with no attempts, not commit-attempted, and with an idempotency
-- key. Outcomes cannot be forged by inserting a row directly as 'verified'.
CREATE FUNCTION guard_run_record_insert() RETURNS trigger AS $$
BEGIN
  IF NEW.state <> 'pending' THEN
    RAISE EXCEPTION 'a run_records row is inserted only as pending (got %)', NEW.state;
  END IF;
  IF NEW.attempts <> 0 THEN
    RAISE EXCEPTION 'a run_records row starts with attempts = 0 (got %)', NEW.attempts;
  END IF;
  IF NEW.commit_attempted THEN
    RAISE EXCEPTION 'a run_records row starts with commit_attempted = false';
  END IF;
  IF NEW.idempotency_key IS NULL OR NEW.idempotency_key = '' THEN
    RAISE EXCEPTION 'a run_records row requires a non-empty idempotency_key';
  END IF;
  IF NEW.record_key IS NULL OR NEW.record_key = '' THEN
    RAISE EXCEPTION 'a run_records row requires a non-empty record_key';
  END IF;
  RETURN NEW;
END $$ LANGUAGE plpgsql;

CREATE TRIGGER run_records_insert_guard
  BEFORE INSERT ON run_records
  FOR EACH ROW EXECUTE FUNCTION guard_run_record_insert();

CREATE FUNCTION forbid_run_record_delete() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'run_records is append-only: DELETE is not allowed';
END $$ LANGUAGE plpgsql;

CREATE TRIGGER run_records_no_delete
  BEFORE DELETE ON run_records
  FOR EACH ROW EXECUTE FUNCTION forbid_run_record_delete();

-- runs carry their destination from the moment they exist, and it never changes (G7, H9).
CREATE FUNCTION require_run_destination_key() RETURNS trigger AS $$
BEGIN
  IF NEW.destination_key IS NULL THEN
    RAISE EXCEPTION 'runs.destination_key is required (copied from the workflow)';
  END IF;
  RETURN NEW;
END $$ LANGUAGE plpgsql;

CREATE TRIGGER runs_destination_key_guard
  BEFORE INSERT ON runs
  FOR EACH ROW EXECUTE FUNCTION require_run_destination_key();

-- A run cannot move to another destination: it would slip out from under
-- runs_one_active_per_destination and open a second active run (H9).
CREATE FUNCTION forbid_destination_key_change() RETURNS trigger AS $$
BEGIN
  IF NEW.destination_key IS DISTINCT FROM OLD.destination_key THEN
    RAISE EXCEPTION 'runs.destination_key is set at insert and never changed (run %)', OLD.id;
  END IF;
  RETURN NEW;
END $$ LANGUAGE plpgsql;

CREATE TRIGGER runs_destination_key_immutable
  BEFORE UPDATE OF destination_key ON runs
  FOR EACH ROW EXECUTE FUNCTION forbid_destination_key_change();

-- ---------------------------------------------------------------------------
-- d. Transition audit (G6)
-- ---------------------------------------------------------------------------

CREATE TABLE run_record_transitions (
  id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  tenant_id     uuid NOT NULL REFERENCES tenants(id),
  run_record_id uuid NOT NULL REFERENCES run_records(id),
  from_state    record_state NOT NULL,
  to_state      record_state NOT NULL,
  reason        text,
  actor         text,                             -- the worker, or the session actor
  at            timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX run_record_transitions_record_idx ON run_record_transitions (run_record_id, id);

-- The audit trail is append-only: a transition row is never edited or removed (H4).
CREATE TRIGGER run_record_transitions_append_only
  BEFORE UPDATE OR DELETE ON run_record_transitions
  FOR EACH ROW EXECUTE FUNCTION forbid_mutation();

-- One row per actual change, including changes made by a bulk UPDATE (G6).
CREATE FUNCTION record_transition_audit() RETURNS trigger AS $$
BEGIN
  IF NEW.state IS DISTINCT FROM OLD.state THEN
    INSERT INTO run_record_transitions
      (tenant_id, run_record_id, from_state, to_state, reason, actor)
    VALUES
      (NEW.tenant_id, NEW.id, OLD.state, NEW.state, NEW.reason,
       COALESCE(NEW.claimed_by, current_setting('processlens.actor', true)));
  END IF;
  RETURN NULL;
END $$ LANGUAGE plpgsql;

CREATE TRIGGER run_records_transition_audit
  AFTER UPDATE OF state ON run_records
  FOR EACH ROW EXECUTE FUNCTION record_transition_audit();

-- ---------------------------------------------------------------------------
-- g. Approvals (G10)
-- ---------------------------------------------------------------------------

CREATE TYPE approval_kind     AS ENUM ('first_commit', 'commit', 'ai_value', 'repair', 'requeue');
CREATE TYPE approval_decision AS ENUM ('approved', 'rejected');

-- Who approved what: the exact values the person saw, their decision, and who made it.
CREATE TABLE approvals (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     uuid NOT NULL REFERENCES tenants(id),
  run_id        uuid NOT NULL REFERENCES runs(id),
  run_record_id uuid REFERENCES run_records(id),     -- null for a run-level approval
  step_id       text,
  kind          approval_kind NOT NULL,
  shown         jsonb NOT NULL DEFAULT '{}',         -- the exact values the person saw
  decision      approval_decision NOT NULL,
  decided_by    uuid NOT NULL REFERENCES users(id),
  decided_at    timestamptz NOT NULL DEFAULT now()
);
CREATE TRIGGER approvals_append_only
  BEFORE UPDATE OR DELETE ON approvals
  FOR EACH ROW EXECUTE FUNCTION forbid_mutation();

-- ---------------------------------------------------------------------------
-- h. TRUNCATE protection (G5)
-- ---------------------------------------------------------------------------

-- A row-level trigger does not fire on TRUNCATE; a statement-level one does.
CREATE FUNCTION forbid_truncate() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION '% is append-only: TRUNCATE is not allowed', TG_TABLE_NAME;
END $$ LANGUAGE plpgsql;

CREATE TRIGGER recipe_versions_no_truncate       BEFORE TRUNCATE ON recipe_versions       FOR EACH STATEMENT EXECUTE FUNCTION forbid_truncate();
CREATE TRIGGER committed_keys_no_truncate        BEFORE TRUNCATE ON committed_keys        FOR EACH STATEMENT EXECUTE FUNCTION forbid_truncate();
CREATE TRIGGER step_events_no_truncate           BEFORE TRUNCATE ON step_events           FOR EACH STATEMENT EXECUTE FUNCTION forbid_truncate();
CREATE TRIGGER ai_calls_no_truncate              BEFORE TRUNCATE ON ai_calls              FOR EACH STATEMENT EXECUTE FUNCTION forbid_truncate();
CREATE TRIGGER run_record_transitions_no_truncate BEFORE TRUNCATE ON run_record_transitions FOR EACH STATEMENT EXECUTE FUNCTION forbid_truncate();
CREATE TRIGGER approvals_no_truncate             BEFORE TRUNCATE ON approvals            FOR EACH STATEMENT EXECUTE FUNCTION forbid_truncate();
CREATE TRIGGER run_records_no_truncate           BEFORE TRUNCATE ON run_records           FOR EACH STATEMENT EXECUTE FUNCTION forbid_truncate();

COMMIT;
