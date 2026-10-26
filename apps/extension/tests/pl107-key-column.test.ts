/**
 * PL-107 item 14: which value identifies a row.
 *
 * The default used to be "the first email-named column, else nothing". It now prefers an
 * email-shaped value, then an id/number, and keys on a person's name only last, because two people
 * can share one.
 */
import { describe, expect, it } from "vitest";

import { defaultRecordKeyColumn } from "../src/popup/make-recipe";
import type { RecordedEvent } from "../src/shared/recipe-types";

let seq = 0;

function fill(name: string, value: string): RecordedEvent {
  seq += 1;
  return {
    seq,
    at: "2026-10-26T10:00:00Z",
    kind: "fill",
    url: "https://app.example.test/form",
    step: {
      id: `fill-${seq}`,
      action: "fill",
      targets: [{ by: "role", role: "textbox", name }],
      value: { kind: "constant", value },
      clear_first: true,
    },
  };
}

describe("the default key column", () => {
  it("prefers an email-shaped value over a person's name", () => {
    expect(
      defaultRecordKeyColumn([fill("Name", "Priya Nair"), fill("Email", "priya@example.com")]),
    ).toBe("email");
  });

  it("prefers an id/number over a person's name", () => {
    expect(
      defaultRecordKeyColumn([fill("Name", "Priya Nair"), fill("Reference", "INV-20431")]),
    ).toBe("reference");
  });

  it("keys on a person's name only when nothing better is offered", () => {
    expect(
      defaultRecordKeyColumn([fill("Name", "Priya Nair"), fill("Interest", "Data course")]),
    ).toBe("name");
  });

  it("offers no default when no value can identify a row", () => {
    expect(
      defaultRecordKeyColumn([fill("Interest", "Data course"), fill("Notes", "Called on Monday")]),
    ).toBeNull();
  });
});
