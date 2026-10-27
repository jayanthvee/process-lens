// PL-109 items 5 and 6 in the popup: the same decide-and-show lines the panel has.
//
// The key and the save click are decided when the recording can decide them, and the question is
// only shown under Change (or when nothing can be decided). "Nothing gets saved" stays a choice.
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { beforeEach, describe, expect, it } from "vitest";

import { createMakeRecipeForm, type FormStorage, type MakeRecipeForm } from "../src/popup/make-recipe";
import type { RecordedEvent } from "../src/shared/recipe-types";

const POPUP_HTML = readFileSync(
  resolve(dirname(fileURLToPath(import.meta.url)), "../src/popup/popup.html"),
  "utf8",
);
const POPUP_BODY = /<body[^>]*>([\s\S]*?)<\/body>/.exec(POPUP_HTML)?.[1] ?? POPUP_HTML;

const STAMP = "2026-10-12T09:00:00.000Z";
const PAGE = "https://crm.example.test/leads";

const storage: FormStorage = {
  get: async () => ({}),
  set: async () => undefined,
};

const fetchImpl = (async () => ({
  ok: true,
  status: 200,
  json: async () => ({ recipe: { id: "t" }, warnings: [] }),
})) as unknown as typeof fetch;

function control<T extends HTMLElement>(id: string): T {
  const found = document.getElementById(id);
  if (!found) throw new Error(`the popup markup is missing #${id}`);
  return found as T;
}

function click(seq: number, id: string, name: string): RecordedEvent {
  return {
    seq,
    at: STAMP,
    kind: "click",
    url: PAGE,
    step: { id, name, action: "click", targets: [{ by: "role", role: "button", name }] },
  };
}

function fill(seq: number, id: string, field: string, value: string): RecordedEvent {
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

/** Email can identify a row, and Save is the last click. */
function leadRecording(): RecordedEvent[] {
  return [
    click(1, "click-1", "New lead"),
    fill(2, "fill-1", "Email", "ravi@example.test"),
    click(3, "click-2", "Save"),
  ];
}

/** A note is not something that can identify a row, so the key must be asked. */
function notesOnly(): RecordedEvent[] {
  return [fill(1, "fill-1", "Notes", "Called on Monday"), click(2, "click-1", "Save")];
}

function mount(): MakeRecipeForm {
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

describe("the popup decides the key", () => {
  it("shows the decision instead of the question, and Change opens the question", () => {
    mount().refresh(leadRecording());

    expect(control("make-key-decided").hidden).toBe(false);
    expect(control("make-key-decided-note").textContent).toBe(
      "We'll use Email so nothing gets added twice.",
    );
    // The question is not on show while the decision stands.
    expect(control("make-key-ask").hidden).toBe(true);

    document.getElementById("make-key-change")?.click();

    expect(control("make-key-decided").hidden).toBe(true);
    expect(control("make-key-ask").hidden).toBe(false);
    expect(control<HTMLSelectElement>("make-key").value).toBe("email");
  });

  it("asks only when no value can identify a row", () => {
    mount().refresh(notesOnly());

    expect(control("make-key-decided").hidden).toBe(true);
    expect(control("make-key-ask").hidden).toBe(false);
    expect(
      control("make-key-ask").querySelector(".field__label")?.textContent,
    ).toBe("Which value is different for every row?");
  });
});

describe("the popup decides the click that saves", () => {
  it("shows the decision instead of the question, and Change opens the question", () => {
    mount().refresh(leadRecording());

    expect(control("make-commit-decided").hidden).toBe(false);
    expect(control("make-commit-decided-note").textContent).toBe(
      "The Save click saves your work.",
    );
    expect(control("make-commit-ask").hidden).toBe(true);

    document.getElementById("make-commit-change")?.click();

    expect(control("make-commit-decided").hidden).toBe(true);
    expect(control("make-commit-ask").hidden).toBe(false);
    expect(
      control("make-commit-ask").querySelector(".field__label")?.textContent,
    ).toBe("Which click saves your work?");
    expect(control<HTMLSelectElement>("make-commit").value).toBe("click-2");
  });

  it("keeps Nothing gets saved as a choice under Change", () => {
    mount().refresh(leadRecording());
    document.getElementById("make-commit-change")?.click();

    const options = Array.from(control<HTMLSelectElement>("make-commit").options);
    expect(options.map((option) => option.value)).toContain("");
    expect(options.find((option) => option.value === "")?.textContent).toBe("Nothing gets saved");
  });

  it("says nothing gets saved when the recording has no click to save with", () => {
    mount().refresh([fill(1, "fill-1", "Email", "ravi@example.test")]);

    expect(control("make-commit-decided").hidden).toBe(false);
    expect(control("make-commit-decided-note").textContent).toBe("Nothing gets saved.");
    expect(control("make-commit-ask").hidden).toBe(true);
  });
});
