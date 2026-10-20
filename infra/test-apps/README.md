# Test apps: Odoo 18 Community and Twenty CRM

These are the "real apps" the recorder and the runner are tested against (decision D-022).
They are real software running locally, not pages we wrote, so they carry bugs and
behaviours nobody here invented: Odoo's CRM, sales, inventory, accounting and calendar, and
Twenty's React UI with autocompletes, inline editing and command menus.

Nothing here sends mail or listens beyond `127.0.0.1`. **T1 runs the browser tests against
these apps; this folder only stands them up, seeds them and resets them.**

## What runs

Six containers, all defined in `docker-compose.yml` (pinned tags, named volumes):

- **Odoo 18 Community** — `odoo:18.0-20260926` on <http://localhost:8069>, with its own
  `postgres:16` (`odoo-db`).
- **Twenty CRM** — `twentycrm/twenty:v2.47.0` on <http://localhost:3000>, running its server
  and its worker plus `postgres:16` (`twenty-db`) and `redis:7.4-alpine` (`twenty-redis`).

Only the two web ports are published, both bound to `127.0.0.1`. The databases and Redis are
not reachable from the host.

## Requirements

- Docker Desktop running Linux containers. If Docker is not running, stop: nothing here
  works around that.
- Python 3.12 to run the seeders. They use the standard library only — no packages to install.
- **RAM: about 1.7 GB** for the six containers at rest (measured: Twenty's worker ~780 MB,
  Twenty's server ~710 MB, the two Postgres instances ~150 MB together, Redis ~19 MB, Odoo
  ~80 MB idle). Allow ~3 GB once both UIs are open in a browser and Docker Desktop's own VM
  is counted, so this fits a 16 GB laptop with room to spare. The stack is idle-cheap; Odoo
  grows as its modules load and while its threaded workers handle requests.

## Start, stop, look

```bash
docker compose -f infra/test-apps/docker-compose.yml up -d      # start (waits for health)
docker compose -f infra/test-apps/docker-compose.yml ps         # what is up
docker compose -f infra/test-apps/docker-compose.yml logs -f    # follow
docker compose -f infra/test-apps/docker-compose.yml stop       # stop, keep the data
docker compose -f infra/test-apps/docker-compose.yml down       # stop, keep the volumes
docker compose -f infra/test-apps/docker-compose.yml down -v    # stop and wipe all data
```

## Seed and reset

```bash
python infra/test-apps/reset.py all        # both apps: drop the data, seed it again
python infra/test-apps/reset.py odoo       # Odoo only
python infra/test-apps/reset.py twenty     # Twenty only
python infra/test-apps/reset.py all --seed-only   # ensure the data, drop nothing
```

- **Odoo reset** drops the `processlens_test` database, creates it again, installs `crm`,
  `sale_management`, `stock`, `account` and `calendar`, and seeds it. The module install is the
  slow part; the run prints a time for each phase.
- **Twenty reset** clears every person, company, opportunity, task and note, then creates the
  seeded people and companies. A brand-new workspace also arrives with a few of Twenty's own
  sample records; clearing removes those, so the workspace ends up holding exactly the seeded
  data. Seconds, not minutes.
- Both seeders are idempotent — running `--seed-only` twice creates nothing the second time.
- Each app resets in well under three minutes, the limit in the ticket.

The seeders can also be run on their own: `python infra/test-apps/seed_odoo.py`,
`python infra/test-apps/seed_twenty.py` (add `--reset` to wipe first).

## Logins

Test values only, and the same ones `docker-compose.yml` defaults to. They live in
`.env.example` (committed); copy it to `.env` to override anything, since `.env` is gitignored.

- **Odoo** — <http://localhost:8069> — `admin` / `ProcessLensTest18`
  - also seeded: `sales1@processlens.test`, `sales2@processlens.test`, `sales3@processlens.test`,
    each with `SalesDemoPass18`
- **Twenty** — <http://localhost:3000> — `test@processlens.test` / `ProcessLensTest20`

**No manual sign-up step.** The Twenty seeder creates the workspace and that login over
Twenty's own API and activates the workspace, exactly the way the browser does it, so
`reset.py twenty` on a fresh volume leaves the app logged-in-ready.

## What gets seeded

- **Odoo:** 3 salespeople; 10 existing leads; 20 products with codes, prices and stock; 5
  customers that each carry one open invoice; 8 contacts with tags for the enrollment case.
- **Twenty:** 10 people and 5 companies.
- Both apps keep `seed_data.py` as the single source of truth for these records, so the sample
  CSVs below cannot silently drift away from what is in the apps.

## Sample CSVs

`samples/` holds one input file per use case the apps cover. They are for T1's runs; the row
counts and the awkward rows are asserted by `tests/infra/test_test_apps_samples.py`.

- `usecase-1-leads.csv` — 20 rows for use case 1 on Odoo: 3 rows duplicate a seeded lead's
  email (the dedupe check must find them) and 2 rows have no email at all.
- `usecase-4-products.csv` — 10 rows for use case 4 on Odoo: 9 update a product that exists
  and 1 carries a code that exists nowhere.
- `usecase-6-enrollment.csv` — 8 rows for use case 6 on Odoo, as contacts plus tags.
- `twenty-leads.csv` — 10 rows for use case 1 on Twenty: 3 duplicate a seeded person's email.

## Notes

- **Mail is off in both apps.** Odoo has no outgoing mail server configured (the seeder leaves
  `ir.mail_server` empty and checks that), and its SMTP fallback points at the reserved,
  non-resolving name `smtp.invalid`. Twenty runs with `EMAIL_DRIVER=logger`, which logs a
  message instead of sending it. Nothing can leave either container.
- **Ports.** 8069 and 3000 are published on `127.0.0.1` only, and both are overridable:
  `ODOO_HTTP_PORT`, `TWENTY_HTTP_PORT`. Change those rather than touching the ProcessLens
  stack's ports.
- **Volumes.** `odoo-db-data`, `odoo-filestore`, `twenty-db-data`, `twenty-redis-data`,
  `twenty-local-storage`. `down -v` removes them; after that, start the stack and run
  `reset.py all` again to get a fresh pair of apps.
- **Compose project.** The project is named `processlens-test-apps`, so it never collides with
  the root `docker-compose.yml` (project `processlens`). The two stacks share no network and
  no volumes. This folder's variables are namespaced `ODOO_*` / `TWENTY_*` on purpose, so the
  repository root `.env` cannot leak its `POSTGRES_*` values in here.
- **Image pinning.** The two apps are pinned to exact tags (`odoo:18.0-20260926`,
  `twentycrm/twenty:v2.47.0`). Postgres and Redis are pinned to a minor line (`postgres:16`,
  `redis:7.4-alpine`) — the same choice Twenty's own compose makes — so a `docker compose pull`
  can pick up a patch release of those two.

## OrangeHRM (PL-069)

Real HR software with employees, customers, projects, activities and weekly timesheets, standing in
for Workday (which has no public sandbox). It is **real software**, not a look-alike (D-022).

- **URL:** <http://localhost:8070> -- **Login:** `Admin` / `ProcessLensTest57!`
- **Seeded people:** `priya.raman`, `jay.vee`, `ravi.iyer` (ESS logins, password
  `EmployeeTest57!`), 2 customers, 4 projects, the activities *Development* and *Meetings*.
- **No browser step.** The first `up` installs it unattended: the image's CLI installer
  (`installer/cli_install.php`) is driven from a YAML file the container writes from its
  `ORANGEHRM_*` environment. Once installed, the install is kept in the `orangehrm-confs` volume.
- **Mail is off:** nothing in the stack talks SMTP.

```bash
docker compose -f infra/test-apps/docker-compose.yml up -d orangehrm   # start (waits for health)
python infra/test-apps/reset.py orangehrm                              # seed it, empty the test week
python infra/test-apps/seed_orangehrm.py                               # the seeder on its own
```

**The reset never drops the database.** It ensures the employees, customers, projects and
activities over OrangeHRM's own REST API (v2), sets the timesheet week to start on Monday (which
also enables the Time module), and deletes the rows of the current week's timesheet so the next run
starts from an empty week. Seconds, not minutes.

The timesheet golden (`tests/e2e/test_orangehrm_timesheet_golden.py`) is the browser test that uses
it; see `internal/agent/reports/PL-069.md` for what it does and what it found.
