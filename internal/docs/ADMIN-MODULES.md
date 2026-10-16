# Adding an admin module that needs schema

The admin panel is a separate application (`process-lens-admin`, D-023). It has no `db/` folder, and it
never runs a migration. A feature that needs the schema to change is therefore always a pair of
tickets: a migration in this repository, and a module in the panel (D-025). This page is the order to
write that pair in, and the rules each half has to keep.

## The split

- **This repository owns the schema and the migrations.** `db/migrations/` is the only place a
  migration is written, and it is append-only history: a file that has landed is never edited, and a
  correction is a new file. This repository also owns the runner, which is why the tables that decide
  what a run may do live here too.
- **`process-lens-admin` owns the panel.** It reaches the database through `ADMIN_DATABASE_URL`, a
  login that inherits the role `processlens_admin`. It holds no DDL privileges and runs no migration.
- **The role is least-privilege by construction.** `processlens_admin` can read every table, and it
  can write only the columns the panel's own modules write. Everything that affects a run -- starting,
  resuming, pausing, cancelling, freezing runs, setting the minimum extension version -- goes through
  the ProcessLens API (D-024), so the role never needs a write on any table a run owns.

## The PL migration

`db/migrations/0006_admin_core.sql` is the worked example: the `core` module, the first admin module,
written in one file. A migration for a new module has the same four parts.

**1. The schema the module needs.** `0006` adds `admin_modules`; three columns on `tenants`
(`runs_frozen_at`, `runs_frozen_by`, `min_extension_version`); `users.deactivated_at`;
`extension_reports`; and `admin_audit`. A later module adds its own table or column and changes
nothing an earlier file created.

**2. Its `admin_modules` row, inserted in the same file.**

```sql
INSERT INTO admin_modules (id, migration) VALUES ('core', '0006_admin_core.sql');
```

`id` is the module's id, and `migration` is the file's own name, so a person reading the table can go
straight to the file that made it. The row is written here, not by the panel: the panel reads
`admin_modules` to decide which modules to switch on (D-025), and `processlens_admin` has no `INSERT`
on that table at all.

**3. Its grants, in the same file.** They take exactly this shape, and nothing more.

`0006` grants `SELECT` on everything that exists when it runs:

```sql
GRANT SELECT ON ALL TABLES IN SCHEMA public TO processlens_admin;
```

`ALL TABLES` covers the tables that exist at that moment, so a later migration grants `SELECT` on its
own new tables by name.

Then one `GRANT INSERT` or `GRANT UPDATE (column, ...)` per table and per column the module writes:

```sql
GRANT INSERT ON admin_audit TO processlens_admin;
GRANT UPDATE (role, deactivated_at) ON users TO processlens_admin;
GRANT UPDATE (min_extension_version) ON tenants TO processlens_admin;
```

That is the whole list for `core`: one table it writes whole (an audit row), and two sets of columns
on tables that already existed.

**4. The role itself**, created once per cluster and conditionally:

```sql
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'processlens_admin') THEN
    CREATE ROLE processlens_admin NOLOGIN;
  END IF;
END $$;
```

A role belongs to the cluster, and each test builds a fresh database, so a later migration names the
role and does not create it again.

### The two rules a new migration must follow

- **`SELECT` on every table it creates.** A module that cannot read its own table cannot tell a person
  anything, and the panel's reads are its own (its queries run as the role, not through the API).
- **It grants only the columns its own module writes.** `0006` asks for two columns on `users` and one
  on `tenants` because that is what `core` changes. A later module asks for its own columns and no
  other, so a reader can tell from the grants alone what each module is able to change.

And the floor, which never moves: `processlens_admin` must never be able to write `runs`,
`run_records`, `committed_keys` or `jobs`. Those carry the ledger and the queue, and only the API and
the runner write them (D-024). A migration that grants a write there is wrong whatever the module
wants; what the module wants is an API endpoint, which is a change in this repository's API and not a
grant.

## The ADM ticket

In `process-lens-admin`, a module is two folders and one manifest:

- `api/modules/<id>/`: its FastAPI router, its queries, and `manifest.py`;
- `web/src/modules/<id>/`: its pages, and `manifest.ts`.

The manifest declares three things that matter here:

- `id`, its title and its sidebar entries;
- `requires`: the `admin_modules` row it needs. For a module built on `0006`'s schema that row is
  `core`, the id its migration inserted. A module that only reads tables that already existed
  requires nothing;
- `writes`: the tables and columns it writes. A test in the panel checks this list against the role's
  real grants, so the manifest is documentation the test suite enforces.

The registry (`api/registry.py`, `web/src/registry.ts`) reads `admin_modules` at start-up and mounts
only the modules whose row the database has. A panel deployed against an older schema therefore loses
the module instead of crashing: its sidebar entry says "Needs a ProcessLens update" and its page
explains that in one sentence. That is why the row in step 2 matters as much as the tables: a
migration that adds the tables and forgets the row leaves the module unmounted and looks broken.

`internal/agent/tickets/ADM-001.md` is the ticket that defines the module system, and it is also the
worked example of a module set (`overview`, `people`, `runs`, `tasks`, `ai_spend`, `activity`).

## The order these land in

1. **The PL migration, merged in this repository first.** Nothing in the panel can be checked until
   the tables, the `admin_modules` row and the grants exist.
2. **The ADM module, written against that schema.** Its manifest test checks `writes` against the
   role's actual column grants, so the panel's own suite fails if the migration granted fewer columns
   than the module declares.
