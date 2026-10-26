// PL-103: the recorder forgives what a normal person does while teaching. Each case here happened on
// the pilot call: a click on the empty form background, Tab between fields, starting on a tab that
// was open before ProcessLens was installed.
import { beforeEach, describe, expect, it } from "vitest";

import { attachRecorder } from "../src/content/recorder";
import { resetStepIds, type PendingEvent } from "../src/shared/events";

function harness(): { events: PendingEvent[]; detach: () => void } {
  const events: PendingEvent[] = [];
  const detach = attachRecorder(document, {
    send: (event) => events.push(event),
    isActive: () => true,
    url: () => "https://crm.example.test/odoo/crm/new",
    now: () => "2026-10-25T10:00:00.000Z",
  });
  return { events, detach };
}

function typeInto(field: HTMLInputElement, value: string): void {
  field.focus();
  field.value = value;
  field.dispatchEvent(new Event("input", { bubbles: true }));
}

function press(target: Element, key: string): void {
  target.dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true, cancelable: true }));
}

const FORM =
  "<div class='o_form_sheet_bg' id='bg'><div class='o_form_sheet'>" +
  "<h1>New lead</h1><p>Fill in the details of the opportunity, then save it to the pipeline. " +
  "Every field here can be changed later from the lead itself.</p>" +
  "<label for='name'>Opportunity</label><input id='name'>" +
  "<label for='email'>Email</label><input id='email'>" +
  "</div></div>";

beforeEach(() => {
  document.body.innerHTML = "";
  resetStepIds();
});

describe("a click on empty space", () => {
  it("is not a step when it lands on the form's background", () => {
    document.body.innerHTML = FORM;
    const recorded = harness();

    (document.getElementById("bg") as HTMLElement).click();

    expect(recorded.events.filter((event) => event.kind === "click")).toEqual([]);
  });

  it("is not a step when it lands on a plain container that holds fields", () => {
    document.body.innerHTML = FORM;
    const recorded = harness();

    (document.querySelector(".o_form_sheet") as HTMLElement).click();

    expect(recorded.events.filter((event) => event.kind === "click")).toEqual([]);
  });

  it("is still a step on a real button", () => {
    document.body.innerHTML = FORM + "<button id='save'>Save</button>";
    const recorded = harness();

    (document.getElementById("save") as HTMLElement).click();

    expect(recorded.events.filter((event) => event.kind === "click")).toHaveLength(1);
  });
});

describe("Tab between fields", () => {
  it("is not a step from a plain text field: the next field is filled by its own step", () => {
    document.body.innerHTML = FORM;
    const recorded = harness();
    const name = document.getElementById("name") as HTMLInputElement;

    typeInto(name, "Demo lead");
    press(name, "Tab");
    typeInto(document.getElementById("email") as HTMLInputElement, "demo@example.test");
    (document.getElementById("email") as HTMLInputElement).dispatchEvent(
      new Event("focusout", { bubbles: true }),
    );

    const kinds = recorded.events.map((event) => event.kind);
    expect(kinds).not.toContain("press");
    expect(kinds.filter((kind) => kind === "fill")).toHaveLength(2);
  });

  it("is still a step from a field whose suggestion list is open: Tab picks the suggestion", () => {
    document.body.innerHTML =
      "<label for='sp'>Salesperson</label>" +
      "<input id='sp' role='combobox' aria-expanded='true' aria-controls='list'>" +
      "<ul id='list' role='listbox'><li role='option'>Priya Raman</li></ul>";
    const recorded = harness();
    const field = document.getElementById("sp") as HTMLInputElement;

    typeInto(field, "Priya");
    press(field, "Tab");

    expect(recorded.events.map((event) => event.kind)).toContain("press");
  });
});

describe("names", () => {
  it("are never longer than 120 characters, whatever the element's text", () => {
    document.body.innerHTML =
      "<div role='button' tabindex='0' id='card'>" + "Lead details and history. ".repeat(40) + "</div>";
    const recorded = harness();

    (document.getElementById("card") as HTMLElement).click();

    for (const event of recorded.events) {
      const step = event.step as { name?: string; targets?: { name?: string; value?: string }[] };
      expect((step.name ?? "").length).toBeLessThanOrEqual(120);
      for (const target of step.targets ?? []) {
        expect((target.name ?? target.value ?? "").length).toBeLessThanOrEqual(120);
      }
    }
  });
});
