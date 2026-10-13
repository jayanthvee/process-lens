# Review PL-017

Status: **approved for merge**, with one high-priority follow-up (PL-017b) · Reviewer: T1 · Reviewed commit: a872ae9
(branch `pl-017`)

## Gates (run by T1 on a872ae9)

```
python -m pytest -q                       644 passed
ruff check . / ruff format --check .      clean / 186 files already formatted
npm --prefix apps/extension run check     284 tests passed, build ok, validation passed
npm --prefix apps/web run check           passed
python fixtures/sites/messy-crm/selftest.py   SELFTEST PASSED (77 checks)
e2e full suite (T2)                       14 passed
```

## Verdict

- **D-019** is recorded.
- **Claim order** follows `input_records.row_index`, with fencing and `SKIP LOCKED` kept, so row 0 is the canary.
- **The duplicate check** is built only from a recorded search, never invented. The reviewer's wizard false-positive
  (`fill → Next`) is fixed, with tests. An unanswered check still parks: R1 is untouched.
- **Required-field park rules** work as specified.
- **The lead-crm golden test** is back to 3/1/1 from a real recording, with no automatic `canary_failed` resume.
- **The messy golden test** passes and its `xfail` is gone.

## The finding that matters: a click can land on whatever moved under it (PL-017b)

T2's requirement-3 diagnosis is correct, and it exposes an executor weakness that has been in `main` since PL-010.
**It is not new in this branch.**

- **What `dispatchClick` does today:**
  1. `measureTarget` checks that the target is hit-testable at measure time (`elementFromPoint`, in `ladder.ts`).
  2. The mouse events are then sent to those coordinates.
- **The gap:** nothing checks that the element is still at that point when the press lands. If the page reflows in
  between (an autocomplete list opening, a banner, a late-loading image), the press goes to whatever is now there.
  - In the fixture, that was empty space, so the record parked.
  - **On a real site it could be a different control,** possibly a commit control. That breaks the guarantee that
    the element the guards approved is the element acted on.
- **T2 made the fixture's listbox an overlay.** That is acceptable for this ticket's scope, but real sites do reflow.
  So PL-017b must restore an in-flow variant (a fixture switch) and fix the executor.

### PL-017b requirements (T1 will write the ticket)

1. **Stability.** Before any press, the target's box must be the same on two consecutive measurements one animation
   frame apart. If it moves, re-measure, up to the step's timeout. If it never settles, park, never click.
2. **The landing check.**
   - Immediately before the press, hit-test the point again. If it is not the target or a descendant, re-measure.
   - In the page, a one-shot capture listener records which element received the `mousedown`.
   - If it was not the target:
     - a non-commit step parks, with reason "the click landed on a different element";
     - a commit step reports `lost`, so the runner reconciles.
3. **The fixture.** The messy-crm switch `listbox_in_flow` restores the reflowing listbox. The messy golden test runs
   with it on and still passes, now because of the executor fix.
4. **Tests:**
   - vitest with a fake CDP, where the box moves between measurements;
   - vitest where the hit test lands on another element;
   - the e2e golden test with `listbox_in_flow`.

## Notes

- **The e2e budget was heavily exceeded:** about 8 messy runs, about 6 lead runs, and 2 full suites. It was disclosed,
  and no timeout or retry was added. It is accepted this once, because the diagnosis was the point of requirement 3.
  For PL-017b the budget is enforced.
- **The Claude workers did not run.** `anthropic/claude-sonnet-5-5` failed authorization in CodeWhale even though the
  key is valid (T1 confirmed it against the API, with a 200). This is an operator configuration issue, which T1 and
  the owner are resolving.
