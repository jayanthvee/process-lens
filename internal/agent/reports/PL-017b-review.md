# Review PL-017b

Status: **approved for merge** · Reviewer: T1 · Reviewed commit: 8394446 (branch `pl-017b`)

## Gates (run by T1 on 8394446)

```
python -m pytest -q                       644 passed
ruff check . / ruff format --check .      clean / 189 files already formatted
npm --prefix apps/extension run check     292 tests passed, build ok, validation passed
python fixtures/sites/messy-crm/selftest.py   SELFTEST PASSED (80 checks)
e2e full suite (T2)                       15 passed; median click overhead 33.2 ms
```

## Verdict

- **Stability:** a two-frame `requestAnimationFrame` pair, with 1 px tolerance, inside the step's deadline. If the
  target never settles, the step parks with no input sent.
- **Hit test before the press:** if the point is covered, the target is re-measured. If it is still covered at the
  deadline, the step parks with no input sent.
- **Landing check:** a one-shot capture listener, installed immediately before the press.
  - **On a commit step,** a miss reports `lost`, so the runner reconciles. It is never `rejected` or
    `not_dispatched`.
  - **On a non-commit step,** the result is also `lost`, and `run_loop.py:799-815` parks it (unless the recipe sets
    `on_failure: "fail"`; see follow-up 2).
- **The fixture's `listbox_in_flow`** reflows the page, and the messy golden test passes with it on. The fix, not the
  fixture, is now what makes it pass.
- **Overhead:** a median of 33 ms per click, well inside the 5 s ack budget.

## Follow-ups (not blocking; for the PL-019 verification ticket)

1. **A pre-commit misclick may have hit a commit control.**
   - The landing check now detects that a press landed on a different element. But a fill-phase step never entered
     `submitting`, so the record parks with `commit_attempted = false`.
   - A person can then re-queue it without the destination check, even though the stray press could have hit Save.
   - **Fix:** a parked record whose reason is "the click landed on a different element" requires the same
     destination confirmation as a commit-attempted record. Enforce it in the operator page and the requeue and
     dismiss endpoints.
2. **`lost` should never become `failed`.**
   - With `on_failure: "fail"`, a non-commit `lost` result is recorded as `failed`. But `lost` is an unknown outcome,
     not evidence, and the constitution says `failed` is set only on evidence.
   - **Fix:** map `lost` to `parked` regardless of `on_failure`.
3. **The custom-combobox select path** inherits the guard, with a 2000 ms settle deadline instead of the step
   timeout. Make it use the step timeout.

## Notes

- **The e2e budget overran again:** 2 lead runs against a cap of 1, and 2 full suites against a cap of 1. Both were
  disclosed, with no timeout or retry added. The reasons hold: the overhead helper errored, and the reviewer's
  changes needed a re-run. But the budget has now been exceeded on three tickets in a row. From the next ticket on,
  an overrun means **stop and ask T1 before the extra run**, not a disclosure afterwards.
- **The intermittent recorder-phase flake** in `wait_for_observation` (messy file) is noted. If it recurs, it gets a
  ticket.
