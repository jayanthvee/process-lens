// PL-107 item 11: a click the site already did by itself counts as done.
//
// The site moves on once a valid email is in: the button turns into its loading state (aria-busy or
// disabled) while the run is about to click it. If the step's own assert_after is already true, the
// click's result is there and the step is done -- no press is sent. With no assertion, today's wait
// is unchanged.
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { resetInputClock } from "../src/executor/dispatch";
import { performStep, type StepRunDeps } from "../src/executor/step-runner";
import type { ExecutorStep } from "../src/executor/types";
import { fakeCdp } from "./helpers/fake-cdp";
import { installLayout } from "./helpers/layout";

let restoreLayout: (() => void) | null = null;

beforeEach(() => {
  document.body.innerHTML = "";
  resetInputClock();
  restoreLayout = installLayout();
});

afterEach(() => {
  restoreLayout?.();
  restoreLayout = null;
});

function deps(): StepRunDeps {
  return { cdp: fakeCdp().cdp };
}

function step(overrides: Partial<ExecutorStep> = {}): ExecutorStep {
  return {
    id: "click-1",
    action: "click",
    targets: [{ by: "css", selector: "#continue" }],
    timeout_ms: 600,
    ...overrides,
  };
}

describe("a click whose target the site already handled", () => {
  it("is done, with no press, when a busy target's own assertion already holds", async () => {
    document.body.innerHTML =
      "<button id='continue' aria-busy='true'>Continuing</button>" +
      "<p role='status'>You are signed in</p>";
    const { cdp, callsTo } = fakeCdp();

    const report = await performStep(
      step({ assert_after: { text_visible: "You are signed in" } }),
      { row: {} },
      undefined,
      { ...deps(), cdp },
    );

    expect(report.outcome).toBe("done");
    expect(report.detail).toBe("the page moved on by itself");
    expect(callsTo("Input.dispatchMouseEvent")).toHaveLength(0);
  });

  it("is done when a disabled target's assertion already holds", async () => {
    document.body.innerHTML =
      "<button id='continue' disabled>Continue</button>" +
      "<p role='status'>Code accepted</p>";
    const { cdp, callsTo } = fakeCdp();

    const report = await performStep(
      step({ assert_after: { text_visible: "Code accepted" } }),
      { row: {} },
      undefined,
      { ...deps(), cdp },
    );

    expect(report.outcome).toBe("done");
    expect(report.detail).toBe("the page moved on by itself");
    expect(callsTo("Input.dispatchMouseEvent")).toHaveLength(0);
  });

  it("keeps today's behaviour when the step has no assertion", async () => {
    document.body.innerHTML = "<button id='continue' disabled>Continue</button>";
    const { cdp, callsTo } = fakeCdp();

    await expect(
      performStep(step(), { row: {} }, undefined, { ...deps(), cdp }),
    ).rejects.toThrow(/could not be resolved/);
    expect(callsTo("Input.dispatchMouseEvent")).toHaveLength(0);
  });
});
