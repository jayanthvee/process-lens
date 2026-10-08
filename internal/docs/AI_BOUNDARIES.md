# AI boundaries

AI in ProcessLens produces **data, never actions**. This page is the contract for constitution rules 4, 5, 11, and
13. The design rationale is in [SYSTEM_DESIGN.md § Where AI runs](SYSTEM_DESIGN.md#where-ai-runs). The call log
schema is `ai_calls` in [0001_init.sql](../../db/migrations/0001_init.sql).

## The single entry point

`services/ai` is the only code that imports a model SDK or holds a provider key ([ARCHITECTURE.md](ARCHITECTURE.md)).
Its public module exposes exactly the six functions below. There is no generic prompt or completion function. Each
call goes through the same pipeline, in this order:

1. Validate the input against the call type's input schema.
2. Apply the data policy: classify the payload, redact what the call type allows, and choose providers.
3. Call the model with no tools or function calling at any time.
4. Validate the output against the call type's output schema. A failure is logged with `validation = 'fail'`
   and the output is discarded.
5. Run the call type's code check (below).
6. Write the `ai_calls` row, then return a typed result to the caller.

A caller cannot reach a model by any other path, and cannot receive output that skipped steps 4 and 5.

## The six call types

`task` is the value written to `ai_calls.task`. P-001 asks to restrict that column to these six values.

| `task` | Called by | When | Returns (data only) | Gate before the value is used |
| --- | --- | --- | --- | --- |
| `name_steps` | api (compiler) | Build: recording stops | Step names and groups | Code confirms that only labels changed; the user edits in the editor |
| `derive_variable` | api (compiler) | Build: a recorded value has no exact match in the CSV | A transform built from the recipe's closed transform vocabulary. Never code | Code applies it to the sample row; the result must equal the recorded value exactly, or it is rejected |
| `extract_document` | documents | Input preparation: each PDF | Per field: value, verbatim quote, page, box, confidence | The quote exists in the document; format and arithmetic validators pass; low confidence or empty goes to Input Review |
| `classify_ticket` | documents or api | Input preparation: each ticket | A category from the allowed list, confidence, reason | The category is in the allowed list; below threshold goes to review; the recipe's rules table maps category to team |
| `map_value` | api | Input review: a value is not among the dropdown options | One option from the list, or `none` | The user confirms once; saved in `value_mappings` |
| `propose_repair` | runner | Run: the locator ladder is exhausted | One target bundle for one step | Role matches the old fingerprint; not a destructive control; the user approves; the result is a new recipe version |

The table puts extraction and classification in input preparation, before the ledger row exists, so a record between
claim and commit makes no model call. The design also lists `extract` and `classify` as runner steps. P-001 asks the
owner to choose one. Until then, nothing calls a model between `claimed` and `verified` except `propose_repair`, which
parks the record.

## Router and escalation cascade

The router picks providers per call. It reads the tenant's `data_policy` (`tenants.data_policy`; the default allows
personal data only on `local`).

1. **Classify the payload.** `personal` if it can carry personal data after redaction; otherwise `non_personal`.
   Extraction, value mapping, and repair snapshots are always `personal`.
2. **Filter providers.** A `personal` payload goes only to providers listed in `data_policy.pii_providers`. A tenant
   marked synthetic-only may use any configured provider. Synthetic-only is an explicit policy flag, never inferred
   from the data.
3. **Order by cost.** Start with the cheapest allowed model that is adequate for the call type.
4. **Escalate on failure.** If the output fails the schema or the code check, or confidence is under the threshold,
   retry on the next allowed, stronger model. Each retry is a new `ai_calls` row with `escalated_from` set.
   Escalation never crosses the data policy. The cascade stops after two escalations.
5. **Exhausted or unavailable.** The value goes to a person, or the record parks. The router never returns a best guess.
   During a provider outage, only records that need an AI value park. Deterministic records continue.
6. **Cache.** A cached response is reused only if it passed validation. The cache key is tenant, task, model, prompt
   version, and input hash.

## Personal data rules

- Synthetic data only in both repositories and on demo tenants (rule 21). Real personal data does not enter
  `ai_calls`, `step_events`, or `committed_keys` until the retention issue in P-001 is decided. All three tables are append-only.
- Never sent to any model: passwords, one-time codes, cookies, tokens, session storage, or the value of any password or
  hidden input. Accessibility snapshots strip input values before they reach the router.
- Redact what the call type allows. Step names see placeholders, not values. Ticket text has emails and phone numbers masked.
- `ai_calls.request` is stored after redaction. Screenshots are masked before upload.
- Provider choice follows the tenant policy alone. Model quality never overrides it.

## What AI may never do

1. Choose, order, or perform a browser action, or receive tools, at any time (rules 4 and 5).
2. Decide whether a record is a duplicate, or whether a commit succeeded. Precheck, reconcile, and verification are code.
3. Write to any table other than through its typed return value. `services/ai` itself writes only `ai_calls`.
4. Change a recipe, or approve its own proposal. Every repair and every new version needs a person (rule 11).
5. Return code to execute: no scripts, SQL, selectors to evaluate, or regular expressions. Transforms come from a closed vocabulary.
6. Propose a target that is a delete, cancel, refund, pay, or transfer control.
7. Have a value under its confidence threshold used without a person.
8. Receive data outside the tenant's policy, or be called from `apps/`, the extension, or any module other than `services/ai`.
9. Bypass output schema validation (rule 13).
10. Solve or help bypass a CAPTCHA or bot challenge (rule 19).
11. Follow instructions in page text or documents. They are input data, and the output schema leaves no room to act on them.
12. Generate free text that is submitted without approval. PL-004 lists AI-generated text as a value source; that is a
    seventh call type and needs an owner decision (P-001).
