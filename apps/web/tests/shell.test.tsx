/** The shell: the sidebar of DESIGN.md §6, the header, the count, and the extension status. */

import { cleanup, fireEvent, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { createFetchStub, renderApp, settle, type FetchStub } from "./helpers";

const TASK = {
  workflow_id: "w-1",
  recipe_version_id: "r-1",
  name: "Add inquiries",
  app: "crm.example.com",
  last_run: null,
};

function stubWith(needsHelp: unknown[], attached: boolean): FetchStub {
  return createFetchStub({
    "/api/v1/needs-help": { body: needsHelp },
    "/api/v1/executor/session": { body: { attached } },
    "/api/v1/tasks": { body: [TASK] },
  });
}

let stub: FetchStub;

beforeEach(() => {
  stub = stubWith([], false);
});

afterEach(() => {
  stub.restore();
  cleanup();
});

describe("the sidebar", () => {
  it("shows every place the design names, in its groups", () => {
    renderApp("/");

    for (const label of ["Home", "Needs help", "Tasks", "Runs", "Spreadsheets", "Apps"]) {
      expect(screen.getByRole("link", { name: label }), label).toBeTruthy();
    }
    for (const group of ["Automate", "Your data", "Connect"]) {
      expect(screen.getByText(group)).toBeTruthy();
    }
  });

  it("routes each item to a real page, not to a placeholder href", () => {
    renderApp("/");

    expect(screen.getByRole("link", { name: "Runs" }).getAttribute("href")).toBe("/runs");
    expect(screen.getByRole("link", { name: "Spreadsheets" }).getAttribute("href")).toBe(
      "/spreadsheets",
    );
  });

  it("says Chrome not connected when no session is attached", async () => {
    renderApp("/");

    expect(await screen.findByText("Chrome not connected")).toBeTruthy();
    expect(screen.queryByText("Chrome connected")).toBeNull();
  });

  it("says Chrome connected when the API reports an attached session", async () => {
    stub.restore();
    stub = stubWith([], true);
    renderApp("/");

    expect(await screen.findByText("Chrome connected")).toBeTruthy();
  });

  it("shows the count of rows that need help only when there is something to count", async () => {
    stub.restore();
    stub = stubWith([{ record_id: "rec-1", run_id: "run-1", task_name: "Add inquiries", row: "ACME-1", state: "parked", reason: null, commit_attempted: false }], false);
    stub.calls.length = 0;
    const first = renderApp("/");
    const nav = screen.getByRole("link", { name: /Needs help/ });
    expect(await within(nav).findByText("1")).toBeTruthy();
    first.unmount();

    stub.restore();
    stub = stubWith([], false);
    renderApp("/");
    await settle();
    expect(within(screen.getByRole("link", { name: "Needs help" })).queryByText("1")).toBeNull();
  });
});

describe("the header", () => {
  it("shows the page title, one primary action and the avatar", () => {
    renderApp("/");

    expect(screen.getByRole("heading", { level: 2 }).textContent).toBe("Home");
    expect(screen.getByRole("link", { name: "Teach a new task" }).getAttribute("href")).toBe(
      "/teach",
    );
    expect(document.querySelectorAll(".btn.primary")).toHaveLength(1);
    expect(screen.getByLabelText("You")).toBeTruthy();
  });

  it("opens the search over task names with Ctrl K and closes it with Escape", async () => {
    renderApp("/");
    expect(screen.queryByRole("dialog", { name: "Search tasks" })).toBeNull();

    fireEvent.keyDown(window, { key: "k", ctrlKey: true });
    const panel = await screen.findByRole("dialog", { name: "Search tasks" });
    expect(await within(panel).findByText("Add inquiries")).toBeTruthy();

    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: "Search tasks" })).toBeNull();
  });
});
