/**
 * PL-107 item 9: the Try-one-row card tells the truth about the row it tried.
 *
 * A preview run that ended `completed` with its one row parked showed "Finished", with no reason
 * and no button. The badge must come from the row's own state, and a parked row must say why and
 * offer Try again / Skip it right there.
 */
import { act, fireEvent, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type {
  BatchCheck,
  BatchUpload,
  RecordRow,
  RunDetail as RunPayload,
  TaskRow,
} from "../src/types";
import { createFetchStub, renderApp, settle, type FetchStub, type StubRoutes } from "./helpers";

const OPERATOR_ID = "0f8fad5b-d9cb-469f-a165-70867728950e";

const SHELL = {
  "/api/v1/needs-help": { body: [] },
  "/api/v1/executor/session": { body: { attached: true } },
  "/api/v1/operator": { body: { operator_id: OPERATOR_ID } },
};

const TASK: TaskRow = {
  workflow_id: "wf-1",
  recipe_version_id: "rv-1",
  name: "Add inquiries",
  app: "crm.example.com",
  last_run: null,
};

const UPLOAD: BatchUpload = { batch_id: "batch-1", row_count: 1, columns: ["Name", "Email"] };

const CHECK: BatchCheck = {
  batch_id: "batch-1",
  row_count: 1,
  columns: ["Name", "Email"],
  recipe_name: "Add inquiries",
  mapping: [
    {
      column: "contact_name",
      label: "Contact name",
      chosen: "Name",
      confidence: "exact",
      required: true,
      alternatives: [],
    },
    {
      column: "email",
      label: "Email",
      chosen: "Email",
      confidence: "exact",
      required: true,
      alternatives: [],
    },
  ],
  rows_ok: 1,
  rows_need_a_look: [],
  first_ready: {
    row: 1,
    values: { contact_name: "Priya Nair", email: "priya@example.com" },
  },
};

function parkedRow(overrides: Partial<RecordRow> = {}): RecordRow {
  return {
    id: "rr-parked",
    record_key: "priya@example.com",
    state: "parked",
    reason: "Couldn't click Continue: something was in front of it",
    attempts: 1,
    max_attempts: 3,
    commit_attempted: false,
    destination_ref: null,
    ...overrides,
  };
}

function runDetail(status: string, overrides: Partial<RunPayload> = {}): RunPayload {
  return {
    id: "run-preview",
    status,
    pause_reason: null,
    counts: { total: 1, parked: status === "completed" ? 1 : 0 },
    open_approval: null,
    started_at: null,
    finished_at: null,
    ...overrides,
  };
}

function harness(extra: StubRoutes = {}): FetchStub {
  return createFetchStub({
    ...SHELL,
    "/api/v1/tasks": { body: [TASK] },
    "/api/v1/batches/batch-1/check": { body: CHECK },
    "/api/v1/batches/batch-1/mapping": { body: { batch_id: "batch-1", mapping: {} } },
    "/api/v1/batches": { body: UPLOAD },
    "/api/v1/runs": { body: { run_id: "run-preview", status: "queued" } },
    "/api/v1/runs/run-preview": { body: runDetail("completed") },
    "/api/v1/runs/run-preview/records": { body: [parkedRow()] },
    "/api/v1/records/rr-parked/requeue": { body: { record_id: "rr-parked", state: "pending" } },
    ...extra,
  });
}

/** Add the file, check it, and try the one row, so the card is the one that ran. */
async function tryOneRow(extra: StubRoutes = {}): Promise<FetchStub> {
  const stub = harness(extra);
  const view = renderApp("/tasks/wf-1/run");
  await settle(4);
  const input = view.container.querySelector<HTMLInputElement>("input[type=file]");
  expect(input, "the page offers a file input").not.toBeNull();
  const file = new File(["Name,Email\nPriya Nair,priya@example.com\n"], "people.csv", {
    type: "text/csv",
  });
  await act(async () => {
    fireEvent.change(input!, { target: { files: [file] } });
  });
  await settle(4);
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
  });
  await settle(4);
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: "Try it" }));
  });
  await settle(6);
  return stub;
}

function tryCard(): HTMLElement {
  const card = screen.getByText("3. Try one row").closest("section");
  expect(card, "the card that tried the row").not.toBeNull();
  return card as HTMLElement;
}

describe("the try-one-row card tells the truth about its row", () => {
  it("shows the row's own outcome, its reason, and its buttons, never a bare Finished", async () => {
    await tryOneRow();
    const card = tryCard();
    // The badge comes from the row, not the run's completed status.
    expect(within(card).getByText("Needs your help")).toBeTruthy();
    expect(within(card).queryByText("Finished")).toBeNull();
    // Its plain reason is right there.
    expect(within(card).getByText(/something was in front of it/)).toBeTruthy();
    // And the two actions, on this screen.
    expect(within(card).getByRole("button", { name: "Try again" })).toBeTruthy();
    expect(within(card).getByRole("button", { name: "Skip it" })).toBeTruthy();
  });

  it("sends Try again for that row through the same record action", async () => {
    const stub = await tryOneRow();
    const card = tryCard();
    await act(async () => {
      fireEvent.click(within(card).getByRole("button", { name: "Try again" }));
    });
    await settle(4);
    const posts = stub.requests.filter(
      (call) => call.method === "POST" && call.path === "/api/v1/records/rr-parked/requeue",
    );
    expect(posts).toHaveLength(1);
  });

  it("a finished row that worked reads Done and offers nothing", async () => {
    await tryOneRow({
      "/api/v1/runs/run-preview/records": {
        body: [parkedRow({ state: "verified", reason: null })],
      },
      "/api/v1/runs/run-preview": {
        body: runDetail("completed", { counts: { total: 1, verified: 1 } }),
      },
    });
    const card = tryCard();
    expect(within(card).getByText("Done")).toBeTruthy();
    expect(within(card).queryByRole("button", { name: "Try again" })).toBeNull();
  });
});
