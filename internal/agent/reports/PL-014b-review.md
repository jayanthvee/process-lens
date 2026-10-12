# Review PL-014b

Status: changes requested (small) · Reviewer: T1 · Reviewed commit: eb9fc46 (branch `pl-014b`)

## Verdict

Good work:
- Items 2, 3 and 5–10 are done as specified, with tests.
- The adversarial pass found a real requirement-2 bug, and the `_is_expired` fix is correct.
- The negative checks were actually run.
- The e2e run was reported as it showed, not re-run.

Three things block merge:
- C1 is item 1, half done.
- C2 is item 4 reopening R1.
- C3: the e2e diagnosis is half right. The two wall cases are not just stale tests.

## Gates (run by T1 on eb9fc46)

```
python -m pytest -q                       593 passed
ruff check . / ruff format --check .      clean / 157 files already formatted
npm --prefix apps/extension run check     13 files, 194 tests passed, build ok, validation passed
e2e (T2, one run)                         8 / 11
```

## Must fix

**C1. Item 1: the previous resume token dies before the extension has the new one.**
- `_send_frame` only queues a frame (`hub.py`). So `attached = True` and the token commit happen before anything
  has reached the extension.
- If the socket drops after the commit but before the `session` frame arrives, the extension still holds the old
  token. The hub has already replaced it, so the extension is refused with 4401 for good.
- This is exactly the case the requirement names: the old token must stay valid "until the extension has
  re-attached with the new one". Today it only survives a failed `accept`.
- **Fix:** keep the token **this attach used** valid alongside the newly issued one. On the next successful attach,
  the used token becomes the previous one again.
  - At most two resume tokens are ever live: the one last used and the one last issued.
  - On a first attach, the spent session token plays the role of the "used" token, so a lost first `session` frame
    doesn't lock the extension out either.
- **Tests:**
  - attach with A, which issues B; drop the socket without reading the frame; re-attach with A → accepted;
  - re-attach with B → accepted, after which A is refused;
  - a lost first `session` frame → re-attach with the session token is accepted.

**C2. Item 4: a poll that never evaluated cleanly ends as a real "no" (an R1 regression).**
- `waitForCondition` counts a transient throw as a poll that answered false. If every poll until the timeout
  throws, it returns `ok: false`. `checkCommandCondition` then sends `condition passed:false`, which is an answered
  "no".
- A page that keeps redirecting for the whole window would send a duplicate search down its `else` branch and
  create the lead again. This is the B1 bug coming back by a different path.
- **Fix:**
  - `passed: false` is allowed only when **the last poll before the timeout evaluated cleanly**.
  - If the last poll was a transient error, return a "could not evaluate" result. `checkCommandCondition` turns
    that into `{ok: false, reason}`, which becomes `result refused`.
  - Narrow the regex to the real messages: "Execution context was destroyed", "Cannot find context with specified
    id", and "Inspected target navigated or closed". The last one is what Chrome returns when a navigation
    interrupts `Runtime.evaluate`. Drop the bare `was destroyed`.
- **Tests:**
  - every poll throws a transient error → refused, not `passed:false`;
  - transient errors then a clean false → `passed:false`;
  - "Inspected target navigated or closed" is retried.

**C3. A wall that appears after a save pauses the run as `canary_failed`, not `challenge` / `login_required`.**
- The `challenge_page` and `login_page` switches do not change the page the tab starts on. They make **the save**
  (`POST /leads/new`) land on the wall (fixture README, lines 94–95). PL-014 plan, lines 837–838: the run pauses
  `challenge` / `login_required`.
- The e2e tests took a shortcut: they left the tab on the wall, so the pre-command guard of the opening navigate
  caught it. Item 5 rightly removed that shortcut. Now the real scenario runs, and nothing looks for the wall.
  `assert_after` times out, the record parks (safe), and the run pauses `canary_failed`. The person is never told
  they need to log in or solve a challenge.
- The runner already does the right thing with a `challenge` event inside the commit window: `_stop_record_for_event`
  reconciles and the run pauses with the event's reason. Only the extension is missing a piece.
- **Fix (extension):** when a `check` poll completes with `passed: false`, run `checkChallengeOrLogin` on the
  current page.
  - If it finds a wall, send the `challenge` / `login_required` event **before** the condition reply (R6), gated by
    `current()` (item 7).
  - Then send the `condition` reply as usual. Do not change the answer.
  - A failed duplicate-search `when` on a wall page then releases the record and pauses the run, which is better than
    taking the `else` branch.
- **Tests:**
  - a vitest case: a `check` whose poll ends false on a challenge page sends `event challenge` before `condition`;
  - a runner test: a challenge event during the `assert_after` check of a commit leaves the record `parked` (never
    `pending`) and pauses the run `challenge`.

**C4. Update the three e2e cases (now in scope).**
- **`test_challenge_and_login.py`, both cases:** remove the `page.goto` to the wall, and leave the tab on the CRM. The
  switch makes the save land on the wall. Assert:
  - `pause_reason` is `challenge` / `login_required`;
  - the record is `parked`, never `failed`;
  - the leads count is 0.
  - Update the module docstring.
- **`test_an_off_origin_page_parks_the_record`:** split it in two.
  - `test_an_opening_navigate_leaves_an_off_origin_page`: the tab starts on the partner page and the record is
    `verified`. This is the item-5 contract.
  - `test_a_step_on_an_off_origin_page_parks_the_record`: the tab is on the partner page, and a recipe with no
    opening navigate (like the destructive case, a single click via `simple_recipe`) parks with no lead created.

## Notes (no change needed)

- **A navigate prepare runs no origin guard.** The runner sends `prepare` only for commit steps, and `navigate` is
  not one, so this path is unreachable. Making `prepareCommandStep` refuse a `navigate` outright would be a cheap,
  welcome change, but not required.
- **The conservative `_purge_expired`** (it never drops a session with a socket attached): accepted.
- **`_send_frame` overflow during an attach leaves `attached` true on a gone peer.** `_relay` retires it. Acceptable
  for now.

## Instructions for T2

1. Fix C1–C4, each with the tests named. No other changes.
2. Run the non-e2e gates.
3. E2e budget: **one** invocation of only the two changed files:
   `pytest tests/e2e/test_challenge_and_login.py tests/e2e/test_off_origin_and_destructive.py -q -m e2e`.
   Report it as it shows. No full-suite run and no re-runs.
4. Add a "Review fixes" section to `internal/agent/reports/PL-014b.md` with the real output. Commit this review with
   it, push, and stop for T1.
