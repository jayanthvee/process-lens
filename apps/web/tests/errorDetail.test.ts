/**
 * Refusals: a 409's `detail` text appears next to the button that caused it, the page never retries
 * on its own, and a 503 from the operator endpoint disables what needs the id.
 */

import { describe, expect, it } from "vitest";
import { mountApp } from "../src/app";
import type { RecordRow } from "../src/types";
import {
  OPERATOR_ID,
  callsTo,
  createFetchStub,
  sampleApproval,
  sampleRecord,
  sampleRunDetail,
  settle,
  type RecordedCall,
} from "./helpers";

const CONFLICT = "record record-1 is parked and commit_attempted needs a destination check";
const OPERATOR_503 = "PROCESSLENS_OPERATOR_ID is not set";

function runResponses(
  record: RecordRow,
  extra?: (call: RecordedCall) => { status: number; body: unknown } | undefined,
) {
  return (call: RecordedCall): { status: number; body: unknown } => {
    const handled = extra?.(call);
    if (handled !== undefined) return handled;
    if (call.path === "/api/v1/operator") return { status: 200, body: { operator_id: OPERATOR_ID } };
    if (call.path === "/api/v1/runs/run-1") return { status: 200, body: sampleRunDetail() };
    if (call.path === "/api/v1/runs/run-1/records") return { status: 200, body: [record] };
    if (call.path === "/api/v1/runs/run-1/approvals") return { status: 200, body: [sampleApproval()] };
    if (call.path.startsWith("/api/v1/runs/run-1/transitions")) return { status: 200, body: [] };
    return { status: 404, body: { detail: `unexpected ${call.method} ${call.path}` } };
  };
}

describe("a refused request shows the API's detail text next to the button", () => {
  it("shows a 409 from Dismiss beside the Dismiss button and does not retry", async () => {
    const record = sampleRecord({ state: "parked", commit_attempted: false });
    const { fetchImpl, calls } = createFetchStub((call) => {
      const handled = runResponses(record)(call);
      if (handled.status === 404 && call.path === "/api/v1/records/record-1/dismiss") {
        return { status: 409, body: { detail: CONFLICT } };
      }
      return handled;
    });

    const root = document.createElement("div");
    document.body.appendChild(root);
    const app = mountApp(root, { fetchImpl });
    await app.openRun("run-1");

    const dismiss = root.querySelector<HTMLButtonElement>(".record-dismiss");
    expect(dismiss).not.toBeNull();
    dismiss?.click();
    await settle();

    const cell = root.querySelector<HTMLElement>(".record-actions");
    const error = cell?.querySelector<HTMLElement>(".action-error");
    expect(error?.textContent).toBe(CONFLICT);
    // "next to the button": the same cell the Dismiss button lives in.
    expect(root.querySelector<HTMLButtonElement>(".record-dismiss")?.closest("td")).toBe(cell);

    const before = callsTo(calls, "POST", "/api/v1/records/record-1/dismiss").length;
    expect(before).toBe(1);
    await settle();
    expect(callsTo(calls, "POST", "/api/v1/records/record-1/dismiss").length).toBe(1);
    app.destroy();
  });

  it("shows a 409 from Pause beside the Pause button", async () => {
    const record = sampleRecord({ state: "verified", commit_attempted: false });
    const { fetchImpl } = createFetchStub((call) => {
      if (call.path === "/api/v1/runs/run-1/pause") {
        return { status: 409, body: { detail: "run run-1 is completed" } };
      }
      return runResponses(record)(call);
    });

    const root = document.createElement("div");
    document.body.appendChild(root);
    const app = mountApp(root, { fetchImpl });
    await app.openRun("run-1");

    root.querySelector<HTMLButtonElement>(".run-action-pause")?.click();
    await settle();

    const bar = root.querySelector<HTMLElement>(".run-actions");
    expect(bar?.querySelector(".action-error")?.textContent).toBe("run run-1 is completed");
    expect(root.querySelector<HTMLButtonElement>(".run-action-pause")?.closest("p")).toBe(bar);
    app.destroy();
  });
});

describe("the operator identity", () => {
  it("disables approvals and says why when the endpoint answers 503", async () => {
    const record = sampleRecord({ state: "parked", commit_attempted: true });
    const { fetchImpl } = createFetchStub((call) => {
      if (call.path === "/api/v1/operator") return { status: 503, body: { detail: OPERATOR_503 } };
      return runResponses(record)(call);
    });

    const root = document.createElement("div");
    document.body.appendChild(root);
    const app = mountApp(root, { fetchImpl });
    await app.openRun("run-1");

    expect(app.status().operatorId).toBeNull();
    const approve = root.querySelector<HTMLButtonElement>(".approval-approved");
    const reject = root.querySelector<HTMLButtonElement>(".approval-rejected");
    expect(approve?.disabled).toBe(true);
    expect(reject?.disabled).toBe(true);
    // It refuses to act, and it says why — including the API's own words.
    const note = root.querySelector<HTMLElement>(".operator-problem");
    expect(note?.textContent).toContain(OPERATOR_503);
    expect(root.querySelector<HTMLElement>(".operator-note")?.textContent).toContain(
      "PROCESSLENS_OPERATOR_ID",
    );
    // A commit-attempted record also needs the id, so its buttons stay disabled too.
    expect(root.querySelector<HTMLButtonElement>(".record-dismiss")?.disabled).toBe(true);
    app.destroy();
  });

  it("answers an approval with the API's operator id", async () => {
    const record = sampleRecord({ state: "verified", commit_attempted: false });
    const { fetchImpl, calls } = createFetchStub((call) => {
      if (call.path === "/api/v1/approvals") return { status: 200, body: { ok: true } };
      return runResponses(record)(call);
    });

    const root = document.createElement("div");
    document.body.appendChild(root);
    const app = mountApp(root, { fetchImpl });
    await app.openRun("run-1");

    root.querySelector<HTMLButtonElement>(".approval-approved")?.click();
    await settle();

    const posts = callsTo(calls, "POST", "/api/v1/approvals");
    expect(posts).toHaveLength(1);
    expect(posts[0]?.body).toEqual({
      request_id: "approval-1",
      decision: "approved",
      decided_by: OPERATOR_ID,
    });
    app.destroy();
  });
});
