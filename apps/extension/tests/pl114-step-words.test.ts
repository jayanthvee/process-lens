// PL-114: the step list says the state a clicked field was left in, in a person's words.
//
// A check reads "Ticked Limit registrations", never "Clicked the checkbox": the person did not
// click a box on every row, they set it. A group reads "Chose Goods for Product type", so the
// choice and its field are both on the line.
import { describe, expect, it } from "vitest";

import { stepSentence } from "../src/popup/words";
import type { CheckStep, SelectStep } from "../src/shared/recipe-types";

function checkStep(checked: boolean, name = "Limit registrations"): CheckStep {
  return {
    id: "check-1",
    action: "check",
    name,
    targets: [{ by: "role", role: "checkbox", name }],
    value: { kind: "constant", value: checked },
    checked,
  };
}

describe("how a clicked field reads (PL-114)", () => {
  it("says a box was ticked, or unticked", () => {
    expect(stepSentence(checkStep(true))).toBe("Ticked Limit registrations");
    expect(stepSentence(checkStep(false))).toBe("Unticked Limit registrations");
  });

  it("names the field and the choice for a group", () => {
    const group: SelectStep = {
      id: "select-1",
      action: "select",
      name: "Product type",
      targets: [{ by: "role", role: "radiogroup", name: "Product type" }],
      value: { kind: "constant", value: "Goods" },
    };

    expect(stepSentence(group)).toBe("Chose Goods for Product type");
  });

  it("still says only the choice when the field has no other name", () => {
    const group: SelectStep = {
      id: "select-2",
      action: "select",
      name: "Express",
      targets: [{ by: "role", role: "radiogroup", name: "Express" }],
      value: { kind: "constant", value: "Express" },
    };

    expect(stepSentence(group)).toBe("Chose Express");
  });
});
