# ADM-001a review (T1)

**Verdict: approved.** Branch `adm-001a` in `process-lens-admin`, HEAD `09c5015`.

## What I checked

- `pytest -q api/tests` against the local database with `ADMIN_TEST_DATABASE_URL` set: 25 passed and 0 skipped,
  so the database tests really ran.
- `ruff check .` is clean. `npm --prefix web run check` passed: typecheck, the vitest tests and the build.
- The freeze and unfreeze contract matches `services/api/admin.py`: `POST /api/v1/admin/freeze` and `/unfreeze`
  with `{"tenant_id"}` and the `x-processlens-admin-token` header.
- Every query in `overview/router.py` and `people/router.py` filters by `admin.tenant_id`, and tests cover a second
  tenant.
- The `extension_reports` join can't duplicate people, because `user_id` is its primary key.
- Sign-in refuses a builder, a deactivated owner, an unknown address and an unset address.
- No migration was run, and `.env` is ignored.

## Notes (none blocking)

1. **The freeze is two steps.** The panel calls the ProcessLens API first, and then writes its own `admin_audit`
   row. If the panel's insert fails after the API succeeds, the freeze stands, but only the core row records it.
   Acceptable for now, because the core row is written in the same transaction as the freeze.
2. **The core audit row names the wrong person.** It names the ProcessLens operator user as the actor, not the
   admin who pressed the button. Only the panel's row has the real person. When SSO lands, pass the actor through
   the API.
3. **The dev sign-in uses the oldest matching user** when the same email exists in two tenants. That's fine for the
   dev provider only; SSO replaces it.
