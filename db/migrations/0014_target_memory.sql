-- PL-122: the element a run-time model call found, remembered per task, step and site.
--
-- The record is the ladder that worked, not a change to the recipe: a recipe is immutable, and this
-- table is only read before `find_target` on the next run. One row per step per origin, updated in
-- place; `hit_count` is how many times the remembered element has been used since it was stored.
--
-- The key is (tenant, workflow, step_id, origin). `origin` is the page the element was found on, so
-- the same step on two sites remembers two different elements.

BEGIN;

CREATE TABLE target_memory (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id    uuid NOT NULL REFERENCES tenants(id),
  workflow_id  uuid NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
  step_id      text NOT NULL,
  origin       text NOT NULL,
  ladder       jsonb NOT NULL CHECK (jsonb_typeof(ladder) = 'array'),
  hit_count    int NOT NULL DEFAULT 1 CHECK (hit_count > 0),
  last_used_at timestamptz NOT NULL DEFAULT now(),
  created_at   timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, workflow_id, step_id, origin)
);

COMMIT;
