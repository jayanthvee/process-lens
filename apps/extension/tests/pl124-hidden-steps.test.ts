// PL-124 item 1: which recorded steps the list hides after Stop is the code's rule, not the
// model's. The owner's recording: Odoo filled the product's fields when the card opened (page_set),
// and the owner typed 3200 over the sales price. The model kept the page's 400 and hid the 3200.
import { describe, expect, it } from "vitest";

import {
  hiddenSeqs,
  recordedValues,
  type CleanOutcome,
} from "../src/popup/make-recipe";
import type { RecordedEvent } from "../src/shared/recipe-types";

const PRICE = [{ by: "label", text: "Sales Price" }];

function fill(
  seq: number,
  id: string,
  name: string,
  value: string,
  extra: Partial<RecordedEvent> = {},
): RecordedEvent {
  return {
    seq,
    kind: "fill",
    url: "http://localhost:8069/odoo/products/1",
    at: "2026-10-29T05:36:00Z",
    step: {
      id,
      action: "fill",
      name,
      targets: name === "Fill Sales Price" ? PRICE : [{ by: "label", text: name }],
      value: { kind: "constant", value },
    },
    ...extra,
  } as RecordedEvent;
}

function click(seq: number, id: string, name: string): RecordedEvent {
  return {
    seq,
    kind: "click",
    url: "http://localhost:8069/odoo/products",
    at: "2026-10-29T05:36:00Z",
    step: { id, action: "click", name, targets: [{ by: "role", role: "button", name }] },
  } as RecordedEvent;
}

const OWNER: RecordedEvent[] = [
  fill(1, "fill-search", "Fill Search...", "Oak Standing Desk", { keystrokes: 17 }),
  click(2, "click-menu", "Click All Apps"),
  fill(3, "fill-cost", "Fill Cost", "455.00", { page_set: true, keystrokes: 0 }),
  fill(4, "fill-ref", "Fill Reference", "PL-DESK-1001", { page_set: true, keystrokes: 0 }),
  fill(5, "fill-price-page", "Fill Sales Price", "400.00", { page_set: true, keystrokes: 0 }),
  fill(6, "fill-price-typed", "Fill Sales Price", "3200", { keystrokes: 4 }),
];

// The answer that lost the owner's price: keep the page's 400, drop the typed 3200 and the menu.
const WRONG_ANSWER: CleanOutcome = {
  ok: true,
  available: true,
  steps: [
    { id: "fill-search", keep: true, intent: "search for the product" },
    { id: "click-menu", keep: false, intent: "" },
    { id: "fill-cost", keep: true, intent: "type the cost" },
    { id: "fill-ref", keep: true, intent: "type the reference" },
    { id: "fill-price-page", keep: true, intent: "type the sales price" },
    { id: "fill-price-typed", keep: false, intent: "" },
  ],
};

describe("PL-124: the list after Stop", () => {
  it("never hides a value the person typed, whatever the model answered", () => {
    const hidden = hiddenSeqs(OWNER, WRONG_ANSWER, new Set());

    expect(hidden.has(6)).toBe(false);
    expect(hidden.has(1)).toBe(false);
  });

  it("hides every value the page set by itself, whatever the model answered", () => {
    const hidden = hiddenSeqs(OWNER, WRONG_ANSWER, new Set());

    expect([3, 4, 5].every((seq) => hidden.has(seq))).toBe(true);
  });

  it("lets the model hide a stray click", () => {
    expect(hiddenSeqs(OWNER, WRONG_ANSWER, new Set()).has(2)).toBe(true);
  });

  it("offers the typed 3200 as the sales price column", () => {
    const hidden = hiddenSeqs(OWNER, WRONG_ANSWER, new Set());
    const visible = OWNER.filter((event) => !hidden.has(event.seq));
    const values = recordedValues(visible);

    const prices = values.filter((value) => value.label.toLowerCase().includes("sales price"));
    expect(prices.map((value) => value.value)).toEqual(["3200"]);
  });

  it("hides the page's values even when no model answered", () => {
    const none: CleanOutcome = { ok: false, problems: ["Could not reach ProcessLens."] };
    const hidden = hiddenSeqs(OWNER, none, new Set());

    expect([...hidden].sort()).toEqual([3, 4, 5]);
  });

  it("hides nothing before Stop, and keeps a step the person brought back", () => {
    expect(hiddenSeqs(OWNER, null, new Set()).size).toBe(0);
    expect(hiddenSeqs(OWNER, WRONG_ANSWER, new Set([5])).has(5)).toBe(false);
  });
});
