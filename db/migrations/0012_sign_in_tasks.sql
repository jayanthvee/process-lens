-- PL-112 item 4: a task taught through a page that asks for an emailed code is a sign-in task.
--
-- The marker belongs to the task, not to a run: a sign-in task is run from one picked email
-- instead of a spreadsheet. The column the email was typed into is stored beside it so the
-- one-row run can put the value where the recording expects it.
--
-- `runs.pause_detail` carries the extension's own reason when a code read failed (`none`,
-- `ambiguous`, `not_allowed`, ...). `pause_reason` stays the stable code (`code_required`) that
-- every existing screen already reads; the detail is only what the row says in plain words.

BEGIN;

ALTER TABLE workflows
  ADD COLUMN sign_in boolean NOT NULL DEFAULT false,
  ADD COLUMN sign_in_column text;

ALTER TABLE runs
  ADD COLUMN pause_detail text;

COMMIT;
