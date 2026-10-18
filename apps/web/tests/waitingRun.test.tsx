/**
 * PL-041 part 1: a queued run says what it is waiting for.
 *
 * The database allows one running-or-paused run per app, so a queued run can be stuck behind
 * another task's run on the same site. Both places the queued run shows -- the run page's first
 * panel and the "3. Try one row" card -- name that run, in words a person can act on, without
 * sending them to hunt on another page.
 */

import { act, fireEvent, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { engineWordsIn } from "../src/lib/words";
import type { BatchCheck, BatchUpload, RunDetail as RunPayload, TaskRow } from "../src/types";
import {
  callsTo,
  createFetchStub,
  renderApp,
  settle,
  type FetchStub,
  type StubRoutes,
} from "./helpers";

const OPERATOR_ID = "0f8fad5b-d9cb-469f-a165-70867728950e";

/* Every path the Shell asks for as well, so a page test never hits the real API. */
const SHELL = {
  "/api/v1/needs-help": { body: [] },
  "/api/v1/executor/session": { body: { attached: true } },
  "/api/v1/operator": { body: { operator_id: OPERATOR_ID } },
};

const WAITING_PAUSED = {
  run_id: "run-paused",
  task_name: "Send invoices",
  status: "paused",
};

const WAITING_RUNNING = {
  run_id: "run-running",
  task_name: "Send invoices",
  status: "running",
};

let stub: FetchStub | null = null;

afterEach(() => {
  stub?.restore();
  stub = null;
});

function runDetail(overrides: Partial<RunPayload> = {}): RunPayload {
  return {
    id: "run-queued",
    status: "queued",
    pause_reason: null,
    counts: { total: 1, verified: 0 },
    open_approval: null,
    started_at: null,
    finished_at: null,
    waiting_behind: null,
    ...overrides,
  };
}

/** The notice on screen, whichever page rendered it. */
function notice(container: HTMLElement): HTMLElement {
  const found = container.querySelector<HTMLElement>(".waiting-run");
  expect(found, "the page says what the queued run is waiting for").not.toBeNull();
  return found as HTMLElement;
}

describe("a queued run on the run page", () => {
  it("names the paused run ahead, offers to open it, and stops it if the person asks", async () => {
    stub = createFetchStub({
      ...SHELL,
      "/api/v1/runs/run-queued/records": { body: [] },
      "/api/v1/runs/run-queued": { body: runDetail({ waiting_behind: WAITING_PAUSED }) },
      // The blocker is still paused until a worker finishes stopping it, which is what the API
      // keeps reporting; the page says it is on its way out rather than offering the button twice.
      "/api/v1/runs/run-paused/cancel": { body: runDetail({ status: "paused" }) },
    });
    const view = renderApp("/runs/run-queued");
    await settle();

    const block = notice(view.container);
    expect(block.textContent).toContain(
      "Waiting: your Send invoices run on this app is paused. Only one run per app can go at a time.",
    );
    expect(within(block).getByRole("link", { name: "Open it" }).getAttribute("href")).toBe(
      "/runs/run-paused",
    );
    expect(engineWordsIn(block.textContent ?? "")).toEqual([]);

    const before = callsTo(stub, "/api/v1/runs/run-queued");
    await act(async () => {
      fireEvent.click(within(block).getByRole("button", { name: "Cancel that run" }));
    });
    await settle();

    // The cancel goes to the run ahead, never to this one.
    const cancelled = stub.requests.filter(
      (call) => call.method === "POST" && call.path === "/api/v1/runs/run-paused/cancel",
    );
    expect(cancelled).toHaveLength(1);
    expect(
      stub.requests.filter((call) => call.path === "/api/v1/runs/run-queued/cancel"),
    ).toHaveLength(0);
    // And the page asks again, so the wait it shows is the wait that is left.
    expect(callsTo(stub, "/api/v1/runs/run-queued")).toBeGreaterThan(before);
    expect(screen.getByText("Stopping that run. This one starts when it has.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Cancel that run" })).toBeNull();
  });

  it("waits for a running run in its own words, and offers the link only", async () => {
    stub = createFetchStub({
      ...SHELL,
      "/api/v1/runs/run-queued/records": { body: [] },
      "/api/v1/runs/run-queued": { body: runDetail({ waiting_behind: WAITING_RUNNING }) },
    });
    const view = renderApp("/runs/run-queued");
    await settle();

    const block = notice(view.container);
    expect(block.textContent).toContain(
      "Waiting for your Send invoices run on this app to finish.",
    );
    expect(within(block).getByRole("link", { name: "Open it" }).getAttribute("href")).toBe(
      "/runs/run-running",
    );
    // A running run cannot be stopped from here: there is nothing safe to press.
    expect(within(block).queryByRole("button")).toBeNull();
    expect(engineWordsIn(block.textContent ?? "")).toEqual([]);
  });

  it("shows nothing at all when nothing is ahead of it", async () => {
    stub = createFetchStub({
      ...SHELL,
      "/api/v1/runs/run-queued/records": { body: [] },
      "/api/v1/runs/run-queued": { body: runDetail({ waiting_behind: null }) },
    });
    const view = renderApp("/runs/run-queued");
    await settle();

    expect(view.container.querySelector(".waiting-run")).toBeNull();
    expect(view.container.textContent).not.toContain("Only one run per app");
  });
});

/* The "3. Try one row" card needs the whole flow: a file, a check, a confirmed mapping, a run. */

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

/** Render the run flow and press Try it, so the preview run is the one on screen. */
async function tryOneRow(extra: StubRoutes): Promise<HTMLElement> {
  const routes: StubRoutes = {
    ...SHELL,
    "/api/v1/tasks": { body: [TASK] },
    "/api/v1/batches/batch-1/check": { body: CHECK },
    "/api/v1/batches/batch-1/mapping": { body: { batch_id: "batch-1", mapping: {} } },
    "/api/v1/batches": { body: UPLOAD },
    "/api/v1/runs": { body: { run_id: "run-preview", status: "queued" } },
    ...extra,
  };
  stub = createFetchStub(routes);
  const view = renderApp("/tasks/wf-1/run");
  await settle();

  const input = view.container.querySelector<HTMLInputElement>("input[type=file]");
  const file = new File(["Email\npriya@example.com\n"], "people.csv", { type: "text/csv" });
  fireEvent.change(input as HTMLInputElement, { target: { files: [file] } });
  await settle();
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
  });
  await settle();
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: "Try it" }));
  });
  await settle();
  await settle();

  const card = screen.getByText("3. Try one row").closest("section");
  expect(card, "the card that started the run").not.toBeNull();
  return card as HTMLElement;
}

describe("a queued preview row in the Try one row card", () => {
  it("says it is waiting for the paused run ahead, in the card that started it", async () => {
    let waiting: RunPayload["waiting_behind"] = WAITING_PAUSED;
    const card = await tryOneRow({
      "/api/v1/runs/run-preview": () => ({
        body: runDetail({ id: "run-preview", waiting_behind: waiting }),
      }),
      "/api/v1/runs/run-paused/cancel": () => {
        waiting = null;
        return { body: runDetail({ id: "run-preview" }) };
      },
    });

    const block = card.querySelector<HTMLElement>(".waiting-run");
    expect(block, "the card names the wait").not.toBeNull();
    expect(block?.textContent).toContain(
      "Waiting: your Send invoices run on this app is paused. Only one run per app can go at a time.",
    );
    expect(engineWordsIn(block?.textContent ?? "")).toEqual([]);

    await act(async () => {
      fireEvent.click(
        within(block as HTMLElement).getByRole("button", { name: "Cancel that run" }),
      );
    });
    await settle();

    const cancelled = (stub as FetchStub).requests.filter(
      (call) => call.method === "POST" && call.path === "/api/v1/runs/run-paused/cancel",
    );
    expect(cancelled).toHaveLength(1);
    // The wait is gone from the card once the run ahead is on its way out.
    expect(card.querySelector(".waiting-run")).toBeNull();
  });

  it("shows nothing in the card when the row is not behind anything", async () => {
    const card = await tryOneRow({
      "/api/v1/runs/run-preview": { body: runDetail({ id: "run-preview" }) },
    });

    expect(card.querySelector(".waiting-run")).toBeNull();
  });
});
