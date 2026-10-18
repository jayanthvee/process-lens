// PL-040: a hidden work page pauses the run instead of failing the row.
//
// Chrome stops drawing a background tab or a covered window, and the step that runs there fails
// with a message about the target ("19 element(s)", "no animation frame arrived") that hides the
// real cause. The step now checks first: it brings the window forward, and a page that stays hidden
// refuses with the `window_hidden` event, which the runner turns into a pause.
//
// PL-045: the refusal now also says WHY the window would not come forward. `ensureVisible` reports
// what it tried, what Chrome said when the raise failed, and the window's state, focus and tab
// activity before and after. That report becomes one short line on the `window_hidden` detail.
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { dispatchCommandStep, prepareCommandStep } from "../src/background/command-step";
import { ExecutorError, type ExecutorStep } from "../src/executor/types";
import {
  WINDOW_HIDDEN_MESSAGE,
  ensureVisible,
  pageIsHidden,
  windowHiddenMessage,
  type VisibilityDeps,
  type VisibilityReport,
  type WindowFacts,
} from "../src/executor/visibility";
import { PROTOCOL, parseIncoming } from "../src/shared/command-protocol";
import { fakeCdp } from "./helpers/fake-cdp";
import { installLayout } from "./helpers/layout";

let restoreLayout: (() => void) | null = null;

beforeEach(() => {
  document.body.innerHTML = `<label for="email">Email</label><input id="email" type="text">`;
  restoreLayout = installLayout();
});

afterEach(() => {
  restoreLayout?.();
  restoreLayout = null;
});

function fillStep(): ExecutorStep {
  return {
    id: "fill-1",
    action: "fill",
    value: { kind: "constant", value: "ada@example.test" },
    targets: [{ by: "label", text: "Email" }],
  };
}

/** A page that is hidden for the first `hiddenChecks` answers, and records each raise. */
function pageDeps(hiddenChecks: number): VisibilityDeps & { raised: number; checks: number } {
  const deps: VisibilityDeps & { raised: number; checks: number } = {
    raised: 0,
    checks: 0,
    isHidden: async () => deps.checks++ < hiddenChecks,
    bringForward: async () => {
      deps.raised += 1;
    },
    sleep: async () => undefined,
  };
  return deps;
}

/** The report a page that stayed hidden leaves behind. */
function hiddenReport(overrides: Partial<VisibilityReport> = {}): VisibilityReport {
  return {
    visible: false,
    tried: true,
    before: { state: "minimized", focused: false, tabActive: false },
    after: { state: "minimized", focused: false, tabActive: true },
    error: "no window",
    waitedMs: 2000,
    ...overrides,
  };
}

function visibleReport(): VisibilityReport {
  return { visible: true, tried: false, waitedMs: 0 };
}

describe("ensureVisible", () => {
  it("a visible page is left alone and reports that nothing was tried", async () => {
    const deps = pageDeps(0);
    const report = await ensureVisible(deps);
    expect(report).toEqual({ visible: true, tried: false, waitedMs: 0 });
    expect(deps.raised).toBe(0);
  });

  it("a hidden page that comes forward reports the raise and the wait", async () => {
    const deps = pageDeps(3);
    const report = await ensureVisible(deps);
    expect(report.visible).toBe(true);
    expect(report.tried).toBe(true);
    expect(report.waitedMs).toBe(300);
    expect(deps.raised).toBe(1);
  });

  it("a page that stays hidden reports how long it waited", async () => {
    const deps = pageDeps(Number.POSITIVE_INFINITY);
    const report = await ensureVisible(deps, 500, 100);
    expect(report.visible).toBe(false);
    expect(report.tried).toBe(true);
    expect(report.waitedMs).toBe(500);
    expect(deps.raised).toBe(1);
  });

  it("a raise that throws keeps its message in the report", async () => {
    const deps = pageDeps(Number.POSITIVE_INFINITY);
    deps.bringForward = async () => {
      throw new Error("no window");
    };
    const report = await ensureVisible(deps, 200, 100);
    expect(report.visible).toBe(false);
    expect(report.tried).toBe(true);
    expect(report.error).toBe("no window");
  });

  it("reads the window before the raise and again at the end", async () => {
    const deps = pageDeps(Number.POSITIVE_INFINITY);
    const facts: WindowFacts[] = [
      { state: "minimized", focused: false, tabActive: false },
      { state: "normal", focused: true, tabActive: true },
    ];
    let calls = 0;
    deps.windowFacts = async () => facts[calls++];
    const report = await ensureVisible(deps, 200, 100);
    expect(calls).toBe(2);
    expect(report.before).toEqual(facts[0]);
    expect(report.after).toEqual(facts[1]);
  });

  it("a windowFacts that throws or answers undefined breaks nothing", async () => {
    const throwing = pageDeps(Number.POSITIVE_INFINITY);
    throwing.windowFacts = async () => {
      throw new Error("gone");
    };
    let report = await ensureVisible(throwing, 100, 100);
    expect(report.visible).toBe(false);
    expect(report.error).toBeUndefined();
    expect(report.before).toBeUndefined();
    expect(report.after).toBeUndefined();

    const silent = pageDeps(Number.POSITIVE_INFINITY);
    silent.windowFacts = async () => undefined;
    report = await ensureVisible(silent, 100, 100);
    expect(report.visible).toBe(false);
    expect(report.before).toBeUndefined();
    expect(report.after).toBeUndefined();
  });
});

describe("windowHiddenMessage", () => {
  it("one line names the window before and after, the focus, the raise error and the wait", () => {
    const message = windowHiddenMessage(hiddenReport());
    expect(message.startsWith(WINDOW_HIDDEN_MESSAGE)).toBe(true);
    expect(message).toContain("window minimized→minimized");
    expect(message).toContain("focused no");
    expect(message).toContain("tab active yes");
    expect(message).toContain("raise error: no window");
    expect(message).toContain("waited 2000ms");
    expect(message).not.toMatch(/https?:\/\//);
  });

  it("is the plain message when there is no report", () => {
    expect(windowHiddenMessage()).toBe(WINDOW_HIDDEN_MESSAGE);
  });

  it("is the plain message when the check found the page visible and nothing was raised (T1)", () => {
    expect(windowHiddenMessage({ visible: true, tried: false, waitedMs: 0 })).toBe(
      WINDOW_HIDDEN_MESSAGE,
    );
  });
});

describe("pageIsHidden", () => {
  it("reads document.visibilityState through the debugger", async () => {
    const hidden = {
      send: async () => ({ result: { value: "hidden" } }),
    } as unknown as Parameters<typeof pageIsHidden>[0];
    const visible = {
      send: async () => ({ result: { value: "visible" } }),
    } as unknown as Parameters<typeof pageIsHidden>[0];
    expect(await pageIsHidden(hidden)).toBe(true);
    expect(await pageIsHidden(visible)).toBe(false);
  });

  it("a page that cannot answer is not called hidden", async () => {
    const broken = {
      send: async () => {
        throw new Error("detached");
      },
    } as unknown as Parameters<typeof pageIsHidden>[0];
    expect(await pageIsHidden(broken)).toBe(false);
  });
});

describe("a step on a hidden page", () => {
  it("prepare refuses with the window_hidden event and sends no input", async () => {
    const fake = fakeCdp();
    const error = await prepareCommandStep(fillStep(), {
      cdp: fake.cdp,
      allowedOrigins: [window.location.origin],
      ensureVisible: async () => hiddenReport(),
    }).catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(ExecutorError);
    expect((error as ExecutorError).event).toBe("window_hidden");
    expect((error as ExecutorError).message.startsWith(WINDOW_HIDDEN_MESSAGE)).toBe(true);
    expect(fake.calls.filter((call) => call.method.startsWith("Input."))).toEqual([]);
  });

  it("the window_hidden detail says what the window was doing and carries no URL", async () => {
    const fake = fakeCdp();
    const error = (await prepareCommandStep(fillStep(), {
      cdp: fake.cdp,
      allowedOrigins: [window.location.origin],
      ensureVisible: async () => hiddenReport(),
    }).catch((caught: unknown) => caught)) as ExecutorError;

    const message = error.message;
    expect(message.startsWith(WINDOW_HIDDEN_MESSAGE)).toBe(true);
    expect(message).toContain("window minimized→minimized");
    expect(message).toContain("focused no");
    expect(message).toContain("tab active yes");
    expect(message).toContain("raise error: no window");
    expect(message).toContain("waited 2000ms");
    expect(message).not.toMatch(/https?:\/\//);
    expect(message).not.toContain(window.location.href);
  });

  it("dispatch refuses the same way", async () => {
    const fake = fakeCdp();
    const error = await dispatchCommandStep(fillStep(), {
      cdp: fake.cdp,
      allowedOrigins: [window.location.origin],
      ensureVisible: async () => hiddenReport(),
    }).catch((caught: unknown) => caught);

    expect((error as ExecutorError).event).toBe("window_hidden");
    expect(fake.calls.filter((call) => call.method.startsWith("Input."))).toEqual([]);
  });

  it("a visible page runs the step as before", async () => {
    const fake = fakeCdp();
    const report = await prepareCommandStep(fillStep(), {
      cdp: fake.cdp,
      allowedOrigins: [window.location.origin],
      ensureVisible: async () => visibleReport(),
    });
    expect(report.outcome).toBe("done");
  });
});

describe("the protocol", () => {
  it("accepts window_hidden as an event the runner pauses on", () => {
    const parsed = parseIncoming({
      protocol: PROTOCOL,
      type: "event",
      kind: "window_hidden",
      detail: WINDOW_HIDDEN_MESSAGE,
    });
    expect("message" in parsed).toBe(true);
  });
});
