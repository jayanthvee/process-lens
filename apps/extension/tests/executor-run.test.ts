// The executor end to end: a compiled recipe replayed against a page.
//
// The fake transport evaluates page expressions in jsdom, so the real 5-rung
// resolver and the real condition predicates run here; only the browser input
// itself is recorded rather than performed.
import { afterEach, beforeEach, describe, expect, it } from "vitest";
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

describe("runRecipe", () => {
  it("replays a fill, a select, and a committing click", async () => {
    formMarkup();
    document.body.append(Object.assign(document.createElement("div"), { textContent: "Record created" }));
    const { cdp, callsTo } = fakeCdp();

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
    expect((document.getElementById("email") as HTMLInputElement).value).toBe("");
    expect((document.getElementById("interest") as HTMLSelectElement).value).toBe("Data course");
    // The committing click was resolved by rung 1 (role + name).
    expect(summary.steps[3]).toMatchObject({ rung: 0, by: "role" });
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
