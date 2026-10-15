# Review PL-022

Status: **approved for merge** · Reviewer: T1 · Reviewed commit: 3041848 (branch `pl-022`)

## Checks (run by T1)

```
python -m pytest -q                    681 passed
ruff check . / ruff format --check .   clean / 212 files already formatted
GET http://127.0.0.1:8069/web/login    200 (Odoo, seeded stack left running by T2)
GET http://127.0.0.1:3000              200 (Twenty)
git check-ignore infra/test-apps/.env  ignored by .gitignore:9
```

## Verdict

- **Pinned images:** `odoo:18.0-20260926` and `twentycrm/twenty:v2.47.0`.
- **Ports:** both are published on `127.0.0.1` only.
- **Outgoing mail:** disabled.
- **The root `docker-compose.yml` is untouched.**
- **The seeders are idempotent.** A reset from scratch took 131 s in total, with Twenty's workspace activated through its API and no manual sign-up.
- **Two real defects were found by running the stack, not by reading code:**
  - Odoo 18's `--smtp` flag;
  - the XML-RPC `allow_none` failure on `action_apply_inventory`.

  Both are fixed and explained in the report.
- **D-021 (Railway) and D-022 (real test apps)** are recorded.

## Follow-ups (not blocking)

- **The Postgres images use the floating tag `postgres:16`.** Pin them to an exact tag the next time this file
  changes.
- **The local `.data/pgdata` Postgres on port 57288 stopped** when T2's session closed. The test suite uses the
  Docker Postgres on 5432 through `.env`, and that works.
