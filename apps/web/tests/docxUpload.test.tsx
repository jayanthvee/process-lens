/** Adding a Word document on the Spreadsheets page: the preview, the notes, and both ways on. */

import { fireEvent, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { engineWordsIn } from "../src/lib/words";
import { createFetchStub, renderApp, settle, type FetchStub } from "./helpers";

const SHELL = {
  "/api/v1/needs-help": { body: [] },
  "/api/v1/executor/session": { body: { attached: false } },
  "/api/v1/spreadsheets": { body: [] },
};

const PREVIEW = {
  columns: ["Name", "Email"],
  rows: [
    { Name: "Ana Ruiz", Email: "ana@example.test" },
    { Name: "Sam Lee", Email: "sam@example.test" },
  ],
  notes: ["Removed 1 empty row"],
  csv_token: "tok-1",
};

let stub: FetchStub | null = null;

afterEach(() => {
  stub?.restore();
  stub = null;
});

function choose(name = "contacts.docx"): void {
  const file = new File(["PK"], name, {
    type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  });
  fireEvent.change(screen.getByLabelText("Add a Word document"), {
    target: { files: [file] },
  });
}

describe("adding a Word document", () => {
  it("shows the first rows and the notes after the upload", async () => {
    stub = createFetchStub({ ...SHELL, "/api/v1/documents/to-csv": { body: PREVIEW } });
    renderApp("/spreadsheets");
    await settle();

    choose();
    await settle();

    const posted = stub.requests.find(
      (request) => request.method === "POST" && request.path === "/api/v1/documents/to-csv",
    );
    expect(posted).toBeTruthy();
    expect(screen.getByText("Ana Ruiz")).toBeTruthy();
    expect(screen.getByText("ana@example.test")).toBeTruthy();
    expect(screen.getByText("Sam Lee")).toBeTruthy();
    expect(screen.getByText("Removed 1 empty row")).toBeTruthy();
  });

  it("offers the cleaned CSV and a way on to a task", async () => {
    stub = createFetchStub({ ...SHELL, "/api/v1/documents/to-csv": { body: PREVIEW } });
    renderApp("/spreadsheets");
    await settle();

    choose();
    await settle();

    expect(screen.getByRole("link", { name: "Download CSV" }).getAttribute("href")).toBe(
      "/api/v1/documents/to-csv/tok-1.csv",
    );
    // PL-076: the way on carries the token (and the document's name), so the task list can hand
    // the cleaned rows to the task a person picks.
    expect(screen.getByRole("link", { name: "Use for a task" }).getAttribute("href")).toBe(
      "/tasks?csv=tok-1&name=contacts.docx",
    );
  });

  it("says in one sentence when the document cannot be read", async () => {
    stub = createFetchStub({
      ...SHELL,
      "/api/v1/documents/to-csv": {
        status: 422,
        body: { detail: "This document has no table or list ProcessLens can read." },
      },
    });
    renderApp("/spreadsheets");
    await settle();

    choose("notes.docx");
    await settle();

    expect(
      screen.getByText("This document has no table or list ProcessLens can read."),
    ).toBeTruthy();
    expect(screen.queryByRole("link", { name: "Download CSV" })).toBeNull();
  });

  it("shows only the plain words of DESIGN.md §2", async () => {
    stub = createFetchStub({ ...SHELL, "/api/v1/documents/to-csv": { body: PREVIEW } });
    const view = renderApp("/spreadsheets");
    await settle();

    choose();
    await settle();

    expect(engineWordsIn(view.container.textContent ?? "")).toEqual([]);
  });
});
