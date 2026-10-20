/**
 * PL-061: the real manual-versus-automated time on the two screens the brief names.
 *
 * The line under a finished run's outcome compares what one row costs by hand (measured from the
 * demonstration) with what ProcessLens costs, and the Home figure uses the API's own saved
 * seconds, saying when part of it had to be estimated.
 */

import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { estimatedRowsNote, secondsWords, timeComparisonWords } from "../src/lib/words";
import {
  createFetchStub,
  renderApp,
  settle,
  type FetchStub,
  type StubRoutes,
} from "./helpers";

const SHELL: StubRoutes = {
  "/api/v1/needs-help": { body: [] },
  "/api/v1/executor/session": { body: { attached: false } },
  "/api/v1/operator": { body: { operator_id: "0f8fad5b-d9cb-469f-a165-70867728950e" } },
};

function homeBody(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    window_days: 7,
    counts: { done: 412, already_there: 3, needs_help: 0 },
    manual_seconds: 50_000,
    automated_seconds: 2_000,
    saved_seconds: 2_040,
    estimated_rows: 0,
    minutes_saved: 34,
    needs_help: [],
    running: [],
    tasks: [],
    ...overrides,
  };
}

function runBody(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    id: "run-1",
    tenant_id: "t-1",
    workflow_id: "wf-1",
    recipe_version_id: "rv-1",
    input_batch_id: "b-1",
    mode: "batch",
    status: "completed",
    pause_reason: null,
    cancel_requested_at: null,
    started_at: "2026-10-15T09:00:00Z",
    finished_at: "2026-10-15T09:05:00Z",
    counts: { total: 20, verified: 20, skipped: 0, parked: 0, failed: 0, in_progress: 0 },
    open_approval: null,
    waiting_behind: null,
    manual_seconds_per_row: 130,
    automated_seconds_per_row: 14,
    saved_seconds: 1_860,
    ...overrides,
  };
}

function renderRun(detail: Record<string, unknown>): FetchStub {
  const stub = createFetchStub({
    ...SHELL,
    "/api/v1/runs/run-1/records": { body: [] },
    "/api/v1/runs/run-1": { body: detail },
    "/api/v1/runs": { body: [] },
  });
  renderApp("/runs/run-1");
  return stub;
}

const MEASURED_LINE =
  "By hand: about 2 min 10 s a row · ProcessLens: 14 s a row · About 31 minutes saved";

describe("the manual-versus-automated words", () => {
  it("says a short stretch of time the way a person would", () => {
    expect(secondsWords(14)).toBe("14 s");
    expect(secondsWords(130)).toBe("2 min 10 s");
    expect(secondsWords(180)).toBe("3 min");
    expect(secondsWords(0)).toBe("0 s");
  });

  it("builds the comparison, and the estimated version when nothing was measured", () => {
    expect(
      timeComparisonWords({
        manualSecondsPerRow: 130,
        automatedSecondsPerRow: 14,
        savedSeconds: 1_860,
      }),
    ).toBe(MEASURED_LINE);
    expect(
      timeComparisonWords({
        manualSecondsPerRow: null,
        automatedSecondsPerRow: null,
        savedSeconds: null,
      }),
    ).toBe("By hand: about 3 min a row (estimated)");
  });

  it("counts the estimated rows, and says nothing when there are none", () => {
    expect(estimatedRowsNote(3)).toBe("estimated for 3 rows");
    expect(estimatedRowsNote(1)).toBe("estimated for 1 row");
    expect(estimatedRowsNote(0)).toBeNull();
  });
});

describe("the results screen's comparison line", () => {
  it("shows the measured line under the outcome", async () => {
    const stub = renderRun(runBody());
    await settle();

    expect(screen.getByText(MEASURED_LINE)).toBeTruthy();
    stub.restore();
  });

  it("says the manual figure is an estimate when the task never measured itself", async () => {
    const stub = renderRun(
      runBody({
        manual_seconds_per_row: null,
        automated_seconds_per_row: null,
        saved_seconds: null,
      }),
    );
    await settle();

    expect(screen.getByText("By hand: about 3 min a row (estimated)")).toBeTruthy();
    stub.restore();
  });

  it("shows no comparison while the run is still going", async () => {
    const stub = renderRun(
      runBody({
        status: "running",
        finished_at: null,
        counts: { total: 20, verified: 4, skipped: 0, parked: 0, failed: 0, in_progress: 16 },
        manual_seconds_per_row: null,
        automated_seconds_per_row: null,
        saved_seconds: null,
      }),
    );
    await settle();

    expect(screen.queryByText(/By hand:/)).toBeNull();
    stub.restore();
  });
});

describe("the Home figure", () => {
  it("uses the API's saved seconds and shows no estimate note when everything was measured", async () => {
    const stub = createFetchStub({ ...SHELL, "/api/v1/home": { body: homeBody() } });
    renderApp("/");
    await settle();

    expect(screen.getByText("34 m")).toBeTruthy();
    expect(screen.queryByText(/estimated for/)).toBeNull();
    stub.restore();
  });

  it("says how many rows were estimated", async () => {
    const stub = createFetchStub({
      ...SHELL,
      "/api/v1/home": { body: homeBody({ estimated_rows: 12 }) },
    });
    renderApp("/");
    await settle();

    expect(screen.getByText("estimated for 12 rows")).toBeTruthy();
    stub.restore();
  });
});
