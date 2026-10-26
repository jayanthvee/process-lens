/**
 * PL-104: a run paused because the ProcessLens window was hidden has one button, right under the
 * reason: it brings the window forward and carries on.
 */

import { fireEvent, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { SHOW_WINDOW } from "../src/lib/browser";
import { SHOW_AND_CONTINUE, pauseReasonWords } from "../src/lib/words";
import { createFetchStub, renderApp, settle, type StubRoutes } from "./helpers";

const SHELL: StubRoutes = {
  "/api/v1/needs-help": { body: [] },
  "/api/v1/executor/session": { body: { attached: true } },
  "/api/v1/operator": { body: { operator_id: "0f8fad5b-d9cb-469f-a165-70867728950e" } },
};

function run(pauseReason: string): Record<string, unknown> {
  return {
    id: "run-1",
    tenant_id: "t-1",
    workflow_id: "wf-1",
    recipe_version_id: "rv-1",
    input_batch_id: "b-1",
    mode: "batch",
    status: "paused",
    pause_reason: pauseReason,
    cancel_requested_at: null,
    started_at: "2026-10-26T09:00:00Z",
    finished_at: null,
    counts: { total: 3, verified: 1, skipped: 0, parked: 0, failed: 0, in_progress: 2 },
    open_approval: null,
    waiting_behind: null,
  };
}

describe("a run paused because the ProcessLens window was hidden", () => {
  it("says so, and Show it and continue brings the window forward and resumes", async () => {
    const posted: unknown[] = [];
    const listener = (event: MessageEvent): void => {
      posted.push(event.data);
    };
    window.addEventListener("message", listener);
    const stub = createFetchStub({
      ...SHELL,
      "/api/v1/runs/run-1/records": { body: [] },
      "/api/v1/runs/run-1": { body: run("window_hidden") },
      "/api/v1/runs/run-1/resume": { body: run("window_hidden") },
    });
    renderApp("/runs/run-1");
    await settle();

    expect(screen.getByText(pauseReasonWords("window_hidden")!)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: SHOW_AND_CONTINUE }));
    await settle();

    expect(posted).toContainEqual({ type: SHOW_WINDOW });
    expect(stub.requests.some((request) => request.path.endsWith("/resume"))).toBe(true);
    window.removeEventListener("message", listener);
    stub.restore();
  });

  it("is not offered for any other pause", async () => {
    const stub = createFetchStub({
      ...SHELL,
      "/api/v1/runs/run-1/records": { body: [] },
      "/api/v1/runs/run-1": { body: run("login_required") },
    });
    renderApp("/runs/run-1");
    await settle();

    expect(screen.queryByRole("button", { name: SHOW_AND_CONTINUE })).toBeNull();
    stub.restore();
  });
});
