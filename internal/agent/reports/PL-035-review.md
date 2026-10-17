# PL-035 review (T1)

**Verdict: approved, with one T1 change.**

## What I checked

- **The tests:** `tests/api/test_apps_api.py` plus `tests/runner/test_sql_separation.py` gave 11 passed. The web check
  passed 134 vitest tests and the build.
- **The SQL:**
  - It reads only the latest recipe version and leaves archived workflows out.
  - The newest run per origin comes from `row_number()`.
  - The `jsonb_agg` carries only task ids and names, so no row contents.
- **The read stays in `packages.ledger.reads`.** The route only names and shapes the result.
- **The Chrome line reuses `GET /api/v1/executor/session`,** as the sidebar does. No new route.

## The T1 change

- **One column.** `apps.css` put the apps two side by side, which is a card grid, and DESIGN.md §4 bans those. The
  panels now stack in one column at every width.

## Notes (none blocking)

- **With no `tenant_id`** (the single-tenant dev default, same as `/tasks`), two tenants sharing an origin are
  grouped into one app. `per_origin` and `last_run` partition by origin only. Add `tenant_id` to both when SaaS
  lands.
