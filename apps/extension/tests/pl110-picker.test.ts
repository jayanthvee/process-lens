// PL-110: typing into a box that searches as you type.
//
// A picker rewrites the box while the run types, so the strict "the fill did not stick" read-back
// is wrong for it: the run must let the listbox settle, then choose the option a person would. A
// plain box keeps today's refusal. The markup is generic (role/aria only), never one product's.
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { RecorderSession } from "../src/background/recorder-session";
import type { PendingEvent } from "../src/shared/events";
import { performStep } from "../src/executor/step-runner";
import { ExecutorError, type ExecutorStep } from "../src/executor/types";
import { fakeCdp } from "./helpers/fake-cdp";
import { installLayout } from "./helpers/layout";

let restoreLayout: (() => void) | null = null;

beforeEach(() => {
  document.body.innerHTML = "";
  restoreLayout = installLayout();
});

afterEach(() => {
  restoreLayout?.();
  restoreLayout = null;
});

function fillStep(overrides: Partial<ExecutorStep> = {}): ExecutorStep {
  return {
    id: "person",
    action: "fill",
    targets: [{ by: "css", selector: "#person" }],
    value: { kind: "input", column: "contact" },
    timeout_ms: 600,
    ...overrides,
  } as unknown as ExecutorStep;
}

/** A combobox that names its listbox, with the given option texts. */
function pickerHtml(options: string[], extra = ""): string {
  return (
    `<input id="person" role="combobox" aria-controls="people" aria-autocomplete="list" ${extra}>` +
    `<ul id="people" role="listbox">` +
    options.map((text, index) => `<li id="opt-${index}" role="option">${text}</li>`).join("") +
    `</ul>`
  );
}

/** The option a press landed on, as the test page recorded it. */
function installedPicker(options: { rewrite?: (typed: string) => string; delayMs?: number } = {}) {
  const field = document.getElementById("person") as HTMLInputElement;
  const picked: string[] = [];
  const wire = (): void => {
    for (const el of Array.from(document.querySelectorAll("[role='option']"))) {
      // A real picker acts on the press (many close the list on mousedown), and the fake transport
      // dispatches mousedown/mouseup for a press and release, so the press is the event to watch.
      el.addEventListener("mousedown", () => {
        const text = (el.textContent ?? "").trim();
        picked.push(text);
        // A real picker puts the chosen record's name in the box, not the Create wording.
        field.value = text.replace(/^Create\s+"|"$/g, "");
        field.dispatchEvent(new Event("input", { bubbles: true }));
      });
    }
  };
  if (options.delayMs === undefined) {
    wire();
  } else {
    // The listbox arrives late, as a debounced picker's does.
    const hold = document.getElementById("people")!.innerHTML;
    document.getElementById("people")!.innerHTML = "";
    setTimeout(() => {
      document.getElementById("people")!.innerHTML = hold;
      wire();
    }, options.delayMs);
  }
  return {
    picked,
    fake: fakeCdp(
      (method, params) => {
        if (method !== "Input.insertText") return undefined;
        const typed = String(params["text"] ?? "");
        // The picker rewrites the box while it searches.
        field.value = options.rewrite ? options.rewrite(typed) : typed;
        field.dispatchEvent(new Event("input", { bubbles: true }));
        return {};
      },
      { simulateInput: true },
    ),
  };
}

describe("a picker whose box changes while it types", () => {
  it("picks the exact option instead of refusing 'the fill did not stick'", async () => {
    document.body.innerHTML = pickerHtml(["Nadia Rahman"]);
    const { fake, picked } = installedPicker({
      rewrite: (typed) => `${typed}!`,
    });

    const report = await performStep(fillStep(), { row: { contact: "Nadia Rahman" } }, undefined, {
      cdp: fake.cdp,
    });

    expect(report.outcome).toBe("done");
    expect(picked).toEqual(["Nadia Rahman"]);
    expect((document.getElementById("person") as HTMLInputElement).value).toBe("Nadia Rahman");
  });

  it("prefers an existing exact match over the Create option", async () => {
    document.body.innerHTML = pickerHtml([
      'Create "Nadia Rahman"',
      "Nadia Rahman",
    ]);
    const { fake, picked } = installedPicker({ rewrite: (typed) => typed });

    await performStep(fillStep(), { row: { contact: "Nadia Rahman" } }, undefined, {
      cdp: fake.cdp,
    });

    expect(picked).toEqual(["Nadia Rahman"]);
  });

  it("waits for a listbox that arrives after the typing has finished", async () => {
    document.body.innerHTML = pickerHtml(["Nadia Rahman"]);
    const { fake, picked } = installedPicker({ delayMs: 120 });

    const report = await performStep(fillStep(), { row: { contact: "Nadia Rahman" } }, undefined, {
      cdp: fake.cdp,
    });

    expect(report.outcome).toBe("done");
    expect(picked).toEqual(["Nadia Rahman"]);
  });
});

describe("a picker with no existing record", () => {
  it("chooses the Create option the recording used", async () => {
    document.body.innerHTML = pickerHtml(['Create "Nadia Rahman"', "Someone Else"]);
    const { fake, picked } = installedPicker();

    const report = await performStep(fillStep(), { row: { contact: "Nadia Rahman" } }, undefined, {
      cdp: fake.cdp,
    });

    expect(report.outcome).toBe("done");
    expect(picked).toEqual(['Create "Nadia Rahman"']);
  });

  it("parks with one plain sentence when there is no match and no way to create one", async () => {
    document.body.innerHTML = pickerHtml(["Someone Else", "Another Person"]);
    const { fake } = installedPicker();

    let error: unknown = null;
    try {
      await performStep(
        fillStep({ name: "Fill Contact" }),
        { row: { contact: "Nadia Rahman" } },
        undefined,
        { cdp: fake.cdp },
      );
    } catch (caught) {
      error = caught;
    }

    expect(error).toBeInstanceOf(ExecutorError);
    expect((error as ExecutorError).message).toBe(
      'No match for “Nadia Rahman” in Contact, and no way to create it.',
    );
  });
});

describe("a plain field is not a picker", () => {
  it("still refuses a box the page rewrites as it was typed", async () => {
    document.body.innerHTML = `<input id="person">`;
    const { fake } = installedPicker({ rewrite: (typed) => `${typed} (edited)` });

    await expect(
      performStep(fillStep(), { row: { contact: "Nadia Rahman" } }, undefined, { cdp: fake.cdp }),
    ).rejects.toThrow(/did not stick/);
  });
});

// --- recording ---------------------------------------------------------------------------------

function fill(seq: number, id: string, name: string, value: string): PendingEvent {
  return {
    at: "2026-10-27T10:00:00Z",
    kind: "fill",
    url: "https://app.example.test/new",
    step: {
      id,
      action: "fill",
      name,
      targets: [{ by: "role", role: "combobox", name }],
      value: { kind: "constant", value },
    },
  };
}

describe("the recording marks a pick, not a plain fill", () => {
  it("marks a fill that was followed by a click on an option", () => {
    const session = new RecorderSession();
    session.append(fill(1, "fill-1", "Contact", "Nadia Rahman"));
    session.append({
      at: "2026-10-27T10:00:01Z",
      kind: "click",
      url: "https://app.example.test/new",
      step: {
        id: "click-1",
        action: "click",
        name: 'Click Create "Nadia Rahman"',
        targets: [{ by: "role", role: "option", name: 'Create "Nadia Rahman"' }],
      },
    });

    const fillEvent = session.events.find((event) => event.step?.action === "fill");
    expect(fillEvent?.autocomplete).toBe(true);
  });

  it("leaves a fill that ended a run of its own as a plain fill", () => {
    const session = new RecorderSession();
    session.append(fill(1, "fill-1", "Notes", "called on Monday"));

    const fillEvent = session.events.find((event) => event.step?.action === "fill");
    expect(fillEvent?.autocomplete).toBeUndefined();
  });
});
