/** The tasks a person has taught: what a person sees on the list, and the way into each task. */

import { screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { TaskRow } from "../src/types";
import { createFetchStub, renderApp, settle, type FetchStub } from "./helpers";

/* Every path the Shell asks for as well, so a page test never hits the real API. */
const SHELL = {
  "/api/v1/needs-help": { body: [] },
  "/api/v1/executor/session": { body: { attached: false } },
};

let stub: FetchStub | null = null;

afterEach(() => {
  stub?.restore();
  stub = null;
});

function task(overrides: Partial<TaskRow> = {}): TaskRow {
  return {
    workflow_id: "wf-1",
    recipe_version_id: "rv-1",
    name: "Add inquiries",
    app: "crm.example.com",
    last_run: null,
    ...overrides,
  };
}

describe("the task list", () => {
  it("shows each task, its site, its last run and its result", async () => {
    const tasks: TaskRow[] = [
      task(),
      task({
        workflow_id: "wf-2",
        name: "Send invoices",
        app: "billing.example.com",
        last_run: {
          run_id: "run-9",
          status: "completed",
          started_at: new Date().toISOString(),
          counts: { verified: 4, skipped: 1 },
        },
      }),
    ];
    stub = createFetchStub({ ...SHELL, "/api/v1/tasks": { body: tasks } });
    renderApp("/tasks");
    await settle();

    expect(screen.getByText("Add inquiries")).toBeTruthy();
    expect(screen.getByText("crm.example.com")).toBeTruthy();
    expect(screen.getAllByText("Ready").length).toBeGreaterThan(0);
    expect(screen.getByText("Send invoices")).toBeTruthy();
    expect(screen.getAllByText("Finished").length).toBeGreaterThan(0);
    expect(screen.getByText("Now")).toBeTruthy();
  });

  it("offers 'Try one row' to a task that never ran and 'Run again' to one that did", async () => {
    const tasks: TaskRow[] = [
      task(),
      task({
        workflow_id: "wf-2",
        name: "Send invoices",
        last_run: {
          run_id: "run-9",
          status: "completed",
          started_at: "2026-10-01T09:00:00Z",
          counts: { verified: 4, skipped: 1 },
        },
      }),
    ];
    stub = createFetchStub({ ...SHELL, "/api/v1/tasks": { body: tasks } });
    renderApp("/tasks");
    await settle();

    // The run flow arrives in a later ticket, so both labels open the task page.
    const tryOne = screen.getByRole("link", { name: "Try one row" });
    expect(tryOne.getAttribute("href")).toBe("/tasks/wf-1");
    const again = screen.getByRole("link", { name: "Run again" });
    expect(again.getAttribute("href")).toBe("/tasks/wf-2");
  });

  it("opens the task when its name is clicked", async () => {
    stub = createFetchStub({ ...SHELL, "/api/v1/tasks": { body: [task()] } });
    renderApp("/tasks");
    await settle();

    expect(screen.getByRole("link", { name: "Add inquiries" }).getAttribute("href")).toBe(
      "/tasks/wf-1",
    );
  });

  it("shows the task's own run while it is going", async () => {
    stub = createFetchStub({
      ...SHELL,
      "/api/v1/tasks": {
        body: [
          task({
            last_run: {
              run_id: "run-9",
              status: "running",
              started_at: new Date().toISOString(),
              counts: { verified: 3, skipped: 1, pending: 6 },
            },
          }),
        ],
      },
    });
    renderApp("/tasks");
    await settle();

    expect(screen.getByRole("link", { name: "Open" }).getAttribute("href")).toBe("/runs/run-9");
    expect(screen.getAllByText("Running").length).toBeGreaterThan(0);
  });

  it("asks a person to teach a task when there are none", async () => {
    stub = createFetchStub({ ...SHELL, "/api/v1/tasks": { body: [] } });
    renderApp("/tasks");
    await settle();

    expect(screen.getByRole("link", { name: "Teach your first task" }).getAttribute("href")).toBe(
      "/teach",
    );
    expect(screen.getByText(/will appear here/)).toBeTruthy();
  });
});

describe("a cleaned document waiting for a task (PL-076)", () => {
  it("says which document's rows are waiting, and passes the token to the task's run link", async () => {
    stub = createFetchStub({ ...SHELL, "/api/v1/tasks": { body: [task()] } });
    renderApp("/tasks?csv=tok-123&name=spring%20leads.docx");
    await settle();

    expect(
      screen.getByText("Pick the task to use spring leads.docx's rows with."),
    ).toBeTruthy();
    const tryOne = screen.getByRole("link", { name: "Try one row" }) as HTMLAnchorElement;
    const href = tryOne.getAttribute("href") ?? "";
    expect(href).toContain("/tasks/wf-1");
    expect(href).toContain("csv=tok-123");
  });

  it("falls back to document.csv when the link carried no name", async () => {
    stub = createFetchStub({ ...SHELL, "/api/v1/tasks": { body: [task()] } });
    renderApp("/tasks?csv=tok-123");
    await settle();

    expect(screen.getByText("Pick the task to use document.csv's rows with.")).toBeTruthy();
  });
});
