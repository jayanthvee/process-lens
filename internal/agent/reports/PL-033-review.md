# PL-033 review (T1)

**Verdict: approved.**

## What I checked

- **The full e2e suite on `pl-033`, merged with `main`:** 20 passed and 2 failed. The two failures are the known
  `test_widget_shadow_form` and `test_widget_iframe_form`, which are frame and shadow-DOM replay, owed to PL-018b.
- **My change:** a T1 fix to the e2e harness, `test(e2e): open the popup's More section`. The popup now keeps
  Connect and the address field inside a closed `<details>`, so the harness opens it after each `popup.goto`.
- **The popup flow** (`apps/extension/src/popup/flow.ts`):
  - it is DOM-only and tested;
  - the task link opens through `chrome.tabs.create`, so it works in a popup;
  - auto-connect fires only from `idle` or `disconnected`.
- **The dashboard** says "Waiting for Chrome" instead of a bare "Queued", and step names read like a person wrote
  them.
