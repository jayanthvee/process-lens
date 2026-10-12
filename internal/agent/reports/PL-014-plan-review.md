# Review: PL-014 Step 0 plan

Status: approved with required changes · Reviewer: T1 · Reviewed commit: 9ac1635 (branch `pl-014`)

This is a strong plan. The ground-truth section was checked against the code, and the three self-corrections in
9ac1635 are right:
- the existing `filling → pending` release;
- the fake's default-`True` condition, which would park every commit;
- a wire expiry derived from the assert timeout, which would refuse legitimate dispatches.

The hub security model is fail-closed in the right places. Implementation may start once the plan is amended with
the changes below; no second stop is needed. **R1 and R2 are the important ones.** Both let the real extension
create a duplicate record, and the fake hides both today.

## Required changes

**R1. A check that got no answer must never count as "false".**
- `ConditionResult` carries only `passed` (`executor.py:52`), and `_check` returns `result.passed`
  (`run_loop.py:760-769`). With the fake that is harmless. With the real extension, a `check` can end with no
  answer at all: lost, refused by a guard, `peer_unavailable`, or past the runner's budget.
- If that reads as `False`, the duplicate-search `branch` takes its `else` and the recipe creates the lead. If
  the lead already existed, it now exists twice.
- **Fix:**
  - Give `ConditionResult` a `status` (`ok` | `lost` | `refused`), defaulting to `ok` so the fake is unchanged.
  - `passed: false` is only a real answer: the extension polled to the timeout and the condition never held.
  - **Branch or gate `when`, no answer:** park the record. If a run-level event or disconnect is flagged, release
    it and pause instead (R6). Never take either branch.
  - **`assert_after`, no answer:** `reconciling`, which is already the not-passed path.
  - **Fresh-confirmation probe, no answer:** treat it as "present" and park. Never as "absent".
- **Tests:** a lost branch check and a refused branch check each take neither branch.

**R2. `_check` sends conditions without filling in the template values (an existing PL-013 bug).**
- `_check` receives `step.get("when")` and `step.get("assert_after")` raw (`run_loop.py:372`, `530`).
  `_render_condition` is applied only inside `_command`.
- Our own `packages/recipe/examples/create-lead.recipe.json` searches for a row named `{{row.email | lower}}`
  (line 115). The extension would look for that literal text, never find it, and create a duplicate of every
  existing lead.
- The fixture site would mask this, because it rejects duplicate emails. A real CRM would not.
- **Fix:** render every condition with `_render_condition` before `_check`. That covers `branch.when`,
  `approval_gate.when`, `assert_after` and the fresh-confirmation probe.
- **Test:** a branch whose `when` uses `{{row.email}}` reaches the executor with the value filled in.

**R3. Match every reply to the command it answers.**
- **`BrowserExecutor`:** accept an `ack`, `result` or `condition` only when its wire id equals the command it
  is waiting on. Log and drop anything else: a late reply, or the extension's "`lost` after reconnect" report
  for an earlier command.
- **`on_dispatched`** fires only on a matching `ack`, and only for a commit `dispatch`.
- **Test:** a late `ack` for command N arriving while command N+1 waits never calls N+1's `on_dispatched`.

**R4. The extension must never act after the runner has stopped waiting.**
- A 60 s wire TTL against a 5 s ack budget leaves 55 s in which the runner has already written the dispatch off
  as `lost` → `reconciling` → parked, and the extension can still click Save.
- **Wire expiry:** `dispatch` gets `expires_at = send + ack budget`. Every other command gets
  `send + its own result budget`.
- **Re-check:** the extension checks `expires_at` on receipt, and **again immediately before the first input
  event**, after the guards, scroll and hit test. If it has passed, refuse.
- **No reply within budget, socket still up:** `BrowserExecutor` sends nothing more and the run pauses
  `executor_disconnected`. It never moves on to the next record while the extension may still be acting on the
  previous command.
- **Test (unit):** a command that was fresh on receipt but expired before input is refused, and no input is sent.

**R5. Reconnect cannot work as designed.**
- The session token is single-use (§3.2.3), yet §4.4 reconnects with it.
- An MV3 service-worker restart also loses `sessionId` and the token from memory.
- **Fix:**
  - On every successful attach, the hub sends a fresh **resume token** as its first message, and the old one dies.
  - A detached session accepts one re-attach with the current resume token within its TTL.
  - The extension keeps `{hubUrl, sessionId, resumeToken, boundTabId}` in `chrome.storage.session`, which lives in
    memory and survives a worker restart.
- **Losing `highestEpoch` and `recentIds` on a worker restart is acceptable.** The hub never replays, so a new
  socket only ever carries new messages. Say this in the plan.
- **Test (unit):** reconnect after a simulated worker restart re-attaches to the same session.

**R6. Run-level events take precedence over the step result.**
- **On the wire:** the extension sends the `event` **before** the `result refused` it causes.
- **In the run loop:** drain events **before** interpreting a result. Without this, a challenge first parks the
  record through the `refused → parked` rule, and only then pauses the run. The ticket says the record is
  released (`filling → pending`), not parked.
- **`peer_gone` is authoritative, not a hint.** When the extension's socket drops (the hub `close` test does
  exactly this), the runner's own socket stays up, so "the socket dying under `BrowserExecutor`" never happens.
  On `peer_gone`, the in-flight command is `lost` at once and the run pauses `executor_disconnected`, even if the
  extension reconnects within 500 ms.
- **Test:** a `challenge` event plus `result refused` on a pre-commit step releases the record. It does not park
  it.

**R7. Wire command ids must be unique across processes.**
- `{command_id}#{n}`, with a per-process counter, restarts at `#1` after a crash. The takeover worker's first
  command for the same record and step then matches an id the extension already remembers, and is refused as a
  duplicate. That is the crash-and-takeover e2e path.
- **Fix:** use `uuid4()` per send, and carry `StepCommand.command_id` alongside as `step_ref` for logs.

**R8. Correct §5.4.**
- `awaiting_approval` and `duplicate_detected` already hand back. Their runs are paused, and the loop's status
  check at `run_loop.py:156` releases them.
- Only the `internal_error` return (`run_loop.py:168-169`) skips the hand-back. Don't report the other two as a
  gap.

**R9. Keep the Playwright browser inside the repo.**
- `playwright install chromium` downloads into `%LOCALAPPDATA%\ms-playwright` by default, which is outside the
  repo folder (constitution §2.1).
- Set `PLAYWRIGHT_BROWSERS_PATH` to a gitignored folder in the repo (for example `.cache/ms-playwright/`), and add
  that folder to `.gitignore`, a scope touch to list in §9.
- Use `channel="chromium"` so the bundled browser loads the extension.

## Smaller fixes

- **S1. Rewrite the end of §4.3; it contradicts itself.** Rule: after the stale check, every well-formed command
  sets `highestEpoch[run] = max(highestEpoch[run], lease_epoch)`, even if it is then refused for another reason. A
  higher epoch always means a newer holder.
- **S2. Destructive matching.** "Payment history is refused" contradicts whole-word matching, because `\bpay\b`
  does not match "Payment". Match word **prefixes**, case-insensitively: `\b(delete|cancel|refund|pay|transfer)`.
  Over-refusing is the safe direction.
- **S3. Subprotocol.** Offer two values, `processlens.v1` and `pl.token.<t>`. The hub selects `processlens.v1`.
  Browsers require the server to pick one offered value, and this way the token is never echoed back.
- **S4. Host check.** Refuse any HTTP or WebSocket request whose `Host` is not `127.0.0.1:<port>` or
  `localhost:<port>`. It is a cheap guard against DNS rebinding on the `POST /executor/sessions` route.
- **S5. Claim only when paired.** The browser worker claims a run only after `peer_ready`, meaning the extension
  is attached, not merely when its own socket is open.
- **S6. `close` detaches, it does not end the session.** As written, the session ends, yet
  `test_reconnecting_and_resuming_continues_the_run` needs the extension to re-attach to the same session (R5).

## Answers to §14

1. **`refused` as a seventh status: yes.** A dispatch-time refusal going `submitting → failed`: yes. No input was
   sent, and that is evidence, as with `not_dispatched`. The ticket's "no record `failed`" on a challenge refers to
   the pre-commit path, which R6 handles.
2. **Re-driving a half-filled tab: accepted.** Every recipe we have starts the record with `navigate`, and
   `clear_first` is the default.
3. **Executor-side id minting: yes**, with uuids (R7).
4. **The `close` endpoint: accepted**, as a detach (S6).
5. **The `Sec-WebSocket-Protocol` token: accepted**, with S3.
6. **Playwright for Python: yes**, with R9.
7. **§9 scope touches: all accepted.** Also accepted: the `_check` rendering fix (R2) and the `.gitignore` line
   (R9).

## Tests to add (beyond §11)

| change | test |
|---|---|
| R1 | `test_a_lost_branch_check_takes_neither_branch`, `test_a_refused_branch_check_takes_neither_branch`, `test_an_unanswered_confirmation_probe_parks` |
| R2 | `test_a_templated_branch_condition_reaches_the_executor_rendered` |
| R3 | `test_a_late_ack_never_fires_the_next_commands_on_dispatched` |
| R4 | `an expired command is refused before any input is sent` (vitest), `test_no_reply_within_budget_pauses_the_run` |
| R5 | `a restarted worker re-attaches with the resume token` (vitest), `test_a_resume_token_is_single_use` |
| R6 | `test_a_challenge_releases_a_pre_commit_record_instead_of_parking`, `test_peer_gone_loses_the_in_flight_command_at_once` |
| R7 | `test_a_takeover_workers_first_command_is_not_a_duplicate` |

## Next

Amend the plan with a new section, "Changes from T1 review", covering R1–R9 and S1–S6. Commit it with this file,
then proceed straight to implementation, in the §10 commit order. R1 and R2 belong in commit 3, with their tests.
