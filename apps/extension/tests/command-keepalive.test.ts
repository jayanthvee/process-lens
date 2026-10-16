// The command session's keepalive (PL-014): while it is connected, it pings the
// hub every twenty seconds through the injected timer seam. Driven with a fake
// socket and a fake timer, so no real socket and no real wait is involved.
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import {
  CommandSession,
  type SessionDeps,
  type SessionState,
  type StoredSession,
  type WebSocketLike,
} from "../src/background/command-session";

const KEEPALIVE_MS = 20_000;

class FakeSocket implements WebSocketLike {
  readonly sent: string[] = [];
  private readonly listeners = new Map<string, Array<(event: unknown) => void>>();

  send(data: string): void {
    this.sent.push(data);
  }

  close(): void {
    // Nothing to release in a fake socket.
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

describe("the command session keepalive", () => {
  it("a ping is sent every twenty seconds", async () => {
    const sockets: FakeSocket[] = [];
    const timers: Array<{ fn: () => void; ms: number }> = [];
    let stored: StoredSession | null = null;
    const states: SessionState[] = [];

    const deps: SessionDeps = {
      createSocket: () => {
        const socket = new FakeSocket();
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
      setInterval: (fn, ms) => {
        const timer = { fn, ms };
        timers.push(timer);
        return () => {
          // A cancel is not needed for this test; the timer fires once.
        };
      },
      bindActiveTab: async () => 7,
      runStep: async () => ({ id: "step-1", action: "click", outcome: "done" }),
      checkCondition: async () => ({ ok: true, passed: true }),
      restoreBoundTab: async () => true,
      // PL-027: the abandon guard's two deps. These tests never arm it; the guard has its own.
      armAbandonGuard: async () => undefined,
      disarmAbandonGuard: async () => [],
      onState: (state) => states.push(state),
    };

    const session = new CommandSession(deps);
    await session.connect("http://127.0.0.1:8000");
    const socket = sockets[0];
    expect(socket).toBeDefined();
    socket?.emit("open");
    expect(session.currentState).toBe("ready");

    const keepalive = timers.find((timer) => timer.ms === KEEPALIVE_MS);
    expect(keepalive).toBeDefined();
    keepalive?.fn();

    const pings = socket?.messages().filter((message) => message["type"] === "ping") ?? [];
    expect(pings).toHaveLength(1);
    expect(pings[0]?.["protocol"]).toBe(1);
  });
});
