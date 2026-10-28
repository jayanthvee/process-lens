/**
 * PL-118 item 3: Try one row shows what the page kept.
 *
 * "now 799" is the value read back from the page, never the value in the spreadsheet. "still 910,
 * not saved" is the page's old value, and the row says it could not be confirmed.
 */

import { act, fireEvent, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { BatchCheck, BatchUpload, RunDetail as RunPayload, TaskRow } from "../src/types";
import { createFetchStub, renderApp, settle, type FetchStub } from "./helpers";

const SHELL = {
  "/api/v1/needs-help": { body: [] },
  "/api/v1/executor/session": { body: { attached: false } },
};

const TASK: TaskRow = {
  workflow_id: "wf-1",
  recipe_version_id: "rv-1",
  name: "Update prices",
  app: "crm.example.com",
  last_run: null,
};

const UPLOAD: BatchUpload = {
  batch_id: "batch-1",
  row_count: 1,
  columns: ["Product", "Price"],
};

const CHECK: BatchCheck = {
  batch_id: "batch-1",
  row_count: 1,
  columns: UPLOAD.columns,
  recipe_name: "Update prices",
  mapping: [
    {
      column: "product",
      label: "Product",
      chosen: "Product",
      confidence: "exact",
      required: true,
      alternatives: [],
    },
    {
      column: "price",
      label: "Price",
      chosen: "Price",
      confidence: "exact",
      required: true,
      alternatives: [],
    },
  ],
  rows_ok: 1,
  rows_need_a_look: [],
  first_ready: { row: 1, values: { product: "Oak Standing Desk", price: "799" } },
};

function runDetail(status: string): RunPayload {
  return {
    id: "run-preview",
    status,
    pause_reason: null,
    counts: { total: 1, verified: status === "completed" ? 1 : 0 },
    open_approval: null,
    started_at: null,
    finished_at: null,
  };
}

function record(row: Record<string, unknown>): Record<string, unknown> {
  return {
    id: "rr-1",
    run_id: "run-preview",
    input_record_id: "in-1",
    record_key: "row-1",
    idempotency_key: "row-1:799",
    state: "verified",
    reason: "added and checked",
    attempts: 1,
    max_attempts: 3,
    commit_attempted: true,
    destination_ref: null,
    last_step_error: null,
    read_back: [],
    ...row,
  };
}

let stub: FetchStub | null = null;

afterEach(() => {
  stub?.restore();
  stub = null;
});

/** The three steps up to a preview run, with the records the run reports. */
async function tryOneRow(row: Record<string, unknown>): Promise<HTMLElement> {
  stub = createFetchStub({
    ...SHELL,
    "/api/v1/tasks": { body: [TASK] },
    "/api/v1/batches/batch-1/check": { body: CHECK },
    "/api/v1/batches/batch-1/mapping": { body: { batch_id: "batch-1", mapping: {} } },
    "/api/v1/batches": { body: UPLOAD },
    "/api/v1/runs": { body: { run_id: "run-preview", status: "queued" } },
    "/api/v1/runs/run-preview": { body: runDetail("completed") },
    "/api/v1/runs/run-preview/records": { body: [row] },
  });
  const view = renderApp("/tasks/wf-1/run");
  await settle(4);
  const input = view.container.querySelector<HTMLInputElement>("input[type=file]");
  expect(input).not.toBeNull();
  fireEvent.change(input as HTMLInputElement, {
    target: {
      files: [new File(["Product,Price\nOak Standing Desk,799\n"], "prices.csv", { type: "text/csv" })],
    },
  });
  await settle(4);
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
  });
  await settle(4);
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: "Try it" }));
  });
  await settle(4);
  await settle(4);
  const card = screen.getByText("3. Try one row").closest("section");
  expect(card).not.toBeNull();
  return card as HTMLElement;
}

describe("Try one row shows the page's value", () => {
  it("says now 799 when the page kept the new value", async () => {
    const card = await tryOneRow(
      record({
        read_back: [{ label: "Sales price", value: "$ 799.00", matched: true }],
      }),
    );

    expect(card.textContent).toContain("Sales price: now $ 799.00");
  });

  it("says still 910, not saved when the page kept the old value", async () => {
    const card = await tryOneRow(
      record({
        state: "parked",
        reason: "ProcessLens couldn't confirm the change was saved.",
        read_back: [{ label: "Sales price", value: "910", matched: false }],
      }),
    );

    expect(card.textContent).toContain("Sales price: still 910, not saved");
  });
});
