// The `press` action: one key, recorded as a step of its own -- and the keys that are not a press.
import { beforeEach, describe, expect, it } from "vitest";
import { attachRecorder } from "../src/content/recorder";
import { resetStepIds, type PendingEvent } from "../src/shared/events";

interface Harness {
  events: PendingEvent[];
  detach: () => void;
}

function harness(): Harness {
  const events: PendingEvent[] = [];
  const detach = attachRecorder(document, {
    send: (event) => events.push(event),
    isActive: () => true,
    url: () => "https://mail.example.test/inbox",
    now: () => "2026-10-14T00:00:00.000Z",
  });
  return { events, detach };
}

function pressOn(target: Element, key: string, init: KeyboardEventInit = {}): boolean {
  const event = new KeyboardEvent("keydown", { key, bubbles: true, cancelable: true, ...init });
  target.dispatchEvent(event);
  // The default action of a key is the page's, never the recorder's.
  return event.defaultPrevented;
}

beforeEach(() => {
  document.body.innerHTML = "";
  resetStepIds();
});

describe("the keys that are a press", () => {
  it("records Enter as a press step on the focused field", () => {
    document.body.innerHTML = "<label for='to'>To</label><input id='to'>";
    const recorded = harness();
    const field = document.getElementById("to") as HTMLInputElement;
    field.focus();

    expect(pressOn(field, "Enter")).toBe(false);

    expect(recorded.events).toHaveLength(1);
    expect(recorded.events[0]).toMatchObject({
      kind: "press",
      step: { action: "press", name: "Press Enter", key: "Enter" },
    });
    expect(recorded.events[0]!.step?.targets.length).toBeGreaterThan(0);
  });

  it("records Control+Enter and Meta+Enter as their own keys", () => {
    document.body.innerHTML = "<label for='body'>Message</label><input id='body'>";
    const recorded = harness();
    const field = document.getElementById("body") as HTMLInputElement;
    field.focus();

    pressOn(field, "Enter", { ctrlKey: true });
    pressOn(field, "Enter", { metaKey: true });

    expect(
      recorded.events.map((event) => (event.step && "key" in event.step ? event.step.key : null)),
    ).toEqual(["Control+Enter", "Meta+Enter"]);
  });

  it("records Escape, which is how a listbox is dismissed", () => {
    document.body.innerHTML = "<input id='search' aria-label='Search'>";
    const recorded = harness();
    const field = document.getElementById("search") as HTMLInputElement;
    field.focus();

    pressOn(field, "Escape");

    expect(recorded.events.map((event) => event.kind)).toEqual(["press"]);
    expect(recorded.events[0]!.step).toMatchObject({ key: "Escape" });
  });

  it("records Tab when a suggestion list is open, where it accepts the suggestion", () => {
    // PL-103: Tab from a plain field is not a step (recorder-clumsy.test.ts); with the field's
    // suggestions showing it picks one, so it is.
    document.body.innerHTML = "<input id='to' aria-label='To' role='combobox' aria-expanded='true'>";
    const recorded = harness();
    const field = document.getElementById("to") as HTMLInputElement;
    field.focus();

    pressOn(field, "Tab");

    expect(recorded.events[0]!.step).toMatchObject({ action: "press", key: "Tab" });
  });

  it("emits the typing before the press it preceded", () => {
    document.body.innerHTML = "<input id='to' aria-label='To'>";
    const recorded = harness();
    recorded.events.length = 0; // the baseline is taken by the first checkpoint below
    const field = document.getElementById("to") as HTMLInputElement;
    field.focus();
    field.value = "ma";
    field.dispatchEvent(new Event("input", { bubbles: true }));

    pressOn(field, "Enter");

    expect(recorded.events.map((event) => event.kind)).toEqual(["fill", "press"]);
  });
});

describe("the keys that are not a press", () => {
  it("records nothing for a printable character: that is typing", () => {
    document.body.innerHTML = "<input id='to' aria-label='To'>";
    const recorded = harness();
    const field = document.getElementById("to") as HTMLInputElement;
    field.focus();

    pressOn(field, "a");
    pressOn(field, "@");

    expect(recorded.events).toHaveLength(0);
  });

  it("records nothing for Backspace inside a text field, but records it elsewhere", () => {
    document.body.innerHTML = "<input id='to' aria-label='To'><div id='token' tabindex='0'>ada@example.test</div>";
    const recorded = harness();
    const field = document.getElementById("to") as HTMLInputElement;
    field.focus();

    pressOn(field, "Backspace");
    expect(recorded.events).toHaveLength(0);

    const token = document.getElementById("token")!;
    token.focus();
    pressOn(token, "Delete");

    expect(recorded.events).toHaveLength(1);
    expect(recorded.events[0]!.step).toMatchObject({ action: "press", key: "Delete" });
  });

  it("never records a key sent to a password field", () => {
    document.body.innerHTML = "<input id='pw' type='password' aria-label='Password'>";
    const recorded = harness();
    const field = document.getElementById("pw") as HTMLInputElement;
    field.focus();

    pressOn(field, "Enter");
    pressOn(field, "Tab");

    expect(recorded.events).toHaveLength(0);
  });

  it("ignores keys with Alt held, and keys that are not presses at all", () => {
    document.body.innerHTML = "<input id='to' aria-label='To'>";
    const recorded = harness();
    const field = document.getElementById("to") as HTMLInputElement;
    field.focus();

    pressOn(field, "Enter", { altKey: true });
    pressOn(field, "ArrowDown");
    pressOn(field, "Shift");
    pressOn(field, "F5");

    expect(recorded.events).toHaveLength(0);
  });
});
