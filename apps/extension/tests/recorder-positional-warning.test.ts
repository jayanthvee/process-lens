// Requirement 9's warning: a step whose only remaining rung is a positional path is
// recorded with a caveat, and the Make recipe form shows it. A positional path
// (`div > div:nth-child(3)`) breaks the moment the layout changes, so it is worth
// saying out loud rather than hiding behind "a step always has some rung".
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { beforeEach, describe, expect, it } from "vitest";
import { attachRecorder, POSITIONAL_ONLY_WARNING } from "../src/content/recorder";
import { createMakeRecipeForm, recordedWarnings } from "../src/popup/make-recipe";
import type { PendingEvent } from "../src/shared/events";
import { resetStepIds } from "../src/shared/events";
import { hasOnlyPositionalTargets } from "../src/shared/fingerprint";
import type { LocatorRung, RecordedEvent } from "../src/shared/recipe-types";

const POPUP_HTML = readFileSync(
  resolve(dirname(fileURLToPath(import.meta.url)), "../src/popup/popup.html"),
  "utf8",
);
const POPUP_BODY = /<body[^>]*>([\s\S]*?)<\/body>/.exec(POPUP_HTML)?.[1] ?? POPUP_HTML;

const STAMP = "2026-10-12T09:00:00.000Z";
const PAGE = "https://crm.example.test/leads";

function harness(): { events: PendingEvent[]; detach: () => void } {
  const events: PendingEvent[] = [];
  const detach = attachRecorder(document, {
    send: (event) => events.push(event),
    isActive: () => true,
    url: () => PAGE,
    now: () => STAMP,
  });
  return { events, detach };
}

function click(el: Element): void {
  el.dispatchEvent(new Event("click", { bubbles: true }));
}

/** A recorded click whose target ladder is one positional css rung. */
function warnedEvent(): RecordedEvent {
  return {
    seq: 1,
    at: STAMP,
    kind: "click",
    url: PAGE,
    step: {
      id: "click-1",
      name: "Click Remove",
      action: "click",
      targets: [{ by: "css", selector: "button" }],
    },
    warning: POSITIONAL_ONLY_WARNING,
  };
}

/** A recording with no warning anywhere. */
function cleanEvents(): RecordedEvent[] {
  return [
    {
      seq: 1,
      at: STAMP,
      kind: "click",
      url: PAGE,
      step: {
        id: "click-1",
        name: "Click Save",
        action: "click",
        targets: [{ by: "css", selector: "#lead-save" }],
      },
    },
  ];
}

function control(id: string): HTMLElement {
  const found = document.getElementById(id);
  if (!found) throw new Error(`the popup markup is missing #${id}`);
  return found;
}

function fakeFetch(): typeof fetch {
  return (async () => ({ ok: true, status: 200, json: async () => ({}) })) as unknown as typeof fetch;
}

beforeEach(() => {
  document.body.innerHTML = "";
  resetStepIds();
});

describe("hasOnlyPositionalTargets", () => {
  const css = (selector: string): LocatorRung[] => [{ by: "css", selector }];

  it("treats a bare positional path as positional-only and a named selector as durable", () => {
    expect(hasOnlyPositionalTargets(css("button"))).toBe(true);
    expect(hasOnlyPositionalTargets(css("div > span:nth-of-type(2)"))).toBe(true);
    expect(hasOnlyPositionalTargets(css("#lead-save"))).toBe(false);
    expect(hasOnlyPositionalTargets(css("button.lead-save"))).toBe(false);
    expect(hasOnlyPositionalTargets(css('button[data-testid="x"]'))).toBe(false);
    // A fuzzy text rung is not a durable rung either.
    expect(hasOnlyPositionalTargets([{ by: "text_fuzzy", value: "Save" }])).toBe(true);
    // A role, test id or label rung is durable on its own.
    expect(hasOnlyPositionalTargets([{ by: "role", role: "button", name: "Save" }])).toBe(false);
    expect(hasOnlyPositionalTargets([{ by: "testid", value: "lead-save" }])).toBe(false);
    expect(hasOnlyPositionalTargets([{ by: "label", text: "Email" }])).toBe(false);
  });
});

describe("a click whose durable rungs are all gone (requirement 9)", () => {
  it("is recorded with the warning, on the event and not on the step", () => {
    document.body.innerHTML = "<button id=':qq'></button>";
    const recorded = harness();

    click(document.querySelector("button")!);

    expect(recorded.events).toHaveLength(1);
    const event = recorded.events[0]!;
    expect(event.warning).toContain("only a positional selector remains");
    // The step stays a plain recipe step: the schema forbids unknown step properties.
    expect(JSON.stringify(event.step)).not.toContain("warning");
    expect(event.step!.targets).toEqual([{ by: "css", selector: "button" }]);
  });

  it("records no warning when a stable id names the element", () => {
    document.body.innerHTML = "<button id='lead-save'>Save</button>";
    const recorded = harness();

    click(document.querySelector("button")!);

    expect(recorded.events).toHaveLength(1);
    expect(recorded.events[0]!.warning).toBeUndefined();
  });

  it("records no warning when a generated id still has a name", () => {
    document.body.innerHTML = "<button id=':r1:' aria-label='Remove alice@example.test'></button>";
    const recorded = harness();

    click(document.querySelector("button")!);

    expect(recorded.events).toHaveLength(1);
    expect(recorded.events[0]!.warning).toBeUndefined();
  });
});

describe("recordedWarnings", () => {
  it("returns one sentence naming the step for an event that carries a warning", () => {
    expect(recordedWarnings([warnedEvent()])).toEqual([
      `step click-1: ${POSITIONAL_ONLY_WARNING}`,
    ]);
  });

  it("returns an empty list for a recording with no warnings", () => {
    expect(recordedWarnings(cleanEvents())).toEqual([]);
  });
});

describe("the make-recipe form", () => {
  it("shows the recorder's warning after refresh", () => {
    document.body.innerHTML = POPUP_BODY;
    const form = createMakeRecipeForm({
      root: document,
      storage: { get: async () => ({}), set: async () => undefined },
      fetchImpl: fakeFetch(),
      baseUrl: "http://127.0.0.1:8000",
    });

    form.refresh([warnedEvent()]);

    const warnings = control("make-warnings").textContent ?? "";
    expect(warnings).toContain("only a positional selector remains");
    expect(warnings).toContain("click-1");
  });
});
