/**
 * PL-120: removing a task from the list, and Undo.
 *
 * Both task surfaces are exercised: the web Tasks page (a table) and the side panel's Tasks tab (a
 * list of cards). Remove asks on the task itself, Keep it leaves it alone, Remove calls the
 * endpoint and the card goes away, Undo calls restore, and a 409's own sentence stays on the card.
 */

import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";

import { PanelTasks } from "../src/panel/PanelTasks";
import type { TaskRow } from "../src/types";
import { createFetchStub, renderApp, settle, type FetchStub, type StubRoutes } from "./helpers";

/* Every path the Shell asks for as well, so a page test never hits the real API. */
const SHELL = {
  "/api/v1/needs-help": { body: [] },
  "/api/v1/executor/session": { body: { attached: false } },
};

const QUESTION = 'Remove "Add inquiries" from the list? Its past runs stay.';
const STOP_FIRST = "Stop its run first.";

let stub: FetchStub | null = null;

afterEach(() => {
  stub?.restore();
  stub = null;
  cleanup();
});

function task(overrides: Partial<TaskRow> = {}): TaskRow {
  return {
    workflow_id: "wf-1",
    recipe_version_id: "rv-1",
    name: "Add inquiries",
    app: "crm.example.com",
    last_run: null,
    inputs: [],
    ...overrides,
  };
}

function renderPanel(): void {
  render(
    <MemoryRouter initialEntries={["/tasks"]}>
      <PanelTasks />
    </MemoryRouter>,
  );
}

function removalRoutes(status = 200, detail = "Not found"): StubRoutes {
  return {
    "/api/v1/tasks": { body: [task()] },
    "/api/v1/tasks/wf-1/remove":
      status === 200 ? { body: { workflow_id: "wf-1", removed: true } } : { status, body: { detail } },
    "/api/v1/tasks/wf-1/restore": { body: { workflow_id: "wf-1", removed: false } },
  };
}

describe("the web Tasks page (PL-120)", () => {
  it("asks first, and Keep it leaves the task alone", async () => {
    stub = createFetchStub({ ...SHELL, ...removalRoutes() });
    renderApp("/tasks");
    await settle();

    fireEvent.click(screen.getByRole("button", { name: "Remove" }));
    const dialog = screen.getByRole("alertdialog");
    expect(within(dialog).getByText(QUESTION)).toBeTruthy();
    fireEvent.click(within(dialog).getByRole("button", { name: "Keep it" }));
    await settle();

    expect(screen.queryByRole("alertdialog")).toBeNull();
    expect(screen.getByText("Add inquiries")).toBeTruthy();
    expect(stub.requests.some((call) => call.path.endsWith("/remove"))).toBe(false);
  });

  it("Remove calls the endpoint, the card goes away, and Undo restores it", async () => {
    stub = createFetchStub({ ...SHELL, ...removalRoutes() });
    renderApp("/tasks");
    await settle();

    fireEvent.click(screen.getByRole("button", { name: "Remove" }));
    fireEvent.click(
      within(screen.getByRole("alertdialog")).getByRole("button", { name: "Remove" }),
    );
    await settle();

    const removed = stub.requests.find((call) => call.path === "/api/v1/tasks/wf-1/remove");
    expect(removed?.method).toBe("POST");
    expect(screen.queryByText("Add inquiries")).toBeNull();
    expect(screen.getByText(/Removed "Add inquiries"\./)).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    await settle();

    const restored = stub.requests.find((call) => call.path === "/api/v1/tasks/wf-1/restore");
    expect(restored?.method).toBe("POST");
    expect(screen.getByText("Add inquiries")).toBeTruthy();
    expect(screen.queryByText(/Removed "Add inquiries"\./)).toBeNull();
  });

  it("shows a 409's sentence on the task's row", async () => {
    stub = createFetchStub({ ...SHELL, ...removalRoutes(409, STOP_FIRST) });
    renderApp("/tasks");
    await settle();

    fireEvent.click(screen.getByRole("button", { name: "Remove" }));
    fireEvent.click(
      within(screen.getByRole("alertdialog")).getByRole("button", { name: "Remove" }),
    );
    await settle();

    expect(screen.getByText(STOP_FIRST)).toBeTruthy();
    expect(screen.getByText("Add inquiries")).toBeTruthy();
  });
});

describe("the panel Tasks tab (PL-120)", () => {
  it("asks first, and Keep it leaves the task alone", async () => {
    stub = createFetchStub(removalRoutes());
    renderPanel();
    await settle();

    fireEvent.click(screen.getByRole("button", { name: "Remove" }));
    const dialog = screen.getByRole("alertdialog");
    expect(within(dialog).getByText(QUESTION)).toBeTruthy();
    fireEvent.click(within(dialog).getByRole("button", { name: "Keep it" }));
    await settle();

    expect(screen.queryByRole("alertdialog")).toBeNull();
    expect(screen.getByText("Add inquiries")).toBeTruthy();
    expect(stub.requests.some((call) => call.path.endsWith("/remove"))).toBe(false);
  });

  it("Remove calls the endpoint, the card goes away, and Undo restores it", async () => {
    stub = createFetchStub(removalRoutes());
    renderPanel();
    await settle();

    fireEvent.click(screen.getByRole("button", { name: "Remove" }));
    fireEvent.click(
      within(screen.getByRole("alertdialog")).getByRole("button", { name: "Remove" }),
    );
    await settle();

    expect(
      stub.requests.find((call) => call.path === "/api/v1/tasks/wf-1/remove")?.method,
    ).toBe("POST");
    expect(screen.queryByText("Add inquiries")).toBeNull();
    expect(screen.getByText(/Removed "Add inquiries"\./)).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    await settle();

    expect(
      stub.requests.find((call) => call.path === "/api/v1/tasks/wf-1/restore")?.method,
    ).toBe("POST");
    expect(screen.getByText("Add inquiries")).toBeTruthy();
    expect(screen.queryByText(/Removed "Add inquiries"\./)).toBeNull();
  });

  it("shows a 409's sentence on the task's card", async () => {
    stub = createFetchStub(removalRoutes(409, STOP_FIRST));
    renderPanel();
    await settle();

    fireEvent.click(screen.getByRole("button", { name: "Remove" }));
    fireEvent.click(
      within(screen.getByRole("alertdialog")).getByRole("button", { name: "Remove" }),
    );
    await settle();

    expect(screen.getByText(STOP_FIRST)).toBeTruthy();
    expect(screen.getByText("Add inquiries")).toBeTruthy();
  });
});
