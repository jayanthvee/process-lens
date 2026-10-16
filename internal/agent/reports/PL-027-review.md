# PL-027 review (T1)

**Verdict: approved, Odoo part only.** The Twenty golden moves to PL-027b.

## What landed
- **T2's nine fixes from the real Odoo:**
  - values the page sets itself are no longer recorded as typing;
  - an Enter-to-search counts as a search;
  - the duplicate branch is scoped to result cells;
  - table cells get roles;
  - a navigation assertion must not already be true before the save;
  - same-document save evidence;
  - suggestion fills are folded;
  - a navigate must be proved to have arrived.
- **The approval hold (T2).** `first_record` holds the tab at the commit step and runs the prepared commit on
  approval. Every row has `attempts == 1`, which shows nothing was re-queued.
- **The abandon guard (T1 redesign).**
  - T2's version paused every request with `Fetch` and answered each one from the service worker. It also asked for
    a `Preflight` filter, which Chrome rejects. On real Odoo the worker stalled until the hub socket dropped.
  - The guard now blocks every URL in the browser's network stack, sends the tab to `about:blank` so the page's
    unload saves fail, and lifts the block once the blank page has arrived.
  - There is no work per request and no state left armed between records.

## Checks I ran
- **The Odoo golden passed 3 times in a row** (about 3 minutes each) with approval on. It proves:
  - no lead exists before approval, and exactly one after;
  - every field of the 3 new leads is right in Odoo;
  - the duplicate is skipped, and the row with no email creates nothing;
  - the row that parks mid-form leaves **zero** leads, and the guard recorded the save Odoo tried to send.
- **Merged with `main`.** The service worker's deps and `make_run` conflicted; both are resolved.
- **Unit gates:** pytest 830 passed and extension 411 passed. I also fixed PL-026's freeze test, which counted
  `committed_keys` across the whole database instead of its own tenant.
- **Full e2e: 20 of 22 passed.** `test_widget_shadow_form` and `test_widget_iframe_form` fail **on `main` too**,
  checked on main's code. They belong to PL-018b and were not caused by this branch.
