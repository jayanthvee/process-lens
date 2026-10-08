import { beforeEach, describe, expect, it } from "vitest";
import { attachRecorder } from "../src/content/recorder";
import type { PendingEvent } from "../src/shared/events";
import { resetStepIds } from "../src/shared/events";

interface Harness {
  events: PendingEvent[];
  setActive: (value: boolean) => void;
  detach: () => void;
}

function harness(): Harness {
  const events: PendingEvent[] = [];
  let active = true;
  const detach = attachRecorder(document, {
    send: (event) => events.push(event),
    isActive: () => active,
    url: () => "https://crm.example.com/leads",
    now: () => "2026-10-08T00:00:00.000Z",
  });
  return {
    events,
    setActive: (value: boolean) => {
      active = value;
    },
    detach,
  };
}

function typeInto(el: HTMLInputElement | HTMLTextAreaElement, value: string): void {
  el.value = value;
  el.dispatchEvent(new Event("input", { bubbles: true }));
}

beforeEach(() => {
  document.body.innerHTML = "";
  resetStepIds();
});

describe("click capture", () => {
  it("records a click on a button as a click step", () => {
    document.body.innerHTML = "<button id='save'>Save</button>";
    const recorded = harness();
    document.getElementById("save")!.click();

    expect(recorded.events).toHaveLength(1);
    const event = recorded.events[0]!;
    expect(event.kind).toBe("click");
    expect(event.url).toBe("https://crm.example.com/leads");
    expect(event.step).toMatchObject({ action: "click" });
    expect(event.step?.targets.length).toBeGreaterThan(0);
  });

  it("resolves a click on a nested element to the control that owns it", () => {
    document.body.innerHTML = "<button id='save'><span id='inner'>Save</span></button>";
    const recorded = harness();
    document.getElementById("inner")!.dispatchEvent(new Event("click", { bubbles: true }));

    expect(recorded.events).toHaveLength(1);
    const role = recorded.events[0]!.step?.targets[0];
    expect(role).toMatchObject({ by: "role", role: "button", name: "Save" });
  });

  it("does not record a click on a text field as a step", () => {
    document.body.innerHTML = "<input id='email'>";
    const recorded = harness();
    document.getElementById("email")!.dispatchEvent(new Event("click", { bubbles: true }));
    expect(recorded.events).toHaveLength(0);
  });
});

describe("fill capture", () => {
  it("records typing as a single fill step when the field loses focus", () => {
    document.body.innerHTML = "<label for='email'>Email</label><input id='email'>";
    const recorded = harness();
    const field = document.getElementById("email") as HTMLInputElement;

    typeInto(field, "ravi");
    typeInto(field, "ravi@example.com");
    field.dispatchEvent(new Event("focusout", { bubbles: true }));

    expect(recorded.events).toHaveLength(1);
    expect(recorded.events[0]).toMatchObject({
      kind: "fill",
      step: { action: "fill", value: { kind: "constant", value: "ravi@example.com" } },
    });
  });

  it("keeps events in the order they happened", () => {
    document.body.innerHTML =
      "<button id='new'>New lead</button><input id='name'><select id='interest'><option>A</option><option>B</option></select>";
    const recorded = harness();
    const field = document.getElementById("name") as HTMLInputElement;
    const select = document.getElementById("interest") as HTMLSelectElement;

    document.getElementById("new")!.dispatchEvent(new Event("click", { bubbles: true }));
    typeInto(field, "Ravi");
    field.dispatchEvent(new Event("focusout", { bubbles: true }));
    select.value = "B";
    select.dispatchEvent(new Event("change", { bubbles: true }));

    expect(recorded.events.map((event) => event.kind)).toEqual(["click", "fill", "select"]);
  });
});

describe("select capture", () => {
  it("records a native dropdown change as a select step", () => {
    document.body.innerHTML =
      "<select id='interest'><option>Interest</option><option>Data course</option></select>";
    const recorded = harness();
    const select = document.getElementById("interest") as HTMLSelectElement;
    select.value = "Data course";
    select.dispatchEvent(new Event("change", { bubbles: true }));

    expect(recorded.events).toHaveLength(1);
    expect(recorded.events[0]).toMatchObject({
      kind: "select",
      step: {
        action: "select",
        value: { kind: "constant", value: "Data course" },
        option_match: "text",
      },
    });
  });

  it("records a checkbox toggle as a click step", () => {
    document.body.innerHTML = "<input id='agree' type='checkbox'>";
    const recorded = harness();
    const box = document.getElementById("agree") as HTMLInputElement;
    box.checked = true;
    box.dispatchEvent(new Event("change", { bubbles: true }));

    expect(recorded.events).toHaveLength(1);
    expect(recorded.events[0]!.kind).toBe("click");
    expect(recorded.events[0]!.step?.action).toBe("click");
  });
});

describe("submit capture", () => {
  it("records a form submit as a submit event targeting the control", () => {
    document.body.innerHTML =
      "<form id='f' aria-label='New lead'><input id='name'><button id='go' type='submit'>Save</button></form>";
    const recorded = harness();
    document.getElementById("f")!.dispatchEvent(new Event("submit", { bubbles: true }));

    expect(recorded.events).toHaveLength(1);
    expect(recorded.events[0]!.kind).toBe("submit");
    expect(recorded.events[0]!.step?.action).toBe("click");
  });

  it("flushes a pending fill before the submit", () => {
    document.body.innerHTML = "<form id='f'><input id='name'><button type='submit'>Save</button></form>";
    const recorded = harness();
    const field = document.getElementById("name") as HTMLInputElement;
    typeInto(field, "Ravi");
    document.getElementById("f")!.dispatchEvent(new Event("submit", { bubbles: true }));

    expect(recorded.events.map((event) => event.kind)).toEqual(["fill", "submit"]);
  });
});

describe("password fields are never captured", () => {
  const SECRET = "correct-horse-battery-staple";

  it("ignores typing into a password field", () => {
    document.body.innerHTML = "<label for='pw'>Password</label><input id='pw' type='password'>";
    const recorded = harness();
    const field = document.getElementById("pw") as HTMLInputElement;

    typeInto(field, SECRET);
    field.dispatchEvent(new Event("focusout", { bubbles: true }));
    field.dispatchEvent(new Event("change", { bubbles: true }));

    expect(recorded.events).toHaveLength(0);
  });

  it("ignores a click on a password field", () => {
    document.body.innerHTML = "<input id='pw' type='password'>";
    const recorded = harness();
    document.getElementById("pw")!.dispatchEvent(new Event("click", { bubbles: true }));
    expect(recorded.events).toHaveLength(0);
  });

  it("keeps the secret out of every payload, even alongside other fields", () => {
    document.body.innerHTML =
      "<form id='f'>" +
      "<input id='user' aria-label='User'>" +
      "<input id='pw' type='password' aria-label='Password'>" +
      "<button type='submit'>Sign in</button>" +
      "</form>";
    const recorded = harness();
    const user = document.getElementById("user") as HTMLInputElement;
    const password = document.getElementById("pw") as HTMLInputElement;

    typeInto(user, "ravi@example.com");
    user.dispatchEvent(new Event("focusout", { bubbles: true }));
    typeInto(password, SECRET);
    password.dispatchEvent(new Event("focusout", { bubbles: true }));
    document.getElementById("f")!.dispatchEvent(new Event("submit", { bubbles: true }));

    const payload = JSON.stringify(recorded.events);
    expect(payload).not.toContain(SECRET);
    expect(payload).not.toContain("correct-horse");
    expect(recorded.events.map((event) => event.kind)).toEqual(["fill", "submit"]);
  });
});

describe("recording state", () => {
  it("captures nothing while inactive", () => {
    document.body.innerHTML = "<button id='save'>Save</button><input id='name'>";
    const recorded = harness();
    recorded.setActive(false);

    document.getElementById("save")!.dispatchEvent(new Event("click", { bubbles: true }));
    const field = document.getElementById("name") as HTMLInputElement;
    typeInto(field, "Ravi");
    field.dispatchEvent(new Event("focusout", { bubbles: true }));

    expect(recorded.events).toHaveLength(0);
  });

  it("stops capturing after it is detached", () => {
    document.body.innerHTML = "<button id='save'>Save</button>";
    const recorded = harness();
    recorded.detach();
    document.getElementById("save")!.dispatchEvent(new Event("click", { bubbles: true }));
    expect(recorded.events).toHaveLength(0);
  });
});
