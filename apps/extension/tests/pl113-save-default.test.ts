// PL-113 item 2: a tab, a breadcrumb or a menu item is never "the click that saves".
//
// The owner never clicked Save (the app saves by itself when the form is left), so the panel took
// the last click -- the Sales tab -- as "The Sales click saves your work". Its inferred check (the
// tab is there) is true on every page, so every row passed.
import { describe, expect, it } from "vitest";

import { commitChoices, defaultCommitStepId } from "../src/popup/make-recipe";
import type { RecordedEvent } from "../src/shared/recipe-types";

const PAGE = "https://app.example.test/products/1";

function click(id: string, role: string, name: string): RecordedEvent {
  return {
    seq: Number(id.replace(/\D/g, "")) || 1,
    at: "2026-10-27T10:00:00Z",
    kind: "click",
    url: PAGE,
    step: { id, action: "click", targets: [{ by: "role", role, name }] },
  } as RecordedEvent;
}

const FILL: RecordedEvent = {
  seq: 3,
  at: "2026-10-27T10:00:02Z",
  kind: "fill",
  url: PAGE,
  step: {
    id: "fill-3",
    action: "fill",
    targets: [{ by: "label", text: "Sales price" }],
    value: { kind: "constant", value: "799" },
  },
} as RecordedEvent;

const TAB = click("click-1", "tab", "Sales");
const SAVE = click("click-2", "button", "Save");

describe("which click saves the work (PL-113 item 2)", () => {
  it("is not a tab, even when the tab is the last click", () => {
    const events = [FILL, SAVE, TAB];

    expect(defaultCommitStepId(events)).toBe("click-2");
    expect(commitChoices(events).map((choice) => choice.id)).toEqual(["click-2"]);
  });

  it("is nothing at all when every click only moves around the page", () => {
    const menu = click("click-9", "menuitem", "Products");
    const link = click("click-8", "link", "Home");

    expect(defaultCommitStepId([FILL, TAB, menu, link])).toBeNull();
    expect(commitChoices([FILL, TAB, menu, link])).toEqual([]);
  });

  it("is still the last real click", () => {
    expect(defaultCommitStepId([TAB, SAVE])).toBe("click-2");
  });
});
