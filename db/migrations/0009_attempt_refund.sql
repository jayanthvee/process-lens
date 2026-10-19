-- 0009: a release because the run paused gives the claim's attempt back.
--
-- Until now every return to `pending` cost an attempt on the next claim, so a row released three
-- times by pauses (Chrome reconnecting, the window hidden, an admin freeze) failed as "attempts
-- exhausted" without ever having run. The runner now sets `processlens.refund_attempt = 'on'`
-- (transaction-local) on exactly those releases, and the guard decrements `attempts` on that update.
-- The whole function is replaced, as 0002 and 0003 did; only the two commented clauses differ from
-- 0003. Application code still cannot write `attempts` itself.

BEGIN;

CREATE OR REPLACE FUNCTION guard_record_transition() RETURNS trigger AS $$
BEGIN
  -- max_attempts is fixed at insert (G3).
  IF NEW.max_attempts <> OLD.max_attempts THEN
    RAISE EXCEPTION 'max_attempts is set at insert and never changed (run_record %)', OLD.id;
  END IF;

  -- attempts moves only by the trigger's own increment on pending -> claimed (G3).
  -- 0009: and by its refund on a marked release (below), which this check runs before.
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
    ELSIF NEW.destination_checked_by IS NOT NULL
          AND NEW.destination_checked_at IS NOT NULL
          AND NEW.destination_checked_at IS DISTINCT FROM OLD.destination_checked_at
          AND NEW.destination_checked_at >= now() - interval '5 minutes'
          AND NEW.destination_checked_at <= now()
          AND (
            (OLD.state IN ('failed', 'parked') AND NEW.state = 'pending')
            OR (OLD.state IN ('parked', 'prechecked') AND NEW.state = 'skipped')
          )
    THEN
      NULL;  -- a fresh check, recorded by the requeue or the dismissal itself (item 2)
    ELSE
      RAISE EXCEPTION
        'destination_checked_* change only by clearing on entering submitting, or by a '
        'failed|parked -> pending or parked|prechecked -> skipped update that sets both with a '
        'fresh timestamp (run_record %)',
        OLD.id;
    END IF;
  END IF;

  IF NEW.state = OLD.state THEN
    RETURN NEW;
  END IF;

  -- The 32 legal transitions, unchanged from 0003.
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

  -- R1 + review item 2: `-> skipped` says "the destination already has this record" (or a person
  -- dismissed it). A record that may already have saved is never reported as skipped without a fresh
  -- destination check recorded in the same update. `filling -> skipped` is refused outright (a search
  -- that finds a duplicate must park, not skip, once the record entered the commit window).
  IF OLD.state = 'filling' AND NEW.state = 'skipped' AND OLD.commit_attempted THEN
    RAISE EXCEPTION
      'run_record % was commit-attempted; it cannot end skipped from filling', OLD.id;
  END IF;

  IF OLD.state IN ('parked', 'prechecked') AND NEW.state = 'skipped' AND OLD.commit_attempted
     AND NOT (NEW.destination_checked_by IS NOT NULL
              AND NEW.destination_checked_at IS NOT NULL
              AND NEW.destination_checked_at >= now() - interval '5 minutes'
              AND NEW.destination_checked_at <= now())
  THEN
    RAISE EXCEPTION
      'run_record % was commit-attempted; % -> skipped requires a fresh destination check',
      OLD.id, OLD.state;
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

  -- 0009: a record released back to pending because the run paused (a pause, a lost connection, a
  -- hidden window, an admin freeze) never failed, so the claim it is released from does not count.
  -- The runner marks only those releases, with a transaction-local setting; crash recovery does not
  -- mark its releases, so a row that keeps killing a worker still runs out of attempts.
  IF OLD.state IN ('claimed', 'prechecked', 'filling') AND NEW.state = 'pending'
     AND OLD.attempts > 0
     AND coalesce(current_setting('processlens.refund_attempt', true), '') = 'on'
  THEN
    NEW.attempts := OLD.attempts - 1;
  END IF;

  RETURN NEW;
END $$ LANGUAGE plpgsql;

COMMIT;
