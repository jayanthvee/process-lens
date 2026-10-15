# Review: PL-018 Step 0 plan

Status: **approved with two required changes** (R1, R2) · Reviewer: T1 · Reviewed commit: 00d09f1 (branch `pl-018`)

The plan is strong:
- State-diff recording at `pointerdown` with a defined baseline is the right model.
- The design review's eight findings are real, and every disposition in §11 is correct.
- Popup listboxes are excluded from chip detection, the fold witness is shared with replay, and Enter plus submit
  collapse into one commit.

Implementation may start once R1 and R2 are in the plan. No second stop is needed.

## Rulings on §10

1. **D-020, a superseding entry.** Accepted entries in `DECISIONS.md` are never edited. D-020 restates the layer
   numbering, AI summary (PL-019), verification (PL-020) and self-healing (PL-021), and says it supersedes that part of
   D-019.
2. **The accept mechanism: click the matched option through the guarded click path** (PL-017b's stability, hit and
   landing checks).
   - If the widget offers no clickable option, `Enter` is allowed **only** when the widget's
     `aria-activedescendant` (or its selected option) is the single option whose text contains the row value.
   - Otherwise, park. Never press `Enter` blind.
3. **`"press"` in `EXECUTOR_ACTIONS` (`services/runner/creation.py`): approved.** This is the only runner touch.
   `press` is never commit-capable, so the runner's commit path is unchanged.
4. **Chip removal without a remove control: rejected as proposed.** See R2.
5. **The snapshot cap of 1500, with a warning:** accepted.
6. **Same-origin iframes only,** with a warning page for cross-origin: confirmed.
7. **The fold needs the recorder's witness;** older recordings don't fold: confirmed. That is the fail-safe direction.

## Required changes

**R1. A key press must never be the step that commits.**
- §3.6 handles `Enter` followed by a form `submit`. But Gmail's **Ctrl+Enter** (and many apps' Enter in a non-form
  composer) sends **with no `submit` event at all**. Under the plan, that press stays a plain `press` step.
- A `press` is not commit-capable. So the action that actually sends the message would run with **no write-ahead**:
  no `submitting` entry before it, no `assert_after`, no reconcile. A crash right after it would re-queue the record,
  and the message would be sent twice. That breaks the product's core guarantee.
- **Rule:**
  - When a `press` of `Enter` or Ctrl/Cmd+`Enter` is the last action before the recording's success observation, or
    is the step the person marks as the commit, the compiler refuses the recording, with this problem: "The save or
    send was done with a key. Re-record it by clicking the button (for example Send)."
  - Make recipe shows that as the problem, and stores nothing.
  - §3.6's collapse into a form submit's click stays as designed. It produces a commit-capable click, so it is fine.
- **Tests:**
  - a compiler test: a recording ending press(Ctrl+Enter) → "Undo" is refused;
  - a compiler test: the §3.6 Enter→submit collapse still compiles to one commit click;
  - a corpus or golden check: the Gmail-style compose sent with Ctrl+Enter is refused at compile time, not replayed.

**R2. A chip removal that can't be replayed must block the recipe, not just warn.**
- Recording nothing for a removed chip means the replay keeps a recipient the person removed. That is a wrong
  recipient: the message goes to someone it shouldn't. A warning in a list is easy to miss.
- **Rule:** when a chip vanishes with no remove control, the compile refuses, with this problem: "A recipient was
  removed in a way that can't be replayed (no remove button). Re-record it using the ✕, or don't remove recipients
  during the recording."
- **Test:** the corpus selftest's chip-removal-without-a-control case gives a compile refusal, not a recipe.

## Process

- **The design review ran on Sonnet 5.5,** although the fleet's reviewer had been switched to `deepseek-flash`
  before the review spawned. The owner is on a small Claude budget. Implementation reviewers use whatever model the
  T2 prompt names. Do not choose a model yourself.
- **The e2e budget is as the ticket states.** Before any run past it, stop and ask T1.

## Next

Fold R1 and R2 into the plan, adding a "Changes from T1 review" section. Commit it with this file, then proceed
straight to implementation. No second stop.
