// PL-040: a hidden work page pauses the run instead of failing the row.
//
// Chrome stops drawing a background tab or a covered window, and the step that runs there fails
// with a message about the target ("19 element(s)", "no animation frame arrived") that hides the
// real cause. The step now checks first: it brings the window forward, and a page that stays hidden
// refuses with the `window_hidden` event, which the runner turns into a pause.
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { dispatchCommandStep, prepareCommandStep } from "../src/background/command-step";
import { ExecutorError, type ExecutorStep } from "../src/executor/types";
import {
  WINDOW_HIDDEN_MESSAGE,
  ensureVisible,
  pageIsHidden,
  type VisibilityDeps,
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
  const deps = {
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

describe("ensureVisible", () => {
  it("a visible page is left alone", async () => {
    const deps = pageDeps(0);
    expect(await ensureVisible(deps)).toBe(true);
    expect(deps.raised).toBe(0);
  });

  it("a hidden page is brought forward once and waited for", async () => {
    const deps = pageDeps(3);
    expect(await ensureVisible(deps)).toBe(true);
    expect(deps.raised).toBe(1);
  });

  it("a page that stays hidden is reported, after one raise", async () => {
    const deps = pageDeps(Number.POSITIVE_INFINITY);
    expect(await ensureVisible(deps, 500, 100)).toBe(false);
    expect(deps.raised).toBe(1);
  });

  it("a raise that throws still reports the page honestly", async () => {
    const deps = pageDeps(Number.POSITIVE_INFINITY);
    deps.bringForward = async () => {
      throw new Error("no window");
    };
    expect(await ensureVisible(deps, 200, 100)).toBe(false);
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
      ensureVisible: async () => false,
    }).catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(ExecutorError);
    expect((error as ExecutorError).event).toBe("window_hidden");
    expect((error as ExecutorError).message).toBe(WINDOW_HIDDEN_MESSAGE);
    expect(fake.calls.filter((call) => call.method.startsWith("Input."))).toEqual([]);
  });

  it("dispatch refuses the same way", async () => {
    const fake = fakeCdp();
    const error = await dispatchCommandStep(fillStep(), {
      cdp: fake.cdp,
      allowedOrigins: [window.location.origin],
      ensureVisible: async () => false,
    }).catch((caught: unknown) => caught);

    expect((error as ExecutorError).event).toBe("window_hidden");
    expect(fake.calls.filter((call) => call.method.startsWith("Input."))).toEqual([]);
  });

  it("a visible page runs the step as before", async () => {
    const fake = fakeCdp();
    const report = await prepareCommandStep(fillStep(), {
      cdp: fake.cdp,
      allowedOrigins: [window.location.origin],
      ensureVisible: async () => true,
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
