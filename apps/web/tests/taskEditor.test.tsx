/**
 * PL-062: the workflow editor on the task page.
 *
 * A person opens a task, presses Edit steps, renames a step, changes where a value comes from and
 * how the save is checked, and saves. The page posts the edited document as the task's next version
 * and says so; a refusal leaves the steps exactly where they were.
 */

import { fireEvent, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { createFetchStub, renderApp, settle, type FetchStub } from "./helpers";

const SHELL = {
  "/api/v1/needs-help": { body: [] },
  "/api/v1/executor/session": { body: { attached: false } },
  "/api/v1/workspace/state": {
    body: {
      tenant_id: "tenant-1",
      runs_frozen: false,
      min_extension_version: null,
      extension_version: null,
      extension_out_of_date: false,
    },
  },
  "/api/v1/tasks": {
    body: [
      {
        workflow_id: "wf-1",
        recipe_version_id: "rv-2",
        name: "Add inquiries",
        app: "crm.example.com",
        last_run: null,
      },
    ],
  },
  "/api/v1/runs": { body: [] },
};

const RECIPE = {
  id: "rv-2",
  workflow_id: "wf-1",
  workflow_name: "Add inquiries",
  version: 2,
  name: "Add inquiries",
  recipe: {
    format_version: 1,
    meta: {
      name: "Add inquiries",
      when_to_use: "A spreadsheet of inquiries.",
      input_schema: {
        fields: {
          email: { type: "string", required: true },
          full_name: { type: "string" },
        },
      },
      record_key: "{{row.email | lower}}",
      idempotency_key: "{{row.email | lower}}",
      approval_policy: { commit: "first_record" },
      allowed_origins: ["https://crm.example.com"],
    },
    steps: [
      { id: "open", action: "navigate", url: "https://crm.example.com/leads/new" },
      {
        id: "type-email",
        action: "fill",
        name: "Email",
        targets: [{ by: "label", text: "Email" }],
        value: { kind: "input", column: "email" },
      },
      {
        id: "save-lead",
        action: "click",
        name: "Save",
        commit: true,
        targets: [{ by: "role", role: "button", name: "Save" }],
        assert_after: { text_visible: "Lead created", timeout_ms: 8000 },
      },
      { id: "done", action: "end_record", outcome: "verified", reason: "Added." },
    ],
  },
};

let stub: FetchStub | null = null;

afterEach(() => {
  stub?.restore();
  stub = null;
});

function routes(extra: Record<string, unknown> = {}) {
  return { ...SHELL, "/api/v1/recipes/rv-2": { body: RECIPE }, ...extra };
}

async function openEditor(extra: Record<string, unknown> = {}) {
  stub = createFetchStub(routes(extra));
  const view = renderApp("/tasks/wf-1");
  await settle();
  fireEvent.click(screen.getByRole("button", { name: "Edit steps" }));
  await settle();
  return view;
}

function saved() {
  // The API answers with the document it stored, as `POST /api/v1/recipes` does.
  return (call: { body: unknown }) => ({
    status: 201,
    body: { ...RECIPE, id: "rv-3", version: 3, recipe: call.body },
  });
}

describe("the workflow editor", () => {
  it("offers Edit steps beside the steps, and leaves Run as the primary action", async () => {
    stub = createFetchStub(routes());
    renderApp("/tasks/wf-1");
    await settle();

    expect(screen.getByRole("button", { name: "Edit steps" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "Run this task" })).toBeTruthy();
    // The note says what the editor will not do.
    fireEvent.click(screen.getByRole("button", { name: "Edit steps" }));
    await settle();
    expect(
      screen.getByText("To change what a step clicks, teach that part again."),
    ).toBeTruthy();
  });

  it("renames, changes where a value comes from, changes the save check, and saves", async () => {
    await openEditor({ "/api/v1/recipes": saved() });

    const name = screen.getByLabelText("Name for step 2");
    fireEvent.change(name, { target: { value: "Work email" } });
    fireEvent.blur(name);
    await settle();

    fireEvent.change(screen.getByLabelText("Comes from for step 2"), {
      target: { value: "column:full_name" },
    });
    await settle();

    fireEvent.change(screen.getByLabelText("How we know it saved for step 3"), {
      target: { value: "url_matches" },
    });
    const address = await screen.findByLabelText("Address for step 3");
    fireEvent.change(address, { target: { value: "/leads/9" } });
    fireEvent.blur(address);
    await settle();

    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await settle();

    const post = stub?.requests.find((request) => request.method === "POST");
    expect(post, "the page posted the edited steps").toBeTruthy();
    expect(post?.path).toBe("/api/v1/recipes");
    expect(post?.search).toContain("workflow_id=wf-1");
    expect(post?.search).toContain(
      `change_reason=${encodeURIComponent(
        'Renamed step 2; step 2 now comes from the "full_name" column; the save is now checked by the address.',
      )}`,
    );

    const steps = (post?.body as { steps: Record<string, unknown>[] }).steps;
    expect(steps[1]).toMatchObject({ name: "Work email", value: { kind: "input", column: "full_name" } });
    expect(steps[2]?.assert_after).toEqual({ url_matches: "/leads/9$", timeout_ms: 8000 });
    // Nothing else moved.
    expect(steps[0]).toEqual(RECIPE.recipe.steps[0]);
    expect(steps[3]).toEqual(RECIPE.recipe.steps[3]);

    expect(screen.getByRole("status").textContent).toContain("Saved as version 3");
    expect(screen.getByRole("status").textContent).toContain(
      "Runs already started keep the old steps.",
    );
    expect(screen.getByText("Type the work email")).toBeTruthy();
  });

  it("shows the API's problems in plain words on a refusal, and changes nothing", async () => {
    await openEditor({
      "/api/v1/recipes": {
        status: 422,
        body: {
          detail: {
            message: "the recipe does not match the recipe schema",
            problems: [
              "steps[2] (save-lead): a commit step must carry an assert_after",
              "steps[1]: value is required",
            ],
          },
        },
      },
    });

    const name = screen.getByLabelText("Name for step 2");
    fireEvent.change(name, { target: { value: "Work email" } });
    fireEvent.blur(name);
    await settle();

    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await settle();

    expect(
      screen.getByText("The step that saves has to say how we know it saved."),
    ).toBeTruthy();
    expect(screen.getByText("Something the steps need is missing.")).toBeTruthy();
    // Still editing, and no saved note.
    expect(screen.getByRole("button", { name: "Save" })).toBeTruthy();
    expect(screen.queryByRole("status")).toBeNull();
    expect(screen.queryByText(/Saved as version/)).toBeNull();
  });

  it("cancels back to the steps as they were", async () => {
    await openEditor();

    const name = screen.getByLabelText("Name for step 2");
    fireEvent.change(name, { target: { value: "Work email" } });
    fireEvent.blur(name);
    await settle();
    expect(screen.getByText("Type the work email")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await settle();

    expect(screen.getByText("Type the email")).toBeTruthy();
    expect(screen.queryByText("Type the work email")).toBeNull();
    expect(screen.queryByRole("button", { name: "Save" })).toBeNull();
    expect(stub?.requests.some((request) => request.method === "POST")).toBe(false);
  });

  it("says on the row why the step that saves cannot be removed", async () => {
    await openEditor();

    fireEvent.click(screen.getByRole("button", { name: "Remove step 3" }));
    await settle();

    expect(
      screen.getByText("This is the step that saves; it can't be removed."),
    ).toBeTruthy();
    // The step is still there.
    expect(screen.getByText("Click Save")).toBeTruthy();
  });

  it("shows the column a fill currently comes from, and offers fixed text", async () => {
    await openEditor();

    const comesFrom = screen.getByLabelText("Comes from for step 2") as HTMLSelectElement;
    expect(comesFrom.value).toBe("column:email");

    fireEvent.change(comesFrom, { target: { value: "fixed" } });
    const fixed = await screen.findByLabelText("Fixed text for step 2");
    fireEvent.change(fixed, { target: { value: "Acme" } });
    fireEvent.blur(fixed);
    await settle();

    expect(screen.getByText('Always “Acme”')).toBeTruthy();
  });
});
