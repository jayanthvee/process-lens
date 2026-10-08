// TypeScript shapes for the subset of the recipe format the recorder produces.
//
// These mirror the recipe JSON Schema (packages/recipe/schema/recipe.schema.json)
// so that a recorded event can be carried into a recipe without translation. The
// schema stays the authority; this file describes what the recorder is able to emit.

/** One rung of the ordered target ladder, best first. */
export type LocatorRung =
  | { by: "role"; role: string; name: string; within?: string }
  | { by: "testid"; value: string; within?: string }
  | { by: "label"; text: string; within?: string }
  | { by: "css"; selector: string; within?: string }
  | { by: "text_fuzzy"; value: string; within?: string };

/** A step of the document holding the element, outermost first. */
export type FrameStep = { by: "index"; index: number } | { by: "url"; url_matches: string };

/** Where a value comes from. The recorder only ever produces a literal constant. */
export type ValueSource =
  | { kind: "input"; column: string }
  | { kind: "constant"; value: string | number | boolean }
  | { kind: "rule_table"; table: string; key: ValueSource; default?: string }
  | { kind: "page"; variable: string }
  | { kind: "document"; field: string }
  | { kind: "ai_text"; template: string; requires_approval: true; max_characters?: number };

export type Condition = Record<string, unknown>;

interface StepCommon {
  id: string;
  name?: string;
  commit?: boolean;
  assert_after?: Condition;
  timeout_ms?: number;
  on_failure?: "park" | "fail";
}

export interface ClickStep extends StepCommon {
  action: "click";
  targets: LocatorRung[];
  within?: string;
  frame?: FrameStep[];
  allow_destructive_control?: boolean;
}

export interface FillStep extends StepCommon {
  action: "fill";
  targets: LocatorRung[];
  within?: string;
  frame?: FrameStep[];
  value: ValueSource;
  clear_first?: boolean;
}

export interface SelectStep extends StepCommon {
  action: "select";
  targets: LocatorRung[];
  within?: string;
  frame?: FrameStep[];
  value: ValueSource;
  option_match?: "text" | "value" | "index";
}

/** The step shapes the recorder emits. It never emits a commit step. */
export type RecordedStep = ClickStep | FillStep | SelectStep;

/**
 * The kinds the recorder captures: click, fill/input, change/select, submit, and
 * the post-action observation.
 *
 * `observation` is not an interaction: it is what the page showed after a click
 * or a submit, carried on its own event so the compiler can use it as the
 * assertion for the commit step it followed. It carries no step of its own.
 */
export type RecordedKind = "click" | "fill" | "select" | "submit" | "observation";

/**
 * One captured interaction, in order.
 *
 * `step` is the recipe-shaped step. It is null for a form submit that had no
 * identifiable submit control (a submit that has one carries the click step for
 * it) and for an `observation` event, which never has a step.
 */
export interface RecordedEvent {
  seq: number;
  at: string;
  kind: RecordedKind;
  url: string;
  step: RecordedStep | null;
  /**
   * Present on an `observation` event: the condition the page was observed to
   * satisfy after the action before it. Keys are the assertion predicates the
   * recipe schema accepts (`text_visible`, `url_matches`, and the like).
   */
  observation?: Condition;
}
