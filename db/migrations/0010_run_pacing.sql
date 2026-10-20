-- PL-067: runs go at a person's pace, and stay under a daily cap per site.
--
-- The pacing settings ride the run row: two nullable integers, each limited to
-- 0..3600 seconds. NULL means "not set", which the runner reads as 0 -- today's
-- behaviour. The ledger guard is untouched: these columns carry no state the
-- state machine reads.
--
-- The site daily caps ride the tenant: `site_daily_caps` is a JSON object of
-- {origin: number} inside the tenant's settings. A tenant had no settings
-- column, so this migration adds one.

ALTER TABLE runs
  ADD COLUMN min_seconds_between_rows smallint,
  ADD COLUMN min_seconds_between_steps smallint;

ALTER TABLE runs ADD CONSTRAINT runs_pacing_range CHECK (
  (min_seconds_between_rows IS NULL OR min_seconds_between_rows BETWEEN 0 AND 3600)
  AND (min_seconds_between_steps IS NULL OR min_seconds_between_steps BETWEEN 0 AND 3600)
);

ALTER TABLE tenants ADD COLUMN settings jsonb NOT NULL DEFAULT '{}'::jsonb;
