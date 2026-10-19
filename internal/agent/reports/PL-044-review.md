# PL-044 review (T1)

Approved, no changes.

- `_page_change_start` walks the steps before the save and starts the entry after the last navigate, off-page `after_url`, or observation whose `url_matches` does not fit the save's path; a step with no URL is never a page change. Only `_infer_assertion_from_fills` uses it, for both the first entry and the columns check, and it still returns None when the entry gives nothing.
- On the owner's two-saves recording the save check is now `{{row.contact}}`, not the abandoned entry's "Probe Four Contact".

Gates (after merging main, which carries PL-042 and PL-045): compiler 177 passed; full e2e 23 passed (17 min).
