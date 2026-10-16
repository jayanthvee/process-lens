-- ProcessLens schema v6: the `core` admin module (PL-026).
--
-- The admin panel lives in its own repository (D-023), so this repository owns the schema and the
-- runner; one migration per admin module (D-025). This file is the `core` module's schema and it
-- records itself in `admin_modules`, the table the panel reads to decide which modules to switch on.
-- It adds, before the panel may act at all:
--   A1  `admin_modules`: one row per admin module this schema supports. This file inserts `core`.
--   A2  the freeze switch: `tenants.runs_frozen_at` and `runs_frozen_by`. The runner reads
--       `runs_frozen_at` at the same step boundary where it reads `cancel_requested_at`, and pauses
--       the run there with `pause_reason = 'admin_freeze'`. It never stops between `submitting` and
--       `verified`.
--   A3  the extension floor: `tenants.min_extension_version` (null means any version) and
--       `extension_reports`, the version each person's extension last reported.
--   A4  `users.deactivated_at`: a deactivated person cannot start, resume or approve anything.
--   A5  `admin_audit`, append-only like `approvals`: one row per admin action, written in the same
--       transaction as the change it describes.
--   A6  the role `processlens_admin`, which the admin panel's login inherits. The role reaches the
--       database for the panel's own reads (D-023); everything that affects runs goes through the
--       API, so the grants below are exactly the columns this module writes and nothing more.
--
-- 0001, 0002, 0003 and 0005 are not modified. This file is append-only history: corrections are new
-- files.

BEGIN;

-- ---------------------------------------------------------------------------
-- A1. The admin module registry
-- ---------------------------------------------------------------------------

CREATE TABLE admin_modules (
  id        text PRIMARY KEY,
  migration text NOT NULL,
  added_at  timestamptz NOT NULL DEFAULT now()
);

-- This migration is the `core` module. Every later admin-module migration inserts its own row here,
-- in its own file, naming its own migration.
INSERT INTO admin_modules (id, migration) VALUES ('core', '0006_admin_core.sql');

-- ---------------------------------------------------------------------------
-- A2. The freeze switch, and A4. a person turned off
-- ---------------------------------------------------------------------------

ALTER TABLE tenants ADD COLUMN runs_frozen_at timestamptz;
ALTER TABLE tenants ADD COLUMN runs_frozen_by uuid REFERENCES users(id);

-- A3. The extension floor: null means any version, so a tenant that never set one is unrestricted.
ALTER TABLE tenants ADD COLUMN min_extension_version text;

ALTER TABLE users ADD COLUMN deactivated_at timestamptz;

-- ---------------------------------------------------------------------------
-- A3. The version each person's extension last reported
-- ---------------------------------------------------------------------------

-- One row per person: the report is an upsert, so the table holds the newest version only.
-- There is no format CHECK on `version`: the API is the validator and the database stores what it
-- is told, so a version the API accepts is never rejected here.
CREATE TABLE extension_reports (
  user_id     uuid PRIMARY KEY REFERENCES users(id),
  tenant_id   uuid NOT NULL REFERENCES tenants(id),
  version     text NOT NULL,
  reported_at timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- A5. The admin action log (append-only, like `approvals`)
-- ---------------------------------------------------------------------------

CREATE TABLE admin_audit (
  id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  tenant_id     uuid NOT NULL REFERENCES tenants(id),
  actor_user_id uuid REFERENCES users(id),
  module        text NOT NULL,
  action        text NOT NULL,
  target        text,
  detail        jsonb NOT NULL DEFAULT '{}',     -- what the action was taken on
  at            timestamptz NOT NULL DEFAULT now(),
  -- The actions the `core` module uses. A later admin-module migration uses its own `module` id and
  -- constrains its own actions in its own CHECK, so this one never grows.
  CONSTRAINT admin_audit_core_actions CHECK (
    module <> 'core' OR action IN (
      'freeze_runs', 'unfreeze_runs', 'change_role', 'deactivate_user', 'reactivate_user',
      'pause_run', 'cancel_run', 'set_min_extension_version'
    )
  )
);

-- An audit row is never edited, deleted or truncated: a row trigger for UPDATE and DELETE, and a
-- statement trigger for TRUNCATE, which the row trigger does not see (the same pair as `approvals`).
CREATE TRIGGER admin_audit_append_only
  BEFORE UPDATE OR DELETE ON admin_audit
  FOR EACH ROW EXECUTE FUNCTION forbid_mutation();

CREATE TRIGGER admin_audit_no_truncate
  BEFORE TRUNCATE ON admin_audit
  FOR EACH STATEMENT EXECUTE FUNCTION forbid_truncate();

-- ---------------------------------------------------------------------------
-- A6. The role the admin panel connects as
-- ---------------------------------------------------------------------------

-- A role is cluster-wide, and one cluster holds many databases (each test builds a fresh one), so
-- the creation is conditional: the role is created once and is there for every later database. The
-- deploy creates a login with INHERIT that is a member of this role.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'processlens_admin') THEN
    CREATE ROLE processlens_admin NOLOGIN;
  END IF;
END $$;

-- The panel's own reads: every table that exists now. A later admin-module migration grants SELECT
-- on the tables it adds, in its own file.
GRANT SELECT ON ALL TABLES IN SCHEMA public TO processlens_admin;

-- Writing an admin action is the one whole-table write this module needs. The identity column needs
-- no separate sequence grant.
GRANT INSERT ON admin_audit TO processlens_admin;

-- Column by column, and nothing else: the API owns every other write. `users` and `tenants` are
-- readable by the role (above), but the columns below are the only ones this module changes, and
-- `runs`, `run_records`, `committed_keys` and `jobs` stay read-only to the panel, because everything
-- that affects runs goes through the API.
GRANT UPDATE (role, deactivated_at) ON users TO processlens_admin;
GRANT UPDATE (min_extension_version) ON tenants TO processlens_admin;

COMMIT;
