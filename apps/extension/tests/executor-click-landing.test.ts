// PL-017b: a click lands only on the element the guards approved.
//
// R1 (the target must hold still across two animation frames), R2 (the point must hold its hit test
// to the target as the LAST check before the press), and R3 (a one-shot capture listener checks where
// the press actually landed). Each refusal sends no press at all -- the hover move that precedes the
// final hit test is not a press, per T1's ruling; a press that lands elsewhere throws only AFTER
// input was sent, so the command session reports it `lost` (the runner reconciles), never `rejected`
// (F2).
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { dispatchCommandStep } from "../src/background/command-step";
import type { CdpTransport } from "../src/executor/cdp";
import {
  CommandSession,
  type CheckAnswer,
  type SessionDeps,
  type SessionState,
  type StoredSession,
  type WebSocketLike,
} from "../src/background/command-session";
import { dispatchClick, resetInputClock } from "../src/executor/dispatch";
import { resolveTargetInPage } from "../src/executor/ladder";
import type { StepReport } from "../src/executor/types";
import { fakeCdp } from "./helpers/fake-cdp";
import { installLayout } from "./helpers/layout";

let restoreLayout: (() => void) | null = null;

function stamp(html: string, token: string): void {
  document.body.innerHTML = html;
  resolveTargetInPage({
    targets: [{ by: "css", selector: "#save" }],
    within: null,
    token,
  });
}

beforeEach(() => {
  document.body.innerHTML = "";
  // The post-input quiet window is session state: input in one test must not slow the next one.
  resetInputClock();
});

afterEach(() => {
  restoreLayout?.();
  restoreLayout = null;
});

describe("R1 — the target must stop moving before the click", () => {
  it("clicks a box that moves once, then settles, at the SETTLED point", async () => {
    stamp("<button id='save'>Save</button>", "c1");
    // The box keeps moving for the first several measures, then holds at y=100. A click that did
    // not wait for it to stop would measure mid-motion and press there; the settled point is 112.
    restoreLayout = installLayout({
      rectAt: (call) => ({ left: 10, top: call < 6 ? 10 + call * 10 : 100, width: 120, height: 24 }),
    });
    const { cdp, callsTo } = fakeCdp();

    const box = await dispatchClick(cdp, "c1", undefined, undefined, 500);

    expect(box).toMatchObject({ ok: true, x: 70, y: 112 });
    const mouse = callsTo("Input.dispatchMouseEvent").map((call) => call.params);
    expect(mouse.map((params) => params["type"])).toEqual([
      "mouseMoved",
      "mousePressed",
      "mouseReleased",
    ]);
    expect(mouse[1]).toMatchObject({ x: 70, y: 112, button: "left", clickCount: 1 });
    expect(mouse[2]).toMatchObject({ x: 70, y: 112 });
  });

  it("parks with zero Input.* calls when the box never settles", async () => {
    stamp("<button id='save'>Save</button>", "c1");
    // An alternation: never the same box across two consecutive frames.
    restoreLayout = installLayout({
      rectAt: (call) => ({ left: 10, top: call % 2 === 0 ? 10 : 60, width: 120, height: 24 }),
    });
    const { cdp, callsTo } = fakeCdp();

    await expect(dispatchClick(cdp, "c1", undefined, undefined, 60)).rejects.toThrow(
      /kept moving/,
    );
    expect(callsTo("Input.dispatchMouseEvent")).toHaveLength(0);
    expect(callsTo("Input.insertText")).toHaveLength(0);
    expect(callsTo("Input.dispatchKeyEvent")).toHaveLength(0);
  });
});

describe("R2 — the point is hit-tested again before the press", () => {
  it("parks with no press when the point is covered by an overlay", async () => {
    stamp("<button id='save'>Save</button>", "c1");
    restoreLayout = installLayout({ hit: "other" });
    const { cdp, callsTo } = fakeCdp();

    await expect(dispatchClick(cdp, "c1", undefined, undefined, 60)).rejects.toThrow(/covered by/);
    // The final check refuses; the move that precedes it is the only mouse event -- never a press.
    const mouseTypes = callsTo("Input.dispatchMouseEvent").map((call) => call.params["type"]);
    expect(mouseTypes).not.toContain("mousePressed");
    expect(mouseTypes).not.toContain("mouseReleased");
  });

  it("re-measures and clicks at the target's new position when an overlay opens under the old point", async () => {
    stamp("<button id='save'>Save</button>", "c1");
    // The page is still for the first two measures (y=10). Then the in-flow listbox opens under the
    // old point — a hit test there now returns a stranger — and Add is pushed down to y=60.
    restoreLayout = installLayout({
      rectAt: (call) => ({ left: 10, top: call <= 2 ? 10 : 60, width: 120, height: 24 }),
      hitAt: (_call, _x, y) => (y < 50 ? document.createElement("div") : document.querySelector("[data-pl-target]")),
    });
    const { cdp, callsTo } = fakeCdp();

    const box = await dispatchClick(cdp, "c1", undefined, undefined, 300);

    expect(box).toMatchObject({ ok: true, x: 70, y: 72 });
    const pressed = callsTo("Input.dispatchMouseEvent").filter((call) => call.params["type"] === "mousePressed");
    expect(pressed).toHaveLength(1);
    expect(pressed[0]?.params).toMatchObject({ x: 70, y: 72 });
  });
});

describe("R3 — where the press actually landed", () => {
  it("succeeds when the press lands on the target", async () => {
    stamp("<button id='save'>Save</button>", "c1");
    restoreLayout = installLayout();
    const { cdp, callsTo } = fakeCdp();

    const box = await dispatchClick(cdp, "c1", undefined, undefined, 200);

    expect(box.ok).toBe(true);
    expect(callsTo("Input.dispatchMouseEvent")).toHaveLength(3);
  });

  it("parks when a non-commit click lands elsewhere, only after input was sent", async () => {
    stamp("<button id='save'>Save</button>", "c1");
    restoreLayout = installLayout();
    // The press lands on a different element than the one the point resolved to.
    const { cdp, callsTo } = fakeCdp(undefined, {
      pressAt: (_params, at) => (at === document.querySelector("[data-pl-target]") ? stranger() : at),
    });
    let firstInput = false;
    const hooks = { onFirstInput: () => { firstInput = true; } };

    await expect(dispatchClick(cdp, "c1", undefined, hooks, 200)).rejects.toThrow(
      /landed on a different element/,
    );
    // The press WAS sent: this is a lost outcome, which is why the error carries the park
    // disposition but the command session will classify it `lost`, not `not_dispatched`.
    expect(callsTo("Input.dispatchMouseEvent")).toHaveLength(3);
    expect(firstInput).toBe(true);
  });

  it("treats a click that navigated, with no listener result, as landed", async () => {
    stamp("<button id='save'>Save</button>", "c1");
    restoreLayout = installLayout();
    // The click replaces the document: no DOM mouse event, and a frameNavigated on release.
    const { cdp, callsTo } = fakeCdp(undefined, { navigateOnPress: true });

    const box = await dispatchClick(cdp, "c1", undefined, undefined, 200);

    expect(box.ok).toBe(true);
    expect(callsTo("Input.dispatchMouseEvent")).toHaveLength(3);
  });

  it("reports lost (not rejected) when a commit dispatch lands elsewhere", async () => {
    stamp("<button id='save'>Save</button>", "c1");
    restoreLayout = installLayout();
    const { cdp } = fakeCdp(undefined, {
      pressAt: (_params, at) => (at === document.querySelector("[data-pl-target]") ? stranger() : at),
    });
    const harness = makeSessionHarness(cdp);

    const socket = await openSession(harness);
    deliver(socket, commitCommand());
    await flush();

    const result = socket.lastResult();
    expect(result?.["status"]).toBe("lost");
    expect(result?.["status"]).not.toBe("rejected");
    expect(String(result?.["error"])).toMatch(/landed on a different element/);
  });
});

/** An element that is not the marked target, so a press on it is "elsewhere". */
function stranger(): Element {
  const node = document.createElement("span");
  node.textContent = "elsewhere";
  document.body.appendChild(node);
  return node;
}

// ------------------------------------------------------------------ a minimal command session
//
// A trimmed copy of the command-state harness: enough to drive the REAL session with the REAL
// dispatchCommandStep, so the F2 classification is exercised end to end, not re-implemented.

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

function makeSessionHarness(cdp: CdpTransport): { session: CommandSession; sockets: FakeSocket[] } {
  const sockets: FakeSocket[] = [];
  const deps: SessionDeps = {
    createSocket: (url, protocols) => {
      const socket = new FakeSocket(url, protocols);
      sockets.push(socket);
      return socket;
    },
    storage: {
      async get(): Promise<StoredSession | null> {
        return null;
      },
      async set(): Promise<void> {
        return undefined;
      },
    },
    now: () => 1_000,
    setInterval: () => () => undefined,
    bindActiveTab: async () => 7,
    restoreBoundTab: async () => true,
    runStep: (step, opts) =>
      dispatchCommandStep(step, {
        cdp,
        allowedOrigins: opts.allowedOrigins,
        beforeInput: opts.beforeInput,
        onFirstInput: opts.sentInput,
      }),
    checkCondition: async (): Promise<CheckAnswer> => ({ ok: true, passed: true }),
    onState: (_state: SessionState) => undefined,
  };
  const session = new CommandSession(deps);
  return { session, sockets };
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
  // The real click now awaits two animation-frame pairs (each a macrotask), so give the session
  // enough turns to finish the command before reading its result.
  for (let i = 0; i < 8; i += 1) {
    await new Promise<void>((resolve) => setTimeout(resolve, 0));
  }
}

async function openSession(harness: { session: CommandSession; sockets: FakeSocket[] }): Promise<FakeSocket> {
  await harness.session.connect("http://127.0.0.1:8000");
  const sockets = harness.sockets;
  const socket = sockets[sockets.length - 1];
  if (!socket) throw new Error("the session did not open a socket");
  socket.emit("open");
  return socket;
}

function deliver(socket: FakeSocket, message: unknown): void {
  socket.emit("message", { data: JSON.stringify(message) });
}

function commitCommand(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    protocol: 1,
    type: "dispatch",
    command_id: "c-commit",
    step_ref: "s1",
    run_id: "run-1",
    run_record_id: "rec-1",
    lease_epoch: 1,
    expires_at: 10_000_000,
    step: {
      id: "step-1",
      action: "click",
      timeout_ms: 500,
      targets: [{ by: "css", selector: "#save" }],
    },
    commit: true,
    allowed_origins: [],
    ...overrides,
  };
}
