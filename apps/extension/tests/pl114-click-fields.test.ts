// PL-114 items 1, 2 and 6: a field the person clicks is recorded as the value it ends in.
//
// A checkbox click was recorded as a click, so replay flipped it: a row whose box was already
// ticked while teaching was unticked by every run. A radio group was a click on one option, so
// every row chose the option that was chosen during teaching. Both are now one step that names the
// field and the value: `check` with the state it ended in, `select` on the group with the chosen
// option's own label.
//
// Two markups per kind, so a fix cannot match one site's shape.
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { attachRecorder } from "../src/content/recorder";
import type { CheckStep, SelectStep, RecordedStep } from "../src/shared/recipe-types";
import type { PendingEvent } from "../src/shared/events";
import { resetStepIds } from "../src/shared/events";
import { installLayout } from "./helpers/layout";

const PAGE = "https://app.example.test/settings";

let restoreLayout: (() => void) | null = null;

function harness(): { events: PendingEvent[]; detach: () => void } {
  const events: PendingEvent[] = [];
  const detach = attachRecorder(document, {
    send: (event) => events.push(event),
    isActive: () => true,
    url: () => PAGE,
    now: () => "2026-10-27T10:00:00.000Z",
  });
  return { events, detach };
}

function steps(events: PendingEvent[]): RecordedStep[] {
  const found: RecordedStep[] = [];
  for (const event of events) {
    if (event.step) found.push(event.step);
  }
  return found;
}

/** A check step, narrowed for the assertions below. */
function checkOf(step: RecordedStep | undefined): CheckStep {
  return step as CheckStep;
}

/** A select step, narrowed for the assertions below. */
function selectOf(step: RecordedStep | undefined): SelectStep {
  return step as SelectStep;
}

beforeEach(() => {
  document.body.innerHTML = "";
  resetStepIds();
  restoreLayout = installLayout();
});

afterEach(() => {
  restoreLayout?.();
  restoreLayout = null;
});

describe("a checkbox (PL-114 item 1)", () => {
  it("records the state it ended in, not the click", () => {
    document.body.innerHTML = `
      <label for="limit">Limit registrations</label>
      <input type="checkbox" id="limit" />`;
    const recorded = harness();

    const box = document.getElementById("limit") as HTMLInputElement;
    box.click();
    recorded.detach();

    const recordedSteps = steps(recorded.events);
    expect(recordedSteps).toHaveLength(1);
    const step = checkOf(recordedSteps[0]);
    expect(step.action).toBe("check");
    expect(step.value).toEqual({ kind: "constant", value: true });
    expect(step.checked).toBe(true);
    // No click step: a click is what flipped it on replay.
    expect(recordedSteps.some((step) => step.action === "click")).toBe(false);
  });

  it("records the unticked state of a box that was ticked when the person arrived", () => {
    document.body.innerHTML = `
      <label for="published">Published</label>
      <input type="checkbox" id="published" checked />`;
    const recorded = harness();

    (document.getElementById("published") as HTMLInputElement).click();
    recorded.detach();

    const recordedSteps = steps(recorded.events);
    expect(recordedSteps).toHaveLength(1);
    const step = checkOf(recordedSteps[0]);
    expect(step.action).toBe("check");
    expect(step.value).toEqual({ kind: "constant", value: false });
  });

  it("records a switch built from a div and an ARIA role", () => {
    document.body.innerHTML = `
      <button type="button" role="switch" aria-checked="false" aria-label="Weekly digest">
        Weekly digest
      </button>`;
    const recorded = harness();

    const control = document.querySelector("[role='switch']") as HTMLElement;
    control.addEventListener("click", () => control.setAttribute("aria-checked", "true"));
    control.click();

    return new Promise<void>((resolve) => {
      setTimeout(() => {
        recorded.detach();
        const recordedSteps = steps(recorded.events);
        expect(recordedSteps).toHaveLength(1);
        const step = checkOf(recordedSteps[0]);
        expect(step.action).toBe("check");
        expect(step.value).toEqual({ kind: "constant", value: true });
        resolve();
      }, 5);
    });
  });
});

describe("a radio group (PL-114 items 2 and 6)", () => {
  it("records one select on the group, with the chosen option's label", () => {
    document.body.innerHTML = `
      <fieldset>
        <legend>Shipping</legend>
        <label><input type="radio" name="ship" value="standard" /> Standard</label>
        <label><input type="radio" name="ship" value="express" /> Express</label>
        <label><input type="radio" name="ship" value="overnight" /> Overnight</label>
      </fieldset>`;
    const recorded = harness();

    (document.querySelectorAll("input[type='radio']")[1] as HTMLInputElement).click();
    recorded.detach();

    const recordedSteps = steps(recorded.events);
    expect(recordedSteps).toHaveLength(1);
    const step = selectOf(recordedSteps[0]);
    expect(step.action).toBe("select");
    expect(step.value).toEqual({ kind: "constant", value: "Express" });
    // The target is the group, not the one option that was chosen while teaching.
    expect(step.targets.length).toBeGreaterThan(0);
    expect(recordedSteps.some((step) => step.action === "click")).toBe(false);
  });

  it("records a status bar of aria-checked buttons as a select with its own words", () => {
    document.body.innerHTML = `
      <div role="radiogroup" aria-label="Stage">
        <button type="button" role="radio" aria-checked="true">New</button>
        <button type="button" role="radio" aria-checked="false">Booked</button>
      </div>`;
    const recorded = harness();

    const booked = document.querySelectorAll("[role='radio']")[1] as HTMLElement;
    booked.addEventListener("click", () => {
      booked.setAttribute("aria-checked", "true");
      (document.querySelectorAll("[role='radio']")[0] as HTMLElement).setAttribute(
        "aria-checked",
        "false",
      );
    });
    booked.click();

    return new Promise<void>((resolve) => {
      setTimeout(() => {
        recorded.detach();
        const recordedSteps = steps(recorded.events);
        expect(recordedSteps).toHaveLength(1);
        const step = selectOf(recordedSteps[0]);
        expect(step.action).toBe("select");
        expect(step.value).toEqual({ kind: "constant", value: "Booked" });
        resolve();
      }, 5);
    });
  });
});
