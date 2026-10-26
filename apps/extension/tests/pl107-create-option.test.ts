// PL-107 item 16: a Create "<value>" option follows the row's value, and an existing record is
// accepted first. The compiler orders the two rungs (exact value, then Create); this proves the
// resolver picks the right one at run time, on markup that is not Odoo's.
import { beforeEach, describe, expect, it } from "vitest";

import { TARGET_MARKER, resolveTargetInPage, type ResolveRung } from "../src/executor/ladder";

// What the compiler emits for a quick-create click, with the row's contact substituted in.
const RUNGS: ResolveRung[] = [
  { by: "role", role: "option", name: "Nadia Rahman" },
  { by: "role", role: "option", name: 'Create "Nadia Rahman"' },
];

function marked(token = "t1"): Element | null {
  return document.querySelector(`[${TARGET_MARKER}="${token}"]`);
}

beforeEach(() => {
  document.body.innerHTML = "";
});

describe("a create-option click follows the row's value", () => {
  it("accepts an existing record: the exact option is picked first", () => {
    document.body.innerHTML =
      "<ul role='listbox'>" +
      "<li role='option' id='existing'>Nadia Rahman</li>" +
      "<li role='option' id='create'>Create \"Nadia Rahman\"</li>" +
      "</ul>";

    const result = resolveTargetInPage({ targets: RUNGS, token: "t1" });

    expect(result.ok).toBe(true);
    expect(marked()?.id).toBe("existing");
    expect(result.attempts[0]).toMatchObject({ rung: 1, matches: 1 });
  });

  it("creates a new record: the Create option is picked when no exact option is there", () => {
    document.body.innerHTML =
      "<ul role='listbox'>" +
      "<li role='option' id='create'>Create \"Nadia Rahman\"</li>" +
      "</ul>";

    const result = resolveTargetInPage({ targets: RUNGS, token: "t1" });

    expect(result.ok).toBe(true);
    expect(marked()?.id).toBe("create");
    // The exact-value rung found nothing, and the Create rung then matched exactly one.
    expect(result.attempts[0]).toMatchObject({ matches: 0 });
    expect(result.attempts[1]).toMatchObject({ matches: 1 });
  });
});
