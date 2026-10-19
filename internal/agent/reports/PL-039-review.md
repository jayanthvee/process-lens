# PL-039 review (T1): approved, with two fixes

**Part 1 (pause answers 409).** Correct, and the resume path can't hit the index, as the report says.

**Part 2 (the start check). The ping, the warnings and the popup line are correct. Two fixes came from the e2e suite:**
1. **The popup opened as a tab of its own.** In that case the active tab is the popup itself, which has no content script, so every recording was refused. When the active tab is the sender, the check no longer refuses.
2. **The wrong window.** The worker's "active tab of the current window" is whichever window was focused **last**. During a run, that can be the ProcessLens work window, so Start on the person's own tab pinged the work tab and wrongly said "Refresh this page". Now the popup sends the tab it is looking at (`record:start` carries `tabId`), and the worker pings that tab.

**Tests after merging main:**
- extension 477 passed;
- API 192 passed;
- **e2e 23 of 23 passed.**
