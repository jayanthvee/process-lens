import { screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { callsTo, createFetchStub, renderApp, settle } from "./helpers";

const NOW = new Date();

const HOME = {
  window_days: 7,
  counts: { done: 412, already_there: 3, needs_help: 2 },
  minutes_saved: 34,
  needs_help: [
    {
      record_id: "rec-1",
      run_id: "run-1",
      task_name: "Add inquiries",
      row: "ACME-1",
      state: "parked",
      reason: "missing email",
      commit_attempted: false,
    },
    {
      record_id: "rec-2",
      run_id: "run-1",
      task_name: "Add inquiries",
      row: "ACME-2",
      state: "failed",
      reason: null,
      commit_attempted: true,
    },
  ],
  running: [
    { run_id: "run-1", task_name: "Add inquiries", done: 11, total: 20, already_there: 2 },
  ],
  tasks: [
    {
      workflow_id: "wf-1",
      recipe_version_id: "rv-1",
      name: "Add inquiries",
      app: "crm.example.com",
      last_run: {
        run_id: "run-1",
        status: "running",
        started_at: NOW.toISOString(),
        counts: { verified: 11, parked: 1 },
      },
    },
    {
      workflow_id: "wf-2",
      recipe_version_id: "rv-2",
      name: "Update prices",
      app: "inventory.example.com",
      last_run: {
        run_id: "run-2",
        status: "completed",
        started_at: new Date(NOW.getTime() - 2 * 3600_000).toISOString(),
        counts: { verified: 4, skipped: 1 },
      },
    },
    {
      workflow_id: "wf-3",
      recipe_version_id: "rv-3",
      name: "Add leads",
      app: null,
      last_run: null,
    },
  ],
};

/** What the Shell asks for on the way to Home: the help count and the extension's status. */
const SHELL = {
  "/api/v1/needs-help": { body: HOME.needs_help },
  "/api/v1/executor/session": { body: { attached: true } },
};

const EMPTY = {
  window_days: 7,
  counts: { done: 0, already_there: 0, needs_help: 0 },
  minutes_saved: 0,
  needs_help: [],
  running: [],
  tasks: [],
};

afterEach(() => {
  vi.useRealTimers();
});

describe("Home", () => {
  it("shows the week's real numbers in plain words", async () => {
    const stub = createFetchStub({ "/api/v1/home": { body: HOME }, ...SHELL });
    renderApp("/");
    await settle();

    const headline = screen.getByRole("heading", { level: 1 });
    expect(headline.textContent).toContain("412 rows done this week.");
    expect(headline.textContent).toContain("2 need you.");
    expect(screen.getByText("3 rows were already there.")).toBeTruthy();

    // The gold figure is the minutes saved, in hours and minutes.
    expect(screen.getByText("34 m")).toBeTruthy();
    expect(screen.getByText("Time saved this week")).toBeTruthy();

    // The two panels: the rows that need a person, and the live run's progress.
    expect(screen.getByText("ACME-1")).toBeTruthy();
    expect(screen.getByText("11 of 20 done")).toBeTruthy();
    expect(screen.getByText("2 already there")).toBeTruthy();

    // My tasks: the name, the site it may use, when it last ran and one quiet button per row.
    expect(screen.getByText("crm.example.com")).toBeTruthy();
    expect(screen.getByText("Now")).toBeTruthy();
    expect(screen.getByText("2 hours ago")).toBeTruthy();
    expect(screen.getByText("Never")).toBeTruthy();
    expect(screen.getAllByRole("link", { name: "Run again" })).toHaveLength(1);
    expect(screen.getAllByRole("link", { name: "Try one row" })).toHaveLength(1);
    // Two on the help panel, one on the task whose run is still going.
    expect(screen.getAllByRole("link", { name: "Open" })).toHaveLength(3);

    stub.restore();
  });

  it("says so when nothing has run yet and offers the one quiet way to start", async () => {
    const stub = createFetchStub({ "/api/v1/home": { body: EMPTY }, ...SHELL });
    renderApp("/");
    await settle();

    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Nothing has run yet.");
    expect(screen.getAllByRole("link", { name: "Teach a new task" }).length).toBeGreaterThan(0);
    expect(screen.getByText("Nothing needs you right now.")).toBeTruthy();
    expect(screen.getByText("Nothing is running right now.")).toBeTruthy();

    stub.restore();
  });

  it("stops polling the moment no run is going", async () => {
    // Only the interval is faked, so `settle()`'s own timeout still runs and the 2 s poll fires
    // exactly when this test advances the clock.
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    let live = true;
    const stub = createFetchStub({
      "/api/v1/home": () => ({ body: live ? HOME : { ...HOME, running: [] } }),
      ...SHELL,
    });
    renderApp("/");
    await settle();
    const firstCount = callsTo(stub, "/api/v1/home");
    expect(firstCount).toBeGreaterThanOrEqual(1);

    // While a run is going, every tick asks again.
    vi.advanceTimersByTime(2000);
    await settle();
    const whileLive = callsTo(stub, "/api/v1/home");
    expect(whileLive).toBeGreaterThan(firstCount);
    vi.advanceTimersByTime(2000);
    await settle();
    expect(callsTo(stub, "/api/v1/home")).toBeGreaterThan(whileLive);

    // The run ends: the next ask says nothing is running, and the page never asks again.
    live = false;
    vi.advanceTimersByTime(2000);
    await settle();
    const afterStop = callsTo(stub, "/api/v1/home");
    vi.advanceTimersByTime(8000);
    await settle();
    expect(callsTo(stub, "/api/v1/home")).toBe(afterStop);

    stub.restore();
  });
});
