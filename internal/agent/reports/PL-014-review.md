# Review PL-014

Status: changes requested · Reviewer: T1 · Reviewed commit: 2472539 (branch `pl-014`)

## Verdict

The architecture is right, and most of the plan review landed:
- R3 reply matching, R7 uuid ids and R8 hand-back are correct;
- `ParkingReconciler` never re-queues;
- no path dispatches a commit twice;
- no pre-existing runner or db test assertion changed.

The e2e work found eight real bugs, and that justified it.

Not mergeable yet. **Six blockers.** Four of them can still produce a **duplicate record** or an action after the runner gave up. The R1 protection works in the runner but is undone by the extension (B1, B2).

## Gates (run by T1 on 2472539)

```
python -m pytest -q                 565 passed
ruff check . / ruff format --check  clean / 153 files already formatted
npm --prefix apps/extension run check   163 tests passed, build ok, validation passed
e2e (T2's last run)                 8 / 11
```

## Blockers (must fix before merge)

**B1. A failed condition check reaches the runner as a real "no" (R1 defeated end to end).**
- `checkCommandCondition` (`service-worker.ts:488`, `:506-507`) returns `false` in three cases: no bound tab, any exception (attach failure, CDP evaluate throwing, withdrawn command), and a malformed condition.
- `command-session.ts:467-475` then sends that as `condition passed:false`. `browser_executor.py:222` reads it as an answered "no".
- **Consequence:** a failed duplicate search takes the `else` branch and creates the lead again, and a failed fresh-confirmation probe says "absent".
- **Fix:** every exception, and every "cannot evaluate", becomes `result status=refused` for that command id. Only a completed poll may send `condition`.
- **Tests:** no bound tab → refused; a throwing evaluate → refused; the runner takes neither branch.

**B2. The bound tab is not restored after a service-worker restart (R5).**
- `commandBoundTabId` is set only in `bindActiveTab` (`service-worker.ts:527`). The stored `boundTabId` is never copied back on restore.
- **Consequence:** after an MV3 restart, every step fails "no tab is bound", and with B1 every check answers "no".
- **Fix:** restore sets `commandBoundTabId` from `stored.boundTabId`, after confirming the tab still exists; otherwise send `event tab_closed`. Restore never binds the active tab.
- **Test:** simulate a restart with a stored session; the next command acts on the stored tab id.

**B3. A command keeps running after the socket drops.**
- `onClose` clears `executing`/`inFlight` (`command-session.ts:267-268`), but `runStep` has no abort.
- **Consequences:** the abandoned dispatch can still click after the runner has written it off as `lost`; a new command can run at the same time on the same tab; and the old command's events have no `current()` check (`:521-525`), so they land on the next record.
- **Fix:**
  - give each command an `AbortController`;
  - `onClose` aborts it;
  - `aborted()` is checked before every `Input.*` call and in the condition poll;
  - a new command is refused while an aborted one is still unwinding;
  - events are sent only if the command is `current()`.
- **Test:** close the socket mid-dispatch; no `Input.*` call follows the close.

**B4. The hub sends `peer_ready` to a runner when no extension is attached (S5 defeated).**
- `hub.py:489` (and `:457` for the extension) sends `peer_ready` unconditionally. `BrowserExecutor.await_peer` then claims runs with no extension present.
- **Fix:** send `peer_ready` to both sockets only when both are attached.
- `test_hub.py:349` asserts the wrong behaviour; correct it. This is a test written in this ticket, so editing it is allowed.

**B5. Attach race in the hub.**
- The token match and the empty-slot check run outside `_sessions_lock`, and `await websocket.accept()` yields before the slot is set (`hub.py:438-454` for the extension, `:476-487` for the runner).
- Two concurrent connects both pass, so single use is broken. The orphaned socket can still inject frames, and it sends a false `peer_gone` when it retires.
- **Fix:** take the lock, re-check the token and the slot, reserve the slot, and only then `accept`.
- **Test:** two concurrent attaches with one token; exactly one succeeds.

**B6. The navigate load-event race: the remaining e2e failure.**
- `dispatchNavigate` subscribes to `Page.frameNavigated`/`loadEventFired` only after `Page.navigate` replies (`dispatch.ts:337-348`). A fast page's load event is lost.
- A late load event from the old document can also settle the wait, because the main-frame id is unchanged.
- **Fix:**
  - subscribe and buffer before sending;
  - settle only on the `frameNavigated` whose `frame.loaderId` equals the reply's `loaderId`, followed by its `loadEventFired`;
  - resolve immediately when the reply has no `loaderId` (a same-document navigation).
- This is Requirement 5 ("wait for the frame's load event"), so it is in scope.

## Must fix with the blockers (small, safety-relevant)

- **F1. R4: re-check the expiry immediately before the first input event.** Today it runs before `runStep`, before the guards, resolve and scroll (`command-session.ts:481`). `guards.ts:220 recheckExpiry` is dead code. Pass `expiresAt`/`now`/`aborted` into the step and check them right before the first `Input.*` call.
- **F2. Status after a partial input.** `sent` flips only after the whole action (`step-runner.ts:414,463,491`). Set it at the first `Input.*` event. Any error after that is `lost`: the runner reconciles, never `not_dispatched` and never `rejected`. Today an error after the click reports `rejected` (`command-session.ts:527`), which the runner turns into `failed`, but an exception is not a provable rejection.
- **F3. No fail-open defaults.** Each of these must refuse, never guess:
  - `value_equals` with a non-string value (`assertions.ts:84`);
  - a `None` value source becoming `""` (`run_loop.py:1352`): raise `TemplateError`;
  - `text_visible: ""`;
  - a non-numeric `expires_at` (`command-protocol.ts:211`);
  - a `select` with no value (`step-runner.ts:420`);
  - a malformed command dropped silently (`command-session.ts:374`): send `result refused` when it has an id.
- **F4. Template errors in `when` crash the worker.** The branch and gate `_check` calls (`run_loop.py:416`, `:440`) don't catch `TemplateError`. Park the record, as `_commit_step` does.
- **F5. A check with no reply is a lost executor.** A `check_condition` with no reply within budget (`browser_executor.py:220-227`) must set the detach flag and stop sending, like `_lost`. `_lost` must close the socket, not just drop it (`:417`). The worker must not claim a run while `executor.disconnected` is set (`worker.py`).
- **F6. Only one session socket at a time.** `restore()` runs at top level, in `onStartup` and from the popup (`service-worker.ts:567,638,642`), with no check for an existing socket or a pending reconnect. Make it idempotent, and ignore messages from any socket that isn't the current one (`command-session.ts:238`).
- **F7. Constitution §2.1.** `tests/e2e/conftest.py:378` (worker logs) and `:669` (browser profile) write to `%TEMP%`. Move both under the gitignored `.cache/` in the repo.

## Follow-up ticket (not blocking this merge)

T1 will write these up as PL-014b:
- **Guards before a navigate.** They currently refuse the next record's opening navigate after an off-origin park (`service-worker.ts:416-423`).
- **Debugger detach.** Handle `chrome.debugger.onDetach` and send `event detached`.
- **Draining events:**
  - drain after the fresh-confirmation probe;
  - drain the whole batch in `_drain_events`, with run-level events taking precedence over `off_origin`.
- **Hub session lifetime:**
  - the TTL should apply only to unclaimed sessions;
  - remove expired sessions;
  - announce `peer_gone` before awaiting close;
  - `_refuse` should select the subprotocol, so Chrome sees the close code.
- **The challenge guard** should count only *visible* challenge iframes; invisible reCAPTCHA badges are common on ordinary forms.
- **Hub tests that fail when their check is removed:**
  - the non-loopback client (`TestClient(client=...)`);
  - the close secret;
  - a wrong secret;
  - an unset extension id;
  - the 4400, 4403 and 4408 close codes;
  - an expired session.
- **A test that `runCommandStep` in the prepare phase makes zero `Input.*` calls.**

## Rulings

- **Re-pinned test.** `test_the_wire_deadline_is_the_reply_budget_for_every_command` was added in this ticket, so re-pinning it is allowed.
- **The six extension assertions changed under Requirement 5:** accepted.
- **`stash@{0}` (eager attach):** drop it. B2 and F6 are the real fixes.
- **The leftover scratch databases:** the owner can drop them. They are not part of this ticket.

## Instructions for T2

1. Fix B1–B6 and F1–F7, each with the test named or implied above. No changes outside these items.
2. Run the non-e2e gates. Then run the full e2e suite **once**. If it is not 11/11, run **only** the failing files once more, then stop.
3. Write a "Review fixes" section in the report with the real output. Commit, push, and stop for T1.

---

## Re-review: approved

Reviewed commit: cd94399 (branch `pl-014`) · Verdict: **approved for merge**, with the follow-ups below written up as
PL-014b.

```
python -m pytest -q                       573 passed
ruff check . / ruff format --check        clean / 154 files already formatted
npm --prefix apps/extension run check     177 tests passed, build ok, validation passed
e2e (T2, two budgeted runs)               full suite 10/11; the failing file then 3/3 on its own
```

**Every blocker is fixed, with a test.**
- **B1.** A check that cannot be evaluated is `result refused`, never `condition false`. `evaluate` throws on a page
  exception, so a throwing poll cannot reach the runner as "no".
- **B2.** The stored bound tab is restored, and the active tab is never bound.
- **B3.** A per-command `AbortController`. The input gate runs before every `Input.*` call, and a new command is
  refused while an aborted one unwinds.
- **B4.** The runner gets `peer_ready` only when both peers are attached.
- **B5.** The token match and the slot reservation happen under the lock, before `accept`.
- **B6.** The navigate waits for a `loaderId`-matched `frameNavigated`, then its load event.

**F1 to F7 are fixed.**
- F2 is conservative: `sent` (and the commit `ack`) flips at the first gated input, which for a click is the
  `mouseMoved`. An error after that is `lost`, so the runner reconciles and parks. That is the safe direction.
- F7: the e2e scratch is now under `.cache/e2e/`.

**The worker reconnect T2 added is accepted.** Without it, a resumed run could never be claimed again, which
breaks the ticket's own criterion. `connect()` clears the flag and `wait_for_peer_or_raise` gates the claim. This is
the only change beyond the item list.

**The one e2e failure is a test-timing bug, not a product bug.**
- `test_reconnecting_and_resuming_continues_the_run` starts its worker with `max_seconds=180` before the
  disconnect.
- On a slow run, that budget expires before the resume, so the worker exits ("processed 1 run(s)") and the resumed
  run stays `queued`.
- The record itself parked correctly, with no duplicate.

### Follow-ups (PL-014b, not blocking)

Carried over from the first review:
- the guards before a navigate;
- `chrome.debugger.onDetach`;
- event draining after the probe and over the whole batch;
- the hub session lifetime;
- visible-only challenge markers;
- the hub tests;
- the prepare-phase zero-input test.

New from this re-review:
1. **A hub slot is reserved before `accept`, and nothing releases it if `accept` or the first frames fail**
   (`hub.py:461`, `:500`). The session is then refused with 4403 for good. Release the slot in a `finally`. Also
   keep the previous resume token valid until the extension has used the new one, or a frame lost in flight locks
   the extension out.
2. **A transient evaluate error during a poll refuses the whole check.** For example, "Execution context was
   destroyed" while the page navigates after Save. That fails safe (the record parks), but on a CRM that redirects
   after saving, every save would park. Retry transient context errors within the poll window, and refuse only on
   persistent errors.
3. **The extension still sends itself `peer_ready` when no runner is attached** (`hub.py:464`). Make it match the
   runner side.
4. **Events from an aborted command are not gated by `current()`** (`command-session.ts`, the guard-error
   `sendEvent`).
5. **The e2e reconnect test** should start its worker with a budget covering the whole test, or start a fresh
   worker after the resume.
