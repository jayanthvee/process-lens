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
  it("is done, with no press, when a busy target's confirmation appears while waiting", async () => {
    // Legitimate case: nothing proves it before the step; the page adds the confirmation while the
    // button is busy, which is the site finishing the work the click would have started.
    document.body.innerHTML = "<button id='continue' aria-busy='true'>Continuing</button>";
    const { cdp, callsTo } = fakeCdp();
    setTimeout(() => {
      const status = document.createElement("p");
      status.setAttribute("role", "status");
      status.textContent = "You are signed in";
      document.body.appendChild(status);
    }, 20);

    const report = await performStep(
      step({ assert_after: { text_visible: "You are signed in", timeout_ms: 400 } }),
      { row: {} },
      undefined,
      { ...deps(), cdp },
    );

    expect(report.outcome).toBe("done");
    expect(report.detail).toBe("the page moved on by itself");
    expect(report.assertionAlreadySatisfied).toBe(true);
    expect(callsTo("Input.dispatchMouseEvent")).toHaveLength(0);
  });

  it("is done when a disabled target's confirmation appears while waiting", async () => {
    document.body.innerHTML = "<button id='continue' disabled>Continue</button>";
    const { cdp, callsTo } = fakeCdp();
    setTimeout(() => {
      const status = document.createElement("p");
      status.textContent = "Code accepted";
      document.body.appendChild(status);
    }, 20);

    const report = await performStep(
      step({ assert_after: { text_visible: "Code accepted", timeout_ms: 400 } }),
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

  it("waits on a busy Save when the confirmation is ALREADY on screen, never skipping it", async () => {
    // The review's blocking case: a confirmation left from the previous row makes text_visible true
    // before this step. Because it was true before, the probe must never claim the click is done.
    document.body.innerHTML =
      "<button id='continue' aria-busy='true'>Save</button>" +
      "<p role='status'>Saved</p>";
    const { cdp, callsTo } = fakeCdp();

    await expect(
      performStep(
        step({ timeout_ms: 200, assert_after: { text_visible: "Saved", timeout_ms: 200 } }),
        { row: {} },
        undefined,
        { ...deps(), cdp },
      ),
    ).rejects.toThrow(/could not be resolved/);
    // It waited on the busy control instead of treating the stale confirmation as success.
    expect(callsTo("Input.dispatchMouseEvent")).toHaveLength(0);
  });

  it("counts a busy Save as done when the page adds the confirmation while waiting", async () => {
    // A count condition: the baseline is taken before the step, so an increase after it is real
    // evidence the page moved on. The button stays busy, so there is nothing to click.
    document.body.innerHTML = "<button id='continue' aria-busy='true'>Save</button>";
    const { cdp, callsTo } = fakeCdp();
    setTimeout(() => {
      const saved = document.createElement("p");
      saved.textContent = "Saved";
      document.body.appendChild(saved);
    }, 20);

    const report = await performStep(
      step({
        timeout_ms: 800,
        assert_after: { text_count_increases: "Saved", timeout_ms: 400 },
      }),
      { row: {} },
      undefined,
      { ...deps(), cdp },
    );

    expect(report.outcome).toBe("done");
    expect(report.detail).toBe("the page moved on by itself");
    expect(callsTo("Input.dispatchMouseEvent")).toHaveLength(0);
  });
});
