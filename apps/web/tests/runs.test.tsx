/**
 * The runs list and one run: the buttons follow the status, a refusal sits next to the button that
 * caused it, the pause reason is in words, and the live poll stops the moment the run is not open
 * (PL-015's verified behaviour, carried forward).
 */

import { act, fireEvent, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { RecordRow, RunDetail as RunPayload, RunSummary } from "../src/types";
import { DESTINATION_CHECK_LABEL } from "../src/pages/needsHelpActions";
import { engineWordsIn } from "../src/lib/words";
import { POLL_INTERVAL_MS } from "../src/lib/useJson";
import { callsTo, createFetchStub, renderApp, settle, type FetchStub } from "./helpers";

const OPERATOR_ID = "0f8fad5b-d9cb-469f-a165-70867728950e";
const OPERATOR_503 = "PROCESSLENS_OPERATOR_ID is not set, so there is no operator id to act as";

/* Every path the Shell asks for as well, so a page test never hits the real API. */
const SHELL = {
  "/api/v1/needs-help": { body: [] },
  "/api/v1/executor/session": { body: { attached: false } },
  "/api/v1/operator": { body: { operator_id: OPERATOR_ID } },
};

let stub: FetchStub | null = null;

afterEach(() => {
  stub?.restore();
  stub = null;
  vi.useRealTimers();
});

function runSummary(overrides: Partial<RunSummary> = {}): RunSummary {
  return {
    id: "run-1",
    status: "running",
    pause_reason: null,
    created_at: new Date().toISOString(),
    recipe_name: "Add inquiries",
    recipe_version: "3",
    counts: { verified: 11, pending: 9 },
    ...overrides,
  };
}

function runDetail(overrides: Partial<RunPayload> = {}): RunPayload {
  return {
    id: "run-1",
    status: "running",
    pause_reason: null,
    counts: { total: 20, verified: 11, skipped: 0, parked: 0, failed: 0, in_progress: 9 },
    open_approval: null,
    started_at: null,
    finished_at: null,
    ...overrides,
  };
}

function record(overrides: Partial<RecordRow> = {}): RecordRow {
  return {
    id: "rr-1",
    record_key: "priya@example.com",
    state: "verified",
    reason: null,
    attempts: 1,
    max_attempts: 3,
    commit_attempted: false,
    destination_ref: null,
    ...overrides,
  };
}

/** The request a paused run is stopped at, with the engine internals the API really sends. */
function sampleApproval() {
  return {
    id: "approval-1",
    run_record_id: "rr-1",
    // The API sends the step's id, the action's name and the whole target ladder. None of it may
    // reach the screen, so this fixture carries all three.
    step_id: "save-lead",
    kind: "commit",
    shown: {
      step: "save-lead",
      action: "click",
      targets: [{ by: "css", selector: "#lead-save" }],
      value: "New enquiry",
      record: { email: "priya@example.com", company_name: "Acme" },
    },
    created_at: "2026-10-15T09:10:00Z",
  };
}

function buttonNames(): string[] {
  return screen
    .getAllByRole("button")
    .map((node) => node.textContent ?? "")
    .filter((label) => ["Pause", "Resume", "Cancel"].includes(label));
}

describe("the runs list", () => {
  it("shows the task, when it started, what it is doing and how far it got", async () => {
    stub = createFetchStub({
      ...SHELL,
      "/api/v1/runs": { body: [runSummary()] },
    });
    renderApp("/runs");
    await settle();

    expect(screen.getByText("Add inquiries")).toBeTruthy();
    expect(screen.getAllByText("Running").length).toBeGreaterThan(0);
    expect(screen.getByText("11 of 20 done")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Open" }).getAttribute("href")).toBe("/runs/run-1");
  });

  it("counts a row that was already there as done", async () => {
    stub = createFetchStub({
      ...SHELL,
      "/api/v1/runs": {
        body: [runSummary({ status: "completed", counts: { verified: 4, skipped: 6 } })],
      },
    });
    renderApp("/runs");
    await settle();

    expect(screen.getByText("10 of 10 done")).toBeTruthy();
    expect(screen.getAllByText("Finished").length).toBeGreaterThan(0);
  });

  it("says what will appear here and points at the tasks when nothing has run", async () => {
    stub = createFetchStub({ ...SHELL, "/api/v1/runs": { body: [] } });
    renderApp("/runs");
    await settle();

    expect(screen.getByText(/Nothing has run yet/)).toBeTruthy();
    expect(screen.getByRole("link", { name: "Go to your tasks" }).getAttribute("href")).toBe(
      "/tasks",
    );
  });
});

describe("one run", () => {
  it("offers Pause and Cancel while it is going, Resume only when it is paused", async () => {
    stub = createFetchStub({
      ...SHELL,
      "/api/v1/runs/run-1/records": { body: [] },
      "/api/v1/runs/run-1": { body: runDetail({ status: "running" }) },
    });
    const first = renderApp("/runs/run-1");
    await settle();
    expect(buttonNames()).toEqual(["Pause", "Cancel"]);
    first.unmount();
    stub.restore();

    stub = createFetchStub({
      ...SHELL,
      "/api/v1/runs/run-1/records": { body: [] },
      "/api/v1/runs/run-1": { body: runDetail({ status: "paused", pause_reason: "login_required" }) },
    });
    renderApp("/runs/run-1");
    await settle();
    expect(buttonNames()).toEqual(["Pause", "Resume", "Cancel"]);
  });

  it("says Waiting for Chrome when a queued run has no Chrome session", async () => {
    stub = createFetchStub({
      ...SHELL,
      "/api/v1/runs/run-1/records": { body: [] },
      "/api/v1/runs/run-1": { body: runDetail({ status: "queued" }) },
    });
    renderApp("/runs/run-1");
    await settle();

    expect(
      screen.getByText("Waiting for Chrome: open the ProcessLens extension and it will connect"),
    ).toBeTruthy();
    expect(screen.queryByText("Queued")).toBeNull();
  });

  it("offers nothing once the run is finished", async () => {
    stub = createFetchStub({
      ...SHELL,
      "/api/v1/runs/run-1/records": { body: [] },
      "/api/v1/runs/run-1": { body: runDetail({ status: "completed" }) },
    });
    renderApp("/runs/run-1");
    await settle();

    expect(buttonNames()).toEqual([]);
  });

  it("puts the pause reason into words, and says so when there is none", async () => {
    stub = createFetchStub({
      ...SHELL,
      "/api/v1/runs/run-1/records": { body: [] },
      "/api/v1/runs/run-1": { body: runDetail({ status: "paused", pause_reason: "login_required" }) },
    });
    const paused = renderApp("/runs/run-1");
    await settle();

    expect(
      screen.getByText("Log in to the site in the ProcessLens window, then resume."),
    ).toBeTruthy();
    paused.unmount();
    stub.restore();

    stub = createFetchStub({
      ...SHELL,
      "/api/v1/runs/run-1/records": { body: [] },
      "/api/v1/runs/run-1": { body: runDetail({ status: "running" }) },
    });
    renderApp("/runs/run-1");
    await settle();
    expect(screen.getByText("Not paused.")).toBeTruthy();
  });

  it("shows every row's name, its result and its reason, and leaves a finished row's reason empty", async () => {
    stub = createFetchStub({
      ...SHELL,
      "/api/v1/runs/run-1/records": {
        body: [
          record(),
          record({ id: "rr-2", record_key: "sam@example.com", state: "parked", reason: "missing email" }),
          record({ id: "rr-3", record_key: "kim@example.com", state: "skipped", reason: null }),
          record({ id: "rr-4", record_key: "lee@example.com", state: "parked", reason: null }),
        ],
      },
      "/api/v1/runs/run-1": { body: runDetail() },
    });
    renderApp("/runs/run-1");
    await settle();

    expect(screen.getByText("priya@example.com")).toBeTruthy();
    expect(screen.getByText("sam@example.com")).toBeTruthy();
    expect(screen.getAllByText("Done").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Needs your help").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Already there").length).toBeGreaterThan(0);
    expect(screen.getByText("Missing email.")).toBeTruthy();
    // A row that needs a person always says something; a row that finished has nothing to explain,
    // so its reason is a dash and never a sentence that is not true about it.
    expect(screen.getByText("Something went wrong.")).toBeTruthy();
    const row = screen.getByText("priya@example.com").closest("tr");
    expect(row?.textContent).not.toContain("Something went wrong");
  });

  it("exports the results from this origin", async () => {
    stub = createFetchStub({
      ...SHELL,
      "/api/v1/runs/run-1/records": { body: [] },
      "/api/v1/runs/run-1": { body: runDetail({ status: "completed" }) },
    });
    renderApp("/runs/run-1");
    await settle();

    expect(screen.getByRole("link", { name: "Export CSV" }).getAttribute("href")).toBe(
      "/api/v1/runs/run-1/results.csv",
    );
  });

  it("shows a refusal next to the button that caused it", async () => {
    stub = createFetchStub({
      ...SHELL,
      "/api/v1/runs/run-1/records": { body: [] },
      "/api/v1/runs/run-1/pause": { status: 409, body: { detail: "run run-1 is completed" } },
      "/api/v1/runs/run-1": { body: runDetail({ status: "running" }) },
    });
    const view = renderApp("/runs/run-1");
    await settle();

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Pause" }));
    });
    await settle();

    // The API's own words, in the same element as the button that caused them (PL-015's
    // `closest(...)` placement assertion, carried forward against this page's own markup).
    const pause = view.container.querySelector<HTMLButtonElement>(".run-action-pause");
    const group = pause?.closest(".run-action");
    expect(group?.querySelector(".action-error")?.textContent).toBe("run run-1 is completed");
    expect(screen.getByText("run run-1 is completed")).toBeTruthy();
  });

  it("shows the times and the time saved for a finished run", async () => {
    stub = createFetchStub({
      ...SHELL,
      "/api/v1/runs/run-1/records": { body: [] },
      "/api/v1/runs/run-1": {
        body: runDetail({
          status: "completed",
          started_at: "2026-10-15T09:00:00Z",
          finished_at: "2026-10-15T09:52:00Z",
          counts: { total: 20, verified: 20, skipped: 0, parked: 0, failed: 0, in_progress: 0 },
        }),
      },
    });
    renderApp("/runs/run-1");
    await settle();

    expect(screen.getByText(/Started/)).toBeTruthy();
    expect(screen.getAllByText(/Finished/).length).toBeGreaterThan(0);
    // 20 rows at about three minutes each, less the 52 minutes the run took.
    expect(screen.getByText("About 8 minutes saved")).toBeTruthy();
  });
});

describe("a run stopped for your decision", () => {
  it("shows what it is about to save, in words, and never the step id or the selector", async () => {
    stub = createFetchStub({
      ...SHELL,
      "/api/v1/runs/run-1/records": { body: [] },
      "/api/v1/runs/run-1": {
        body: runDetail({
          status: "paused",
          pause_reason: "awaiting_approval",
          open_approval: sampleApproval(),
        }),
      },
    });
    renderApp("/runs/run-1");
    await settle();

    expect(screen.getByText("Waiting for your decision")).toBeTruthy();
    expect(screen.getByText("The value it will save")).toBeTruthy();
    expect(screen.getByText("New enquiry")).toBeTruthy();
    // The row's own columns, named as the person's spreadsheet names them.
    expect(screen.getByText("Email")).toBeTruthy();
    expect(screen.getByText("priya@example.com")).toBeTruthy();
    expect(screen.getByText("Company name")).toBeTruthy();
    expect(screen.getByText("Acme")).toBeTruthy();
    // The API's own payload carries these, and none of them is a person's business.
    const page = screen.getByRole("main");
    expect(page.textContent).not.toContain("save-lead");
    expect(page.textContent).not.toContain("lead-save");
    expect(page.textContent).not.toContain("targets");
  });

  it("answers the request as the operator the API names, and asks for nothing else", async () => {
    const fetchStub = createFetchStub({
      ...SHELL,
      "/api/v1/runs/run-1/records": { body: [] },
      "/api/v1/runs/run-1": {
        body: runDetail({
          status: "paused",
          pause_reason: "awaiting_approval",
          open_approval: sampleApproval(),
        }),
      },
      "/api/v1/approvals": {
        body: { approval_id: "a-1", request_id: "approval-1", decision: "approved", run_id: "run-1", resumed: true },
      },
    });
    stub = fetchStub;
    renderApp("/runs/run-1");
    await settle();

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Go ahead" }));
    });
    await settle();

    const posts = fetchStub.requests.filter((call) => call.path === "/api/v1/approvals");
    expect(posts).toHaveLength(1);
    expect(posts[0]?.body).toEqual({
      request_id: "approval-1",
      decision: "approved",
      decided_by: OPERATOR_ID,
    });
  });

  it("says no without inventing a reason the person did not give", async () => {
    const fetchStub = createFetchStub({
      ...SHELL,
      "/api/v1/runs/run-1/records": { body: [] },
      "/api/v1/runs/run-1": {
        body: runDetail({
          status: "paused",
          pause_reason: "awaiting_approval",
          open_approval: sampleApproval(),
        }),
      },
      "/api/v1/approvals": {
        body: { approval_id: "a-2", request_id: "approval-1", decision: "rejected", run_id: "run-1", resumed: true },
      },
    });
    stub = fetchStub;
    renderApp("/runs/run-1");
    await settle();

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "No, stop this row" }));
    });
    await settle();

    const posts = fetchStub.requests.filter((call) => call.path === "/api/v1/approvals");
    expect(posts).toHaveLength(1);
    expect(posts[0]?.body).toEqual({
      request_id: "approval-1",
      decision: "rejected",
      decided_by: OPERATOR_ID,
    });
  });

  it("disables both decisions and says why when the API names no operator", async () => {
    stub = createFetchStub({
      ...SHELL,
      "/api/v1/operator": { status: 503, body: { detail: OPERATOR_503 } },
      "/api/v1/runs/run-1/records": { body: [] },
      "/api/v1/runs/run-1": {
        body: runDetail({
          status: "paused",
          pause_reason: "awaiting_approval",
          open_approval: sampleApproval(),
        }),
      },
    });
    renderApp("/runs/run-1");
    await settle();

    expect(screen.getByRole("button", { name: "Go ahead" }).hasAttribute("disabled")).toBe(true);
    expect(
      screen.getByRole("button", { name: "No, stop this row" }).hasAttribute("disabled"),
    ).toBe(true);
    // It refuses to act, and it says why -- the setup sentence, never the API's own detail text
    // with the environment variable in it.
    expect(
      screen.getByText("Actions are turned off until an operator is set up. Ask your admin."),
    ).toBeTruthy();
    expect(screen.queryByText(/PROCESSLENS/)).toBeNull();
  });

  it("shows no decision panel when the run is not waiting for one", async () => {
    stub = createFetchStub({
      ...SHELL,
      "/api/v1/runs/run-1/records": { body: [] },
      "/api/v1/runs/run-1": { body: runDetail({ status: "running" }) },
    });
    renderApp("/runs/run-1");
    await settle();

    expect(screen.queryByText("Waiting for your decision")).toBeNull();
  });
});

describe("a row that needs a person, on the run page", () => {
  function posts(
    stub: { requests: { method: string; path: string; body: unknown }[] },
    path: string,
  ) {
    return stub.requests.filter((call) => call.method === "POST" && call.path === path);
  }

  it("offers Try again on a row that failed, and puts that row back in the queue", async () => {
    const fetchStub = createFetchStub({
      ...SHELL,
      "/api/v1/runs/run-1/records": {
        body: [
          record({ id: "rr-2", record_key: "sam@example.com", state: "failed", reason: "missing email" }),
        ],
      },
      "/api/v1/runs/run-1": { body: runDetail({ status: "completed" }) },
      "/api/v1/records/rr-2/requeue": { body: { record_id: "rr-2", state: "pending" } },
    });
    stub = fetchStub;
    renderApp("/runs/run-1");
    await settle();

    const beforeRun = callsTo(fetchStub, "/api/v1/runs/run-1");
    const beforeRecords = callsTo(fetchStub, "/api/v1/runs/run-1/records");
    expect(screen.getByRole("button", { name: "Dismiss" })).toBeTruthy();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    });
    await settle();

    const sent = posts(fetchStub, "/api/v1/records/rr-2/requeue");
    expect(sent).toHaveLength(1);
    expect(sent[0]?.body).toEqual({});
    // The page reloads what it shows, so the row's new state is the API's answer and not a guess.
    expect(callsTo(fetchStub, "/api/v1/runs/run-1/records")).toBeGreaterThan(beforeRecords);
    expect(callsTo(fetchStub, "/api/v1/runs/run-1")).toBeGreaterThan(beforeRun);
  });

  it("keeps Try again off for a row that may already be saved, until the tick", async () => {
    const fetchStub = createFetchStub({
      ...SHELL,
      "/api/v1/runs/run-1/records": {
        body: [
          record({ id: "rr-4", record_key: "Felix", state: "parked", commit_attempted: true }),
        ],
      },
      "/api/v1/runs/run-1": { body: runDetail({ status: "completed" }) },
      "/api/v1/records/rr-4/requeue": { body: { record_id: "rr-4", state: "pending" } },
    });
    stub = fetchStub;
    const view = renderApp("/runs/run-1");
    await settle();

    const box = view.container.querySelector<HTMLInputElement>(".destination-check-box");
    expect(box).not.toBeNull();
    expect(box?.checked).toBe(false);
    expect(view.container.querySelector(".destination-check-label")?.textContent).toBe(
      DESTINATION_CHECK_LABEL,
    );
    expect(
      view.container.querySelector(".destination-check-why")?.textContent?.trim(),
    ).not.toBe("");
    const tryAgain = screen.getByRole("button", { name: "Try again" }) as HTMLButtonElement;
    expect(tryAgain.disabled).toBe(true);

    // Clicking a disabled button sends nothing.
    fireEvent.click(tryAgain);
    await settle();
    expect(posts(fetchStub, "/api/v1/records/rr-4/requeue")).toHaveLength(0);

    await act(async () => {
      fireEvent.click(box as HTMLInputElement);
    });
    await settle();
    expect(
      (screen.getByRole("button", { name: "Try again" }) as HTMLButtonElement).disabled,
    ).toBe(false);

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    });
    await settle();
    const sent = posts(fetchStub, "/api/v1/records/rr-4/requeue");
    expect(sent).toHaveLength(1);
    expect(sent[0]?.body).toEqual({ destination_checked_by: OPERATOR_ID });
  });

  it("shows no actions on a row that is done", async () => {
    const fetchStub = createFetchStub({
      ...SHELL,
      "/api/v1/runs/run-1/records": {
        body: [record(), record({ id: "rr-3", record_key: "kim@example.com", state: "skipped" })],
      },
      "/api/v1/runs/run-1": { body: runDetail({ status: "completed" }) },
    });
    stub = fetchStub;
    const view = renderApp("/runs/run-1");
    await settle();

    expect(view.container.querySelector(".record-requeue")).toBeNull();
    expect(view.container.querySelector(".record-dismiss")).toBeNull();
    expect(view.container.querySelector(".destination-check-box")).toBeNull();
    expect(screen.queryByRole("button", { name: /Try all/ })).toBeNull();
  });

  it("tries every row it can again, and names the row that needs a check first", async () => {
    const fetchStub = createFetchStub({
      ...SHELL,
      "/api/v1/runs/run-1/records": {
        body: [
          record({ id: "rr-2", record_key: "sam@example.com", state: "failed", reason: "missing email" }),
          record({ id: "rr-3", record_key: "kim@example.com", state: "parked", reason: "a value is missing" }),
          record({ id: "rr-4", record_key: "Felix", state: "parked", commit_attempted: true }),
        ],
      },
      "/api/v1/runs/run-1": { body: runDetail({ status: "completed" }) },
      "/api/v1/records/rr-2/requeue": { body: { record_id: "rr-2", state: "pending" } },
      "/api/v1/records/rr-3/requeue": { body: { record_id: "rr-3", state: "pending" } },
      "/api/v1/records/rr-4/requeue": { body: { record_id: "rr-4", state: "pending" } },
    });
    stub = fetchStub;
    renderApp("/runs/run-1");
    await settle();

    expect(screen.getByText("Felix needs a check first.")).toBeTruthy();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Try all 2 again" }));
    });
    await settle();

    expect(posts(fetchStub, "/api/v1/records/rr-2/requeue")).toHaveLength(1);
    expect(posts(fetchStub, "/api/v1/records/rr-3/requeue")).toHaveLength(1);
    // A row that may already be saved is never swept up by the batch action.
    expect(posts(fetchStub, "/api/v1/records/rr-4/requeue")).toHaveLength(0);
  });

  it("shows the rows in plain words, with no engine word on the page", async () => {
    const fetchStub = createFetchStub({
      ...SHELL,
      "/api/v1/runs/run-1/records": {
        body: [
          record({ id: "rr-2", record_key: "sam@example.com", state: "failed", reason: "missing email" }),
          record({ id: "rr-4", record_key: "Felix", state: "parked", commit_attempted: true }),
        ],
      },
      "/api/v1/runs/run-1": { body: runDetail({ status: "completed" }) },
    });
    stub = fetchStub;
    const view = renderApp("/runs/run-1");
    await settle();

    expect(engineWordsIn(view.container.textContent ?? "")).toEqual([]);
  });

  it("shows Running again and starts polling when a finished run is put back to work", async () => {
    vi.useFakeTimers();
    let status = "completed";
    const fetchStub = createFetchStub({
      ...SHELL,
      "/api/v1/runs/run-1/records": {
        body: [record({ id: "rr-2", record_key: "sam@example.com", state: "failed", reason: "missing email" })],
      },
      "/api/v1/runs/run-1": () => ({ body: runDetail({ status }) }),
      "/api/v1/records/rr-2/requeue": () => {
        status = "running";
        return { body: { record_id: "rr-2", state: "pending" } };
      },
    });
    stub = fetchStub;
    renderApp("/runs/run-1");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(screen.getByText("Finished")).toBeTruthy();

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(screen.getByText("Running again")).toBeTruthy();

    // The poll had stopped for the finished run; the row re-queued, so it must start again.
    const before = callsTo(fetchStub, "/api/v1/runs/run-1");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS);
    });
    expect(callsTo(fetchStub, "/api/v1/runs/run-1")).toBeGreaterThan(before);
  });
});

describe("the page's poll", () => {
  it("asks every two seconds while the run is open and stops for good once it finishes", async () => {
    vi.useFakeTimers();
    let status = "running";
    const fetchStub = createFetchStub({
      ...SHELL,
      "/api/v1/runs/run-1/records": { body: [record()] },
      "/api/v1/runs/run-1": () => ({ body: runDetail({ status }) }),
    });
    stub = fetchStub;

    renderApp("/runs/run-1");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    const first = callsTo(fetchStub, "/api/v1/runs/run-1");
    expect(first).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "Pause" })).toBeTruthy();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(callsTo(fetchStub, "/api/v1/runs/run-1")).toBeGreaterThan(first);

    // The API now reports the run as finished: the next refresh must stop the poll for good.
    status = "completed";
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(screen.queryByRole("button", { name: "Pause" })).toBeNull();

    const afterFinish = callsTo(fetchStub, "/api/v1/runs/run-1");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });
    expect(callsTo(fetchStub, "/api/v1/runs/run-1")).toBe(afterFinish);
  });

  it("asks nothing at all of a run that is already finished", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    const fetchStub = createFetchStub({
      ...SHELL,
      "/api/v1/runs/run-1/records": { body: [] },
      "/api/v1/runs/run-1": { body: runDetail({ status: "failed" }) },
    });
    stub = fetchStub;

    renderApp("/runs/run-1");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    const afterLoad = callsTo(fetchStub, "/api/v1/runs/run-1");
    expect(afterLoad).toBeGreaterThan(0);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });
    expect(callsTo(fetchStub, "/api/v1/runs/run-1")).toBe(afterLoad);
  });

  it("polls on the interval the design fixes", () => {
    expect(POLL_INTERVAL_MS).toBe(2000);
  });
});

describe("a hostile value is rendered as text", () => {
  it("shows a row's key and reason as characters, and never as elements", async () => {
    const HOSTILE = '<img src=x onerror=alert(1)>ACME-1';
    stub = createFetchStub({
      ...SHELL,
      "/api/v1/runs/run-1/records": {
        body: [record({ record_key: HOSTILE, state: "parked", reason: HOSTILE })],
      },
      "/api/v1/runs/run-1": { body: runDetail() },
    });
    const view = renderApp("/runs/run-1");
    await settle();

    expect(view.container.querySelectorAll("img")).toHaveLength(0);
    expect(view.container.querySelectorAll("script")).toHaveLength(0);
    expect(screen.getByText(HOSTILE)).toBeTruthy();
  });
});


describe("a run paused at a site's daily cap (PL-067)", () => {
  it("names the site and its limit, and keeps Resume disabled while the count stands", async () => {
    stub = createFetchStub({
      ...SHELL,
      "/api/v1/runs/run-1/records": { body: [] },
      "/api/v1/runs/run-1": {
        body: runDetail({
          status: "paused",
          pause_reason: "site_daily_cap",
          site_cap: { origin: "https://www.linkedin.com/", limit: 25, used: 25 },
        }),
      },
    });
    renderApp("/runs/run-1");
    await settle();

    expect(
      screen.getAllByText(
        "Paused: today's limit for linkedin.com (25 rows) is reached. It can continue tomorrow.",
      ).length,
    ).toBeGreaterThan(0);
    const resume = screen.getByRole("button", { name: "Resume" }) as HTMLButtonElement;
    expect(resume.disabled).toBe(true);
  });
});
