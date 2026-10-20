/**
 * PL-062: the edit model, as pure functions.
 *
 * An edit never changes a stored version; these functions only shape the document the page sends as
 * the task's next version. Each returns a new document or one refusal sentence.
 */

import { describe, expect, it } from "vitest";

import {
  diffSummary,
  flattenSteps,
  problemsInPlainWords,
  removeStep,
  renameStep,
  setSaveCheck,
  setValueSource,
  type RecipeDocument,
} from "../src/lib/editRecipe";

function base(): RecipeDocument {
  return {
    format_version: 1,
    meta: {
      name: "Add inquiries",
      when_to_use: "A spreadsheet of inquiries.",
      input_schema: {
        fields: {
          email: { type: "string", required: true },
          full_name: { type: "string" },
        },
      },
      record_key: "{{row.email | lower}}",
      idempotency_key: "{{row.email | lower}}",
      approval_policy: { commit: "first_record" },
      allowed_origins: ["https://crm.example.com"],
    },
    steps: [
      { id: "open", action: "navigate", url: "https://crm.example.com/leads/new" },
      {
        id: "type-email",
        action: "fill",
        name: "Email",
        targets: [{ by: "label", text: "Email" }],
        value: { kind: "input", column: "email" },
      },
      {
        id: "save-lead",
        action: "click",
        name: "Save",
        commit: true,
        targets: [{ by: "role", role: "button", name: "Save" }],
        assert_after: { text_visible: "Lead created", timeout_ms: 8000 },
      },
      { id: "done", action: "end_record", outcome: "verified", reason: "Added." },
    ],
  };
}

function docOf(result: { ok: true; document: RecipeDocument } | { ok: false; refusal: string }) {
  if (!result.ok) throw new Error(`refused: ${result.refusal}`);
  return result.document;
}

describe("renameStep", () => {
  it("gives the step a new name and leaves everything else alone", () => {
    const before = base();
    const after = docOf(renameStep(before, "type-email", "Work email"));

    expect(flattenSteps(after)[1]?.step.name).toBe("Work email");
    expect(flattenSteps(after)[0]).toEqual(flattenSteps(before)[0]);
    // A new document, not the one that was passed in.
    expect(after).not.toBe(before);
    expect(flattenSteps(before)[1]?.step.name).toBe("Email");
  });

  it("refuses an empty name", () => {
    const result = renameStep(base(), "type-email", "   ");
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.refusal).toContain("name");
  });

  it("refuses a name longer than 120 characters", () => {
    const result = renameStep(base(), "type-email", "x".repeat(121));
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.refusal).toContain("120");
  });

  it("refuses a step that is not there", () => {
    const result = renameStep(base(), "no-such-step", "Anything");
    expect(result.ok).toBe(false);
  });
});

describe("removeStep", () => {
  it("removes a step the document does not need", () => {
    const after = docOf(removeStep(base(), "open"));
    const ids = flattenSteps(after).map((entry) => entry.step.id);
    expect(ids).toEqual(["type-email", "save-lead", "done"]);
  });

  it("refuses the step that saves", () => {
    const result = removeStep(base(), "save-lead");
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.refusal).toBe("This is the step that saves; it can't be removed.");
    }
  });

  it("refuses the last step", () => {
    const document = base();
    document.steps = [{ id: "only", action: "end_record", outcome: "verified", reason: "x" }];
    const result = removeStep(document, "only");
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.refusal).toContain("last step");
  });

  it("refuses a step another step names", () => {
    const document = base();
    (document.steps as Record<string, unknown>[]).push({
      id: "ask",
      action: "approval_gate",
      reason: "Check it first.",
      goto: "type-email",
    });
    const result = removeStep(document, "type-email");
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.refusal).toContain("uses this one");
  });

  it("removes a step from inside a branch, and the branch keeps the rest", () => {
    const document = base();
    (document.steps as Record<string, unknown>[]).splice(3, 0, {
      id: "already",
      action: "branch",
      when: { text_visible: "No results" },
      then: [
        { id: "skip-it", action: "end_record", outcome: "skipped", reason: "There already." },
        { id: "stop-it", action: "end_record", outcome: "failed", reason: "Stopped." },
      ],
    });
    const after = docOf(removeStep(document, "skip-it"));
    const branch = flattenSteps(after).find((entry) => entry.step.id === "already");
    const then = branch?.step.then as Record<string, unknown>[];
    expect(then.map((step) => step.id)).toEqual(["stop-it"]);
  });
});

describe("setValueSource", () => {
  it("points a fill at a spreadsheet column", () => {
    const after = docOf(
      setValueSource(base(), "type-email", { kind: "input", column: "full_name" }),
    );
    expect(flattenSteps(after)[1]?.step.value).toEqual({ kind: "input", column: "full_name" });
  });

  it("points a fill at fixed text", () => {
    const after = docOf(setValueSource(base(), "type-email", { kind: "constant", value: "Acme" }));
    expect(flattenSteps(after)[1]?.step.value).toEqual({ kind: "constant", value: "Acme" });
  });

  it("refuses a column the spreadsheet does not have", () => {
    const result = setValueSource(base(), "type-email", { kind: "input", column: "phone" });
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.refusal).toContain("column");
  });

  it("refuses empty fixed text", () => {
    const result = setValueSource(base(), "type-email", { kind: "constant", value: "  " });
    expect(result.ok).toBe(false);
  });

  it("refuses a step that types or chooses nothing", () => {
    const result = setValueSource(base(), "open", { kind: "input", column: "email" });
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.refusal).toContain("types");
  });

  it("never offers AI text", () => {
    const result = setValueSource(base(), "type-email", {
      kind: "ai_text",
    } as unknown as { kind: "input"; column: string });
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.refusal).toContain("AI");
  });
});

describe("setSaveCheck", () => {
  it("checks the text the page shows, and keeps the timeout", () => {
    const after = docOf(
      setSaveCheck(base(), "save-lead", { kind: "text_visible", text: "Contact saved" }),
    );
    expect(flattenSteps(after)[2]?.step.assert_after).toEqual({
      text_visible: "Contact saved",
      timeout_ms: 8000,
    });
  });

  it("checks the address, with the path escaped into a pattern", () => {
    const after = docOf(setSaveCheck(base(), "save-lead", { kind: "url_matches", path: "/leads/9" }));
    expect(flattenSteps(after)[2]?.step.assert_after).toEqual({
      url_matches: "/leads/9$",
      timeout_ms: 8000,
    });
  });

  it("escapes a path's own punctuation", () => {
    const after = docOf(
      setSaveCheck(base(), "save-lead", { kind: "url_matches", path: "/leads?ok=1" }),
    );
    const check = flattenSteps(after)[2]?.step.assert_after as { url_matches: string };
    expect(check.url_matches).toBe("/leads\\?ok=1$");
  });

  it("keeps what was recorded", () => {
    const before = base();
    const after = docOf(setSaveCheck(before, "save-lead", { kind: "keep" }));
    expect(flattenSteps(after)[2]?.step.assert_after).toEqual({
      text_visible: "Lead created",
      timeout_ms: 8000,
    });
  });

  it("refuses empty text and an empty address", () => {
    expect(setSaveCheck(base(), "save-lead", { kind: "text_visible", text: " " }).ok).toBe(false);
    expect(setSaveCheck(base(), "save-lead", { kind: "url_matches", path: " " }).ok).toBe(false);
  });

  it("refuses a step that does not save", () => {
    const result = setSaveCheck(base(), "type-email", { kind: "text_visible", text: "Done" });
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.refusal).toContain("saves");
  });

  it("keeps every commit step's check after a rename and a value change", () => {
    let document = docOf(renameStep(base(), "type-email", "Work email"));
    document = docOf(setValueSource(document, "type-email", { kind: "input", column: "full_name" }));
    const commits = flattenSteps(document).filter((entry) => entry.step.commit === true);
    expect(commits.length).toBe(1);
    expect(commits[0]?.step.assert_after).toBeDefined();
  });
});

describe("diffSummary", () => {
  it("names a rename by the step's place", () => {
    const after = docOf(renameStep(base(), "type-email", "Work email"));
    expect(diffSummary(base(), after)).toBe("Renamed step 2.");
  });

  it("names a removal", () => {
    const after = docOf(removeStep(base(), "open"));
    expect(diffSummary(base(), after)).toBe("Removed step 1.");
  });

  it("names a new value source", () => {
    const after = docOf(setValueSource(base(), "type-email", { kind: "input", column: "full_name" }));
    expect(diffSummary(base(), after)).toBe('Step 2 now comes from the "full_name" column.');
  });

  it("names a fixed value", () => {
    const after = docOf(setValueSource(base(), "type-email", { kind: "constant", value: "Acme" }));
    expect(diffSummary(base(), after)).toBe('Step 2 always says "Acme".');
  });

  it("names a new save check, after the rename", () => {
    let after = docOf(renameStep(base(), "type-email", "Work email"));
    after = docOf(setSaveCheck(after, "save-lead", { kind: "url_matches", path: "/leads/9" }));
    expect(diffSummary(base(), after)).toBe(
      "Renamed step 2; the save is now checked by the address.",
    );
  });

  it("names the page text check", () => {
    const after = docOf(
      setSaveCheck(base(), "save-lead", { kind: "text_visible", text: "Contact saved" }),
    );
    expect(diffSummary(base(), after)).toBe("The save is now checked by the text on the page.");
  });

  it("says so when nothing changed", () => {
    expect(diffSummary(base(), base())).toBe("The steps are unchanged.");
  });
});

describe("problemsInPlainWords", () => {
  it("turns the API's problems into sentences a person can act on", () => {
    const problems = problemsInPlainWords([
      "steps[2] (save-lead): a commit step must carry an assert_after",
      "steps[1]: value is required",
    ]);
    expect(problems).toEqual([
      "The step that saves has to say how we know it saved.",
      "Something the steps need is missing.",
    ]);
  });

  it("never repeats the same sentence twice", () => {
    const problems = problemsInPlainWords([
      "steps[2]: a commit step must carry an assert_after",
      "steps[3]: a commit step must carry an assert_after",
    ]);
    expect(problems).toEqual(["The step that saves has to say how we know it saved."]);
  });

  it("has one plain sentence when the API says nothing useful", () => {
    expect(problemsInPlainWords([])).toEqual([
      "ProcessLens couldn't use these steps as they are.",
    ]);
  });
});
