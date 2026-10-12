// Assertion verification: the condition predicates and the poll-to-timeout loop.
import { beforeEach, describe, expect, it } from "vitest";
import {
  conditionExpression,
  conditionTimeout,
  isTransientEvaluateError,
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
    // The runner renders the value source before it sends a condition, so the wire value is
    // already the literal (F3); the fixture carries that shape.
    const eq = conditionExpression(
      {
        value_equals: {
          selector: { targets: [{ by: "css", selector: "#email" }] },
          value: "ravi@example.com",
        },
      },
      { ...ctx, valueEquals: "ravi@example.com" },
    );
    expect(run(eq)).toBe(true);
  });

  it("refuses a value_equals with a non-string value rather than stringifying it", () => {
    // F3: stringifying a value nobody asked for could compare equal and verify a commit that
    // never happened.
    expect(() =>
      conditionExpression(
        {
          value_equals: {
            selector: { targets: [{ by: "css", selector: "#email" }] },
            value: 42,
          },
        } as unknown as Condition,
        ctx,
      ),
    ).toThrow(/string value/);
  });

  it("refuses an empty text_visible rather than matching every page", () => {
    // F3: `includes("")` is true for every page, so `text_visible: ""` would verify anything.
    expect(() => conditionExpression({ text_visible: "" }, ctx)).toThrow(/non-empty string/);
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

  it("retries a transient evaluate error, so a navigated page still verifies", async () => {
    // Requirement 4: a Save that redirects destroys the execution context mid-poll. Treating that
    // throw as "not yet" is what lets `assert_after` verify on the page the redirect landed on.
    let clock = 0;
    let attempts = 0;
    const { cdp } = fakeCdp((_method) => {
      attempts += 1;
      if (attempts === 1) {
        throw new Error("Execution context was destroyed, most likely because of a navigation.");
      }
      return { result: { value: true } };
    });

    const result = await waitForCondition(
      cdp,
      { text_visible: "Record created", timeout_ms: 1000 },
      ctx,
      { pollMs: 10, now: () => clock, sleep: async (ms) => { clock += ms; } },
    );

    expect(result).toMatchObject({ ok: true, polls: 2, transientRetries: 1 });
    expect(result.elapsedMs).toBe(10);
  });

  it("counts a transient error toward the timeout instead of refusing at once", async () => {
    let clock = 0;
    const { cdp } = fakeCdp(() => {
      throw new Error("Cannot find context with specified id");
    });

    const result = await waitForCondition(
      cdp,
      { text_visible: "never", timeout_ms: 100 },
      ctx,
      { pollMs: 40, now: () => clock, sleep: async (ms) => { clock += ms; } },
    );

    expect(result.ok).toBe(false);
    expect(result.reason ?? "").toMatch(/did not hold within 100ms/);
    expect(result.transientRetries ?? 0).toBeGreaterThanOrEqual(2);
    expect(result.transientRetries ?? 0).toBe(result.polls);
  });

  it("propagates a persistent evaluate error, so a dead context still refuses", async () => {
    const { cdp } = fakeCdp(() => {
      throw new Error("Target closed");
    });

    await expect(
      waitForCondition(
        cdp,
        { text_visible: "x", timeout_ms: 1000 },
        ctx,
        { pollMs: 10, now: () => 0, sleep: async () => {} },
      ),
    ).rejects.toThrow(/Target closed/);
  });
});

describe("isTransientEvaluateError", () => {
  it("recognises the execution-context errors a navigation throws", () => {
    expect(isTransientEvaluateError(new Error("Execution context was destroyed."))).toBe(true);
    expect(isTransientEvaluateError("Cannot find context with specified id")).toBe(true);
    expect(isTransientEvaluateError(new Error("context WAS DESTROYED"))).toBe(true);
  });

  it("does not call a persistent failure transient", () => {
    expect(isTransientEvaluateError(new Error("Target closed"))).toBe(false);
    expect(
      isTransientEvaluateError(new Error("the executor does not understand the condition: {}")),
    ).toBe(false);
    expect(isTransientEvaluateError(undefined)).toBe(false);
  });
});
