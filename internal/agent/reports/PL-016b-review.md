# Review PL-016b: rulings on the stop

Status: rulings given, continue · Reviewer: T1 · Reviewed commit: 7c801b3 (branch `pl-016b`)

T2 stopped correctly. Both golden failures are real product gaps, and they were reported with evidence instead of
being patched around. That is what this ticket was for.

## Ruling 1: requirement 1's pass condition was wrong. Restate it.

- **The 3/1/1 expectation assumed a duplicate-search `branch` and input rules.** In the product design
  (SYSTEM_DESIGN step 8) a person adds those in the workflow editor, after recording. There is no editor yet, so a
  compiled recipe is linear by design. Expecting a `skipped` from it was T1's error in the ticket.
- **The safety property still holds without the branch, and that is what this test must prove:** an existing lead
  is never created twice.
- **The restated pass condition** for the 5-row batch:
  - The rows are ordered so the first row (the canary) is a new lead.
  - The 3 new rows are `verified`.
  - The existing lead (`ana`) and the missing-email row each end `parked`, with a reason.
  - Nothing is `failed`.
  - The fixture's test API shows **each email exactly once**, and `ana` still exactly once.
- **The canary rule is correct as it is.** A parking canary pausing the run is the designed behaviour. Do not change
  it.
- Skipping duplicates from a compiled recipe needs the duplicate-check options in **Make recipe**. That is
  **PL-017**, which T1 will write. It is not part of this ticket.

## Ruling 2: fix the field-name slug (product change, authorized)

- **The bug:** the popup turns "Add recipient" into `add-recipient`, which the schema's `^[a-z][a-z0-9_]{0,63}$`
  refuses. This is a PL-016 requirement-2 bug, and it is fixed in this ticket.
- **The fix:**
  - The slug lowercases the name and maps every run of characters outside `[a-z0-9]` to a single `_`.
  - It strips leading and trailing `_`, and prefixes `f_` if the result does not start with a letter.
  - It truncates to 64 characters.
  - It de-duplicates collisions with `_2`, `_3` and so on.
- **Tests (vitest):**
  - "Add recipient" → `add_recipient`;
  - "2nd email" → `f_2nd_email`;
  - two "Email" fields → `email` and `email_2`;
  - every output matches the schema pattern.

## Ruling 3: the two adjacent defects (product changes, authorized)

- **`POST /api/v1/runs` returns 500 on a duplicate `record_key` in a batch.** It must be a 422 listing each duplicated
  key and its row numbers (SYSTEM_DESIGN step 10: duplicate keys are flagged before anything runs). Catch the
  violation, or better, detect duplicates before inserting.
  - **Test:** a batch with two rows sharing an email gets a 422 naming the email and both rows, and no run is
    created.
- **The Make recipe form's default record key.**
  - Default to the field whose name or label says email. Otherwise default to the first recorded value that looks
    like an email address.
  - Otherwise, there is no default and the person must choose; the form cannot submit until one is chosen.
  - Never default to the first recorded value.
  - **Tests (vitest):** each of the three cases.

## Notes

- **The e2e budget overrun** (4 runs of one file) is accepted, because each run hit a distinct defect and it was
  disclosed. The budget for the rest of this ticket: **at most two** runs of each golden file, then one full-suite
  run.
- **The profiles' Anthropic authorization failure** is an operator setup issue, and T1 and the owner will fix it.
  The fallback to `deepseek-flash` is fine for this ticket.

## Instructions for T2

1. Apply Rulings 1–3: update `test_record_compile_run.py`'s pass condition and row order; fix the slug, the
   `POST /runs` 422 and the default record key, each with the tests named.
2. Run the non-e2e gates.
3. Run each golden file at most twice, then one full-suite run, reported as it shows.
4. Add a "Review fixes" section to `internal/agent/reports/PL-016b.md`. Commit it with this review, push, and stop
   for T1.
