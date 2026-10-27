/**
 * PL-105: the side panel shows an employee three places and a connection line, nothing else; the
 * website's sidebar drops the four places that were only ever "Coming soon".
 */

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { MemoryRouter } from "react-router-dom";

import { PanelApp } from "../src/panel/PanelApp";
import { createFetchStub, renderApp, settle, type FetchStub } from "./helpers";

const NO_INPUTS = {
  workflow_id: "w-once",
  recipe_version_id: "r-once",
  name: "Close the day",
  app: "erp.example.com",
  last_run: null,
  inputs: [],
};
const WITH_INPUTS = {
  workflow_id: "w-sheet",
  recipe_version_id: "r-sheet",
  name: "Add inquiries",
  app: "crm.example.com",
  last_run: null,
  inputs: ["email", "company"],
};

const HIDDEN = ["Saved answers", "Documents", "Apps", "Spreadsheets", "Settings", "Help"];

let stub: FetchStub;

function stubWith(attached: boolean): FetchStub {
  return createFetchStub({
    "/api/v1/executor/session": { body: { attached } },
    "/api/v1/needs-help": { body: [] },
    "/api/v1/tasks": { body: [NO_INPUTS, WITH_INPUTS] },
    "/api/v1/runs": { body: [] },
    "/api/v1/tasks/w-once/run-once": { status: 201, body: { run_id: "run-9", status: "queued" } },
  });
}

function renderPanel(path = "/tasks"): void {
  render(
    <MemoryRouter initialEntries={[path]}>
      <PanelApp />
    </MemoryRouter>,
  );
}

afterEach(() => {
  stub.restore();
  cleanup();
});

describe("the side panel", () => {
  it("offers exactly Teach, Tasks and Runs", () => {
    stub = stubWith(true);
    renderPanel();

    const nav = screen.getByRole("navigation");
    const labels = Array.from(nav.querySelectorAll("a")).map((link) => link.textContent?.trim());
    expect(labels).toEqual(["Teach", "Tasks", "Runs"]);
    for (const label of [...HIDDEN, "Home", "Needs help"]) {
      expect(screen.queryByRole("link", { name: label }), label).toBeNull();
    }
  });

  it("says not connected, with Connect right there", async () => {
    stub = stubWith(false);
    renderPanel();

    expect(await screen.findByText(/Not connected/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Connect" })).toBeTruthy();
  });

  it("says connected when the extension's session is attached", async () => {
    stub = stubWith(true);
    renderPanel();

    expect(await screen.findByText("Connected")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Connect" })).toBeNull();
  });

  it("asks for no access code", async () => {
    stub = stubWith(true);
    renderPanel();
    await settle();

    expect(screen.queryByText(/access code/i)).toBeNull();
    expect(stub.calls).not.toContain("/api/v1/access");
  });
});

describe("Run", () => {
  it("is offered only for a task with no spreadsheet columns", async () => {
    stub = stubWith(true);
    renderPanel();

    const once = await screen.findByRole("button", { name: "Run" });
    expect(once.closest("li, tr, article")?.textContent).toContain("Close the day");
    expect(screen.getAllByRole("button", { name: "Run" })).toHaveLength(1);
    expect(screen.getAllByRole("link", { name: "Run with a spreadsheet" })).toHaveLength(1);
  });

  it("starts the run with no upload", async () => {
    stub = stubWith(true);
    renderPanel();

    fireEvent.click(await screen.findByRole("button", { name: "Run" }));
    await settle();

    const started = stub.requests.find((call) => call.path === "/api/v1/tasks/w-once/run-once");
    expect(started?.method).toBe("POST");
    expect(stub.calls).not.toContain("/api/v1/batches");
  });
});

describe("the website's sidebar", () => {
  it("no longer lists the Coming soon places", () => {
    stub = stubWith(true);
    renderApp("/");

    for (const label of ["Saved answers", "Documents", "Settings", "Help"]) {
      expect(screen.queryByRole("link", { name: label }), label).toBeNull();
    }
    expect(screen.getByRole("link", { name: "Tasks" })).toBeTruthy();
  });
});
