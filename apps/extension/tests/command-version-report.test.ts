// PL-026 requirement 3: the extension's one addition to the command session.
//
// The session reports its manifest version to the API every time it reaches `ready`, and only then:
// not while pairing, not while connecting, not twice for one arrival, and not in a way that lets a
// failed report disturb the socket, the bound tab or the command it is running. The version itself
// is read by the service worker's implementation of the dependency, so this test drives the session
// through the same injected seam every other session test uses and counts the calls.
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import {
  CommandSession,
  type CheckAnswer,
  type SessionDeps,
  type SessionState,
  type StoredSession,
  type WebSocketLike,
} from "../src/background/command-session";
import type { StepReport } from "../src/executor/types";

/** A socket shaped exactly like the interface, so a plain object satisfies it. */
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

  close(): void {}

  addEventListener(type: string, listener: (event: unknown) => void): void {
    const list = this.listeners.get(type) ?? [];
    list.push(listener);
    this.listeners.set(type, list);
  }

  emit(type: string, event: unknown = {}): void {
    for (const listener of this.listeners.get(type) ?? []) listener(event);
  }
}

interface Harness {
  session: CommandSession;
  sockets: FakeSocket[];
  states: SessionState[];
  reports: number[];
  failReports: { value: boolean };
}

function makeHarness(): Harness {
  const sockets: FakeSocket[] = [];
  const states: SessionState[] = [];
  const reports: number[] = [];
  const failReports = { value: false };
  let stored: StoredSession | null = null;

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
    now: () => 1_000,
    setInterval: () => () => undefined,
    bindActiveTab: async () => 7,
    restoreBoundTab: async () => true,
    runStep: async (step, opts): Promise<StepReport> => {
      opts.beforeInput();
      opts.sentInput();
      return { id: "step-1", action: "click", outcome: "done", rung: 0, by: "role" };
    },
    checkCondition: async (): Promise<CheckAnswer> => ({ ok: true, passed: true }),
    reportVersion: () => {
      reports.push(reports.length + 1);
      if (failReports.value) throw new Error("the report could not be sent");
    },
    // PL-027: the abandon guard's two deps. These tests never arm it; the guard has its own.
    armAbandonGuard: async () => undefined,
    disarmAbandonGuard: async () => [],
    onState: (state) => states.push(state),
  };

  return { session: new CommandSession(deps), sockets, states, reports, failReports };
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

async function open(harness: Harness): Promise<FakeSocket> {
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

describe("the extension reports its version", () => {
  it("reports once when the session first reaches ready", async () => {
    const harness = makeHarness();

    await open(harness);

    expect(harness.session.currentState).toBe("ready");
    expect(harness.reports).toHaveLength(1);
  });

  it("says nothing while it is still pairing or connecting", async () => {
    const harness = makeHarness();

    await harness.session.connect("http://127.0.0.1:8000");

    expect(harness.session.currentState).toBe("connecting");
    expect(harness.reports).toHaveLength(0);

    // The socket opening is the moment it becomes ready, and the only moment it reports.
    harness.sockets[0]?.emit("open");

    expect(harness.reports).toHaveLength(1);
  });

  it("reports again when a command finishes and the session is ready a second time", async () => {
    const harness = makeHarness();
    const socket = await open(harness);
    expect(harness.reports).toHaveLength(1);

    socket.emit("message", { data: JSON.stringify(command()) });
    await flush();

    expect(harness.states.filter((state) => state === "executing")).toHaveLength(1);
    expect(harness.session.currentState).toBe("ready");
    expect(harness.reports).toHaveLength(2);
  });

  it("does not report twice for one arrival", async () => {
    const harness = makeHarness();
    const socket = await open(harness);

    // A ping and a peer_gone change nothing about the state, so nothing is reported for them.
    socket.emit("message", { data: JSON.stringify({ protocol: 1, type: "ping" }) });
    socket.emit("message", { data: JSON.stringify({ protocol: 1, type: "peer_gone" }) });
    await flush();

    expect(harness.session.currentState).toBe("ready");
    expect(harness.reports).toHaveLength(1);
  });

  it("carries on when the report itself fails", async () => {
    const harness = makeHarness();
    harness.failReports.value = true;

    await open(harness);

    // A failed report is not a failed session: the state machine is untouched, and a command still
    // runs on it.
    expect(harness.session.currentState).toBe("ready");
    const socket = harness.sockets[0];
    socket?.emit("message", { data: JSON.stringify(command()) });
    await flush();

    expect(harness.session.currentState).toBe("ready");
    expect(socket?.sent.some((line) => line.includes('"result"'))).toBe(true);
    expect(harness.reports).toHaveLength(2);
  });
});
