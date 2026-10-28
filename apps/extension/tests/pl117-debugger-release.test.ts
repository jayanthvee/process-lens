// PL-117 item 5: while a run waits, the extension lets go of the tab's debugger.
//
// The runner sends one command at a time; when it pauses (a parked row, an approval, a canary, the
// code window, the daily cap) or ends, no next command arrives and Chrome's "started debugging this
// browser" bar stayed on every window. The session now lets go after a short quiet spell and holds
// the debugger again before the next command runs. Letting go is not a detach the person caused, so
// it must never be reported as `detached` or abort anything.
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import {
  CommandSession,
  DEBUGGER_IDLE_RELEASE_MS,
  type SessionDeps,
  type SessionState,
  type StoredSession,
  type WebSocketLike,
} from "../src/background/command-session";
import type { StepReport } from "../src/executor/types";

const HERE = dirname(fileURLToPath(import.meta.url));

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

class FakeSocket implements WebSocketLike {
  readonly sent: string[] = [];
  private readonly listeners = new Map<string, Array<(event: unknown) => void>>();

  constructor(
    readonly url: string,
    readonly protocols: string[],
  ) {}

  send(data: string): void {
    this.sent.push(data);
  }

  close(): void {
    // Nothing to close in a fake.
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
}

interface Timeout {
  fn: () => void;
  ms: number;
  cancelled: boolean;
  fired: boolean;
}

interface Harness {
  session: CommandSession;
  socket(): FakeSocket;
  events: string[];
  states: SessionState[];
  timeouts: Timeout[];
  fireIdle(): Promise<void>;
}

function makeHarness(): Harness {
  const sockets: FakeSocket[] = [];
  const events: string[] = [];
  const states: SessionState[] = [];
  const timeouts: Timeout[] = [];
  let stored: StoredSession | null = null;

  const deps: SessionDeps = {
    createSocket: (url, protocols) => {
      const socket = new FakeSocket(url, protocols);
      sockets.push(socket);
      return socket;
    },
    storage: {
      async get() {
        return stored;
      },
      async set(value) {
        stored = value;
      },
    },
    now: () => 1_000,
    setInterval: () => () => undefined,
    setTimeout: (fn, ms) => {
      const timeout: Timeout = { fn, ms, cancelled: false, fired: false };
      timeouts.push(timeout);
      return () => {
        timeout.cancelled = true;
      };
    },
    bindActiveTab: async () => 7,
    runStep: async (_step, opts) => {
      events.push("step");
      opts.beforeInput();
      opts.sentInput();
      return { id: "step-1", action: "click", outcome: "done" } as StepReport;
    },
    checkCondition: async () => ({ ok: true, passed: true }),
    armAbandonGuard: async () => undefined,
    disarmAbandonGuard: async () => [],
    restoreBoundTab: async () => true,
    reportVersion: () => undefined,
    wakeDebugger: async () => {
      events.push("wake");
    },
    releaseDebugger: async () => {
      events.push("release");
    },
    onState: (state) => states.push(state),
  };

  const session = new CommandSession(deps);
  return {
    session,
    socket: () => {
      const socket = sockets[0];
      if (!socket) throw new Error("the session did not open a socket");
      return socket;
    },
    events,
    states,
    timeouts,
    async fireIdle() {
      for (const timeout of timeouts) {
        if (timeout.ms === DEBUGGER_IDLE_RELEASE_MS && !timeout.cancelled && !timeout.fired) {
          timeout.fired = true;
          timeout.fn();
        }
      }
      await flush();
    },
  };
}

async function flush(): Promise<void> {
  await new Promise<void>((done) => setTimeout(done, 0));
  await new Promise<void>((done) => setTimeout(done, 0));
}

async function openSession(harness: Harness): Promise<FakeSocket> {
  await harness.session.connect("http://127.0.0.1:8000");
  const socket = harness.socket();
  socket.emit("open");
  return socket;
}

function command(id: string): Record<string, unknown> {
  return {
    protocol: 1,
    type: "dispatch",
    command_id: id,
    step_ref: "s1",
    run_id: "run-1",
    run_record_id: "rec-1",
    lease_epoch: 1,
    expires_at: 10_000_000,
    step: { id: "step-1", action: "click", targets: [{ by: "role", role: "button", name: "Save" }] },
    commit: false,
    allowed_origins: [],
  };
}

function deliver(socket: FakeSocket, message: unknown): void {
  socket.emit("message", { data: JSON.stringify(message) });
}

describe("letting go of the debugger while a run waits (PL-117)", () => {
  it("releases it exactly once after the quiet spell, and never reports a detach", async () => {
    const harness = makeHarness();
    const socket = await openSession(harness);

    deliver(socket, command("c1"));
    await flush();
    expect(harness.events).toEqual(["wake", "step"]);
    expect(harness.events.filter((event) => event === "release")).toHaveLength(0);

    await harness.fireIdle();
    expect(harness.events.filter((event) => event === "release")).toHaveLength(1);

    // The one-shot timer does not fire again while the run keeps waiting.
    await harness.fireIdle();
    expect(harness.events.filter((event) => event === "release")).toHaveLength(1);
    // Our own release is not the person pressing Cancel: no run-level event is sent.
    expect(socket.messages().some((message) => message["type"] === "event")).toBe(false);
  });

  it("holds the debugger again before the next step when the run resumes", async () => {
    const harness = makeHarness();
    const socket = await openSession(harness);

    deliver(socket, command("c1"));
    await flush();
    await harness.fireIdle();
    expect(harness.events).toEqual(["wake", "step", "release"]);

    deliver(socket, command("c2"));
    await flush();

    expect(harness.events).toEqual(["wake", "step", "release", "wake", "step"]);
    const results = socket.messages().filter((message) => message["type"] === "result");
    expect(results.map((message) => message["command_id"])).toEqual(["c1", "c2"]);
    expect(results.every((message) => message["status"] === "ok")).toBe(true);
    // The resumed step ran, so letting go did not abort the run.
    expect(socket.messages().some((message) => message["type"] === "event")).toBe(false);
  });

  it("keeps the debugger when the next command arrives inside the quiet spell", async () => {
    const harness = makeHarness();
    const socket = await openSession(harness);

    deliver(socket, command("c1"));
    await flush();
    expect(harness.timeouts).toHaveLength(1);

    deliver(socket, command("c2"));
    await flush();

    // The first quiet spell was cancelled by the next command; the second starts after it.
    expect(harness.timeouts[0]?.cancelled).toBe(true);
    expect(harness.events.filter((event) => event === "release")).toHaveLength(0);
  });

  it("removes the detach watcher before its own detach, so it is never taken for a Cancel", () => {
    const worker = readFileSync(resolve(HERE, "../src/background/service-worker.ts"), "utf8");
    const start = worker.indexOf("async function releaseDebugger()");
    const end = worker.indexOf("async function runCommandStep(", start);
    expect(start).toBeGreaterThanOrEqual(0);
    expect(end).toBeGreaterThan(start);
    const body = worker.slice(start, end);
    const watchAt = body.indexOf("stopCommandDetachWatch();");
    const detachAt = body.indexOf("detachDebugger(");
    expect(watchAt).toBeGreaterThanOrEqual(0);
    expect(detachAt).toBeGreaterThan(watchAt);
  });
});
