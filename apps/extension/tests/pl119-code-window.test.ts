// PL-119 item 2: when the session never saw the code page, `since` is the moment the enter-code
// command arrived less ten seconds -- not three minutes. And item 1: nothing about a typed code
// survives in storage, a log line, a step event or an answer.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { typeEmailedCode } from "../src/background/code-entry";
import {
  CommandSession,
  type SessionDeps,
  type StoredSession,
  type WebSocketLike,
} from "../src/background/command-session";
import { digestCode, SaltedCodeMemory } from "../src/background/used-codes";
import type { StepReport } from "../src/executor/types";
import type { MailMessage } from "../src/shared/email-code";

const SITE = "https://app.goodlabs.example/login";
const FROM = "Goodlabs <team@goodlabs.example>";

function mail(digits: string, at: number): MailMessage {
  return {
    from: FROM,
    subject: "Your Login Code",
    snippet: `Your login code is ${digits}. It expires in 10 minutes.`,
    receivedAt: at,
  };
}

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

  lastResult(): Record<string, unknown> | undefined {
    return [...this.sent]
      .map((line) => JSON.parse(line) as Record<string, unknown>)
      .reverse()
      .find((message) => message["type"] === "result");
  }
}

interface Harness {
  session: CommandSession;
  sockets: FakeSocket[];
  storage: () => StoredSession | null;
}

function makeSession(options: {
  now: () => number;
  codeFromEmail?: SessionDeps["codeFromEmail"];
}): Harness {
  const sockets: FakeSocket[] = [];
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
    now: options.now,
    setInterval: () => () => undefined,
    bindActiveTab: async () => 7,
    runStep: async (_step, opts): Promise<StepReport> => {
      opts.beforeInput();
      opts.sentInput();
      return { id: "step-1", action: "click", outcome: "done", rung: 0, by: "role" };
    },
    checkCondition: async () => ({ ok: true, passed: true }),
    armAbandonGuard: async () => undefined,
    disarmAbandonGuard: async () => [],
    restoreBoundTab: async () => true,
    onState: () => undefined,
    ...(options.codeFromEmail ? { codeFromEmail: options.codeFromEmail } : {}),
  };
  return { session: new CommandSession(deps), sockets, storage: () => stored };
}

const originalFetch = globalThis.fetch;

beforeEach(() => {
  globalThis.fetch = (async () => ({
    status: 201,
    json: async () => ({ session_id: "sess-119", session_token: "tok-119" }),
  })) as unknown as typeof fetch;
});

afterEach(() => {
  globalThis.fetch = originalFetch;
});

async function flush(): Promise<void> {
  for (let round = 0; round < 5; round += 1) {
    await new Promise<void>((resolve) => setTimeout(resolve, 0));
  }
}

async function openSession(harness: Harness): Promise<FakeSocket> {
  await harness.session.connect("http://127.0.0.1:8000");
  const socket = harness.sockets[harness.sockets.length - 1];
  if (!socket) throw new Error("the session did not open a socket");
  socket.emit("open");
  return socket;
}

function enterCodeCommand(expiresAt: number, lookMs: number): Record<string, unknown> {
  return {
    protocol: 1,
    type: "enter_code",
    command_id: "code-119",
    step_ref: "enter_code",
    run_id: "run-119",
    run_record_id: "",
    lease_epoch: 0,
    expires_at: expiresAt,
    from_email: true,
    remember: false,
    look_ms: lookMs,
  };
}

function deliver(socket: FakeSocket, message: unknown): void {
  socket.emit("message", { data: JSON.stringify(message) });
}

describe("a session that never saw the code page", () => {
  it("looks back ten seconds, not three minutes", async () => {
    const now = Date.parse("2026-10-28T01:18:45Z");
    let seen: number | undefined;
    const harness = makeSession({
      now: () => now,
      codeFromEmail: async (options) => {
        seen = options.since;
        return { entered: false, reason: "none" };
      },
    });
    const socket = await openSession(harness);

    deliver(socket, enterCodeCommand(now + 60_000, 1_000));
    await flush();

    expect(seen).toBe(now - 10_000);
  });

  it("does not use an email that arrived two minutes earlier", async () => {
    const start = Date.now();
    let time = start;
    const used = new SaltedCodeMemory({ salt: "window-salt", now: () => time });
    const typed: string[] = [];
    const harness = makeSession({
      now: () => time,
      codeFromEmail: async ({ since, lookMs, stop }) =>
        typeEmailedCode(
          {
            read: async () =>
              [mail("111111", start - 2 * 60_000)].filter(
                (message) => message.receivedAt <= time,
              ),
            now: () => time,
            sleep: async (ms) => {
              time += ms;
            },
            enter: async (code) => {
              typed.push(code);
              return { entered: true, reason: "submitted", cleared: true };
            },
            used,
          },
          { since, site: SITE, lookMs, stop },
        ),
    });
    const socket = await openSession(harness);

    deliver(socket, enterCodeCommand(start + 60_000, 1_000));
    await flush();

    expect(typed).toEqual([]);
    expect(socket.lastResult()?.["observed"]).toEqual({ entered: false, reason: "none" });
  });
});

describe("a typed code is kept nowhere", () => {
  it("puts no code and no hash of one in storage, a log line, an event or an answer", async () => {
    const start = Date.now();
    let time = start;
    const salt = "nothing-kept-salt";
    const used = new SaltedCodeMemory({ salt, now: () => time });
    const typed: string[] = [];
    const spies = (["log", "info", "warn", "error", "debug"] as const).map((level) =>
      vi.spyOn(console, level).mockImplementation(() => undefined),
    );
    try {
      const harness = makeSession({
        now: () => time,
        codeFromEmail: async ({ since, lookMs, stop }) =>
          typeEmailedCode(
            {
              read: async () =>
                [mail("111111", start + 1_000)].filter((message) => message.receivedAt <= time),
              now: () => time,
              sleep: async (ms) => {
                time += ms;
              },
              enter: async (code) => {
                typed.push(code);
                return { entered: true, reason: "submitted", cleared: true };
              },
              used,
            },
            { since, site: SITE, lookMs, stop },
          ),
      });
      const socket = await openSession(harness);

      deliver(socket, enterCodeCommand(start + 60_000, 10_000));
      await flush();

      expect(typed).toEqual(["111111"]);
      const digest = await digestCode(salt, "111111");
      const written = [socket.sent.join("\n"), JSON.stringify(harness.storage())];
      for (const text of written) {
        expect(text).not.toContain("111111");
        expect(text).not.toContain(digest);
      }
      for (const spy of spies) {
        const lines = spy.mock.calls.map((args) => args.map(String).join(" ")).join("\n");
        expect(lines).not.toContain("111111");
        expect(lines).not.toContain(digest);
      }
    } finally {
      for (const spy of spies) spy.mockRestore();
    }
  });
});
