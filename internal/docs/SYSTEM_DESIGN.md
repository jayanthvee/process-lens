# ProcessLens — System Design

Oct 6, 2026 · @jv

## Summary

ProcessLens turns one recorded demonstration into a versioned **recipe**, then runs that recipe once per input record. A **ledger** guarantees every record ends verified, skipped, failed, or parked for a human, and is never submitted twice.

The product is three machines in sequence:

1. **A recorder** (Chrome extension) that captures what each element *means* (its role, label, and form), not where it sat on screen.
2. **A compiler and editor** that turn the recording into a recipe: a typed JSON program the user can read, edit, and approve.
3. **A job runner** that executes recipe × records deterministically, writes every state change to the ledger, and calls AI only at a few defined edges.

A clean record uses zero LLM calls. AI helps author the recipe, read documents, and propose repairs, but code and humans make every decision that changes data.

This is deliberately different from agent products such as Grok Bot and Claude in Chrome shortcuts. Those save a prompt and let a model re-decide every click on every run. ProcessLens saves a program and only asks a model for help when the program can't proceed.

## Architecture overview

The backend owns all state and decisions; the Chrome extension only records demonstrations and executes one step at a time when told to.

&#91;embedded content: ProcessLens architecture · 9 components, 3 tiers\]

Two boundaries matter most. The extension never decides what to do next: the run worker sends one step, the extension performs it and reports what the page shows, and the worker checks that against the recipe. The AI service is the only component that talks to a model, so every AI call is logged, validated, and routed by data policy in one place.

## End-to-end flow

One customer-inquiry CSV (use case 1) passes through six phases; only phase E touches the CRM in bulk, and only after one previewed record succeeds.

**A. Teach** (extension + API)

1. The user logs into the demo CRM manually, then clicks **Teach a task** in the web app and picks the CRM tab.
2. They demonstrate one record: search by email, see no match, click New lead, fill name, email, phone, and interest, choose a salesperson, click Save.
3. For every click, keystroke, and choice, the recorder saves a fingerprint *at that instant*: role, accessible name, label, enclosing form or dialog, test ID if any, nearby text, iframe path, URL. It also notes what changed right after (URL change, a "Lead created" message). Password fields are never recorded.
4. Events stream to the API over a WebSocket and are stored as the raw event log.

**B. Build** (compiler + editor)

5. The compiler, in plain code, merges keystrokes into single fill steps, drops noise, splits steps by page, and turns observed changes into suggested assertions.
6. The user uploads a sample CSV. Recorded values that exactly match row 1 become variables such as `{{row.email}}`.
7. Two small AI calls run: one names and groups the steps; one proposes transforms only for values with no exact match. Each proposed transform must reproduce the recorded value in code or it is rejected.
8. In the workflow editor the user sets the record key (email), adds the branch "search finds a match → skip as duplicate", adds the rule "missing email → park", and marks Save as a commit step.
9. Saving creates **recipe v1**, an immutable version.

**C. Input review**

10. The user uploads the 20-row CSV. Each row is validated against the recipe's input schema; duplicate keys inside the file and rows missing an email are flagged before anything runs.

**D. Preview**

11. The user runs one record. The executor stops before the commit step and shows the values it is about to submit.
12. The user approves; Save is clicked; the "Lead created" assertion passes; the record is verified.

**E. Batch run** (orchestrator + extension executor)

13. The orchestrator creates one ledger row per CSV row, keyed by normalized email.
14. Record 1 is the **canary**: every locator and assertion is checked before records 2–20 start.
15. For each record: claim it, run the duplicate search, fill fields, write `submitting` to the ledger, click Save, write `submitted_unverified`, check the assertion, write `verified`.
16. Duplicates end as `skipped` with the reason; missing emails end as `parked`; a locator that can't be resolved parks that record while the batch continues.

**F. Results**

17. The results screen shows counts and a reason per record, with step timings and failure screenshots. Parked records can be corrected and re-queued.
18. If anything crashed mid-run, resume reads the ledger first: records stuck in `submitting` or `submitted_unverified` are reconciled by searching the CRM before any retry.

## The recipe

The recipe is a versioned JSON document and the single source of truth for what a workflow does; the compiler writes it, the editor changes it, the runner executes it.

It has three parts: **meta** (name, when to use it, input schema, record key, approval policy), **variables** (bound to input columns or extracted values), and **steps**. The format borrows from three public designs: the step and selector-array layout of the [Chrome DevTools Recorder](https://developer.chrome.com/docs/devtools/recorder/reference), the semantic fallback order of [workflow-use](https://pypi.org/project/workflow-use/), and the header fields of a [Grok Bot skill](https://docs.x.ai/grok-bot/skills-routines-and-automations).

| Step type | Runs in | Used by |
| --- | --- | --- |
| `navigate`, `fill`, `select`, `click` | Browser | All browser use cases |
| `upload_file` | Browser | 2 (attach résumé) |
| `read_value` | Browser | 4 (old price), 5 (source order), 9 (free slots) |
| `wait_for`, `assert` | Browser | Every commit step |
| `download` | Browser + storage | 8 (reports) |
| `branch` | Runner | 1, 3, 6 (exists → skip) |
| `for_each` | Runner | 5 (order line items) |
| `transform`, `match`, `aggregate` | Runner (plain code) | 8, 10 |
| `classify`, `extract` | Runner → AI service | 2, 3, 7 |
| `approval_gate` | Runner → user | Any commit the policy marks |
| `end_record` | Runner | Skip or park with a reason |

**Targets.** Each element step stores an ordered bundle of ways to find its element: role + accessible name inside a named container, test ID, label link, stable CSS, then fuzzy text. A target counts as found only when exactly one visible, enabled element matches.

**Commit steps.** Any step that changes data in the destination is marked `commit: true` and must carry an `assert_after` check, such as a confirmation text or a URL change. The ledger treats commits differently from every other step.

**Versions.** Versions are immutable. A run pins one version; an edit or an approved repair creates the next version with a recorded reason, and keeps the old fingerprint beside the new one.

```json
{
  "meta": { "record_key": "{{row.email | lower}}", "approval": "first_record" },
  "steps": [
    { "id": "save", "action": "click", "commit": true,
      "targets": [ { "by": "role", "role": "button", "name": "Save", "within": "form:New lead" },
                   { "by": "testid", "value": "lead-save" },
                   { "by": "text_fuzzy", "value": "Save" } ],
      "assert_after": { "text_visible": "Lead created", "timeout_ms": 8000 } }
  ]
}
```

## The run ledger

Each record moves through a fixed set of states, and every state is written to Postgres *before* the action it describes, so a crash can always be resumed safely.

&#91;embedded content: Ledger states for one record · 7 main states, 4 exits\]

**Write-ahead.** `submitting` is saved before Save is clicked; `submitted_unverified` right after. A crash in either state means "we don't know if the CRM received it," so resume never clicks Save again blindly. It first re-runs the duplicate search: found means verified, not found means retry, up to the attempt limit.

**The destination is the source of truth.** The ledger records intent and outcome; the CRM's own search decides what really happened. This only works if each record has a key the destination can be searched by. Where a system has none, the recipe writes our record key into a reference or notes field.

**Two layers of uniqueness.** One record key per run stops a CSV with duplicate rows from submitting twice. `committed_keys`, unique per destination, stops a later re-run from creating the same record again, which is the brief's test for use case 5.

**Parked records** can be fixed by a person and sent back to `pending`. Records are processed one at a time per destination by default; that serialization is what prevents two appointment requests from booking the same slot.

## Where AI runs

All LLM calls go through one backend module, the AI service; there are six call types, and none of them can click, submit, or decide whether a record is a duplicate.

| Call | Triggered by | Model gets | Model returns | Code check before it is trusted |
| --- | --- | --- | --- | --- |
| Name and group steps | Recording stops | Cleaned step list | Names, groups | User edits in the workflow editor |
| Derived variable | A recorded value with no exact CSV match | The value, CSV headers, sample row | A transform (e.g. first word of Full name) | Code runs it; output must equal the recorded value |
| Document extraction | Each uploaded PDF | Structured text with boxes, field schema | Field values, each with its source quote | Quote must exist in the document; format and arithmetic validators; low confidence → review |
| Ticket classification | Each ticket (use case 7) | Ticket text, allowed categories | Category, confidence, reason | Must be an allowed category; below threshold → review queue |
| Value mapping | An input value not among dropdown options | The value, the option list | One option or "none" | User confirms once; saved to a mapping table |
| Repair proposal | Element-finding ladder exhausted | Accessibility snapshot of the region, step description, old fingerprint | One element | Role must match; never a delete/cancel/pay control; user approves |

The AI service owns four things no other component does: API keys, a log of every prompt and response (the `ai_calls` table), redaction of personal data where the call allows it, and a **router** that picks the model per call type and per customer data policy. Your available models plug into that router: Claude for build-time reasoning, the local Qwen for documents that must not leave the customer's network, and DeepSeek for synthetic demo and test data until a residency policy is settled.

AI is never given tools at run time. Its output is data that passes a schema check and then a human or a rule, so page text or a hostile PDF can at worst produce a wrong suggestion that someone reviews.

## Document pipeline

A PDF becomes input data only after every field is backed by a quote from the document and has passed code validation; anything else lands in Input Review instead of a run.

1. **Ingest.** Store the file, hash it, and reject an exact duplicate upload.
2. **Text layer or OCR.** If the PDF has embedded text, read it with positions. If it is a scan, run OCR. Either way the output is *grounded structure*: text, reading order, and bounding boxes. Boxes are what let Input Review highlight where a value came from.
3. **Extract.** One AI call per document turns that structure into the field schema (résumé: name, email, phone, skills; invoice: supplier, number, date, line items, total). Every field must return the exact snippet it came from; a field it cannot find returns empty.
4. **Validate in code.** The snippet must exist in the document. Emails and phones must match patterns, dates must parse, and invoice line items must add up to the total. Invoice numbers are normalized ("INV-042" and "42") before the duplicate check.
5. **Review.** Empty, failed, or low-confidence fields are shown beside the highlighted page. Only approved values become a record's input.

**Short versus long documents.** Résumés and invoices are short and holistic: a label on page 1 can belong to a value on page 2, and tables split across pages. They are extracted whole. Long repetitive documents, such as bank statements or exported reports, use map-reduce per page, and the reduce step must check an invariant (running balances chain; opening balance plus transactions equals closing).

**Choosing the OCR engine.** Decide by a one-day bake-off: 50–100 pages that mirror the use cases, 5–10 candidate engines, outputs diffed and failures inspected, as described in [Isaac Flath's write-up of Joe Barrow's talk](https://isaacflath.com/writing/how-to-choose-an-ocr-model). The same source puts big-cloud OCR at about $0.60–$1.50 per 1,000 pages and document startups at about $5–$20, and advises most product teams to use an API rather than self-host. Check licenses first: Datalab's Chandra and Surya weights are free only below $2M in both revenue and funding, while LightOnOCR (Apache) and GLM-OCR (MIT) are unrestricted.

## Failure handling

Every failure ends one record in a named state with evidence, and only a lost session or a challenge pauses the whole batch.

| Failure | Detected by | Response |
| --- | --- | --- |
| Element not found or ambiguous | Locator ladder: role+name → fallbacks → scored fuzzy match, unique match required | AI repair proposal; record parked until approved; batch continues |
| Site changed overnight | Canary record checks every locator and assertion first | Run stops before any bulk write; user re-teaches the broken section |
| Save rejected (e.g. new required field) | `assert_after` times out; page error text captured | Record failed with the error text and screenshot |
| Login page or session expired | Page-state check: login URL pattern or password field present | Whole run pauses; user logs in; run resumes |
| CAPTCHA or MFA prompt | Challenge detector | Whole run pauses for the human; never bypassed |
| Unexpected popup | Step precondition fails | Known dismissals (cookie banner) run as handlers; otherwise park |
| Slow page | `wait_for` on a condition with a timeout, never fixed sleeps | Retry the wait once, then fail the step |
| Crash during a commit | Ledger shows `submitting` or `submitted_unverified` | Reconcile by searching the destination before any retry |
| Bad input (missing field, unknown dropdown value) | Input schema and rules | Park, or apply the saved value mapping |
| Download failed (use case 8) | Missing or empty file | Report marked incomplete, never silently partial |
| AI provider down | AI service timeout | Only records needing an AI step park; deterministic records continue |

## Data model

Postgres holds all state, and two unique constraints do the most important work: one record key per run, and one committed key per destination across all runs.

| Table | Holds | Constraint that matters |
| --- | --- | --- |
| `tenants`, `users` | Tenant and its data policy for the AI router; users and roles | Unique (tenant, email) |
| `workflows` | Name, destination system, allowed origins | Unique (tenant, name) |
| `recipe_versions` | Recipe JSON, parent version, change reason | Unique (workflow, version); updates and deletes rejected |
| `recordings` | Raw event log from Teach | — |
| `files`, `documents` | Upload metadata and hash; text-layer or OCR result, with grounded structure in object storage | Hash index catches duplicate uploads |
| `input_batches`, `input_records` | Uploaded batch; per row the raw values, accepted clean values, record key, issues | A row can't be `ready` without a record key |
| `field_proposals` | Every proposed field value (from code, a mapping, AI, or a person) with evidence quote, page, box, and confidence; the Input Review screen reads this | AI proposals must reference an `ai_calls` row |
| `value_mappings` | Input value → dropdown option, per field | Unique (workflow, field, input value) |
| `runs` | Pinned recipe version, mode, status, pause reason, worker heartbeat | — |
| `run_records` (the ledger) | State, attempts, reason, destination reference | Unique (run, record key); transitions enforced by trigger; attempts capped |
| `committed_keys` | Every verified commit | Unique (tenant, destination, idempotency key); append-only |
| `step_events` | Every step attempt: timing, locator rung, observation, screenshot | Append-only |
| `heal_proposals` | Old and proposed target, source, approver, resulting version | An approval requires a new recipe version |
| `ai_calls` | Task, provider, model, redacted request, response, validation, cost, escalation link | Append-only |
| `jobs` | Background work (OCR, input cleaning) | Claimed with `SKIP LOCKED` |

The full schema is `db/migrations/0001_init.sql` and the approved query shapes are in `docs/sql-patterns.md`. Three rules are enforced by the database itself, so an application bug cannot break them:

- The ledger accepts only legal state transitions. A blind retry (`submitting → pending`) is rejected; a crash must pass through the `reconciling` state.
- `committed_keys` uses an idempotency key defined by the recipe: the record key for create workflows, and key plus new value for update workflows. The same change applies once, but a later change to the same record is still allowed.
- Recipe versions, committed keys, step events, and AI calls can never be updated or deleted.

## Security and trust boundaries

Safety is enforced in code at the executor, never by asking a model to behave, and ProcessLens never stores a password.

- **Credentials.** Users log in themselves; password inputs are masked at capture and never stored. The local runner acts inside the user's existing session.
- **Site scope.** A workflow lists its allowed origins. The extension requests host access only for those, and the executor refuses any action on another origin.
- **Destructive controls.** The executor blocks clicks on elements labelled delete, cancel, refund, pay, or transfer unless the recipe step explicitly allows it and the run's approval policy has been met.
- **Prompt injection.** Page text and documents are data. AI has no action tools at run time, and its outputs are schema-checked and approved, so injected instructions cannot trigger an action.
- **Personal data.** Screenshots and documents contain PII, so retention is configurable with a short default. The AI router sends a call only to providers allowed by the customer's data policy.
- **Audit.** `step_events` and `ai_calls` are append-only. Every record's outcome can be traced to the recipe version, steps, and evidence that produced it.
- **Tenants.** Every table carries a tenant ID; Postgres row-level security is the next layer.
- **Bot detection.** No evasion: no fingerprint spoofing, stealth plugins, or captcha solving. Challenges pause the run for a human. A future cloud runner should identify itself through Cloudflare's [Verified bots program](https://developers.cloudflare.com/bots/concepts/bot/verified-bots/), which requires honest self-identification and non-abusive behaviour.

## Deployment

Ship the attended local runner first and the unattended cloud runner second; the recipe, ledger, and backend are identical for both, and only the executor changes.

**Phase 1: attended local runner.** The extension drives the user's own Chrome through the `chrome.debugger` API, which sends trusted input events that React-style apps accept. Runs use a dedicated browser window, because input sent to a background tab can stall and the debugger shows a visible "being debugged" bar. The user's own session, SSO, and MFA are inherited, so no credentials are stored and the traffic looks like what it is: an employee working. The cost: the laptop must stay on and that window is reserved during a run.

**Phase 2: unattended cloud runner.** Playwright workers in containers, one persistent browser profile per customer, a credential vault, and fixed egress IPs that customers can allowlist on their internal portals. This is what lets runs happen overnight, and it is where bot identity and credential storage become real work.

**Student or demo scope.** Everything runs locally with Docker Compose: Postgres, MinIO for files, the API, the run worker, and the five demo sites (CRM, recruitment portal, accounting portal, shop, scheduler). The extension is loaded unpacked. The same Playwright code doubles as the automated test harness against the demo sites, including deliberately injected drift.

## Defensible decisions

The central decision is a deterministic recipe with AI at the edges, because per-record reliability compounds: at 95% per record, a 20-row batch finishes cleanly only about 36% of the time (0.95²⁰); at 99.9%, about 98%.

| Decision | Chosen | Rejected | Why | Cost we accept |
| --- | --- | --- | --- | --- |
| Execution model | Compiled recipe, AI only on exceptions | An agent re-deciding each run | On a live benchmark of 300 real tasks, frontier agents scored far below their self-reported \~90%, with OpenAI's Operator at 61% ([Online-Mind2Web](https://arxiv.org/html/2504.01382v4)); a recipe is auditable and costs about nothing per record | Structural site changes need a re-teach or an approved repair |
| Finding elements | Semantic target bundle, role + name first; vision only as a fallback | CSS/XPath selectors; screenshots as the main input | Survives regenerated IDs and class names; Chrome's Recorder and workflow-use both moved to semantic-first ordering | Depends on reasonably accessible markup; canvas apps unsupported |
| Duplicate safety | Write-ahead ledger + reconcile before retry + `committed_keys` | Retry on failure | Closes the crash-after-Save window; UiPath users report items stuck "In Progress" that a uniqueness rule then blocks from retry ([forum](https://forum.uipath.com/t/clone-remove-in-progress-queue-items/624169)) | Every workflow needs a key that can be searched in the destination |
| Where the run loop lives | Backend orchestrator; extension is a thin executor | Loop inside the extension | Chrome suspends idle extension background scripts; one source of truth for state | A live WebSocket per run; small latency per step |
| First runner | Attended, in the user's Chrome | Cloud browser | No stored credentials; inherits SSO and MFA; avoids datacenter-IP bot flags | No overnight runs in phase 1 |
| Input events | `chrome.debugger` trusted input | Script `.click()` and value setting | Modern apps ignore untrusted events | Visible debugging bar; dedicated window |
| Queue and state | Postgres for both job claims and the ledger | Separate broker (Redis, Celery) | A claim and its state change commit in one transaction | Lower ceiling on throughput than a broker, likely far above our volume (unmeasured) |
| Recipe changes | Immutable versions, runs pin one | Edit in place | Every past run stays explainable; repairs are reviewable diffs | More rows; a migration path when the format changes |
| AI authority | Output = data, schema-checked, then approved | AI with tools at run time | Removes prompt injection as an action path | More review steps for users |
| OCR | API chosen by a one-day bake-off; self-host only for residency | Pick a model from benchmarks | Benchmarks don't match your documents; licenses differ | One day of evaluation per document family |
| Backend language | Python (FastAPI) | Node | OCR, PDF, and ML libraries are Python-first; Playwright supports Python | Two languages overall (TypeScript UI and extension) |

## Use case coverage

All ten use cases run on the same engine; four of them need features beyond plain record-and-replay (read steps, loops, downloads, data steps).

| # | Use case | Engine features beyond fill/click | Fit |
| --- | --- | --- | --- |
| 1 | Customer inquiries | `branch` on search result; match rules; ambiguous → park | Good |
| 2 | Candidates | `extract` from résumé; `upload_file` | Good |
| 3 | Invoices | `extract` with OCR path; normalized duplicate key; `approval_gate` | Medium |
| 4 | Product updates | `read_value` for old price; diff shown before commit | Good |
| 5 | Order transfer | `read_value` on the source site; `for_each` over line items; source order ID stored in the destination | Hard |
| 6 | Enrollment | Locate-or-create `branch`; duplicate-enrollment check | Good |
| 7 | Ticket routing | `classify`; category → team rules table | Mostly AI |
| 8 | Report collection | `download`; date-range picker; `aggregate` in code; incomplete-report flag | Hard |
| 9 | Appointments | `read_value` on calendar slots; re-check slot right before booking; one record at a time | Hard |
| 10 | Reconciliation | `match` with amount and reference tolerance; browser applies only confirmed updates | Mostly data |

## Build order and open questions

Build the recipe schema and ledger first, because every other component reads or writes them.

1. Demo CRM, recipe JSON schema, and ledger tables, with concurrency tests for claims and commits.
2. Recorder and compiler for fill, click, and select; the extension executor; a single-record preview run.
3. Ledger state machine, reconcile, canary record, results screen. This completes use cases 1, 4, and 6.
4. Branches, `read_value`, and `for_each` (use cases 5 and 9).
5. Document pipeline after the OCR bake-off (use cases 2 and 3).
6. Data steps: `transform`, `match`, `aggregate`, `download`, `classify` (use cases 7, 8, 10).
7. Repair ladder and AI proposals, tested by injecting drift into the demo sites.
8. Timing instrumentation for the manual-versus-automated comparison the brief requires.

Open questions:

- [ ] Is the target a student prototype on demo sites only, or a hosted commercial backend from the start?
- [ ] Which AI providers may receive customer personal data, given DeepSeek's data residency?
- [ ] Does every destination expose a searchable key, or must recipes write our record key into a reference field?
- [ ] Default approval policy: first record only, or every commit for the first N records?

Sources: [Chrome DevTools Recorder reference](https://developer.chrome.com/docs/devtools/recorder/reference) · [workflow-use](https://pypi.org/project/workflow-use/) · [Grok Bot skills and routines](https://docs.x.ai/grok-bot/skills-routines-and-automations) · [Grok Bot computer and apps](https://docs.x.ai/grok-bot/computer-and-apps) · [UiPath queue item data model](https://docs.uipath.com/insights/automation-cloud/latest/user-guide/real-time-data-export-data-model) · [UiPath forum: stuck In Progress items](https://forum.uipath.com/t/clone-remove-in-progress-queue-items/624169) · [How to Choose an OCR Model](https://isaacflath.com/writing/how-to-choose-an-ocr-model) · [An Illusion of Progress? (Online-Mind2Web)](https://arxiv.org/html/2504.01382v4) · [Building Browser Agents (production paper)](https://arxiv.org/html/2511.19477v1) · [Cloudflare Verified bots](https://developers.cloudflare.com/bots/concepts/bot/verified-bots/)
