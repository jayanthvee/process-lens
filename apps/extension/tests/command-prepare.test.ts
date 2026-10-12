// The prepare phase of a command step (PL-014b item 10) and the navigate guard rule (item 5).
//
// These drive the REAL step body — the guards, the resolver, and the destructive check — through
// the shared fake CDP, so what is asserted is what the service worker actually runs, not a copy of
// it re-implemented in the test.
//
// The item-10 contract is absolute: a prepare sends no `Input.*` call at all. A prepare proves the
// target resolves and passes every guard, and nothing more; the runner writes `submitting` only
// after it returns, so a prepare that typed or clicked would make a mere locator failure look like
// a commit attempt and dispatch every commit twice.
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { dispatchCommandStep, prepareCommandStep } from "../src/background/command-step";
import type { ExecutorStep } from "../src/executor/types";
import { fakeCdp, type FakeCdp } from "./helpers/fake-cdp";
import { installLayout } from "./helpers/layout";

let restoreLayout: (() => void) | null = null;

beforeEach(() => {
  document.body.innerHTML = "";
  document.title = "Test page";
  restoreLayout = installLayout();
});

afterEach(() => {
  restoreLayout?.();
  restoreLayout = null;
});

/** Every `Input.*` call the fake recorded; a prepare must produce an empty list. */
function inputCalls(fake: FakeCdp): string[] {
  return fake.calls
    .filter((call) => call.method.startsWith("Input."))
    .map((call) => call.method);
}

const PAGE = `
  <label for="email">Email</label>
  <input id="email" name="email" type="text">
  <button id="save">Save</button>
  <select id="plan" data-testid="plan"><option>Basic</option></select>
`;

function fillStep(): ExecutorStep {
  return {
    id: "fill-1",
    action: "fill",
    value: { kind: "constant", value: "ada@example.test" },
    targets: [{ by: "label", text: "Email" }],
  };
}

function clickStep(): ExecutorStep {
  return {
    id: "click-1",
    action: "click",
    targets: [{ by: "role", role: "button", name: "Save" }],
  };
}

function selectStep(): ExecutorStep {
  return {
    id: "select-1",
    action: "select",
    value: { kind: "constant", value: "Basic" },
    targets: [{ by: "testid", value: "plan" }],
  };
}

describe("prepareCommandStep sends no input (item 10)", () => {
  it("a fill prepare resolves the target and sends no Input.* call", async () => {
    document.body.innerHTML = PAGE;
    const fake = fakeCdp();

    const report = await prepareCommandStep(fillStep(), {
      cdp: fake.cdp,
      allowedOrigins: [window.location.origin],
    });

    expect(report.outcome).toBe("done");
    // The label rung resolved, so the prepare proved the target is reachable.
    expect(report.rung).toBeGreaterThan(0);
    expect(inputCalls(fake)).toEqual([]);
  });

  it("a click prepare hit-tests the target and sends no Input.* call", async () => {
    document.body.innerHTML = PAGE;
    const fake = fakeCdp();

    const report = await prepareCommandStep(clickStep(), {
      cdp: fake.cdp,
      allowedOrigins: [window.location.origin],
    });

    expect(report.outcome).toBe("done");
    expect(report.rung).toBeGreaterThan(0);
    expect(inputCalls(fake)).toEqual([]);
  });

  it("a select prepare resolves the target and sends no Input.* call", async () => {
    document.body.innerHTML = PAGE;
    const fake = fakeCdp();

    const report = await prepareCommandStep(selectStep(), {
      cdp: fake.cdp,
      allowedOrigins: [window.location.origin],
    });

    expect(report.outcome).toBe("done");
    expect(report.rung).toBeGreaterThan(0);
    expect(inputCalls(fake)).toEqual([]);
  });

  it("a navigate prepare sends no Input.* call and does not navigate", async () => {
    document.body.innerHTML = PAGE;
    const fake = fakeCdp();

    const report = await prepareCommandStep(
      { id: "nav-1", action: "navigate", url: `${window.location.origin}/leads` },
      { cdp: fake.cdp, allowedOrigins: [window.location.origin] },
    );

    expect(report.outcome).toBe("done");
    expect(inputCalls(fake)).toEqual([]);
    // A prepare is a proof, not an action: it must not move the person's tab either.
    expect(fake.callsTo("Page.navigate")).toEqual([]);
  });
});

describe("the pre-command navigate guard rule (item 5)", () => {
  it("a navigate to an allowed origin is not refused against an off-origin page, but a click still is", async () => {
    // The regression: after record A parks off-origin, record B's opening navigate was refused by
    // the origin guard against record A's stale page. The navigate's own target origin is what
    // must be judged, and it is checked against allowed_origins inside the navigate itself.
    document.body.innerHTML = PAGE;
    const allowed = ["https://portal.test"];

    // The tab is on the off-origin page the previous record parked on (its origin is not allowed).
    await expect(
      prepareCommandStep(
        { id: "nav-1", action: "navigate", url: "https://portal.test/leads" },
        { cdp: fakeCdp().cdp, allowedOrigins: allowed },
      ),
    ).resolves.toMatchObject({ outcome: "done" });

    // A non-navigate step keeps today's order: the origin guard refuses against the current page,
    // before any input.
    const clickFake = fakeCdp();
    await expect(
      prepareCommandStep(clickStep(), { cdp: clickFake.cdp, allowedOrigins: allowed }),
    ).rejects.toMatchObject({ event: "off_origin" });
    expect(inputCalls(clickFake)).toEqual([]);
  });

  it("a navigate dispatch runs the guards on the page it lands on, after the navigate", async () => {
    // Item 5: the guards must judge the landed page, not the page the tab left. The order of the
    // recorded calls proves it — Page.navigate first, then the origin probe.
    const fake = fakeCdp((method, params) => {
      if (method === "Runtime.evaluate" && String(params["expression"]) === "location.origin") {
        return { result: { value: "https://portal.test" } };
      }
      return undefined;
    });

    const report = await dispatchCommandStep(
      { id: "nav-1", action: "navigate", url: "https://portal.test/leads", timeout_ms: 1_000 },
      { cdp: fake.cdp, allowedOrigins: ["https://portal.test"] },
    );

    expect(report.outcome).toBe("done");
    const navigateAt = fake.calls.findIndex((call) => call.method === "Page.navigate");
    const originAt = fake.calls.findIndex(
      (call) =>
        call.method === "Runtime.evaluate" && String(call.params["expression"]) === "location.origin",
    );
    expect(navigateAt).toBeGreaterThanOrEqual(0);
    expect(originAt).toBeGreaterThan(navigateAt);
    expect(inputCalls(fake)).toEqual([]);
  });

  it("a navigate whose landed page is off-origin is refused after the navigate happened", async () => {
    // The navigate's target URL was allowed, so it was performed; the landed page is not, so the
    // guard refuses there. That is the "guards run on the page the navigate lands on" rule.
    const fake = fakeCdp((method, params) => {
      if (method === "Runtime.evaluate" && String(params["expression"]) === "location.origin") {
        return { result: { value: "https://evil.test" } };
      }
      return undefined;
    });

    await expect(
      dispatchCommandStep(
        { id: "nav-1", action: "navigate", url: "https://portal.test/leads", timeout_ms: 1_000 },
        { cdp: fake.cdp, allowedOrigins: ["https://portal.test"] },
      ),
    ).rejects.toMatchObject({ event: "off_origin" });

    expect(fake.callsTo("Page.navigate")).toHaveLength(1);
  });
});
