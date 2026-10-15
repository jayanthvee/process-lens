// T1: the click-1 race the messy fixture exposed.
//
// Two rulings, one change. (a) The landing listener is armed and the pointer moved BEFORE the final
// hit test, so that hit test is the last check before the press -- a page that reflows in response
// to the move itself is then seen by the check that follows it, and the only window left for a late
// reflow is the single press send. (b) A click whose stability check would start within
// `INPUT_QUIET_MS` of the executor's own last input (a fill, a press or a select) waits the
// remainder out first, inside the step's own deadline: a page's reaction to input can be delayed --
// the messy fixture opens its listbox 120 ms after the keystroke that triggered it. R3, the landing
// check, is unchanged and stays the backstop.
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import {
  dispatchClick,
  dispatchFill,
  INPUT_QUIET_MS,
  resetInputClock,
} from "../src/executor/dispatch";
import { resolveTargetInPage } from "../src/executor/ladder";
import { ExecutorError } from "../src/executor/types";
import { fakeCdp, type FakeCdp } from "./helpers/fake-cdp";
import { installLayout } from "./helpers/layout";

let restoreLayout: (() => void) | null = null;

/**
 * Mount markup and let the real resolver stamp the named element, returning it. The resolver clears
 * the other markers, so each element is stamped immediately before the action that uses it.
 */
function stamp(html: string, selector: string, token: string): Element | null {
  document.body.innerHTML = html;
  resolveTargetInPage({ targets: [{ by: "css", selector }], within: null, token });
  return document.querySelector("[data-pl-target]");
}

/** An element that is not the marked target, so a press on it is "elsewhere". */
function stranger(): Element {
  const node = document.createElement("span");
  node.textContent = "elsewhere";
  document.body.appendChild(node);
  return node;
}

/** The `mousePressed` events, so a test can prove there was exactly one and no retry. */
function presses(fake: FakeCdp): Array<Record<string, unknown>> {
  return fake
    .callsTo("Input.dispatchMouseEvent")
    .filter((call) => call.params["type"] === "mousePressed")
    .map((call) => call.params);
}

beforeEach(() => {
  document.body.innerHTML = "";
  resetInputClock();
});

afterEach(() => {
  restoreLayout?.();
  restoreLayout = null;
});

describe("the post-input quiet window (T1b)", () => {
  it("waits out the quiet window, so a box that moves 120 ms after a fill is clicked at its settled position", async () => {
    const html = "<input id='to'><button id='add'>Add</button>";
    let add: Element | null = null;
    // The first measure of the button, as a number: `NaN` until the click's own measurement runs,
    // so the assertion below needs no nullable cast.
    let firstMeasureAt = Number.NaN;
    // When the fill's own input was sent, from its hook: the quiet window is measured from THIS
    // moment in the executor, so the timing assertion is anchored here too. Anchoring it at the
    // click's start instead made the test flaky: the gap between the fill and the click (a stamp
    // and a call) shrinks what is left of the window. Run under the full suite's load.
    let inputSentAt = 0;
    let fillReturnedAt = 0;
    restoreLayout = installLayout({
      // The page reacts to the fill late: the box holds at y=10 and moves to y=60 only once 120 ms
      // have passed since the fill returned, which is what the quiet window has to wait out.
      rectAt: (_call, element) => {
        if (element === add && Number.isNaN(firstMeasureAt)) firstMeasureAt = Date.now();
        return { left: 10, top: Date.now() - fillReturnedAt >= 120 ? 60 : 10, width: 120, height: 24 };
      },
      hitAt: () => add,
    });
    const fake = fakeCdp(undefined, { simulateInput: true });

    stamp(html, "#to", "f1");
    await dispatchFill(fake.cdp, "f1", "dana@example.test", {
      clearFirst: false,
      hooks: {
        onFirstInput: () => {
          inputSentAt = Date.now();
        },
      },
    });
    fillReturnedAt = Date.now();

    add = stamp(html, "#add", "c1");
    // Stamping measures the button once itself; the timing under test starts at the click.
    firstMeasureAt = Number.NaN;

    const clickStartedAt = Date.now();
    const box = await dispatchClick(fake.cdp, "c1", undefined, undefined, 2000);

    // The settled box center: top 60 + height/2. Without the wait the click would have measured the
    // pre-reflow box (top 10) and pressed at y=22.
    expect(box).toMatchObject({ ok: true, x: 70, y: 72 });
    const pressed = presses(fake);
    expect(pressed).toHaveLength(1);
    expect(pressed[0]).toMatchObject({ x: 70, y: 72 });
    expect(Number.isNaN(firstMeasureAt)).toBe(false);
    expect(inputSentAt).toBeGreaterThan(0);
    // ~INPUT_QUIET_MS when the wait runs; the 60 ms of slack absorbs the clock's granularity, not
    // the wait -- a timer never fires early, so the only way this is short is a run with no wait.
    expect(firstMeasureAt - inputSentAt).toBeGreaterThanOrEqual(INPUT_QUIET_MS - 60);
    expect(clickStartedAt).toBeLessThanOrEqual(firstMeasureAt);
  });

  it("pays no wait when there is no recent input", async () => {
    // Case 1: the executor has sent no input at all in this session.
    let save: Element | null = null;
    let firstMeasureAt = Number.NaN;
    restoreLayout = installLayout({
      rectAt: (_call, element) => {
        if (element === save && Number.isNaN(firstMeasureAt)) firstMeasureAt = Date.now();
        return { left: 10, top: 10, width: 120, height: 24 };
      },
    });
    const bare = fakeCdp();

    save = stamp("<button id='save'>Save</button>", "#save", "c1");
    firstMeasureAt = Number.NaN;
    const started = Date.now();
    const box = await dispatchClick(bare.cdp, "c1", undefined, undefined, 2000);

    expect(box.ok).toBe(true);
    expect(Number.isNaN(firstMeasureAt)).toBe(false);
    expect(firstMeasureAt - started).toBeLessThan(INPUT_QUIET_MS - 60);

    // Case 2: input was sent, but long enough ago that the window is already over. A fresh
    // transport and a fresh layout stub, with the first stub restored before the second.
    restoreLayout();
    restoreLayout = null;
    let save2: Element | null = null;
    let firstMeasure2 = Number.NaN;
    restoreLayout = installLayout({
      rectAt: (_call, element) => {
        if (element === save2 && Number.isNaN(firstMeasure2)) firstMeasure2 = Date.now();
        return { left: 10, top: 10, width: 120, height: 24 };
      },
    });
    const html = "<input id='to'><button id='save'>Save</button>";
    const filled = fakeCdp(undefined, { simulateInput: true });

    stamp(html, "#to", "f1");
    await dispatchFill(filled.cdp, "f1", "x@example.test", { clearFirst: false });
    await new Promise((resolve) => setTimeout(resolve, INPUT_QUIET_MS + 40));

    save2 = stamp(html, "#save", "c1");
    firstMeasure2 = Number.NaN;
    const started2 = Date.now();
    const box2 = await dispatchClick(filled.cdp, "c1", undefined, undefined, 2000);

    expect(box2.ok).toBe(true);
    expect(Number.isNaN(firstMeasure2)).toBe(false);
    expect(firstMeasure2 - started2).toBeLessThan(INPUT_QUIET_MS - 60);
  });

  it("leaves no landing listener armed when the step throws after arming it", async () => {
    // The armed listener is the only thing that can decide R3, so a step that throws with it
    // already installed (a withdrawn command at the gate, a settle that failed on a later attempt)
    // must remove it on its way out. Without the cleanup this test fails: the install leaves the
    // state object on `window.__plClickLanding` until its own timer fires, seconds later.
    stamp("<button id='add'>Add</button>", "#add", "c1");
    restoreLayout = installLayout();
    const fake = fakeCdp();
    const hooks = {
      // The gate runs immediately before every input: the first call is the pointer move that
      // follows the install, so refusing there throws with the listener armed and nothing pressed.
      beforeInput: () => {
        throw new Error("the command was withdrawn");
      },
    };

    const thrown = await dispatchClick(fake.cdp, "c1", undefined, hooks, 2000).catch(
      (error: unknown) => error,
    );

    expect(String((thrown as Error).message)).toMatch(/withdrawn/);
    expect(fake.callsTo("Input.dispatchMouseEvent").map((call) => call.params["type"])).not.toContain(
      "mousePressed",
    );
    const landing = (globalThis as unknown as { __plClickLanding?: unknown }).__plClickLanding;
    expect(landing).toBeUndefined();
  });

  it("still reports lost when a press lands elsewhere, only after input was sent", async () => {
    const save = stamp("<button id='save'>Save</button>", "#save", "c1");
    restoreLayout = installLayout();
    // R3 is unchanged: the press lands on a different element than the point resolved to.
    const fake = fakeCdp(undefined, {
      pressAt: (_params, at) => (at === save ? stranger() : at),
    });
    let firstInput = false;
    const hooks = {
      onFirstInput: () => {
        firstInput = true;
      },
    };

    const thrown = await dispatchClick(fake.cdp, "c1", undefined, hooks, 2000).catch(
      (error: unknown) => error,
    );

    expect(thrown).toBeInstanceOf(ExecutorError);
    expect((thrown as ExecutorError).disposition).toBe("park");
    expect((thrown as ExecutorError).message).toMatch(/landed on a different element/);
    // The press WAS sent, which is what makes the command session classify this step `lost`.
    expect(firstInput).toBe(true);
    const mouseTypes = fake
      .callsTo("Input.dispatchMouseEvent")
      .map((call) => call.params["type"]);
    expect(mouseTypes).toContain("mousePressed");
    expect(mouseTypes).toContain("mouseReleased");
  });

  it("runs the final hit test after the pointer move, so a hover-triggered reflow is caught", async () => {
    let hovered = false;
    const onMove = (): void => {
      hovered = true;
    };
    document.addEventListener("mousemove", onMove);
    try {
      const add = stamp("<input id='to'><button id='add'>Add</button>", "#add", "c1");
      const overlay = document.createElement("div");
      overlay.id = "overlay";
      document.body.appendChild(overlay);
      // The page reacts to the pointer itself: the move reflows the target down to y=60 and puts
      // the overlay under the old point.
      restoreLayout = installLayout({
        rectAt: () => ({ left: 10, top: hovered ? 60 : 10, width: 120, height: 24 }),
        hitAt: (_call, _x, y) =>
          hovered ? (y >= 50 ? add : overlay) : y >= 10 && y <= 34 ? add : null,
      });
      const fake = fakeCdp();

      const box = await dispatchClick(fake.cdp, "c1", undefined, undefined, 2000);

      expect(hovered).toBe(true);
      expect(box).toMatchObject({ ok: true, x: 70, y: 72 });
      const pressed = presses(fake);
      expect(pressed).toHaveLength(1);
      expect(pressed[0]).toMatchObject({ x: 70, y: 72 });
    } finally {
      document.removeEventListener("mousemove", onMove);
    }
  });
});
