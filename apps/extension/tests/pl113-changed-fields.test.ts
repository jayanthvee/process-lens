// PL-113 item 3: a recording holds the fields the person changed, and nothing else.
//
// The owner's recording held a fill for every field on the form -- service, cost, category,
// reference, sales price -- though they changed one. A checkbox came through as "Typed … "on"".
// These are the three rules from the ticket, on ordinary markup.
import { beforeEach, describe, expect, it } from "vitest";

import { attachRecorder } from "../src/content/recorder";
import type { PendingEvent } from "../src/shared/events";
import { resetStepIds } from "../src/shared/events";

const PAGE = "https://app.example.test/products/1";

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

function type(field: HTMLInputElement, value: string): void {
  field.focus();
  field.value = value;
  field.dispatchEvent(new Event("input", { bubbles: true }));
  field.dispatchEvent(new Event("change", { bubbles: true }));
  field.dispatchEvent(new Event("focusout", { bubbles: true }));
}

/** A ten-field product form, all of it already filled by the page when the person arrives. */
function productForm(): void {
  const fields = Array.from(
    { length: 10 },
    (_, index) =>
      `<label for="f${index}">Field ${index}</label><input id="f${index}" value="page-${index}" />`,
  ).join("");
  document.body.innerHTML = `<form id="product">${fields}
    <label><input type="checkbox" id="published" /> Published</label>
    <button type="submit">Save</button></form>`;
}

function fills(events: PendingEvent[]): PendingEvent[] {
  return events.filter((event) => event.kind === "fill");
}

/** The literal a fill recorded, read off the event in the shape the compiler receives. */
function filledValue(event: PendingEvent | undefined): string | undefined {
  const step = event?.step as { value?: { value?: string } } | null | undefined;
  return step?.value?.value;
}

beforeEach(() => {
  document.body.innerHTML = "";
  resetStepIds();
});

describe("only the fields the person changed (PL-113 item 3)", () => {
  it("records one fill when one of ten fields is changed", () => {
    productForm();
    const recorded = harness();

    // Tab through two fields without typing, then change the third.
    (document.getElementById("f0") as HTMLInputElement).focus();
    (document.getElementById("f1") as HTMLInputElement).focus();
    const price = document.getElementById("f2") as HTMLInputElement;
    type(price, "799");
    (document.getElementById("published") as HTMLInputElement).focus();
    recorded.detach();

    const recorded_fills = fills(recorded.events);
    expect(recorded_fills).toHaveLength(1);
    expect(filledValue(recorded_fills[0])).toBe("799");
  });

  it("records a checkbox as a click, never as Typed … \"on\"", () => {
    productForm();
    const recorded = harness();

    const published = document.getElementById("published") as HTMLInputElement;
    published.focus();
    published.click();
    recorded.detach();

    const text = JSON.stringify(recorded.events);
    expect(text).not.toContain('"on"');
    expect(fills(recorded.events)).toEqual([]);
    // PL-114 item 1: the state it ended in, not a click that would flip it on replay.
    expect(
      recorded.events.some(
        (event) => event.step?.action === "check" && event.step.value !== undefined,
      ),
    ).toBe(true);
  });

  it("records nothing for a field the page filled by itself", () => {
    productForm();
    const recorded = harness();

    // The page writes a value into a field with no keystrokes from the person.
    const cost = document.getElementById("f7") as HTMLInputElement;
    cost.value = "455.00";
    cost.dispatchEvent(new Event("input", { bubbles: true }));
    const other = document.getElementById("f3") as HTMLInputElement;
    type(other, "changed");
    recorded.detach();

    const recorded_fills = fills(recorded.events);
    expect(recorded_fills).toHaveLength(1);
    expect(filledValue(recorded_fills[0])).toBe("changed");
  });
});
