// PL-108: the per-step fallback. An in-page action whose own check cannot see an effect is retried
// once through the debugger, and the report records which path worked. A commit step is never
// retried (a failed commit check may already have saved).
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { runRecipe } from "../src/executor/executor";
import type { ExecutorRecipe, ExecutorStep } from "../src/executor/types";
import { fakeCdp } from "./helpers/fake-cdp";
import { installLayout } from "./helpers/layout";

let restoreLayout: (() => void) | null = null;

function recipe(steps: ExecutorStep[]): ExecutorRecipe {
  return { format_version: 1, meta: { name: "Experiment", allowed_origins: [] }, steps };
}

function clickStep(overrides: Partial<ExecutorStep> = {}): ExecutorStep {
  return {
    id: "click-1",
    action: "click",
    targets: [{ by: "testid", value: "save" }],
    assert_after: { text_visible: "Saved", timeout_ms: 80 },
    ...overrides,
  };
}

/** A page whose button works only for the debugger's press. */
function page(): void {
  document.body.innerHTML = "<button id='save' data-testid='save'>Save</button>";
  document.addEventListener(
    "mousedown",
    () => {
      if (document.getElementById("saved")) return;
      const p = document.createElement("p");
      p.id = "saved";
      p.textContent = "Saved";
      document.body.appendChild(p);
    },
    true,
  );
}

beforeEach(() => {
  document.body.innerHTML = "";
  restoreLayout = installLayout();
});

afterEach(() => {
  restoreLayout?.();
  restoreLayout = null;
});

describe("the per-step fallback", () => {
  it("says in-page when the in-page action's own check passes", async () => {
    page();
    const { cdp, callsTo } = fakeCdp();

    const summary = await runRecipe(recipe([clickStep()]), { row: {} }, { cdp, inPage: true });

    expect(summary.state).toBe("done");
    expect(summary.steps[0]?.via).toBe("in-page");
    // The in-page path sent no trusted mouse command at all.
    expect(callsTo("Input.dispatchMouseEvent")).toHaveLength(0);
  });

  it("retries through the debugger when the in-page action showed no effect", async () => {
    page();
    // Make the in-page click expression a no-op: it answers ok but dispatches nothing, so the
    // page never shows "Saved" and the step's own check cannot see the action.
    const { cdp, callsTo } = fakeCdp((method, params) => {
      if (method === "Runtime.evaluate" && String(params["expression"]).includes("pointerdown")) {
        return {
          result: { value: { ok: true, x: 70, y: 22, width: 120, height: 24, hit: "button", topFrame: true } },
        };
      }
      return undefined;
    });

    const summary = await runRecipe(recipe([clickStep()]), { row: {} }, { cdp, inPage: true });

    expect(summary.state).toBe("done");
    expect(summary.steps[0]?.via).toBe("debugger");
    expect(summary.steps[0]?.detail).toMatch(/in-page action showed no effect/);
    // The debugger path really ran: at least one trusted mouse press.
    expect(callsTo("Input.dispatchMouseEvent").length).toBeGreaterThan(0);
  });

  it("never retries a commit step: a failed commit check parks", async () => {
    page();
    const { cdp, callsTo } = fakeCdp((method, params) => {
      if (method === "Runtime.evaluate" && String(params["expression"]).includes("pointerdown")) {
        return {
          result: { value: { ok: true, x: 70, y: 22, width: 120, height: 24, hit: "button", topFrame: true } },
        };
      }
      return undefined;
    });

    const summary = await runRecipe(
      recipe([clickStep({ commit: true })]),
      { row: {} },
      { cdp, inPage: true },
    );

    expect(summary.state).toBe("failed");
    // No trusted press was sent for the commit after its in-page check failed.
    expect(callsTo("Input.dispatchMouseEvent")).toHaveLength(0);
  });

  it("leaves a debugger-only run's reports without a path", async () => {
    page();
    const { cdp } = fakeCdp();

    const summary = await runRecipe(recipe([clickStep()]), { row: {} }, { cdp });

    expect(summary.state).toBe("done");
    expect(summary.steps[0]?.via).toBeUndefined();
  });
});
