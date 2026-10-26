// PL-109 item 2: field names a person recognises, and never two the same.
//
// Two fields recorded as "Contact" are told apart by what was typed in them -- "Contact (ben
// baller)" and "Contact (ben baller's opportunity)" -- never by "Contact 2".
import { describe, expect, it } from "vitest";

import { recordedValues } from "../src/popup/make-recipe";
import type { RecordedEvent, RecordedStep } from "../src/shared/recipe-types";

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
    } satisfies RecordedStep,
  };
}

describe("two fields with the same name", () => {
  it("are told apart by the value taught in each", () => {
    const values = recordedValues([
      fill("Contact", "ben baller"),
      fill("Contact", "ben baller's opportunity"),
    ]);

    expect(values.map((value) => value.label)).toEqual([
      "Contact (ben baller)",
      "Contact (ben baller's opportunity)",
    ]);
    // The machine columns follow the names and are still distinct.
    expect(new Set(values.map((value) => value.column)).size).toBe(2);
  });

  it("never fall back to a bare '2'", () => {
    const values = recordedValues([fill("Contact", "Ana"), fill("Contact", "Ben")]);

    for (const value of values) {
      expect(value.label).not.toMatch(/contact\s*2\b/i);
      expect(value.label).toContain("Contact (");
    }
  });

  it("keep one plain name when there is no duplicate", () => {
    const values = recordedValues([fill("Contact", "ben baller"), fill("Email", "a@b.test")]);

    expect(values.map((value) => value.label)).toEqual(["Contact", "Email"]);
  });
});
