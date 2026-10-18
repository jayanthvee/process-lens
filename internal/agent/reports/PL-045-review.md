# PL-045 review (T1)

Approved with one T1 change.

- `ensureVisible` returns a report; the raise error is kept, window facts are read before and after, and a failing `windowFacts` is just absent. The `window_hidden` detail has no URL or page text.
- The caller is `background/command-step.ts`, not `executor/` as the ticket said; the change there is right.
- T1 change: when the step failed after a check that found the page visible (nothing was raised), the detail said "window unknown→unknown, waited 0ms". It now uses the plain message; one vitest case covers it.

Gates: extension check 492 passed (after merging main); full e2e 23 passed (17 min, before the merge of main, which touched only the popup).
