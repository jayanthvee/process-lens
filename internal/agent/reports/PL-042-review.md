# PL-042 review (T1): approved, with one safety fix

**`firstEntryEvents`** and the trimmed readers are correct, and a single-save recording is returned untouched (same reference).

**T1 fix (safety).** `makeRecipeRequest` trimmed the recording at the **first** save click, but still sent `commit_step_ids: [choice.commitStepId]`. That is the default commit, the *last* click, which is the dropped repeat's save. The compiler then marked nothing as the commit, so the first Add would have compiled as a plain click: no write-ahead, no `assert_after`.
- **Now:** a trimmed recording's commit is the save click it ends on.
- **Test:** `commit_step_ids` is `["click-8"]` on the two-saves fixture.
- **Checked with the real compiler:** the trimmed fixture compiles `click-8` as the commit, with `text_count_increases`.

**Noted for later (compiler).** The owner's recording starts with an abandoned entry (New → pick a contact → open the contact's page). The PL-037 rule takes the first option pick as the save check's text, so the check counts that constant contact rather than the row's own. The fix is either to prefer the templated entry closest to the save, or to let the person delete the stray steps (PL-043).
