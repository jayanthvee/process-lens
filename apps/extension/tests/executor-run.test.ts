// The executor end to end: a compiled recipe replayed against a page.
//
// The fake transport evaluates page expressions in jsdom, so the real 5-rung
// resolver and the real condition predicates run here; only the browser input
// itself is recorded rather than performed.
import { resolveStepTarget } from "../src/executor/step-runner";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import {
  armAbandonGuard,
  guardArmed,
  resetAbandonGuard,
} from "../src/executor/abandon-guard";
import { RunControl } from "../src/executor/control";
import { resetTokens, resolveValue, runRecipe, validateRecipe } from "../src/executor/executor";
import { FrameContexts } from "../src/executor/frames";
import type { ExecutorRecipe } from "../src/executor/types";
import { fakeCdp } from "./helpers/fake-cdp";
import { installLayout } from "./helpers/layout";

let restoreLayout: (() => void) | null = null;

const ORIGIN = "http://localhost:3000";

function recipe(steps: ExecutorRecipe["steps"]): ExecutorRecipe {
  return {
    format_version: 1,
    meta: { name: "Create a lead", allowed_origins: [ORIGIN] },
    steps,
  };
}

function formMarkup(): void {
  document.body.innerHTML =
    "<form aria-label='New lead'>" +
    "<label for='email'>Email</label><input id='email' data-testid='lead-email'>" +
    "<label for='interest'>Interest</label>" +
    "<select id='interest' data-testid='lead-interest'><option>Interest</option><option>Data course</option></select>" +
    "<button id='save' data-testid='lead-save' type='submit'>Save</button>" +
    "</form>";
}

beforeEach(() => {
  document.body.innerHTML = "";
  window.history.replaceState({}, "", "/records/new");
  resetTokens();
  // Clicks measure and hit-test a box; jsdom has no layout.
  restoreLayout = installLayout();
});

afterEach(() => {
  restoreLayout?.();
  restoreLayout = null;
});

describe("waiting for a page that has not rendered yet", () => {
  /** How many times a step climbed the ladder: one evaluate per climb. */
  function climbs(calls: { method: string; params: Record<string, unknown> }[]): number {
    return calls.filter(
      (call) =>
        call.method === "Runtime.evaluate" &&
        String(call.params["expression"] ?? "").includes('"targets"'),
    ).length;
  }

  it("climbs again while the page has nothing to match, and fills when it renders", async () => {
    // PL-027, on Odoo: the document's `load` event fires while the page is still an HTML shell, so
    // a step sent straight after a navigate climbed every rung against 68 elements and no inputs,
    // found nothing, and parked a record the app was about to be able to take.
    document.body.innerHTML = "<div id='app'></div>";
    const { cdp, calls } = fakeCdp(undefined, { simulateInput: true });

    // The application mounts a moment after the step starts.
    setTimeout(() => {
      document.getElementById("app")!.innerHTML =
        "<label for='email'>Email</label><input id='email'>";
    }, 120);

    const summary = await runRecipe(
      recipe([
        {
          id: "fill-1",
          action: "fill",
          targets: [{ by: "label", text: "Email" }],
          value: { kind: "input", column: "email" },
          timeout_ms: 2000,
        },
      ]),
      { row: { email: "late@example.test" } },
      { cdp },
    );

    expect(summary.steps.map((step) => step.outcome)).toEqual(["done"]);
    expect(calls.filter((call) => call.method === "Input.insertText")).toHaveLength(1);
    expect(climbs(calls)).toBeGreaterThan(1);
  });

  it("climbs again while a rung is ambiguous, and acts once the page settles to one", async () => {
    // PL-048, on Odoo: after "Add a product" the order line renders ~170 ms later. Until then the
    // css rung matches the customer's autocomplete and one other, and a climb that stopped at the
    // first ambiguous answer parked the record just before the row's own input existed.
    document.body.innerHTML =
      "<div id='app'><input class='ac' aria-label='Customer'><input class='ac' aria-label='Other'></div>";
    const { cdp, calls } = fakeCdp();

    setTimeout(() => {
      document.getElementById("app")!.innerHTML =
        "<input class='ac' aria-label='Customer'><input class='ac row' aria-label='Product'>";
      document.querySelector("[aria-label='Customer']")!.classList.remove("ac");
    }, 120);

    const summary = await runRecipe(
      recipe([
        {
          id: "click-1",
          action: "click",
          targets: [{ by: "css", selector: "input.ac" }],
          timeout_ms: 2000,
        },
      ]),
      { row: {} },
      { cdp },
    );

    expect(summary.steps.map((step) => step.outcome)).toEqual(["done"]);
    expect(climbs(calls)).toBeGreaterThan(1);
  });

  it("still refuses a rung that stays ambiguous, once the wait is over", async () => {
    // Waiting never acts on an ambiguous target; it only gives the page its chance to settle.
    document.body.innerHTML = "<button>Save</button><button>Save</button>";
    const { cdp, calls } = fakeCdp();

    const summary = await runRecipe(
      recipe([
        {
          id: "click-1",
          action: "click",
          targets: [{ by: "role", role: "button", name: "Save" }],
          timeout_ms: 600,
        },
      ]),
      { row: {} },
      { cdp },
    );

    expect(summary.state).toBe("failed");
    expect(summary.error).toMatch(/could not be resolved/);
    expect(summary.error).toMatch(/more than one match/);
    expect(climbs(calls)).toBeGreaterThan(1);
  });

  it("refuses a target the page never shows, once the wait is over", async () => {
    document.body.innerHTML = "<div id='app'></div>";
    const { cdp } = fakeCdp();

    const summary = await runRecipe(
      recipe([
        {
          id: "fill-1",
          action: "fill",
          targets: [{ by: "label", text: "Nowhere" }],
          value: { kind: "input", column: "email" },
          timeout_ms: 400,
        },
      ]),
      { row: { email: "gone@example.test" } },
      { cdp },
    );

    // The wait ends the same way a missing element always did: a refusal that names the page it
    // climbed, so a person can tell "the element is gone" from "the wrong page".
    expect(summary.state).toBe("failed");
    expect(summary.error).toMatch(/could not be resolved/);
    expect(summary.error).toMatch(/page .*; 0 input\(s\)/);
  });
});

describe("the abandon guard and a navigate", () => {
  it("releases the guard when the navigate arrives, and reports what it held back", async () => {
    // PL-027: the arrival is the moment the abandoned document is gone, so it is the moment the
    // guard may be released -- and the requests it refused are the evidence a person needs that a
    // parked record did not quietly reach the destination.
    resetAbandonGuard();
    const { cdp, emit } = fakeCdp((method, params) => {
      // The dirty page's save, sent as it unloads while the guard has the network shut.
      if (method === "Page.navigate" && params["url"] === "about:blank") {
        emit("Network.requestWillBeSent", {
          requestId: "w1",
          request: { method: "POST", url: "http://localhost:3000/web/save" },
        });
      }
      // A same-document reply: nothing to await, and the tab's URL is the proof of arrival.
      return method === "Page.navigate" ? { frameId: "F1", loaderId: "" } : undefined;
    });
    await armAbandonGuard(cdp);
    expect(guardArmed()).toBe(true);

    const summary = await runRecipe(
      recipe([{ id: "navigate-1", action: "navigate", url: `${ORIGIN}/records/new` }]),
      { row: {} },
      { cdp },
    );

    expect(summary.steps.map((step) => step.outcome)).toEqual(["done"]);
    expect(summary.steps[0]?.observed).toEqual({
      blocked: [{ method: "POST", url: "http://localhost:3000/web/save" }],
    });
    expect(guardArmed()).toBe(false);
    resetAbandonGuard();
  });
});

describe("runRecipe", () => {
  it("replays a fill, a select, and a committing click", async () => {
    formMarkup();
    document.body.append(Object.assign(document.createElement("div"), { textContent: "Record created" }));
    const { cdp, callsTo } = fakeCdp(undefined, { simulateInput: true });

    const recipeUnderTest = recipe([
      { id: "navigate-1", action: "navigate", url: `${ORIGIN}/records/new` },
      {
        id: "fill-1",
        action: "fill",
        targets: [{ by: "label", text: "Email" }],
        value: { kind: "input", column: "email" },
      },
      {
        id: "select-1",
        action: "select",
        targets: [{ by: "testid", value: "lead-interest" }],
        value: { kind: "constant", value: "Data course" },
        option_match: "text",
      },
      {
        id: "click-1",
        action: "click",
        commit: true,
        assert_after: { text_visible: "Record created", timeout_ms: 400 },
        targets: [{ by: "role", role: "button", name: "Save" }],
      },
    ]);

    const summary = await runRecipe(recipeUnderTest, { row: { email: "ravi@example.com" } }, { cdp });

    expect(summary.state).toBe("done");
    expect(summary.steps.map((step) => step.outcome)).toEqual(["done", "done", "done", "done"]);
    expect(callsTo("Page.navigate")).toHaveLength(1);
    expect((document.getElementById("email") as HTMLInputElement).value).toBe("ravi@example.com");
    expect((document.getElementById("interest") as HTMLSelectElement).value).toBe("Data course");
    // The committing click was resolved by rung 1 (role + name). Rungs are 1 to 5.
    expect(summary.steps[3]).toMatchObject({ rung: 1, by: "role" });
  });

  it("stops fail-closed when a commit's assertion never holds", async () => {
    formMarkup();
    const { cdp } = fakeCdp();

    const summary = await runRecipe(
      recipe([
        {
          id: "click-1",
          action: "click",
          commit: true,
          assert_after: { text_visible: "Record created", timeout_ms: 100 },
          targets: [{ by: "role", role: "button", name: "Save" }],
        },
      ]),
      { row: {} },
      { cdp },
    );

    expect(summary.state).toBe("failed");
    expect(summary.error).toMatch(/post-condition/);
  });

  it("refuses to navigate outside allowed_origins", async () => {
    const { cdp, callsTo } = fakeCdp();

    const summary = await runRecipe(
      recipe([{ id: "navigate-1", action: "navigate", url: "https://evil.example.com/records" }]),
      { row: {} },
      { cdp },
    );

    expect(summary.state).toBe("failed");
    expect(summary.error).toMatch(/allowed_origins/);
    expect(callsTo("Page.navigate")).toHaveLength(0);
  });

  it("stops when the run is aborted", async () => {
    formMarkup();
    const control = new RunControl();
    control.abort();
    const { cdp, callsTo } = fakeCdp();

    const summary = await runRecipe(
      recipe([
        {
          id: "fill-1",
          action: "fill",
          targets: [{ by: "label", text: "Email" }],
          value: { kind: "constant", value: "x" },
        },
      ]),
      { row: {} },
      { cdp, control },
    );

    expect(summary.state).toBe("aborted");
    expect(callsTo("Input.dispatchKeyEvent")).toHaveLength(0);
  });

  it("refuses to start when a step action is outside the executor's set", async () => {
    formMarkup();
    const { cdp, callsTo } = fakeCdp();

    const summary = await runRecipe(
      recipe([
        { id: "wait-1", action: "wait_for", condition: { text_visible: "x" }, timeout_ms: 100 },
        {
          id: "fill-1",
          action: "fill",
          targets: [{ by: "label", text: "Email" }],
          value: { kind: "constant", value: "a" },
        },
      ]),
      { row: {} },
      { cdp },
    );

    expect(summary.state).toBe("failed");
    expect(summary.error).toMatch(/refusing to start/);
    expect(summary.disposition).toBe("park");
    // No step ran: the run is refused before step 1.
    expect(summary.steps).toHaveLength(0);
    expect(callsTo("Input.dispatchKeyEvent")).toHaveLength(0);
  });

  it("reports validation problems through validateRecipe", () => {
    expect(validateRecipe(recipe([{ id: "nested-1", action: "branch" }]))).toHaveLength(1);
    expect(validateRecipe(recipe([{ id: "ok-1", action: "navigate", url: ORIGIN }]))).toEqual([]);
    expect(validateRecipe({ format_version: 1, meta: {}, steps: [] })).toHaveLength(1);
  });

  it("parks a fill whose input column has no value", async () => {
    formMarkup();
    const { cdp, callsTo } = fakeCdp();

    const summary = await runRecipe(
      recipe([
        {
          id: "fill-1",
          action: "fill",
          targets: [{ by: "label", text: "Email" }],
          value: { kind: "input", column: "missing_column" },
        },
      ]),
      { row: { email: "ravi@example.com" } },
      { cdp },
    );

    expect(summary.state).toBe("failed");
    expect(summary.disposition).toBe("park");
    expect(summary.error).toMatch(/missing_column/);
    expect(callsTo("Input.dispatchKeyEvent")).toHaveLength(0);
  });

  it("parks a fill that would type an empty value", async () => {
    formMarkup();
    const { cdp, callsTo } = fakeCdp();

    const summary = await runRecipe(
      recipe([
        {
          id: "fill-1",
          action: "fill",
          targets: [{ by: "label", text: "Email" }],
          value: { kind: "constant", value: "" },
        },
      ]),
      { row: {} },
      { cdp },
    );

    expect(summary.state).toBe("failed");
    expect(summary.disposition).toBe("park");
    expect(summary.error).toMatch(/empty value/);
    expect(callsTo("Input.dispatchKeyEvent")).toHaveLength(0);
  });

  it("parks a fill into a password field", async () => {
    document.body.innerHTML =
      "<form aria-label='Sign in'><label for='pw'>Password</label><input id='pw' type='password' data-testid='pw'></form>";
    const { cdp, callsTo } = fakeCdp();

    const summary = await runRecipe(
      recipe([
        {
          id: "fill-1",
          action: "fill",
          targets: [{ by: "testid", value: "pw" }],
          value: { kind: "constant", value: "secret" },
        },
      ]),
      { row: {} },
      { cdp },
    );

    expect(summary.state).toBe("failed");
    expect(summary.disposition).toBe("park");
    expect(summary.error).toMatch(/password/);
    expect(callsTo("Input.dispatchKeyEvent")).toHaveLength(0);
  });

  it("refuses a step that targets a frame whose context is unknown", async () => {
    formMarkup();
    const { cdp } = fakeCdp((method) => {
      if (method === "Page.getFrameTree") {
        return { frameTree: { frame: { id: "root", url: "https://app.example.com/" } } };
      }
      return undefined;
    });
    const frames = new FrameContexts();

    const summary = await runRecipe(
      recipe([
        {
          id: "fill-1",
          action: "fill",
          frame: [{ by: "index", index: 0 }],
          targets: [{ by: "label", text: "Email" }],
          value: { kind: "constant", value: "a" },
        },
      ]),
      { row: {} },
      { cdp, frames },
    );

    expect(summary.state).toBe("failed");
    expect(summary.disposition).toBe("park");
    expect(summary.error).toMatch(/frame path did not resolve|execution context/);
  });

  it("reports a commit outcome unknown when a commit is aborted mid-flight", async () => {
    formMarkup();
    const control = new RunControl();
    // Abort while the commit's mouse press is in flight: the detach watcher in
    // the service worker does exactly this when the browser detaches.
    const { cdp } = fakeCdp((method) => {
      if (method === "Input.dispatchMouseEvent") control.abort("the debugger detached");
      return undefined;
    });

    const summary = await runRecipe(
      recipe([
        {
          id: "click-1",
          action: "click",
          commit: true,
          assert_after: { text_visible: "Record created", timeout_ms: 200 },
          targets: [{ by: "role", role: "button", name: "Save" }],
        },
      ]),
      { row: {} },
      { cdp, control },
    );

    expect(summary.state).toBe("aborted");
    expect(summary.commitOutcomeUnknown).toBe(true);
    expect(summary.error).toMatch(/commit outcome unknown/);
    expect(summary.disposition).toBe("park");
  });

  it("fails when a target cannot be resolved", async () => {
    formMarkup();
    const { cdp } = fakeCdp();

    const summary = await runRecipe(
      recipe([
        {
          id: "click-1",
          action: "click",
          targets: [{ by: "role", role: "button", name: "Nonexistent" }],
        },
      ]),
      { row: {} },
      { cdp },
    );

    expect(summary.state).toBe("failed");
    expect(summary.error).toMatch(/could not be resolved/);
  });
});

describe("resolveValue", () => {
  it("reads an input column from the row", () => {
    expect(resolveValue({ kind: "input", column: "email" }, { row: { email: "a@b.test" } })).toBe(
      "a@b.test",
    );
  });

  it("returns a constant as-is", () => {
    expect(resolveValue({ kind: "constant", value: "Data course" }, { row: {} })).toBe(
      "Data course",
    );
  });

  it("reads a page variable from the run's variables", () => {
    expect(
      resolveValue({ kind: "page", variable: "price" }, { row: {}, variables: { price: 42 } }),
    ).toBe(42);
  });

  it("reports a source it cannot resolve", () => {
    expect(() => resolveValue({ kind: "document", field: "total" }, { row: {} })).toThrow(
      /cannot resolve a value source/,
    );
  });
});

describe("preparing a click on a list that re-renders", () => {
  it("finds the target again when the page replaced it between finding and measuring", async () => {
    // PL-059 golden, on Odoo: the product autocomplete redraws its options while the runner prepares
    // the pick, so the option the ladder marked was gone when its box was measured, and the row
    // parked. Nothing has been pressed at prepare time, so finding it again is safe.
    document.body.innerHTML = "<ul role='listbox'><li role='option'>Oak Standing Desk</li></ul>";
    const { cdp } = fakeCdp();
    let replaced = false;
    const send = cdp.send.bind(cdp);
    cdp.send = async (method, params = {}) => {
      const answer = await send(method, params);
      if (!replaced && method === "Runtime.evaluate" && String(params["expression"] ?? "").includes('"targets"')) {
        replaced = true;
        document.body.innerHTML = "<ul role='listbox'><li role='option'>Oak Standing Desk</li></ul>";
      }
      return answer;
    };

    const prepared = await resolveStepTarget(
      {
        id: "click-6",
        action: "click",
        targets: [{ by: "role", role: "option", name: "Oak Standing Desk" }],
        timeout_ms: 2000,
      },
      { cdp },
    );

    expect(replaced).toBe(true);
    expect(prepared.ok).toBe(true);
  });
});
