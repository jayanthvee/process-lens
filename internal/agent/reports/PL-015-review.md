# Review PL-015

Status: changes requested (two items) · Reviewer: T1 · Reviewed commit: a51aa56 (branch `pl-015`)

## Verdict

**Good:**
- The middleware design is right:
  - one front door, reusing the hub's own predicates;
  - an absent `Origin` is allowed;
  - a body with no length header is caught by a peek and replayed.
- The SQL follows `sql-patterns.md`.
- The page renders with `textContent` only.
- The commit-attempted confirmation gates both buttons.
- Every requirement's negative check was actually run.
- T2 found and fixed two real bugs of its own: `IllegalTransition` → 500, and the fake-timer deadlock.

**Two blockers.**

## Ruling on the 37 failing `tests/db` API tests

**Option 1 is approved.** The scope is widened to:
- `tests/db/test_health_api.py`
- `tests/db/test_recipes_api.py`
- `tests/db/test_runs_api.py`

**Only** their client construction changes, to
`TestClient(app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 51234))`. No assertion changes. A Host check
that tolerated `testserver` would be the hole requirement 1 closes, so the tests change, not the middleware.

## Must fix

**B1. The front door breaks pairing with the real extension.**
- The extension's service worker opens its session with `POST /api/v1/executor/sessions`
  (`apps/extension/src/background/command-session.ts:211`). Chrome sends `Origin: chrome-extension://<id>` on that
  request.
- `_origin_refusal` accepts only the API's own origin. So after this branch, Connect gets a 403 and no batch can run
  with the real extension.
- The ticket said "the hub routes keep their own stricter checks". That held for the WebSockets, not for the hub's
  HTTP routes. No test in this ticket exercises the extension, so nothing caught it.
- **Fix:**
  - For `POST /api/v1/executor/sessions` only, the origin check also accepts exactly
    `chrome-extension://{PROCESSLENS_EXTENSION_ID}`, the same pinned id the hub uses for its socket.
  - The Host, loopback and content-type checks are unchanged. The hub's own `X-ProcessLens-Client` check still runs
    after them.
  - If `PROCESSLENS_EXTENSION_ID` is unset, no extension origin is accepted.
  - Every other route still accepts only the API's own origin.
- **Tests (`tests/api/test_frontdoor.py`):**
  - the pinned extension origin with the header gets 201;
  - a different `chrome-extension://` id gets 403;
  - the pinned extension origin on `POST /api/v1/runs/{id}/cancel` gets 403;
  - with the extension id unset, the extension origin gets 403.
- **E2e (scoped exception):** run `tests/e2e/test_batch_real_extension.py` **once**. It is the only proof that the real
  popup still connects through the new front door. Report what it shows, with no re-runs.

**B2. Dismiss accepts a `prechecked` record.**
- `dismiss_record` updates `WHERE state IN ('parked', 'prechecked')`. A `prechecked` record is being worked by a
  runner right now. Dismissing it pulls the record out from under a live run, and the runner's next transition is
  then refused (I28).
- The ticket says "moves a **parked** record to `skipped`". `prechecked → skipped` is the runner's duplicate path,
  not a person's.
- **Fix:** both updates use `state = 'parked'`.
- **Test:** dismissing a `prechecked` record gets 409, and the record stays `prechecked`.

## Instructions for T2

1. Fix B1 and B2 with the tests named, and apply the ruling to the three `tests/db` files. No other changes.
2. Gates: `python -m pytest -q` (must be green now), `ruff check .`, `ruff format --check .`,
   `npm --prefix apps/extension run check`, `npm --prefix apps/web run check`.
3. Run `tests/e2e/test_batch_real_extension.py` once.
4. Add a "Review fixes" section to `internal/agent/reports/PL-015.md` with the real output. Commit it with this review,
   push, and stop for T1.
