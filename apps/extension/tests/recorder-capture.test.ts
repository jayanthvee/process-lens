// What counts as one action: a dropdown choice is one event, a rich-text body is one fill, and
// a credential context is never captured at all (PL-016 requirements 4 and 10).
import { beforeEach, describe, expect, it } from "vitest";
import { attachRecorder } from "../src/content/recorder";
import type { PendingEvent } from "../src/shared/events";
import { resetStepIds } from "../src/shared/events";

function harness(): { events: PendingEvent[]; detach: () => void } {
  const events: PendingEvent[] = [];
  const detach = attachRecorder(document, {
    send: (event) => events.push(event),
    isActive: () => true,
    url: () => "https://mail.example.test/compose",
    now: () => "2026-10-12T00:00:00.000Z",
  });
  return { events, detach };
}

beforeEach(() => {
  document.body.innerHTML = "";
  resetStepIds();
});

describe("a <select> change (requirement 4)", () => {
  it("emits exactly one select event for one change", () => {
    document.body.innerHTML =
      "<label for='interest'>Interest</label>" +
      "<select id='interest'><option>Interest</option><option>Data course</option></select>";
    const recorded = harness();
    const select = document.getElementById("interest") as HTMLSelectElement;

    select.value = "Data course";
    // A real browser fires input, then change, then focusout when the field loses focus. The
    // old recorder buffered the input and flushed it on focusout, inventing a second event.
    select.dispatchEvent(new Event("input", { bubbles: true }));
    select.dispatchEvent(new Event("change", { bubbles: true }));
    select.dispatchEvent(new Event("focusout", { bubbles: true }));

    expect(recorded.events.map((event) => event.kind)).toEqual(["select"]);
    expect(recorded.events[0]!.step).toMatchObject({
      action: "select",
      value: { kind: "constant", value: "Data course" },
    });
  });

  it("emits one select event when the change is followed by more typing elsewhere", () => {
    document.body.innerHTML =
      "<select id='interest'><option>Interest</option><option>Data course</option></select>" +
      "<label for='note'>Note</label><input id='note'>";
    const recorded = harness();
    const select = document.getElementById("interest") as HTMLSelectElement;
    const note = document.getElementById("note") as HTMLInputElement;

    select.value = "Data course";
    select.dispatchEvent(new Event("input", { bubbles: true }));
    select.dispatchEvent(new Event("change", { bubbles: true }));
    note.value = "Called Ana";
    note.dispatchEvent(new Event("input", { bubbles: true }));
    note.dispatchEvent(new Event("focusout", { bubbles: true }));

    expect(recorded.events.map((event) => event.kind)).toEqual(["select", "fill"]);
  });
});

describe("a role=combobox field (requirement 4)", () => {
  const COMBOBOX =
    "<label for='to'>Add recipient</label>" +
    "<input id='to' type='text' role='combobox' aria-expanded='false' autocomplete='off'>";

  it("records one fill when the person types and the change follows the input", () => {
    document.body.innerHTML = COMBOBOX;
    const recorded = harness();
    const field = document.getElementById("to") as HTMLInputElement;

    field.value = "alice@example.test";
    field.dispatchEvent(new Event("input", { bubbles: true }));
    field.dispatchEvent(new Event("change", { bubbles: true }));

    expect(recorded.events.map((event) => event.kind)).toEqual(["fill"]);
  });

  it("records one fill when the change follows the focusout", () => {
    // The order a browser can report when the field loses focus: the buffered typing is
    // flushed, and the change that follows is the very same entry.
    document.body.innerHTML = COMBOBOX;
    const recorded = harness();
    const field = document.getElementById("to") as HTMLInputElement;

    field.value = "alice@example.test";
    field.dispatchEvent(new Event("input", { bubbles: true }));
    field.dispatchEvent(new Event("focusout", { bubbles: true }));
    field.dispatchEvent(new Event("change", { bubbles: true }));

    expect(recorded.events.map((event) => event.kind)).toEqual(["fill"]);
    expect(recorded.events[0]!.step).toMatchObject({
      action: "fill",
      value: { kind: "constant", value: "alice@example.test" },
    });
  });

  it("records one select when a value was chosen without typing", () => {
    document.body.innerHTML = COMBOBOX;
    const recorded = harness();
    const field = document.getElementById("to") as HTMLInputElement;

    // An option click sets the value with no input event on the field itself.
    field.value = "bob@example.test";
    field.dispatchEvent(new Event("change", { bubbles: true }));

    expect(recorded.events.map((event) => event.kind)).toEqual(["select"]);
  });

  it("does not repeat the fill when the field is typed into again", () => {
    document.body.innerHTML = COMBOBOX;
    const recorded = harness();
    const field = document.getElementById("to") as HTMLInputElement;

    field.value = "alice@example.test";
    field.dispatchEvent(new Event("input", { bubbles: true }));
    field.dispatchEvent(new Event("change", { bubbles: true }));
    field.value = "bob@example.test";
    field.dispatchEvent(new Event("input", { bubbles: true }));
    field.dispatchEvent(new Event("change", { bubbles: true }));

    expect(recorded.events.map((event) => event.kind)).toEqual(["fill", "fill"]);
    expect(recorded.events[1]!.step).toMatchObject({
      value: { kind: "constant", value: "bob@example.test" },
    });
  });
});

describe("a rich-text body (requirement 10)", () => {
  it("records typing into a contenteditable as one fill with its text", () => {
    document.body.innerHTML =
      "<div id='note' contenteditable='true' role='textbox' aria-label='Message body'></div>";
    const recorded = harness();
    const body = document.getElementById("note")!;

    body.textContent = "Called Ana about the renewal.";
    body.dispatchEvent(new Event("input", { bubbles: true }));
    body.dispatchEvent(new Event("focusout", { bubbles: true }));

    expect(recorded.events).toHaveLength(1);
    expect(recorded.events[0]).toMatchObject({
      kind: "fill",
      step: {
        action: "fill",
        value: { kind: "constant", value: "Called Ana about the renewal." },
        clear_first: true,
      },
    });
  });

  it("keeps the typed text out of the target rungs", () => {
    document.body.innerHTML = "<div id='note' contenteditable='true' aria-label='Notes'></div>";
    const recorded = harness();
    const body = document.getElementById("note")!;

    body.textContent = "secret-ish internal note";
    body.dispatchEvent(new Event("input", { bubbles: true }));
    body.dispatchEvent(new Event("focusout", { bubbles: true }));

    // The ladder describes the element; the value lives on the step, once.
    expect(JSON.stringify(recorded.events[0]!.step!.targets)).not.toContain("secret-ish");
  });

  it("never records a rich-text body inside a credential context", () => {
    document.body.innerHTML =
      "<form aria-label='Sign in'>" +
      "<label for='pw'>Password</label><input id='pw' type='password'>" +
      "<div id='note' contenteditable='true' aria-label='Recovery hint'></div>" +
      "</form>";
    const recorded = harness();
    const body = document.getElementById("note")!;

    body.textContent = "the answer to my security question";
    body.dispatchEvent(new Event("input", { bubbles: true }));
    body.dispatchEvent(new Event("focusout", { bubbles: true }));

    expect(recorded.events).toHaveLength(0);
  });

  it("never records a rich-text body inside an autocomplete=off context", () => {
    document.body.innerHTML =
      "<form aria-label='Secure note' autocomplete='off'>" +
      "<div id='note' contenteditable='true' aria-label='Recovery hint'></div>" +
      "</form>";
    const recorded = harness();
    const body = document.getElementById("note")!;

    body.textContent = "a private recovery hint";
    body.dispatchEvent(new Event("input", { bubbles: true }));
    body.dispatchEvent(new Event("focusout", { bubbles: true }));

    expect(recorded.events).toHaveLength(0);
  });
});

describe("a required field (requirement 5)", () => {
  it("notes required on the event envelope, never on the step", () => {
    document.body.innerHTML =
      "<label for='email'>Email</label><input id='email' required>" +
      "<label for='alias'>Alias</label><input id='alias' aria-required='true'>" +
      "<label for='note'>Note</label><input id='note'>";
    const recorded = harness();

    for (const [id, value] of [
      ["email", "ravi@example.test"],
      ["alias", "ravi"],
      ["note", "hello"],
    ] as const) {
      const field = document.getElementById(id) as HTMLInputElement;
      field.value = value;
      field.dispatchEvent(new Event("input", { bubbles: true }));
      field.dispatchEvent(new Event("focusout", { bubbles: true }));
    }

    expect(recorded.events.map((event) => event.kind)).toEqual(["fill", "fill", "fill"]);
    expect(recorded.events[0]!.required).toBe(true); // native required
    expect(recorded.events[1]!.required).toBe(true); // aria-required
    expect(recorded.events[2]!.required).toBeUndefined(); // optional
    for (const event of recorded.events) {
      expect(event.step).not.toBeNull();
      expect((event.step as unknown as Record<string, unknown>).required).toBeUndefined();
    }
  });
});

describe("a step inside a same-origin iframe (PL-018b)", () => {
  it("carries the top page's URL, not the frame's own", () => {
    const frame = document.createElement("iframe");
    document.body.appendChild(frame);
    const inner = frame.contentDocument as Document;
    inner.body.innerHTML = "<button type='button'>Save note</button>";
    const events: PendingEvent[] = [];
    const detach = attachRecorder(inner, {
      send: (event) => events.push(event),
      isActive: () => true,
      now: () => "2026-10-17T00:00:00.000Z",
    });

    (inner.querySelector("button") as HTMLButtonElement).click();
    detach();

    expect(events.length).toBeGreaterThan(0);
    for (const event of events) {
      expect((event as { url?: string }).url).toBe(window.location.href);
    }
  });
});

describe("a choice made by pressing an option (PL-078)", () => {
  function dropdown(): void {
    // OrangeHRM's OXD select: the value and the list share one wrapper, no `role=combobox`, no
    // `aria-controls`, and the list is a plain `role=listbox` with no id.
    document.body.innerHTML =
      "<div class='oxd-select-wrapper'>" +
      "<div class='oxd-select-text'>" +
      "<div id='activity' class='oxd-select-text-input' tabindex='0'>-- Select --</div>" +
      "</div>" +
      "<div role='listbox' class='oxd-select-dropdown'>" +
      "<div role='option'>-- Select --</div><div role='option'>Development</div>" +
      "</div></div>";
  }

  it("records the press as a select of the list's control, because no click ever fires", async () => {
    dropdown();
    const recorded = harness();
    const option = document.querySelectorAll("[role='option']")[1]!;
    const listbox = document.querySelector("[role='listbox']")!;

    // The widget selects on the press and closes the list before the release, so the browser
    // dispatches no click at all -- exactly what OrangeHRM does (a capture listener on the real
    // page saw pointerdown and mousedown on the option, and nothing after them).
    option.dispatchEvent(new Event("pointerdown", { bubbles: true }));
    listbox.remove();
    document.body.dispatchEvent(new Event("mouseup", { bubbles: true }));
    await new Promise((resolve) => setTimeout(resolve, 0));

    const steps = recorded.events.filter((event) => event.step !== null);
    expect(steps).toHaveLength(1);
    expect(steps[0]!.kind).toBe("select");
    expect(steps[0]!.step).toMatchObject({
      action: "select",
      value: { kind: "constant", value: "Development" },
      option_match: "text",
    });
    expect(JSON.stringify(steps[0]!.step!.targets)).toContain("#activity");
  });

  it("does not record the click a widget dispatches after the same press a second time", async () => {
    dropdown();
    const recorded = harness();
    const option = document.querySelectorAll("[role='option']")[1]!;

    option.dispatchEvent(new Event("pointerdown", { bubbles: true }));
    document.querySelector("[role='listbox']")!.remove();
    (option as HTMLElement).click();
    await new Promise((resolve) => setTimeout(resolve, 0));

    const steps = recorded.events.filter((event) => event.step !== null);
    expect(steps).toHaveLength(1);
    expect(steps[0]!.kind).toBe("select");
  });

  it("does not read a chip (a list of chosen values) as a choice being made", async () => {
    // The messy composer's recipients list: `role=listbox` whose options are the chips already
    // chosen. It stays open, so the press chose nothing new.
    document.body.innerHTML =
      "<div class='composer'>" +
      "<input id='to' role='combobox'>" +
      "<div role='listbox' aria-label='Selected recipients'>" +
      "<div class='chip' role='option' aria-selected='false'>alice@example.test</div>" +
      "</div></div>";
    const recorded = harness();

    document
      .querySelector("[role='option']")!
      .dispatchEvent(new Event("pointerdown", { bubbles: true }));
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(recorded.events.filter((event) => event.step !== null)).toHaveLength(0);
  });
});
