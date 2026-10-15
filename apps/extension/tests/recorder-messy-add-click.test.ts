// The composer's Add click, driven the way the browser delivers it (PL-018).
//
// The messy fixture's composer is the page that broke the recording. Typing a recipient and
// clicking Add appends a chip *and* clears the field, so the same recipient exists twice: once as
// the value that was typed into the input, and again as the chip the click produced. A recorder
// that emits both copies hands the compiler two adjacent fills of the same field, and the second
// (stale) one wins the coalesce.
//
// This test drives the served fixture markup through Playwright's dispatch order — pointerdown,
// mousedown, the blur's focusout, pointerup, mouseup, click — with the fixture's own app.js
// behavior standing in as the page's listeners, and asserts the recording holds each recipient
// exactly once, before the click that consumed it.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { attachRecorder } from "../src/content/recorder";
import { resetStepIds, type PendingEvent } from "../src/shared/events";
import type { RecordedStep } from "../src/shared/recipe-types";

const FIXTURE_DIR = resolve(process.cwd(), "../../fixtures/sites/messy-crm");

/** The value tokens the fixture's server substitutes with a stored value. Here they are blank. */
const VALUE_TOKENS = new Set(["TO_VALUE", "RECIPIENTS_VALUE", "NOTES_TEXT", "NOTES_VALUE"]);

let minted = 0;

/**
 * One load of the served composer page, done the way the fixture's server does it: every
 * `{{TOKEN}}` is replaced, with a fresh id for each `{{ID_*}}` token and an empty string for the
 * value tokens — so the input starts empty and the chips list holds only the static alice chip.
 */
function loadComposer(): Document {
  const raw = readFileSync(resolve(FIXTURE_DIR, "public", "index.html"), "utf8");
  const html = raw.replace(/\{\{([A-Z0-9_]+)\}\}/g, (_match, token: string) => {
    if (VALUE_TOKENS.has(token)) return "";
    minted += 1;
    return `:r${minted}:`;
  });
  const doc = document.implementation.createHTMLDocument("messy-crm");
  doc.documentElement.innerHTML = html;
  document.body.innerHTML = doc.body.innerHTML;
  return document;
}

/**
 * jsdom has no layout, so every element reads as invisible and `collectSnapshot` would skip the
 * whole page: the checkpoint would record nothing at all. Declaring the page visible is what makes
 * the visibility rule a decision in these tests rather than an accident.
 */
function declareVisible(): void {
  Object.defineProperty(Element.prototype, "checkVisibility", {
    configurable: true,
    value: () => true,
  });
}

interface Harness {
  events: PendingEvent[];
  detach: () => void;
}

function harness(): Harness {
  const events: PendingEvent[] = [];
  const detach = attachRecorder(document, {
    send: (event) => events.push(event),
    isActive: () => true,
    url: () => "http://127.0.0.1:8123/index.html",
    now: () => "2026-10-14T00:00:00.000Z",
  });
  return { events, detach };
}

/**
 * The fixture's own `addChip`, minus the two things this test does not need: the fresh id the page
 * mints for every chip (nothing here asserts on ids) and the hidden `recipients` mirror the
 * snapshot skips as a hidden input.
 */
function addChip(list: Element, address: string): Element {
  const chip = document.createElement("div");
  chip.className = "chip";
  chip.setAttribute("role", "option");
  chip.setAttribute("aria-selected", "false");
  chip.setAttribute("data-testid", "recipient-chip");
  chip.setAttribute("data-recipient", address);

  const name = document.createElement("span");
  name.className = "chip-name";
  name.textContent = address;
  chip.appendChild(name);

  const remove = document.createElement("button");
  remove.setAttribute("type", "button");
  remove.setAttribute("aria-label", `Remove ${address}`);
  remove.textContent = "✕";
  chip.appendChild(remove);

  list.appendChild(chip);
  return chip;
}

/**
 * What the fixture's app.js does on the composer: Add appends a chip and clears the field.
 *
 * `normalize` is the page's own transform on the value it puts in the chip — the fixture trims
 * (`var value = (address || "").trim()`), and a page that lowercases its chips does
 * `trim().toLowerCase()`. Stamping the chip with `normalize(input.value)` is what puts the
 * recorder's "this value was already typed" guard to the test, because the chip's value then
 * differs from the raw value the fill carries.
 */
function wireComposerBehavior(
  normalize: (value: string) => string = (value) => value.trim(),
): void {
  const input = document.querySelector<HTMLInputElement>("[data-testid='recipient-input']")!;
  const add = document.querySelector("[data-testid='recipient-add']")!;
  const chips = document.querySelector("[data-testid='recipient-chips']")!;

  add.addEventListener("click", () => {
    addChip(chips, normalize(input.value));
    // The page clears the box with no event of its own, exactly as the fixture does.
    input.value = "";
  });

  chips.addEventListener("click", (event) => {
    const node = event.target as Element | null;
    if (!node || typeof node.closest !== "function") return;
    const chip = node.closest(".chip");
    if (!chip) return;
    const remove = node.closest("button");
    if (remove && chip.contains(remove)) chip.remove();
  });
}

/** Typing: the field takes focus, holds the value, and reports one `input`. */
function fill(input: HTMLInputElement, value: string): void {
  input.focus();
  input.value = value;
  input.dispatchEvent(new Event("input", { bubbles: true, composed: true }));
}

/**
 * A real mouse click, in the browser's order. jsdom has no `PointerEvent`, so the two pointer
 * events are `MouseEvent`s dispatched under the pointer names — the recorder listens by name.
 * The `focusout` is the blur the browser performs during mousedown; it goes to whatever was
 * focused, and a field with nothing buffered is a no-op there.
 */
function click(el: Element, blurring: Element): void {
  el.dispatchEvent(new MouseEvent("pointerdown", { bubbles: true, composed: true, button: 0 }));
  el.dispatchEvent(new MouseEvent("mousedown", { bubbles: true, composed: true, button: 0 }));
  blurring.dispatchEvent(new FocusEvent("focusout", { bubbles: true }));
  el.dispatchEvent(new MouseEvent("pointerup", { bubbles: true, composed: true, button: 0 }));
  el.dispatchEvent(new MouseEvent("mouseup", { bubbles: true, composed: true, button: 0 }));
  el.dispatchEvent(new MouseEvent("click", { bubbles: true, composed: true }));
}

/** The events that carry a step, as `action value-or-name` lines, in the order they were sent. */
function outline(events: PendingEvent[]): string[] {
  return events
    .filter((event): event is PendingEvent & { step: RecordedStep } => event.step !== null)
    .map((event) => {
      const step = event.step;
      if (step.action === "fill") {
        const value = step.value.kind === "constant" ? step.value.value : "<dynamic>";
        return `fill ${value}`;
      }
      const name = step.name ?? "";
      return `${step.action} ${name.replace(/^[A-Za-z]+ /, "")}`;
    });
}

let recorded: Harness | null = null;

beforeEach(() => {
  document.body.innerHTML = "";
  minted = 0;
  resetStepIds();
  declareVisible();
});

afterEach(() => {
  recorded?.detach();
  recorded = null;
});

describe("the messy composer's Add click", () => {
  it("records each recipient once, before the click that consumed it", () => {
    loadComposer();
    wireComposerBehavior();
    recorded = harness();
    const input = document.querySelector<HTMLInputElement>("[data-testid='recipient-input']")!;
    const add = document.querySelector("[data-testid='recipient-add']")!;

    // 1. type bob, 2. Add — the click that consumes bob's value and turns it into a chip.
    fill(input, "bob@example.test");
    click(add, input);

    // 3. type carol, 4. Add again. The checkpoint this press takes must not re-emit bob.
    fill(input, "carol@example.test");
    click(add, input);

    // 5. remove carol through the chip's own ✕.
    const remove = document.querySelector<HTMLButtonElement>(
      "button[aria-label='Remove carol@example.test']",
    )!;
    click(remove, add);

    expect(outline(recorded.events)).toEqual([
      "fill bob@example.test",
      "click Add",
      "fill carol@example.test",
      "click Add",
      "click Remove carol@example.test",
    ]);
    // No chip fill at all: every recipient here was typed, and the click that consumed it is the
    // step that put it there. A chip copy alongside the typed fill is what the compiler coalesces
    // into the stale value.
    expect(recorded.events.filter((event) => event.chip === true)).toHaveLength(0);
  });

  it("suppresses the chip when the page normalizes the typed value", () => {
    // Scenario 1: the page trims. The recorded fill carries the raw typed value — trailing space
    // and all — while the chip carries the trimmed one. Comparing the two raw would miss, and the
    // chip fill that followed would be the stale value the compiler's coalesce keeps (PL-018).
    loadComposer();
    wireComposerBehavior();
    recorded = harness();
    let input = document.querySelector<HTMLInputElement>("[data-testid='recipient-input']")!;
    let add = document.querySelector("[data-testid='recipient-add']")!;

    fill(input, "bob@example.test ");
    click(add, input);

    fill(input, "carol@example.test");
    click(add, input);

    // The chip the page wrote is the trimmed value, so this is the label the fixture produces.
    click(
      document.querySelector<HTMLButtonElement>(
        "button[aria-label='Remove carol@example.test']",
      )!,
      add,
    );

    expect(outline(recorded.events)).toEqual([
      "fill bob@example.test ", // the raw typed value, exactly as the fill recorded it
      "click Add",
      "fill carol@example.test",
      "click Add",
      "click Remove carol@example.test",
    ]);
    expect(recorded.events.filter((event) => event.chip === true)).toHaveLength(0);

    // Scenario 2: a page that lowercases its chips. The fills keep the typed case, the chips are
    // suppressed all the same, and the ✕ is clicked by the label the page actually wrote.
    recorded.detach();
    loadComposer();
    wireComposerBehavior((value) => value.trim().toLowerCase());
    recorded = harness();
    input = document.querySelector<HTMLInputElement>("[data-testid='recipient-input']")!;
    add = document.querySelector("[data-testid='recipient-add']")!;

    fill(input, "BOB@example.test");
    click(add, input);

    fill(input, "CAROL@example.test");
    click(add, input);

    click(
      document.querySelector<HTMLButtonElement>("button[aria-label='Remove bob@example.test']")!,
      add,
    );

    expect(outline(recorded.events)).toEqual([
      "fill BOB@example.test",
      "click Add",
      "fill CAROL@example.test",
      "click Add",
      "click Remove bob@example.test",
    ]);
    expect(recorded.events.filter((event) => event.chip === true)).toHaveLength(0);
  });
});
