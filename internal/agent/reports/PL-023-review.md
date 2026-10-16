# PL-023 review (T1)

Branch `pl-023` at `9e9d5b1`, plus T1's fix commit. **Verdict: approved** with the fixes below.

## Gates (T1, on the branch)
- `npm --prefix apps/web run check`: typecheck clean, 98 tests, build ok.
- `python -m pytest -q`: 697 passed before T1's fixes; the touched modules pass after them.
- `ruff check .` and `ruff format --check .`: clean.
- e2e: see the PR description.

## Scope
There is no change to the runner, the hub's frames or protocol, the migrations, or any ledger write or
transition.
- `packages/ledger/reads.py` is SELECT only.
- `GET /api/v1/executor/session` is one new read-only, local-only hub route that returns `{"attached": bool}`.
- The re-queue safety check is untouched, and the needs-help set (`parked`, `failed`) is exactly what the
  re-queue endpoint accepts.

## Fixed by T1
1. **Major: time saved subtracted waiting time.** `_WINDOW_RUN_SECONDS` summed `coalesce(finished_at, now()) -
   coalesce(started_at, created_at)` over every run, open or not.
   - So a run paused over a weekend for an approval, or a queued run no worker had claimed, subtracted days of
     wall clock, and the week's time saved read 0.
   - Now only runs that finished inside the window count, start to finish. This also lines the run-time window up
     with the rows' window.
   - Two tests were added: an open run (paused, or queued) subtracts nothing; a finished 10-minute run subtracts 10
     minutes.
2. **Minor: bare `/api` served the page.** The SPA fallback only tested the `api/` prefix. `/api` now 404s like
   any API miss, with a test.

## Browser check (T1)
Served by `services/api` against the dev database with synthetic rows. Home, Needs help, Tasks and Runs render
real data, and the vocabulary is plain. The only console errors were 503s from a first start without
`DATABASE_URL`, which is expected.

## Follow-ups (not blocking)
- **The Needs help page shows a developer message** when `PROCESSLENS_OPERATOR_ID` is unset: "The API has no
  operator identity … Set PROCESSLENS_OPERATOR_ID and restart the API". It is true but not plain. Sign-in (the
  Railway ticket) removes the case. Until then, reword it to "You can look, but can't change anything here
  until ProcessLens knows who you are", and keep the variable name in the console.
- **"Running now" includes queued runs** under the Live marker. Show queued runs as "Waiting to start", or leave
  them out of that panel.
- A client route whose last segment contains a dot (`/tasks/v1.2`) 404s on refresh, because of the
  `Path(...).suffix` asset test. Today's routes use UUIDs, so this is harmless for now.
- `test_web_serving.py` skips itself when `apps/web/dist` isn't built. CI must build the web app before
  pytest, or those checks never run.
- `week_counts` scans `run_record_transitions` with no index on `at`, and joins `run_records` when the
  transition row already carries `tenant_id`. This is performance only; add the index before 500-row batches
  pile up.
