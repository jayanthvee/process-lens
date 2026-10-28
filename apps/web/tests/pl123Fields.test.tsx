/** PL-123 item 5: a matched column says Matched, with no leftover question. */

import { describe, expect, it } from "vitest";

import { fieldReading } from "../src/lib/fields";

describe("the check step's Match column", () => {
  it("says Matched when the guessed column shares the field's own word", () => {
    expect(fieldReading("New Price", "close", "New Price", "Sales price", "799")).toEqual({
      label: "Matched",
      tone: "ok",
    });
  });

  it("still asks when the guess shares no word with the field", () => {
    const unsure = fieldReading("Contact info", "close", "Contact info", "E-mail", "a@b.test");
    expect(unsure.label).toBe("Which column has the email addresses?");
  });

  it("a person's own pick is Matched whatever the guess said", () => {
    expect(fieldReading("New Price", "none", "Sales price", "Sales price", "799")).toEqual({
      label: "Matched",
      tone: "ok",
    });
  });
});
