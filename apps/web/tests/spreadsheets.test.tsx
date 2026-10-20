/** The uploaded spreadsheets: what a person sees on the Spreadsheets page, and how a run used them. */

import { fireEvent, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { engineWordsIn } from "../src/lib/words";
import type { SpreadsheetRow } from "../src/types";
import { createFetchStub, renderApp, settle, type FetchStub } from "./helpers";

/* Every other path the Shell asks for, so a page test never hits the real API. */
const SHELL = {
  "/api/v1/needs-help": { body: [] },
  "/api/v1/executor/session": { body: { attached: false } },
};

let stub: FetchStub | null = null;

afterEach(() => {
  stub?.restore();
  stub = null;
});

function sheet(overrides: Partial<SpreadsheetRow> = {}): SpreadsheetRow {
  return {
    id: "batch-1",
    file_name: "spring leads.csv",
    task_id: "wf-1",
    task_name: "Add inquiries",
    row_count: 20,
    uploaded_at: new Date(Date.now() - 2 * 60 * 60 * 1000).toISOString(),
    uploaded_by: "Jay",
    runs: 0,
    last_run_id: null,
    ...overrides,
  };
}

function serve(sheets: SpreadsheetRow[]): FetchStub {
  return createFetchStub({
    ...SHELL,
    "/api/v1/spreadsheets": { body: sheets },
  });
}

describe("the spreadsheets page", () => {
  it("shows one row per spreadsheet, with its file, task, rows and upload", async () => {
    stub = serve([
      sheet({ runs: 3, last_run_id: "run-9" }),
      sheet({
        id: "batch-2",
        file_name: "Pasted rows",
        task_id: "wf-2",
        task_name: "Add people",
        row_count: 5,
        uploaded_at: new Date(Date.now() - 3 * 24 * 60 * 60 * 1000).toISOString(),
        uploaded_by: null,
      }),
    ]);
    renderApp("/spreadsheets");
    await settle();

    expect(screen.getByText("spring leads.csv")).toBeTruthy();
    expect(screen.getByText("Pasted rows")).toBeTruthy();
    expect(screen.getByText("Add inquiries")).toBeTruthy();
    expect(screen.getByText("Add people")).toBeTruthy();
    expect(screen.getByText("20 rows")).toBeTruthy();
    expect(screen.getByText("5 rows")).toBeTruthy();
    expect(screen.getByText("Uploaded 2 hours ago by Jay")).toBeTruthy();
    // A batch with nobody recorded says when, without inventing a name.
    expect(screen.getByText("Uploaded 3 days ago")).toBeTruthy();
    expect(screen.getByText("Used in 3 runs")).toBeTruthy();
    // A spreadsheet nothing has run on says so, rather than showing an empty line.
    expect(screen.getByText("Not used yet")).toBeTruthy();
  });

  it("asks a person to run a task when no spreadsheet has been uploaded", async () => {
    stub = serve([]);
    renderApp("/spreadsheets");
    await settle();

    expect(
      screen.getByText("Spreadsheets appear here once you upload one to run a task."),
    ).toBeTruthy();
    expect(screen.getByRole("link", { name: "Run a task" }).getAttribute("href")).toBe("/tasks");
  });

  it("opens the task and the newest run from their links", async () => {
    stub = serve([sheet({ task_id: "wf-7", runs: 1, last_run_id: "run-4" })]);
    renderApp("/spreadsheets");
    await settle();

    expect(screen.getByRole("link", { name: "Add inquiries" }).getAttribute("href")).toBe(
      "/tasks/wf-7",
    );
    // One run is said in the singular, and links to the run that used the spreadsheet.
    expect(screen.getByRole("link", { name: "Used in 1 run" }).getAttribute("href")).toBe(
      "/runs/run-4",
    );
  });

  it("offers Run it on a spreadsheet nothing has used, and Run again on one that has", async () => {
    stub = serve([
      sheet({ id: "batch-1", task_id: "wf-1" }),
      sheet({ id: "batch-2", task_id: "wf-2", runs: 2, last_run_id: "run-5" }),
    ]);
    renderApp("/spreadsheets");
    await settle();

    expect(screen.getByRole("link", { name: "Run it" }).getAttribute("href")).toBe(
      "/tasks/wf-1/run",
    );
    expect(screen.getByRole("link", { name: "Run again" }).getAttribute("href")).toBe(
      "/tasks/wf-2/run",
    );
  });

  it("shows only the plain words of DESIGN.md Â§2", async () => {
    stub = serve([sheet({ runs: 2, last_run_id: "run-1" })]);
    const view = renderApp("/spreadsheets");
    await settle();

    const text = view.container.textContent ?? "";
    expect(text).not.toBe("");
    expect(engineWordsIn(text)).toEqual([]);
  });
});

describe("carrying a cleaned document to a task (PL-076)", () => {
  it("'Use for a task' carries the csv token and the document's name", async () => {
    stub = createFetchStub({
      ...SHELL,
      "/api/v1/spreadsheets": { body: [] },
      "/api/v1/documents/to-csv": {
        body: {
          columns: ["Name", "Email"],
          rows: [{ Name: "Priya", Email: "priya@example.com" }],
          notes: [],
          csv_token: "tok-123",
        },
      },
    });
    renderApp("/spreadsheets");
    await settle();
    const input = screen.getByLabelText("Add a Word document") as HTMLInputElement;
    const file = new File(["docx-bytes"], "spring leads.docx", {
      type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    });
    fireEvent.change(input, { target: { files: [file] } });
    await settle();

    const link = screen.getByRole("link", { name: "Use for a task" }) as HTMLAnchorElement;
    expect(link.getAttribute("href")).toContain("csv=tok-123");
    expect(decodeURIComponent(link.getAttribute("href") ?? "")).toContain("spring leads.docx");
  });
});
