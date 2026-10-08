-- ProcessLens schema v1 (PostgreSQL 15+)
--
-- Four contracts this schema enforces in the database itself, not just in app code:
--   1. Recipes are immutable versions (recipe_versions rejects UPDATE/DELETE).
--   2. run_records is the ledger; only legal state transitions are accepted (guard trigger).
--   3. committed_keys is the cross-run duplicate guard (unique per destination + idempotency key).
--   4. step_events and ai_calls are append-only audit logs.

BEGIN;

-- ---------------------------------------------------------------------------
-- Enums
-- ---------------------------------------------------------------------------
CREATE TYPE user_role       AS ENUM ('owner', 'builder', 'reviewer', 'viewer');
CREATE TYPE batch_kind      AS ENUM ('csv', 'xlsx', 'documents', 'manual');
CREATE TYPE input_status    AS ENUM ('needs_review', 'ready', 'excluded');
CREATE TYPE value_source    AS ENUM ('code', 'mapping', 'ai', 'human');
CREATE TYPE proposal_status AS ENUM ('proposed', 'accepted', 'rejected', 'edited');
CREATE TYPE run_mode        AS ENUM ('preview', 'batch');
CREATE TYPE run_status      AS ENUM ('queued', 'running', 'paused', 'completed', 'cancelled', 'failed');
CREATE TYPE record_state    AS ENUM (
  'pending', 'claimed', 'prechecked', 'filling',
  'submitting', 'submitted_unverified', 'reconciling',
  'verified', 'skipped', 'parked', 'failed'
);
CREATE TYPE step_outcome    AS ENUM ('ok', 'retried', 'failed', 'skipped');
CREATE TYPE heal_source     AS ENUM ('fuzzy', 'ai');
CREATE TYPE heal_status     AS ENUM ('pending', 'approved', 'rejected');
CREATE TYPE ai_validation   AS ENUM ('pass', 'partial', 'fail');
CREATE TYPE job_status      AS ENUM ('queued', 'running', 'done', 'failed');

-- ---------------------------------------------------------------------------
-- Shared trigger functions
-- ---------------------------------------------------------------------------
CREATE FUNCTION touch_updated_at() RETURNS trigger AS $$
BEGIN
  NEW.updated_at := now();
  RETURN NEW;
END $$ LANGUAGE plpgsql;

CREATE FUNCTION forbid_mutation() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION '% is append-only: % is not allowed', TG_TABLE_NAME, TG_OP;
END $$ LANGUAGE plpgsql;

-- ---------------------------------------------------------------------------
-- Tenancy
-- ---------------------------------------------------------------------------
CREATE TABLE tenants (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name        text NOT NULL,
  data_policy jsonb NOT NULL DEFAULT '{"pii_providers": ["local"]}',  -- read by the AI router
  created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE users (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id  uuid NOT NULL REFERENCES tenants(id),
  email      text NOT NULL,
  name       text NOT NULL,
  role       user_role NOT NULL DEFAULT 'builder',
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, email)
);

-- ---------------------------------------------------------------------------
-- Workflows and recipes
-- ---------------------------------------------------------------------------
CREATE TABLE workflows (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       uuid NOT NULL REFERENCES tenants(id),
  name            text NOT NULL,
  description     text,
  destination_key text NOT NULL,   -- the system it writes to, e.g. 'demo-crm'; scopes committed_keys
  allowed_origins text[] NOT NULL CHECK (cardinality(allowed_origins) > 0),
  created_by      uuid REFERENCES users(id),
  created_at      timestamptz NOT NULL DEFAULT now(),
  archived_at     timestamptz,
  UNIQUE (tenant_id, name)
);

CREATE TABLE recipe_versions (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id         uuid NOT NULL REFERENCES tenants(id),
  workflow_id       uuid NOT NULL REFERENCES workflows(id),
  version           int  NOT NULL CHECK (version > 0),
  format_version    int  NOT NULL DEFAULT 1,     -- version of the recipe JSON schema itself
  recipe            jsonb NOT NULL,
  parent_version_id uuid REFERENCES recipe_versions(id),
  change_reason     text NOT NULL,               -- e.g. 'compiled from recording', 'approved repair: Save -> Save lead'
  created_by        uuid REFERENCES users(id),
  created_at        timestamptz NOT NULL DEFAULT now(),
  UNIQUE (workflow_id, version)
);
CREATE TRIGGER recipe_versions_immutable
  BEFORE UPDATE OR DELETE ON recipe_versions
  FOR EACH ROW EXECUTE FUNCTION forbid_mutation();

CREATE TABLE recordings (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id   uuid NOT NULL REFERENCES tenants(id),
  workflow_id uuid NOT NULL REFERENCES workflows(id),
  recorded_by uuid REFERENCES users(id),
  start_url   text NOT NULL,
  events      jsonb NOT NULL DEFAULT '[]',       -- raw event log with fingerprints; passwords never stored
  event_count int GENERATED ALWAYS AS (jsonb_array_length(events)) STORED,
  started_at  timestamptz NOT NULL DEFAULT now(),
  ended_at    timestamptz
);

-- ---------------------------------------------------------------------------
-- Files, inputs, documents
-- ---------------------------------------------------------------------------
CREATE TABLE files (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     uuid NOT NULL REFERENCES tenants(id),
  storage_key   text NOT NULL UNIQUE,            -- object storage path; bytes never live in Postgres
  original_name text NOT NULL,
  mime_type     text NOT NULL,
  size_bytes    bigint NOT NULL CHECK (size_bytes >= 0),
  sha256        text NOT NULL,
  contains_pii  boolean NOT NULL DEFAULT true,
  retain_until  timestamptz,                     -- retention policy for PII-bearing files and screenshots
  uploaded_by   uuid REFERENCES users(id),
  created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX files_sha256_idx ON files (tenant_id, sha256);   -- exact duplicate upload check

CREATE TABLE input_batches (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id   uuid NOT NULL REFERENCES tenants(id),
  workflow_id uuid NOT NULL REFERENCES workflows(id),
  file_id     uuid REFERENCES files(id),
  kind        batch_kind NOT NULL,
  row_count   int NOT NULL DEFAULT 0,
  created_by  uuid REFERENCES users(id),
  created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE input_records (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id  uuid NOT NULL REFERENCES tenants(id),
  batch_id   uuid NOT NULL REFERENCES input_batches(id),
  row_index  int  NOT NULL CHECK (row_index >= 0),
  raw        jsonb NOT NULL,                     -- exactly as uploaded
  clean      jsonb NOT NULL DEFAULT '{}',        -- accepted values only; this is what a run uses
  record_key text,                               -- normalized key, e.g. lower(email)
  status     input_status NOT NULL DEFAULT 'needs_review',
  issues     jsonb NOT NULL DEFAULT '[]',
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (batch_id, row_index),
  CHECK (status <> 'ready' OR record_key IS NOT NULL)
);
CREATE INDEX input_records_key_idx ON input_records (batch_id, record_key);   -- in-file duplicate check
CREATE TRIGGER input_records_touch
  BEFORE UPDATE ON input_records
  FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

CREATE TABLE documents (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       uuid NOT NULL REFERENCES tenants(id),
  file_id         uuid NOT NULL REFERENCES files(id),
  input_record_id uuid REFERENCES input_records(id),
  has_text_layer  boolean NOT NULL,
  page_count      int CHECK (page_count > 0),
  ocr_engine      text,                          -- null when the text layer was used
  structure_key   text,                          -- grounded structure (text, reading order, boxes) in object storage
  created_at      timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- AI calls (append-only). Every LLM call in the product writes one row.
-- ---------------------------------------------------------------------------
CREATE TABLE ai_calls (
  id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  tenant_id      uuid NOT NULL REFERENCES tenants(id),
  task           text NOT NULL,                  -- 'normalize_fields', 'extract_document', 'propose_repair', ...
  provider       text NOT NULL,                  -- 'anthropic', 'deepseek', 'google', 'local'
  model          text NOT NULL,
  input_hash     text NOT NULL,                  -- cache key: same input + task + model -> reuse
  request        jsonb NOT NULL,                 -- stored after redaction
  response       jsonb,
  validation     ai_validation,
  escalated_from bigint REFERENCES ai_calls(id), -- set when the cascade retried on a stronger model
  tokens_in      int,
  tokens_out     int,
  cost_usd       numeric(12, 6),
  latency_ms     int,
  created_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ai_calls_cache_idx ON ai_calls (tenant_id, task, model, input_hash);
CREATE TRIGGER ai_calls_append_only
  BEFORE UPDATE OR DELETE ON ai_calls
  FOR EACH ROW EXECUTE FUNCTION forbid_mutation();

-- Every proposed value for an input field: from code, a saved mapping, AI, or a human.
-- The Input Review screen is a view over this table.
CREATE TABLE field_proposals (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       uuid NOT NULL REFERENCES tenants(id),
  input_record_id uuid NOT NULL REFERENCES input_records(id),
  field           text NOT NULL,
  raw_value       text,
  proposed_value  jsonb,                         -- null = "could not determine", never a guess
  source          value_source NOT NULL,
  evidence_quote  text,                          -- must literally occur in the source text
  evidence_page   int,
  evidence_bbox   jsonb,                         -- [x0, y0, x1, y1] on evidence_page, for highlighting
  confidence      numeric(4, 3) CHECK (confidence BETWEEN 0 AND 1),
  status          proposal_status NOT NULL DEFAULT 'proposed',
  ai_call_id      bigint REFERENCES ai_calls(id),
  decided_by      uuid REFERENCES users(id),
  decided_at      timestamptz,
  created_at      timestamptz NOT NULL DEFAULT now(),
  CHECK (source <> 'ai' OR ai_call_id IS NOT NULL)
);
CREATE INDEX field_proposals_review_idx ON field_proposals (input_record_id) WHERE status = 'proposed';

CREATE TABLE value_mappings (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id        uuid NOT NULL REFERENCES tenants(id),
  workflow_id      uuid NOT NULL REFERENCES workflows(id),
  field            text NOT NULL,
  input_value_norm text NOT NULL,                -- e.g. 'the data course'
  target_value     text NOT NULL,                -- e.g. 'Data Analytics Course'
  created_by       uuid REFERENCES users(id),
  created_at       timestamptz NOT NULL DEFAULT now(),
  UNIQUE (workflow_id, field, input_value_norm)
);

-- ---------------------------------------------------------------------------
-- Runs and the ledger
-- ---------------------------------------------------------------------------
CREATE TABLE runs (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id         uuid NOT NULL REFERENCES tenants(id),
  workflow_id       uuid NOT NULL REFERENCES workflows(id),
  recipe_version_id uuid NOT NULL REFERENCES recipe_versions(id),   -- pinned for the whole run
  input_batch_id    uuid REFERENCES input_batches(id),
  mode              run_mode NOT NULL,
  status            run_status NOT NULL DEFAULT 'queued',
  pause_reason      text,                        -- 'login_required', 'challenge', 'canary_failed', ...
  approval_policy   jsonb NOT NULL DEFAULT '{"commit": "first_record"}',
  worker_id         text,
  heartbeat_at      timestamptz,
  started_by        uuid REFERENCES users(id),
  created_at        timestamptz NOT NULL DEFAULT now(),
  started_at        timestamptz,
  finished_at       timestamptz
);
CREATE INDEX runs_queue_idx ON runs (created_at) WHERE status = 'queued';

CREATE TABLE run_records (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       uuid NOT NULL REFERENCES tenants(id),
  run_id          uuid NOT NULL REFERENCES runs(id),
  input_record_id uuid NOT NULL REFERENCES input_records(id),
  record_key      text NOT NULL,
  state           record_state NOT NULL DEFAULT 'pending',
  attempts        smallint NOT NULL DEFAULT 0,   -- incremented by the guard on every pending -> claimed
  max_attempts    smallint NOT NULL DEFAULT 3,
  reason          text,                          -- human-readable: 'duplicate', 'missing email', ...
  last_error      jsonb,
  destination_ref text,                          -- the destination's own id for the record, when known
  claimed_by      text,
  claimed_at      timestamptz,
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now(),
  UNIQUE (run_id, record_key),
  UNIQUE (run_id, input_record_id),
  CHECK (attempts <= max_attempts)
);
CREATE INDEX run_records_work_idx ON run_records (run_id, created_at)
  WHERE state IN ('pending', 'submitting', 'submitted_unverified', 'reconciling');

-- The ledger state machine, enforced by the database. Any transition not listed is rejected,
-- so an application bug cannot, for example, move a record from 'submitting' straight to 'pending'
-- and resubmit it blindly.
CREATE FUNCTION guard_record_transition() RETURNS trigger AS $$
BEGIN
  IF NEW.state = OLD.state THEN
    RETURN NEW;
  END IF;

  IF NOT (OLD.state::text || '>' || NEW.state::text = ANY (ARRAY[
    'pending>claimed', 'pending>skipped',
    'pending>failed',                                  -- attempts exhausted; the claim query no longer picks it up
    'claimed>prechecked', 'claimed>pending', 'claimed>parked', 'claimed>failed',
    'prechecked>filling', 'prechecked>skipped', 'prechecked>parked', 'prechecked>pending',
    'filling>submitting', 'filling>verified',          -- filling>verified: read-only workflows with no commit step
    'filling>parked', 'filling>failed', 'filling>pending',
    'submitting>submitted_unverified',
    'submitting>reconciling',                          -- crash between ledger write and click acknowledgment
    'submitting>failed',                               -- click provably never dispatched
    'submitted_unverified>verified', 'submitted_unverified>failed', 'submitted_unverified>reconciling',
    'reconciling>verified', 'reconciling>pending', 'reconciling>parked', 'reconciling>failed',
    'parked>pending', 'failed>pending'
  ])) THEN
    RAISE EXCEPTION 'illegal ledger transition % -> % (run_record %)', OLD.state, NEW.state, OLD.id;
  END IF;

  IF OLD.state = 'pending' AND NEW.state = 'claimed' THEN
    NEW.attempts := OLD.attempts + 1;              -- CHECK (attempts <= max_attempts) stops runaway retries
  END IF;

  RETURN NEW;
END $$ LANGUAGE plpgsql;

CREATE TRIGGER run_records_guard
  BEFORE UPDATE OF state ON run_records
  FOR EACH ROW EXECUTE FUNCTION guard_record_transition();
CREATE TRIGGER run_records_touch
  BEFORE UPDATE ON run_records
  FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

-- Cross-run duplicate guard. idempotency_key comes from the recipe:
--   create workflows: '{record_key}'                      -> a customer is created once, ever
--   update workflows: '{sku}:{new_price}:{effective_date}' -> the same change is applied once,
--                                                            but a later price change is a new key
CREATE TABLE committed_keys (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       uuid NOT NULL REFERENCES tenants(id),
  destination_key text NOT NULL,
  idempotency_key text NOT NULL,
  workflow_id     uuid NOT NULL REFERENCES workflows(id),
  run_record_id   uuid NOT NULL REFERENCES run_records(id),
  destination_ref text,
  verified_at     timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, destination_key, idempotency_key)
);
CREATE TRIGGER committed_keys_append_only
  BEFORE UPDATE OR DELETE ON committed_keys
  FOR EACH ROW EXECUTE FUNCTION forbid_mutation();

CREATE TABLE step_events (
  id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  tenant_id     uuid NOT NULL REFERENCES tenants(id),
  run_record_id uuid NOT NULL REFERENCES run_records(id),
  step_id       text NOT NULL,                   -- matches the step id inside the pinned recipe
  attempt       smallint NOT NULL,
  started_at    timestamptz NOT NULL,
  ended_at      timestamptz,
  outcome       step_outcome NOT NULL,
  locator_rung  smallint CHECK (locator_rung BETWEEN 1 AND 5),   -- which ladder rung found the element
  observed      jsonb,                           -- what the page showed: value read back, text seen, URL
  error         jsonb,
  screenshot_key text                            -- object storage; sensitive fields masked before upload
);
CREATE INDEX step_events_record_idx ON step_events (run_record_id, id);
CREATE TRIGGER step_events_append_only
  BEFORE UPDATE OR DELETE ON step_events
  FOR EACH ROW EXECUTE FUNCTION forbid_mutation();

CREATE TABLE heal_proposals (
  id                     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id              uuid NOT NULL REFERENCES tenants(id),
  workflow_id            uuid NOT NULL REFERENCES workflows(id),
  from_recipe_version_id uuid NOT NULL REFERENCES recipe_versions(id),
  step_id                text NOT NULL,
  old_fingerprint        jsonb NOT NULL,
  proposed_target        jsonb NOT NULL,
  proposed_by            heal_source NOT NULL,
  ai_call_id             bigint REFERENCES ai_calls(id),
  status                 heal_status NOT NULL DEFAULT 'pending',
  decided_by             uuid REFERENCES users(id),
  decided_at             timestamptz,
  new_recipe_version_id  uuid REFERENCES recipe_versions(id),
  created_at             timestamptz NOT NULL DEFAULT now(),
  CHECK (proposed_by <> 'ai' OR ai_call_id IS NOT NULL),
  CHECK (status <> 'approved' OR new_recipe_version_id IS NOT NULL)
);

-- ---------------------------------------------------------------------------
-- Background jobs (OCR, input cleaning, exports). Postgres is the queue.
-- ---------------------------------------------------------------------------
CREATE TABLE jobs (
  id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  tenant_id    uuid NOT NULL REFERENCES tenants(id),
  kind         text NOT NULL,                    -- 'ocr_document', 'clean_batch', ...
  payload      jsonb NOT NULL,
  status       job_status NOT NULL DEFAULT 'queued',
  attempts     smallint NOT NULL DEFAULT 0,
  max_attempts smallint NOT NULL DEFAULT 3,
  run_after    timestamptz NOT NULL DEFAULT now(),
  locked_by    text,
  locked_at    timestamptz,
  last_error   text,
  created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX jobs_ready_idx ON jobs (run_after) WHERE status = 'queued';

-- ---------------------------------------------------------------------------
-- Results screen
-- ---------------------------------------------------------------------------
CREATE VIEW run_summary AS
SELECT
  run_id,
  count(*)                                         AS total,
  count(*) FILTER (WHERE state = 'verified')       AS verified,
  count(*) FILTER (WHERE state = 'skipped')        AS skipped,
  count(*) FILTER (WHERE state = 'parked')         AS parked,
  count(*) FILTER (WHERE state = 'failed')         AS failed,
  count(*) FILTER (WHERE state NOT IN ('verified', 'skipped', 'parked', 'failed')) AS in_progress
FROM run_records
GROUP BY run_id;

COMMIT;
