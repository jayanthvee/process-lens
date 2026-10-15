# Review PL-018

Status: **approved for merge** · Reviewer: T1 · Reviewed commit: 0b8dd50 (branch `pl-018`)

## Gates (run by T1 on 0b8dd50)

```
python -m pytest -q                              670 passed
ruff check . / ruff format --check .             clean / 203 files already formatted
npm --prefix apps/extension run check            371 tests passed, build ok, validation passed
npm --prefix apps/web run check                  28 tests passed, build ok
python fixtures/sites/messy-crm/selftest.py      SELFTEST PASSED (80 checks)
python fixtures/sites/widget-corpus/selftest.py  SELFTEST PASSED (71 checks)
e2e full suite (T2, final round)                 18 passed, 3 failed (shadow and iframe: PL-018b; in-flow messy: 4403, see below)
```

## Verdict

PL-018 changes how the recorder works, and the change holds up.

- **State-diff recording.** Before each click, key press, submit and navigation, the recorder snapshots the
  page and diffs it, and the diff comes out before the action. That removes the old ordering bug by
  construction.
  - The snapshot reads only each field's kind and value. Element locators are computed only for fields that
    changed (T1's fix). A 1,000-field page takes tens of milliseconds.
- **`press`.**
  - Implemented in the schema, recorder, compiler and executor, and on the runner's wire, under T1's ruling
    (`autocomplete` and `key` were added there).
  - **R1:** a recording whose send was done with a key is refused.
  - **R2:** a chip removal that can't be replayed is refused.
- **Folding and replay of autocompletes.**
  - The typed prefix and the accepted suggestion fold into one fill of the chip's address.
  - Replay types the value, then accepts the single suggestion that contains it, through the guarded click.
    If no single suggestion matches, the record parks.
  - The golden test showed this parking is correct: a row whose address had no suggestion parked instead of
    sending a half-addressed message.
- **The messy regression is fixed.** Each value is now emitted exactly once, before its click. A test drives
  Playwright's exact event order, and it fails on the old code.
- **The click race is closed.** The final hit test now runs last, after the listener is armed and the
  pointer has moved. A click is held for 300 ms after input sent by the executor itself, within the step's
  deadline. R3 is unchanged.
  - Median click overhead on lead-crm is 12 ms (PL-017b measured 33 ms on the same helper).

## Browser coverage

| Widget | Status |
|---|---|
| Gmail-style compose | passed |
| Plain editor | passed |
| Framework editor | passed |
| React autocomplete | passed |
| Shadow-root form | moved to PL-018b |
| Same-origin iframe form | moved to PL-018b |

That is **4 of 6** widget types. Both open items have a ticket, and T1 diagnosed the shadow-root one: the text
predicates read only `document.body.innerText`.

## Accepted residual

In the final full suite, the in-flow messy test failed on `4403` ("the extension never attached"). The same
file passed 2 of 2 when run alone.

`hub.py:51` defines 4403 as **"a second socket for the same slot"**. A runner worker attached while an earlier
worker still held the session's runner slot. This is harness worker overlap, not PL-018 code. It is the same
class of problem as PR #14.

**Follow-up:** the e2e harness must wait for the previous worker's runner socket to close before it starts
the next worker.

## Follow-ups

1. **Payload-level verification (PL-020).** A record read `verified` while the message had `to: []`, because
   the only assertion was the toast. This must become a recipient or payload check.
2. **`allow_destructive_control`** is not carried on the wire. It needs its own ruling before any recipe can
   opt in.
3. **The 4403 worker overlap** in the e2e harness, as above.
4. **Line endings.** The repo has no `.gitattributes` and runs with `core.autocrlf=true`. The corpus selftest
   now normalizes CRLF (`9ae7b2d`). Adding `.gitattributes` would stop the next tool tripping on this.

## Process notes

- The ticket took five e2e rounds. Real bugs were found in every round. The cost came mostly from relaying
  between T1 and T2, environment failures (sleep, network, Docker) and builders running out of steps.
- From here on, T2 tickets stop only for:
  - a safety-path change;
  - a runner or protocol change;
  - a budget overrun.
