/**
 * The shell's two bars, and the run controls they stand down.
 *
 * What this file proves: "An admin paused all runs. Nothing new will start." appears while runs are
 * frozen; "Your Chrome extension is out of date. Update it to keep running tasks." appears while the
 * reported extension is below the minimum; both appear, the pause first, when both hold; the controls
 * that start a run are disabled while either holds; and nothing at all is drawn -- no bar, no empty
 * strip, no sentence -- when neither holds or the read fails, in which case the run control still
 * takes a person to the run flow.
 */

import { cleanup, fireEvent, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { WorkspaceState } from "../src/types";
import {
  createFetchStub,
  renderApp,
  settle,
  type FetchStub,
  type StubRoutes,
} from "./helpers";

const FROZEN = "An admin paused all runs. Nothing new will start.";
const OUT_OF_DATE = "Your Chrome extension is out of date. Update it to keep running tasks.";

const TASK = {
  workflow_id: "wf-1",
  recipe_version_id: "rv-1",
  name: "Add inquiries",
  app: "crm.example.com",
  last_run: null,
};

const HOME = {
  window_days: 7,
  counts: { done: 4, already_there: 1, needs_help: 0 },
  minutes_saved: 12,
  needs_help: [],
  running: [],
  tasks: [TASK],
};

const CHECK = {
  batch_id: "batch-1",
  row_count: 2,
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
  ],
  rows_ok: 2,
  rows_need_a_look: [],
  first_ready: { row: 1, values: { contact_name: "Priya Nair" } },
};

/** The workspace state the API reports, with nothing wrong in it unless a test says otherwise. */
function state(overrides: Partial<WorkspaceState> = {}): WorkspaceState {
  return {
    tenant_id: "t-1",
    runs_frozen: false,
    min_extension_version: null,
    extension_version: "1.2.0",
    extension_out_of_date: false,
    ...overrides,
  };
}

/**
 * Every path the Shell and the pages below ask for.
 *
 * A `null` workspace leaves `/api/v1/workspace/state` unmatched, and the stub answers its own 404 --
 * which is the read failing.
 */
function routes(workspace: WorkspaceState | null): StubRoutes {
  const all: StubRoutes = {
    "/api/v1/needs-help": { body: [] },
    "/api/v1/executor/session": { body: { attached: false } },
    "/api/v1/home": { body: HOME },
    "/api/v1/tasks": { body: [TASK] },
    "/api/v1/runs": { body: [] },
    "/api/v1/recipes/rv-1": {
      body: {
        id: "rv-1",
        workflow_id: "wf-1",
        workflow_name: "Add inquiries",
        version: 1,
        name: "Add inquiries",
        recipe: { steps: [] },
      },
    },
    "/api/v1/batches": { body: { batch_id: "batch-1", row_count: 2, columns: ["Name", "Email"] } },
    "/api/v1/batches/batch-1/check": { body: CHECK },
    "/api/v1/batches/batch-1/mapping": { body: { batch_id: "batch-1", mapping: {} } },
  };
  if (workspace !== null) all["/api/v1/workspace/state"] = { body: workspace };
  return all;
}

let stub: FetchStub | null = null;

function start(workspace: WorkspaceState | null, path = "/"): ReturnType<typeof renderApp> {
  stub = createFetchStub(routes(workspace));
  return renderApp(path);
}

afterEach(() => {
  stub?.restore();
  stub = null;
  cleanup();
});

describe("while an admin has paused all runs", () => {
  it("says so on Home", async () => {
    start(state({ runs_frozen: true }));
    await settle();

    expect(await screen.findByText(FROZEN)).toBeTruthy();
    // The bar reports a change, so a screen reader is told about it rather than left to find it.
    expect(document.querySelector(".banner")?.getAttribute("role")).toBe("status");
    expect(screen.queryByText(OUT_OF_DATE)).toBeNull();
  });

  it("turns the task page's way into the run flow into a disabled button", async () => {
    // Asserted on the task page `/tasks/wf-1`: the run page's "Try it" button only exists after a
    // spreadsheet has been added, and this is the same decision one screen earlier.
    start(state({ runs_frozen: true }), "/tasks/wf-1");
    await settle();

    const control = (await screen.findByRole("button", {
      name: "Run this task",
    })) as HTMLButtonElement;
    expect(control.disabled).toBe(true);
    expect(control.className).toBe("btn primary task-run-link");
    expect(screen.queryByRole("link", { name: "Run this task" })).toBeNull();
  });

  it("disables Try it on the run page once a spreadsheet is added", async () => {
    const view = start(state({ runs_frozen: true }), "/tasks/wf-1/run");
    await settle(4);

    const input = view.container.querySelector<HTMLInputElement>("input[type=file]");
    expect(input, "the run page offers a file input").not.toBeNull();
    fireEvent.change(input as HTMLInputElement, {
      target: { files: [new File(["Name,Email\nPriya Nair,priya@example.com\n"], "people.csv")] },
    });
    await settle(4);
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    await settle(4);

    const tryIt = screen.getByRole("button", { name: "Try it" }) as HTMLButtonElement;
    expect(tryIt.disabled).toBe(true);
  });
});

describe("while the Chrome extension is out of date", () => {
  it("says so, and does not also claim runs are paused", async () => {
    start(state({ extension_version: "1.0.0", min_extension_version: "2.0.0", extension_out_of_date: true }));
    await settle();

    expect(await screen.findByText(OUT_OF_DATE)).toBeTruthy();
    expect(screen.queryByText(FROZEN)).toBeNull();
  });

  it("shows both bars, the pause first, when both hold", async () => {
    start(
      state({ runs_frozen: true, extension_version: "1.0.0", extension_out_of_date: true }),
    );
    await settle();
    await screen.findByText(FROZEN);

    const bars = Array.from(document.querySelectorAll(".banner")).map(
      (bar) => bar.textContent ?? "",
    );
    expect(bars).toHaveLength(2);
    expect(bars[0]).toContain(FROZEN);
    expect(bars[1]).toContain(OUT_OF_DATE);
  });
});

describe("when there is nothing to say", () => {
  it("draws no bar at all while the workspace is fine", async () => {
    start(state());
    await settle();

    expect(screen.queryByText(FROZEN)).toBeNull();
    expect(screen.queryByText(OUT_OF_DATE)).toBeNull();
    expect(document.querySelectorAll(".banner")).toHaveLength(0);
  });

  it("says nothing when the read fails, and the run control still opens the run flow", async () => {
    start(null, "/tasks/wf-1");
    await settle();

    expect(screen.queryByText(FROZEN)).toBeNull();
    expect(screen.queryByText(OUT_OF_DATE)).toBeNull();
    expect(document.querySelectorAll(".banner")).toHaveLength(0);

    const link = await screen.findByRole("link", { name: "Run this task" });
    expect(link.getAttribute("href")).toBe("/tasks/wf-1/run");
    fireEvent.click(link);
    expect(await screen.findByText("1. Add your spreadsheet")).toBeTruthy();
  });
});
