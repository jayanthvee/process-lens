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
import type { StepReport } from "../src/executor/types";

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
  storagePeek(): StoredSession | null;
  fireTimers(ms: number): number;
}

interface HarnessOptions {
  runStep?: (
    step: unknown,
    opts: { sentInput: () => void; allowedOrigins?: string[] },
  ) => Promise<StepReport>;
  now?: () => number;
  storageValue?: StoredSession | null;
}

function makeHarness(options: HarnessOptions = {}): Harness {
  const sockets: FakeSocket[] = [];
  const states: SessionState[] = [];
  const timers: Timer[] = [];
  const clock = { value: 1_000 };
  const runStepCalls: unknown[] = [];
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
    runStep: async (step, opts) => {
      runStepCalls.push(step);
      if (options.runStep) return options.runStep(step, opts);
      opts.sentInput();
      return { id: "step-1", action: "click", outcome: "done", rung: 0, by: "role" };
    },
    checkCondition: async () => true,
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
