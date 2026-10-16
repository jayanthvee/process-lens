/** The step sentences: one sentence per action, never a selector, never an id (plan §5). */

import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

import { stepWords, stepWordsList } from "../src/lib/steps";

const EXAMPLE = "../../packages/recipe/examples/create-lead.recipe.json";

interface ExampleDocument {
  steps: unknown[];
}

function exampleDocument(): ExampleDocument {
  return JSON.parse(readFileSync(EXAMPLE, "utf8")) as ExampleDocument;
}

describe("a real recorded task", () => {
  it("turns every step of the example recipe into one plain sentence", () => {
    const document = exampleDocument();
    const sentences = stepWordsList(document.steps).map((step) => step.title);

    // Sixteen top-level steps, and the branch carries one of its own.
    expect(sentences).toHaveLength(17);
    expect(sentences[0]).toBe("Open the customer list");
    expect(sentences).toContain("Type the email");
    expect(sentences).toContain("Choose the interest");
    expect(sentences).toContain("Click Save");
    expect(sentences).toContain("Approve the first commit");
    expect(sentences).toContain("Finish this row");
  });

  it("never lets an engine word, a step id, a test id or a selector reach the screen", () => {
    const document = exampleDocument();
    const sentences = stepWordsList(document.steps).map((step) => step.title);
    for (const sentence of sentences) {
      expect(sentence).not.toMatch(/[#{}]|\[/);
      expect(sentence.toLowerCase()).not.toContain("lead-full-name");
      expect(sentence.toLowerCase()).not.toContain("customer-search");
      expect(sentence.toLowerCase()).not.toContain("step_");
      expect(sentence.trim()).not.toBe("");
    }
  });
});

describe("a step without a name", () => {
  it("opens the site the step navigates to", () => {
    const step = { action: "navigate", url: "https://crm.example.com/new" };
    expect(stepWords(step).title).toBe("Open crm.example.com");
  });

  it("reads the field's accessible name rather than its selector", () => {
    const step = {
      action: "fill",
      targets: [{ by: "role", role: "textbox", name: "Email address" }],
    };
    expect(stepWords(step).title).toBe("Type the email address");
  });

  it("says only 'the field' when the ladder has nothing a person wrote", () => {
    const step = {
      action: "fill",
      targets: [{ by: "css", selector: "#lead-email" }],
      value: { kind: "input", column: "email" },
    };
    const sentence = stepWords(step).title;
    expect(sentence).toBe("Type the field");
    expect(sentence).not.toContain("#");
    expect(sentence).not.toContain("email");
  });

  it("never shows a test id", () => {
    const step = { action: "click", targets: [{ by: "testid", value: "lead-save" }] };
    expect(stepWords(step).title).toBe("Click the button");
  });

  it("presses the key the step names", () => {
    const step = {
      action: "press",
      key: "Enter",
      targets: [{ by: "role", role: "button", name: "Search" }],
    };
    expect(stepWords(step).title).toBe("Press Enter");
  });

  it("says what each remaining action does", () => {
    expect(stepWords({ action: "wait_for" }).title).toBe("Wait for the page");
    expect(stepWords({ action: "assert" }).title).toBe("Check the result");
    expect(stepWords({ action: "upload_file" }).title).toBe("Attach the file");
    expect(stepWords({ action: "read_value" }).title).toBe("Read the value from the page");
    expect(stepWords({ action: "download" }).title).toBe("Download the file");
    expect(stepWords({ action: "extract" }).title).toBe("Read the details from the document");
    expect(stepWords({ action: "classify" }).title).toBe("Choose the best answer");
    expect(stepWords({ action: "approval_gate" }).title).toBe("Wait for your approval");
    expect(stepWords({ action: "transform", op: "title_case" }).title).toBe("Work out the title case");
    expect(stepWords({ action: "match", compare: "amount_within" }).title).toBe(
      "Check the two amounts match",
    );
    expect(stepWords({ action: "aggregate", op: "sum" }).title).toBe("Add the values up");
    expect(stepWords({ action: "branch", when: { url_matches: "/leads" } }).title).toBe(
      "If the page has the right address, do the steps inside",
    );
    expect(stepWords({ action: "for_each", as: "line_item" }).title).toBe(
      "For each line item, do the steps inside",
    );
  });

  it("finishes a row the way the row ended", () => {
    expect(stepWords({ action: "end_record", outcome: "verified" }).title).toBe("Finish this row");
    expect(stepWords({ action: "end_record", outcome: "skipped" }).title).toBe(
      "Mark this row as already there",
    );
  });

  it("still says something for a step it has never seen", () => {
    expect(stepWords({ action: "something_new" }).title).toBe("Do the next thing");
  });
});

describe("a step's own name wins", () => {
  it("uses the name the recipe author wrote", () => {
    const step = {
      action: "wait_for",
      name: "The search box is usable",
      targets: [{ by: "role", role: "textbox", name: "Search customers" }],
    };
    expect(stepWords(step).title).toBe("The search box is usable");
  });

  it("shows a value that is always typed, and nothing when it is a template", () => {
    const withConstant = stepWords({
      action: "select",
      name: "Interest",
      value: { kind: "constant", value: "New enquiry" },
    });
    expect(withConstant.detail).toBe("Always “New enquiry”");
    const withTemplate = stepWords({
      action: "select",
      name: "Interest",
      value: { kind: "constant", value: "{{interest_option}}" },
    });
    expect(withTemplate.detail).toBeUndefined();
  });
});
