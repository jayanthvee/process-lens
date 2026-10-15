/**
 * The runs list and one run: the buttons follow the status, a refusal sits next to the button that
 * caused it, the pause reason is in words, and the live poll stops the moment the run is not open
 * (PL-015's verified behaviour, carried forward).
 */

import { act, fireEvent, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { RecordRow, RunDetail as RunPayload, RunSummary } from "../src/types";
import { callsTo, createFetchStub, renderApp, settle, type FetchStub } from "./helpers";

/* Every path the Shell asks for as well, so a page test never hits the real API. */
const SHELL = {
  "/api/v1/needs-help": { body: [] },
  "/api/v1/executor/session": { body: { attached: false } },
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
      screen.getByText("Log in to the site in the bound tab, then resume."),
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
    renderApp("/runs/run-1");
    await settle();

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Pause" }));
    });
    await settle();

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
});

