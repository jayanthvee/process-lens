/**
 * "Run this task": the three steps, the one primary button at a time, the check's mapping, and the
 * preview run that turns into the rest of the batch.
 */

import { act, fireEvent, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { POLL_INTERVAL_MS } from "../src/lib/useJson";
import type { BatchCheck, BatchUpload, RunDetail as RunPayload, TaskRow } from "../src/types";
import { callsTo, createFetchStub, renderApp, settle, type FetchStub, type RecordedRequest, type StubRoutes } from "./helpers";

/* Every path the Shell asks for as well, so a page test never hits the real API. */
const SHELL = {
  "/api/v1/needs-help": { body: [] },
  "/api/v1/executor/session": { body: { attached: false } },
};

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
  columns: ["Name", "Email", "Phone", "Company", "City", "Notes", "Owner", "Source"],
};

const CHECK: BatchCheck = {
  batch_id: "batch-1",
  row_count: 20,
  columns: UPLOAD.columns,
  recipe_name: "Add inquiries",
  mapping: [
    {
      column: "contact_name",
      label: "Contact name",
      chosen: "Name",
      confidence: "exact",
      required: true,
      alternatives: ["Company"],
    },
    {
      column: "email",
      label: "Email",
      chosen: "Email",
      confidence: "exact",
      required: true,
      alternatives: [],
    },
    {
      column: "phone",
      label: "Phone",
      chosen: null,
      confidence: "none",
      required: false,
      alternatives: [],
    },
  ],
  rows_ok: 20,
  rows_need_a_look: [],
  first_ready: {
    row: 1,
    values: { contact_name: "Priya Nair", email: "priya@example.com" },
  },
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

let stub: FetchStub | null = null;

afterEach(() => {
  stub?.restore();
  stub = null;
  vi.useRealTimers();
});

/** Wait for the page to settle, whether the timers are real or faked. */
async function pump(): Promise<void> {
  if (vi.isFakeTimers()) {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    return;
  }
  await settle(4);
}

interface Harness {
  routes: StubRoutes;
  /** The forms the page posted, in order. */
  forms: FormData[];
}

function harness(extra: StubRoutes = {}): Harness {
  const routes: StubRoutes = {
    ...SHELL,
    "/api/v1/tasks": { body: [TASK] },
    "/api/v1/batches/batch-1/check": { body: CHECK },
    "/api/v1/batches/batch-1/mapping": { body: { batch_id: "batch-1", mapping: {} } },
    "/api/v1/batches": { body: UPLOAD },
    // The preview and the batch are two different runs, so the page has somewhere to go.
    "/api/v1/runs": (call: RecordedRequest) => ({
      body: {
        run_id:
          (call.body as { mode?: string } | null)?.mode === "batch" ? "run-batch" : "run-preview",
        status: "queued",
      },
    }),
    "/api/v1/runs/run-preview": { body: runDetail("completed") },
    "/api/v1/runs/run-preview/records": { body: [] },
    "/api/v1/runs/run-batch": { body: runDetail("completed") },
    "/api/v1/runs/run-batch/records": { body: [] },
    ...extra,
  };
  const fetchStub = createFetchStub(routes);
  stub = fetchStub;
  const forms: FormData[] = [];
  const inner = globalThis.fetch;
  globalThis.fetch = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    if (init?.body instanceof FormData) forms.push(init.body);
    return inner(input, init);
  };
  return { routes, forms };
}

/** Add the spreadsheet the way a person does: through the hidden file input. */
async function addFile(view: ReturnType<typeof renderApp>): Promise<void> {
  const input = view.container.querySelector<HTMLInputElement>("input[type=file]");
  expect(input, "the page offers a file input").not.toBeNull();
  const file = new File(["Name,Email\nPriya Nair,priya@example.com\n"], "people.csv", {
    type: "text/csv",
  });
  fireEvent.change(input as HTMLInputElement, { target: { files: [file] } });
  await pump();
}

function button(label: string): HTMLButtonElement {
  return screen.getByRole("button", { name: label }) as HTMLButtonElement;
}

describe("the run flow", () => {
  it("reveals the three steps in order, with at most one primary button at a time", async () => {
    harness();
    const view = renderApp("/tasks/wf-1/run");
    await pump();

    // Step 1: the way in, and the only gold button on the screen.
    expect(screen.getByText("1. Add your spreadsheet")).toBeTruthy();
    expect(document.querySelectorAll(".btn.primary")).toHaveLength(1);
    expect(button("Choose a file")).toBeTruthy();
    expect(screen.queryByText("2. Check it")).toBeNull();
    expect(screen.queryByText("3. Try one row")).toBeNull();

    await addFile(view);

    // Step 2: step 1 keeps its result and offers no button.
    expect(screen.getByText("2. Check it")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Choose a file" })).toBeNull();
    expect(document.querySelectorAll(".btn.primary")).toHaveLength(1);
    expect(button("Continue")).toBeTruthy();
    expect(screen.queryByText("3. Try one row")).toBeNull();

    await act(async () => {
      fireEvent.click(button("Continue"));
    });
    await pump();

    // Step 3: Continue is done and gone; Try it is the one primary button.
    expect(screen.getByText("3. Try one row")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Continue" })).toBeNull();
    expect(document.querySelectorAll(".btn.primary")).toHaveLength(1);
    expect(button("Try it")).toBeTruthy();
  });

  it("shows what the spreadsheet holds once it is added", async () => {
    const { forms } = harness();
    const view = renderApp("/tasks/wf-1/run");
    await pump();
    await addFile(view);

    expect(screen.getByText("20 rows, 8 columns")).toBeTruthy();
    // The file really went as a form, with the task beside it, and no header of our own.
    const posted = (stub as FetchStub).requests.find(
      (call) => call.method === "POST" && call.path === "/api/v1/batches",
    );
    expect(posted).toBeTruthy();
    expect(forms).toHaveLength(1);
    expect(forms[0]?.get("workflow_id")).toBe("wf-1");
    expect((forms[0]?.get("file") as File).name).toBe("people.csv");
  });

  it("shows one row per task column, with the column it matched and the alternatives", async () => {
    harness();
    const view = renderApp("/tasks/wf-1/run");
    await pump();
    await addFile(view);

    expect(screen.getByText("Contact name")).toBeTruthy();
    // The task's column, and the spreadsheet's own header, both read "Email".
    expect(screen.getAllByText("Email").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Phone").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Matched")).toHaveLength(2);
    expect(screen.getByText("Not found")).toBeTruthy();
    expect(screen.getByText("Also looked like: Company")).toBeTruthy();
    expect(
      (screen.getByLabelText("Contact name") as HTMLSelectElement).value,
    ).toBe("Name");
    expect(
      (screen.getByLabelText("Phone") as HTMLSelectElement).value,
    ).toBe("");
    // The rows the check counted, in one sentence.
    expect(screen.getByText("20 rows ready \u00b7 0 rows need a look")).toBeTruthy();
  });

  it("keeps Continue off while a column this task needs has no spreadsheet column", async () => {
    harness({
      "/api/v1/batches/batch-1/check": {
        body: {
          ...CHECK,
          mapping: [{ ...CHECK.mapping[1]!, chosen: null, confidence: "none" }],
        },
      },
    });
    const view = renderApp("/tasks/wf-1/run");
    await pump();
    await addFile(view);

    expect(button("Continue").hasAttribute("disabled")).toBe(true);

    // A person picks the column, and Continue comes back on.
    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "Email" } });
    await pump();
    expect(button("Continue").hasAttribute("disabled")).toBe(false);
  });

  it("saves the confirmed mapping before it moves on", async () => {
    harness();
    const view = renderApp("/tasks/wf-1/run");
    await pump();
    await addFile(view);

    await act(async () => {
      fireEvent.click(button("Continue"));
    });
    await pump();

    const saved = (stub as FetchStub).requests.find((call) => call.method === "PUT");
    expect(saved?.path).toBe("/api/v1/batches/batch-1/mapping");
    expect(saved?.body).toEqual({
      mapping: { contact_name: "Name", email: "Email", phone: null },
    });
    expect(screen.getByText("3. Try one row")).toBeTruthy();
    expect(screen.getByText("About to add:")).toBeTruthy();
    expect(screen.getByText("Priya Nair")).toBeTruthy();
    expect(screen.getByText("priya@example.com")).toBeTruthy();
  });

  it("asks again after Continue, so the run is built from the mapping the person confirmed", async () => {
    // The check writes each row's verdict, and a run is built only from the rows it marked ready.
    // So the second answer is what counts: here the first one says every row is ready, and the
    // answer that follows the confirmation says one row is not.
    let asked = 0;
    harness({
      "/api/v1/batches/batch-1/check": () => {
        asked += 1;
        return asked === 1
          ? { body: CHECK }
          : {
              body: {
                ...CHECK,
                rows_ok: 19,
                rows_need_a_look: [{ row: 4, reason: "No email" }],
              },
            };
      },
    });
    const view = renderApp("/tasks/wf-1/run");
    await pump();
    await addFile(view);

    expect(screen.getByText("20 rows ready \u00b7 0 rows need a look")).toBeTruthy();

    await act(async () => {
      fireEvent.click(button("Continue"));
    });
    await pump();

    expect(asked, "Continue asks the check again").toBe(2);
    // Step 3 shows the confirmed answer, not the one the person saw before they pressed Continue.
    expect(screen.getByText("19 rows ready \u00b7 1 row needs a look")).toBeTruthy();
    expect(screen.getByText("No email")).toBeTruthy();
    // The mapping now reads rather than edits: the run uses what was saved, so a change here would
    // be a change nothing would act on. "Change the columns" opens it again.
    expect((screen.getByLabelText("Email") as HTMLSelectElement).disabled).toBe(true);
    expect(button("Change the columns")).toBeTruthy();

    await act(async () => {
      fireEvent.click(button("Change the columns"));
    });
    await pump();

    // Back on step 2 the selects are editable again, and Continue is the one primary button.
    expect((screen.getByLabelText("Email") as HTMLSelectElement).disabled).toBe(false);
    expect(screen.queryByText("3. Try one row")).toBeNull();
    expect(document.querySelectorAll(".btn.primary")).toHaveLength(1);
    expect(button("Continue")).toBeTruthy();

    await act(async () => {
      fireEvent.click(button("Continue"));
    });
    await pump();

    await act(async () => {
      fireEvent.click(button("Try it"));
    });
    await pump();

    // One of the nineteen was just tried, so the rest are eighteen -- the fresh count, not twenty.
    expect(button("Run the other 18 rows")).toBeTruthy();
  });

  it("calls a column the person picked Matched, whatever the guess's own confidence was", async () => {
    harness();
    const view = renderApp("/tasks/wf-1/run");
    await pump();
    await addFile(view);

    // Phone came back unmatched, so its chip says so.
    expect(screen.getByText("Not found")).toBeTruthy();

    // The person picks the column themselves: it is now their decision, not a guess to check.
    fireEvent.change(screen.getByLabelText("Phone"), { target: { value: "City" } });
    await pump();

    expect(screen.queryByText("Not found")).toBeNull();
    expect(screen.getAllByText("Matched")).toHaveLength(3);
  });

  it("tries one row, then runs the other rows and opens that run", async () => {
    harness();
    const view = renderApp("/tasks/wf-1/run");
    await pump();
    await addFile(view);
    await act(async () => {
      fireEvent.click(button("Continue"));
    });
    await pump();

    await act(async () => {
      fireEvent.click(button("Try it"));
    });
    await pump();

    const requests = (stub as FetchStub).requests.filter(
      (call) => call.method === "POST" && call.path === "/api/v1/runs",
    );
    expect(requests).toHaveLength(1);
    expect(requests[0]?.body).toEqual({
      workflow_id: "wf-1",
      recipe_version_id: "rv-1",
      input_batch_id: "batch-1",
      mode: "preview",
      limit: 1,
    });
    // Twenty rows are ready, and one of them was just tried.
    const rest = button("Run the other 19 rows");
    expect(document.querySelectorAll(".btn.primary")).toHaveLength(1);

    await act(async () => {
      fireEvent.click(rest);
    });
    await pump();

    const posts = (stub as FetchStub).requests.filter(
      (call) => call.method === "POST" && call.path === "/api/v1/runs",
    );
    expect(posts[1]?.body).toEqual({
      workflow_id: "wf-1",
      recipe_version_id: "rv-1",
      input_batch_id: "batch-1",
      mode: "batch",
    });
    // And the page followed it to that run's own page.
    expect(screen.getByText("Progress")).toBeTruthy();
  });

  it("follows the row it is trying until the row is done, and stops asking after that", async () => {
    vi.useFakeTimers();
    let status = "running";
    const fetchStub = createFetchStub({
      ...SHELL,
      "/api/v1/tasks": { body: [TASK] },
      "/api/v1/batches/batch-1/check": { body: CHECK },
      "/api/v1/batches/batch-1/mapping": { body: { batch_id: "batch-1", mapping: {} } },
      "/api/v1/batches": { body: UPLOAD },
      "/api/v1/runs": { body: { run_id: "run-preview", status: "queued" } },
      "/api/v1/runs/run-preview": () => ({ body: runDetail(status) }),
    });
    stub = fetchStub;
    const view = renderApp("/tasks/wf-1/run");
    await pump();
    await addFile(view);
    await act(async () => {
      fireEvent.click(button("Continue"));
    });
    await pump();
    await act(async () => {
      fireEvent.click(button("Try it"));
    });
    await pump();
    await pump();

    expect(callsTo(fetchStub, "/api/v1/runs/run-preview")).toBeGreaterThan(0);
    expect(screen.getByText("Running")).toBeTruthy();
    // While it is going, the screen offers nothing to press.
    expect(screen.queryByRole("button", { name: /Run the other/ })).toBeNull();
    expect(document.querySelectorAll(".btn.primary")).toHaveLength(0);

    const before = callsTo(fetchStub, "/api/v1/runs/run-preview");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS);
    });
    expect(callsTo(fetchStub, "/api/v1/runs/run-preview")).toBeGreaterThan(before);

    status = "completed";
    await act(async () => {
      await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS);
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(button("Run the other 19 rows")).toBeTruthy();

    const afterFinish = callsTo(fetchStub, "/api/v1/runs/run-preview");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });
    expect(callsTo(fetchStub, "/api/v1/runs/run-preview")).toBe(afterFinish);
  });

  it("says so when the task is not there, rather than showing a blank page", async () => {
    harness({ "/api/v1/tasks": { body: [] } });
    renderApp("/tasks/wf-1/run");
    await pump();

    expect(screen.getByText("This task could not be found.")).toBeTruthy();
  });
});
