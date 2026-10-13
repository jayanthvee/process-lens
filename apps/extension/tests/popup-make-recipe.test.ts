// The "Make recipe" form: the compile request it builds and what it stores.
//
// The DOM comes from popup.html itself, so a renamed or missing control fails
// here instead of in a loaded extension.
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { beforeEach, describe, expect, it } from "vitest";
import {
  createMakeRecipeForm,
  defaultCommitStepId,
  defaultRecordKeyColumn,
  makeRecipeRequest,
  recordedValues,
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
): { calls: FetchCall[]; fetchImpl: typeof fetch } {
  const calls: FetchCall[] = [];
  const fetchImpl = (async (input: RequestInfo | URL, init?: RequestInit) => {
    calls.push({ url: String(input), init });
    return { ok: outcome.ok ?? true, status: outcome.status ?? 200, json: async () => body };
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

function control<T extends HTMLElement>(id: string): T {
  const found = document.getElementById(id);
  if (!found) throw new Error(`the popup markup is missing #${id}`);
  return found as T;
}

function mount(fetchImpl: typeof fetch, storage = fakeStorage()): MakeRecipeForm {
  return createMakeRecipeForm({
    root: document,
    storage,
    fetchImpl,
    baseUrl: "http://127.0.0.1:8000",
  });
}

beforeEach(() => {
  document.body.innerHTML = POPUP_BODY;
});

describe("the make-recipe form", () => {
  it("stores the recipe and the recorded values when the hub compiles", async () => {
    const recipe = { id: "new-lead", steps: [] };
    const { fetchImpl, calls } = fakeFetch({
      recipe,
      warnings: ["step click-1 has no assert_after"],
    });
    const storage = fakeStorage();
    const form = mount(fetchImpl, storage);

    form.refresh(leadRecording());
    await form.submit();

    expect(calls).toHaveLength(1);
    expect(storage.writes).toHaveLength(1);
    const written = storage.writes[0]!;
    expect(written["processlens:executor-recipe"]).toEqual(recipe);
    expect(written["processlens:executor-row"]).toEqual({ email: "ravi@example.test" });
    expect(control("make-warnings").textContent).toContain("no assert_after");
    expect(control("make-result").textContent).toContain("ready");
  });

  it("shows the problems and stores nothing when the compiler rejects the recording", async () => {
    const problem =
      "commit step click-2 has no assert_after: a commit step must carry a post-condition";
    const { fetchImpl } = fakeFetch(
      { detail: { message: "the recording cannot be compiled", problems: [problem] } },
      { ok: false, status: 422 },
    );
    const storage = fakeStorage();
    const form = mount(fetchImpl, storage);

    form.refresh(leadRecording());
    await form.submit();

    expect(control("make-warnings").textContent).toContain(problem);
    expect(control("make-result").textContent).toContain("Could not make a recipe");
    expect(storage.writes).toHaveLength(0);
  });

  it("preselects the last recorded click as the commit step", () => {
    const { fetchImpl } = fakeFetch({});
    const form = mount(fetchImpl);

    form.refresh(leadRecording());

    const commit = control<HTMLSelectElement>("make-commit");
    expect(commit.value).toBe("click-2");
    expect(Array.from(commit.options).map((item) => item.textContent)).toEqual(["New lead", "Save"]);
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
    expect(control("make-result").textContent).toContain("No events recorded");
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
      { stepId: "fill-1", column: "contact_name", value: "Ravi", label: "Contact Name" },
      { stepId: "fill-3", column: "contact_name_2", value: "Meera", label: "Contact Name" },
    ]);
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
      { stepId: "fill-1", column: "email", value: "ravi@example.test", label: "Email" },
      { stepId: "select-1", column: "interest", value: "Data course", label: "interest" },
      { stepId: "fill-2", column: "notes", value: "Called on Monday", label: "Notes" },
      // No name anywhere: the step's own id, slugged, is the last resort.
      { stepId: "fill-3", column: "fill_3", value: "No name rung at all", label: "fill_3" },
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
    expect(control("make-result").textContent).toContain("record key");

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
    const problem = "navigate step click-2 is not in allowed_origins";
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
