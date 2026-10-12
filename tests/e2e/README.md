# End-to-end tests (PL-014)

The end-to-end harness drives the **real** Chrome extension in a real Chromium against the
synthetic lead-CRM fixture, with the real API, hub, runner worker and Postgres. It proves the
acceptance criteria that a unit test cannot: a batch that saves three leads, a disconnect, a
crash and takeover, a challenge wall, a login wall, an off-origin page, a destructive control,
and a run that idles past Chrome's MV3 suspension window.

## The exact command

```
$env:PROCESSLENS_E2E="1"; .\.venv\Scripts\python.exe -m pytest tests\e2e -q -m e2e
```

The fixture builds `apps/extension/dist` for you if it is missing (`npm --prefix apps/extension
run build`). A plain `pytest -q` collects nothing here: without `PROCESSLENS_E2E=1` the module
guard ignores every `test_*.py`, so the default suite stays fast and green and never imports
Playwright.

## Why it is out of pull-request CI

It needs a browser, a full stack (Postgres, the API and hub under uvicorn, a runner worker, the
extension), and about a minute per run - the keepalive case alone idles for over thirty seconds.
That is the same reason the existing Node browser smoke test (`apps/extension/scripts/e2e-smoke.mjs`)
is out of CI. Run it on demand, or nightly.

## The `PLAYWRIGHT_BROWSERS_PATH` rule (R9)

`tests/e2e/conftest.py` sets `PLAYWRIGHT_BROWSERS_PATH` to `.cache/ms-playwright/` **at the top of
the module, before Playwright is imported anywhere**. Every download (`playwright install chromium`)
and every run therefore write inside the repository and never to `%LOCALAPPDATA%\ms-playwright`.
`.gitignore` carries `.cache/ms-playwright/` so the browser is never committed. If the browser is
missing, a session fixture installs it with that variable already set; it never installs to the
default location.

`channel="chromium"` selects Playwright's bundled build. Branded Chrome and Edge no longer load an
unpacked extension from the command line, which is why the harness does not use them.

## What each test proves

| file | test | the criterion it proves |
|---|---|---|
| `test_batch_real_extension.py` | `test_a_five_record_batch_ends_three_verified_one_skipped_one_parked` | a five-record batch ends 3 `verified`, 1 `skipped`, 1 `parked` |
| | `test_each_email_exists_exactly_once_in_the_fixture` | the fixture's test API shows each email exactly once (no duplicate) |
| | `test_a_stale_toast_does_not_verify_the_record` | a stale "Lead created" toast leaves the record unverified |
| `test_disconnect_mid_save.py` | `test_a_disconnect_mid_save_parks_the_record_without_a_duplicate` | a mid-save disconnect parks the record, no duplicate, run paused `executor_disconnected` |
| | `test_reconnecting_and_resuming_continues_the_run` | reconnecting and resuming continues the run |
| | `test_a_runner_crash_and_takeover_parks_without_a_duplicate` | a crash and takeover parks (`ParkingReconciler`), no duplicate |
| `test_challenge_and_login.py` | `test_a_challenge_page_pauses_the_run_with_challenge` | a challenge page pauses the run `challenge`, no record `failed` |
| | `test_a_login_page_pauses_the_run_with_login_required` | a login page pauses the run `login_required`, no record `failed` |
| `test_off_origin_and_destructive.py` | `test_an_off_origin_page_parks_the_record` | an off-origin page parks the record |
| | `test_a_destructive_control_parks_the_record` | a destructive control parks the record |
| `test_keepalive.py` | `test_the_run_survives_a_thirty_five_second_idle_wait` | the run survives an idle wait of over thirty seconds |

Every assertion reads the fixture's test API (`GET /__test__/state`) or the ledger's own endpoints
(`/api/v1/runs/{id}`, `/api/v1/runs/{id}/records`); none reads a screenshot.

## Known blocker against a green run (2026-10-12)

The harness itself stands the whole stack up - the fixture site, a scratch Postgres, Playwright's
bundled Chromium with `apps/extension/dist` loaded, the real extension id, the API and hub under
uvicorn, the popup's **Connect** reaching `ready`, and a runner worker that claims a run and drives
it. Against the current revision every command is nonetheless refused by the extension with
`the command expired before it was received`, so the batch parks every record.

The cause is outside this harness, in `services/runner/browser_executor.py`: line 94 defaults
`self._now` to `time.monotonic()`, and line 262 mints
`expires_at = int((self._now() + ttl) * 1000)` - monotonic seconds times 1000, not epoch
milliseconds as the protocol requires. The extension compares that against `Date.now()`
(`command-session.ts`: `now: () => Date.now()`), where `expires_at` is ~10^7 against ~1.8 x 10^12,
so every command looks stale. Fixing it is a one-word change in the runner
(`time.time()` for the wire expiry, or a separate wall clock for `expires_at`), and it is owned by
the runner change, not by `tests/e2e`.

## The fixture rows

The batch is five rows: the one `ana@example.test` the fixture already holds (the recipe's search
branch ends it `skipped`), three brand-new leads with a company (three `verified`), and
`el@example.test` with an empty company, which the recipe's own `fill-company` branch parks. See
the note in the PL-014 report: the ticket's wording is "one missing email", but the fixture recipe
renders `record_key` from the email (`{{row.email | lower}}`), so a row with no email is refused at
run creation (`CreationError: a rendered key is empty`) and can never reach the ledger. The parked
record is therefore the one the recipe can actually park - the no-company row - which keeps the
required outcome (3/1/1) and stays honest about the recipe as written.
