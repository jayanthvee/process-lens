/**
 * PL-080: the pace a person picks when they start a run.
 *
 * The three choices are one radio group on the run's start screen; the numbers ride the run's
 * create call only when the person moved off the default, so "as fast as the site allows" keeps
 * today's request exactly as it was. The run page then says which pace is set, in one line.
 */

import { act, fireEvent, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { PACE_CHOICES, paceWords } from "../src/lib/words";
import type { BatchCheck, BatchUpload, RunDetail as RunPayload, TaskRow } from "../src/types";
import {
  createFetchStub,
  renderApp,
  settle,
  type FetchStub,
  type RecordedRequest,
} from "./helpers";

const SHELL = {
  "/api/v1/needs-help": { body: [] },
  "/api/v1/executor/session": { body: { attached: false } },
  "/api/v1/operator": { body: { operator_id: "0f8fad5b-d9cb-469f-a165-70867728950e" } },
};

const OPERATOR_ID = "0f8fad5b-d9cb-469f-a165-70867728950e";

const TASK: TaskRow = {
  workflow_id: "wf-1",
  recipe_version_id: "rv-1",
  name: "Add inquiries",
  app: "crm.example.com",
  last_run: null,
};

const UPLOAD: BatchUpload = {
  batch_id: "batch-1",
  row_count: 3,
  columns: ["Name", "Email"],
};

const CHECK: BatchCheck = {
  batch_id: "batch-1",
  row_count: 3,
  columns: UPLOAD.columns,
  recipe_name: "Add inquiries",
  mapping: [],
  rows_ok: 3,
  rows_need_a_look: [],
  first_ready: { row: 1, values: { email: "priya@example.com" } },
};

let stub: FetchStub | null = null;

afterEach(() => {
  stub?.restore();
  stub = null;
});

function runDetail(overrides: Partial<RunPayload> = {}): RunPayload {
  return {
    id: "run-preview",
    status: "queued",
    pause_reason: null,
    counts: { total: 1 },
    open_approval: null,
    started_at: null,
    finished_at: null,
    ...overrides,
  };
}

function harness(extra: Record<string, unknown> = {}): void {
  stub = createFetchStub({
    ...SHELL,
    "/api/v1/tasks": { body: [TASK] },
    "/api/v1/batches/batch-1/check": { body: CHECK },
    "/api/v1/batches/batch-1/mapping": { body: { mapping: {} } },
    "/api/v1/batches": { body: UPLOAD },
    "/api/v1/runs": (call: RecordedRequest) => ({
      body: {
        run_id: (call.body as { mode?: string } | null)?.mode === "batch" ? "run-batch" : "run-preview",
        status: "queued",
      },
    }),
    "/api/v1/runs/run-preview": { body: runDetail() },
    "/api/v1/runs/run-preview/records": { body: [] },
    ...extra,
  });
}

/** Add the spreadsheet and press Continue, so the page is on step 3 where a run starts. */
async function reachTryIt(view: ReturnType<typeof renderApp>): Promise<void> {
  const input = view.container.querySelector<HTMLInputElement>("input[type=file]");
  expect(input).not.toBeNull();
  fireEvent.change(input as HTMLInputElement, {
    target: { files: [new File(["Name,Email\n"], "people.csv", { type: "text/csv" })] },
  });
  await settle();
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
  });
  await settle();
}

function runRequests(): RecordedRequest[] {
  return (stub as FetchStub).requests.filter(
    (call) => call.method === "POST" && call.path === "/api/v1/runs",
  );
}

describe("the pace choice", () => {
  it("offers three choices, with the fast one chosen", async () => {
    harness();
    const view = renderApp("/tasks/wf-1/run");
    await settle();
    await reachTryIt(view);

    expect(screen.getByRole("group", { name: "Pace" })).toBeTruthy();
    const radios = screen.getAllByRole("radio") as HTMLInputElement[];
    expect(radios).toHaveLength(PACE_CHOICES.length);
    for (const choice of PACE_CHOICES) {
      expect(screen.getByText(choice.label)).toBeTruthy();
      expect(screen.getByText(choice.explanation)).toBeTruthy();
    }
    expect(radios[0]?.checked).toBe(true);
  });

  it("sends nothing extra when the person leaves the site's own speed alone", async () => {
    harness();
    const view = renderApp("/tasks/wf-1/run");
    await settle();
    await reachTryIt(view);

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Try it" }));
    });
    await settle();

    const posted = runRequests();
    expect(posted).toHaveLength(1);
    expect(posted[0]?.body).toEqual({
      workflow_id: "wf-1",
      recipe_version_id: "rv-1",
      input_batch_id: "batch-1",
      mode: "preview",
      limit: 1,
    });
  });

  it("sends the steady pace's numbers with the run", async () => {
    harness();
    const view = renderApp("/tasks/wf-1/run");
    await settle();
    await reachTryIt(view);

    fireEvent.click(screen.getByRole("radio", { name: /Steady/ }));
    await settle();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Try it" }));
    });
    await settle();

    const posted = runRequests();
    expect(posted).toHaveLength(1);
    expect(posted[0]?.body).toEqual({
      workflow_id: "wf-1",
      recipe_version_id: "rv-1",
      input_batch_id: "batch-1",
      mode: "preview",
      limit: 1,
      min_seconds_between_rows: 10,
      min_seconds_between_steps: 2,
    });
  });

  it("sends the careful pace's numbers with the rest of the rows", async () => {
    harness({
      "/api/v1/runs/run-preview": { body: runDetail({ status: "completed", counts: { total: 3, verified: 1 } }) },
      "/api/v1/runs/run-batch": { body: runDetail({ id: "run-batch", status: "queued" }) },
      "/api/v1/runs/run-batch/records": { body: [] },
    });
    const view = renderApp("/tasks/wf-1/run");
    await settle();
    await reachTryIt(view);

    fireEvent.click(screen.getByRole("radio", { name: /Careful/ }));
    await settle();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Try it" }));
    });
    await settle();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Run the other 2 rows" }));
    });
    await settle();

    const batch = runRequests().find(
      (call) => (call.body as { mode?: string } | null)?.mode === "batch",
    );
    expect(batch?.body).toEqual({
      workflow_id: "wf-1",
      recipe_version_id: "rv-1",
      input_batch_id: "batch-1",
      mode: "batch",
      min_seconds_between_rows: 30,
      min_seconds_between_steps: 5,
    });
  });
});

describe("the run page's pace line", () => {
  it("names the pace the run was started with", async () => {
    harness({
      "/api/v1/runs/run-1": {
        body: runDetail({ id: "run-1", min_seconds_between_rows: 10, min_seconds_between_steps: 2 }),
      },
      "/api/v1/runs/run-1/records": { body: [] },
    });
    renderApp("/runs/run-1");
    await settle();

    expect(screen.getByText(paceWords(10, 2))).toBeTruthy();
    expect(screen.getByText(/Steady/)).toBeTruthy();
  });

  it("says a run with no pace goes as fast as the site allows", async () => {
    harness({
      "/api/v1/runs/run-1": { body: runDetail({ id: "run-1" }) },
      "/api/v1/runs/run-1/records": { body: [] },
    });
    renderApp("/runs/run-1");
    await settle();

    expect(screen.getByText(/as fast as the site allows/)).toBeTruthy();
  });
});

describe("the pace words", () => {
  it("names each choice and its numbers", () => {
    expect(paceWords(0, 0)).toBe("Pace: as fast as the site allows.");
    expect(paceWords(10, 2)).toContain("Steady");
    expect(paceWords(10, 2)).toContain("10 s between rows");
    expect(paceWords(30, 5)).toContain("Careful");
    // A run started before this ticket has no numbers; a pace set by hand still reads plainly.
    expect(paceWords(12, 3)).toBe("Pace: 12 s between rows, 3 s between steps.");
  });
});
