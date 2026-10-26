/** The sites a task may use: what a person sees on the Apps page, and the way into each task. */

import { screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { engineWordsIn } from "../src/lib/words";
import type { AppRow } from "../src/types";
import { createFetchStub, renderApp, settle, type FetchStub } from "./helpers";

/* Every other path the Shell asks for, so a page test never hits the real API. */
const SHELL = {
  "/api/v1/needs-help": { body: [] },
};

const CHROME_CONNECTED = "Chrome is connected";
const CHROME_AWAY = "Chrome isn't connected. Click the ProcessLens icon in Chrome's toolbar to connect.";

let stub: FetchStub | null = null;

afterEach(() => {
  stub?.restore();
  stub = null;
});

function app(overrides: Partial<AppRow> = {}): AppRow {
  return {
    name: "Odoo",
    host: "localhost:8069",
    origin: "http://localhost:8069",
    tasks: [{ id: "wf-1", name: "Add inquiries" }],
    last_run_at: null,
    last_run_status: null,
    ...overrides,
  };
}

function serve(apps: AppRow[], attached = false): FetchStub {
  return createFetchStub({
    ...SHELL,
    "/api/v1/executor/session": { body: { attached } },
    "/api/v1/apps": { body: apps },
  });
}

describe("the apps page", () => {
  it("shows one card per app, with its site, its tasks and its last run", async () => {
    const twoHoursAgo = new Date(Date.now() - 2 * 60 * 60 * 1000).toISOString();
    stub = serve([
      app({ last_run_at: twoHoursAgo, last_run_status: "completed" }),
      app({
        name: "crm.example.com",
        host: "crm.example.com",
        origin: "https://crm.example.com",
        tasks: [
          { id: "wf-2", name: "Send invoices" },
          { id: "wf-3", name: "Add people" },
        ],
      }),
    ]);
    renderApp("/apps");
    await settle();

    expect(screen.getByText("Odoo")).toBeTruthy();
    expect(screen.getByText("localhost:8069")).toBeTruthy();
    expect(screen.getAllByText("Used by").length).toBe(2);
    expect(screen.getByText("Add inquiries")).toBeTruthy();
    // The newest run of any task on the site, in plain words: when, then how it went.
    expect(screen.getByText(/Last run 2 hours ago · Finished/)).toBeTruthy();
    // A site nothing has run on says so, rather than showing an empty line.
    expect(screen.getByText("Not run yet")).toBeTruthy();
    expect(screen.getByText("Send invoices")).toBeTruthy();
    expect(screen.getByText("Add people")).toBeTruthy();
  });

  it("asks a person to teach a task when no app has one yet", async () => {
    stub = serve([]);
    renderApp("/apps");
    await settle();

    expect(screen.getByText("Apps appear here once you teach a task on one.")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Teach a task" }).getAttribute("href")).toBe("/teach");
  });

  it("opens the task from its name", async () => {
    stub = serve([app({ tasks: [{ id: "wf-9", name: "Add inquiries" }] })]);
    renderApp("/apps");
    await settle();

    expect(screen.getByRole("link", { name: "Add inquiries" }).getAttribute("href")).toBe(
      "/tasks/wf-9",
    );
  });

  it("says Chrome is connected when the extension is attached", async () => {
    stub = serve([app()], true);
    renderApp("/apps");
    await settle();

    expect(screen.getByText(CHROME_CONNECTED)).toBeTruthy();
    expect(screen.queryByText(CHROME_AWAY)).toBeNull();
  });

  it("asks the person to connect the extension when it is not attached", async () => {
    stub = serve([app()], false);
    renderApp("/apps");
    await settle();

    expect(screen.getByText(CHROME_AWAY)).toBeTruthy();
    expect(screen.queryByText(CHROME_CONNECTED)).toBeNull();
  });

  it("shows only the plain words of DESIGN.md §2", async () => {
    const twoHoursAgo = new Date(Date.now() - 2 * 60 * 60 * 1000).toISOString();
    stub = serve(
      [
        app({ last_run_at: twoHoursAgo, last_run_status: "completed" }),
        app({
          name: "Twenty",
          host: "localhost:3000",
          origin: "http://localhost:3000",
          tasks: [{ id: "wf-2", name: "Add leads" }],
        }),
      ],
      true,
    );
    const view = renderApp("/apps");
    await settle();

    const text = view.container.textContent ?? "";
    expect(text).not.toBe("");
    expect(engineWordsIn(text)).toEqual([]);
  });
});

describe("what to do when Chrome is not connected (PL-106 item 8)", () => {
  it("says where to click on the Apps page", async () => {
    stub = serve([app()], false);
    renderApp("/apps");
    await settle();

    expect(
      screen.getByText("Click the ProcessLens icon in Chrome's toolbar to connect.", {
        exact: false,
      }),
    ).toBeTruthy();
  });
});
