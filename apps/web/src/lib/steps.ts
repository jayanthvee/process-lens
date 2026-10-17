/**
 * The one place a step becomes one sentence (plan §5, DESIGN.md §2).
 *
 * A person never sees a step id, a target selector, a test id, a variable name or a `record key`.
 * A step's own `name` always wins when it has one; otherwise the sentence is built from the action
 * and the first rung of its target ladder (the accessible name), which is the only rung that holds
 * words a person wrote. The CSS rung and the test-id rung are never read at all.
 *
 * `stepWords` is a pure function so `steps.test.ts` can call it directly.
 */

import type { StepWords } from "../types";

/** One rung of a step's target ladder (plan §5: `$defs/targets`). */
interface TargetRung {
  by?: unknown;
  role?: unknown;
  name?: unknown;
  text?: unknown;
}

/**
 * The parts of a recipe step this module reads. Everything is optional and guarded, because the
 * document comes from the API and a page must never assume a field is there.
 */
export interface RecipeStep {
  action?: unknown;
  name?: unknown;
  url?: unknown;
  key?: unknown;
  value?: unknown;
  targets?: unknown;
  condition?: unknown;
  when?: unknown;
  outcome?: unknown;
  op?: unknown;
  compare?: unknown;
  as?: unknown;
}

function text(value: unknown): string | undefined {
  if (typeof value !== "string") return undefined;
  const trimmed = value.trim();
  return trimmed === "" ? undefined : trimmed;
}

/** A template is not for a person's eyes, so a sentence never falls back to one. */
function isTemplate(value: string): boolean {
  return value.includes("{{");
}

/**
 * A step name is a name, not a sentence: longer than this, the step shows its action and its first
 * label instead (PL-033 requirement 5).
 */
const NAME_LIMIT = 40;

/** The step's own name, when the recipe author gave it one. */
function stepName(step: RecipeStep): string | undefined {
  return text(step.name);
}

/**
 * The recorder writes a step's name as "fill Contact" or "click Add" — action word first. The
 * sentence already says what the action is ("Type the contact", "Click Add"), so the prefix is
 * dropped; a name that does not start with the step's own action is left alone.
 */
function plainName(action: string, name: string): string {
  const clean = name.trim();
  const prefix = `${action.toLowerCase()} `;
  return clean.toLowerCase().startsWith(prefix) ? clean.slice(prefix.length).trim() : clean;
}

/**
 * The name a sentence may use: the step's own name, without the recorder's action prefix, and only
 * while it is short enough to be a name at all. A longer name falls back to the action and the
 * target's first label, which the sentence builder below already turns into one readable line.
 */
function usableName(action: string, step: RecipeStep): string | undefined {
  const raw = stepName(step);
  if (raw === undefined || raw.length > NAME_LIMIT) return undefined;
  const stripped = plainName(action, raw);
  return stripped === "" ? undefined : stripped;
}

/**
 * The first rung of the target ladder, and only the words in it: the accessible name of the role
 * rung or the label rung. The test id and the CSS selector are ignored on purpose.
 */
function accessibleName(step: RecipeStep): string | undefined {
  if (!Array.isArray(step.targets)) return undefined;
  const rung = step.targets[0] as TargetRung | undefined;
  if (rung === undefined || typeof rung !== "object") return undefined;
  const by = text(rung.by);
  if (by === "role") return text(rung.name);
  if (by === "label") return text(rung.text);
  return undefined;
}

/** "title_case" -> "title case", so a closed-vocabulary word is still readable. */
function words(token: string): string {
  return token.replace(/[_-]+/g, " ").replace(/\s+/g, " ").trim().toLowerCase();
}

function lowerFirst(value: string): string {
  if (value === "") return value;
  return value.charAt(0).toLowerCase() + value.slice(1);
}

function upperFirst(value: string): string {
  if (value === "") return value;
  return value.charAt(0).toUpperCase() + value.slice(1);
}

/** "Email" -> "the email"; a phrase that already carries its own article is left alone. */
function definite(noun: string): string {
  const clean = noun.trim();
  if (/^(the|a|an)\s/i.test(clean)) return lowerFirst(clean);
  return `the ${lowerFirst(clean)}`;
}

function startsWithVerb(value: string, verb: string): boolean {
  return value.toLowerCase().startsWith(`${verb.toLowerCase()} `);
}

/** `verb + object`, unless the object already begins with that verb. */
function phrase(verb: string, object: string): string {
  const clean = object.trim();
  if (startsWithVerb(clean, verb)) return upperFirst(clean);
  return `${verb} ${clean}`;
}

/** The site a navigate step opens: the URL's host, never the whole URL and never a template. */
function hostOf(url: string): string | undefined {
  if (isTemplate(url)) return undefined;
  try {
    const parsed = new URL(url);
    return parsed.host === "" ? undefined : parsed.host;
  } catch {
    return undefined;
  }
}

/** A condition, in words a person would use. Never a selector, never a template. */
function conditionWords(condition: unknown): string {
  if (condition === null || typeof condition !== "object") return "the page is ready";
  const keys = Object.keys(condition as Record<string, unknown>);
  if (keys.includes("text_visible")) return "the page shows what we expect";
  if (keys.includes("text_absent")) return "the old text is gone";
  if (keys.includes("url_matches")) return "the page has the right address";
  if (keys.includes("element_present")) return "the page is ready";
  if (keys.includes("element_absent")) return "the page has moved on";
  if (keys.includes("value_equals")) return "the field holds the right value";
  return "the page is ready";
}

const END_RECORD: Record<string, string> = {
  verified: "Finish this row",
  skipped: "Mark this row as already there",
  parked: "Pause this row and ask for help",
  failed: "Stop this row",
};

const COMPARE: Record<string, string> = {
  equals: "Check the two values match",
  contains: "Check one value contains the other",
  amount_within: "Check the two amounts match",
};

const AGGREGATE: Record<string, string> = {
  sum: "Add the values up",
  count: "Count the values",
  min: "Find the smallest value",
  max: "Find the largest value",
  mean: "Work out the average",
};

/** One step, in one plain sentence. */
export function stepWords(step: RecipeStep): StepWords {
  const action = text(step.action) ?? "";
  const name = usableName(action, step);
  const target = accessibleName(step);
  const object = name ?? target;

  switch (action) {
    case "navigate": {
      const url = text(step.url);
      const host = url === undefined ? undefined : hostOf(url);
      return { title: phrase("Open", name ?? host ?? "the site") };
    }
    case "fill": {
      const title = phrase("Type", definite(object ?? "field"));
      const always = constantValue(step.value);
      return always === undefined ? { title } : { title, detail: `Always “${always}”` };
    }
    case "select": {
      const title = phrase("Choose", definite(object ?? "option"));
      const always = constantValue(step.value);
      return always === undefined ? { title } : { title, detail: `Always “${always}”` };
    }
    case "click":
      return { title: phrase("Click", object ?? "the button") };
    case "press": {
      // "Press Tab in Phone": the key, and the place the step presses it (PL-033 requirement 5).
      const key = text(step.key) ?? "the key";
      return { title: target === undefined ? `Press ${key}` : `Press ${key} in ${target}` };
    }
    case "upload_file":
      return { title: phrase("Attach", definite(object ?? "file")) };
    case "read_value": {
      if (name !== undefined && startsWithVerb(name, "Read")) return { title: upperFirst(name) };
      return { title: `${phrase("Read", definite(object ?? "value"))} from the page` };
    }
    case "wait_for": {
      if (name !== undefined) return { title: upperFirst(name) };
      return { title: `Wait for ${definite(target ?? "page")}` };
    }
    case "assert": {
      if (name !== undefined) return { title: upperFirst(name) };
      return { title: `Check ${definite(target ?? "result")}` };
    }
    case "download":
      return { title: `Download ${definite(object ?? "file")}` };
    case "extract":
      return { title: `Read ${object === undefined ? "the details" : definite(object)} from the document` };
    case "classify":
      return { title: `Choose ${object === undefined ? "the best answer" : definite(object)}` };
    case "transform": {
      if (name !== undefined) return { title: upperFirst(name) };
      return { title: `Work out the ${words(text(step.op) ?? "value")}` };
    }
    case "match": {
      if (name !== undefined) return { title: upperFirst(name) };
      return { title: COMPARE[text(step.compare) ?? ""] ?? "Compare the two values" };
    }
    case "aggregate": {
      if (name !== undefined) return { title: upperFirst(name) };
      return { title: AGGREGATE[text(step.op) ?? ""] ?? "Combine the values" };
    }
    case "branch": {
      if (name !== undefined) return { title: upperFirst(name) };
      const condition = step.when ?? step.condition;
      return { title: `If ${conditionWords(condition)}, do the steps inside` };
    }
    case "for_each": {
      if (name !== undefined) return { title: upperFirst(name) };
      const item = text(step.as);
      return { title: `For each ${item === undefined ? "item" : words(item)}, do the steps inside` };
    }
    case "approval_gate":
      return { title: name === undefined ? "Wait for your approval" : upperFirst(name) };
    case "end_record":
      return {
        title:
          name === undefined
            ? (END_RECORD[text(step.outcome) ?? ""] ?? "Finish this row")
            : upperFirst(name),
      };
    default:
      return { title: name === undefined ? "Do the next thing" : upperFirst(name) };
  }
}

/** A plain constant a step always types; a template or an object is not shown to a person. */
function constantValue(value: unknown): string | undefined {
  if (value === null || typeof value !== "object") return undefined;
  const source = value as { kind?: unknown; value?: unknown };
  if (text(source.kind) !== "constant") return undefined;
  const constant = text(source.value);
  return constant === undefined || isTemplate(constant) ? undefined : constant;
}

/**
 * Every step of a recipe document, in order. A nested branch or loop is its own sentence and its
 * inner steps follow it, so a person reads the same order the runner follows.
 */
export function stepWordsList(steps: unknown): StepWords[] {
  if (!Array.isArray(steps)) return [];
  const out: StepWords[] = [];
  for (const entry of steps) {
    if (entry === null || typeof entry !== "object") continue;
    const step = entry as RecipeStep;
    out.push(stepWords(step));
    const nested = step as unknown as { then?: unknown; else?: unknown; steps?: unknown };
    for (const key of ["then", "else", "steps"] as const) {
      const inner = nested[key];
      if (Array.isArray(inner)) out.push(...stepWordsList(inner));
    }
  }
  return out;
}
