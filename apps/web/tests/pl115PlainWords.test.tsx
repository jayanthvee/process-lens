/**
 * PL-115: Tasks, Runs, Needs your help and the panel header read plainly to someone who has never
 * seen ProcessLens.
 *
 * Each item in the ticket gets a test here that renders the screen and checks the words a person
 * sees. The last describe scans every screen this ticket owns for the engine's status words and for
 * a port number.
 */

import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { NeedsHelp } from "../src/pages/NeedsHelp";
import { PanelApp } from "../src/panel/PanelApp";
import type { NeedsHelpRow, RunSummary, TaskRow } from "../src/types";
import {
  createFetchStub,
  renderApp,
  settle,
  type FetchStub,
  type StubRoutes,
} from "./helpers";

const OPERATOR_ID = "0f8fad5b-d9cb-469f-a165-70867728950e";

const SHELL = {
  "/api/v1/needs-help": { body: [] },
  "/api/v1/executor/session": { body: { attached: true } },
  "/api/v1/operator": { body: { operator_id: OPERATOR_ID } },
};

let stub: FetchStub | null = null;

afterEach(() => {
  stub?.restore();
  stub = null;
  cleanup();
  vi.useRealTimers();
});

function task(overrides: Partial<TaskRow> = {}): TaskRow {
  return {
    workflow_id: "wf-1",
    recipe_version_id: "rv-1",
    name: "Record leads",
    app: "goodlabs.com",
    last_run: null,
    inputs: ["email"],
    ...overrides,
  };
}

function run(overrides: Partial<RunSummary> = {}): RunSummary {
  return {
    id: "run-1",
    status: "completed",
    pause_reason: null,
    created_at: null,
    recipe_name: "Add inquiries",
    recipe_version: "1",
    counts: { verified: 3, skipped: 1 },
    ...overrides,
  };
}

function helpRow(overrides: Partial<NeedsHelpRow> = {}): NeedsHelpRow {
  return {
    record_id: "rec-1",
    run_id: "run-1",
    task_name: "Add inquiries",
    row: "ACME-1",
    state: "parked",
    reason: "missing email",
    commit_attempted: false,
    ...overrides,
  };
}

function renderPanel(path = "/tasks"): void {
  render(
    <MemoryRouter initialEntries={[path]}>
      <PanelApp />
    </MemoryRouter>,
  );
}

function panelRoutes(tasks: TaskRow[], extra: StubRoutes = {}): StubRoutes {
  return {
    ...SHELL,
    "/api/v1/tasks": { body: tasks },
    "/api/v1/runs": { body: [] },
    ...extra,
  };
}

describe("item 1: task cards name the app, not the address", () => {
  it("shows Odoo for a loopback Odoo address and never a port", async () => {
    stub = createFetchStub(
      panelRoutes([
        task({ workflow_id: "wf-odoo", name: "Record leads", app: "localhost:8069" }),
        task({ workflow_id: "wf-ip", name: "Add a lead", app: "127.0.0.1:8069" }),
        task({ workflow_id: "wf-web", name: "Send invoices", app: "www.goodlabs.com" }),
      ]),
    );
    renderPanel();
    await settle();

    expect(screen.getAllByText("Odoo")).toHaveLength(2);
    expect(screen.getByText("goodlabs.com")).toBeTruthy();
    const text = document.body.textContent ?? "";
    expect(text).not.toContain("localhost:8069");
    expect(text).not.toContain("127.0.0.1:8069");
    expect(text).not.toMatch(/:\d{2,5}\b/);
  });
});

describe("item 2: default task names are not names", () => {
  it("shows Untitled task with the site, and offers no Rename until the server can rename", async () => {
    const fetchStub = createFetchStub(
      panelRoutes([
        task({
          workflow_id: "wf-default",
          name: "Recorded on goodlabs.com",
          app: "goodlabs.com",
        }),
      ]),
    );
    stub = fetchStub;
    renderPanel();
    await settle();

    expect(screen.getByText("Untitled task · goodlabs.com")).toBeTruthy();
    // T1 review B1: services/api serves no PATCH /tasks/{id}, so a Save here could only 404.
    // The card names the task and leaves renaming to the report's "Needs PL-113/114" list.
    expect(screen.queryByRole("button", { name: "Rename" })).toBeNull();
    expect(screen.queryByRole("textbox", { name: /task name/i })).toBeNull();
    expect(fetchStub.requests.filter((call) => call.method === "PATCH")).toHaveLength(0);
  });

  it("shows Recorded task as Untitled task when there is no site", async () => {
    stub = createFetchStub(
      panelRoutes([
        task({ workflow_id: "wf-bare", name: "Recorded task", app: null, inputs: [] }),
      ]),
    );
    renderPanel();
    await settle();

    expect(screen.getByText("Untitled task")).toBeTruthy();
  });
});

describe("item 3: one button per card that says what happens", () => {
  it("offers Run for a task with no inputs, Run with a spreadsheet, and the email pick for a sign-in", async () => {
    stub = createFetchStub(
      panelRoutes([
        task({ workflow_id: "wf-once", name: "Close the day", app: "erp.example.com", inputs: [] }),
        task({
          workflow_id: "wf-sheet",
          name: "Add inquiries",
          app: "crm.example.com",
          inputs: ["email", "company"],
        }),
        task({
          workflow_id: "wf-signin",
          name: "Sign in to the app",
          app: "app.example.test",
          inputs: ["email"],
          sign_in: true,
          sign_in_column: "email",
          sign_in_email: "a@example.com",
        }),
      ]),
    );
    renderPanel();
    await settle();

    const cards = screen.getAllByRole("listitem");
    expect(cards).toHaveLength(3);
    const byName = (name: string): HTMLElement => {
      const card = cards.find((entry) => entry.textContent?.includes(name));
      expect(card, `a card for ${name}`).toBeTruthy();
      return card as HTMLElement;
    };
    for (const card of cards) {
      const runButtons = within(card).queryAllByRole("button", { name: /^Run/ });
      const runLinks = within(card).queryAllByRole("link", { name: /^Run/ });
      expect(runButtons.length + runLinks.length).toBe(1);
    }
    // A task with no inputs runs once, and its one control says what happens: Run.
    expect(within(byName("Close the day")).getByRole("button", { name: "Run" })).toBeTruthy();
    expect(within(byName("Close the day")).queryByRole("link", { name: /Run/ })).toBeNull();
    // A spreadsheet task keeps its spreadsheet control.
    expect(
      within(byName("Add inquiries")).getByRole("link", { name: "Run with a spreadsheet" }),
    ).toBeTruthy();
    // A sign-in task shows the PL-112 email pick and one Run that sends it.
    const signIn = byName("Sign in to the app");
    expect(within(signIn).getByRole("combobox", { name: /email/i })).toBeTruthy();
    expect(within(signIn).getByRole("button", { name: "Run" })).toBeTruthy();
    expect(within(signIn).queryByRole("link", { name: /Run/ })).toBeNull();
    expect(screen.queryByRole("button", { name: "Run once" })).toBeNull();
  });
});

describe("item 4: the runs list says what each run did in one line", () => {
  it("writes a running, a finished and a stopped run as one sentence each", async () => {
    stub = createFetchStub({
      ...SHELL,
      "/api/v1/runs": {
        body: [
          run({
            id: "run-running",
            status: "running",
            recipe_name: "Add inquiries",
            counts: { verified: 3, skipped: 1, parked: 1 },
          }),
          run({
            id: "run-finished",
            status: "completed",
            recipe_name: "Send invoices",
            counts: { verified: 4 },
          }),
          run({
            id: "run-stopped",
            status: "cancelled",
            recipe_name: "Close the day",
            counts: { verified: 2 },
          }),
        ],
      },
    });
    renderApp("/runs");
    await settle();

    expect(screen.getByText("Added 3 of 5 · 1 already added · 1 needs you")).toBeTruthy();
    expect(screen.getByText("Finished: all 4 done")).toBeTruthy();
    expect(screen.getByText("Stopped by you after 2 rows")).toBeTruthy();
    expect(screen.queryByText("This task")).toBeNull();
  });

  it("says a paused run is waiting for you, and gives the reason", async () => {
    stub = createFetchStub({
      ...SHELL,
      "/api/v1/runs": {
        body: [
          run({
            id: "run-paused",
            status: "paused",
            pause_reason: "login_required",
            recipe_name: "Add inquiries",
            counts: { verified: 1 },
          }),
        ],
      },
    });
    renderApp("/runs");
    await settle();

    expect(
      screen.getByText(
        "Waiting for you: Log in to the site in the ProcessLens window, then resume.",
      ),
    ).toBeTruthy();
    // The button the sentence names sits on the same line, next to Open.
    expect(screen.getByRole("button", { name: "Resume" })).toBeTruthy();
  });

  it("sends a pause that needs the run page to Open, not a blind Resume", async () => {
    stub = createFetchStub({
      ...SHELL,
      "/api/v1/runs": {
        body: [
          run({
            id: "run-code",
            status: "paused",
            pause_reason: "code_required",
            recipe_name: "Sign in to the app",
            counts: { verified: 1 },
          }),
        ],
      },
    });
    renderApp("/runs");
    await settle();

    expect(
      screen.getByText(
        "Waiting for you: The site is asking for a verification code. Open the run and enter it.",
      ),
    ).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Resume" })).toBeNull();
    expect(screen.getByRole("link", { name: "Open" }).getAttribute("href")).toBe("/runs/run-code");
  });

  it("shows today's time with the hour, yesterday as yesterday, and dates only when older", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date(2026, 9, 27, 23, 55, 0));
    stub = createFetchStub({
      ...SHELL,
      "/api/v1/runs": {
        body: [
          run({
            id: "run-today",
            recipe_name: "Today's task",
            created_at: new Date(2026, 9, 27, 23, 49, 0).toISOString(),
            counts: { verified: 1 },
          }),
          run({
            id: "run-yesterday",
            recipe_name: "Yesterday's task",
            created_at: new Date(2026, 9, 26, 12, 0, 0).toISOString(),
            counts: { verified: 1 },
          }),
          run({
            id: "run-old",
            recipe_name: "Last week's task",
            created_at: new Date(2026, 9, 17, 12, 0, 0).toISOString(),
            counts: { verified: 1 },
          }),
        ],
      },
    });
    renderApp("/runs");
    await settle();

    expect(screen.getByText("today 11:49 PM")).toBeTruthy();
    expect(screen.getByText("yesterday")).toBeTruthy();
    expect(screen.getByText("Oct 17")).toBeTruthy();
  });
});

describe("item 5: already added or already done, for the kind of task", () => {
  it("says Already added for a task that adds things", async () => {
    stub = createFetchStub({
      ...SHELL,
      "/api/v1/runs": {
        body: [
          run({
            id: "run-add",
            status: "running",
            recipe_name: "Add inquiries",
            counts: { verified: 2, skipped: 1 },
          }),
        ],
      },
    });
    renderApp("/runs");
    await settle();

    expect(screen.getByText("Added 2 of 3 · 1 already added")).toBeTruthy();
  });

  it("says Already done for a task that changes things", async () => {
    stub = createFetchStub({
      ...SHELL,
      "/api/v1/runs": {
        body: [
          run({
            id: "run-change",
            status: "running",
            recipe_name: "Update prices",
            counts: { verified: 2, skipped: 1 },
          }),
        ],
      },
    });
    renderApp("/runs");
    await settle();

    expect(screen.getByText("Changed 2 of 3 · 1 already done")).toBeTruthy();
  });

  it("never says already for a sign-in task", async () => {
    stub = createFetchStub({
      ...SHELL,
      "/api/v1/runs": {
        body: [
          run({
            id: "run-signin",
            status: "completed",
            recipe_name: "Sign in to the app",
            counts: { verified: 1, skipped: 1 },
          }),
        ],
      },
    });
    renderApp("/runs");
    await settle();

    expect(screen.queryByText(/already/i)).toBeNull();
  });
});

describe("item 6: only the five status words", () => {
  it("shows Waiting to start, Running, Waiting for you, Finished and Stopped, and nothing else", async () => {
    stub = createFetchStub({
      ...SHELL,
      "/api/v1/runs": {
        body: [
          run({ id: "run-1", status: "queued", recipe_name: "First task", counts: {} }),
          run({ id: "run-2", status: "running", recipe_name: "Second task", counts: { verified: 1 } }),
          run({
            id: "run-3",
            status: "paused",
            pause_reason: "login_required",
            recipe_name: "Third task",
            counts: { verified: 1 },
          }),
          run({ id: "run-4", status: "completed", recipe_name: "Fourth task", counts: { verified: 1 } }),
          run({ id: "run-5", status: "cancelled", recipe_name: "Fifth task", counts: { verified: 1 } }),
        ],
      },
    });
    renderApp("/runs");
    await settle();

    for (const word of [
      "Waiting to start",
      "Running",
      "Waiting for you",
      "Finished",
      "Stopped",
    ]) {
      expect(screen.getAllByText(word).length).toBeGreaterThan(0);
    }
    const text = document.body.textContent ?? "";
    for (const banned of [
      "Not paused.",
      "queued",
      "preview",
      "completed",
      "parked",
      "reconciling",
      "submitted_unverified",
    ]) {
      expect(text.toLowerCase()).not.toContain(banned.toLowerCase());
    }
  });
});

describe("a finished run that left rows for you (T1 review B2)", () => {
  it("says Needs you, never a bare Finished", async () => {
    stub = createFetchStub({
      ...SHELL,
      "/api/v1/runs": {
        body: [
          run({
            id: "run-needs",
            status: "completed",
            recipe_name: "Add inquiries",
            counts: { verified: 11, parked: 3, failed: 1 },
          }),
        ],
      },
    });
    renderApp("/runs");
    await settle();

    // The owner's rule (PL-106 item 4): a finished run with rows left for a person is never
    // labelled a bare Finished. The badge names what is left; the sentence says how much got done.
    expect(screen.getByText("Needs you")).toBeTruthy();
    expect(screen.getByText("Finished: 11 done · 4 need you")).toBeTruthy();
    expect(screen.queryByText("Finished")).toBeNull();
  });
});

describe("item 7: a waiting run's decision sits on its line", () => {
  it("shows the sentence and the decision buttons on the runs list, and sends the decision", async () => {
    const fetchStub = createFetchStub({
      ...SHELL,
      "/api/v1/runs": {
        body: [
          run({
            id: "run-approval",
            status: "paused",
            pause_reason: "awaiting_approval",
            recipe_name: "Add inquiries",
            counts: { verified: 1, pending: 1 },
          }),
        ],
      },
      "/api/v1/runs/run-approval/approvals": {
        body: [
          {
            id: "approval-1",
            run_record_id: "rr-1",
            step_id: "save-lead",
            kind: "commit",
            shown: { value: "New enquiry", record: { email: "priya@example.com" } },
            created_at: "2026-10-27T10:00:00Z",
          },
        ],
      },
      "/api/v1/approvals": {
        body: { request_id: "approval-1", decision: "approved", run_id: "run-approval", resumed: true },
      },
    });
    stub = fetchStub;
    renderApp("/runs");
    await settle();

    expect(
      screen.getByText(
        "Waiting for you: An approval request is waiting for your decision, then resume.",
      ),
    ).toBeTruthy();
    const line = screen.getByText("Add inquiries").closest("li");
    expect(line).not.toBeNull();
    const goAhead = within(line as HTMLElement).getByRole("button", { name: "Go ahead" });
    expect(within(line as HTMLElement).getByRole("button", { name: "No, stop this row" })).toBeTruthy();

    await act(async () => {
      fireEvent.click(goAhead);
    });
    await settle();

    const sent = fetchStub.requests.find(
      (call) => call.method === "POST" && call.path === "/api/v1/approvals",
    );
    expect(sent?.body).toEqual({
      request_id: "approval-1",
      decision: "approved",
      decided_by: OPERATOR_ID,
    });
  });
});

describe("item 8: time saved only for a finished run that did something", () => {
  it("shows the rows and the estimate when rows were done", async () => {
    stub = createFetchStub({
      ...SHELL,
      "/api/v1/runs": {
        body: [
          run({
            id: "run-done",
            status: "completed",
            recipe_name: "Add inquiries",
            counts: { verified: 3 },
          }),
        ],
      },
    });
    renderApp("/runs");
    await settle();

    expect(screen.getByText("Saved about 9 minutes (3 rows × about 3 min by hand)")).toBeTruthy();
  });

  it("shows no time saved, and no by-hand line alone, on a run that did nothing", async () => {
    stub = createFetchStub({
      ...SHELL,
      "/api/v1/runs": {
        body: [
          run({
            id: "run-empty",
            status: "completed",
            recipe_name: "Add inquiries",
            counts: {},
          }),
          run({
            id: "run-all-there",
            status: "completed",
            recipe_name: "Send invoices",
            counts: { skipped: 4 },
          }),
        ],
      },
    });
    renderApp("/runs");
    await settle();

    expect(screen.queryByText(/Saved about/)).toBeNull();
    expect(screen.queryByText(/By hand/)).toBeNull();
  });
});

describe("item 9: empty and loading states say what to do next", () => {
  it("says No runs yet, and points at Tasks", async () => {
    stub = createFetchStub({ ...SHELL, "/api/v1/runs": { body: [] } });
    renderApp("/runs");
    await settle();

    expect(screen.getByText("No runs yet. Pick a task in Tasks and press Run.")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Go to your tasks" }).getAttribute("href")).toBe(
      "/tasks",
    );
  });

  it("says Nothing needs you right now on an empty Needs your help", async () => {
    stub = createFetchStub({ ...SHELL, "/api/v1/needs-help": { body: [] } });
    renderApp("/needs-help");
    await settle();

    expect(screen.getByText("Nothing needs you right now.")).toBeTruthy();
  });

  it("says it is loading before Needs your help has an answer", () => {
    const original = globalThis.fetch;
    globalThis.fetch = (() => new Promise(() => {})) as unknown as typeof fetch;
    const view = render(
      <MemoryRouter initialEntries={["/needs-help"]}>
        <NeedsHelp />
      </MemoryRouter>,
    );

    expect(screen.getByText("Loading what needs you…")).toBeTruthy();
    view.unmount();
    globalThis.fetch = original;
  });
});

describe("item 10: the header's connection line", () => {
  it("keeps Connect on every tab while Not connected", async () => {
    stub = createFetchStub({
      ...SHELL,
      "/api/v1/executor/session": { body: { attached: false } },
      "/api/v1/tasks": { body: [task()] },
      "/api/v1/runs": { body: [] },
    });

    for (const path of ["/tasks", "/runs", "/teach"]) {
      renderPanel(path);
      await settle();
      expect(screen.getByText("Not connected")).toBeTruthy();
      expect(screen.getByRole("button", { name: "Connect" })).toBeTruthy();
      cleanup();
    }
  });

  it("says Connected as quiet text, not a button-shaped box", async () => {
    stub = createFetchStub({
      ...SHELL,
      "/api/v1/tasks": { body: [task()] },
      "/api/v1/runs": { body: [] },
    });
    renderPanel();
    await settle();

    const connected = screen.getByText("Connected");
    expect(connected.closest("button")).toBeNull();
    expect(connected.classList.contains("panel-connect-quiet")).toBe(true);
  });
});

describe("the banned words and the port number are absent from every screen", () => {
  const BANNED = [
    "Not paused.",
    "queued",
    "preview",
    "completed",
    "parked",
    "reconciling",
    "submitted_unverified",
  ];

  /** Every text node a person can see, one per line: concatenated text would hide a port that
   * runs straight into the next word. */
  function visibleText(): string {
    const parts: string[] = [];
    const walk = (node: Node): void => {
      if (node.nodeType === Node.TEXT_NODE) {
        parts.push(node.nodeValue ?? "");
        return;
      }
      if (node.nodeType !== Node.ELEMENT_NODE) return;
      const element = node as HTMLElement;
      if (element.tagName === "SCRIPT" || element.tagName === "STYLE") return;
      for (const child of Array.from(node.childNodes)) walk(child);
    };
    walk(document.body);
    return parts.join("\n");
  }

  function expectPlain(text: string, where: string): void {
    for (const banned of BANNED) {
      expect(text.toLowerCase(), `${where} must not say "${banned}"`).not.toContain(
        banned.toLowerCase(),
      );
    }
    expect(text, `${where} must not show a port`).not.toMatch(/:\d{2,5}\b/);
  }

  it("Tasks", async () => {
    stub = createFetchStub(
      panelRoutes([
        task({ workflow_id: "wf-1", name: "Add inquiries", app: "localhost:8069" }),
        task({ workflow_id: "wf-2", name: "Recorded on goodlabs.com", app: "goodlabs.com" }),
        task({ workflow_id: "wf-3", name: "Recorded task", app: null, inputs: [] }),
        task({
          workflow_id: "wf-4",
          name: "Sign in to the app",
          app: "app.example.test",
          sign_in: true,
          sign_in_column: "email",
          sign_in_email: "a@example.com",
        }),
      ]),
    );
    renderPanel();
    await settle();
    expect(screen.getByText("Add inquiries")).toBeTruthy();
    expect(screen.getByText("Untitled task")).toBeTruthy();
    expectPlain(visibleText(), "Tasks");
  });

  it("Runs", async () => {
    stub = createFetchStub({
      ...SHELL,
      "/api/v1/runs": {
        body: [
          run({
            id: "run-1",
            status: "running",
            recipe_name: "Add inquiries",
            created_at: null,
            counts: { verified: 2, skipped: 1, parked: 1 },
          }),
          run({
            id: "run-2",
            status: "completed",
            recipe_name: "Send invoices",
            created_at: null,
            counts: { verified: 4 },
          }),
          run({
            id: "run-3",
            status: "cancelled",
            recipe_name: "Close the day",
            created_at: null,
            counts: { verified: 1 },
          }),
          run({
            id: "run-4",
            status: "paused",
            pause_reason: "login_required",
            recipe_name: "Update prices",
            created_at: null,
            counts: { verified: 1 },
          }),
        ],
      },
    });
    renderApp("/runs");
    await settle();
    expect(screen.getByText("Add inquiries")).toBeTruthy();
    expectPlain(visibleText(), "Runs");
  });

  it("Needs your help", async () => {
    stub = createFetchStub({
      ...SHELL,
      "/api/v1/needs-help": {
        body: [
          helpRow({ record_id: "rec-1", reason: "missing email" }),
          helpRow({
            record_id: "rec-2",
            row: "ACME-2",
            reason: "Not found in crm.example.com.",
          }),
        ],
      },
    });
    renderApp("/needs-help");
    await settle();
    expect(screen.getByText("ACME-1")).toBeTruthy();
    expectPlain(visibleText(), "Needs your help");
  });

  it("the website's Tasks page", async () => {
    stub = createFetchStub({
      ...SHELL,
      "/api/v1/tasks": {
        body: [
          task({
            workflow_id: "wf-paused",
            name: "Add inquiries",
            app: "localhost:8069",
            last_run: {
              run_id: "run-1",
              status: "paused",
              started_at: "2026-10-27T10:00:00Z",
              counts: { verified: 1, parked: 1 },
            },
          }),
          task({
            workflow_id: "wf-queued",
            name: "Send invoices",
            app: "www.goodlabs.com",
            last_run: {
              run_id: "run-2",
              status: "queued",
              started_at: "2026-10-27T10:00:00Z",
              counts: {},
            },
          }),
          task({
            workflow_id: "wf-failed",
            name: "Update prices",
            app: "crm.example.com",
            last_run: {
              run_id: "run-3",
              status: "failed",
              started_at: "2026-10-27T10:00:00Z",
              counts: { failed: 1 },
            },
          }),
          task({
            workflow_id: "wf-untitled",
            name: "Recorded on recorded.example.com",
            app: "recorded.example.com",
            inputs: [],
          }),
        ],
      },
    });
    renderApp("/tasks");
    await settle();

    expect(screen.getAllByText("Waiting for you").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Waiting to start").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Stopped").length).toBeGreaterThan(0);
    expect(screen.getByText("Odoo")).toBeTruthy();
    expect(screen.getByText("goodlabs.com")).toBeTruthy();
    // The site is named once: in "Untitled task · recorded.example.com", never twice.
    expect(screen.getByText("Untitled task · recorded.example.com")).toBeTruthy();
    expect(screen.getAllByText(/recorded\.example\.com/)).toHaveLength(1);
    expectPlain(visibleText(), "the website's Tasks page");
  });
});
