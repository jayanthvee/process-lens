// PL-103: after Stop, a step that cannot be repeated says so on its own row, with the fix right
// there. On the pilot call a click on the form's background blocked the whole save with a schema
// error, and teaching started after New recorded a form that cannot be reached again.
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  EMPTY_SPACE_PROBLEM,
  MID_FORM_PROBLEM,
  renderStepList,
  stepProblems,
} from "../src/popup/flow";
import type { RecordedEvent } from "../src/shared/recipe-types";

const LIST = "https://crm.example.test/odoo/crm";
const NEW_FORM = "https://crm.example.test/odoo/crm/new";

function event(seq: number, kind: string, url: string, step: Record<string, unknown> | null): RecordedEvent {
  return { seq, kind, url, at: "2026-10-25T10:00:00Z", step } as unknown as RecordedEvent;
}

function fill(seq: number, url: string, name: string): RecordedEvent {
  return event(seq, "fill", url, {
    id: `fill-${seq}`,
    action: "fill",
    name: `Type in ${name}`,
    targets: [{ by: "role", role: "textbox", name }],
    value: { kind: "constant", value: "x" },
  });
}

function click(seq: number, url: string, role: string, name: string): RecordedEvent {
  return event(seq, "click", url, {
    id: `click-${seq}`,
    action: "click",
    name: `Click ${name}`,
    targets: [{ by: "role", role, name }],
  });
}

const PAGE_TEXT = "New lead Fill in the details of the opportunity, then save it to the pipeline. ".repeat(3);

describe("the step check", () => {
  it("passes a recording that starts on the list and presses New", () => {
    const events = [
      event(1, "navigate", LIST, null),
      click(2, LIST, "button", "New"),
      fill(3, NEW_FORM, "Opportunity"),
      click(4, NEW_FORM, "button", "Save"),
    ];

    expect(stepProblems(events).size).toBe(0);
  });

  it("flags a click on a page area as empty space, with Remove", () => {
    const events = [
      click(1, LIST, "button", "New"),
      click(2, NEW_FORM, "generic", PAGE_TEXT),
      fill(3, NEW_FORM, "Opportunity"),
    ];

    expect(stepProblems(events).get(2)).toEqual({ sentence: EMPTY_SPACE_PROBLEM, fix: "remove" });
  });

  it("flags teaching that started on a form already opened by New, with Start over", () => {
    const events = [fill(1, NEW_FORM, "Opportunity"), click(2, NEW_FORM, "button", "Save")];

    expect(stepProblems(events).get(1)).toEqual({ sentence: MID_FORM_PROBLEM, fix: "start-over" });
  });

  it("does not flag a public form that is simply the page the task starts on", () => {
    const form = "https://forms.example.test/contact";
    const events = [fill(1, form, "Email"), click(2, form, "button", "Send")];

    expect(stepProblems(events).size).toBe(0);
  });
});

describe("the step list shows the problem on its own row", () => {
  let list: HTMLUListElement;
  beforeEach(() => {
    document.body.innerHTML = "<ul id='steps'></ul>";
    list = document.getElementById("steps") as HTMLUListElement;
  });

  it("puts the sentence and its fix under the step, and the fix removes that step", () => {
    const onRemove = vi.fn();
    const events = [click(1, LIST, "button", "New"), click(2, NEW_FORM, "generic", PAGE_TEXT)];

    renderStepList(list, events, { recording: false, onRemove });

    const row = list.querySelector("[data-seq='2']") as HTMLElement;
    expect(row.textContent).toContain(EMPTY_SPACE_PROBLEM);
    (row.querySelector(".steps__fix") as HTMLButtonElement).click();
    expect(onRemove).toHaveBeenCalledWith(2);
  });

  it("offers Start over on the step where teaching began mid-form", () => {
    const onStartOver = vi.fn();
    const events = [fill(1, NEW_FORM, "Opportunity")];

    renderStepList(list, events, { recording: false, onStartOver });

    const row = list.querySelector("[data-seq='1']") as HTMLElement;
    expect(row.textContent).toContain(MID_FORM_PROBLEM);
    (row.querySelector(".steps__fix") as HTMLButtonElement).click();
    expect(onStartOver).toHaveBeenCalled();
  });

  it("says nothing while still recording", () => {
    const events = [click(1, LIST, "button", "New"), click(2, NEW_FORM, "generic", PAGE_TEXT)];

    renderStepList(list, events, { recording: true });

    expect(list.textContent).not.toContain(EMPTY_SPACE_PROBLEM);
  });
});
