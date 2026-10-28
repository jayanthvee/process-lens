// The command session: the extension's side of the PL-014 executor protocol.
//
// One socket to the hub, one command at a time, and a fail-closed answer for
// every command. The session owns no workflow state: it refuses a command it
// cannot prove is current (wrong protocol, a stale lease, a duplicate, an
// expiry, a missing tab, a second command while one is running) and reports what
// the step did. The runner decides what happens next.
//
// Every side effect is injected, so the whole state machine can be driven in
// vitest with a plain object instead of a socket and no real Chrome. The pieces
// that must survive a worker restart ({hubUrl, sessionId, resumeToken,
// boundTabId}) live in injected storage; the pieces that must NOT survive
// (highestEpoch, recentIds) live only in memory, because the hub never replays.
import {
  KEEPALIVE_MS,
  PROTOCOL,
  PROTOCOL_SUBPROTOCOL,
  TOKEN_SUBPROTOCOL_PREFIX,
  parseIncoming,
  type CommandMessage,
  type CommandStatus,
  type EventKind,
  type OutgoingMessage,
} from "../shared/command-protocol";
import { accessHeaders } from "../shared/access";
import type { BlockedRequest } from "../executor/abandon-guard";
import type { StepReport } from "../executor/types";
import { ExecutorError } from "../executor/types";
import { POLICY_BLOCKED_MESSAGE, recheckExpiry } from "../executor/guards";

/** The socket surface the session needs; a plain object satisfies it. */
export interface WebSocketLike {
  send(data: string): void;
  close(): void;
  addEventListener(type: string, listener: (event: unknown) => void): void;
}

/** Where the session is, for the popup's status line. */
export type SessionState =
  | "idle"
  | "pairing"
  | "connecting"
  | "ready"
  | "executing"
  | "disconnected";

/** The parts of a session that survive a worker restart. */
export interface StoredSession {
  hubUrl: string;
  /** PL-086: the team access code a hosted hub asks for; absent or empty on a laptop. */
  accessCode?: string;
  sessionId: string;
  resumeToken: string;
  boundTabId: number;
}

/**
 * The answer to one `check`.
 *
 * `ok: false` means the check could not be evaluated at all (no bound tab, a CDP failure, a
 * condition the executor does not understand, a withdrawn command, or a poll that never evaluated
 * cleanly) — the session reports that as a *refusal*, never as a real "no" (B1, C2). `ok: true`
 * carries the answer: `passed: true` when the condition held, `passed: false` when the page was
 * evaluated and the condition did not hold.
 */
export type CheckAnswer =
  | {
      ok: true;
      passed: boolean;
      /**
       * C3: the run-level event the page is showing — a challenge or a login wall the recipe does
       * not describe — when the condition was evaluated cleanly and did not hold on a page that
       * needs a person. Absent on a clean page and on a `passed: true` answer, so only a wall ever
       * produces an event. The session sends it BEFORE the condition reply (R6); it never changes
       * the answer.
       */
      event?: EventKind;
      /** The guard's readable reason, sent as the event's detail. */
      eventDetail?: string;
      /**
       * PL-117 item 2: whether the page still shows a one-time-code prompt. The runner uses it to
       * say the sign-in never got past the code page, or to name the page it never reached.
       * `undefined` means the page was not probed.
       */
      codePage?: boolean;
    }
  | { ok: false; reason: string };

/** Every side effect, injected. */
export interface SessionDeps {
  createSocket: (url: string, protocols: string[]) => WebSocketLike;
  storage: { get(): Promise<StoredSession | null>; set(v: StoredSession | null): Promise<void> };
  now: () => number;
  /** Returns a cancel for the timer. */
  setInterval: (fn: () => void, ms: number) => () => void;
  bindActiveTab: () => Promise<number | null>;
  /** PL-087: this install's browser id, sent with the session so its runs come to this Chrome. */
  browserId?: () => Promise<string>;
  /**
   * Wraps step-runner: guard, and either resolve only (`prepare`) or resolve and perform
   * (`dispatch`). `sentInput` fires after the trusted input event.
   */
  runStep: (
    step: unknown,
    opts: {
      sentInput: () => void;
      phase: "prepare" | "dispatch";
      allowedOrigins?: string[];
      /** Runs immediately before every trusted input; throws to refuse (F1, B3). */
      beforeInput: () => void;
    },
  ) => Promise<StepReport>;
  /**
   * Evaluate a `check` command's condition. A poll that completed answers with `ok: true`; a
   * check that could not be evaluated at all \u2014 no bound tab, a CDP failure, a condition the
   * executor does not understand, a withdrawn command, a poll whose last attempt threw a transient
   * navigation error (C2) \u2014 answers `ok: false`, which the session reports as a *refusal*,
   * never as a real "no" (B1).
   *
   * A clean `passed: false` may carry the `event` of a challenge or login wall the page is showing
   * (C3); the session sends that event before the condition reply.
   */
  checkCondition: (
    condition: unknown,
    timeoutMs: number,
    aborted: () => boolean,
  ) => Promise<CheckAnswer>;
  /**
   * Type a person's one-time code into the page and press its own continue (PL-068). The answer
   * says whether the page had a code field; it never carries the code. The code lives only for
   * this call.
   */
  /** PL-068: types a person's verification code; a session without it answers "not found". */
  enterCode?: (code: string) => Promise<CodeAnswer>;
  /**
   * PL-102: read the code from the person's own Gmail and type it. `since` is when the code page
   * appeared; `remember` stores the person's yes. Answers `not_allowed` without that yes, and
   * `none`, `ambiguous` or `no_gmail` when there is no single code to type. Never the code.
   */
  codeFromEmail?: (options: {
    since: number;
    remember: boolean;
    lookMs: number;
    stop: () => boolean;
  }) => Promise<CodeAnswer>;
  /**
   * Arm the abandon guard on the bound tab (PL-027): refuse every request that is not a read, so a
   * page that autosaves when it unloads cannot write a record the runner did not commit. The
   * executor releases it itself, on the next navigate's arrival.
   */
  armAbandonGuard: () => Promise<void>;
  /** Release the abandon guard, and report the requests it refused while it held. */
  disarmAbandonGuard: () => Promise<BlockedRequest[]>;
  /**
   * Confirm a stored bound tab still exists, and adopt it as the session's bound tab (B2).
   * Returns false when the tab is gone. Restore never binds the active tab.
   */
  restoreBoundTab: (tabId: number) => Promise<boolean>;
  /**
   * PL-117 item 5: one-shot timer seam for the quiet spell. Returns a cancel. A session without it
   * (older harnesses) never lets the debugger go on its own.
   */
  setTimeout?: (fn: () => void, ms: number) => () => void;
  /**
   * PL-117 item 5: hold the debugger again before a command runs. The real implementation is the
   * same `ensureDebugger` every step already uses; this makes "Resume attaches before the next
   * step" one observable call.
   */
  wakeDebugger?: () => void | Promise<void>;
  /**
   * PL-117 item 5: no command has run for the quiet spell (a pause, or the run ended), so let the
   * debugger go. This is not a person's Cancel: the caller removes its detach watcher first.
   */
  releaseDebugger?: () => void | Promise<void>;
  /**
   * PL-026: tell the API which version this extension is running, every time the session reaches
   * `ready`. The implementation reads the manifest itself, so the session carries no version of its
   * own; a report that fails changes nothing about the session, which is why this returns nothing.
   */
  reportVersion?: () => void;
  onState: (state: SessionState) => void;
}

/** The backoff schedule for a dropped socket: 500 ms → 1 s → 2 s → 4 s (cap). */
const RECONNECT_SCHEDULE_MS = [500, 1000, 2000, 4000];

/** PL-102: what typing a code did. `cleared` says the code page went away afterwards. */
export interface CodeAnswer {
  entered: boolean;
  reason: string;
  cleared?: boolean;
}

/** PL-102: how long to look for an emailed code when the command does not say. */
const DEFAULT_LOOK_MS = 60_000;
/** PL-102: with no code page seen by this session (a restart), look this far back. */
const CODE_PAGE_FALLBACK_MS = 3 * 60_000;

/**
 * PL-117 item 5: how long the session sits with no command before letting the debugger go.
 *
 * A run's steps arrive back to back; a pause or the end of a run leaves the session quiet. The
 * quiet spell is short enough that Chrome's "started debugging this browser" bar goes away while a
 * person reads a parked row, and long enough that ordinary step boundaries do not detach and
 * re-attach for every command. A hosted hub's round trip, or a slow page between two steps, can
 * pass 2 s; each release and re-attach also shows and hides Chrome's bar, which resizes the page
 * mid-row. 10 s keeps a working run attached.
 */
export const DEBUGGER_IDLE_RELEASE_MS = 10_000;

/** Trim trailing slashes so a hub URL composes cleanly. */
/** What the popup shows when a hosted ProcessLens refuses the team access code (PL-086). */
/** PL-104: Chrome would not open the work window, so nothing was connected. */
export const WORK_WINDOW_REFUSED =
  "Chrome didn't let ProcessLens open its own window. Close a few Chrome windows, then press Connect again.";

export const ACCESS_REFUSED =
  "ProcessLens needs the team access code. Open More and check Team access code.";

function trimBase(hubUrl: string): string {
  return hubUrl.replace(/\/+$/, "");
}

/** The WebSocket origin for an HTTP hub URL. */
function socketBase(hubUrl: string): string {
  return trimBase(hubUrl).replace(/^http:/i, "ws:").replace(/^https:/i, "wss:");
}

interface ChromeTabs {
  query(query: { active: boolean; currentWindow: boolean }): Promise<
    Array<{ id?: number; url?: string; title?: string }>
  >;
  get(tabId: number): Promise<unknown>;
}

function chromeTabs(): ChromeTabs | null {
  const api = (globalThis as { chrome?: { tabs?: ChromeTabs } }).chrome?.tabs;
  return api ?? null;
}

/** A session with a hub, driven entirely through its injected dependencies. */
export class CommandSession {
  private state: SessionState = "idle";
  /** PL-102: when this session last reported a code page, so older emails are not read. */
  private codePageAt: number | null = null;
  private socket: WebSocketLike | null = null;
  private stored: StoredSession | null = null;
  /** The first socket's token, from the hub's 201 response; the resume token replaces it. */
  private sessionToken = "";
  private cancelKeepalive: (() => void) | null = null;
  /** A one-shot reconnect timer, as a cancel plus a fired guard. */
  private cancelReconnect: (() => void) | null = null;
  private reconnectAttempt = 0;
  private stopped = true;
  /** Deliberately in memory only: lost on a restart, because the hub never replays. */
  private readonly highestEpoch = new Map<string, number>();
  private readonly recentIds = new Set<string>();
  private executing: string | null = null;
  private inFlight: CommandMessage | null = null;
  private unreported: CommandMessage[] = [];
  /** Aborts the running command's input when the socket drops (B3). */
  private currentAbort: AbortController | null = null;
  /**
   * The id of a command that was aborted on socket close and is still unwinding its input. A new
   * command is refused while it is set, so two commands never run concurrently on the same tab
   * (B3). Cleared in the aborted command's own `finally`, the moment its work actually settles.
   */
  private unwinding: string | null = null;
  /**
   * The number of the newest Connect or Disconnect (PL-093). A Connect that is still waiting on the
   * hub when a newer Connect or a Disconnect arrives is stale: whatever the hub answers it, it opens
   * nothing and changes nothing. Over the internet pairing takes about a second, long enough for
   * the popup's own auto-connect and a person's click to overlap.
   */
  private pairingAttempt = 0;
  /** True while a restore is in flight, so a second call cannot open a second socket (F6). */
  private restoring = false;
  /** Set when restore found the stored tab gone; announced once the socket is up (B2). */
  private announceTabClosed = false;
  /** The last peer_gone / hub_error the runner may want to act on. */
  private notice: string | null = null;
  /** PL-117: the pending quiet-spell timer, and whether a command has attached the debugger. */
  private cancelIdle: (() => void) | null = null;
  private debuggerHeld = false;
  /** A release still running; a command that arrives now waits for it before re-attaching. */
  private releasing: Promise<void> | null = null;

  constructor(private readonly deps: SessionDeps) {}

  /** The current state, for the popup and for tests. */
  get currentState(): SessionState {
    return this.state;
  }

  /** The last unsolicited hub notice (peer_gone / hub_error), if any. */
  get lastNotice(): string | null {
    return this.notice;
  }

  private setState(state: SessionState): void {
    const previous = this.state;
    this.state = state;
    if (state === "executing") this.cancelIdleRelease();
    if (state === "ready" && previous !== "ready") this.scheduleIdleRelease();
    if (state === "idle" || state === "disconnected") {
      this.cancelIdleRelease();
      this.debuggerHeld = false;
    }
    this.deps.onState(state);
    // PL-026: "each time the command session reaches ready" -- so on the transition into it, not on
    // every call. A reconnect, a restore and a finished command all arrive here, and all three are
    // moments the API should hear the version again.
    if (state === "ready" && previous !== "ready") {
      try {
        this.deps.reportVersion?.();
      } catch {
        // A report is a courtesy: one that fails must never disturb the state machine that owns
        // the socket, the bound tab and the commands.
      }
    }
  }

  /**
   * Pair with the hub and open the socket. The hub creates the session from the
   * active tab, and the extension opens the socket with the token it was given.
   */
  async connect(hubUrl: string, accessCode = ""): Promise<void> {
    const attempt = ++this.pairingAttempt;
    // PL-093: a newer Connect or a Disconnect took over while this one waited. It must not open a
    // socket (a zombie session the hub would keep and a worker would talk to) nor touch the state
    // the newer one owns, so it ends quietly.
    const overtaken = (): boolean => attempt !== this.pairingAttempt;
    this.stopped = true; // refuse a competing reconnect while pairing
    // PL-092: Connect starts over. A socket left open from an earlier session (an auto-connect the
    // person then pressed Connect over) stayed attached at the hub, a worker was started for it,
    // and its commands were run here but answered on the new socket: that worker heard nothing
    // and paused the run "Chrome disconnected". So the old socket is closed and a pending
    // reconnect, which would reopen it, is cancelled.
    this.cancelReconnect?.();
    this.cancelReconnect = null;
    this.dropSocket();
    this.setState("pairing");
    const info = await this.activeTabInfo();
    if (overtaken()) return;
    // PL-104: the session works in a window of its own, or not at all. Falling back to the person's
    // own tab meant every look at the run page hid the work and the run paused or failed.
    const boundTabId = await this.deps.bindActiveTab();
    if (overtaken()) return;
    if (boundTabId === null) {
      this.stopped = true;
      this.setState("idle");
      throw new Error(WORK_WINDOW_REFUSED);
    }

    const fetchFn = (globalThis as { fetch?: typeof fetch }).fetch;
    if (typeof fetchFn !== "function") throw new Error("fetch is unavailable in this context");
    const browser = this.deps.browserId ? await this.deps.browserId().catch(() => "") : "";
    if (overtaken()) return;
    let response: Response;
    try {
      response = await fetchFn(`${trimBase(hubUrl)}/api/v1/executor/sessions`, {
        method: "POST",
        headers: {
          "content-type": "application/json",
          "X-ProcessLens-Client": "extension",
          ...accessHeaders(accessCode),
        },
        body: JSON.stringify({
          tab_url: info.url,
          tab_title: info.title,
          ...(browser ? { browser_id: browser } : {}),
        }),
      });
    } catch (error) {
      if (overtaken()) return;
      // Nothing listening at that address (the popup's auto-connect to 127.0.0.1:8000 before the
      // demo is started) used to leave the session in "pairing" for good: the popup said
      // "Connecting to ProcessLens" forever and Connect stayed disabled. Back to idle, so it can be
      // pressed again.
      this.stopped = true;
      this.setState("idle");
      throw new Error(
        `ProcessLens is not answering at ${trimBase(hubUrl)} (${error instanceof Error ? error.message : String(error)})`,
      );
    }
    if (overtaken()) return;
    if (response?.status === 401) {
      // PL-086: a hosted ProcessLens without the right team access code.
      this.stopped = true;
      this.setState("idle");
      throw new Error(ACCESS_REFUSED);
    }
    if (!response || response.status !== 201) {
      this.stopped = true;
      this.setState("idle");
      throw new Error(`the hub refused the session (status ${response?.status ?? "?"})`);
    }
    const body = (await response.json()) as Record<string, unknown>;
    if (overtaken()) return;
    const sessionId = typeof body["session_id"] === "string" ? body["session_id"] : "";
    if (!sessionId) {
      this.stopped = true;
      this.setState("idle");
      throw new Error("the hub did not return a session id");
    }
    this.sessionToken = typeof body["session_token"] === "string" ? body["session_token"] : "";
    this.stored = { hubUrl, accessCode, sessionId, resumeToken: "", boundTabId };
    await this.deps.storage.set(this.stored);
    if (overtaken()) return;
    this.stopped = false;
    this.reconnectAttempt = 0;
    this.dropSocket();
    this.openSocket();
  }

  /**
   * Re-attach to a session that outlived a worker restart, using the resume
   * token the hub last issued. Returns whether a stored session was found.
   *
   * Idempotent (F6): top level, `onStartup` and the popup all ask to restore, and
   * only the first call may open a socket. It also adopts the stored bound tab
   * (B2) \u2014 without that, every command after a restart fails "no tab is bound"
   * \u2014 and never binds the active tab, which at restore time is whatever the
   * person is looking at, not the tab the run was bound to.
   */
  async restore(): Promise<boolean> {
    if (this.socket !== null || this.cancelReconnect !== null) return this.stored !== null;
    if (this.restoring) return this.stored !== null;
    this.restoring = true;
    try {
      const stored = await this.deps.storage.get();
      if (!stored) return false;
      this.stored = stored;
      this.stopped = false;
      this.reconnectAttempt = 0;
      if (stored.boundTabId >= 0) {
        this.announceTabClosed = !(await this.deps.restoreBoundTab(stored.boundTabId));
      }
      this.openSocket();
      return true;
    } finally {
      this.restoring = false;
    }
  }

  /**
   * Close the current socket on purpose, as Connect does before pairing again (PL-092).
   *
   * Its close event is not a drop (the socket is no longer current), so nothing reconnects. A
   * command still running on it is aborted and marked unwinding, exactly as a drop would, so it
   * cannot run beside a command from the new session. It is not reported lost later: its runner
   * belongs to the old session and has been told by the hub that this socket is gone.
   */
  private dropSocket(): void {
    const socket = this.socket;
    if (!socket) return;
    this.socket = null;
    this.stopKeepalive();
    this.currentAbort?.abort();
    const stillRunning = this.inFlight;
    if (stillRunning) this.unwinding = stillRunning.command_id;
    this.executing = null;
    this.inFlight = null;
    this.unreported = [];
    try {
      socket.close();
    } catch {
      // Already closed; nothing to do.
    }
  }

  /** Close the socket, forget the session, and go idle. */
  async disconnect(): Promise<void> {
    this.pairingAttempt += 1; // PL-093: a Connect still waiting on the hub opens nothing now
    this.stopped = true;
    this.cancelReconnect?.();
    this.cancelReconnect = null;
    this.stopKeepalive();
    const socket = this.socket;
    this.socket = null;
    if (socket) {
      try {
        socket.close();
      } catch {
        // The socket was already closed; nothing to do.
      }
    }
    this.stored = null;
    this.sessionToken = "";
    this.executing = null;
    this.inFlight = null;
    this.unreported = [];
    await this.deps.storage.set(null);
    this.setState("idle");
  }

  /**
   * The browser detached the debugger from the bound tab (PL-014b item 6). The service worker drops
   * its cached transport before calling this, so the next command re-attaches; the session is what
   * knows whether a command is in flight, so the session decides what to say.
   *
   * The runner must learn at once and the abandoned command must stop. `event detached` is sent
   * only while a command is current (item 7): the event belongs to the command, not to the session,
   * and an event with no current command would land on whatever record comes next. The abort makes
   * the command's own probe refuse its next input, so a dispatch the runner has written off cannot
   * still type or click.
   */
  notifyDebuggerDetached(reason: string): void {
    // PL-117: the browser took the debugger; there is nothing left for the quiet spell to release.
    this.cancelIdleRelease();
    this.debuggerHeld = false;
    this.releasing = null;
    if (this.inFlight === null) return;
    this.sendEvent("detached", `the debugger detached from the bound tab (${reason})`);
    this.currentAbort?.abort();
  }

  private async activeTabInfo(): Promise<{ id: number | null; url: string; title: string }> {
    const tabs = chromeTabs();
    if (!tabs) return { id: null, url: "", title: "" };
    try {
      const found = await tabs.query({ active: true, currentWindow: true });
      const tab = found[0];
      return { id: tab?.id ?? null, url: tab?.url ?? "", title: tab?.title ?? "" };
    } catch {
      return { id: null, url: "", title: "" };
    }
  }

  private openSocket(): void {
    const stored = this.stored;
    if (!stored) return;
    const token = stored.resumeToken || this.sessionToken;
    const socket = this.deps.createSocket(
      `${socketBase(stored.hubUrl)}/api/v1/executor/ws?session=${encodeURIComponent(stored.sessionId)}`,
      [PROTOCOL_SUBPROTOCOL, `${TOKEN_SUBPROTOCOL_PREFIX}${token}`],
    );
    this.socket = socket;
    this.setState("connecting");
    socket.addEventListener("open", () => this.onOpen(socket));
    socket.addEventListener("close", () => this.onClose(socket));
    socket.addEventListener("message", (event) => this.onMessage(socket, event));
    socket.addEventListener("error", () => {
      // The close handler owns the recovery; an error alone changes nothing.
    });
  }

  private onOpen(socket: WebSocketLike): void {
    if (this.socket !== socket) return;
    this.reconnectAttempt = 0;
    // A command aborted on the last close may still be unwinding, and until it settles a new
    // command is refused (B3). Saying "ready" then would be a promise the session cannot keep;
    // the aborted command's `finally` reports ready once it has settled.
    this.setState(this.unwinding === null ? "ready" : "executing");
    this.startKeepalive();
    // B2: a restore that found the bound tab gone says so, once the socket can carry it.
    if (this.announceTabClosed) {
      this.announceTabClosed = false;
      this.sendEvent("tab_closed", "the bound tab is gone");
    }
    // A reconnect must first tell the runner what it lost, before it takes new work.
    this.reportUnreported();
  }

  private onClose(socket: WebSocketLike): void {
    if (this.socket !== socket) return;
    this.socket = null;
    this.stopKeepalive();
    // B3: a command in flight when the socket dropped may still be mid-input. Abort it, so a
    // dispatch the runner has already written off as `lost` cannot still click, and mark it
    // unwinding, so a command that arrives on the reconnected socket never runs concurrently on
    // the same tab. The aborted command's `finally` clears the mark once its work settles.
    this.currentAbort?.abort();
    const stillRunning = this.inFlight;
    if (stillRunning) this.unwinding = stillRunning.command_id;
    if (this.stopped) {
      this.setState("idle");
      return;
    }
    // An in-flight command's outcome is unknown; report it lost on re-attach and
    // never re-run it. The runner reconciles the destination.
    const inFlight = this.inFlight;
    if (inFlight && !this.unreported.some((held) => held.command_id === inFlight.command_id)) {
      this.unreported.push(inFlight);
    }
    this.executing = null;
    this.inFlight = null;
    this.setState("disconnected");
    this.scheduleReconnect();
  }

  private scheduleReconnect(): void {
    if (this.stopped || !this.stored) return;
    if (this.cancelReconnect) return;
    const index = Math.min(this.reconnectAttempt, RECONNECT_SCHEDULE_MS.length - 1);
    const delay = RECONNECT_SCHEDULE_MS[index] ?? 4000;
    this.reconnectAttempt += 1;
    let fired = false;
    const cancel = this.deps.setInterval(() => {
      if (fired) return;
      fired = true;
      cancel();
      this.cancelReconnect = null;
      if (this.stopped) return;
      this.openSocket();
    }, delay);
    this.cancelReconnect = cancel;
  }

  private reportUnreported(): void {
    const lost = this.unreported;
    this.unreported = [];
    for (const command of lost) {
      this.sendResult(command.command_id, "lost", null, {}, "the connection dropped while this command was in flight");
    }
  }

  private startKeepalive(): void {
    this.stopKeepalive();
    this.cancelKeepalive = this.deps.setInterval(() => {
      this.send({ protocol: PROTOCOL, type: "ping" });
    }, KEEPALIVE_MS);
  }

  private stopKeepalive(): void {
    this.cancelKeepalive?.();
    this.cancelKeepalive = null;
  }

  /** PL-117: a command is starting, so a quiet spell is over. */
  private cancelIdleRelease(): void {
    this.cancelIdle?.();
    this.cancelIdle = null;
  }

  /**
   * PL-117 item 5: after the last command, wait a quiet spell and let the debugger go.
   *
   * Only a command that actually held the debugger arms it, and only while no command is in flight
   * and the session is ready. One timer per quiet spell: a pause or the end of a run releases once,
   * and the next command cancels the timer (or waits out a release already under way) before it
   * attaches again.
   */
  private scheduleIdleRelease(): void {
    if (!this.debuggerHeld || this.cancelIdle !== null) return;
    const arm = this.deps.setTimeout;
    const release = this.deps.releaseDebugger;
    if (!arm || !release) return;
    this.cancelIdle = arm(() => {
      this.cancelIdle = null;
      if (!this.debuggerHeld || this.inFlight !== null || this.state !== "ready") return;
      this.debuggerHeld = false;
      try {
        this.releasing = Promise.resolve(release()).catch(() => undefined);
      } catch {
        this.releasing = null;
      }
    }, DEBUGGER_IDLE_RELEASE_MS);
  }

  private send(message: OutgoingMessage): void {
    const socket = this.socket;
    if (!socket) return;
    try {
      socket.send(JSON.stringify(message));
    } catch {
      // A dropped socket is handled by its close listener.
    }
  }

  private sendResult(
    commandId: string,
    status: CommandStatus,
    rung: number | null,
    observed: Record<string, unknown>,
    error: string | null,
  ): void {
    this.send({
      protocol: PROTOCOL,
      type: "result",
      command_id: commandId,
      status,
      rung,
      observed,
      error,
    });
  }

  private sendEvent(kind: EventKind, detail: string): void {
    // PL-102: an emailed code is looked for from the moment its page appeared, not before.
    if (kind === "code_required") this.codePageAt = Date.now();
    this.send({ protocol: PROTOCOL, type: "event", kind, detail });
  }

  private onMessage(socket: WebSocketLike, event: unknown): void {
    // PL-092: only the current socket speaks for this session. A command arriving on one that was
    // replaced must not run, because its answer could only go out on the new socket, to a runner
    // that never sent it.
    if (socket !== this.socket) return;
    const data = typeof event === "object" && event !== null ? (event as { data?: unknown }).data : undefined;
    if (typeof data !== "string") return;
    let raw: unknown;
    try {
      raw = JSON.parse(data);
    } catch {
      return;
    }
    // Fail-closed: a command in a version we do not speak is refused with a
    // reply, because the contract requires a refusal, not silence. Any other
    // message is validated by the shared contract below.
    if (typeof raw === "object" && raw !== null) {
      const record = raw as Record<string, unknown>;
      const type = record["type"];
      const isCommandType =
        type === "prepare" || type === "dispatch" || type === "check" || type === "enter_code";
      if (isCommandType && record["protocol"] !== PROTOCOL) {
        const commandId = typeof record["command_id"] === "string" ? record["command_id"] : "";
        if (commandId) {
          this.sendResult(
            commandId,
            "refused",
            null,
            {},
            `unsupported protocol ${String(record["protocol"])}`,
          );
        }
        return;
      }
    }
    const parsed = parseIncoming(raw);
    if ("error" in parsed) {
      // F3: a malformed command is refused, not dropped silently. When it still carries an id,
      // the runner gets a refusal instead of waiting out a reply that will never come.
      if (typeof raw === "object" && raw !== null) {
        const record = raw as Record<string, unknown>;
        const type = record["type"];
        if (
          (type === "prepare" ||
            type === "dispatch" ||
            type === "check" ||
            type === "enter_code") &&
          record["protocol"] === PROTOCOL
        ) {
          const commandId = typeof record["command_id"] === "string" ? record["command_id"] : "";
          if (commandId) this.sendResult(commandId, "refused", null, {}, parsed.error);
        }
      }
      return;
    }
    const message = parsed.message;
    switch (message.type) {
      case "session":
        void this.onSessionMessage(message.resume_token);
        return;
      case "ping":
        this.send({ protocol: PROTOCOL, type: "pong" });
        return;
      case "peer_gone":
        // Surface it; the runner decides. Nothing is done step-wise.
        this.notice = "the runner's peer socket dropped";
        return;
      case "hub_error":
        this.notice = `the hub could not deliver a message (${message.code})`;
        return;
      case "prepare":
      case "dispatch":
      case "check":
      case "guard":
      case "enter_code":
        void this.onCommand(message);
        return;
      default:
        return;
    }
  }

  private async onSessionMessage(resumeToken: string): Promise<void> {
    if (!this.stored) return;
    this.stored = { ...this.stored, resumeToken };
    await this.deps.storage.set(this.stored);
  }

  private async tabAlive(): Promise<boolean> {
    const tabs = chromeTabs();
    const stored = this.stored;
    if (!tabs || !stored || stored.boundTabId < 0) return true;
    try {
      await tabs.get(stored.boundTabId);
      return true;
    } catch {
      return false;
    }
  }

  private async onCommand(command: CommandMessage): Promise<void> {
    // S1: record the lease epoch of EVERY well-formed command, before any
    // refusal. A command refused for another reason still advances the run's
    // high-water mark, so an older lease can never be accepted later.
    const priorEpoch = this.highestEpoch.get(command.run_id);
    if (priorEpoch === undefined || command.lease_epoch > priorEpoch) {
      this.highestEpoch.set(command.run_id, command.lease_epoch);
    }

    const refuse = (reason: string): void => {
      this.sendResult(command.command_id, "refused", null, {}, reason);
    };

    if (command.protocol !== PROTOCOL) {
      refuse(`unsupported protocol ${String(command.protocol)}`);
      return;
    }
    if (priorEpoch !== undefined && command.lease_epoch < priorEpoch) {
      refuse(`stale lease epoch ${command.lease_epoch} (this run is at ${priorEpoch})`);
      return;
    }
    if (this.recentIds.has(command.command_id)) {
      refuse("this command id has already been handled");
      return;
    }
    if (command.expires_at <= this.deps.now()) {
      refuse("the command expired before it was received");
      return;
    }
    if (!(await this.tabAlive())) {
      this.sendEvent("tab_closed", "the bound tab is gone");
      refuse("the bound tab is gone");
      return;
    }
    if (this.executing !== null) {
      refuse("another command is already executing");
      return;
    }
    if (!this.canTake()) {
      // B3: an aborted command may still be unwinding its input; a new one must wait rather than
      // run concurrently on the same tab.
      refuse("the previous command has not finished unwinding");
      return;
    }

    // B3: an abort probe for THIS command. `onClose` aborts it, and every trusted input checks it
    // immediately before it acts, so a command the runner has written off cannot still click.
    const abort = new AbortController();
    this.currentAbort = abort;
    this.unwinding = null;
    const withdrawn = (): boolean => abort.signal.aborted;

    this.executing = command.command_id;
    this.inFlight = command;
    // A result is only sent while this command is still the session's in-flight
    // one. If the socket dropped mid-run, `onClose` cleared the in-flight command
    // and queued it as `unreported`; its late outcome must not be sent as a
    // second, contradictory result on the reconnected socket.
    const current = (): boolean => this.inFlight === command;
    let sent = false;
    // Set when the input gate (F1, B3) refuses: that is a `refused`, distinct from a failure that
    // never reached the gate (`not_dispatched`) and from an outcome after input (`lost`).
    let gateRefusal: string | null = null;
    try {
      // PL-117 item 5: the quiet spell is over. A release that is still running finishes first,
      // then the debugger is held again before this command's step runs. A command that arrives
      // after a pause therefore re-attaches exactly as the first one did.
      this.cancelIdleRelease();
      const releasing = this.releasing;
      if (releasing !== null) {
        await releasing;
        if (this.releasing === releasing) this.releasing = null;
      }
      if (!current()) return;
      await this.deps.wakeDebugger?.();
      this.debuggerHeld = true;
      if (command.type === "guard") {
        // The guard is not a step: it has no target, sends no input, and its answer is what it
        // refused. Arming is what a record that leaves the tab without committing needs; disarming
        // reports what the guard held back, so the runner can write it to the ledger (PL-027).
        let blocked: BlockedRequest[] = [];
        if (command.action === "arm") {
          await this.deps.armAbandonGuard();
        } else {
          blocked = await this.deps.disarmAbandonGuard();
        }
        if (current()) this.sendResult(command.command_id, "ok", null, { blocked }, null);
        return;
      }
      if (command.type === "enter_code") {
        // PL-068: the person supplied the code on the run page; it is typed here and reported as
        // entered or not found. The answer never contains the code, and nothing stores it.
        try {
          // PL-102: or the runner (or the person's "Yes" on the run page) asked for the code to be
          // read from the person's own Gmail, starting from when the code page appeared.
          let answer: CodeAnswer;
          if (command.from_email === true) {
            answer = this.deps.codeFromEmail
              ? await this.deps.codeFromEmail({
                  since: this.codePageAt ?? Date.now() - CODE_PAGE_FALLBACK_MS,
                  remember: command.remember === true,
                  lookMs: typeof command.look_ms === "number" ? command.look_ms : DEFAULT_LOOK_MS,
                  stop: () => this.stopped || withdrawn(),
                })
              : { entered: false, reason: "no_gmail" };
          } else {
            answer = this.deps.enterCode
              ? await this.deps.enterCode(command.code ?? "")
              : { entered: false, reason: "not_found" };
          }
          if (current()) {
            this.sendResult(
              command.command_id,
              answer.entered ? "ok" : "locator_failed",
              null,
              {
                entered: answer.entered,
                reason: answer.reason,
                ...(answer.cleared !== undefined ? { cleared: answer.cleared } : {}),
              },
              answer.entered ? null : "the page's code field could not be found",
            );
          }
        } catch (error) {
          if (current()) {
            this.sendResult(
              command.command_id,
              "not_dispatched",
              null,
              { entered: false },
              error instanceof Error ? error.message : String(error),
            );
          }
        }
        return;
      }
      if (command.type === "check") {
        const answer = await this.deps.checkCondition(command.condition, command.timeout_ms, () =>
          this.stopped || withdrawn(),
        );        if (current()) {
          if (answer.ok) {
            // C3 (R6): the page is showing a wall the check just found under a clean "no". The
            // event goes out BEFORE the condition reply it explains, only while this command is
            // still current (item 7), so the runner can release the record and pause the run with
            // the wall's reason instead of taking an `else` branch on a page that needs a person.
            // The answer is unchanged: the reply below still carries the same `passed` value.
            if (answer.event) {
              this.sendEvent(
                answer.event,
                answer.eventDetail ?? "the page needs a person before this condition can hold",
              );
            }
            this.send({
              protocol: PROTOCOL,
              type: "condition",
              command_id: command.command_id,
              passed: answer.passed,
              observed:
                answer.passed || answer.codePage === undefined
                  ? {}
                  : { code_page: answer.codePage },
            });
          } else {
            // B1: a check that could not be evaluated is a REFUSAL, never a real "no". Reporting
            // it as `condition passed:false` would take the runner's duplicate-search `else` branch
            // and create the lead a second time.
            this.sendResult(command.command_id, "refused", null, {}, answer.reason);
          }
        }
      } else {
        // R4: re-check the expiry immediately before the input event. A slow
        // prepare must never let a command that has since expired type into a
        // page. A `prepare` sends no input, so its receipt check is enough.
        if (command.type === "dispatch" && command.expires_at <= this.deps.now()) {
          refuse("the command expired before any input was sent");
          return;
        }
        const stepOptions = {
          sentInput: () => {
            sent = true;
            if (current()) {
              this.send({ protocol: PROTOCOL, type: "ack", command_id: command.command_id });
            }
          },
          // R2: a `prepare` resolves and hit-tests the target and sends NOTHING. The runner's
          // write-ahead only happens after a successful prepare, so a prepare that typed or
          // clicked would make a mere locator failure look like a commit attempt — and would
          // dispatch every commit twice.
          phase: command.type === "prepare" ? ("prepare" as const) : ("dispatch" as const),
          allowedOrigins: command.allowed_origins,
          // F1: one probe, run immediately before EVERY trusted input event, so the expiry and the
          // withdrawal are judged at the last moment rather than before the guards and the resolve.
          beforeInput: () => {
            try {
              this.assertInputAllowed(command, withdrawn);
            } catch (error) {
              gateRefusal = error instanceof Error ? error.message : String(error);
              throw error;
            }
          },
        };
        this.setState("executing");
        const report = await this.deps.runStep(command.step, stepOptions);
        if (current()) {
          this.sendResult(
            command.command_id,
            "ok",
            report.rung ?? null,
            {
              id: report.id,
              action: report.action,
              outcome: report.outcome,
              rung: report.rung ?? null,
              by: report.by ?? null,
              // What the step saw beyond its own success (PL-027): a navigate that released the
              // abandon guard reports the requests it refused.
              ...(report.observed ?? {}),
            },
            null,
          );
        }
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      // R6: a run-level event is sent BEFORE the refusal it causes, so the runner can release the
      // record and pause the run instead of reading only the refusal and parking the record. Item 7:
      // it is sent only while this command is still current — an event from a command the socket
      // abandoned must not land on the next record's socket.
      if (error instanceof ExecutorError && error.event) {
        if (current()) this.sendEvent(error.event, message);
      }
      // A policy-blocked attach is a run-level event, not just a failed command. Gated the same
      // way: it, too, belongs to the command that is current.
      if (message === POLICY_BLOCKED_MESSAGE && current()) this.sendEvent("policy_blocked", message);
      if (current()) {
        // F2: once any input event has reached the page the outcome is unknown, so the honest
        // report is `lost` (the runner reconciles) — never `rejected`, which would assert a
        // rejection the extension cannot prove. A gate refusal is `refused`; a failure that never
        // reached the gate is a clean `not_dispatched`.
        const status: CommandStatus = sent
          ? "lost"
          : gateRefusal !== null
            ? "refused"
            : "not_dispatched";
        this.sendResult(command.command_id, status, null, {}, gateRefusal ?? message);
      }
    } finally {
      this.recentIds.add(command.command_id);
      if (this.currentAbort === abort) this.currentAbort = null;
      if (this.unwinding === command.command_id) {
        this.unwinding = null;
        // The reconnected socket held back "ready" while this command unwound; say it now.
        if (this.socket && this.inFlight === null && this.state === "executing") {
          this.setState("ready");
        }
      }
      if (current()) {
        this.executing = null;
        this.inFlight = null;
        if (this.state === "executing") {
          this.setState(this.socket ? "ready" : "disconnected");
        }
        // A check, guard or code command never entered `executing`; its quiet spell starts here.
        if (this.state === "ready") this.scheduleIdleRelease();
      }
    }
  }

  /** Whether a new command may be taken now (B3). */
  private canTake(): boolean {
    return this.unwinding === null;
  }

  /**
   * The probe that runs immediately before every trusted input event (F1, B3). It refuses a
   * command that has been withdrawn or has expired, judged at the last moment rather than before
   * the guards and the resolve, so a slow prepare cannot type into a dead command.
   */
  private assertInputAllowed(command: CommandMessage, withdrawn: () => boolean): void {
    if (withdrawn()) {
      throw new ExecutorError(
        "the command was withdrawn before any input was sent",
        undefined,
        "park",
      );
    }
    const expiry = recheckExpiry(command.expires_at, this.deps.now);
    if (!expiry.ok) {
      throw new ExecutorError(expiry.reason, undefined, "park");
    }
  }
}
