/**
 * PL-109 items 1 and 4: what the Check-it step says about a field.
 *
 * The pure words are tested directly, and the page is rendered through the real TaskRun flow to
 * prove they reach the screen: the taught value beside the name, "Matched" with the first row's
 * value, a question instead of "Check this", and what "not in my file" does.
 */
import { act, fireEvent, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import {
  EMPTY_COLUMN,
  columnQuestion,
  fieldReading,
  matchedExample,
  taughtLine,
} from "../src/lib/fields";
import type { BatchCheck, BatchUpload, TaskRow } from "../src/types";
import { createFetchStub, renderApp, settle, type StubRoutes } from "./helpers";

describe("the words", () => {
  it("names a field with the value the person typed", () => {
    expect(taughtLine("Contact", "ben baller")).toBe("Contact — you typed “ben baller”");
    expect(taughtLine("Contact", null)).toBe("Contact");
  });

  it("asks about the person's data, not a confidence", () => {
    expect(columnQuestion("E-mail", "ana@x.test")).toBe("Which column has the email addresses?");
    expect(columnQuestion("Expected revenue", "$1,299.00")).toBe(
      "Which column has the expected revenue amounts?",
    );
    expect(columnQuestion("Phone", "5550000000")).toBe("Which column has the phone numbers?");
    expect(columnQuestion("Contact", "Ana Ruiz")).toBe("Which column has the contact?");
  });

  it("never says Check this: a confident match is Matched, an unsure one is a question", () => {
    expect(fieldReading("Email", "exact", "Email", "Email", "a@b.test")).toEqual({
      label: "Matched",
      tone: "ok",
    });
    const unsure = fieldReading("Contact info", "close", "Contact info", "E-mail", "a@b.test");
    expect(unsure.label).toBe("Which column has the email addresses?");
    expect(unsure.question).toBe(true);
    expect(String(unsure.label)).not.toMatch(/check this/i);
  });

  it("says what an unmapped field does", () => {
    expect(fieldReading(null, "none", null, "Phone", null)).toEqual({
      label: EMPTY_COLUMN,
      tone: "idle",
    });
    expect(EMPTY_COLUMN).toBe("Left empty for every row.");
  });

  it("shows the first row's value beside a matched column", () => {
    expect(matchedExample("Email", "riley.ivanova@example.test", 16)).toBe(
      "Email → riley.ivanova@e…",
    );
    expect(matchedExample("Email", "a@b.test")).toBe("Email → a@b.test");
  });
});

// --- the real Check-it page -------------------------------------------------------------------

const SHELL = {
  "/api/v1/needs-help": { body: [] },
  "/api/v1/executor/session": { body: { attached: true } },
};

const TASK: TaskRow = {
  workflow_id: "wf-1",
  recipe_version_id: "rv-1",
  name: "Add inquiries",
  app: "crm.example.com",
  last_run: null,
};

const UPLOAD: BatchUpload = { batch_id: "batch-1", row_count: 2, columns: ["Full Name", "Email"] };

const CHECK: BatchCheck = {
  batch_id: "batch-1",
  row_count: 2,
  columns: ["Full Name", "Email"],
  recipe_name: "Add inquiries",
  mapping: [
    {
      column: "contact",
      label: "Contact",
      chosen: "Full Name",
      confidence: "exact",
      required: true,
      alternatives: [],
      example: "ben baller",
    },
    {
      column: "email",
      label: "E-mail",
      chosen: "Email",
      confidence: "close",
      required: true,
      alternatives: [],
      example: "ana@x.test",
    },
    {
      column: "phone",
      label: "Phone",
      chosen: null,
      confidence: "none",
      required: false,
      alternatives: [],
      example: "5550000000",
    },
  ],
  rows_ok: 2,
  rows_need_a_look: [],
  first_ready: { row: 1, values: { contact: "Priya Nair", email: "riley.ivanova@example.test" } },
};

function harness(extra: StubRoutes = {}) {
  return createFetchStub({
    ...SHELL,
    "/api/v1/tasks": { body: [TASK] },
    "/api/v1/batches/batch-1/check": { body: CHECK },
    "/api/v1/batches/batch-1/mapping": { body: { batch_id: "batch-1", mapping: {} } },
    "/api/v1/batches": { body: UPLOAD },
    ...extra,
  });
}

async function openCheck(): Promise<void> {
  harness();
  const view = renderApp("/tasks/wf-1/run");
  await settle(4);
  const input = view.container.querySelector<HTMLInputElement>("input[type=file]");
  expect(input, "the page offers a file input").not.toBeNull();
  const file = new File(["Full Name,Email\nPriya Nair,p@x.test\n"], "people.csv", {
    type: "text/csv",
  });
  await act(async () => {
    fireEvent.change(input!, { target: { files: [file] } });
  });
  await settle(4);
}

describe("the Check-it page", () => {
  it("shows the taught value next to the field's name", async () => {
    await openCheck();
    expect(screen.getByText("Contact — you typed “ben baller”")).toBeTruthy();
    expect(screen.getByText("E-mail — you typed “ana@x.test”")).toBeTruthy();
  });

  it("says Matched with the first row's value, and never Check this", async () => {
    await openCheck();
    expect(screen.queryByText("Check this")).toBeNull();
    const matched = screen.getByText("Matched");
    const row = matched.closest("tr") as HTMLElement;
    expect(within(row).getByText(/Full Name → Priya Nair/)).toBeTruthy();
  });

  it("asks about an unsure match in the person's words", async () => {
    await openCheck();
    // The E-mail field's guess is only close, so its Match cell is a question.
    expect(screen.getByText("Which column has the email addresses?")).toBeTruthy();
  });

  it("says an unmapped field is left empty for every row", async () => {
    await openCheck();
    expect(screen.getByText(EMPTY_COLUMN)).toBeTruthy();
  });
});
