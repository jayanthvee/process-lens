/**
 * PL-041 part 2: one click, one run.
 *
 * A double click on **Try it** used to create two preview runs, and **Run the other N rows** and
 * **Try all N again** could do the same. `busy` state alone is too late -- a second click lands
 * before React re-renders -- so each button carries a `useRef` in-flight flag that is set before
 * the request and cleared in `finally`.
 *
 * Each test clicks twice inside one `act` with the DOM's own `click()`, so no render happens
 * between the two clicks: exactly the race a person's double click produces.
 */

import { act, fireEvent, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type {
  BatchCheck,
  BatchUpload,
  RecordRow,
  RunDetail as RunPayload,
  TaskRow,
} from "../src/types";
import {
  createFetchStub,
  renderApp,
  settle,
  type FetchStub,
  type RecordedRequest,
  type StubRoutes,
} from "./helpers";

const OPERATOR_ID = "0f8fad5b-d9cb-469f-a165-70867728950e";

/* Every path the Shell asks for as well, so a page test never hits the real API. */
const SHELL = {
  "/api/v1/needs-help": { body: [] },
  "/api/v1/executor/session": { body: { attached: true } },
  "/api/v1/operator": { body: { operator_id: OPERATOR_ID } },
};

let stub: FetchStub | null = null;

afterEach(() => {
  stub?.restore();
  stub = null;
});

function posts(path: string): RecordedRequest[] {
  return (stub as FetchStub).requests.filter(
    (call) => call.method === "POST" && call.path === path,
  );
}

function button(label: string): HTMLButtonElement {
  return screen.getByRole("button", { name: label }) as HTMLButtonElement;
}

/** Two clicks with no render in between: the second one lands before React knows. */
async function doubleClick(element: HTMLElement): Promise<void> {
  await act(async () => {
    element.click();
    element.click();
  });
  await settle();
}

/* ------------------------------------------------------------------ */
/* The run flow                                                        */
/* ------------------------------------------------------------------ */

const TASK: TaskRow = {
  workflow_id: "wf-1",
  recipe_version_id: "rv-1",
  name: "Add inquiries",
  app: "crm.example.com",
  last_run: null,
};

const UPLOAD: BatchUpload = {
  batch_id: "batch-1",
  row_count: 20,
  columns: ["Name", "Email"],
};

const CHECK: BatchCheck = {
  batch_id: "batch-1",
  row_count: 20,
  columns: UPLOAD.columns,
  recipe_name: "Add inquiries",
  mapping: [
    {
      column: "email",
      label: "Email",
      chosen: "Email",
      confidence: "exact",
      required: true,
      alternatives: [],
    },
  ],
  rows_ok: 20,
  rows_need_a_look: [],
  first_ready: { row: 1, values: { email: "priya@example.com" } },
};

function runDetail(overrides: Partial<RunPayload> = {}): RunPayload {
  return {
    id: "run-preview",
    status: "completed",
    pause_reason: null,
    counts: { total: 1, verified: 1 },
    open_approval: null,
    started_at: null,
    finished_at: null,
    waiting_behind: null,
    ...overrides,
  };
}

/** Walk the flow to the "3. Try one row" card, so its buttons are on screen. */
async function readyToTry(): Promise<void> {
  const routes: StubRoutes = {
    ...SHELL,
    "/api/v1/tasks": { body: [TASK] },
    "/api/v1/batches/batch-1/check": { body: CHECK },
    "/api/v1/batches/batch-1/mapping": { body: { batch_id: "batch-1", mapping: {} } },
    "/api/v1/batches": { body: UPLOAD },
    // Each POST is its own run, so a double click is visible as two of them.
    "/api/v1/runs": (call: RecordedRequest) => ({
      body: {
        run_id:
          (call.body as { mode?: string } | null)?.mode === "batch" ? "run-batch" : "run-preview",
        status: "queued",
      },
    }),
    "/api/v1/runs/run-preview": { body: runDetail() },
    "/api/v1/runs/run-preview/records": { body: [] },
    "/api/v1/runs/run-batch": { body: runDetail({ id: "run-batch" }) },
    "/api/v1/runs/run-batch/records": { body: [] },
  };
  stub = createFetchStub(routes);
  const view = renderApp("/tasks/wf-1/run");
  await settle();

  const input = view.container.querySelector<HTMLInputElement>("input[type=file]");
  const file = new File(["Email\npriya@example.com\n"], "people.csv", { type: "text/csv" });
  fireEvent.change(input as HTMLInputElement, { target: { files: [file] } });
  await settle();
  await act(async () => {
    fireEvent.click(button("Continue"));
  });
  await settle();
}

describe("the run flow's buttons start one run each", () => {
  it("a double click on Try it sends one preview run, not two", async () => {
    await readyToTry();

    await doubleClick(button("Try it"));

    const sent = posts("/api/v1/runs");
    expect(sent).toHaveLength(1);
    expect(sent[0]?.body).toMatchObject({ mode: "preview", limit: 1 });
    // And the page followed the one run it started.
    expect(button("Run the other 19 rows")).toBeTruthy();
  });

  it("a double click on Run the other rows sends one batch run, not two", async () => {
    await readyToTry();
    await act(async () => {
      fireEvent.click(button("Try it"));
    });
    await settle();

    await doubleClick(button("Run the other 19 rows"));

    const sent = posts("/api/v1/runs").filter(
      (call) => (call.body as { mode?: string } | null)?.mode === "batch",
    );
    expect(sent).toHaveLength(1);
  });
});

/* ------------------------------------------------------------------ */
/* The run page                                                        */
/* ------------------------------------------------------------------ */

function record(overrides: Partial<RecordRow> = {}): RecordRow {
  return {
    id: "rr-2",
    record_key: "sam@example.com",
    state: "failed",
    reason: "a value is missing",
    attempts: 1,
    max_attempts: 3,
    commit_attempted: false,
    destination_ref: null,
    ...overrides,
  };
}

describe("the run page's batch action starts one pass, not two", () => {
  it("a double click on Try all again puts each row back once", async () => {
    stub = createFetchStub({
      ...SHELL,
      "/api/v1/runs/run-1/records": {
        body: [
          record(),
          record({ id: "rr-3", record_key: "kim@example.com", state: "parked" }),
        ],
      },
      "/api/v1/runs/run-1": { body: runDetail({ id: "run-1" }) },
      "/api/v1/records/rr-2/requeue": { body: { record_id: "rr-2", state: "pending" } },
      "/api/v1/records/rr-3/requeue": { body: { record_id: "rr-3", state: "pending" } },
    });
    renderApp("/runs/run-1");
    await settle();

    await doubleClick(button("Try all 2 again"));

    expect(posts("/api/v1/records/rr-2/requeue")).toHaveLength(1);
    expect(posts("/api/v1/records/rr-3/requeue")).toHaveLength(1);
  });
});
