import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { resolve } from "node:path";
import { beforeEach, describe, expect, it } from "vitest";
import { attachRecorder } from "../src/content/recorder";
import type { PendingEvent } from "../src/shared/events";
import { resetStepIds } from "../src/shared/events";

// The recipe schema is the authority for what a step looks like. Every step the
// recorder emits is validated against it here, so a schema change and a recorder
// change can never drift apart silently.
//
// Paths are resolved from the package directory (the working directory when
// `npm test` runs), so this works regardless of how the test runner names modules.
const require = createRequire(resolve(process.cwd(), "package.json"));
const recipeSchemaPath = resolve(
  process.cwd(),
  "../../packages/recipe/schema/recipe.schema.json",
);
const recipeSchema = JSON.parse(readFileSync(recipeSchemaPath, "utf8")) as {
  $schema: string;
  $defs: Record<string, unknown>;
};

interface ValidateFn {
  (value: unknown): boolean;
  errors?: unknown;
}

const Ajv2020 = require("ajv/dist/2020") as new (options: object) => {
  compile: (schema: unknown) => ValidateFn;
};

const ajv = new Ajv2020({ strict: false, allErrors: true });
const validateStep = ajv.compile({
  $schema: recipeSchema.$schema,
  $id: "https://processlens.example/schema/recipe-step/v1",
  $defs: recipeSchema.$defs,
  $ref: "#/$defs/step",
});

function recordScriptedSession(): PendingEvent[] {
  const events: PendingEvent[] = [];
  const detach = attachRecorder(document, {
    send: (event) => events.push(event),
    isActive: () => true,
    url: () => "https://crm.example.com/leads",
    now: () => "2026-10-08T00:00:00.000Z",
  });

  document.body.innerHTML = `
    <form aria-label="New lead">
      <label for="email">Email</label>
      <input id="email" data-testid="lead-email" />
      <label for="interest">Interest</label>
      <select id="interest" data-testid="lead-interest">
        <option>Interest</option>
        <option>Data course</option>
      </select>
      <input id="agree" type="checkbox" aria-label="Subscribe" />
      <button id="save" data-testid="lead-save" type="submit">Save</button>
    </form>`;

  const email = document.getElementById("email") as HTMLInputElement;
  email.value = "ravi@example.com";
  email.dispatchEvent(new Event("input", { bubbles: true }));
  email.dispatchEvent(new Event("focusout", { bubbles: true }));

  const interest = document.getElementById("interest") as HTMLSelectElement;
  interest.value = "Data course";
  interest.dispatchEvent(new Event("change", { bubbles: true }));

  const agree = document.getElementById("agree") as HTMLInputElement;
  agree.checked = true;
  agree.dispatchEvent(new Event("change", { bubbles: true }));

  document.getElementById("save")!.dispatchEvent(new Event("click", { bubbles: true }));

  detach();
  return events;
}

beforeEach(() => {
  document.body.innerHTML = "";
  resetStepIds();
});

describe("recorded steps conform to the recipe schema", () => {
  it("emits at least one event for the scripted session", () => {
    expect(recordScriptedSession().length).toBeGreaterThan(0);
  });

  it("validates every emitted step against the schema", () => {
    const events = recordScriptedSession();
    events.forEach((event, index) => {
      expect(event.step, `event ${index} (${event.kind}) has no step`).not.toBeNull();
      const valid = validateStep(event.step);
      expect(valid, JSON.stringify(validateStep.errors)).toBe(true);
    });
  });

  it("covers click, fill, and select with the expected actions", () => {
    const kinds = recordScriptedSession().map((event) => event.kind);
    expect(kinds).toEqual(expect.arrayContaining(["click", "fill", "select"]));
  });

  it("never emits a commit step or an assertion of its own", () => {
    for (const event of recordScriptedSession()) {
      expect(event.step?.commit).toBeUndefined();
      expect(event.step?.assert_after).toBeUndefined();
    }
  });

  it("gives every step a unique id", () => {
    const ids = recordScriptedSession().map((event) => event.step?.id);
    expect(new Set(ids).size).toBe(ids.length);
  });
});
