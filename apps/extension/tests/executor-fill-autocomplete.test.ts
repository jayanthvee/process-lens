// PL-018 §4.3: completing a fill the recorder witnessed as an autocomplete.
//
// The recorder only sets `autocomplete: true` on a field whose suggestion the person accepted, so a
// replay must accept that suggestion too: a fill that cannot find it parks rather than submitting a
// half-typed value. The accept is a click through the guarded click path; Enter is allowed only
// when the widget offers no clickable option AND its own active descendant is the single match.
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { performStep } from "../src/executor/step-runner";
import { ExecutorError, type ExecutorStep } from "../src/executor/types";
import { fakeCdp } from "./helpers/fake-cdp";
import { installLayout } from "./helpers/layout";

let restoreLayout: (() => void) | null = null;

beforeEach(() => {
  document.body.innerHTML = "";
});

afterEach(() => {
  restoreLayout?.();
  restoreLayout = null;
});

/** jsdom has no layout engine; the click path measures a box and hit-tests it. */
function withLayout(): void {
  restoreLayout = installLayout();
}

/**
 * A witnessed fill of `#to` with the row's `email` column. `timeout_ms` bounds the popup poll, so a
 * test that expects to exhaust it does not wait the attended default.
 */
function fillStep(overrides: Partial<ExecutorStep> = {}): ExecutorStep {
  return {
    id: "to",
    action: "fill",
    targets: [{ by: "css", selector: "#to" }],
    value: { kind: "input", column: "email" },
    timeout_ms: 400,
    ...overrides,
  } as unknown as ExecutorStep;
}

function comboboxHtml(listbox: string, fieldAttributes = ""): string {
  return `<input id="to" role="combobox" aria-controls="dd" aria-expanded="true" ${fieldAttributes}>${listbox}`;
}

function keyEvents(cdp: ReturnType<typeof fakeCdp>): Array<Record<string, unknown>> {
  return cdp.callsTo("Input.dispatchKeyEvent").map((call) => call.params);
}

function enterKeys(cdp: ReturnType<typeof fakeCdp>): Array<Record<string, unknown>> {
  return keyEvents(cdp).filter((params) => params["key"] === "Enter");
}

function mouseTypes(cdp: ReturnType<typeof fakeCdp>): string[] {
  return cdp.callsTo("Input.dispatchMouseEvent").map((call) => String(call.params["type"]));
}

/** The element the executor last marked: what an input was actually sent to. */
function markedId(): string | null {
  return document.querySelector("[data-pl-target]")?.id ?? null;
}

describe("a witnessed fill whose popup offers one suggestion", () => {
  it("clicks the suggestion through the guarded click path, and presses no Enter", async () => {
    withLayout();
    document.body.innerHTML = comboboxHtml(
      `<ul id="dd" role="listbox"><li id="opt-1" role="option">ann@example.com</li></ul>`,
    );
    const fake = fakeCdp(undefined, { simulateInput: true });

    const report = await performStep(
      fillStep({ autocomplete: true }),
      { row: { email: "ann" } },
      undefined,
      { cdp: fake.cdp },
    );

    expect(report).toMatchObject({ id: "to", action: "fill", outcome: "done" });
    // The accept is a real mouse press and release — the guarded path, with its landing check.
    expect(mouseTypes(fake)).toContain("mousePressed");
    expect(mouseTypes(fake)).toContain("mouseReleased");
    // ...on the option, not on the field: the resolver stamped the option it clicked.
    expect(markedId()).toBe("opt-1");
    // And nothing pressed Enter.
    expect(enterKeys(fake)).toHaveLength(0);
  });

  it("clicks it even when the widget also marks it as the active descendant", async () => {
    withLayout();
    document.body.innerHTML = comboboxHtml(
      `<ul id="dd" role="listbox"><li id="opt-1" role="option">ann@example.com</li></ul>`,
      `aria-activedescendant="opt-1"`,
    );
    const fake = fakeCdp(undefined, { simulateInput: true });

    await performStep(fillStep({ autocomplete: true }), { row: { email: "ann" } }, undefined, {
      cdp: fake.cdp,
    });

    // The click is the deterministic accept; Enter is the fallback for a widget that offers no
    // clickable option, not a preference.
    expect(mouseTypes(fake)).toContain("mousePressed");
    expect(enterKeys(fake)).toHaveLength(0);
  });

  it("finds a popup that opens after the typing has finished", async () => {
    withLayout();
    document.body.innerHTML = comboboxHtml(`<ul id="dd" role="listbox"></ul>`);
    const fake = fakeCdp(undefined, { simulateInput: true });
    // A debounced widget inserts its options well after the last keystroke; the poll must read
    // again instead of giving up on the first empty read.
    const timer = setTimeout(() => {
      document
        .getElementById("dd")
        ?.insertAdjacentHTML("beforeend", `<li id="opt-1" role="option">ann@example.com</li>`);
    }, 250);

    try {
      const report = await performStep(
        fillStep({ autocomplete: true }),
        { row: { email: "ann" } },
        undefined,
        { cdp: fake.cdp },
      );
      expect(report.outcome).toBe("done");
    } finally {
      clearTimeout(timer);
    }

    expect(mouseTypes(fake)).toContain("mousePressed");
    expect(markedId()).toBe("opt-1");
    expect(enterKeys(fake)).toHaveLength(0);
  });

  it("presses Enter when the single match has no box to click and the widget marks it active", async () => {
    // No layout stub: the option has no box with area, so it is not a click target — the widget is
    // keyboard-only, and its own active descendant names this one match.
    document.body.innerHTML = comboboxHtml(
      `<ul id="dd" role="listbox"><li id="opt-1" role="option">ann@example.com</li></ul>`,
      `aria-activedescendant="opt-1"`,
    );
    const fake = fakeCdp(undefined, { simulateInput: true });

    await performStep(fillStep({ autocomplete: true }), { row: { email: "ann" } }, undefined, {
      cdp: fake.cdp,
    });

    const enter = enterKeys(fake);
    expect(enter).toHaveLength(2);
    expect(enter[0]).toMatchObject({
      type: "keyDown",
      key: "Enter",
      code: "Enter",
      windowsVirtualKeyCode: 13,
      text: "\r",
    });
    expect(enter[1]).toMatchObject({ type: "keyUp", key: "Enter" });
    // The keyboard accept is the only input after the fill: no mouse was ever pressed.
    expect(mouseTypes(fake)).toHaveLength(0);
  });

  it("presses Enter when the widget marks the match selected and names no active descendant", async () => {
    document.body.innerHTML = comboboxHtml(
      `<ul id="dd" role="listbox"><li id="opt-1" role="option" aria-selected="true">ann@example.com</li></ul>`,
    );
    const fake = fakeCdp(undefined, { simulateInput: true });

    await performStep(fillStep({ autocomplete: true }), { row: { email: "ann" } }, undefined, {
      cdp: fake.cdp,
    });

    expect(enterKeys(fake)).toHaveLength(2);
    expect(mouseTypes(fake)).toHaveLength(0);
  });

  it("presses Enter for a match the widget marks disabled, when it also marks it active", async () => {
    withLayout();
    document.body.innerHTML = comboboxHtml(
      `<ul id="dd" role="listbox"><li id="opt-1" role="option" aria-disabled="true">ann@example.com</li></ul>`,
      `aria-activedescendant="opt-1"`,
    );
    const fake = fakeCdp(undefined, { simulateInput: true });

    await performStep(fillStep({ autocomplete: true }), { row: { email: "ann" } }, undefined, {
      cdp: fake.cdp,
    });

    expect(mouseTypes(fake)).toHaveLength(0);
    expect(enterKeys(fake)).toHaveLength(2);
  });

  it("parks rather than press Enter blind when the widget marks no suggestion active", async () => {
    // No layout stub and no active descendant: the match is not clickable and nothing points at it.
    document.body.innerHTML = comboboxHtml(
      `<ul id="dd" role="listbox"><li id="opt-1" role="option">ann@example.com</li></ul>`,
    );
    const fake = fakeCdp(undefined, { simulateInput: true });

    const error = await performStep(
      fillStep({ autocomplete: true }),
      { row: { email: "ann" } },
      undefined,
      { cdp: fake.cdp },
    ).catch((thrown: unknown) => thrown);

    expect(error).toBeInstanceOf(ExecutorError);
    expect((error as ExecutorError).stepId).toBe("to");
    expect((error as ExecutorError).disposition).toBe("park");
    expect((error as ExecutorError).message).toContain("Enter blind");
    expect(enterKeys(fake)).toHaveLength(0);
    expect(mouseTypes(fake)).toHaveLength(0);
  });
});

describe("a witnessed fill whose suggestion is missing", () => {
  it("parks when no suggestion contains the typed value, naming the value", async () => {
    document.body.innerHTML = comboboxHtml(
      `<ul id="dd" role="listbox"><li id="opt-1" role="option">ann@example.com</li></ul>`,
    );
    const fake = fakeCdp(undefined, { simulateInput: true });

    const error = await performStep(
      fillStep({ autocomplete: true, timeout_ms: 150 }),
      { row: { email: "zzz" } },
      undefined,
      { cdp: fake.cdp },
    ).catch((thrown: unknown) => thrown);

    expect(error).toBeInstanceOf(ExecutorError);
    expect((error as ExecutorError).disposition).toBe("park");
    expect((error as ExecutorError).message).toContain('no suggestion contains "zzz"');
    expect(mouseTypes(fake)).toHaveLength(0);
    expect(enterKeys(fake)).toHaveLength(0);
  });

  it("parks when more than one suggestion contains the typed value, naming the count", async () => {
    document.body.innerHTML = comboboxHtml(
      `<ul id="dd" role="listbox"><li role="option">ann@example.com</li><li role="option">anna@example.com</li></ul>`,
    );
    const fake = fakeCdp(undefined, { simulateInput: true });

    const error = await performStep(
      fillStep({ autocomplete: true, timeout_ms: 150 }),
      { row: { email: "ann" } },
      undefined,
      { cdp: fake.cdp },
    ).catch((thrown: unknown) => thrown);

    expect(error).toBeInstanceOf(ExecutorError);
    expect((error as ExecutorError).disposition).toBe("park");
    expect((error as ExecutorError).message).toContain('more than one suggestion contains "ann"');
    expect((error as ExecutorError).message).toContain("(2 matches)");
    expect(mouseTypes(fake)).toHaveLength(0);
    expect(enterKeys(fake)).toHaveLength(0);
  });

  it("parks when the control names a listbox that is not in the document", async () => {
    document.body.innerHTML = `<input id="to" role="combobox" aria-controls="gone" aria-expanded="true">`;
    const fake = fakeCdp(undefined, { simulateInput: true });

    const error = await performStep(
      fillStep({ autocomplete: true }),
      { row: { email: "ann" } },
      undefined,
      { cdp: fake.cdp },
    ).catch((thrown: unknown) => thrown);

    expect(error).toBeInstanceOf(ExecutorError);
    expect((error as ExecutorError).disposition).toBe("park");
    expect((error as ExecutorError).message).toContain("not in the document");
    expect(mouseTypes(fake)).toHaveLength(0);
  });

  it("leaves the fill standing when the field shows no listbox at all", async () => {
    // A widget that never opened a popup is filled and left alone: no park, no click.
    document.body.innerHTML = `<input id="to" type="text">`;
    const fake = fakeCdp(undefined, { simulateInput: true });

    const report = await performStep(
      fillStep({ autocomplete: true }),
      { row: { email: "ann" } },
      undefined,
      { cdp: fake.cdp },
    );

    expect(report).toMatchObject({ id: "to", action: "fill", outcome: "done" });
    expect(mouseTypes(fake)).toHaveLength(0);
    expect(enterKeys(fake)).toHaveLength(0);
  });
});

describe("a fill without the autocomplete witness", () => {
  it("resolves no listbox, polls nothing, and sends no extra input", async () => {
    withLayout();
    document.body.innerHTML = comboboxHtml(
      `<ul id="dd" role="listbox"><li id="opt-1" role="option">ann@example.com</li></ul>`,
    );
    const fake = fakeCdp(undefined, { simulateInput: true });

    const report = await performStep(fillStep(), { row: { email: "ann" } }, undefined, {
      cdp: fake.cdp,
    });

    expect(report.outcome).toBe("done");
    // The plain fill's own three reads: resolve the field, prepare it, read it back. Nothing else.
    const evaluates = fake.callsTo("Runtime.evaluate").map((call) => String(call.params["expression"]));
    expect(evaluates).toHaveLength(3);
    // The autocomplete probe is the thing that must not run — not the substring "listbox", which
    // the ladder's own source mentions in prose.
    expect(evaluates.some((expression) => expression.includes("plSuggestionMatches"))).toBe(false);
    // The only key events are the Backspace pair that clears the field.
    expect(keyEvents(fake).map((params) => params["key"])).toEqual(["Backspace", "Backspace"]);
    expect(mouseTypes(fake)).toHaveLength(0);
  });
});
