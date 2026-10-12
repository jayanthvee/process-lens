# PL-014 plan: connect the runner to the real extension

Status: **Step 0, for T1 review.** No implementation commit exists yet.
Author: T2 · Reviewer: T1 (this plan, then the code and break-it tests) · Branch: `pl-014` · Depends on: PL-013 (merged, `main` = `a9817c9`)
Ticket: `internal/agent/tickets/PL-014.md` (committed here as `44ddbbe`). Related: `internal/agent/proposals/P-003-recipe-reconcile.md` (parked decision; this plan implements PL-014's fallback, not P-003).

Where this plan and the ticket disagree, the ticket wins. Where both are silent, §9 flags the touch and §14 asks.

---

## 0. How to read this plan

Six things were owed: the message types and their fields (§2); the hub's pairing and security model (§3); the
extension's command state machine including reconnect (§4); how the real executor maps onto the `Executor`
protocol (§5); the fixture site design (§7); and a named test per acceptance criterion (§11).

Two further sections exist because the work demands them: §6 records, per PL-010 hardening item, whether the
existing code already satisfies it (three do not, and two only partly), and §9 lists every path the ticket's
Scope does not name but the change forces. §10 is the commit order this will be implemented in, which is also
the order it should be reviewed in.

This is the largest ticket so far: a new transport, a new protocol, a new command mode in the extension, a
fixture site, and a browser e2e harness. §10 is deliberately sequenced so that each commit is independently
verifiable and a stop can be made at any of the four checkpoints without leaving the tree broken.

---

## 1. Ground truth: what exists today

Established by reading the tree, not from memory. Line numbers are from `main` at `a9817c9`.

**The executor seam (Python).** `services/runner/executor.py` defines the whole contract: a `StepCommand`
frozen dataclass (`command_id, run_id, run_record_id, record_key, idempotency_key, lease_epoch, step: dict,
commit: bool, expires_at: float`), a `StepResult` (`status`, `observed`, `error`, `rung`), a `ConditionResult`
(`passed`, `observed`), and a four-method `Executor` Protocol: `prepare`, `dispatch(command, on_dispatched)`,
`perform(command, on_dispatched)`, `check_condition(condition, *, timeout_ms, step_id, record_key)`.
Failures are **values, not exceptions**: `StepStatus = Literal["ok","rejected","locator_failed","timeout",
"lost","not_dispatched"]`.

**The run loop's order around a commit** (`services/runner/run_loop.py:419-540`): `prepare` (no input, record
still `filling`) → policy gate → `begin_commit` (write-ahead to `submitting`, committed) →
`dispatch(command, on_dispatched)` where `on_dispatched` writes `submitted_unverified` → condition check →
verify or reconcile. `not_dispatched` → `failed`; acked + `rejected` → `failed`; acked and anything else →
`reconciling`. Crash points: `after_submitting`, `after_ack`, `after_dispatch`, `after_assert`.

**Pause reasons the loop already uses:** `canary_failed` (179), `records_not_exit` (206), `internal_error`
(250), `duplicate_detected` (558, 698), `awaiting_approval` (657). `_hand_back` (release_run) is called at
156, 183, 210 — **not** on the `internal_error` early return, which is the PL-013 follow-up the ticket names.

**`_command`** (771-807) renders the step and builds the `StepCommand`, with
`command_id = f"{record.id}:{step['id']}"` and `expires_at = clock.time() + assert_after_timeout/1000`. The
`command_id` is therefore **not unique across a legitimate retry of the same step** — see §2.6, which is one of
the two places this plan deliberately deviates from a naive reading of the ticket.

**The worker** (`services/runner/worker.py`) is synchronous and constructor-injected
(`executor`, `reconciler`, `clock`, `crash_hooks`, `connect`, `heartbeat_*`). It runs a `Heartbeat` thread and
refuses to claim runs today (`main()` prints "no browser executor is connected yet (PL-014)";
`--executor` has `choices=["disconnected"]`).

**Reconcile** (`services/runner/reconcile.py`) has `Reconciler`/`NoopReconciler`/`ReconciledDiff`/`outcome_of`
and **no `ParkingReconciler`**.

**The API** (`services/api/main.py`) is a module-level `app` with three routers, each `APIRouter(prefix=
"/api/v1")`. No auth dependency anywhere. **No WebSocket server code exists anywhere in the repo.**

**Dependencies.** `pyproject.toml`: `fastapi>=0.115`, `uvicorn[standard]>=0.32`, `psycopg[binary]>=3.2`,
`jsonschema>=4.23`; dev extras `httpx, pre-commit, pytest, ruff`. Installed: fastapi 0.142.4, starlette 1.7.0,
uvicorn 0.54.0, **websockets 17.2** (so `websockets.sync` is available), httpx 0.28.1. **Playwright is not
installed anywhere** — not in `.venv`, not in `apps/extension/package.json` (whose dev deps are `@types/chrome,
@types/node, ajv, esbuild, jsdom, typescript, vitest`). `internal/docs/ARCHITECTURE.md` already permits
`playwright` (python) in `tests`, and `puppeteer-core` in `apps/extension` — that YAML is the authority for
where the e2e driver may live.

**The extension.** MV3, `minimum_chrome_version: 116`, `permissions: ["activeTab","storage","debugger",
"webNavigation"]`, `host_permissions: ["<all_urls>"]`, service worker `type: "module"`. The only WebSocket today
is **outbound and one-way**: the recorder streams JSON lines to `status.wsUrl`
(`src/background/service-worker.ts:107-143`). There is no inbound protocol, no command state, and no keepalive.
`webNavigation` is referenced **nowhere** in `src/` (grep: 0 hits).

**The PL-010 executor** is free functions behind `runRecipe` (`src/executor/executor.ts`), with clean seams:
`CdpTransport`, `RunControl`, `FrameContexts`, and an injectable page-side resolver source. `validateRecipe`
and `assertAllowedOrigin` already exist. `dispatch.ts` already scrolls-into-view, re-measures, and hit-tests
before a click; `assertions.ts` already polls conditions with a `sleep`/`now` timer seam and a closed predicate
set.

**Tests today:** `tests/` holds only `db/` and `runner/` (Python). `apps/extension/tests/` holds 10 vitest
files. `tests/e2e/`, `tests/contract/`, `tests/integration/`, `tests/breakit/` do not exist. **`fixtures/` is
empty** (one `.gitkeep`). No test in the repo starts an HTTP server; the only local server anywhere is the Node
one inside `apps/extension/scripts/e2e-smoke.mjs`, which spawns a Chromium-family binary with
`--load-extension=dist` and drives it over the DevTools WebSocket. That script is the working precedent for
Requirement 7 and it is **not** in CI.

**CI** (`.github/workflows/ci.yml`): `lint-and-test` (ruff, `python -m db.migrate`, pytest), `secret-scan`
(gitleaks), `extension` (`npm --prefix apps/extension run check`). No browser job. `make` is not installed on
this machine, so the gates run as raw commands.

---

## 2. The protocol

### 2.1 Envelope and versioning

Every message, in both directions, is a JSON object with `protocol` and `type`:

```json
{ "protocol": 1, "type": "dispatch", ... }
```

`protocol` is `1`. **Both ends refuse a message whose `protocol` is not exactly `1`**, before acting on
anything else in it, and the hub refuses a socket whose first message is not `1`. The rule is fail-closed: an
unknown `type` or a missing `protocol` is an error, never ignored. This is the versioning the ticket asks for,
and it is the cheapest possible forward-compatibility story: a v2 must be negotiated, not guessed.

The hub adds exactly two message types of its own, which are **control, not relay**: `peer_ready` and
`peer_gone`. Rationale in §3.4. Third addition, flagged: `hub_error` (§2.5).

### 2.2 Runner → extension

Three types, as the ticket lists.

**`prepare`** — resolve and hit-test the target, send no input.

| field | type | meaning |
|---|---|---|
| `protocol` | `1` | version |
| `type` | `"prepare"` | |
| `command_id` | string | unique **on the wire** (§2.6) |
| `run_id` | uuid string | |
| `run_record_id` | uuid string | |
| `lease_epoch` | integer | the run's lease epoch at send time |
| `expires_at` | integer | epoch **milliseconds**; after this the extension must refuse |
| `step` | object | the rendered step: `id`, `action`, `targets`, `within`, `frame`, `value`, `clear_first`, `option_match`, `timeout_ms` |
| `commit` | boolean | true when this step is the record's commit |
| `allowed_origins` | string[] | from `meta.allowed_origins` |

**`dispatch`** — perform the action. Same nine fields (`type: "dispatch"`). The same message serves a commit
step and a non-commit step; `commit` distinguishes them. `Executor.dispatch` and `Executor.perform` therefore
both send `dispatch`, with `commit: true` and `commit: false` respectively. This is why the protocol needs only
the three types the ticket names rather than one per Python method.

**`check`** — evaluate a condition, polling to its timeout. Fields: `protocol`, `type`, `command_id`, `run_id`,
`run_record_id`, `lease_epoch`, `expires_at`, plus:

| field | type | meaning |
|---|---|---|
| `condition` | object | a rendered recipe condition |
| `timeout_ms` | integer | poll budget |
| `step_id` | string \| null | diagnostics only |
| `record_key` | string \| null | diagnostics only |

**Not on the wire:** `record_key` and `idempotency_key` are deliberately omitted from the three messages. The
extension performs no ledger write and must not learn the run's idempotency key; `record_key` appears only in
`check`, as a diagnostic label the extension echoes back in `observed`. `StepCommand` carries both; the mapping
in §5 drops them. Flagged in §9 as a narrowing, not a widening.

`step` values are **already rendered** by the runner (`_render_value_source`, `_render_target`), so the
extension never resolves `{{row.email}}` itself and never reads the input row. That is what keeps the input
schema and the record key on the backend side of the boundary.

### 2.3 Extension → runner

**`ack`** — `{protocol, type: "ack", command_id}`. Sent the moment a commit's trusted input event has been
dispatched (after `mouseReleased`, or after the last keystroke) and **before** waiting for anything else. It
carries nothing but the id: `on_dispatched` is not a place to be clever.

**`result`** — `{protocol, type: "result", command_id, status, rung, observed, error}`.

- `status` ∈ `ok`, `locator_failed`, `not_dispatched`, `rejected`, `refused`, `timeout`, `lost`.
- `rung` is `1..5` or `null` (§6, first two rows).
- `observed` is a small dict for the step event: `{by, tag, value_after, text, url, frame_depth}` — whatever
  the action recorded, never the whole page.
- `error` is a message for a person, and it is the only place a refusal reason lives.

**`condition`** — `{protocol, type: "condition", command_id, passed, observed}`.

**`event`** — `{protocol, type: "event", kind, detail}` with `kind` ∈ `detached`, `tab_closed`,
`login_required`, `challenge`, `policy_blocked`, `off_origin`. `detail` is a short human string. Events are
**unsolicited**: they may arrive between commands, and the runner must be able to read them while waiting for a
result (§5.2 fixes the race).

**Keepalive** — `{protocol, type: "ping"}` / `{protocol, type: "pong"}`, both directions, every 20 s while
connected. Not a recipe command; the hub relays them like anything else.

### 2.4 The status vocabulary, and how it maps onto the ledger

The wire's seven statuses are the ticket's. The Python `StepStatus` has six; `refused` is the seventh and is
**not** in it. The mapping, and the reason for each:

| wire | Python | why |
|---|---|---|
| `ok` | `ok` | same |
| `locator_failed` | `locator_failed` | a failed `prepare`; from `filling` this parks |
| `not_dispatched` | `not_dispatched` | the only status that proves no input was sent |
| `rejected` | `rejected` | the destination itself refused the save (acked, then evidence) |
| `refused` | **`refused`** (new) | see below |
| `timeout` | `timeout` | acked and unproven → reconcile |
| `lost` | `lost` | acked and unproven → reconcile |

**`refused` gets its own value, and the run loop handles it explicitly.** The alternative — folding `refused`
into `not_dispatched` — is nearly right, because a refusal does mean nothing was sent; but `not_dispatched` from
a non-commit step with `on_failure: "fail"` fails the record (`_simple_step`, `run_loop.py:608-620`), and the
ticket is explicit that a destructive-control refusal and an off-origin step **park**. The destructive guard is
a safety rule about what the extension will not do, and a recipe the compiler wrote must not be able to turn it
into a `failed` record. So: `fail_to = "parked" if result.status == "refused" else ("failed" if
step.get("on_failure") == "fail" else "parked")`. This widens `StepStatus` in `services/runner/executor.py`,
which the Scope does not name — flagged in §9 as a one-line, unavoidable change.

The commit path needs care, and the resolution is to make the guard fire where parking is legal. The guards run
before **every** command, so a commit step's `prepare` is guarded too, and a refusal therefore arrives on
`prepare` — where the record is still `filling`, and `filling → parked` is legal. That is the path the
destructive-control and off-origin acceptance criteria exercise.

The residual case is a guard firing on the `dispatch` that follows a successful `prepare` (the page changed
between the two messages: a cookie banner, a redirect). There the record is already in `submitting`, and the
ledger has **no** `submitting → parked` (legal from `submitting`: `submitted_unverified`, `reconciling`,
`failed`). The plan takes `submitting → failed` with the refusal reason. The alternative —
`submitting → reconciling → parked` — would end in a park, but `reconciling` asserts "the destination may
already have this record", which is a lie when the extension refused before sending any input; `failed` is what
LEDGER_SPEC reserves for "a step that failed with evidence", and a named refusal is evidence. So the honest
rule is: **a refusal parks wherever parking is legal, and fails from `submitting`, where it is not.** The e2e
destructive-control test asserts the parking path, because that is where the guard actually fires.

### 2.5 Errors at the transport layer

Three failures that are not step results:

- `peer_unavailable` — the hub answers the sender immediately when the other side is not connected
  (`{protocol:1, type:"hub_error", code:"peer_unavailable", command_id}`). Never queued, never retried by the
  hub.
- `bad_protocol` / `unauthorized` — the hub closes the socket with code `4400` / `4401` before reading a
  message.
- `session_expired` — a command for a session whose extension is gone is a `peer_unavailable`.

`BrowserExecutor` turns `peer_unavailable` into `StepResult(status="lost", error=...)` **and** raises the
`disconnected` flag that pauses the run (§5.4): "the extension is gone" is a pause, not a step failure.

**R1 — a `check` with no answer is never "false".** `ConditionResult` carries only `passed` today, which is
harmless against the fake and dangerous against a real page: a `check` can end with no answer at all (lost,
refused by a guard, `peer_unavailable`, or past the runner's own budget). Read as `False`, a duplicate-search
`branch` takes its `else` and **creates a record that already exists**. So `ConditionResult` gains
`status: Literal["ok","lost","refused"] = "ok"` (defaulted, so the fake is unchanged), and the rule is:
`passed is False` means one thing only — the extension polled to its timeout and the condition never held. A
no-answer is propagated and handled by the caller: §5.3 for `branch`/`when`, §5.6 for the fresh-confirmation
probe, and the existing not-passed path for `assert_after`.

**R4 — nothing may act after the runner has stopped waiting.** The wire expiry is not decorative; see §5.2.

### 2.6 `command_id` uniqueness

`run_loop._command` builds `command_id = f"{record.id}:{step['id']}"`. A legitimate retry of the same step in
the same record — a re-queued record, a `wait_for` retry — would therefore reuse it, and a strict
"repeated id is refused" rule would deadlock the run.

Resolution (R7 — corrected): **`BrowserExecutor` mints the wire `command_id` per send as `str(uuid4())`**, and
carries `StepCommand.command_id` alongside in the message as `step_ref`, for the runner's logs and for a person
reading a step event. The extension's dedupe window is then over wire ids, where "repeated" can only mean
"duplicate delivery of the same message", which is exactly what the rule exists to catch.

The earlier draft used `f"{command.command_id}#{n}"` with a per-process counter, and that is wrong: after a
worker crash the counter restarts at `#1`, so the takeover worker's first command for the same record and step
mints an id the extension still remembers from before the crash, and the extension refuses it as a duplicate.
That is precisely the crash-and-takeover e2e path (`test_a_takeover_workers_first_command_is_not_a_duplicate`),
so a per-process counter would break the acceptance criterion it was meant to serve. A uuid is unique across
processes and restarts.

---

## 3. The hub (`services/api/hub.py`)

### 3.1 Pairing

Attended, single user, per the ticket.

1. The person presses **Connect** in the popup. The popup sends `executor:connect` to the service worker.
2. The worker reads the active tab (`chrome.tabs.query({active:true, currentWindow:true})`), and calls
   `POST {hub}/api/v1/executor/sessions` with `{tab_url, tab_title}` and the header
   `X-ProcessLens-Client: extension`.
3. The hub creates an **unclaimed session**: `session_id` (`uuid4`), a one-time `session_token`
   (`secrets.token_urlsafe(32)`), and `expires_at` (now + 10 min). It returns `{session_id, session_token,
   protocol: 1, expires_at}`.
4. The worker opens `ws://{hub}/api/v1/executor/ws?session={session_id}` offering the subprotocols
   `processlens.v1` and `pl.token.<token>` (S3). Browsers cannot set headers on a WebSocket, and the server
   must select exactly one offered value, so the token travels as one subprotocol and the hub selects the
   other — the token is never echoed back into a response header. The hub ties that socket to the session and
   marks it claimed; the session token is consumed and never returned again.
5. **R5 — the resume token.** On every successful attach, the hub's **first** message to the extension is
   `{type:"session", resume_token: <secrets.token_urlsafe(32)>}`, and the previous resume token dies. This is
   what makes reconnect possible: the session token is single-use, so it cannot be the thing a reconnect
   presents. A detached session accepts exactly one re-attach carrying the current resume token within its
   TTL; a second socket presenting the same token, or any token after the TTL, is refused `4401`.
6. A worker started with `--executor browser --session <session_id>` connects to
   `ws://{hub}/api/v1/runner/ws?session={session_id}` with `Authorization: Bearer $PROCESSLENS_RUNNER_SECRET`.
   The hub binds that runner socket to the same session.
7. When both sockets of a session are attached, the hub sends `peer_ready` to each; when either goes, it sends
   `peer_gone` to the other.

**The session is bound to the tab by the extension, not the hub.** The hub records `tab_url`/`tab_title` for
display and diagnostics only. The extension stores the bound `tabId` in memory and refuses any command that
would act on a different tab; the hub never learns tab ids. That keeps "the extension owns no workflow state"
(D-004) intact on this axis: the hub has no opinion about which page is being driven.

### 3.2 Security model

Four checks, all before a message is read, in the spirit of "refuse, do not negotiate":

1. **Loopback peer.** The hub refuses any client whose address is not loopback (`127.0.0.1`, `::1`) with close
   code `4403`. The ticket says "by default the hub listens on 127.0.0.1 only"; a code check makes it a
   guarantee rather than a deployment note, and it holds even if uvicorn is started with `--host 0.0.0.0`.
2. **S4 — host header.** Every request (HTTP and WebSocket alike) is refused unless its `Host` header names
   `127.0.0.1:<port>` or `localhost:<port>`. This is the cheap guard against DNS rebinding: a page at
   `evil.example` that resolves to `127.0.0.1` still sends `Host: evil.example`, and is refused before it
   reaches `POST /executor/sessions`.
3. **Extension channel: origin.** `Origin` must be exactly `chrome-extension://<id>`, with `<id>` pinned to
   `PROCESSLENS_EXTENSION_ID`. An unset/mismatched id, a `http(s)://` origin, or a missing origin is `4401`
   before the token is even examined. A web page that opens a WebSocket to localhost sends its own `http(s)`
   origin and is refused.
4. **Extension channel: session token or resume token.** The offered subprotocols must include
   `processlens.v1` and a `pl.token.<token>` whose value matches either an **unclaimed, unexpired** session
   (the session token) or a **detached** session's current resume token (R5). Constant-time compare
   (`secrets.compare_digest`). One socket per session.
5. **Runner channel: secret.** `Authorization: Bearer <secret>` compared against
   `PROCESSLENS_RUNNER_SECRET` with `secrets.compare_digest`. **If the variable is unset the runner channel
   refuses everything** (fail closed — an unset secret must not mean "no secret required"). A runner socket that
   also carries a `chrome-extension://` origin is refused.

Only configuration in `os.environ`; nothing is logged, and the token/secret never appear in a response after
creation. No CORS headers are ever set, so no page can read a response even if it could make the request.
The `POST /api/v1/executor/sessions` route requires `X-ProcessLens-Client: extension`; a cross-origin page that
sends a custom header triggers a preflight the hub never approves, so the check is real defence, not decoration.

Accepted residual risk, stated for T1: any *local* process can POST for a session and then sit on the
extension channel claiming a session it will never use, and a local process can read the environment. Local
processes are already trusted to touch the ledger database in this design (D-005), and the token exists to stop
*web pages*, not local malware.

### 3.3 Routing

The session comes from the socket's own query string, not from the message body. A runner socket bound to
session A can therefore only reach the extension socket bound to session A, and cannot name a different session
in a payload to escape it. An unknown, expired, already-paired, or extension-less session means
`peer_unavailable` to the sender, immediately.

### 3.4 Dumb relay

- One `asyncio.Queue` per socket. The reader loop pops a message, checks `protocol`, and writes it to the
  peer's socket. Nothing else.
- **No interpretation**: no reordering, no coalescing, no dedupe, no rewrite of ids. The relay does not know
  what a `step` is.
- **No buffering, no replay**: if the peer is absent, the sender gets `peer_unavailable` and the hub forgets the
  message. It does not hold it for a reconnect — because the extension refusing a stale command is the design,
  and a relay that replays would defeat it.
- `peer_ready` / `peer_gone` are the hub's own two control messages; they are not relayed. **R6: `peer_gone` is
  authoritative, not a hint.** When the extension's socket drops — which is exactly what the hub's `close` test
  does — the *runner's* socket stays up, so "the socket dying under `BrowserExecutor`" never happens. On
  `peer_gone` the runner's in-flight command is `lost` at once and the run pauses `executor_disconnected`, even
  if the extension reconnects 500 ms later. `peer_ready` is what the browser worker waits for before it claims
  a run (S5).
- `session` is the hub's third control message: the first message an extension socket receives (R5).
- A socket that exceeds a small per-socket queue (`1024` messages) is closed `4408`: a peer that cannot keep up
  must not grow the hub's memory in a run that could last an hour. The runner is strictly one-command-at-a-time,
  so this can only fire when the extension is wedged.

### 3.5 Lifecycle and the one operational control

`POST /api/v1/executor/sessions/{session_id}/close` (runner secret required) **detaches** the session's
extension socket (S6: it does not end the session). It exists because an acceptance criterion requires a
**deterministic** extension disconnect mid-save, and the alternatives are worse: stopping the MV3 service worker
over CDP is flaky and needs the test to command a browser-internal target; a test-only hook inside the extension
would be production code that only tests use. A "drop this socket" control is also what an operator needs when
a session is wedged. Flagged in §9/§14.

Because it detaches rather than ends the session, the sequence is: the extension sees its socket close and
reconnects with its resume token (R5); the runner sees `peer_gone`, loses the in-flight command at once and
pauses `executor_disconnected`; on resume the run continues against the same session. That is exactly what
`test_reconnecting_and_resuming_continues_the_run` needs, and it is why S6 and R5 are the same change.

---

## 4. The extension in command mode

### 4.1 Files

| file | role |
|---|---|
| `src/shared/command-protocol.ts` | the wire types (§2), a `parseMessage` validator, and `PROTOCOL = 1` |
| `src/executor/step-runner.ts` | `prepareStep`, `dispatchStep`, `checkCondition` — the per-step work extracted from `runRecipe`, so command mode and PL-010 mode share one implementation |
| `src/executor/guards.ts` | the four guards (origin, challenge, login, destructive) and attach-error classification |
| `src/background/command-session.ts` | the state machine, the socket, the keepalive, the per-session memory |
| `src/popup/popup.{ts,html}` | the Connect button, the hub field, the session state, the run state |
| `src/background/service-worker.ts` | wires `executor:connect`/`executor:disconnect` and the local-test-run path |

`step-runner.ts` is the load-bearing refactor: `runRecipe` becomes a loop over `prepareStep`/`dispatchStep`
instead of owning the bodies. The extension's existing 10 test files must stay green through it — that is the
regression signal, and §6's changes ride on the same extraction.

### 4.2 The state machine

```
idle ──connect──▶ pairing ──201──▶ connecting ──ws open──▶ ready
  ▲                  │                 │                    │
  │                  │ non-201         │ close/error        │ command
  │                  ▼                 ▼                    ▼
  └──disconnect── disconnected ◀───────┘              executing
                       │                                   │
                       │  backoff 500ms → 1s → 2s → 4s (cap) │ result sent
                       └──────────────▶ connecting          ▼
                                                        ready
```

- **`idle`**: nothing bound. A command received here is impossible (no socket) and is refused by the hub.
- **`pairing`**: the POST is in flight. A second Connect is ignored, not queued.
- **`connecting`**: the socket is opening. Reconnect backoff: 500 ms, 1 s, 2 s, 4 s, then 4 s forever, with
  jitter, until Disconnect or the tab closes. **Commands are never queued while disconnected** — the runner
  gets `peer_unavailable` and the ledger records an outcome, which is the whole point.
- **`ready`**: connected, no command in flight. An unbounded idle is fine — the keepalive (§4.5) is what keeps
  the worker alive, not the commands.
- **`executing`**: exactly one command in flight. The state machine is **serial by construction**: a second
  command arriving while `executing` is refused (`refused`, "a command is already in flight"), never queued and
  never run concurrently. One tab, one action, one at a time.
- **`disconnected`**: the socket closed. The in-flight command, if any, is abandoned and **reported `lost` on
  the next successful connection** (§4.4) — and never re-run.

Per-session memory. Two tiers, and the split is deliberate (R5):

- **In memory only** — `highestEpoch: Map<run_id, number>`, `recentIds: Set<command_id>` (a bounded ring of the
  last 256 wire ids), `inFlight: {command_id, run_id, lease_epoch, commit, dispatched} | null`. An MV3 service
  worker is suspended and restarted routinely, and losing this is **acceptable**: the hub never replays, so a
  new socket only ever carries new messages. A restarted worker cannot be handed a stale command, so it has
  nothing to compare ids against.
- **In `chrome.storage.session`** (memory-backed, survives a service-worker restart, dies with the browser) —
  `{hubUrl, sessionId, resumeToken, boundTabId}`. This is what makes reconnect-after-restart work at all (R5).
  It holds no run state, no row, and no ledger fact, so D-004 is untouched: the extension still owns no
  workflow state, only the address of the socket it is supposed to be on.

### 4.3 The refusal rules

Before acting on any command, in this order, each producing `result status=refused` with a reason:

1. `protocol !== 1` → refuse.
2. `lease_epoch < highestEpoch[run_id]` → refuse ("a stale command from an earlier lease") and record the epoch.
3. `command_id ∈ recentIds` → refuse ("a duplicate of a command already run").
4. `expires_at <= now()` → refuse ("the command expired before it could be acted on").
5. bound tab missing or closed → `event tab_closed` and refuse.
6. `state === "executing"` → refuse (serial, §4.2).

A refusal is a `result`, never an `event`: the runner needs a per-command answer to keep the ledger honest.

**S1 — the epoch rule, stated once and unambiguously.** After the stale check, *every* well-formed command
advances the watermark before any other rule is applied:

```
highestEpoch[run_id] = max(highestEpoch[run_id], lease_epoch)
```

even when the command is then refused for a different reason. A higher epoch always means a newer lease
holder, so a command that carries one is evidence that everything from the previous holder is stale —
including commands the extension has not yet refused. The earlier draft of this section said the opposite in
one place and then talked itself out of it; this is the rule.

### 4.4 Reconnect, and the `lost` report

On `close`:
- if a command was in flight, keep `{command_id, run_id, lease_epoch, commit, dispatched}` in `unreported`;
- attempt reconnect (backoff above) **with the resume token from `chrome.storage.session`** (R5) — not the
  consumed session token, which the hub will refuse;
- on the next successful `ready`, **first** send `result {command_id, status: "lost", error: "the connection
  dropped while this command was in flight"}` for each `unreported` entry — with `status: "lost"` regardless of
  whether the input had been dispatched, because after a drop the extension cannot prove otherwise;
- then accept new commands.

The in-flight command is never re-run, and the extension never holds the result of a command it will not send:
`unreported` is cleared when sent. If the extension dies before reconnecting, the runner's own budget expires
and it records `lost` there — the two mechanisms are independent on purpose, because either side can be the
survivor.

**R5 — why storage.session, and what is deliberately lost.** An MV3 service worker is suspended and restarted
routinely, so `sessionId`, `resumeToken` and `boundTabId` cannot live in module scope: a restart would leave the
extension unable to re-attach, and the run would be stranded. They live in `chrome.storage.session` (memory-
backed, survives a restart, dies with the browser). `highestEpoch` and `recentIds` are **deliberately not
persisted**: the hub never replays, so a fresh socket only ever carries new messages, and a restarted worker has
no stale command to compare ids against.

### 4.5 Keepalive

Both ends exchange `ping`/`pong` every 20 s while connected. `minimum_chrome_version` is already 116, and from
Chrome 116 an extension service worker stays alive while a WebSocket is active and receiving traffic; a 20 s
ping is comfortably inside the ~30 s idle window and inside the runner's 5 s dispatch budget. The timer is a
seam (`now`/`setInterval` injectable) so the unit test can prove the ping fires without waiting 20 s, and the
e2e test (§11, keepalive) proves it against real Chrome.

If the field shows the worker still being suspended, the fallback is a long-lived `chrome.runtime.connect` port
to the popup plus an offscreen document. Not planned; noted in §13 as the risk this design carries.

### 4.6 The guards: before every command, in order, never acting

1. **Origin.** Read the target frame's `location.origin` (in the frame's own context). It must be in
   `allowed_origins`. Otherwise `event off_origin` **then** `result refused` (R6: the event goes first). This is
   checked per command, not once per run: a page can navigate between steps.
2. **Challenge or login.** One page-side probe expression returns
   `{challenge: bool, marker: string, password_field: string | null}`:
   - a challenge iframe whose `src` matches `challenges.cloudflare.com`, `recaptcha`, `hcaptcha`, or
     `turnstile`; or a document title containing "Just a moment";
   - a **visible** password field (`input[type=password]`, non-zero box) whose element is not the target of the
     current step.
   On a challenge, `event challenge` and refuse the command. On a login page, `event login_required` and
   refuse. **Neither is ever solved, skipped, or bypassed** (rule 19, D-017): a person logs in.
3. **Destructive control (S2 — corrected matching).** For a `click`, read the target's accessible name (the
   resolver already computes it). Refuse when it matches `/\b(delete|cancel|refund|pay|transfer)/i`, unless the
   step sets `allow_destructive_control: true`. `result refused` with the reason naming the matched prefix and
   the control.
   **Prefix, not whole word.** The earlier draft said whole-word *and* gave "Payment history" as an example of
   a refusal, which cannot both be true: `\bpay\b` does not match "Payment". Matching the prefix does, and it
   also catches "Payment", "Transferring", "Cancelations" and "Refunds". Over-refusing is the safe direction,
   and the flag is the escape hatch.
4. **R4 — the expiry re-check.** Immediately before the first input event, and after the guards, the scroll and
   the hit test, re-read `expires_at`. If it has passed, refuse and send **no input**. The command was fresh
   when it arrived; it may not be fresh by the time the target is measured, and the runner uses the expiry to
   decide how long it will keep waiting (§5.2). This is the extension's half of R4.
5. **Debugger attach.** `attachDebugger` currently rejects with `cannot attach the debugger: <lastError>`.
   Classification: a message matching `/host access is restricted by policy/i` or `/screenshot capture is
   restricted by policy/i` (or any `policy` + `restricted`) becomes `event policy_blocked` with the readable
   sentence *"your administrator's Chrome policy blocks ProcessLens"*, and the run pauses `policy_blocked`.
   Anything else keeps the current behaviour (park/fail with the raw message). The strings are matched
   loosely on purpose: Chrome's exact wording varies by version and platform, and a miss here means a person
   sees a raw error rather than the actionable sentence — annoying, not unsafe.

The guards run **before** the command's action, so `prepare` also runs them: a command must never even measure a
target on a page that shows a challenge.

### 4.7 The popup

The existing recorder controls and the PL-010 run controls stay. Added:

- **Hub** (a text field, default `http://127.0.0.1:8000`, persisted with the existing settings key).
- **Connect / Disconnect**, showing `idle | pairing | ready | executing | disconnected` and the bound tab.
- The PL-010 **Run** button stays and is labelled **"local test run"**, with a line under it saying it does not
  write to the ledger. The ticket requires this labelling; it is also the honest description, since PL-010 mode
  writes nothing anywhere.

### 4.8 PL-010 mode is untouched in behaviour

`executor:start/pause/resume/abort` keep working exactly as they do. The only change under them is the
`step-runner.ts` extraction and the §6 hardening, both of which the existing tests cover.

---

## 5. The real executor and the runner

### 5.1 Mapping `<Executor>` onto the wire

| `Executor` method | wire | returns |
|---|---|---|
| `prepare(command)` | `prepare`, then wait for `result` | `StepResult` (status mapped per §2.4) |
| `dispatch(command, on_dispatched)` | `dispatch`; on `ack` call `on_dispatched()` **once**; then wait for `result` | `StepResult` |
| `perform(command, on_dispatched)` | `dispatch` with `commit: false` | `StepResult` |
| `check_condition(condition, *, timeout_ms, …)` | `check`, wait for `condition` | `ConditionResult` |

`on_dispatched` is called from the reader thread/loop that sees the `ack` — see §5.2 for why that is safe.

### 5.2 Transport choice: a synchronous client, and where events are read

`websockets.sync.client.connect` (websockets 17.2 is installed). The run loop, the worker, and `on_dispatched`
are synchronous, and `on_dispatched` writes `mark_submitted_unverified` on the *same* connection the loop is
using — so `on_dispatched` must fire on the same thread that called `dispatch`. An async client would force the
whole runner onto an event loop or into a bridge thread, for no benefit.

So `BrowserExecutor` owns one blocking socket and one reader that **runs inside the calls it makes**:

- `dispatch` blocks reading messages until it has seen `ack` (then calls `on_dispatched`), the `result`, or a
  deadline. Any `event` read on the way is appended to the event queue and the read continues — no event is lost
  because it arrived while a result was expected, and no thread is involved.
- `prepare`, `perform` and `check_condition` do the same, minus the ack.
- Two deadlines, both real: the **ack budget** is `DISPATCH_BUDGET_SECONDS` (`run_loop.py:76`, 5 s) and the
  **result budget** is the step's own `timeout_ms` plus the same budget. A missing ack inside 5 s is `lost` (the
  ticket says so explicitly); a missing result inside its budget is `timeout`.
- **R4 — the wire `expires_at` is `send + the budget of the command it is on`, and it is not
  `StepCommand.expires_at`.** That field is the *runner's own* budget, computed once in `_command` as
  `clock.time() + condition_timeout(assert_after, fallback)/1000` (`run_loop.py:805-806`) — and for a commit
  whose `assert_after.timeout_ms` is small (the schema allows 100 ms) it can already be in the past by the time
  the `dispatch` is sent, after `prepare` and the fresh confirmation ran. So:
  - a **`dispatch` that expects an ack** gets `expires_at = now_ms + ack budget (5 s)` — the extension may act
    on it only as long as the runner will still be waiting for the ack;
  - **every other command** gets `expires_at = now_ms + its own result budget` (the `check`'s or the step's
    `timeout_ms`, plus the same 5 s of slack).

  This closes a real hole the review found: a flat 60 s TTL against a 5 s ack budget leaves 55 s in which the
  runner has already written the dispatch off (`lost` → `reconciling` → parked) while the extension can still
  click Save. The extension also **re-checks the expiry immediately before the first input event** (§4.6).
- **R4 — no reply within the budget, socket still up.** `BrowserExecutor` sends nothing further and flags the run
  to pause `executor_disconnected`. It never moves on to the next record while the extension may still be acting
  on the previous command. The socket has not died, so the pause is driven by the missed budget, not by a close
  event; the flag is drained by the loop exactly as a `peer_gone` is (§5.3).
- The socket being closed or refused at any point is `lost`, and it sets the `disconnected` flag (§5.4).

The worker keeps one connection for the life of the process and reconnects between runs; a run never starts
without a live socket, which is enforced where the run is claimed (the worker refuses to claim with the browser
executor when the socket is down — the same fail-closed shape as `DisconnectedExecutor` today).

### 5.3 The event queue, and how the run loop maps it

`BrowserExecutor` exposes `take_events() -> list[tuple[str, str]]` (drain). The run loop drains it **at the top
of the record loop, immediately after every executor call, and — R6 — *before* it interprets any result**. A new
optional constructor argument `events: Callable[[], list[tuple[str, str]]] | None = None` keeps `FakeExecutor`
tests unchanged and lets a test inject any sequence.

**R6 — the ordering, and why it matters.** The extension sends an `event` **before** the `result refused` it
causes, and the loop processes drained events before it reads a result. Without both halves, a challenge would
first park the record through the `refused → parked` rule (§2.4) and only then pause the run — but the ticket
says a pre-commit record is *released*, not parked. Draining first means the event decides the record's fate
before the refusal is ever interpreted. `peer_gone` is drained the same way and is authoritative (§3.4).

Mapping, per the ticket:

| event | what the loop does |
|---|---|
| `challenge`, `login_required`, `policy_blocked` | pause the run with that exact `pause_reason`; a record **before** its commit is released `filling → pending` (also `claimed`/`prechecked` → `pending`); a record inside the commit window goes through reconcile |
| `off_origin` | park the record (`filling → parked`, reason from the event's detail) |
| `detached`, `tab_closed` | in-flight commands are `lost`, then pause the run `executor_disconnected` |
| (socket loss with no event) | same as `detached` |

"Released `filling → pending`" is not a new mechanism: **the run loop already does exactly it.**
`_run_steps` re-reads `runs.status` at every step boundary and, when it is no longer `running`, writes
`self._transition(conn, record, "filling", "pending", "paused")` — `run_loop.py:361`. `("filling", "pending")`
is a legal, unguarded pair in `packages/ledger/transitions.py`, and `recover_run`'s `_RELEASE_WORKING` moves the
same three working states back to `pending` after a crash. The work here is therefore to route the new event
kinds into that existing pause path (a `paused` run status plus the pause reason), not to invent a release. A
resume re-queues the run and the released records are claimed again, consuming an attempt — the ledger's
designed retry semantics, unchanged from today's pause behaviour.

### 5.4 Pauses need a hand-back (R8 — corrected)

A record in `filling` that hits a run-level event is released to `pending` (§5.3); every new pause path ends with
`_hand_back`, exactly as `canary_failed` and `records_not_exit` do — otherwise the run stays claimed by a worker
that has stopped, and "resume" would have nothing to resume.

**Correction to the earlier draft:** `awaiting_approval` and `duplicate_detected` already hand back, and always
have. They set the run `paused` and return; the loop's own status check at `run_loop.py:156` then releases the
run on the next iteration. Only the `internal_error` path (`run_loop.py:168-169`) `return`s directly out of
`process_run` and skips that check, so **only `internal_error` needs the fix** — the ticket's PL-013 follow-up is
exactly this, and nothing else is a gap.

### 5.5 `ParkingReconciler`

`services/runner/reconcile.py` gains:

```python
class ParkingReconciler:
    """The default for a real run until P-003 is decided: a person checks the destination."""

    def search(self, record: RecordClaim) -> ReconciledDiff:
        return ReconciledDiff(matches=0, error=PARKING_REASON)


PARKING_REASON = "could not verify the save automatically; check the destination"
```

`outcome_of` already maps an error to `parked` and never to `pending`, so "it is never returned to `pending`"
is a property of the existing code path, which the test asserts directly. `Worker.__init__` keeps
`reconciler or NoopReconciler()` for the fake path; the CLI/wiring selects `ParkingReconciler` for
`--executor browser`.

### 5.6 Fresh confirmation, before the dispatch

In `_commit_step`, after `prepare` succeeds and **before** `begin_commit`:

```python
# one bounded evaluation, not the step's own polling budget: the question is "is the
# confirmation on the page now", and `_check` would otherwise poll for the full fallback timeout
verdict = self._probe_confirmation(
    conn, record, step["assert_after"]
)  # present/absent/unanswered/unsupported
if verdict in ("present", "unanswered"):  # R1: only a definite "absent" may save
    self._transition(
        conn,
        record,
        box.state,
        "parked",
        ALREADY_ON_PAGE_REASON if verdict == "present" else UNKNOWN_CONFIRMATION_REASON,
    )
    return "parked"
ALREADY_ON_PAGE_REASON = "the confirmation was already on the page before saving"
UNKNOWN_CONFIRMATION_REASON = "could not tell whether the confirmation was already on the page"
```

**R1 at the probe: no answer means park, never "absent".** The probe is the one place where a no-answer would
be consumed as a *negative*, and a negative here means "the page is clean, go ahead and save" — which can
duplicate a record whose confirmation the extension merely failed to read. So the probe has four outcomes,
not three: `"present"` (already there → park, the confirmation could not be trusted), `"absent"` (a real poll to
the timeout found nothing → save), **`"unanswered"` → park**, and `"unsupported"` (this executor cannot see
page state at all — the scripted fake — so the gate does not run, which is what keeps the existing suite
meaning what it meant). The two parked reasons are distinct on purpose: one says a stale confirmation was seen,
the other says the runner could not tell, and a person reading the ledger needs to know which. A plain
`bool | None` was not enough, because `None` had to mean both "the fake has no page" and "the page did not
answer", and those are opposite decisions.

`_confirmation_present` asks the **page**, so it must be a probe the executor can decline. It is gated on a new
capability on the `Executor` seam: `probe_confirmation(condition, *, step_id, record_key) -> bool | None`,
where `None` means "this executor cannot see page state". `FakeExecutor` and `DisconnectedExecutor` return
`None` (a scripted fake has no page, so it can never have a stale toast), which is what keeps every existing
PL-013 commit test meaning exactly what it means today; `BrowserExecutor` sends one `check` command and returns
the answer.

This gate is load-bearing, and the plan states it plainly because the obvious implementation is wrong.
`FakeExecutor.check_condition` returns `True` by default for an unknown condition (`testing.py:120-126`), so a
probe routed through the ordinary `check_condition` would answer "the confirmation is already on the page" for
every unscripted commit in the existing suite and park records that must verify. The runner tests that must now
change are named in §10: the fake gains `probe_confirmation -> None`, and the fresh-confirmation behaviour gets
its own tests with an executor that answers `True` and `False`.

Because a `check` costs a round trip, the probe uses `timeout_ms=100` (the schema's own minimum for a condition
timeout) rather than `_check`'s fallback: `_check` would otherwise read the *step's* `assert_after.timeout_ms`,
which is the budget for waiting after the save, not for asking whether the confirmation is already there. One
probe, at most one retry, and the answer is immediate.

Placing it before `begin_commit` matters: the record never enters the commit window, so `commit_attempted`
stays false and a later requeue is not blocked by the destination-check rule. A stale toast is exactly what the
fixture's `stale_toast` switch produces.

### 5.7 Worker and CLI wiring

`worker.py`: `--executor [disconnected|browser]`, `--session <session_id>`,
`--hub-url` (default `ws://127.0.0.1:8000`), `--runner-secret` (default `$PROCESSLENS_RUNNER_SECRET`).
`browser` requires `--session` and a secret, builds `BrowserExecutor`, connects before claiming, and passes
`ParkingReconciler` plus the event drain into `RunLoop`. `main()` stops printing "PL-014 is coming" and starts
the loop; with `disconnected` it still refuses to claim. `__main__.py` is unchanged (it already forwards to
`main`).

---

## 6. Requirement 5 (PL-010 hardening): what is already true, and what is not

Each row was checked against the code, not assumed. Where the answer is "already true", the work is a test that
would fail if it regressed — several items have **no** test today.

| item | today | work | test |
|---|---|---|---|
| ladder order: **sort by rung rank, not array order** | **not true** — `resolveTargetInPage` iterates `targets` in array order (`ladder.ts:359-364`) | sort a copy of `targets` by `LADDER.indexOf(by)`, stable | `ladder ranks before array order` |
| **report rungs as 1 to 5** | **not true** — reports the 0-based array index | report `rank + 1`; `StepReport.rung` and `observed.rung` become 1..5 | `a rung-1 match reports rung 1` |
| `within` must match **exactly one visible container** | **not true** — `container()` returns the first match (`ladder.ts:314-327`) | collect all matches, require exactly one *visible* one, else the rung reports "container not unique" | `an ambiguous within container fails the rung` |
| hit test: **not an ancestor** | **partly** — accepts `hit.contains(element)` (`ladder.ts:142`) | accept only `hit === element || element.contains(hit)` | `an ancestor at the click point is not a hit` |
| **refuse a click in a non-top frame** | **not true** — a frame-path step measures in the frame's own coordinates and clicks there, which is the wrong point in the top document | `measureTargetInPage` reports `topFrame: boolean`; `dispatchClick` refuses (`refused`) when false | `a click inside an iframe is refused` |
| `fill`: **`Input.insertText`** so `\n`/`\t` are characters | **not true** — types per character; `\n` becomes a real Enter (`dispatch.ts:91-107`, `156`) | build the text and send `Input.insertText`; keep `Input.dispatchKeyEvent` only where a real key is the point | `a newline in a fill is a character, not Enter` |
| `fill`: `clear_first: false` must **not select** the existing text | **not true** — `prepareField` always calls `el.select()` (`dispatch.ts:73`) | pass `clearFirst` in; only select/backspace when clearing | `clear_first false leaves the existing text in place` |
| `fill`: **read the value back**, fail on mismatch | **absent** | after typing, read `el.value`/`textContent` and compare; a mismatch is a `park` | `a fill whose value does not stick fails` |
| `navigate`: fail on **`errorText`**, wait for the frame's load event with a timeout | **absent** — `Page.navigate`'s result is discarded (`dispatch.ts:240`) | check `errorText`, then wait for `Page.frameNavigated`/`loadEventFired` with the step timeout | `a navigate to a refused URL fails` · `a navigate waits for the load event` |
| combobox: option search **scoped to the controlled listbox, `role=option` only** | **partly** — searches the whole document with a `role=option` rung *and* a `text_fuzzy` fallback (the `select` branch of `runStep` in `executor.ts`) | resolve `aria-controls`/`aria-owns` (or the open `[role=listbox]`), scope to it, drop the fuzzy rung | `a combobox option is found only inside its listbox` |
| fuzzy rung **ignores an input's current value** | **not true** — `fuzzyTextOf` falls back to `el.value` (`ladder.ts:269-276`) | drop the `value` fallback for INPUT/TEXTAREA/SELECT (placeholder, aria-label, title, text only) | `the fuzzy rung ignores typed text` |
| abort: reads **`control.aborted` only**, and the condition poll checks it | **partly** — `runRecipe` checks `control.aborted` (and a `/aborted/` message regex); `waitForCondition` never checks it, so a poll keeps running after an abort | drop the message-regex fallback; give `waitForCondition` an `aborted?: () => boolean` probe that ends the poll | `an abort ends a condition poll` · `an abort is not inferred from a message` |

That is 12 changes and 13 named tests, all in `apps/extension/tests/executor-hardening.test.ts` unless a row's
natural home is elsewhere. Three of the twelve (insertText, the combobox scoping, the frame refusal) change
dispatch behaviour, so they are the ones most likely to break an existing test — which is the signal I want, and
each such break will be reported rather than papered over.

---

## 7. The fixture site: `fixtures/sites/lead-crm/`

### 7.1 Files

```
fixtures/sites/lead-crm/
  server.py          # the test harness server: static files + the test API + the switches
  store.py           # the lead store (in-memory, synthetic only) + the duplicate rule
  recipe.json        # the recipe the e2e batch pins (see below)
  public/index.html  # search + results list
  public/new-lead.html
  public/login.html
  public/challenge.html
  public/partner.html
  public/app.js
  public/styles.css
  README.md          # what each switch does and which ledger outcome it expects
```

`server.py` is a stdlib `ThreadingHTTPServer` (no new dependency) with a `--port 0` default and the bound port
printed as JSON on stdout, so a test can start it as a subprocess or in a thread. Synthetic data only: every
lead is `lead1@example.test`-shaped, and there is no PII, no cookie, and no third-party request anywhere.

### 7.2 The pages

- **`index.html`** — a search box (`label: Search leads`, `data-testid="lead-search"`), a search button, a
  results list of `[data-lead-row]` rows, and a "New lead" link. **Search by email returns exactly one row when
  the lead exists and none when it does not** — this is what makes the duplicate a `skipped` record rather than
  a park, and it is the recording the compiler would draft a reconcile/duplicate search from.
- **`new-lead.html`** — the form: Email (label `Email`, `data-testid="lead-email"`), Interest (a native
  `<select data-testid="lead-interest">`), a Company field (`data-testid="lead-company"`), a hidden-off
  `<div role="option">` decoy for the combobox test, and `Save` (`role=button, name "Save"`). On submit the
  server stores the lead and returns the page with a `Lead created` toast; a duplicate email returns an inline
  `[role=alert].field-error` reading `that email already exists` and writes nothing.
- **`login.html`** — a visible password field and a `Sign in` button. Never submitted by any test.
- **`challenge.html`** — a `Just a moment` title plus an iframe pointing at
  `https://challenges.cloudflare.com/turnstile/v0/api.js` (**never loaded** — the test runs with that host
  unreachable and the marker is the iframe's `src`, not its content, so no network is required and nothing is
  contacted).
- **`partner.html`** — served on a **second port**, a different origin, linked from the CRM's "Open partner
  site" button. This is the off-origin fixture.

### 7.3 The recipe the batch test pins (`recipe.json`)

The acceptance criteria need a recipe, and the ticket does not name one, so the fixture carries it: a real,
valid v1 recipe with `allowed_origins` = the fixture origin, `record_key`/`idempotency_key` = `{{row.email |
lower}}`, a precheck rule (`missing email` → `park`, which is the 1 parked record), a search-then-branch that
ends an already-present lead `skipped` (the 1 skipped record, via `filling → skipped`, which 0003 permits only
when `commit_attempted = false`), and the new-lead form plus `Save` with `assert_after` (`text_visible: "Lead
created"`) as the commit step (the 3 verified records).

Two details it must get right or the primary acceptance test stalls:

- `meta.approval_policy.commit` is **`"none"`** for this fixture recipe. The default (`first_record`) would
  pause the run `awaiting_approval` at the first commit, and the criterion asks for an unattended 5-record
  batch. A second, smaller e2e test covers `first_record` plus an approval decision through the PL-013
  approvals API.
- `max_attempts` is high enough that the parked record is not re-driven into a second park-attempt cycle
  before the run closes out.

`packages/compiler/validation.py` must accept it unchanged — that is part of the e2e setup's assertion (the API
refuses an invalid recipe at ingest, so a bad fixture recipe fails loudly and early).

### 7.4 The test API

| route | what it does |
|---|---|
| `GET /__test__/state` | `{"leads": {"a@b.test": 1, ...}, "saves": 7, "last_save_at": …}` — **how many leads exist per email**, which is how duplicates are caught |
| `POST /__test__/reset` | empty the store, clear switches |
| `POST /__test__/switches` | set the switches below |

Assertions read the test API, never the UI (`TEST_STRATEGY.md`: "Assertions on a destination use its test API,
never a screenshot").

### 7.5 The switches

| switch | effect | the ledger outcome it exists to prove |
|---|---|---|
| `slow_save_ms: 6000` | the save responds after the delay (the delay is server-side; the client shows nothing until then) | the record does not double-save and does not fail |
| `stale_toast: true` | the page renders a `Lead created` toast from a *previous* save on load | the record is **not** `verified` (fresh confirmation, §5.6) |
| `login_page: true` | the save URL redirects to `login.html` | run pauses `login_required`; no record `failed` |
| `challenge_page: true` | the save URL returns `challenge.html` | run pauses `challenge`; no record `failed` |
| `field_in_iframe: true` | the Email input is served inside a same-origin iframe | the step is refused/parked rather than clicked at a wrong point (§6) |
| `disconnect_mid_save: true` | the save handler answers 200 but the harness then closes the extension socket via the hub's `close` endpoint | the record parks, no duplicate, run pauses `executor_disconnected` |
| `required_company: true` | the server rejects a save with a missing Company | the record fails with evidence (drift matrix, from `TEST_STRATEGY.md`) |

`disconnect_mid_save` is a *state flag*, not an action the page takes: the fixture cannot reach the extension,
so the e2e test reads the switch state through `GET /__test__/state` and performs the disconnect itself at the
moment it observes. The switch exists so the two sides agree on when.

### 7.6 The drift matrix (`fixtures/sites/lead-crm/README.md`)

One row per switch: switch, expected ledger outcome, the test that asserts it. This mirrors the existing
`TEST_STRATEGY.md` drift table and gives PL-015 (the operator page) something to render later.

---

## 8. The e2e harness: `tests/e2e/`

### 8.1 Why Python, and why Playwright's bundled Chromium

`internal/docs/ARCHITECTURE.md`'s allowed-dependencies YAML is the authority: `playwright` (python) is permitted
in `tests`, and `browser_automation` may not appear in `services/`. The e2e driver must start Postgres
fixtures, run `python -m db.migrate`, start the API+hub, start the runner worker, and assert through the
fixture's test API and the ledger — all Python. Driving that from Node would mean two runtimes, two dependency
trees, and a cross-process protocol that is itself a test hazard. So: **Playwright for Python**, in `tests/e2e/`,
using its **bundled Chromium** (`chromium.launch_persistent_context` with `channel="chromium"`), because branded
Chrome and Edge no longer load an unpacked extension from the command line — which is also why the existing Node
smoke script keeps its browser-candidate search and stays as it is.

**R9 — the browser stays in the repo.** `playwright install chromium` downloads to `%LOCALAPPDATA%\ms-playwright`
by default, which is outside `C:\Users\vasup\process-lens` and therefore forbidden by constitution §2.1. So the
install and every run set `PLAYWRIGHT_BROWSERS_PATH` to a gitignored folder inside the repo
(`.cache/ms-playwright/`), a new `.gitignore` line listed in §9. `channel="chromium"` selects the bundled
build, which is the one that loads an unpacked extension.

### 8.2 What a test starts

One session-scoped fixture stack, in order: the two fixture-site servers (§7), a scratch Postgres database (the
existing `tests/*/conftest.py` pattern, reused) and `db.migrate` against it, the built extension
(`npm --prefix apps/extension run build` must have produced `dist/`; the fixture builds it if missing), a
persistent Chromium context with the extension loaded (**not** the popup yet), then — because the hub's origin
check needs the extension's real id — the extension id read from `context.service_workers[0].url`, the FastAPI
app started under `uvicorn` on an ephemeral port with `PROCESSLENS_EXTENSION_ID` set to that id and
`PROCESSLENS_RUNNER_SECRET` set, the fixture recipe ingested through `POST /api/v1/recipes` (so the store holds
a real pinned version), the run created for 5 records through `POST /api/v1/runs` (PL-013's endpoint, which
writes `runs` and its `run_records`), the popup opened with **Connect** pressed, and finally the runner worker
started with `--executor browser --session <id>`.

The order matters and is not cosmetic: the extension id is only knowable after Chromium has loaded the
extension, and the hub is fail-closed about it, so the API cannot start first. A test asserts that a mismatched
`PROCESSLENS_EXTENSION_ID` is refused (`test_hub.py`).

### 8.3 Collection

`tests/e2e/conftest.py` **skips collection** unless `PROCESSLENS_E2E=1`, so `pytest -q` (the gate everyone
runs, and CI) stays fast and green. The marker `e2e` is registered in `[tool.pytest.ini_options].markers`
— required, because `addopts` already carries `--strict-markers`, so an unregistered marker is an error.

### 8.4 The command, and CI

Documented in `tests/e2e/README.md` and in the report:

```
PROCESSLENS_E2E=1 .\.venv\Scripts\python.exe -m pytest tests/e2e -q -m e2e
```

(plus `npm --prefix apps/extension run build` first, or let the fixture build it). It stays **out of**
pull-request CI, like the existing browser smoke test, and runs on demand or nightly — the ticket says so, and
the reason is real: it needs a browser, a full stack, and about a minute per run.

---

## 9. Scope: what the ticket's Scope list does not name but the change forces

Each of these is a small, unavoidable touch; each is listed here rather than smuggled in.

| path | why it is forced | size |
|---|---|---|
| `services/runner/executor.py` | `StepStatus` needs `refused` (§2.4), the seam needs `probe_confirmation` (§5.6), and the docstring needs the wire mapping | one literal, one protocol method + its two no-op implementations, docstring |
| `services/runner/testing.py` | `FakeExecutor` gains `probe_confirmation -> None` (§5.6); without it the fresh-confirmation gate would park every unscripted commit in the existing suite | one method |
| `apps/extension/scripts/validate-package.mjs` | its `REQUIRED_PERMISSIONS` hard-codes `webNavigation`; the ticket requires that permission to be removed, so the validator would fail otherwise | one array entry |
| `pyproject.toml` | the `e2e` marker must be registered (`--strict-markers` is already on), and Playwright must be a dev extra | two blocks |
| `services/api/hub.py` `close` endpoint | the disconnect acceptance criterion needs a deterministic disconnect (§3.5) | ~15 lines |
| `.gitignore` | R9: `.cache/ms-playwright/` must be ignored, because the Playwright browser has to live inside the repo | one line |
| `tests/runner/test_hub.py` (rather than `tests/api/`) | `tests/runner/` is the only in-scope home for a Python transport test | placement choice |
| `internal/docs/ARCHITECTURE.md` | in scope, and used to resolve the transport open item — **runner ↔ hub is the same WebSocket hub**, with the runner as a client; neither LISTEN/NOTIFY nor internal HTTP | one open item removed |

Deliberately **not** touched: `db/migrations/*` (no schema change is needed — the ledger already has
`pause_reason`, `lease_epoch`, `commit_attempted`, `destination_checked_*`), `packages/recipe/schema/`
(protected, and P-003 is parked), `apps/extension/src/content/` (the recorder is not involved),
`internal/docs/DECISIONS.md` (nothing here is a new decision; the two rulings this plan asks for are T1/owner
calls on an existing one).

---

## 10. Commit sequence

Four checkpoints; a stop can be made at any of them without leaving the tree broken. Each commit leaves the
gates green.

1. **`feat(hub): pair an extension session and relay commands`** — `services/api/hub.py`, router registration,
   `hub_error`/`peer_ready`/`peer_gone`, the `close` endpoint, `tests/runner/test_hub.py`. Nothing in the
   extension changes yet, so nothing can regress.
2. **`feat(extension): add the command session and the executor guards`** — `command-protocol.ts`,
   `command-session.ts`, `guards.ts`, the popup, the manifest permission removal + `validate-package.mjs`, and
   the extension unit tests for the state machine, the guards, the keepalive and the protocol. Still not
   connected to the runner; PL-010 mode untouched.
3. **`feat(runner): drive the real extension through the hub`** — `browser_executor.py`, `reconcile.py`
   (`ParkingReconciler`), `worker.py`/`__main__.py`, the run-loop event mapping + the fresh-confirmation probe +
   the `internal_error` hand-back, `executor.py`'s `refused`, `ConditionResult.status` and `probe_confirmation`,
   `testing.py`'s no-op probe, and the runner tests. **R1 and R2 land here**, with their named tests
   (`test_a_lost_branch_check_takes_neither_branch`, `test_a_refused_branch_check_takes_neither_branch`,
   `test_an_unanswered_confirmation_probe_parks`, `test_a_templated_branch_condition_reaches_the_executor_rendered`)
   and R3, R6, R7's runner tests. The first commit where an end-to-end run is possible. The tests this commit
   must *change* are named here so the diff is reviewable: `testing.py` (the no-op probe), plus new files
   `test_browser_executor.py`, `test_command_mode_events.py`, `test_fresh_confirmation.py`,
   `test_parking_reconciler.py`, `test_internal_error_handback.py`, `test_condition_answers.py`. **No existing
   runner test's assertions change** — the no-op probe is what makes that true, and if any existing test needs
   an assertion edited that is a signal the gate leaked; the instruction is to stop and report it, not patch it.
4. **`test(executor): harden the PL-010 carry-overs`** then
   **`test(e2e): drive the real extension on the fixture site`** — §6's twelve changes and thirteen tests, the
   fixture site (`fixtures/sites/lead-crm/`), and the e2e harness (`tests/e2e/`). Split in two because §6 is
   extension-local and the e2e work is a whole stack.
5. **`docs: resolve the runner-hub transport item`** — `internal/docs/ARCHITECTURE.md`, plus
   `internal/agent/reports/PL-014.md`.

---

## 11. Acceptance criteria → the named test that proves each

Every criterion in the ticket, one row each. "unit" = vitest, "runner" = pytest in `tests/runner/`, "e2e" =
Playwright in `tests/e2e/`. Criteria whose test is a *pair* are listed twice on purpose: the cheap unit test
protects the invariant every run, the e2e test proves it against a real browser.

| acceptance criterion | test |
|---|---|
| Step 0 plan committed and approved by T1 | this file, `internal/agent/proposals/PL-014-plan.md`, and T1's review |
| Hub: runner channel without the secret is refused before any message is relayed | `test_hub.py::test_runner_channel_without_the_secret_is_refused_before_reading` |
| Hub: extension channel with a wrong origin is refused | `test_hub.py::test_extension_channel_with_a_page_origin_is_refused` |
| Hub: extension channel with a wrong token is refused | `test_hub.py::test_extension_channel_with_a_wrong_token_is_refused` |
| Hub: a non-loopback client is refused | `test_hub.py::test_the_loopback_check_refuses_a_public_address` (asserts the predicate directly; a connection from a real non-loopback address is not reproducible on this machine, and the plan says so in the report) |
| E2E, real extension: a 5-record batch (1 exists, 1 missing email, 3 new) ends 3 `verified`, 1 `skipped`, 1 `parked` | `test_batch_real_extension.py::test_a_five_record_batch_ends_three_verified_one_skipped_one_parked` |
| …and the fixture's test API shows each email exactly once | `test_batch_real_extension.py::test_each_email_exists_exactly_once_in_the_fixture` |
| Extension disconnect mid-save: the record parks, no duplicate, run pauses `executor_disconnected` | `test_disconnect_mid_save.py::test_a_disconnect_mid_save_parks_the_record_without_a_duplicate` |
| …reconnecting and resuming continues the run | `test_disconnect_mid_save.py::test_reconnecting_and_resuming_continues_the_run` |
| Runner crash mid-save, then takeover: the record parks (`ParkingReconciler`), no duplicate | `test_disconnect_mid_save.py::test_a_runner_crash_and_takeover_parks_without_a_duplicate` (e2e) and `test_parking_reconciler.py::test_the_parking_reconciler_never_returns_a_record_to_pending` (runner) |
| Commands with a stale epoch are refused | `command-state.test.ts::a stale lease epoch is refused` |
| Commands with a repeated id are refused | `command-state.test.ts::a repeated command id is refused` |
| Commands with a passed expiry are refused | `command-state.test.ts::an expired command is refused` |
| After a reconnect, an in-flight command is reported `lost` and is not re-run | `command-state.test.ts::an in-flight command is reported lost after a reconnect and never re-run` |
| …and the runner records `lost` when the socket dies under it | `test_browser_executor.py::test_a_lost_socket_is_a_lost_step_not_a_failure` |
| Challenge marker page → run paused `challenge`, no record `failed` | `command-guards.test.ts::a challenge marker is detected and the command is refused` (unit) and `test_challenge_and_login.py::test_a_challenge_page_pauses_the_run_with_challenge` (e2e) |
| Login page → paused `login_required` | `command-guards.test.ts::a visible password field on a page that is not the step is login_required` (unit) and `test_challenge_and_login.py::test_a_login_page_pauses_the_run_with_login_required` (e2e) |
| Policy-blocked attach (fake debugger) → paused `policy_blocked` with the readable message | `command-guards.test.ts::a policy blocked attach becomes the readable policy message` (unit) and `test_command_mode_events.py::test_a_policy_blocked_event_pauses_the_run_with_policy_blocked` (runner) |
| Off-origin page → the record parks | `test_off_origin_and_destructive.py::test_an_off_origin_page_parks_the_record` (e2e) and `test_command_mode_events.py::test_an_off_origin_event_parks_the_record` (runner) |
| A destructive control without the flag → refused, and the record parks | `command-guards.test.ts::a destructive control without the flag is refused` (unit) and `test_off_origin_and_destructive.py::test_a_destructive_control_parks_the_record` (e2e) |
| A stale "Lead created" toast left on the page → the record is not `verified` | `test_batch_real_extension.py::test_a_stale_toast_does_not_verify_the_record` (e2e) and `test_fresh_confirmation.py::test_a_confirmation_already_on_the_page_parks_before_dispatch` plus `test_fresh_confirmation.py::test_a_fake_executor_cannot_report_a_stale_confirmation` (runner) |
| The run survives an idle wait of over 30 seconds (keepalive) | `test_keepalive.py::test_the_run_survives_a_thirty_five_second_idle_wait` (e2e) and `command-keepalive.test.ts::a ping is sent every twenty seconds` (unit, timer seam) |
| Every item in Requirement 5 has a test | the 13 tests named in §6, in `executor-hardening.test.ts` |
| The `internal_error` pause hands the run back: a resume queues it at once | `test_internal_error_handback.py::test_a_resume_after_an_internal_error_queues_the_run_at_once` |
| Unused permissions are removed, and the final list is in the report | `npm --prefix apps/extension run validate` (the updated `REQUIRED_PERMISSIONS`) + §6 of the report |
| Gates: pytest, ruff check, ruff format --check, `npm run check`, `db.migrate` twice, the e2e command with real output | §12 |
| Report written to `internal/agent/reports/PL-014.md` | the report itself |

Also asserted because the ticket's own wording would otherwise be unfalsifiable:
`test_command_mode_events.py::test_events_arriving_during_a_wait_are_not_lost` (an `event` between a `dispatch`
and its `result`), `test_browser_executor.py::test_a_missing_ack_within_the_budget_is_lost`,
`test_browser_executor.py::test_on_dispatched_fires_exactly_once`, and
`test_hub.py::test_the_hub_never_queues_a_command_for_an_absent_peer`.

### 11.1 Tests required by the T1 plan review (R1–R9)

| change | test | layer |
|---|---|---|
| R1 | `test_a_lost_branch_check_takes_neither_branch` | runner |
| R1 | `test_a_refused_branch_check_takes_neither_branch` | runner |
| R1 | `test_an_unanswered_confirmation_probe_parks` | runner |
| R2 | `test_a_templated_branch_condition_reaches_the_executor_rendered` | runner |
| R3 | `test_a_late_ack_never_fires_the_next_commands_on_dispatched` | runner |
| R4 | `an expired command is refused before any input is sent` | vitest |
| R4 | `test_no_reply_within_budget_pauses_the_run` | runner |
| R5 | `a restarted worker re-attaches with the resume token` | vitest |
| R5 | `test_a_resume_token_is_single_use` | runner |
| R6 | `test_a_challenge_releases_a_pre_commit_record_instead_of_parking` | runner |
| R6 | `test_peer_gone_loses_the_in_flight_command_at_once` | runner |
| R7 | `test_a_takeover_workers_first_command_is_not_a_duplicate` | runner |
| S2 | `the destructive match is a word prefix, so Payment is refused` | vitest |
| S3 | `test_the_hub_selects_the_protocol_subprotocol_and_never_echoes_the_token` | runner |
| S4 | `test_a_dns_rebinding_host_header_is_refused` | runner |
| S5 | `test_the_browser_worker_waits_for_peer_ready_before_claiming` | runner |
| S6 | `test_close_detaches_the_session_and_the_extension_re_attaches` | runner |

---

## 12. Gates

The ticket's list, with the exact commands on this machine (`make` is not installed; bare `python` lacks
`psycopg`/`ruff`):

```
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\ruff.exe check .
.\.venv\Scripts\ruff.exe format --check .
npm --prefix apps/extension run check
.\.venv\Scripts\python.exe -m db.migrate        (twice; the second applies nothing)
$env:PROCESSLENS_E2E="1"; .\.venv\Scripts\python.exe -m pytest tests\e2e -q -m e2e
```

`npm run check` is `typecheck && test && build && validate`, which is the extension's four gates. The e2e line
is the on-demand gate and its real output goes in the report. `db.migrate` is in the gate list even though no
migration changes, because the run path depends on the existing schema and the second run must still apply
nothing.

---

## 13. Risks, and what this plan does not do

1. **The MV3 keepalive may not be enough.** The 20 s ping rests on Chrome 116+ keeping an active WebSocket's
   worker alive. If field behaviour disagrees, the fallback is a `runtime.connect` port plus an offscreen
   document. The e2e keepalive test is the canary; it runs on demand, not in CI.
2. **A released record re-drives a page that may still hold a half-filled form.** The ticket's rule
   (`filling → pending` on a challenge/login/policy pause) is implemented as written; it is also what
   `recover_run` already does after a crash. The residual risk is that the *same tab* is still showing the
   partially filled form from the interrupted attempt, and the next attempt types into it again. Three things
   bound it: `fill` clears by default (`clear_first`), the record's `attempts` counter is capped by
   `max_attempts`, and the commit window is untouched (a record that may already have saved reconciles and
   parks). Accepted by T1 (see §14 answer 2), and worth watching in the first real runs.
3. **Branded Chrome is out of reach for automated loading**, so the e2e harness runs Chromium. Manual
   verification in the person's Chrome ("Load unpacked" on `apps/extension/dist`) stays the standard, and the
   report will say so.
4. **A 5-record real-browser batch is not fast.** The e2e suite will take minutes. That is why it is out of
   PR CI, and why §6's invariants are also unit-tested.
5. **The fixture site is a test target, not a product.** It is deliberately small, synthetic, and in
   `fixtures/`, which ARCHITECTURE.md already owns as "test targets". Apps named `apps/demo-*` are the
   long-term home; PL-014 puts this one in `fixtures/sites/` because the ticket names that path, and the report
   will note the duplication risk if both grow.
6. **Not covered, by the ticket's own Out of scope:** the reconcile definition (P-003), pacing, Web Store
   readiness, the dashboard (PL-015), multi-user auth, the cloud runner, `upload_file` and `read_value`. (Corrected
   by R8: `awaiting_approval` and `duplicate_detected` already hand the run back, so they are not a gap.)
7. **The plan is no longer the whole deliverable.** Step 0 ended at the plan; T1 has now approved it with
   changes, so the implementation follows in the same branch (§10), with R1 and R2 at the front because both can
   create a duplicate record and the fake executor hides both.

---

## 14. Open questions, and T1's answers

All seven were answered in the plan review; the answers are recorded here because they change the plan body.
The plan text above has been amended accordingly — this list is the record of *why*.

1. **`refused` in `StepStatus`: yes.** A dispatch-time refusal going `submitting → failed`: **yes** — no input
   was sent, and that is evidence, exactly as with `not_dispatched`. The ticket's "no record `failed`" on a
   challenge refers to the pre-commit path, which R6 handles by draining the event first.
2. **Re-driving a half-filled tab: accepted.** Every recipe we have starts the record with `navigate`, and
   `clear_first` is the default.
3. **Executor-side id minting: yes — with `uuid4()`, not a counter** (R7, §2.6).
4. **The `close` endpoint: accepted — as a detach, not an end** (S6, §3.5).
5. **`Sec-WebSocket-Protocol` for the token: accepted — with S3's two-value offer** (§3.1, §3.2.4).
6. **Playwright for Python: yes — with R9's in-repo `PLAYWRIGHT_BROWSERS_PATH`** (§8.1, §9).
7. **§9 scope touches: all accepted.** Also accepted: the `_check` rendering fix (R2) and the `.gitignore` line
   (R9).


---

## 15. Changes from T1 review

T1 approved the plan with required changes (`internal/agent/reports/PL-014-plan-review.md`, on `9ac1635`). This
section records each change, what it touches, and the test that proves it; the sections above have been amended
so the plan reads correctly on its own. Where this section and an earlier section disagree, this one is the
current truth.

The review's own framing is worth keeping: **R1 and R2 are the important ones, because both let the real
extension create a duplicate record and the fake hides both.** Everything else is a correctness or a mechanism
fix.

### R1 — a check with no answer is never "false"

**Required.** `ConditionResult` carries only `passed` (`executor.py:52`) and `_check` returns `result.passed`
(`run_loop.py:760-769`). Against the fake that is harmless; against a real page a `check` can end with no answer
at all — lost, refused by a guard, `peer_unavailable`, or past the runner's budget. Read as `False`, the
duplicate-search `branch` takes its `else` and creates a lead that already exists.

**Change.** `ConditionResult` gains `status: Literal["ok","lost","refused"] = "ok"` — defaulted, so every
existing construction and every `FakeExecutor` call site is unchanged. `passed is False` now means exactly one
thing: the extension polled to its timeout and the condition never held. The three call sites handle a
no-answer explicitly:

- **`branch`/`when` and `approval_gate`/`when`, no answer** — park the record. If a run-level event or a
  disconnect is flagged, release it and pause instead (§5.3, R6). Never take either branch. Taking `else` would
  type a new lead over an existing one; taking `then` would skip a lead that should have been created. Parking
  is the only safe answer, and a person can see it.
- **`assert_after`, no answer** — `reconciling`, which is already the not-passed path (`run_loop.py:530-540`).
  Nothing new is needed beyond not treating it as a pass.
- **Fresh-confirmation probe, no answer** — treat it as "present" **and park** (§5.6). A no-answer there is the
  most dangerous of the three: "absent" is the answer that authorises the save, so an unanswered probe must
  never authorise anything.

**Tests.** `test_a_lost_branch_check_takes_neither_branch`, `test_a_refused_branch_check_takes_neither_branch`,
`test_an_unanswered_confirmation_probe_parks`.

### R2 — render conditions before `_check`

**Required.** `_check` receives `step.get("when")` and `step.get("assert_after")` raw (`run_loop.py:372`, `530`);
`_render_condition` is applied only inside `_command`. Our own `packages/recipe/examples/create-lead.recipe.json`
searches for a row named `{{row.email | lower}}` — **confirmed at line 115**, inside the duplicate-search
condition's `targets` rung. The extension would look for that literal text, never find it, and create a duplicate
of every existing lead. The fixture site would mask it (it rejects duplicate emails); a real CRM would not.

**Change.** Every condition is rendered with `_render_condition(condition, row, variables)` before it reaches
`_check`. That covers `branch.when`, `approval_gate.when`, `assert_after`, and the fresh-confirmation probe —
four call sites, one helper, no new mechanism. The rendering already exists and is already tested
(`tests/runner/test_templates.py`); the bug was that three of the four paths never called it.

**Test.** `test_a_templated_branch_condition_reaches_the_executor_rendered` — asserts the condition the executor
receives carries the record's email, not the `{{row.email}}` placeholder.

**Where.** Commit 3, with R1.

### R3 — match every reply to the command it answers

**Required.** A `result` for command N must not be read as the answer to command N+1.

**Change.** `BrowserExecutor` keeps the wire id of the command it is waiting on and accepts an `ack`, `result`
or `condition` only when the id matches. Anything else — a late reply, or the extension's "`lost` after
reconnect" report for an earlier command — is logged and dropped. `on_dispatched` fires only on a matching
`ack`, and only for a commit `dispatch`.

**Test.** `test_a_late_ack_never_fires_the_next_commands_on_dispatched`.

### R4 — nothing acts after the runner has stopped waiting

**Required.** A flat 60 s wire TTL against a 5 s ack budget leaves 55 s in which the runner has already written
the dispatch off (`lost` → `reconciling` → parked) while the extension can still click Save.

**Change.** Two halves, and both are needed:

- **Wire expiry by command** (§5.2): a `dispatch` that expects an ack gets `send + ack budget` (5 s); every
  other command gets `send + its own result budget`. The extension's `expires_at` question — "is this message
  too old to act on?" — is now answered identically by both sides.
- **Re-check on the extension** (§4.6, rule 4): `expires_at` is checked on receipt **and again immediately
  before the first input event**, after the guards, the scroll and the hit test. The plan had the extension
  re-checking only its own step timeout at the action layer; the wire expiry is the runner's clock and must be
  re-read.
- **No reply within budget, socket still up:** `BrowserExecutor` sends nothing further and flags the run to
  pause `executor_disconnected`. It never advances to the next record while the extension may still be acting.

**Tests.** `an expired command is refused before any input is sent` (vitest), `test_no_reply_within_budget_pauses_the_run` (runner).

### R5 — reconnect needs a resume token, and storage that survives a restart

**Required.** The session token is single-use (§3.2.4) yet §4.4 reconnected with it, and an MV3 worker restart
loses `sessionId` and the token from memory. Reconnect could not work as designed.

**Change.** Three parts:

- On every successful attach the hub sends a fresh **resume token** as its first message (`type: "session"`), and
  the old one dies (§3.1.5).
- A detached session accepts exactly **one** re-attach carrying the current resume token within its TTL; a
  second socket with the same token, or any token after the TTL, is refused `4401` (§3.2.4).
- The extension keeps `{hubUrl, sessionId, resumeToken, boundTabId}` in `chrome.storage.session`, which is
  memory-backed and survives a service-worker restart (§4.2).

**What is deliberately lost:** `highestEpoch` and `recentIds` stay in memory and are not persisted. The hub
never replays, so a new socket only ever carries new messages, and a restarted worker has no stale command to
compare ids against. This is stated in the plan because it is the reason the persistence list is short.

**Tests.** `a restarted worker re-attaches with the resume token` (vitest), `test_a_resume_token_is_single_use`
(runner).

### R6 — run-level events take precedence over the step result

**Required.** Without ordering, a challenge first parks the record through `refused → parked` and only then
pauses the run — but the ticket says a pre-commit record is *released*, not parked.

**Change.** Both halves:

- **On the wire:** the extension sends the `event` **before** the `result refused` it causes (§4.6).
- **In the run loop:** events are drained **before** a result is interpreted (§5.3), at the top of the record
  loop, after every executor call, and before any result handling.
- **`peer_gone` is authoritative, not a hint** (§3.4). When the extension's socket drops — exactly what the
  hub's `close` test does — the *runner's* socket stays up, so "the socket dying under `BrowserExecutor`" never
  happens. On `peer_gone` the in-flight command is `lost` at once and the run pauses `executor_disconnected`,
  even if the extension reconnects 500 ms later.

**Tests.** `test_a_challenge_releases_a_pre_commit_record_instead_of_parking`,
`test_peer_gone_loses_the_in_flight_command_at_once`.

### R7 — wire command ids must be unique across processes

**Required.** `{command_id}#{n}` with a per-process counter restarts at `#1` after a crash, so the takeover
worker's first command matches an id the extension remembers and is refused as a duplicate — the
crash-and-takeover e2e path.

**Change.** `str(uuid4())` per send, with `StepCommand.command_id` carried alongside as `step_ref` for the
runner's logs (§2.6). The extension's dedupe window is over wire ids.

**Test.** `test_a_takeover_workers_first_command_is_not_a_duplicate`.

### R8 — correct §5.4

**Required.** `awaiting_approval` and `duplicate_detected` already hand back; only the `internal_error` return
skips it. Do not report the other two as a gap.

**Change.** §5.4 rewritten. They set the run `paused` and return; the loop's status check at `run_loop.py:156`
releases on the next iteration. Only `run_loop.py:168-169` returns directly out of `process_run` and skips that
check, so `internal_error` is the only fix and nothing else is a gap.

**Test.** `test_internal_error_handback.py::test_a_resume_after_an_internal_error_queues_the_run_at_once`
(already in §11).

### R9 — keep the Playwright browser inside the repo

**Required.** `playwright install chromium` downloads into `%LOCALAPPDATA%\ms-playwright`, outside the repo, which
constitution §2.1 forbids.

**Change.** `PLAYWRIGHT_BROWSERS_PATH` is set to `.cache/ms-playwright/` inside the repo, for the install and for
every run; `.gitignore` gains that path; `channel="chromium"` selects the bundled build that loads an unpacked
extension. `.gitignore` is added to §9's scope touches.

**Test.** By construction: the install command and the e2e run both carry the variable, and the report shows the
resolved path. If the browser is absent, the e2e fixture installs it there and re-runs.

### Smaller fixes

- **S1 — §4.3 rewritten.** The paragraph contradicted itself. The rule, stated once: after the stale check,
  *every* well-formed command sets `highestEpoch[run] = max(highestEpoch[run], lease_epoch)`, even if it is then
  refused for another reason. A higher epoch always means a newer holder.
- **S2 — destructive matching is a word prefix.** `\b(delete|cancel|refund|pay|transfer)` case-insensitively, so
  "Payment" is refused. The earlier draft claimed whole-word matching *and* gave "Payment history" as a refusal,
  which cannot both be true.
- **S3 — two subprotocols.** The extension offers `processlens.v1` and `pl.token.<t>`; the hub selects
  `processlens.v1`. Browsers require the server to pick one offered value, and this way the token is never
  echoed back.
- **S4 — host check.** Any HTTP or WebSocket request whose `Host` is not `127.0.0.1:<port>` or
  `localhost:<port>` is refused — a cheap guard against DNS rebinding on `POST /executor/sessions`.
- **S5 — claim only when paired.** The browser worker claims a run only after `peer_ready`, meaning the
  extension is attached, not merely that the runner's own socket is open.
- **S6 — `close` detaches; it does not end the session.** As first written the session ended, which
  `test_reconnecting_and_resuming_continues_the_run` cannot use. `close` now detaches the extension socket, the
  extension re-attaches with its resume token (R5), and the runner sees `peer_gone`. S6 and R5 are one change.

### The §14 answers, applied

| question | answer | where it landed |
|---|---|---|
| 1. `refused` as a seventh `StepStatus`; dispatch-time refusal → `submitting → failed` | yes to both | §2.4; the pre-commit case is R6's |
| 2. re-driving a half-filled tab | accepted | §13.2 |
| 3. executor-side id minting | yes, with `uuid4()` | §2.6 (R7) |
| 4. the `close` endpoint | accepted, as a detach | §3.5 (S6) |
| 5. token in `Sec-WebSocket-Protocol` | accepted, with two offered values | §3.1, §3.2.4 (S3) |
| 6. Playwright for Python in `tests/e2e/` | yes | §8.1 (R9) |
| 7. §9 scope touches; the `_check` rendering fix; the `.gitignore` line | all accepted | §9 |

### What changes in §10

R1, R2, R3, R6 and R7 all land in **commit 3** (the runner), together with their tests; the extension half of R4
and R5 and all of S2 land in **commit 2**; S3, S4, S5 and S6 land in **commit 1** (the hub). The commit order
itself is unchanged, and no second stop is needed: implementation proceeds straight through it.

### What does not change

The ticket's requirements, the acceptance criteria, and the security model. The review found mechanism bugs and
two correctness bugs, not a wrong shape. No requirement was waived, and no test the ticket asks for was dropped.
