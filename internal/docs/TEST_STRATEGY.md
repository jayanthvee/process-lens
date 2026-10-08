# Test strategy

Tests prove the constitution, not just the features. Every invariant in [LEDGER_SPEC.md](LEDGER_SPEC.md#invariants-and-the-tests-that-prove-them)
and every rule in [AI_BOUNDARIES.md](AI_BOUNDARIES.md) has a test, and every test runs without real data, secrets, or
network access to a model.

## Layers

| Layer | Where | What it proves | Runs against |
| --- | --- | --- | --- |
| Guards | `tools/guards/`, CI (PL-005) | Import boundaries from the [ARCHITECTURE.md](ARCHITECTURE.md#allowed-dependencies) YAML; model SDKs only in `services/ai`; ledger SQL only in `packages/ledger`; no fixed sleeps; no use-case names in `services/runner`; no stealth or captcha packages; protected paths; leak guard; gitleaks | Source tree |
| Unit | next to each component | Compiler (keystroke merge, variable binding), recipe validation (a commit step without `assert_after` fails), transforms, `match` and `aggregate`, locator scoring, AI output validators (quote exists, arithmetic), router policy | Pure code |
| Database | `tests/db/` (PL-002) | The guard matrix: all 110 ordered state pairs, 28 accepted and 82 rejected. Attempts cap, unique constraints, append-only triggers, `run_summary`, and a two-worker race for every pattern in [sql-patterns.md](sql-patterns.md) | Fresh Postgres 16 per session |
| Contract | `tests/contract/` | Generated Python and TypeScript recipe types match the schema; the `packages/ledger` transition table equals the guard; each AI call type's schemas; the runner↔extension command protocol | Schemas and the database |
| Integration | `tests/integration/` | Runner, database, and a scripted fake executor: canary stop, pause and resume, parking, recovery and every reconcile outcome, approval gates. `services/ai` with recorded provider responses | Postgres, fakes |
| End to end | `tests/e2e/` | Teach, compile, preview, batch, and results on the demo sites through the **real extension**: Playwright launches Chromium with the unpacked extension and drives the web app, and the extension executes. The test never reimplements the executor. Outcomes are checked through each demo site's test API, not its UI | Demo sites, headless Chromium (new headless mode) |
| Break-it | `tests/breakit/` (T1) | Adversarial cases for critical tickets: illegal and unsafe transitions (LEDGER_SPEC L1 to L6), stale-worker writes, two runs on one destination, false verification, origin escape, destructive controls, password capture, prompt injection fixtures | Any layer |

## Drift injection on demo sites

Every demo site has drift switches (the CRM's are in PL-003). Each switch has one expected ledger outcome, and the
drift suite asserts it.

| Drift | Expected outcome |
| --- | --- |
| `rename_save` (button text changes) | The canary fails before any bulk write and the run pauses with `canary_failed`. From Phase 7: a repair proposal, then a new version after approval |
| `required_company` (new required field) | The save is rejected with error text, and the record fails with evidence. No retry loop |
| `slow_save=<ms>` (beyond the assertion timeout) | No duplicate: the record goes to `reconciling` and ends `verified` |
| `session_expired=1` | The run pauses with `login_required` and resumes after login. No duplicate |
| To add (owner of each demo site): stale success toast from the previous record, duplicate-looking second Save button, cookie banner, unknown modal, field moved into an iframe, label renamed, layout shift over the target | Respectively: not verified; parked as ambiguous; handled only if the recipe declares the handler; parked; found through the frame path; found by a lower rung (logged); the commit is not dispatched |

## Kill tests (Phase 3)

These prove constitution rules 7 and 8 and LEDGER_SPEC I7.

- **Where:** fault hooks in the runner (enabled only by a test environment variable) stop the process at each crash
  point in the [write-ahead table](LEDGER_SPEC.md#write-ahead-sequence-around-a-commit). Random `SIGKILL` runs separately.
  Kill targets: the runner process, the extension service worker, the browser tab, and the WebSocket.
- **Acceptance:** 20 kills mid-save across a batch. Then: every destination key appears exactly once through the demo
  test API; every record ends verified, skipped, failed, or parked; nothing remains in a working or commit-window state.
- **Repeat:** the same suite with two workers and a forced worker takeover, once takeover exists (P-001).

## Golden sets

`fixtures/golden/<family>/` holds synthetic documents with expected values and their source quotes. Families: résumés,
invoices (text and scanned), long statements, tickets, and derived-variable cases.

- **Metrics per family:** field accuracy; the share of accepted fields with a verbatim quote (must be 100%); false
  accepts (wrong value accepted without review, target 0); review rate.
- **The OCR bake-off (D-010, Phase 5)** uses the same harness and records its results in a report.
- **In CI** golden sets run against recorded model responses, so they test validators, routing, and thresholds. Live
  model evaluation runs only by hand or on a schedule, with synthetic data, and never on pull requests.

## What CI runs

| Trigger | Jobs |
| --- | --- |
| Every push and pull request | Lint and type checks (ruff, eslint, tsc); guards; gitleaks; unit; database (Postgres 16 service); contract; integration; e2e smoke (one record per demo site); a fast drift subset (`rename_save`, `slow_save`) |
| Nightly and on demand | Full drift matrix; kill tests; golden sets on recorded responses; the race tests repeated 10 times |
| Manual only | Live model evaluation on golden sets; the timing benchmark (Phase 8) |

CI constraints (PL-002): `push` and `pull_request` triggers only; `permissions: contents: read`; no secrets. That rules
out live model calls in pull-request CI, so all AI behaviour in CI comes from recorded responses.

## Rules for test code

- Waits are conditions with timeouts, including in tests (rule 20). A flaky test is a bug to fix, never a retry to add.
- Assertions on a destination use its test API, never a screenshot.
- Each test creates and tears down its own data. Demo sites expose a reset endpoint.
- No test depends on another test's order or on wall-clock timing.
