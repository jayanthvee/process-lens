// The "Make recipe" form: the compile request it builds and what it stores.
//
// The DOM comes from popup.html itself, so a renamed or missing control fails
// here instead of in a loaded extension.
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { beforeEach, describe, expect, it } from "vitest";
import {
  compileRecording,
  createMakeRecipeForm,
  saveTask,
  commitChoices,
  defaultCommitStepId,
  defaultRecordKeyColumn,
  firstEntryEvents,
  makeRecipeRequest,
  parkFieldDefaults,
  PARK_WHEN_NOTHING_FOUND,
  recordedValues,
  recordedWarnings,
  searchDetected,
  SKIP_IF_THERE,
  type FormStorage,
  type MakeRecipeForm,
} from "../src/popup/make-recipe";
import type { RecordedEvent } from "../src/shared/recipe-types";

const POPUP_HTML = readFileSync(
  resolve(dirname(fileURLToPath(import.meta.url)), "../src/popup/popup.html"),
  "utf8",
);
const POPUP_BODY = /<body[^>]*>([\s\S]*?)<\/body>/.exec(POPUP_HTML)?.[1] ?? POPUP_HTML;

const STAMP = "2026-10-12T09:00:00.000Z";
const PAGE = "https://crm.example.test/leads";

interface FakeStorage extends FormStorage {
  writes: Record<string, unknown>[];
}

function fakeStorage(): FakeStorage {
  const writes: Record<string, unknown>[] = [];
  return {
    writes,
    get: async () => ({}),
    set: async (items) => {
      writes.push(items);
    },
  };
}

interface FetchCall {
  url: string;
  init: RequestInit | undefined;
}

function fakeFetch(
  body: unknown,
  outcome: { ok?: boolean; status?: number } = {},
  task: { body?: unknown; ok?: boolean; status?: number } = {},
): { calls: FetchCall[]; fetchImpl: typeof fetch } {
  const calls: FetchCall[] = [];
  // PL-030: the form now makes two calls -- compile, then save the task -- so the stub answers
  // each route its own way. The save defaults to a task ProcessLens accepted.
  const compile = {
    ok: outcome.ok ?? true,
    status: outcome.status ?? 200,
    body,
  };
  const save = {
    ok: task.ok ?? true,
    status: task.status ?? 200,
    body: task.body ?? { workflow_id: "wf-1" },
  };
  const fetchImpl = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    calls.push({ url, init });
    const route = url.endsWith("/api/v1/tasks") ? save : compile;
    return { ok: route.ok, status: route.status, json: async () => route.body };
  }) as unknown as typeof fetch;
  return { calls, fetchImpl };
}

function clickEvent(seq: number, id: string, name: string): RecordedEvent {
  return {
    seq,
    at: STAMP,
    kind: "click",
    url: PAGE,
    step: { id, name, action: "click", targets: [{ by: "role", role: "button", name }] },
  };
}

function fillEvent(seq: number, id: string, field: string, value: string): RecordedEvent {
  return {
    seq,
    at: STAMP,
    kind: "fill",
    url: PAGE,
    step: {
      id,
      action: "fill",
      targets: [{ by: "role", role: "textbox", name: field }],
      value: { kind: "constant", value },
    },
  };
}

/** New lead → fill → Save, the shape the recorder produces for the lead fixture. */
function leadRecording(): RecordedEvent[] {
  return [
    clickEvent(1, "click-1", "New lead"),
    fillEvent(2, "fill-1", "Email", "ravi@example.test"),
    clickEvent(3, "click-2", "Save"),
  ];
}

/** Search for the key, then create: the shape PL-017 requirement 6 records. */
function searchRecording(): RecordedEvent[] {
  return [
    fillEvent(1, "fill-1", "Email", "ravi@example.test"),
    clickEvent(2, "click-1", "Search"),
    clickEvent(3, "click-2", "New lead"),
    fillEvent(4, "fill-2", "Notes", "Called on Monday"),
    clickEvent(5, "click-3", "Save"),
  ];
}

/** No search: the key is filled without a click that runs it. */
function noSearchRecording(): RecordedEvent[] {
  return [
    clickEvent(1, "click-1", "New lead"),
    fillEvent(2, "fill-1", "Email", "ravi@example.test"),
  ];
}

/** Email (key, optional), Company (required), Notes (optional), then Save. */
function requiredRecording(): RecordedEvent[] {
  return [
    fillEvent(1, "fill-1", "Email", "ravi@example.test"),
    { ...fillEvent(2, "fill-2", "Company", "Acme"), required: true },
    fillEvent(3, "fill-3", "Notes", "hello"),
    clickEvent(4, "click-1", "Save"),
  ];
}

function control<T extends HTMLElement>(id: string): T {
  const found = document.getElementById(id);
  if (!found) throw new Error(`the popup markup is missing #${id}`);
  return found as T;
}

function mount(
  fetchImpl: typeof fetch,
  storage = fakeStorage(),
  savedUrls?: string[],
): MakeRecipeForm {
  return createMakeRecipeForm({
    root: document,
    storage,
    fetchImpl,
    baseUrl: "http://127.0.0.1:8000",
    onSaved: (taskUrl) => savedUrls?.push(taskUrl),
  });
}

beforeEach(() => {
  document.body.innerHTML = POPUP_BODY;
});

describe("the make-recipe form", () => {
  it("saves the task after compiling and hands the Open step the task's address (PL-030, PL-033)", async () => {
    const recipe = { id: "new-lead", steps: [] };
    const { fetchImpl, calls } = fakeFetch(
      { recipe, warnings: ["the check for this row was taken from what the page showed"] },
      {},
      { body: { workflow_id: "wf-abc123" } },
    );
    const storage = fakeStorage();
    const savedUrls: string[] = [];
    const form = mount(fetchImpl, storage, savedUrls);

    form.refresh(leadRecording());
    await form.submit();

    // Compile first, then save the task it produced, on the same ProcessLens address.
    expect(calls.map((call) => call.url)).toEqual([
      "http://127.0.0.1:8000/api/v1/recipes/compile",
      "http://127.0.0.1:8000/api/v1/tasks",
    ]);
    const save = calls[1]!;
    expect(save.init?.method).toBe("POST");
    expect((save.init?.headers as Record<string, string>)["content-type"]).toBe(
      "application/json",
    );
    expect(JSON.parse(String(save.init?.body))).toEqual({
      name: "Recorded on crm.example.test",
      recipe,
    });

    // The run controls still read these two session keys, exactly as before.
    expect(storage.writes).toHaveLength(1);
    expect(storage.writes[0]!["processlens:executor-recipe"]).toEqual(recipe);
    expect(storage.writes[0]!["processlens:executor-row"]).toEqual({ email: "ravi@example.test" });

    const result = control("make-result");
    expect(result.textContent).toContain("Recorded on crm.example.test is ready");
    // PL-033: no link in the popup — the Open in ProcessLens step gets the address and calls
    // chrome.tabs.create with it, because a plain `<a href>` does nothing in a popup.
    expect(result.querySelector("a")).toBeNull();
    expect(savedUrls).toEqual(["http://127.0.0.1:8000/tasks/wf-abc123"]);
    expect(control("make-warnings").textContent).toContain("taken from what the page showed");
  });

  it("says it could not save the task when ProcessLens refuses it (PL-030)", async () => {
    const { fetchImpl, calls } = fakeFetch(
      { recipe: { id: "new-lead" }, warnings: [] },
      {},
      {
        ok: false,
        status: 409,
        body: { detail: "a task named 'Add inquiries' already exists" },
      },
    );
    const storage = fakeStorage();
    const form = mount(fetchImpl, storage);

    form.refresh(leadRecording());
    await form.submit();

    expect(calls).toHaveLength(2);
    const result = control("make-result");
    expect(result.textContent).toContain("Could not save the task");
    expect(result.textContent).toContain("already exists");
    expect(result.textContent).not.toContain("is ready");
    // A refusal leaves nothing to link to.
    expect(result.querySelector("a")).toBeNull();
  });

  it("names the operator problem when the save fails for lack of one (PL-030)", async () => {
    const { fetchImpl } = fakeFetch(
      { recipe: { id: "new-lead" }, warnings: [] },
      {},
      {
        ok: false,
        status: 503,
        body: {
          detail: "PROCESSLENS_OPERATOR_ID is not set, so there is no operator id to act as",
        },
      },
    );
    const form = mount(fetchImpl);

    form.refresh(leadRecording());
    await form.submit();

    const result = control("make-result");
    expect(result.textContent).toContain("Could not save the task: ");
    expect(result.textContent).toContain("PROCESSLENS_OPERATOR_ID is not set");
  });

  it("shows the problems and stores nothing when the compiler rejects the recording", async () => {
    const problem = "the commit click has no result to check";
    const { fetchImpl } = fakeFetch(
      { detail: { message: "the recording cannot be compiled", problems: [problem] } },
      { ok: false, status: 422 },
    );
    const storage = fakeStorage();
    const form = mount(fetchImpl, storage);

    form.refresh(leadRecording());
    await form.submit();

    expect(control("make-warnings").textContent).toContain(problem);
    expect(control("make-result").textContent).toContain("Could not save this as a task");
    expect(control("make-result").textContent).not.toContain("recipe");
    expect(storage.writes).toHaveLength(0);
  });

  it("says which step the refusal is about, right under Save (PL-103)", async () => {
    const plain = 'Step 1 ("Click the form") can\'t be saved as it was recorded. Remove it with × and save again.';
    const { fetchImpl } = fakeFetch(
      {
        detail: {
          message: "the compiled recipe does not match the recipe schema",
          problems: [plain, "steps/0: {} is not valid under any of the given schemas"],
        },
      },
      { ok: false, status: 422 },
    );
    const form = mount(fetchImpl);

    form.refresh(leadRecording());
    await form.submit();

    expect(control("make-result").textContent).toContain(plain);
  });

  it("preselects the last recorded click as the commit step", () => {
    const { fetchImpl } = fakeFetch({});
    const form = mount(fetchImpl);

    form.refresh(leadRecording());

    const commit = control<HTMLSelectElement>("make-commit");
    expect(commit.value).toBe("click-2");
    // PL-109 item 6: "Nothing gets saved" is an option under Change, never the preselected one.
    expect(Array.from(commit.options).map((item) => item.textContent)).toEqual([
      "New lead",
      "Save",
      "Nothing gets saved",
    ]);
    expect(control<HTMLButtonElement>("make-go").disabled).toBe(false);
  });

  it("sends the request the compile route documents", async () => {
    const events = leadRecording();
    const { fetchImpl, calls } = fakeFetch({ recipe: { id: "new-lead" }, warnings: [] });
    const form = mount(fetchImpl);

    form.refresh(events);
    control<HTMLInputElement>("make-name").value = "New lead";
    control<HTMLSelectElement>("make-key").value = "email";
    await form.submit();

    const call = calls[0]!;
    expect(call.url.endsWith("/api/v1/recipes/compile")).toBe(true);
    expect(call.init?.method).toBe("POST");
    expect((call.init?.headers as Record<string, string>)["content-type"]).toBe("application/json");

    const body = JSON.parse(String(call.init?.body)) as Record<string, unknown>;
    expect(body["recording"]).toEqual(events);
    expect(body["name"]).toBe("New lead");
    expect(body["when_to_use"]).toBe("New lead");
    expect(body["sample_row"]).toEqual({ email: "ravi@example.test" });
    expect(body["record_key_column"]).toBe("email");
    expect(body["commit_step_ids"]).toEqual(["click-2"]);
    expect(body["allowed_origins"]).toEqual(["https://crm.example.test"]);
  });

  it("disables the button and says so when the recording is empty", () => {
    const { fetchImpl } = fakeFetch({});
    const form = mount(fetchImpl);

    form.refresh([]);

    expect(control<HTMLButtonElement>("make-go").disabled).toBe(true);
    expect(control("make-result").textContent).toContain("Nothing has been recorded yet");
  });

  it("names the status code when the error body is not one it knows", async () => {
    const { fetchImpl } = fakeFetch(
      { detail: [{ loc: ["body", "name"], msg: "field required" }] },
      { ok: false, status: 422 },
    );
    const form = mount(fetchImpl);

    form.refresh(leadRecording());
    await form.submit();

    expect(control("make-warnings").textContent).toContain("HTTP 422");
  });
});

describe("recorded values", () => {
  it("slugs a field name into a column and keeps the first occurrence of a value", () => {
    const events = [
      fillEvent(1, "fill-1", "Contact Name", "Ravi"),
      fillEvent(2, "fill-2", "Contact Name", "Ravi"),
      fillEvent(3, "fill-3", "Contact Name", "Meera"),
    ];

    expect(recordedValues(events)).toEqual([
      {
        stepId: "fill-1",
        column: "contact_name",
        value: "Ravi",
        label: "Contact Name (Ravi)",
        required: false,
      },
      {
        stepId: "fill-3",
        column: "contact_name_2",
        value: "Meera",
        label: "Contact Name (Meera)",
        required: false,
      },
    ]);
  });

  it("offers a column for each of two fields taught the same value (PL-078)", () => {
    // A timesheet's Monday and Tuesday both hold 2:00 in the demonstration. One column would bind
    // both fills to one cell, and a row whose Tuesday is 1:00 would replay Monday's hours into it.
    const events = [
      fillEvent(1, "fill-1", "19Mon", "2:00"),
      fillEvent(2, "fill-2", "20Tue", "2:00"),
    ];

    expect(recordedValues(events).map((value) => value.column)).toEqual(["f_19mon", "f_20tue"]);
  });

  it("leaves out a value the page set itself", () => {
    // PL-027: Odoo renders computed defaults (0.00 in Expected Revenue, the resolved salesperson)
    // into a form as it opens. The recorder witnesses the value and marks it; the form must not
    // offer a column a person never typed, or the sample row would carry the page's own arithmetic.
    const events: RecordedEvent[] = [
      fillEvent(1, "fill-1", "Contact Name", "Ravi"),
      { ...fillEvent(2, "fill-2", "Expected Revenue", "0.00"), page_set: true },
    ];

    expect(recordedValues(events).map((value) => value.column)).toEqual(["contact_name"]);
  });

  it("makes every column match the recipe field-name pattern", () => {
    const events = [
      fillEvent(1, "fill-1", "Add recipient", "one@example.test"),
      fillEvent(2, "fill-2", "2nd email", "two@example.test"),
      fillEvent(3, "fill-3", "Email", "three@example.test"),
      fillEvent(4, "fill-4", "Email", "four@example.test"),
    ];

    const columns = recordedValues(events).map((value) => value.column);
    expect(columns).toEqual(["add_recipient", "f_2nd_email", "email", "email_2"]);
    for (const column of columns) expect(column).toMatch(/^[a-z][a-z0-9_]{0,63}$/);
  });

  it("names a field from the label rung, the testid rung, or the step itself", () => {
    const events: RecordedEvent[] = [
      {
        seq: 1,
        at: STAMP,
        kind: "fill",
        url: PAGE,
        step: {
          id: "fill-1",
          action: "fill",
          targets: [{ by: "label", text: "Email" }],
          value: { kind: "constant", value: "ravi@example.test" },
        },
      },
      {
        seq: 2,
        at: STAMP,
        kind: "select",
        url: PAGE,
        step: {
          id: "select-1",
          action: "select",
          targets: [{ by: "testid", value: "interest" }],
          value: { kind: "constant", value: "Data course" },
        },
      },
      {
        seq: 3,
        at: STAMP,
        kind: "fill",
        url: PAGE,
        step: {
          id: "fill-2",
          name: "Notes",
          action: "fill",
          targets: [],
          value: { kind: "constant", value: "Called on Monday" },
        },
      },
      {
        seq: 4,
        at: STAMP,
        kind: "fill",
        url: PAGE,
        step: {
          id: "fill-3",
          action: "fill",
          targets: [],
          value: { kind: "constant", value: "No name rung at all" },
        },
      },
      {
        seq: 5,
        at: STAMP,
        kind: "fill",
        url: PAGE,
        step: {
          id: "fill-4",
          action: "fill",
          targets: [],
          value: { kind: "input", column: "notes" },
        },
      },
    ];

    expect(recordedValues(events)).toEqual([
      { stepId: "fill-1", column: "email", value: "ravi@example.test", label: "Email", required: false },
      { stepId: "select-1", column: "interest", value: "Data course", label: "interest", required: false },
      { stepId: "fill-2", column: "notes", value: "Called on Monday", label: "Notes", required: false },
      // No name anywhere: the step's own id, slugged, is the last resort.
      { stepId: "fill-3", column: "fill_3", value: "No name rung at all", label: "fill_3", required: false },
    ]);
  });

  it("has no commit step when nothing was clicked", () => {
    expect(defaultCommitStepId([fillEvent(1, "fill-1", "Email", "ravi@example.test")])).toBeNull();
  });

  it("sends an empty commit list when no step was chosen", () => {
    const request = makeRecipeRequest(leadRecording(), {
      name: "New lead",
      recordKeyColumn: "email",
      commitStepId: null,
    });

    expect(request.commit_step_ids).toEqual([]);
  });
});

describe("the default record key", () => {
  it("defaults to a field whose column or label mentions email", () => {
    const events = [
      fillEvent(1, "fill-1", "Interest", "Data course"),
      fillEvent(2, "fill-2", "Email", "ravi@example.test"),
    ];

    expect(defaultRecordKeyColumn(events)).toBe("email");

    const { fetchImpl } = fakeFetch({});
    const form = mount(fetchImpl);
    form.refresh(events);
    expect(control<HTMLSelectElement>("make-key").value).toBe("email");
    expect(control<HTMLButtonElement>("make-go").disabled).toBe(false);
  });

  it("falls back to a value that looks like an email when no field is named email", () => {
    const events = [
      fillEvent(1, "fill-1", "Contact", "ravi@example.test"),
      fillEvent(2, "fill-2", "Interest", "Data course"),
    ];

    expect(defaultRecordKeyColumn(events)).toBe("contact");

    const { fetchImpl } = fakeFetch({});
    const form = mount(fetchImpl);
    form.refresh(events);
    expect(control<HTMLSelectElement>("make-key").value).toBe("contact");
  });

  it("has no default key and will not submit until one is chosen", async () => {
    const events = [
      fillEvent(1, "fill-1", "Interest", "Data course"),
      fillEvent(2, "fill-2", "Notes", "Called on Monday"),
    ];
    const { fetchImpl, calls } = fakeFetch({ recipe: { id: "lead" }, warnings: [] });
    const form = mount(fetchImpl);

    form.refresh(events);

    expect(defaultRecordKeyColumn(events)).toBeNull();
    const key = control<HTMLSelectElement>("make-key");
    const go = control<HTMLButtonElement>("make-go");
    expect(key.value).toBe("");
    expect(go.disabled).toBe(true);

    await form.submit();
    expect(calls).toHaveLength(0);
    expect(control("make-result").textContent).toContain(
      "Choose which value is different for every row first.",
    );

    key.value = "interest";
    key.dispatchEvent(new Event("change"));
    expect(go.disabled).toBe(false);
  });
});

describe("allowed origins", () => {
  const OTHER_PAGE = "https://portal.example.test/review";

  /** The lead recording, with one event captured on a second origin. */
  function twoOriginRecording(): RecordedEvent[] {
    return [
      clickEvent(1, "click-1", "New lead"),
      { ...fillEvent(2, "fill-1", "Email", "ravi@example.test"), url: OTHER_PAGE },
      clickEvent(3, "click-2", "Save"),
    ];
  }

  function originBoxes(): HTMLInputElement[] {
    return Array.from(
      control("make-origins").querySelectorAll<HTMLInputElement>('input[type="checkbox"]'),
    );
  }

  it("lists the derived origins", () => {
    const { fetchImpl } = fakeFetch({});
    const form = mount(fetchImpl);

    form.refresh(twoOriginRecording());

    const boxes = originBoxes();
    expect(boxes.map((box) => box.value)).toEqual([
      "https://crm.example.test",
      "https://portal.example.test",
    ]);
    expect(boxes.every((box) => box.checked)).toBe(true);
    expect(control("make-origins").textContent).toContain("https://crm.example.test");
    expect(control("make-origins").textContent).toContain("https://portal.example.test");
  });

  it("sends the pinned list when one is unticked", async () => {
    const { fetchImpl, calls } = fakeFetch({ recipe: { id: "new-lead" }, warnings: [] });
    const form = mount(fetchImpl);

    form.refresh(twoOriginRecording());
    const boxes = originBoxes();
    boxes[1]!.checked = false;

    await form.submit();

    const body = JSON.parse(String(calls[0]!.init?.body)) as Record<string, unknown>;
    expect(body["allowed_origins"]).toEqual(["https://crm.example.test"]);
  });

  it("shows a 422 in plain words", async () => {
    const problem = "this task may not use https://elsewhere.example/";
    const { fetchImpl } = fakeFetch(
      { detail: { message: "the recording cannot be compiled", problems: [problem] } },
      { ok: false, status: 422 },
    );
    const storage = fakeStorage();
    const form = mount(fetchImpl, storage);

    form.refresh(twoOriginRecording());
    originBoxes()[1]!.checked = false;

    await form.submit();

    expect(control("make-warnings").textContent).toContain(problem);
    expect(storage.writes).toHaveLength(0);
  });
});

describe("the duplicate check (PL-017 requirement 4)", () => {
  it("pre-ticks the checkbox and sends duplicate_check true when a search is detected", async () => {
    const { fetchImpl, calls } = fakeFetch({ recipe: { id: "new-lead" }, warnings: [] });
    const form = mount(fetchImpl);

    form.refresh(searchRecording());

    const box = control<HTMLInputElement>("make-duplicate");
    expect(box.checked).toBe(true);
    expect(box.disabled).toBe(false);
    expect(control("make-duplicate-hint").textContent).toBe("");

    await form.submit();

    const body = JSON.parse(String(calls[0]!.init?.body)) as Record<string, unknown>;
    expect(body["duplicate_check"]).toBe(true);
  });

  it("sends duplicate_check false when the box is unticked", async () => {
    const { fetchImpl, calls } = fakeFetch({ recipe: { id: "new-lead" }, warnings: [] });
    const form = mount(fetchImpl);

    form.refresh(searchRecording());
    control<HTMLInputElement>("make-duplicate").checked = false;

    await form.submit();

    const body = JSON.parse(String(calls[0]!.init?.body)) as Record<string, unknown>;
    expect(body["duplicate_check"]).toBe(false);
  });

  it("disables the box, shows the hint, and sends false when no search was recorded", async () => {
    const { fetchImpl, calls } = fakeFetch({ recipe: { id: "new-lead" }, warnings: [] });
    const form = mount(fetchImpl);

    form.refresh(noSearchRecording());

    const box = control<HTMLInputElement>("make-duplicate");
    expect(box.disabled).toBe(true);
    expect(box.checked).toBe(false);
    expect(control("make-duplicate-hint").textContent).toBe(
      "I couldn't check for duplicates because you didn't search first.",
    );

    await form.submit();

    const body = JSON.parse(String(calls[0]!.init?.body)) as Record<string, unknown>;
    expect(body["duplicate_check"]).toBe(false);
  });
});

describe("the park fields (PL-017 requirement 5)", () => {
  function parkBoxes(): HTMLInputElement[] {
    return Array.from(
      control<HTMLFieldSetElement>("make-park-fields").querySelectorAll<HTMLInputElement>(
        'input[type="checkbox"]',
      ),
    );
  }

  it("pre-ticks required fields and the record key, and sends exactly those", async () => {
    const { fetchImpl, calls } = fakeFetch({ recipe: { id: "new-lead" }, warnings: [] });
    const form = mount(fetchImpl);

    form.refresh(requiredRecording());

    const byColumn = new Map(parkBoxes().map((box) => [box.dataset.column, box]));
    expect(byColumn.get("company")!.checked).toBe(true);
    expect(byColumn.get("email")!.checked).toBe(true);
    expect(byColumn.get("notes")!.checked).toBe(false);

    await form.submit();

    const body = JSON.parse(String(calls[0]!.init?.body)) as Record<string, unknown>;
    expect((body["park_fields"] as string[]).slice().sort()).toEqual(["company", "email"]);
  });

  it("sends the columns the person ticks", async () => {
    const { fetchImpl, calls } = fakeFetch({ recipe: { id: "new-lead" }, warnings: [] });
    const form = mount(fetchImpl);

    form.refresh(requiredRecording());
    // Untick the key but keep the required field; the body follows the boxes, not the defaults.
    const keys = parkBoxes().filter((box) => box.dataset.column === "email");
    for (const box of keys) box.checked = false;

    await form.submit();

    const body = JSON.parse(String(calls[0]!.init?.body)) as Record<string, unknown>;
    expect(body["park_fields"]).toEqual(["company"]);
  });

  it("re-ticks the park row when the record key changes", () => {
    const { fetchImpl } = fakeFetch({});
    const form = mount(fetchImpl);

    form.refresh(requiredRecording());
    const byColumn = () => new Map(parkBoxes().map((box) => [box.dataset.column, box]));
    expect(byColumn().get("email")!.checked).toBe(true);

    const key = control<HTMLSelectElement>("make-key");
    key.value = "notes";
    key.dispatchEvent(new Event("change"));

    expect(byColumn().get("notes")!.checked).toBe(true);
    expect(byColumn().get("email")!.checked).toBe(false);
  });
});

describe("parkFieldDefaults and searchDetected", () => {
  it("names the required columns and the record key to pre-tick", () => {
    expect(parkFieldDefaults(requiredRecording(), "email")).toEqual(["company", "email"]);
    expect(parkFieldDefaults(requiredRecording(), "")).toEqual(["company"]);
    expect(parkFieldDefaults(leadRecording(), "email")).toEqual(["email"]);
  });

  it("detects a search only when the key's value is followed by a click", () => {
    expect(searchDetected(searchRecording(), "email")).toBe(true);
    expect(searchDetected(noSearchRecording(), "email")).toBe(false);
    expect(searchDetected(searchRecording(), "")).toBe(false);
  });

  it("does not treat a fill followed by a non-search click as a search", () => {
    const wizard: RecordedEvent[] = [
      fillEvent(1, "fill-1", "Email", "ravi@example.test"),
      clickEvent(2, "click-1", "Next"),
      clickEvent(3, "click-2", "Save"),
    ];
    expect(searchDetected(wizard, "email")).toBe(false);
  });

  it("counts a search of another column, and a search run with Enter", () => {
    // PL-027, on Odoo: the workflow is keyed on the lead's name, and the destination is searched
    // by email with Enter (Odoo's search view has no button). Keying the check on the record-key
    // column left the checkbox unticked, so the compiled recipe had no duplicate branch and the
    // batch created a second copy of a lead that was already there.
    const events: RecordedEvent[] = [
      {
        ...fillEvent(1, "fill-1", "Search...", "ana@example.test"),
        step: {
          id: "fill-1",
          action: "fill",
          targets: [{ by: "role", role: "searchbox", name: "Search..." }],
          value: { kind: "constant", value: "ana@example.test" },
        },
      },
      {
        seq: 2,
        at: STAMP,
        kind: "press",
        url: PAGE,
        step: {
          id: "press-1",
          action: "press",
          targets: [{ by: "role", role: "searchbox", name: "Search..." }],
          key: "Enter",
        },
      },
      fillEvent(3, "fill-2", "Name", "Ana Silva"),
      clickEvent(4, "click-1", "Save"),
    ];

    expect(searchDetected(events, "name")).toBe(true);
  });
});

it("makeRecipeRequest carries duplicate_check and park_fields", () => {
  const request = makeRecipeRequest(requiredRecording(), {
    name: "New lead",
    recordKeyColumn: "email",
    commitStepId: "click-1",
    duplicateCheck: true,
    parkFields: ["company", "email"],
  });

  expect(request.duplicate_check).toBe(true);
  expect(request.park_fields).toEqual(["company", "email"]);

  const defaults = makeRecipeRequest(requiredRecording(), {
    name: "New lead",
    recordKeyColumn: "email",
    commitStepId: null,
  });
  expect(defaults.duplicate_check).toBe(true);
  expect(defaults.park_fields).toEqual([]);
});

describe("an accepted suggestion (PL-018)", () => {
  /** Type "ma", accept the suggestion: the prefix fill is superseded by the chip's address. */
  function suggestionRecording(): RecordedEvent[] {
    return [
      fillEvent(1, "fill-1", "To", "ma"),
      {
        ...fillEvent(2, "fill-2", "To", "ma@example.test"),
        chip: true,
        autocomplete: true,
        supersedes: "fill-1",
      },
      clickEvent(3, "click-1", "Send"),
    ];
  }

  it("offers the address, not the typed prefix, and it has no column of its own", () => {
    const values = recordedValues(suggestionRecording());

    expect(values).toHaveLength(1);
    expect(values[0]).toMatchObject({
      stepId: "fill-2",
      column: "to",
      value: "ma@example.test",
    });
    // The typed prefix is not a sample row value at all.
    expect(JSON.stringify(values)).not.toContain('"ma"');
  });

  it("defaults the record key to the address the chip carried", () => {
    const events = suggestionRecording();
    expect(defaultRecordKeyColumn(events)).toBe("to");

    const { fetchImpl } = fakeFetch({});
    const form = mount(fetchImpl);
    form.refresh(events);

    expect(control<HTMLSelectElement>("make-key").value).toBe("to");
    expect(control("make-key").textContent).toContain("To");
  });

  it("shows an unrecordable event's warning and never crashes on its empty step", () => {
    const events: RecordedEvent[] = [
      clickEvent(1, "click-2", "Send"),
      {
        seq: 2,
        at: STAMP,
        kind: "unrecordable",
        url: PAGE,
        step: null,
        reason: "chip-removal",
        warning: "a recipient was removed without a remove button; the recording cannot replay it",
      },
    ];

    // A recording of clicks alone offers no sample-row value, and the null step never crashes.
    expect(recordedValues(events)).toEqual([]);
    expect(commitChoices(events).map((choice) => choice.id)).toEqual(["click-2"]);
    expect(commitChoices(events).map((choice) => choice.label)).toEqual(["Send"]);
    expect(recordedWarnings(events)).toEqual([
      "A step the recorder could not name: a recipient was removed without a remove button; the recording cannot replay it",
    ]);

    const { fetchImpl } = fakeFetch({});
    const form = mount(fetchImpl);
    form.refresh(events);
    expect(control("make-warnings").textContent).toContain("cannot replay it");
  });
});

describe("back-to-back fills of one field (PL-037)", () => {
  it("offer only the last value, so a half-typed email never becomes a column", () => {
    const fixture = resolve(
      dirname(fileURLToPath(import.meta.url)),
      "../../../packages/compiler/tests/fixtures/odoo_quick_create_owner_pick.json",
    );
    const events = JSON.parse(readFileSync(fixture, "utf-8"));
    const values = recordedValues(events);
    expect(values.map((value) => value.value)).toEqual([
      "probe.three@example.test",
      "5550000033",
      "400",
    ]);
    expect(values.map((value) => value.column)).toEqual(["email", "phone", "expected_revenue"]);
  });
});

describe("a recording that saves twice (PL-042)", () => {
  const REPEAT_NOTE =
    "This recording saves twice. ProcessLens kept the first entry and ignored the repeat after it.";

  function loadFixture(name: string): RecordedEvent[] {
    const path = resolve(
      dirname(fileURLToPath(import.meta.url)),
      "../../../packages/compiler/tests/fixtures/",
      name,
    );
    return JSON.parse(readFileSync(path, "utf-8"));
  }

  it("offers no `_2` column: every reader sees only the first entry", () => {
    const events = loadFixture("odoo_two_saves_owner.json");

    expect(recordedValues(events).map((value) => value.column)).toEqual([
      "contact",
      "email",
      "phone",
      "expected_revenue",
    ]);
    expect(defaultRecordKeyColumn(events)).toBe("email");
    expect(parkFieldDefaults(events, "email")).toEqual(["email"]);
  });

  it("says in plain words that the repeat was ignored", () => {
    expect(recordedWarnings(loadFixture("odoo_two_saves_owner.json"))).toContain(REPEAT_NOTE);
  });

  it("sends events that end at the first Add", () => {
    const events = loadFixture("odoo_two_saves_owner.json");
    const request = makeRecipeRequest(events, {
      name: "Odoo leads",
      recordKeyColumn: "email",
      commitStepId: defaultCommitStepId(events),
    });

    expect(request.recording).toHaveLength(24);
    expect(request.recording[request.recording.length - 1]?.step?.id).toBe("click-8");
    // The commit is the save the trimmed recording ends on, never the dropped repeat's save.
    expect(request.commit_step_ids).toEqual(["click-8"]);
    expect(request.sample_row).toEqual({
      contact: "Probe Five Contact",
      email: "probe.five.example.test",
      phone: "5550000055",
      expected_revenue: "3820",
    });
  });

  it("firstEntryEvents keeps the first save click and a single save untouched", () => {
    const events = loadFixture("odoo_two_saves_owner.json");
    const entry = firstEntryEvents(events, defaultCommitStepId(events));
    expect(entry).toHaveLength(24);
    expect(entry[entry.length - 1]?.step?.id).toBe("click-8");

    const quickCreate = loadFixture("odoo_quick_create_owner_pick.json");
    expect(firstEntryEvents(quickCreate, defaultCommitStepId(quickCreate))).toBe(quickCreate);
    expect(firstEntryEvents(quickCreate, null)).toBe(quickCreate);
  });

  it("keeps the first save's confirmation when it trims the repeat (T1)", () => {
    const observation = {
      seq: 4,
      at: STAMP,
      kind: "observation",
      url: PAGE,
      observation: { text_visible: "Saved" },
    } as unknown as RecordedEvent;
    const events = [
      fillEvent(1, "fill-1", "Name", "Ana"),
      clickEvent(2, "click-2", "Save"),
      observation,
      fillEvent(5, "fill-5", "Name", "Bo"),
      clickEvent(6, "click-6", "Save"),
    ];
    const entry = firstEntryEvents(events, "click-6");
    expect(entry.map((event) => event.seq)).toEqual([1, 2, 4]);
    const request = makeRecipeRequest(events, {
      name: "Two saves",
      recordKeyColumn: "name",
      commitStepId: "click-6",
    });
    expect(request.commit_step_ids).toEqual(["click-2"]);
  });

  it("does not count a save's own submit as a second save (T1)", () => {
    const submit = (seq: number, id: string) =>
      ({ ...clickEvent(seq, id, "Save"), kind: "submit" }) as unknown as RecordedEvent;
    const observation = (seq: number) =>
      ({
        seq,
        at: STAMP,
        kind: "observation",
        url: PAGE,
        observation: { text_visible: "Saved" },
      }) as unknown as RecordedEvent;
    // One save, logged as click + submit + confirmation: nothing is trimmed.
    const once = [
      fillEvent(1, "fill-1", "Name", "Ana"),
      clickEvent(2, "click-2", "Save"),
      submit(3, "click-3"),
      observation(4),
    ];
    expect(firstEntryEvents(once, "click-3")).toEqual(once);
    expect(recordedWarnings(once)).not.toContain(REPEAT_NOTE);
    // Two such saves: the first keeps its submit and confirmation, and the submit is the commit.
    const twice = [
      ...once,
      fillEvent(5, "fill-5", "Name", "Bo"),
      clickEvent(6, "click-6", "Save"),
      submit(7, "click-7"),
    ];
    expect(firstEntryEvents(twice, "click-7").map((event) => event.seq)).toEqual([1, 2, 3, 4]);
    const request = makeRecipeRequest(twice, {
      name: "Two saves",
      recordKeyColumn: "name",
      commitStepId: "click-7",
    });
    expect(request.commit_step_ids).toEqual(["click-3"]);
  });

  it("leaves a single-save recording exactly as it is today", () => {
    const events = loadFixture("odoo_quick_create_owner_pick.json");

    expect(recordedValues(events).map((value) => value.value)).toEqual([
      "probe.three@example.test",
      "5550000033",
      "400",
    ]);
    expect(recordedValues(events).map((value) => value.column)).toEqual([
      "email",
      "phone",
      "expected_revenue",
    ]);
    expect(recordedWarnings(events)).toEqual([
      "Expected Revenue: this field changed without any typing: the page set it",
      "Contact: this field changed without any typing: the page set it",
      "Opportunity: this field changed without any typing: the page set it",
      "Email: this field changed without any typing: the page set it",
    ]);
    expect(defaultRecordKeyColumn(events)).toBe("email");
    expect(parkFieldDefaults(events, "email")).toEqual(["email"]);
    expect(recordedWarnings(events)).not.toContain(REPEAT_NOTE);
  });

  it("does not treat two Adds with different targets as a repeat", () => {
    const events: RecordedEvent[] = [
      fillEvent(1, "fill-1", "Email", "ravi@example.test"),
      clickEvent(2, "click-1", "Add"),
      fillEvent(3, "fill-2", "Notes", "second entry"),
      clickEvent(4, "click-2", "Submit"),
    ];

    expect(recordedValues(events).map((value) => value.column)).toEqual(["email", "notes"]);
    expect(recordedWarnings(events)).not.toContain(REPEAT_NOTE);
    expect(
      makeRecipeRequest(events, {
        name: "Two adds",
        recordKeyColumn: "email",
        commitStepId: "click-2",
      }).recording,
    ).toHaveLength(4);
  });

  it("shows the note where the other warnings are shown", () => {
    const { fetchImpl } = fakeFetch({});
    const form = mount(fetchImpl);

    form.refresh(loadFixture("odoo_two_saves_owner.json"));

    expect(control("make-warnings").textContent).toContain(REPEAT_NOTE);
    expect(
      Array.from(control<HTMLSelectElement>("make-key").options).map((item) => item.value),
    ).toEqual(["contact", "email", "phone", "expected_revenue"]);
  });
});

describe("the wording of the search rule (PL-077)", () => {
  /**
   * A find-then-update recording: search for the key, open the result it found, change a value,
   * save. The compiler answers with `search_kind: "update"` for this shape.
   */
  function updateRecording(): RecordedEvent[] {
    return [
      fillEvent(1, "fill-1", "Email", "ravi@example.test"),
      clickEvent(2, "click-1", "Search"),
      clickEvent(3, "click-2", "SKU-1"),
      fillEvent(4, "fill-2", "Notes", "changed"),
      clickEvent(5, "click-3", "Save"),
    ];
  }

  it("says the row is parked when the search finds nothing, after an update compiles", async () => {
    const { fetchImpl } = fakeFetch({
      recipe: { id: "price", steps: [] },
      warnings: [],
      search_kind: "update",
    });
    const form = mount(fetchImpl);

    form.refresh(updateRecording());
    await form.submit();

    const box = control<HTMLInputElement>("make-duplicate");
    const label = box.closest("label")?.querySelector(".field__label");
    expect(label?.textContent).toBe(PARK_WHEN_NOTHING_FOUND);
    // The rule is what an update needs, so it stays ticked.
    expect(box.checked).toBe(true);
  });

  it("keeps 'skip it' wording for a create", async () => {
    const { fetchImpl } = fakeFetch({
      recipe: { id: "new-lead", steps: [] },
      warnings: [],
      search_kind: "create",
    });
    const form = mount(fetchImpl);

    form.refresh(searchRecording());
    await form.submit();

    const box = control<HTMLInputElement>("make-duplicate");
    const label = box.closest("label")?.querySelector(".field__label");
    expect(label?.textContent).toBe(SKIP_IF_THERE);
    expect(box.checked).toBe(true);
  });

  it("keeps 'skip it' wording when the answer says nothing about the shape", async () => {
    const { fetchImpl } = fakeFetch({ recipe: { id: "x", steps: [] }, warnings: [] });
    const form = mount(fetchImpl);

    form.refresh(searchRecording());
    await form.submit();

    const label = control<HTMLInputElement>("make-duplicate")
      .closest("label")
      ?.querySelector(".field__label");
    expect(label?.textContent).toBe(SKIP_IF_THERE);
  });
});

describe("the team access code on compile and save (PL-086)", () => {
  it("rides on both calls when there is one, and is absent when there is none", async () => {
    const seen: Array<Record<string, string>> = [];
    const fetchImpl = (async (_url: string, init: RequestInit) => {
      seen.push(init.headers as Record<string, string>);
      return new Response(JSON.stringify({ recipe: { steps: [] }, workflow_id: "wf-1" }), {
        status: 200,
      });
    }) as unknown as typeof fetch;
    const request = { recording: [] } as unknown as Parameters<typeof compileRecording>[0];

    await compileRecording(request, { fetchImpl, baseUrl: "https://h.example", accessCode: "c" });
    await saveTask(
      { name: "t", recipe: {} } as Parameters<typeof saveTask>[0],
      { fetchImpl, baseUrl: "https://h.example", accessCode: "c" },
    );
    await compileRecording(request, { fetchImpl, baseUrl: "http://127.0.0.1:8000" });

    expect(seen[0]?.["authorization"]).toBe("Bearer c");
    expect(seen[1]?.["authorization"]).toBe("Bearer c");
    expect(seen[2]).not.toHaveProperty("authorization");
  });
});

describe("why Save as task is greyed out (PL-106 item 1)", () => {
  // The button is disabled for exactly three reasons in `syncGo`: nothing recorded, no column to
  // choose (no value was typed while teaching), or no column chosen. Each one is said in one
  // sentence right under the button, in `#make-go-why`; an enabled button says nothing there.
  const NOTHING_RECORDED = "Record some steps first.";
  const NO_VALUES = "Type at least one value while teaching, so each row has something to fill in.";
  const NO_KEY = "Pick the value that is different for every row.";

  it("sits right under the button in the popup markup", () => {
    const go = control<HTMLButtonElement>("make-go");
    const why = control<HTMLParagraphElement>("make-go-why");
    expect(go.nextElementSibling).toBe(why);
  });

  it("says to record some steps when the recording is empty", () => {
    const { fetchImpl } = fakeFetch({});
    const form = mount(fetchImpl);

    form.refresh([]);

    expect(control<HTMLButtonElement>("make-go").disabled).toBe(true);
    expect(control("make-go-why").textContent).toBe(NOTHING_RECORDED);
  });

  it("says to type a value when the recording has only clicks", () => {
    const { fetchImpl } = fakeFetch({});
    const form = mount(fetchImpl);

    form.refresh([clickEvent(1, "click-1", "New lead"), clickEvent(2, "click-2", "Save")]);

    expect(control<HTMLButtonElement>("make-go").disabled).toBe(true);
    expect(control("make-go-why").textContent).toBe(NO_VALUES);
  });

  it("says to pick the key column when none is chosen, and goes quiet once one is", () => {
    const { fetchImpl } = fakeFetch({});
    const form = mount(fetchImpl);

    form.refresh([
      fillEvent(1, "fill-1", "Interest", "Data course"),
      fillEvent(2, "fill-2", "Notes", "Called on Monday"),
    ]);

    expect(control<HTMLButtonElement>("make-go").disabled).toBe(true);
    expect(control("make-go-why").textContent).toBe(NO_KEY);

    const key = control<HTMLSelectElement>("make-key");
    key.value = "interest";
    key.dispatchEvent(new Event("change"));

    expect(control<HTMLButtonElement>("make-go").disabled).toBe(false);
    expect(control("make-go-why").textContent).toBe("");
  });

  it("says nothing when the button is ready", () => {
    const { fetchImpl } = fakeFetch({});
    const form = mount(fetchImpl);

    form.refresh(leadRecording());

    expect(control<HTMLButtonElement>("make-go").disabled).toBe(false);
    expect(control("make-go-why").textContent).toBe("");
  });
});
