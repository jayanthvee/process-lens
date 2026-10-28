-- PL-120: a task can be removed from its lists without deleting anything.
--
-- ``removed_at`` is a marker, not a delete: the task's recipe versions, runs, records,
-- ``committed_keys`` and step events stay exactly as they were. Every task list skips a workflow
-- with a stamp; ``POST /api/v1/tasks/{id}/restore`` clears it, so an accidental removal loses
-- nothing.

BEGIN;

ALTER TABLE workflows
  ADD COLUMN removed_at timestamptz;

COMMIT;
