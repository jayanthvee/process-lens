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
  /**
   * PL-018: the recorder witnessed this field offer suggestions, so a replay types the value and
   * accepts the single suggestion that contains it. Absent on an ordinary fill, which is typed and
   * left alone.
   */
  autocomplete?: boolean;
}

export interface SelectStep extends StepCommon {
  action: "select";
  targets: LocatorRung[];
  within?: string;
  frame?: FrameStep[];
  value: ValueSource;
  option_match?: "text" | "value" | "index";
}

/**
 * The keys the recorder records as a `press`: the ones that do something other than type a
 * character (accept a suggestion, move focus with intent, dismiss a listbox, delete a token) and
 * the two chorded forms a composer sends with. A printable single character is never one of these.
 */
export type PressKey =
  | "Enter"
  | "Tab"
  | "Escape"
  | "Backspace"
  | "Delete"
  | "Control+Enter"
  | "Meta+Enter";

/**
 * One key sent to the focused element. Deliberately not commit-capable: the recipe schema gives a
 * press step neither `commit` nor `assert_after`, so "a key alone changes no destination record"
 * is enforced by the schema rather than by convention.
 */
export interface PressStep extends StepCommon {
  action: "press";
  targets: LocatorRung[];
  within?: string;
  frame?: FrameStep[];
  key: PressKey;
}

/** The step shapes the recorder emits. It never emits a commit step. */
export type RecordedStep = ClickStep | FillStep | SelectStep | PressStep;

/**
 * The kinds the recorder captures: click, fill/input, change/select, submit, a
 * top-level navigation nobody's click caused, and the post-action observation.
 *
 * `observation` is not an interaction: it is what the page showed after a click
 * or a submit, carried on its own event so the compiler can use it as the
 * assertion for the commit step it followed. It carries no step of its own.
 *
 * `navigate` is not an interaction either: it says the page moved — a typed URL,
 * a bookmark, a back/forward, a reload — without anybody clicking. Its event has
 * no step and carries the URL the tab landed on.
 *
 * `press` is one key sent to the focused element, in a step of its own.
 *
 * `unrecordable` is the opposite of a step: it says the person did something the recording cannot
 * replay (a recipient removed with no remove button), so the compiler can refuse it instead of
 * compiling a recording that would replay the wrong change. Like `navigate`, it has no step.
 */
export type RecordedKind =
  | "click"
  | "fill"
  | "select"
  | "submit"
  | "navigate"
  | "observation"
  | "press"
  | "unrecordable";

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
   * The condition the page was observed to satisfy after an action. Keys are the
   * assertion predicates the recipe schema accepts (`text_visible`, `url_matches`,
   * and the like).
   *
   * It appears on an `observation` event — evidence about the action before it — and on the
   * action itself when the evidence came from the page that action navigated to, which only
   * the service worker can attach across the navigation.
   */
  observation?: Condition;
  /**
   * The recorder's own caveat about this step (for example, only a positional selector
   * remained). It lives on the event envelope, never on the step: the recipe schema forbids
   * unknown step properties, so a warning there would make the compiled recipe invalid.
   */
  warning?: string;
  /**
   * Whether the control this event acted on is required (`required` or
   * `aria-required="true"`). Like `warning`, it is a recorder note about the
   * event and lives on the envelope, never on the step.
   */
  required?: boolean;
  /**
   * Present only on the `fill` a chip-add produced: this fill's value is a chip's value (an
   * address the field now holds), not text the person typed there.
   */
  chip?: boolean;
  /**
   * Present only on a `fill` whose field showed a listbox of suggestions for it, as the recorder
   * witnessed: the field is an autocomplete. It is the envelope note the compiler folds on and the
   * executor's replay rule mirrors, and it is envelope-only because a step may not carry extras.
   */
  autocomplete?: boolean;
  /**
   * The id of the fill this event supersedes: a chip-add fill that followed a prefix fill of the
   * same field in the same interval is the same action, so the compiler drops the named fill. The
   * value is a step id; the fill that carries it is the step that survives.
   */
  supersedes?: string;
  /**
   * Why an event carries no step at all (`kind: "unrecordable"`): the machine-readable reason for a
   * change the recording cannot replay. It rides the envelope; `warning` is the sentence a person
   * reads beside it.
   */
  reason?: string;
  /**
   * Present only on a `fill` no one typed into: the page itself put the value there while the
   * recording was running (a numeric default, a resolved many2one, a computed field). The value is
   * still recorded -- it is what the page held -- but it is not an action the person took, and a
   * recipe must not replay it: typing a computed default back into a field is not part of the task,
   * and on a page that names such a field in a different language or order it is a wrong step.
   *
   * It rides the envelope as a plain fact (the `warning` beside it is the sentence a person reads),
   * so the compiler drops the fill by reading one boolean instead of matching English prose.
   */
  page_set?: boolean;
}
