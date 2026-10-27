// PL-116: a button that is busy while a click is prepared is the page still working, not a dead
// target.
//
// After the email is typed, the owner's sign-in page shows a spinner on Continue for a moment and
// the button takes no clicks. A person waits without noticing; the prepare used to refuse at its
// first look and the record parked in about a second.
//
// The busy state that reaches the separate measure is `pointer-events: none`: the ladder's own
// resolver already waits on `disabled` and `aria-busy`, but it does not look at pointer-events, so
// the marked button resolves and the measure is the call that refuses.
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { dispatchCommandStep } from "../src/background/command-step";
import type { ExecutorStep } from "../src/executor/types";
import { fakeCdp, type FakeCdp } from "./helpers/fake-cdp";
import { installLayout } from "./helpers/layout";

let restoreLayout: (() => void) | null = null;

beforeEach(() => {
  document.body.innerHTML = "";
  restoreLayout = installLayout();
});

afterEach(() => {
  restoreLayout?.();
  restoreLayout = null;
});

function signInPage(): HTMLButtonElement {
  document.body.innerHTML = `
    <form aria-label="Sign in">
      <label for="email">Email</label>
      <input id="email" type="email">
      <button id="continue" type="button">Continue</button>
    </form>`;
  return document.getElementById("continue") as HTMLButtonElement;
}

function clickContinue(): ExecutorStep {
  return {
    id: "click-2",
    action: "click",
    targets: [{ by: "role", role: "button", name: "Continue" }],
    timeout_ms: 2000,
  };
}

/** The presses the executor actually sent, not the moves and releases around them. */
function presses(fake: FakeCdp): number {
  return fake.callsTo("Input.dispatchMouseEvent").filter(
    (call) => call.params["type"] === "mousePressed",
  ).length;
}

/** The explicit measure calls. A resolver expression carries the measure source inline. */
function measures(fake: FakeCdp): number {
  return fake.calls.filter((call) => {
    if (call.method !== "Runtime.evaluate") return false;
    const expression = String(call.params["expression"] ?? "");
    return expression.includes("return measureTargetInPage(") && !expression.includes("const resolve =");
  }).length;
}

/** Whether this evaluate call is the prepare's own measure, not a climb or a settle. */
function isMeasure(params: Record<string, unknown>): boolean {
  const expression = String(params["expression"] ?? "");
  return expression.includes("return measureTargetInPage(") && !expression.includes("const resolve =");
}

describe("preparing a click on a busy button (PL-116)", () => {
  it("waits while the button takes no clicks, then prepares and clicks it once", async () => {
    const button = signInPage();
    // The spinner state Goodlabs shows after the email is typed: disabled and taking no clicks.
    // `pointer-events` is what reaches the separate measure; the resolver sees a usable button.
    // The state is released on the second measure, not on a timer, so the test cannot race one.
    button.style.pointerEvents = "none";
    let seen = 0;
    const fake = fakeCdp((method, params) => {
      if (method === "Runtime.evaluate" && isMeasure(params)) {
        seen += 1;
        if (seen >= 2) button.style.pointerEvents = "";
      }
      return undefined;
    });

    const report = await dispatchCommandStep(clickContinue(), {
      cdp: fake.cdp,
      allowedOrigins: [window.location.origin],
    });

    expect(report.outcome).toBe("done");
    expect(presses(fake)).toBe(1);
    // The first measure was refused; the prepare climbed again and measured once more.
    expect(measures(fake)).toBeGreaterThan(1);
  });

  it("fails with the busy message after the ready wait, and sends no click", async () => {
    const button = signInPage();
    button.style.pointerEvents = "none";
    const fake = fakeCdp();

    const pending = dispatchCommandStep(
      { ...clickContinue(), timeout_ms: 400 },
      { cdp: fake.cdp, allowedOrigins: [window.location.origin] },
    );

    await expect(pending).rejects.toThrow(/the target is busy/);
    expect(presses(fake)).toBe(0);
    // A target that stayed busy was looked at more than once before the step gave up.
    expect(measures(fake)).toBeGreaterThan(1);
  });
});
