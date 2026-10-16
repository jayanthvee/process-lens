# PL-025 review (T1)

**Verdict: approved.**

- **Gates:** `npm --prefix apps/extension run check` is green (typecheck, 385 tests, build, validate).
- **The e2e contract holds:** every frozen popup id is present exactly once, the same as on `main`.
  - `test_record_compile_run.py` and `test_gmail_compose_golden.py` both pass against the built extension
    (2 passed in 139 s). Both drive Connect, recording and `compile_via_popup`.
- **The new dependency** is `@fontsource/public-sans`, pinned at 5.3.0. The fonts are bundled into `dist/popup/fonts`;
  no remote font is fetched.
- **The leak fix is right.** Route sentences that the popup can't show are counted, not dropped, so the problem count
  stays true.
- **Design call: two gold buttons.** Accepted. *Start teaching* and *Save as task* belong to different moments of the
  same flow and are never both the next action, so each moment still has one primary.

Follow-up (not blocking): the extension still declares no icons.
