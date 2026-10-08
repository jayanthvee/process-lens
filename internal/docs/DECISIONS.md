# Decisions

Protected file. Changes follow: proposal → T1 review → owner approval → new entry here.
Entries are never edited after acceptance; a later entry supersedes an earlier one.

| ID | Decision | Why | Status | Date |
| --- | --- | --- | --- | --- |
| D-001 | Deterministic compiled recipe; AI only at the edges, never deciding actions per run | Per-record reliability compounds: 95% per record finishes a 20-row batch cleanly about 36% of the time, 99.9% about 98%. A recipe is auditable and nearly free per record | Accepted | 2026-10-06 |
| D-002 | Elements are found by a semantic target bundle (role + accessible name first); vision only as a fallback | Survives regenerated IDs and class names | Accepted | 2026-10-06 |
| D-003 | Write-ahead ledger, reconcile before any retry, `committed_keys` across runs | Closes the crash-after-Save window; prevents duplicates on re-runs | Accepted | 2026-10-06 |
| D-004 | The run loop lives in the backend; the extension is a thin executor | Chrome suspends idle extension background scripts; one source of truth for state | Accepted | 2026-10-06 |
| D-005 | Attended local runner first; unattended cloud runner later | No stored credentials, inherits SSO and MFA, avoids datacenter-IP bot flags | Accepted | 2026-10-06 |
| D-006 | Input goes through CDP (`chrome.debugger`, via Puppeteer `ExtensionTransport`) | Modern apps ignore synthetic script events | Accepted | 2026-10-06 |
| D-007 | Postgres is both the job queue and the ledger | A claim and its state change commit in one transaction | Accepted | 2026-10-06 |
| D-008 | Recipe versions are immutable; each run pins one | Every past run stays explainable; repairs are reviewable diffs | Accepted | 2026-10-06 |
| D-009 | AI output is data: schema-checked, then approved by a rule or a human; AI gets no tools at run time | Removes prompt injection as a path to action | Accepted | 2026-10-06 |
| D-010 | OCR engine chosen by a one-day bake-off on documents like ours; check licenses first | Benchmarks don't match our documents; licenses differ | Accepted | 2026-10-06 |
| D-011 | Python (FastAPI) backend; TypeScript for UI and extension | Document and ML tooling is Python-first | Accepted | 2026-10-06 |
| D-012 | Idempotency keys are defined per recipe: record key for create workflows, key + new value for update workflows | Re-runs never duplicate a create, while later legitimate updates still apply | Accepted | 2026-10-07 |
| D-013 | DeepSeek V4.1 Flash (XHIGH) in CodeWhale implements all tickets, including critical paths. Claude Opus in Claude Code is architect and reviewer. Critical tickets require T1 review and T1-written break-it tests before merge | Owner's choice. A different model attacks critical code than the one that wrote it | Accepted (owner) | 2026-10-07 |
| D-014 | Two repositories: public `process-lens` holds product code only, with a professional README; private `process-lens-internal` (nested `internal/` folder, gitignored by the public repo) holds constitution, design, decisions, board, tickets, reports. Synthetic data only, secrets never committed, original brief kept in local `private/` | Owner wants a public, professional repo that does not expose internal reasoning, roadmap, or workflow | Accepted (owner) | 2026-10-07 |
| D-015 | License: none yet, so all rights reserved. Options: proprietary, Apache-2.0, or a source-available license | Publishing code under an open license cannot be undone; choosing later keeps every option open | Open: owner to decide | 2026-10-07 |
| D-016 | Monorepo layout: `apps/`, `services/`, `packages/`, `db/`, `fixtures/`, `tools/` public; `internal/` private | Clean separation for a Python + TypeScript system; enables import-boundary checks | Accepted | 2026-10-07 |
| D-017 | Resolutions for the recipe format (PL-004): keep the schema tests permanently in `packages/recipe/tests/`; restrict `commit` steps to `fill`, `select`, `click`, `upload_file` (never `navigate`, which changes no data); no bounded autonomous agent step in v1; `extract` and `classify` are pre-batch staging operations only, so a clean batch run makes no model call; an AI value used in a commit step always sets `requires_approval: true`; `end_record` may end a record `verified`, `skipped`, or `parked`, and `failed` is runner-managed only | ProcessLens is a deterministic compiler, not an autonomous agent: the batch loop stays deterministic and every AI value stays reviewable by a person | Accepted (owner) | 2026-10-08 |
