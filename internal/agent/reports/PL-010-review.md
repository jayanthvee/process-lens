# Review PL-010

Status: approved · Reviewer: T1 · Reviewed commit: dfcf2ab (branch `pl-010`)

## Verdict

Approved for merge. All six blockers from the first review (against 016dc14) are resolved. Unit tests cover B3,
B4, B5, B6, and the commit-in-flight path of B2. B1 and the `onDetach` wiring in the service worker were verified by
reading the code only, because the service worker has no unit tests (follow-up 13).
The follow-ups below do not block this merge. The first one should be settled before the ledger integration.

## Gates (run on dfcf2ab)

```
$ npm --prefix apps/extension run check
tsc --noEmit (clean)
Test Files  8 passed (8)
     Tests  120 passed (120)
package validation passed

$ python -m pytest -q
299 passed, 1 warning

$ ruff check .
All checks passed!
```

## Blockers

| # | Blocker | Resolution | Where |
| --- | --- | --- | --- |
| B1 | A failed attach left the worker locked as "running" | Attach moved inside `try`; detach only if attached; flags always cleared in `finally` | `service-worker.ts` `startExecutor` |
| B2 | `chrome.debugger.onDetach` not handled | `onDebuggerDetach` aborts the run with the reason; listener removed in `finally`; an abort during a commit step reports `commitOutcomeUnknown` with disposition `park` | `cdp.ts`, `service-worker.ts`, `executor.ts` |
| B3 | Click used coordinates measured before scrolling | `measureTargetInPage` scrolls, re-measures, refuses a zero-area box, and hit-tests the center immediately before dispatch | `ladder.ts`, `dispatch.ts` `dispatchClick` |
| B4 | Unsupported actions were skipped and the run continued | `validateRecipe` refuses the whole recipe before step 1, in both the service worker and `runRecipe` | `executor.ts` |
| B5 | A frame step could run in the top document | `frames.listen()` before `Runtime.enable`; an unknown frame context is a `park` refusal | `service-worker.ts`, `executor.ts` `contextForFrame` |
| B6 | `select` took the first substring match | Exact match first; a substring only when exactly one option contains it; a native select with no match is refused, never sent down the combobox path | `dispatch.ts` `dispatchSelect`, `executor.ts` |

Also fixed in the same commits: a fill into a password field is refused, a missing input column or page variable
parks the record, and an empty fill is refused.

## Follow-ups (not blocking)

1. **A commit step whose assertion times out is not flagged as uncertain.** The run ends `failed`, disposition
   `park`, with `commitOutcomeUnknown` false, though the click was dispatched. Set `commitOutcomeUnknown` from
   `trace.commitInFlight` on the failure path too, so the ledger sends the record to `reconciling`. Do this before
   the backend integration.
2. **The hit test accepts an ancestor of the target** (`hit.contains(element)` in `measureTargetInPage`). The click
   then lands on the ancestor. Accept only the target or its descendants, unless the target has `pointer-events: none`.
3. **Clicks in nested frames use frame-relative coordinates.** Refuse a click in a non-top frame until the frame
   offset is added, rather than relying on the assertion to catch a miss.
4. **Origins are checked on `navigate` only, and an empty `allowed_origins` allows everything.** Check the current
   frame's origin before every fill, select, and click, and refuse a recipe with no allowed origins.
5. **Ladder order follows the recipe array**, not the canonical rung order, and `rung` is 0-based. Sort by rung
   rank, or reject out-of-order ladders, and report rungs as 1 to 5.
6. **`within` containers resolve to the first match**, with no uniqueness or visibility check.
7. **Fill typing.** `\n` and `\t` become Enter and Tab key presses. `clear_first: false` still selects and replaces
   the existing text. Nothing reads the value back after typing.
8. **`navigate`** ignores `errorText` and does not wait for the load before the next step.
9. **A commit's `assert_after` can pass on text already on the page.** Check the condition before the click and
   require it to be false.
10. **Abort detection still matches `/aborted/` in error text** (for example `net::ERR_ABORTED`). Read
    `control.aborted` only, and check it inside the assertion poll loop.
11. **The combobox fallback searches the whole page with `text_fuzzy`** for the option. Scope it to the listbox
    the combobox controls, with `role=option` only.
12. **The fuzzy rung for inputs still matches the field's current value.** That is inconsistent with the recorder,
    which no longer records it.
13. **`startExecutor` has no tests.** Add tests with a fake `chrome.debugger`: an attach failure clears
    `executorRunning`, a detach event aborts the run and removes its listener, and detach is skipped when the attach
    never succeeded.
