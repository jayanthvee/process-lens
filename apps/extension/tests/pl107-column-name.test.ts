// PL-107 item 13: a column is not named after a field's example hint.
//
// Odoo's lead name field has no label; its placeholder is "e.g. Product Pricing". The task asked
// for a column called "E g product pricing" and matched nothing in a file whose column is "Full
// Name". When the only name a field offers is an example, the column falls back to the field's own
// id/name, then to "Name" for a text field.
import { describe, expect, it } from "vitest";

import { recordedValues } from "../src/popup/make-recipe";
import type { RecordedEvent, RecordedStep } from "../src/shared/recipe-types";

function fill(id: string, targets: RecordedStep["targets"], value: string): RecordedEvent {
  return {
    seq: 1,
    at: "2026-10-26T10:00:00Z",
    kind: "fill",
    url: "https://app.example.test/records/new",
    step: { id, action: "fill", targets, value: { kind: "constant", value }, clear_first: true },
  };
}

describe("a field named only by an example placeholder", () => {
  it("is not called 'e g product pricing'; it uses the field's own id", () => {
    const events = [
      fill(
        "fill-1",
        [
          { by: "role", role: "textbox", name: "e.g. Product Pricing" },
          { by: "css", selector: "#name_0" },
          { by: "text_fuzzy", value: "e.g. Product Pricing" },
        ],
        "Acme Deal",
      ),
    ];

    const values = recordedValues(events);

    expect(values[0]?.column).not.toContain("product_pricing");
    expect(values[0]?.label ?? "").not.toMatch(/e\s*[.]?\s*g/i);
    expect(values[0]?.label).toBe("Name");
  });

  it("falls back to 'Name' when it has no id or name to use", () => {
    const events = [
      fill(
        "fill-1",
        [
          { by: "role", role: "textbox", name: "Example: Full Name" },
          { by: "text_fuzzy", value: "Example: Full Name" },
        ],
        "Priya Nair",
      ),
    ];

    expect(recordedValues(events)[0]?.label).toBe("Name");
  });

  it("keeps a real label even when the placeholder is an example", () => {
    const events = [
      fill(
        "fill-1",
        [
          { by: "role", role: "textbox", name: "Company" },
          { by: "label", text: "Company" },
          { by: "css", selector: "#company_0" },
          { by: "text_fuzzy", value: "e.g. Acme Inc" },
        ],
        "Acme",
      ),
    ];

    expect(recordedValues(events)[0]?.label).toBe("Company");
  });
});
