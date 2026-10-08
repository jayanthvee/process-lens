// Assertion verification: the condition predicates and the poll-to-timeout loop.
import { beforeEach, describe, expect, it } from "vitest";
import {
  conditionExpression,
  conditionTimeout,
  waitForCondition,
} from "../src/executor/assertions";
import { resolverExpression } from "../src/executor/ladder";
import type { Condition } from "../src/shared/recipe-types";
import { fakeCdp } from "./helpers/fake-cdp";

const ctx = { resolverSource: resolverExpression() };

function run(expression: string): unknown {
  return new Function(`return (${expression})`)();
}

beforeEach(() => {
  document.body.innerHTML = "";
  window.history.replaceState({}, "", "/records/new");
});

describe("condition predicates", () => {
  it("text_visible is true when the page shows the text", () => {
    document.body.textContent = "Record created";
    expect(run(conditionExpression({ text_visible: "Record created" }, ctx))).toBe(true);
  });

  it("text_visible is false when the text is absent", () => {
    document.body.textContent = "Something else";
    expect(run(conditionExpression({ text_visible: "Record created" }, ctx))).toBe(false);
  });

  it("text_absent is true when the text is gone", () => {
    document.body.textContent = "Saved";
    expect(run(conditionExpression({ text_absent: "Error" }, ctx))).toBe(true);
  });

  it("url_matches tests the current URL as a pattern", () => {
    window.history.replaceState({}, "", "/records/42");
    expect(run(conditionExpression({ url_matches: "/records/" }, ctx))).toBe(true);
    expect(run(conditionExpression({ url_matches: "/invoices/" }, ctx))).toBe(false);
  });

  it("element_present resolves the selector's target ladder", () => {
    document.body.innerHTML = "<div role='alert'>Saved</div>";
    const present = conditionExpression(
      { element_present: { targets: [{ by: "role", role: "alert", name: "Saved" }] } },
      ctx,
    );
    expect(run(present)).toBe(true);
  });

  it("element_absent is true when the ladder finds nothing", () => {
    document.body.innerHTML = "<div>Nothing here</div>";
    const absent = conditionExpression(
      { element_absent: { targets: [{ by: "role", role: "alert", name: "Saved" }] } },
      ctx,
    );
    expect(run(absent)).toBe(true);
  });

  it("value_equals compares a field's value", () => {
    document.body.innerHTML = "<input id='email' value='ravi@example.com'>";
    const eq = conditionExpression(
      {
        value_equals: {
          selector: { targets: [{ by: "css", selector: "#email" }] },
          value: { kind: "constant", value: "ravi@example.com" },
        },
      },
      { ...ctx, valueEquals: "ravi@example.com" },
    );
    expect(run(eq)).toBe(true);
  });

  it("refuses a predicate it does not understand", () => {
    expect(() => conditionExpression({ made_up: true } as Condition, ctx)).toThrow(
      /does not understand the condition/,
    );
  });

  it("reads a condition's own timeout, falling back to the default", () => {
    expect(conditionTimeout({ text_visible: "x", timeout_ms: 500 }, 8000)).toBe(500);
    expect(conditionTimeout({ text_visible: "x" }, 8000)).toBe(8000);
  });
});

describe("waitForCondition", () => {
  it("returns immediately when the condition already holds", async () => {
    let clock = 0;
    const { cdp } = fakeCdp(() => ({ result: { value: true } }));

    const result = await waitForCondition(
      cdp,
      { text_visible: "x", timeout_ms: 1000 },
      ctx,
      { now: () => clock, sleep: async (ms) => { clock += ms; } },
    );

    expect(result).toMatchObject({ ok: true, polls: 1 });
    expect(result.elapsedMs).toBe(0);
  });

  it("polls until the condition becomes true", async () => {
    const answers = [false, false, true];
    let clock = 0;
    const { cdp } = fakeCdp(() => ({ result: { value: answers.shift() ?? true } }));

    const result = await waitForCondition(
      cdp,
      { text_visible: "x", timeout_ms: 1000 },
      ctx,
      { pollMs: 10, now: () => clock, sleep: async (ms) => { clock += ms; } },
    );

    expect(result.ok).toBe(true);
    expect(result.polls).toBe(3);
    expect(result.elapsedMs).toBe(20);
  });

  it("fails closed on timeout, after the last poll", async () => {
    let clock = 0;
    const { cdp } = fakeCdp(() => ({ result: { value: false } }));

    const result = await waitForCondition(
      cdp,
      { text_visible: "never", timeout_ms: 100 },
      ctx,
      { pollMs: 40, now: () => clock, sleep: async (ms) => { clock += ms; } },
    );

    expect(result.ok).toBe(false);
    expect(result.polls).toBeGreaterThanOrEqual(2);
    expect(result.elapsedMs).toBeGreaterThanOrEqual(100);
  });
});
