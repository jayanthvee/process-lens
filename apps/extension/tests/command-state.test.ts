// The command session's state machine (PL-014), driven entirely through its
// injected dependencies: a fake socket, a fake storage area, a fake clock, and a
// fake timer seam. No real Chrome, no real socket, no real network.
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import {
  CommandSession,
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
  ) => Promise<{ ok: true; passed: boolean } | { ok: false; reason: string }>;
  restoreBoundTab?: (tabId: number) => Promise<boolean>;
  now?: () => number;
  storageValue?: StoredSession | null;
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
    setInterval: (fn, ms) => {
      const timer: Timer = { fn, ms, cancelled: false };
      timers.push(timer);
      return () => {
        timer.cancelled = true;
      };
    },
    bindActiveTab: async () => 7,
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
});
