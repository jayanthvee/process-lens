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
import type { StepReport } from "../executor/types";
import { POLICY_BLOCKED_MESSAGE } from "../executor/guards";

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
  sessionId: string;
  resumeToken: string;
  boundTabId: number;
}

/** Every side effect, injected. */
export interface SessionDeps {
  createSocket: (url: string, protocols: string[]) => WebSocketLike;
  storage: { get(): Promise<StoredSession | null>; set(v: StoredSession | null): Promise<void> };
  now: () => number;
  /** Returns a cancel for the timer. */
  setInterval: (fn: () => void, ms: number) => () => void;
  bindActiveTab: () => Promise<number | null>;
  /** Wraps step-runner: resolve, guard, perform. `sentInput` fires after the input. */
  runStep: (step: unknown, opts: { sentInput: () => void }) => Promise<StepReport>;
  checkCondition: (condition: unknown, timeoutMs: number, aborted: () => boolean) => Promise<boolean>;
  onState: (state: SessionState) => void;
}

/** The backoff schedule for a dropped socket: 500 ms → 1 s → 2 s → 4 s (cap). */
const RECONNECT_SCHEDULE_MS = [500, 1000, 2000, 4000];

/** Trim trailing slashes so a hub URL composes cleanly. */
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
  /** The last peer_gone / hub_error the runner may want to act on. */
  private notice: string | null = null;

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
    this.state = state;
    this.deps.onState(state);
  }

  /**
   * Pair with the hub and open the socket. The hub creates the session from the
   * active tab, and the extension opens the socket with the token it was given.
   */
  async connect(hubUrl: string): Promise<void> {
    this.stopped = true; // refuse a competing reconnect while pairing
    this.setState("pairing");
    const info = await this.activeTabInfo();
    const boundTabId = (await this.deps.bindActiveTab()) ?? info.id ?? -1;

    const fetchFn = (globalThis as { fetch?: typeof fetch }).fetch;
    if (typeof fetchFn !== "function") throw new Error("fetch is unavailable in this context");
    const response = await fetchFn(`${trimBase(hubUrl)}/api/v1/executor/sessions`, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "X-ProcessLens-Client": "extension",
      },
      body: JSON.stringify({ tab_url: info.url, tab_title: info.title }),
    });
    if (!response || response.status !== 201) {
      this.stopped = true;
      this.setState("idle");
      throw new Error(`the hub refused the session (status ${response?.status ?? "?"})`);
    }
    const body = (await response.json()) as Record<string, unknown>;
    const sessionId = typeof body["session_id"] === "string" ? body["session_id"] : "";
    if (!sessionId) {
      this.stopped = true;
      this.setState("idle");
      throw new Error("the hub did not return a session id");
    }
    this.sessionToken = typeof body["session_token"] === "string" ? body["session_token"] : "";
    this.stored = { hubUrl, sessionId, resumeToken: "", boundTabId };
    await this.deps.storage.set(this.stored);
    this.stopped = false;
    this.reconnectAttempt = 0;
    this.openSocket();
  }

  /**
   * Re-attach to a session that outlived a worker restart, using the resume
   * token the hub last issued. Returns whether a stored session was found.
   */
  async restore(): Promise<boolean> {
    const stored = await this.deps.storage.get();
    if (!stored) return false;
    this.stored = stored;
    this.stopped = false;
    this.reconnectAttempt = 0;
    this.openSocket();
    return true;
  }

  /** Close the socket, forget the session, and go idle. */
  async disconnect(): Promise<void> {
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
    socket.addEventListener("message", (event) => this.onMessage(event));
    socket.addEventListener("error", () => {
      // The close handler owns the recovery; an error alone changes nothing.
    });
  }

  private onOpen(socket: WebSocketLike): void {
    if (this.socket !== socket) return;
    this.reconnectAttempt = 0;
    this.setState("ready");
    this.startKeepalive();
    // A reconnect must first tell the runner what it lost, before it takes new work.
    this.reportUnreported();
  }

  private onClose(socket: WebSocketLike): void {
    if (this.socket !== socket) return;
    this.socket = null;
    this.stopKeepalive();
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
    this.send({ protocol: PROTOCOL, type: "event", kind, detail });
  }

  private onMessage(event: unknown): void {
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
      const isCommandType = type === "prepare" || type === "dispatch" || type === "check";
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
    if ("error" in parsed) return;
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

    this.executing = command.command_id;
    this.inFlight = command;
    // A result is only sent while this command is still the session's in-flight
    // one. If the socket dropped mid-run, `onClose` cleared the in-flight command
    // and queued it as `unreported`; its late outcome must not be sent as a
    // second, contradictory result on the reconnected socket.
    const current = (): boolean => this.inFlight === command;
    let sent = false;
    try {
      if (command.type === "check") {
        const passed = await this.deps.checkCondition(command.condition, command.timeout_ms, () => this.stopped);
        if (current()) {
          this.send({
            protocol: PROTOCOL,
            type: "condition",
            command_id: command.command_id,
            passed,
            observed: {},
          });
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
          allowedOrigins: command.allowed_origins,
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
            },
            null,
          );
        }
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      // A policy-blocked attach is a run-level event, not just a failed command.
      if (message === POLICY_BLOCKED_MESSAGE) this.sendEvent("policy_blocked", message);
      if (current()) {
        this.sendResult(command.command_id, sent ? "rejected" : "not_dispatched", null, {}, message);
      }
    } finally {
      this.recentIds.add(command.command_id);
      if (current()) {
        this.executing = null;
        this.inFlight = null;
        if (this.state === "executing") {
          this.setState(this.socket ? "ready" : "disconnected");
        }
      }
    }
  }
}
