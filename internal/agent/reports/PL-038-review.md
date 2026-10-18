# PL-038 review (T1): approved, with one addition

**Re-ran after merging `origin/main`** (PL-040's words changes merged cleanly): the web check passed **149 tests**, plus the typecheck and the build.

**Reviewed:**
- `ApprovalPanel` is a faithful move: same markup, same `decide()`, same operator rule.
- The run page's Try again and Dismiss reuse `actionGate` and `sendRecordAction`.
- The commit-window tick is kept, and "Try all N again" excludes rows that need the tick and names them.
- No engine words.

**T1 addition (from the owner's run).** A preview row that paused for anything other than a decision (`window_hidden`, `canary_failed`, `executor_disconnected`, …) still read "Live — Adding this row…" in the card.
- The card now shows the pause sentence (`pauseReasonWords`) and a **Resume** button that POSTs `/runs/{id}/resume` and reloads the run.
- **Test:** `taskRun.test.tsx`, "a row paused for anything but a decision says why and offers Resume in the card".

**Still open, for the next dashboard ticket:**
- Try it can create two runs on a fast double click.
- A queued row waiting behind another app's paused run doesn't say so.
- Old tasks need an "older version" mark.
- The popup's "Contact 2" duplicate column.
