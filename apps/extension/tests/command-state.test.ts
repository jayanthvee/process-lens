// The command session's state machine (PL-014), driven entirely through its
// injected dependencies: a fake socket, a fake storage area, a fake clock, and a
// fake timer seam. No real Chrome, no real socket, no real network.
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import {
  ACCESS_REFUSED,
  CommandSession,
  type CheckAnswer,
  type SessionDeps,
  type SessionState,
  type StoredSession,
  type WebSocketLike,
} from "../src/background/command-session";
import { ExecutorError, type StepReport } from "../src/executor/types";

/** A socket shaped exactly like the interface, so a plain object satisfies it. */
class FakeSocket implements WebSocketLike {
  readonly sent: string[] = [];
  closed = false;
  private readonly listeners = new Map<string, Array<(event: unknown) => void>>();

  constructor(
    readonly url: string,
    readonly protocols: string[],
  ) {}

  send(data: string): void {
    this.sent.push(data);
  }

  close(): void {
    this.closed = true;
  }

  addEventListener(type: string, listener: (event: unknown) => void): void {
    const list = this.listeners.get(type) ?? [];
    list.push(listener);
    this.listeners.set(type, list);
  }

  emit(type: string, event: unknown = {}): void {
    for (const listener of this.listeners.get(type) ?? []) listener(event);
  }

  messages(): Array<Record<string, unknown>> {
    return this.sent.map((line) => JSON.parse(line) as Record<string, unknown>);
  }

  lastResult(): Record<string, unknown> | undefined {
    return [...this.messages()].reverse().find((message) => message["type"] === "result");
  }
}

interface Timer {
  fn: () => void;
  ms: number;
  cancelled: boolean;
}

interface Harness {
  session: CommandSession;
  sockets: FakeSocket[];
  states: SessionState[];
  timers: Timer[];
  clock: { value: number };
  runStepCalls: unknown[];
  restoredTabs: number[];
  storagePeek(): StoredSession | null;
  fireTimers(ms: number): number;
}

interface HarnessOptions {
  runStep?: (
    step: unknown,
    opts: {
      sentInput: () => void;
      phase?: "prepare" | "dispatch";
      allowedOrigins?: string[];
      beforeInput: () => void;
    },
  ) => Promise<StepReport>;
  checkCondition?: (
    condition: unknown,
    timeoutMs: number,
    aborted: () => boolean,
  ) => Promise<CheckAnswer>;
  restoreBoundTab?: (tabId: number) => Promise<boolean>;
  enterCode?: (code: string) => Promise<{ entered: boolean; reason: string }>;
  now?: () => number;
  storageValue?: StoredSession | null;
  browserId?: string;
}

function makeHarness(options: HarnessOptions = {}): Harness {
  const sockets: FakeSocket[] = [];
  const states: SessionState[] = [];
  const timers: Timer[] = [];
  const clock = { value: 1_000 };
  const runStepCalls: unknown[] = [];
  const restoredTabs: number[] = [];
  let stored: StoredSession | null = options.storageValue ?? null;

  const deps: SessionDeps = {
    createSocket: (url, protocols) => {
      const socket = new FakeSocket(url, protocols);
      sockets.push(socket);
      return socket;
    },
    storage: {
      async get(): Promise<StoredSession | null> {
        return stored;
      },
      async set(value: StoredSession | null): Promise<void> {
        stored = value;
      },
    },
    now: options.now ?? (() => clock.value),
    ...(options.enterCode ? { enterCode: options.enterCode } : {}),
    setInterval: (fn, ms) => {
      const timer: Timer = { fn, ms, cancelled: false };
      timers.push(timer);
      return () => {
        timer.cancelled = true;
      };
    },
    bindActiveTab: async () => 7,
    ...(options.browserId ? { browserId: async () => options.browserId ?? "" } : {}),
    restoreBoundTab: async (tabId: number) => {
      restoredTabs.push(tabId);
      if (options.restoreBoundTab) return options.restoreBoundTab(tabId);
      return true;
    },
    runStep: async (step, opts) => {
      runStepCalls.push(step);
      if (options.runStep) return options.runStep(step, opts);
      opts.beforeInput();
      opts.sentInput();
      return { id: "step-1", action: "click", outcome: "done", rung: 0, by: "role" };
    },
    checkCondition: async (condition, timeoutMs, aborted) => {
      if (options.checkCondition) return options.checkCondition(condition, timeoutMs, aborted);
      return { ok: true, passed: true };
    },
    // PL-027: the abandon guard's two deps. These tests never arm it; the guard has its own.
    armAbandonGuard: async () => undefined,
    disarmAbandonGuard: async () => [],
    onState: (state) => states.push(state),
  };

  const session = new CommandSession(deps);
  return {
    session,
    sockets,
    states,
    timers,
    clock,
    runStepCalls,
    restoredTabs,
    storagePeek: () => stored,
    fireTimers(ms) {
      let fired = 0;
      for (const timer of timers) {
        if (timer.ms === ms && !timer.cancelled) {
          fired += 1;
          timer.fn();
        }
      }
      return fired;
    },
  };
}

const originalFetch = globalThis.fetch;

beforeEach(() => {
  globalThis.fetch = (async () => ({
    status: 201,
    json: async () => ({ session_id: "sess-1", session_token: "tok-1" }),
  })) as unknown as typeof fetch;
});

afterEach(() => {
  globalThis.fetch = originalFetch;
});

async function flush(): Promise<void> {
  await new Promise<void>((resolve) => setTimeout(resolve, 0));
  await new Promise<void>((resolve) => setTimeout(resolve, 0));
}

async function openSession(harness: Harness): Promise<FakeSocket> {
  await harness.session.connect("http://127.0.0.1:8000");
  const socket = harness.sockets[harness.sockets.length - 1];
  if (!socket) throw new Error("the session did not open a socket");
  socket.emit("open");
  return socket;
}

function command(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    protocol: 1,
    type: "dispatch",
    command_id: "c1",
    step_ref: "s1",
    run_id: "run-1",
    run_record_id: "rec-1",
    lease_epoch: 1,
    expires_at: 10_000_000,
    step: { id: "step-1", action: "click", targets: [{ by: "role", role: "button", name: "Save" }] },
    commit: true,
    allowed_origins: [],
    ...overrides,
  };
}

function checkCommand(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    protocol: 1,
    type: "check",
    command_id: "c-check",
    step_ref: "s1",
    run_id: "run-1",
    run_record_id: "rec-1",
    lease_epoch: 1,
    expires_at: 10_000_000,
    condition: { text_visible: "Lead created" },
    timeout_ms: 500,
    ...overrides,
  };
}

function deliver(socket: FakeSocket, message: unknown): void {
  socket.emit("message", { data: JSON.stringify(message) });
}

function deferredStep(): { promise: Promise<StepReport>; resolve: (report: StepReport) => void } {
  let resolve!: (report: StepReport) => void;
  const promise = new Promise<StepReport>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

describe("CommandSession", () => {
  it("a guard failure sends its run-level event before the refusal it causes", async () => {
    // R6: the runner decides from the event whether the record is released and the run paused, or
    // the record is parked. A refusal alone cannot express that, so the order is the contract.
    const harness = makeHarness({
      runStep: async () => {
        throw new ExecutorError("the page is showing a challenge", "open", "park", "challenge");
      },
    });
    const socket = await openSession(harness);

    deliver(socket, command({ command_id: "c-event", type: "dispatch" }));
    await flush();

    const kinds = socket.messages().map((message) => message["type"]);
    const eventAt = kinds.indexOf("event");
    const resultAt = kinds.indexOf("result");
    expect(eventAt).toBeGreaterThanOrEqual(0);
    expect(resultAt).toBeGreaterThan(eventAt);
    const sent = socket.messages()[eventAt];
    expect(sent?.["kind"]).toBe("challenge");
  });

  it("a guard failure with no run-level meaning sends no event", async () => {
    // A destructive-control refusal is a refusal, not an event: the record parks, the run does not.
    const harness = makeHarness({
      runStep: async () => {
        throw new ExecutorError("refusing to click Delete lead", "delete", "park");
      },
    });
    const socket = await openSession(harness);

    deliver(socket, command({ command_id: "c-plain", type: "dispatch" }));
    await flush();

    expect(socket.messages().some((message) => message["type"] === "event")).toBe(false);
    expect(socket.lastResult()?.["status"]).toBe("not_dispatched");
  });

  it("a stale lease epoch is refused", async () => {
    const harness = makeHarness();
    const socket = await openSession(harness);

    deliver(socket, command({ command_id: "c1", lease_epoch: 5 }));
    await flush();
    expect(socket.lastResult()?.["status"]).toBe("ok");

    deliver(socket, command({ command_id: "c2", lease_epoch: 3 }));
    await flush();

    const refused = socket.lastResult();
    expect(refused?.["command_id"]).toBe("c2");
    expect(refused?.["status"]).toBe("refused");
    expect(String(refused?.["error"])).toMatch(/stale lease/);
  });

  it("a repeated command id is refused", async () => {
    const harness = makeHarness();
    const socket = await openSession(harness);

    deliver(socket, command({ command_id: "c1" }));
    await flush();
    expect(harness.runStepCalls).toHaveLength(1);

    deliver(socket, command({ command_id: "c1" }));
    await flush();

    const refused = socket.lastResult();
    expect(refused?.["status"]).toBe("refused");
    expect(String(refused?.["error"])).toMatch(/already been handled/);
    expect(harness.runStepCalls).toHaveLength(1);
  });

  it("an expired command is refused", async () => {
    const harness = makeHarness();
    const socket = await openSession(harness);

    deliver(socket, command({ command_id: "c1", expires_at: harness.clock.value - 1 }));
    await flush();

    const refused = socket.lastResult();
    expect(refused?.["status"]).toBe("refused");
    expect(String(refused?.["error"])).toMatch(/expired/);
    expect(harness.runStepCalls).toHaveLength(0);
  });

  it("an expired command is refused before any input is sent", async () => {
    // Fresh when it is received; expired by the time the input would be sent.
    let calls = 0;
    const harness = makeHarness({
      now: () => {
        calls += 1;
        return calls === 1 ? 5_000 : 500_000;
      },
    });
    const socket = await openSession(harness);

    deliver(socket, command({ command_id: "c1", expires_at: 100_000 }));
    await flush();

    const refused = socket.lastResult();
    expect(refused?.["status"]).toBe("refused");
    expect(String(refused?.["error"])).toMatch(/expired before any input/);
    // The step-runner never saw the command, so no input was ever sent.
    expect(harness.runStepCalls).toHaveLength(0);
  });

  it("an in-flight command is reported lost after a reconnect and never re-run", async () => {
    const pending = deferredStep();
    const harness = makeHarness({ runStep: () => pending.promise });
    const socket = await openSession(harness);

    deliver(socket, command({ command_id: "c1" }));
    await flush();
    expect(harness.runStepCalls).toHaveLength(1);

    socket.emit("close");
    expect(harness.session.currentState).toBe("disconnected");

    expect(harness.fireTimers(500)).toBe(1);
    const reconnected = harness.sockets[1];
    expect(reconnected).toBeDefined();
    reconnected?.emit("open");
    await flush();

    const lost = reconnected?.messages().find((message) => message["type"] === "result");
    expect(lost?.["command_id"]).toBe("c1");
    expect(lost?.["status"]).toBe("lost");
    // The command was surfaced as lost, never re-run.
    expect(harness.runStepCalls).toHaveLength(1);

    pending.resolve({ id: "step-1", action: "click", outcome: "done" });
    await flush();
  });

  it("a restarted worker re-attaches with the resume token", async () => {
    const first = makeHarness();
    const socket = await openSession(first);
    deliver(socket, { protocol: 1, type: "session", resume_token: "rt-1" });
    await flush();
    expect(first.storagePeek()?.resumeToken).toBe("rt-1");
    expect(first.storagePeek()?.sessionId).toBe("sess-1");

    // Discard the session and build a new one over the same storage, the way a
    // restarted service worker would.
    const second = makeHarness({ storageValue: first.storagePeek() });
    const reattached = await second.session.restore();
    expect(reattached).toBe(true);

    const restored = second.sockets[0];
    expect(restored).toBeDefined();
    expect(restored?.protocols).toContain("pl.token.rt-1");
    expect(restored?.url).toContain("session=sess-1");
  });

  it("a second command while one is executing is refused", async () => {
    const pending = deferredStep();
    const harness = makeHarness({ runStep: () => pending.promise });
    const socket = await openSession(harness);

    deliver(socket, command({ command_id: "c1" }));
    await flush();

    deliver(socket, command({ command_id: "c2" }));
    await flush();

    const refused = socket.lastResult();
    expect(refused?.["command_id"]).toBe("c2");
    expect(refused?.["status"]).toBe("refused");
    expect(String(refused?.["error"])).toMatch(/already executing/);

    pending.resolve({ id: "step-1", action: "click", outcome: "done" });
    await flush();
  });

  it("a check that could not be evaluated is refused, never answered false", async () => {
    // B1: a failed duplicate search reaching the runner as a real "no" would take the `else`
    // branch and create the lead again.
    const harness = makeHarness({
      checkCondition: async () => ({ ok: false, reason: "no tab is bound to this session" }),
    });
    const socket = await openSession(harness);

    deliver(socket, {
      protocol: 1,
      type: "check",
      command_id: "c-check",
      step_ref: "s1",
      run_id: "run-1",
      run_record_id: "rec-1",
      lease_epoch: 1,
      expires_at: 10_000_000,
      condition: { text_visible: "Lead created" },
      timeout_ms: 500,
    });
    await flush();

    const messages = socket.messages();
    expect(messages.some((message) => message["type"] === "condition")).toBe(false);
    const result = socket.lastResult();
    expect(result?.["status"]).toBe("refused");
    expect(String(result?.["error"])).toMatch(/no tab is bound/);
  });

  it("a wall under a failed check is sent as an event BEFORE the condition reply", async () => {
    // C3 (R6): the condition was evaluated and did not hold on a page showing a challenge. The
    // runner must learn about the wall first, so it releases the record and pauses the run with the
    // wall's reason instead of taking the `else` branch. The answer itself is unchanged.
    const harness = makeHarness({
      checkCondition: async () => ({
        ok: true,
        passed: false,
        event: "challenge",
        eventDetail: "the page is showing a challenge (Just a moment)",
      }),
    });
    const socket = await openSession(harness);

    deliver(socket, checkCommand({ command_id: "c-wall" }));
    await flush();

    const messages = socket.messages();
    const eventAt = messages.findIndex((message) => message["type"] === "event");
    const conditionAt = messages.findIndex((message) => message["type"] === "condition");
    expect(eventAt).toBeGreaterThanOrEqual(0);
    expect(conditionAt).toBeGreaterThan(eventAt);
    expect(messages[eventAt]?.["kind"]).toBe("challenge");
    expect(String(messages[eventAt]?.["detail"])).toMatch(/challenge/);
    // The answer is unchanged: still a clean "no", with no refusal added.
    expect(messages[conditionAt]?.["passed"]).toBe(false);
    expect(messages.some((message) => message["type"] === "result")).toBe(false);
  });

  it("a login wall under a failed check is sent as an event BEFORE the condition reply", async () => {
    const harness = makeHarness({
      checkCondition: async () => ({
        ok: true,
        passed: false,
        event: "login_required",
        eventDetail: "the page is asking for a password (password), which this step is not",
      }),
    });
    const socket = await openSession(harness);

    deliver(socket, checkCommand({ command_id: "c-login" }));
    await flush();

    const messages = socket.messages();
    const eventAt = messages.findIndex((message) => message["type"] === "event");
    const conditionAt = messages.findIndex((message) => message["type"] === "condition");
    expect(messages[eventAt]?.["kind"]).toBe("login_required");
    expect(conditionAt).toBeGreaterThan(eventAt);
    expect(messages[conditionAt]?.["passed"]).toBe(false);
  });

  it("a clean false on a clean page sends no event", async () => {
    // C3: only a wall produces an event; an ordinary "no" is just the condition reply.
    const harness = makeHarness({ checkCondition: async () => ({ ok: true, passed: false }) });
    const socket = await openSession(harness);

    deliver(socket, checkCommand({ command_id: "c-clean" }));
    await flush();

    const messages = socket.messages();
    expect(messages.some((message) => message["type"] === "event")).toBe(false);
    expect(messages.find((message) => message["type"] === "condition")?.["passed"]).toBe(false);
  });

  it("a check that could not be evaluated sends no wall event, only the refusal", async () => {
    // C2/C3: the poll never judged the page, so there is no answer for a wall to explain — and a
    // check the session refuses must not pause a run on a page nobody read.
    const harness = makeHarness({
      checkCondition: async () => ({
        ok: false,
        reason: "the condition could not be evaluated: the last poll failed with a transient navigation error (3 of 3 polls)",
      }),
    });
    const socket = await openSession(harness);

    deliver(socket, checkCommand({ command_id: "c-unanswered" }));
    await flush();

    const messages = socket.messages();
    expect(messages.some((message) => message["type"] === "event")).toBe(false);
    expect(messages.some((message) => message["type"] === "condition")).toBe(false);
    const result = socket.lastResult();
    expect(result?.["status"]).toBe("refused");
    expect(String(result?.["error"])).toMatch(/could not be evaluated/);
  });

  it("the expiry is re-checked immediately before the input, not only before the step", async () => {
    // F1: the command is fresh when it is received and when the step starts, but the resolve,
    // guards and scroll let it expire before the input would be sent.
    let nowValue = 5_000;
    const harness = makeHarness({
      now: () => nowValue,
      runStep: async (_step, opts) => {
        nowValue = 500_000;
        opts.beforeInput();
        throw new Error("the input gate should have refused");
      },
    });
    const socket = await openSession(harness);

    deliver(socket, command({ command_id: "c1", type: "dispatch", expires_at: 100_000 }));
    await flush();

    const refused = socket.lastResult();
    expect(refused?.["status"]).toBe("refused");
    expect(String(refused?.["error"])).toMatch(/expired before any input/);
  });

  it("a socket close mid-dispatch aborts the command before its next input", async () => {
    // B3: the runner has written the command off as `lost`; the abandoned dispatch must not still
    // click, and a command arriving after the reconnect must not run concurrently on the same tab.
    const inputs: string[] = [];
    let gateway: (() => void) | null = null;
    const harness = makeHarness({
      runStep: async (_step, opts) => {
        gateway = opts.beforeInput;
        opts.beforeInput();
        inputs.push("first");
        // The socket drops now, while the step is still mid-action.
        await flush();
        opts.beforeInput();
        inputs.push("second");
        return { id: "step-1", action: "click", outcome: "done" };
      },
    });
    const socket = await openSession(harness);

    deliver(socket, command({ command_id: "c1" }));
    await flush();
    socket.emit("close");
    expect(gateway).not.toBeNull();
    await flush();

    // The second input never happened: the gate threw once the command was withdrawn.
    expect(inputs).toEqual(["first"]);
  });

  it("a new command is refused while an aborted one is still unwinding", async () => {
    // B3: two commands must never run concurrently on the same tab.
    const gate: { release: (() => void) | null } = { release: null };
    const harness = makeHarness({
      runStep: async (_step, opts) => {
        opts.sentInput();
        await new Promise<void>((done) => {
          gate.release = done;
        });
        return { id: "step-1", action: "click", outcome: "done" };
      },
    });
    const socket = await openSession(harness);

    deliver(socket, command({ command_id: "c1" }));
    await flush();
    socket.emit("close");

    // Reconnect while c1 is still unwinding, then send a new command.
    expect(harness.fireTimers(500)).toBe(1);
    const reconnected = harness.sockets[1];
    reconnected?.emit("open");
    await flush();
    deliver(reconnected as FakeSocket, command({ command_id: "c2" }));
    await flush();

    const refused = reconnected?.lastResult();
    expect(refused?.["command_id"]).toBe("c2");
    expect(refused?.["status"]).toBe("refused");
    expect(String(refused?.["error"])).toMatch(/unwinding/);

    gate.release?.();
    await flush();
  });

  it("does not report ready on a reconnect until the aborted command has unwound", async () => {
    // B3: "ready" is the popup's promise that a command would be taken. While an aborted command
    // is still unwinding, a new one is refused, so the session must not say ready until it settles.
    const gate: { release: (() => void) | null } = { release: null };
    const harness = makeHarness({
      runStep: async (_step, opts) => {
        opts.sentInput();
        await new Promise<void>((done) => {
          gate.release = done;
        });
        return { id: "step-1", action: "click", outcome: "done" };
      },
    });
    const socket = await openSession(harness);

    deliver(socket, command({ command_id: "c1" }));
    await flush();
    socket.emit("close");
    expect(harness.fireTimers(500)).toBe(1);
    harness.sockets[1]?.emit("open");
    await flush();
    expect(harness.states.at(-1)).toBe("executing");

    gate.release?.();
    await flush();
    expect(harness.states.at(-1)).toBe("ready");
  });

  it("an error after the first input is reported lost, not rejected", async () => {
    // F2: an exception after input is not a provable rejection; the runner must reconcile.
    const harness = makeHarness({
      runStep: async (_step, opts) => {
        opts.sentInput();
        throw new Error("the read-back failed after the click");
      },
    });
    const socket = await openSession(harness);

    deliver(socket, command({ command_id: "c1" }));
    await flush();

    const result = socket.lastResult();
    expect(result?.["status"]).toBe("lost");
  });

  it("a restore adopts the stored bound tab and reports it when the tab is gone", async () => {
    // B2: after a restart the stored tab must be re-adopted, never the active one.
    const stored: StoredSession = {
      hubUrl: "http://127.0.0.1:8000",
      sessionId: "sess-1",
      resumeToken: "rt-1",
      boundTabId: 42,
    };
    const harness = makeHarness({ storageValue: stored });
    expect(await harness.session.restore()).toBe(true);
    expect(harness.restoredTabs).toEqual([42]);

    const gone = makeHarness({ storageValue: stored, restoreBoundTab: async () => false });
    await gone.session.restore();
    const socket = gone.sockets[0];
    socket?.emit("open");
    await flush();
    const event = socket?.messages().find((message) => message["type"] === "event");
    expect(event?.["kind"]).toBe("tab_closed");
  });

  it("restore is idempotent: a second call opens no second socket", async () => {
    // F6: top level, onStartup and the popup all ask to restore.
    const stored: StoredSession = {
      hubUrl: "http://127.0.0.1:8000",
      sessionId: "sess-1",
      resumeToken: "rt-1",
      boundTabId: 7,
    };
    const harness = makeHarness({ storageValue: stored });
    await harness.session.restore();
    await harness.session.restore();
    await flush();
    expect(harness.sockets).toHaveLength(1);
  });

  it("a malformed command with an id is refused, not dropped silently", async () => {
    // F3: silence would leave the runner waiting for a reply that will never come.
    const harness = makeHarness();
    const socket = await openSession(harness);

    deliver(socket, {
      protocol: 1,
      type: "dispatch",
      command_id: "c-bad",
      step_ref: "s1",
      run_id: "run-1",
      run_record_id: "rec-1",
      lease_epoch: 1,
      expires_at: "not-a-number",
      step: { id: "step-1", action: "click" },
      commit: false,
      allowed_origins: [],
    });
    await flush();

    const refused = socket.lastResult();
    expect(refused?.["command_id"]).toBe("c-bad");
    expect(refused?.["status"]).toBe("refused");
  });

  it("a stale epoch is recorded even when the command is refused for another reason", async () => {
    const harness = makeHarness();
    const socket = await openSession(harness);

    // Well-formed, but expired: refused for the expiry, yet its lease epoch counts.
    deliver(socket, command({ command_id: "c1", lease_epoch: 7, expires_at: harness.clock.value - 1 }));
    await flush();
    expect(String(socket.lastResult()?.["error"])).toMatch(/expired/);

    // A later, unexpired command at a lower epoch is now stale.
    deliver(socket, command({ command_id: "c2", lease_epoch: 5 }));
    await flush();
    expect(socket.lastResult()?.["status"]).toBe("refused");
    expect(String(socket.lastResult()?.["error"])).toMatch(/stale lease/);
  });

  it("a debugger detach sends `detached` and aborts the in-flight command", async () => {
    // Item 6: the browser detached the debugger mid-command. The runner must learn at once, and the
    // abandoned dispatch must not still type or click.
    let gate: (() => void) | null = null;
    const pending = deferredStep();
    const harness = makeHarness({
      runStep: async (_step, opts) => {
        gate = opts.beforeInput;
        opts.beforeInput();
        return pending.promise;
      },
    });
    const socket = await openSession(harness);

    deliver(socket, command({ command_id: "c1" }));
    await flush();
    expect(harness.session.currentState).toBe("executing");

    // The browser detaches while the command is in flight.
    harness.session.notifyDebuggerDetached("target closed");

    const event = socket.messages().find((message) => message["type"] === "event");
    expect(event?.["kind"]).toBe("detached");
    expect(String(event?.["detail"])).toMatch(/debugger detached from the bound tab/);

    // The in-flight command is withdrawn: its next input gate refuses, so the command stops.
    expect(() => gate?.()).toThrow(/withdrawn/);

    pending.resolve({ id: "step-1", action: "click", outcome: "done" });
    await flush();
  });

  it("a debugger detach with no command in flight sends no event", async () => {
    // Item 7: an event belongs to a current command. With none, there is no record for it to land
    // on, so nothing is sent.
    const harness = makeHarness();
    const socket = await openSession(harness);

    deliver(socket, command({ command_id: "c1" }));
    await flush();
    expect(socket.lastResult()?.["status"]).toBe("ok");

    harness.session.notifyDebuggerDetached("target closed");
    await flush();

    expect(socket.messages().some((message) => message["type"] === "event")).toBe(false);
  });

  it("an aborted command's run-level event is not sent on the reconnected socket", async () => {
    // Item 7: the challenge belonged to the command the dropped socket abandoned. Sending it on the
    // reconnected socket would land it on the next record's run and pause the wrong thing.
    const gate: { release: (() => void) | null } = { release: null };
    const harness = makeHarness({
      runStep: async () => {
        await new Promise<void>((done) => {
          gate.release = done;
        });
        throw new ExecutorError("the page is showing a challenge", "s1", "park", "challenge");
      },
    });
    const socket = await openSession(harness);

    deliver(socket, command({ command_id: "c1" }));
    await flush();

    socket.emit("close");
    expect(harness.fireTimers(500)).toBe(1);
    const reconnected = harness.sockets[1];
    reconnected?.emit("open");
    await flush();
    expect(
      reconnected?.messages().some(
        (message) => message["type"] === "result" && message["status"] === "lost",
      ),
    ).toBe(true);

    // The abandoned step finally fails with its run-level event.
    gate.release?.();
    await flush();

    expect(reconnected?.messages().some((message) => message["type"] === "event")).toBe(false);
    // And no second, contradictory result either: the outcome was already reported lost.
    expect(reconnected?.messages().filter((message) => message["type"] === "result")).toHaveLength(1);
  });
});

describe("connect when nothing answers", () => {
  it("returns to idle when the hub cannot be reached, so Connect can be pressed again", async () => {
    // The popup connects by itself on open to http://127.0.0.1:8000. With nothing listening there,
    // fetch throws, and the session used to stay in "pairing" for good: the popup said
    // "Connecting to ProcessLens" forever and the Connect button stayed disabled.
    globalThis.fetch = (async () => {
      throw new TypeError("Failed to fetch");
    }) as unknown as typeof fetch;
    const harness = makeHarness();

    await expect(harness.session.connect("http://127.0.0.1:8000")).rejects.toThrow();

    expect(harness.states.at(-1)).toBe("idle");
    expect(harness.sockets).toHaveLength(0);
  });

  it("can connect on a second try after the first could not reach the hub", async () => {
    let calls = 0;
    globalThis.fetch = (async () => {
      calls += 1;
      if (calls === 1) throw new TypeError("Failed to fetch");
      return { status: 201, json: async () => ({ session_id: "sess-2", session_token: "tok-2" }) };
    }) as unknown as typeof fetch;
    const harness = makeHarness();

    await expect(harness.session.connect("http://127.0.0.1:8000")).rejects.toThrow();
    const socket = await openSession(harness);

    expect(socket).toBeDefined();
    expect(harness.states.at(-1)).toBe("ready");
  });
});


describe("a verification code from the run page", () => {
  it("reaches the code handler and is answered, without echoing the code", async () => {
    // PL-068: the session's message switch dropped `enter_code` silently, so the run page's code
    // never arrived and the API timed out with "not found".
    const typed: string[] = [];
    const harness = makeHarness({
      enterCode: async (code) => {
        typed.push(code);
        return { entered: true, reason: "submitted" };
      },
    });
    const socket = await openSession(harness);

    deliver(socket, {
      protocol: 1,
      type: "enter_code",
      command_id: "code-1",
      step_ref: "enter_code",
      run_id: "run-1",
      run_record_id: "",
      lease_epoch: 0,
      expires_at: 10_000_000,
      code: "246810",
    });
    await flush();

    expect(typed).toEqual(["246810"]);
    const result = socket.lastResult();
    expect(result?.["status"]).toBe("ok");
    expect(JSON.stringify(result)).not.toContain("246810");
  });
});

describe("a hosted ProcessLens and its team access code (PL-086)", () => {
  it("sends the code with the session call and keeps it for the version report", async () => {
    const seen: Array<Record<string, string>> = [];
    globalThis.fetch = (async (_url: string, init: RequestInit) => {
      seen.push(init.headers as Record<string, string>);
      return { status: 201, json: async () => ({ session_id: "sess-h", session_token: "tok-h" }) };
    }) as unknown as typeof fetch;
    const harness = makeHarness();

    await harness.session.connect("https://processlens-api.onrender.com", "team-code");

    expect(seen[0]?.["authorization"]).toBe("Bearer team-code");
    expect(harness.storagePeek()?.accessCode).toBe("team-code");
    expect(harness.sockets[0]?.url).toBe(
      "wss://processlens-api.onrender.com/api/v1/executor/ws?session=sess-h",
    );
  });

  it("sends no authorization header on a laptop", async () => {
    const seen: Array<Record<string, string>> = [];
    globalThis.fetch = (async (_url: string, init: RequestInit) => {
      seen.push(init.headers as Record<string, string>);
      return { status: 201, json: async () => ({ session_id: "sess-l", session_token: "tok-l" }) };
    }) as unknown as typeof fetch;
    const harness = makeHarness();

    await harness.session.connect("http://127.0.0.1:8000");

    expect(seen[0]).not.toHaveProperty("authorization");
  });

  it("says plainly that the code is missing or wrong, and goes back to idle", async () => {
    globalThis.fetch = (async () => ({ status: 401, json: async () => ({}) })) as unknown as typeof fetch;
    const harness = makeHarness();

    await expect(harness.session.connect("https://processlens-api.onrender.com", "")).rejects.toThrow(
      ACCESS_REFUSED,
    );
    expect(harness.states.at(-1)).toBe("idle");
  });
});

describe("the browser this session belongs to (PL-087)", () => {
  it("rides on the session call, so the hub knows which Chrome this is", async () => {
    const bodies: Array<Record<string, unknown>> = [];
    globalThis.fetch = (async (_url: string, init: RequestInit) => {
      bodies.push(JSON.parse(String(init.body)) as Record<string, unknown>);
      return { status: 201, json: async () => ({ session_id: "sess-b", session_token: "tok-b" }) };
    }) as unknown as typeof fetch;

    await makeHarness({ browserId: "browser-anna-0001" }).session.connect("http://127.0.0.1:8000");
    await makeHarness().session.connect("http://127.0.0.1:8000");

    expect(bodies[0]?.["browser_id"]).toBe("browser-anna-0001");
    expect(bodies[1]).not.toHaveProperty("browser_id");
  });
});

describe("Connect pressed while a session is already open (PL-092)", () => {
  function hubGives(sessionIds: string[]): void {
    let call = 0;
    globalThis.fetch = (async () => {
      const id = sessionIds[Math.min(call, sessionIds.length - 1)];
      call += 1;
      return { status: 201, json: async () => ({ session_id: id, session_token: `tok-${id}` }) };
    }) as unknown as typeof fetch;
  }

  it("closes the first socket before the second one opens", async () => {
    hubGives(["sess-1", "sess-2"]);
    const harness = makeHarness({ browserId: "browser-ben-00001" });
    const first = await openSession(harness);

    const second = await openSession(harness);

    expect(second).not.toBe(first);
    expect(first.closed).toBe(true);
    expect(second.closed).toBe(false);
    expect(second.url).toContain("session=sess-2");
    expect(harness.storagePeek()?.sessionId).toBe("sess-2");
  });

  it("never runs a command that arrives on the replaced socket, nor answers it on the new one", async () => {
    hubGives(["sess-1", "sess-2"]);
    const harness = makeHarness({ browserId: "browser-ben-00001" });
    const first = await openSession(harness);
    const second = await openSession(harness);

    deliver(first, command({ command_id: "c-stale" }));
    await flush();

    expect(harness.runStepCalls).toEqual([]);
    expect(second.messages().filter((message) => message["command_id"] === "c-stale")).toEqual([]);
    expect(first.messages().filter((message) => message["command_id"] === "c-stale")).toEqual([]);
  });

  it("still runs a command that arrives on the new socket", async () => {
    hubGives(["sess-1", "sess-2"]);
    const harness = makeHarness({ browserId: "browser-ben-00001" });
    await openSession(harness);
    const second = await openSession(harness);

    deliver(second, command({ command_id: "c-new" }));
    await flush();

    expect(harness.runStepCalls).toHaveLength(1);
    expect(second.lastResult()?.["command_id"]).toBe("c-new");
  });

  it("the replaced socket's close is not a drop: nothing reconnects to the old session", async () => {
    hubGives(["sess-1", "sess-2"]);
    const harness = makeHarness({ browserId: "browser-ben-00001" });
    const first = await openSession(harness);
    await openSession(harness);
    const opened = harness.sockets.length;

    first.emit("close");
    for (const ms of [500, 1000, 2000, 4000]) harness.fireTimers(ms);

    expect(harness.sockets).toHaveLength(opened);
    expect(harness.states.at(-1)).toBe("ready");
  });

  it("cancels a pending reconnect, so the old session is never dialled again", async () => {
    hubGives(["sess-1", "sess-2"]);
    const harness = makeHarness({ browserId: "browser-ben-00001" });
    const first = await openSession(harness);
    first.emit("close"); // a real drop: a reconnect to sess-1 is now scheduled

    const second = await openSession(harness);
    for (const ms of [500, 1000, 2000, 4000]) harness.fireTimers(ms);

    expect(harness.sockets.filter((socket) => socket.url.includes("session=sess-1"))).toHaveLength(1);
    expect(harness.sockets.at(-1)).toBe(second);
  });

  it("aborts a command still running on the replaced socket", async () => {
    hubGives(["sess-1", "sess-2"]);
    const pending = deferredStep();
    let aborted = false;
    const harness = makeHarness({
      browserId: "browser-ben-00001",
      runStep: async (_step, opts) => {
        opts.beforeInput();
        opts.sentInput();
        const report = await pending.promise;
        try {
          opts.beforeInput();
        } catch {
          aborted = true;
        }
        return report;
      },
    });
    const first = await openSession(harness);
    deliver(first, command({ command_id: "c-running" }));
    await flush();

    await openSession(harness);
    pending.resolve({ id: "step-1", action: "click", outcome: "done", rung: 0, by: "role" });
    await flush();

    expect(aborted).toBe(true);
  });
});

describe("two Connects in flight at once (PL-093)", () => {
  // Each hub reply is held until the test releases it, so the test decides which Connect hears
  // back first -- the overlap the popup's auto-connect and a click produce over the internet.
  function heldHub(): {
    release: (sessionId: string) => void;
    releaseNewest: (sessionId: string) => void;
    calls: () => number;
  } {
    const waiting: Array<(sessionId: string) => void> = [];
    let calls = 0;
    globalThis.fetch = (async () => {
      calls += 1;
      const sessionId = await new Promise<string>((resolve) => waiting.push(resolve));
      return {
        status: 201,
        json: async () => ({ session_id: sessionId, session_token: `tok-${sessionId}` }),
      };
    }) as unknown as typeof fetch;
    return {
      release: (sessionId) => {
        const next = waiting.shift();
        if (!next) throw new Error("no Connect is waiting on the hub");
        next(sessionId);
      },
      releaseNewest: (sessionId) => {
        const next = waiting.pop();
        if (!next) throw new Error("no Connect is waiting on the hub");
        next(sessionId);
      },
      calls: () => calls,
    };
  }

  async function waitForCalls(hub: { calls: () => number }, count: number): Promise<void> {
    for (let tries = 0; tries < 50 && hub.calls() < count; tries += 1) await flush();
    expect(hub.calls()).toBe(count);
  }

  it("the older Connect opens nothing when its reply comes back first", async () => {
    const hub = heldHub();
    const harness = makeHarness({ browserId: "browser-ben-00001" });

    const older = harness.session.connect("http://127.0.0.1:8000");
    await waitForCalls(hub, 1);
    const newer = harness.session.connect("http://127.0.0.1:8000");
    await waitForCalls(hub, 2);

    hub.release("sess-old");
    await older;
    hub.release("sess-new");
    await newer;

    expect(harness.sockets.map((socket) => socket.url)).toEqual([
      expect.stringContaining("session=sess-new"),
    ]);
    expect(harness.storagePeek()?.sessionId).toBe("sess-new");
  });

  it("the older Connect opens nothing when its reply comes back last", async () => {
    const hub = heldHub();
    const harness = makeHarness({ browserId: "browser-ben-00001" });

    const older = harness.session.connect("http://127.0.0.1:8000");
    await waitForCalls(hub, 1);
    const newer = harness.session.connect("http://127.0.0.1:8000");
    await waitForCalls(hub, 2);

    hub.releaseNewest("sess-new");
    await newer;
    harness.sockets.at(-1)?.emit("open");
    hub.release("sess-old");
    await older;

    expect(harness.sockets.map((socket) => socket.url)).toEqual([
      expect.stringContaining("session=sess-new"),
    ]);
    expect(harness.sockets[0]?.closed).toBe(false);
    expect(harness.storagePeek()?.sessionId).toBe("sess-new");
    expect(harness.states.at(-1)).toBe("ready");
  });

  it("a Disconnect while a Connect waits on the hub leaves nothing open", async () => {
    const hub = heldHub();
    const harness = makeHarness({ browserId: "browser-ben-00001" });

    const pending = harness.session.connect("http://127.0.0.1:8000");
    await waitForCalls(hub, 1);
    await harness.session.disconnect();
    hub.release("sess-late");
    await pending;

    expect(harness.sockets).toEqual([]);
    expect(harness.storagePeek()).toBeNull();
    expect(harness.states.at(-1)).toBe("idle");
  });

  it("a Connect that fails after being overtaken does not knock the newer one back to idle", async () => {
    let calls = 0;
    let failFirst!: (error: Error) => void;
    globalThis.fetch = (async () => {
      calls += 1;
      if (calls === 1) {
        return new Promise((_resolve, reject) => {
          failFirst = reject;
        });
      }
      return { status: 201, json: async () => ({ session_id: "sess-new", session_token: "tok" }) };
    }) as unknown as typeof fetch;
    const harness = makeHarness({ browserId: "browser-ben-00001" });

    const older = harness.session.connect("http://127.0.0.1:8000").catch(() => undefined);
    for (let tries = 0; tries < 50 && calls < 1; tries += 1) await flush();
    await harness.session.connect("http://127.0.0.1:8000");
    harness.sockets.at(-1)?.emit("open");
    failFirst(new TypeError("Failed to fetch"));
    await older;

    expect(harness.states.at(-1)).toBe("ready");
    expect(harness.sockets).toHaveLength(1);
  });
});
