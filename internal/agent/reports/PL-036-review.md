# PL-036 review (T1): approved

**Re-ran after merging `origin/main`:**
- `tests/api`: 177 passed;
- `npm --prefix apps/web run check`: 138 tests passed, and the build passed.

**The read.**
- It is tenant-filtered and lists only `csv`, `xlsx` and `manual` batches.
- It never reads `input_records` or `raw`.
- The task name rule matches `_LIST_TASKS`.

**Deviation 1.** `uploaded_by` from `input_batches.created_by` is the right call: a pasted batch has no file.

**The page.** It is one table, has no engine words, and its empty state has one action.

**Follow-up, not blocking.** The owner's rule is that the action sits where the person looks. A "Not used yet" row should offer **Run it** (to `/tasks/<id>/run`), and a used one should offer **Run again**. This is for a later dashboard ticket.
