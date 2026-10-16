/**
 * The popup's plain words, in one chrome-free module so a test can read them directly.
 *
 * It mirrors `apps/web/src/lib/words.ts`: the engine's words never reach a person's eyes
 * (DESIGN.md §2), so every sentence the popup shows is built here. Nothing in this file touches
 * `chrome`, the DOM or the worker: the shared types come in with `import type` only, so importing
 * it never pulls the service worker, the recorder or the executor into a page.
 */
import type { RecordedEvent, RecordedStep } from "../shared/recipe-types";
import type { ExecutorState } from "../shared/messages";
import type { SessionState } from "../background/command-session";

/** The empty live list's one sentence, shown in `#steps` until something is recorded. */
export const STEPS_EMPTY = "The steps you take will appear here.";

/** A click with no name of its own: never its step id. */
export const UNNAMED_CLICK = "This click";

/** A warning on an event that carries no step (an `unrecordable` or a bare `navigate`). */
export const UNNAMED_STEP = "A step the recorder could not name";

/**
 * Every word of the engine that a person must never read, and the plain wording each becomes.
 * The same list as PL-023 requirement 7, copied from `apps/web/src/lib/words.ts` so the two
 * cannot drift; whole tokens for `v1`/`v2`, so an ordinary word is not a hit.
 */
export const ENGINE_WORDS: ReadonlyArray<{ engine: string; pattern: RegExp; plain: string }> = [
  { engine: "recipe", pattern: /recipe/i, plain: "task" },
  { engine: "ledger", pattern: /ledger/i, plain: "never shown" },
  { engine: "parked", pattern: /parked/i, plain: "needs your help" },
  { engine: "record key", pattern: /record[\s_-]*key/i, plain: "the person's or thing's name" },
  { engine: "verified", pattern: /verified/i, plain: "added, updated or done" },
  { engine: "rung", pattern: /rung/i, plain: "never shown" },
  { engine: "idempotency", pattern: /idempotency/i, plain: "never shown" },
  { engine: "v1", pattern: /\bv1\b/i, plain: "never shown" },
  { engine: "v2", pattern: /\bv2\b/i, plain: "never shown" },
  { engine: "step_", pattern: /step_/i, plain: "one plain sentence" },
  { engine: "fill-", pattern: /fill-/i, plain: "the element's own words" },
  { engine: "click-", pattern: /click-/i, plain: "the element's own words" },
];

/** The engine words this text contains, so a caller can name the rule it would break. */
export function engineWordsIn(text: string): string[] {
  return ENGINE_WORDS.filter((rule) => rule.pattern.test(text)).map((rule) => rule.engine);
}

/**
 * The element's own words, climbing the target ladder's named rungs: the role's accessible name,
 * then a label, then a test id, then the step's own name. Never a selector and never a step id.
 */
function ownWords(step: RecordedStep): string | null {
  const targets = step.targets ?? [];
  for (const target of targets) {
    if (target.by === "role" && target.name.trim().length > 0) return target.name.trim();
  }
  for (const target of targets) {
    if (target.by === "label" && target.text.trim().length > 0) return target.text.trim();
  }
  for (const target of targets) {
    if (target.by === "testid" && target.value.trim().length > 0) return target.value.trim();
  }
  const name = step.name?.trim();
  return name && name.length > 0 ? name : null;
}

/** A field's name with an article: "Email" -> "the email", "the To line" -> "the to line". */
function withArticle(name: string): string {
  const text = name.trim();
  if (text.length === 0) return "that field";
  const phrase = /^(the|a|an|your|my|this|these|those)\s/i.test(text) ? text : `the ${text}`;
  return phrase.toLowerCase();
}

/** The value a step wrote, when the recording carried one as a constant. */
function constantValue(step: RecordedStep): string | null {
  if (step.action !== "fill" && step.action !== "select") return null;
  const source = step.value;
  if (!source || source.kind !== "constant") return null;
  return String(source.value);
}

/**
 * "Clicked Save", "Typed the email" — one step, one plain sentence.
 *
 * Past tense, and named by what the element is rather than by a selector or a step id. A step the
 * recorder could not name at all falls back to the element's kind, never to its id.
 */
export function stepSentence(step: RecordedStep | null, kind?: string): string {
  if (!step) {
    if (kind === "navigate") return "Went to another page";
    if (kind === "observation") return "The page showed the result";
    return UNNAMED_STEP;
  }
  const name = ownWords(step);
  if (step.action === "click") return name ? `Clicked ${name}` : "Clicked a control on the page";
  if (step.action === "fill") return name ? `Typed ${withArticle(name)}` : "Typed into a field";
  if (step.action === "select") {
    const value = constantValue(step);
    return value ? `Chose ${value}` : "Chose an option";
  }
  if (step.action === "press") return `Pressed ${step.key}`;
  return UNNAMED_STEP;
}

/**
 * One sentence per recorded interaction, in order.
 *
 * An `observation` is evidence about the action before it and an `unrecordable` is the opposite of
 * a step, so neither is a step of its own here; a `navigate` is the page moving and does get a
 * sentence, because the person did that.
 */
export function stepSentences(events: RecordedEvent[]): string[] {
  const sentences: string[] = [];
  for (const event of events) {
    if (!event) continue;
    if (!event.step && event.kind !== "navigate") continue;
    sentences.push(stepSentence(event.step ?? null, event.kind));
  }
  return sentences;
}

/** The recorder's state as a person reads it. */
export function recordingWords(state: "idle" | "recording"): string {
  return state === "recording" ? "Recording" : "Not recording";
}

/** The attended run's state as a person reads it. */
export function runStateWords(state: ExecutorState): string {
  switch (state) {
    case "running":
      return "Running";
    case "paused":
      return "Paused";
    case "aborted":
      return "Stopped";
    case "done":
      return "Finished";
    case "failed":
      return "Didn't work";
    default:
      return "Nothing is running";
  }
}

/** The connection to ProcessLens, with the worker's own detail when it has one. */
export function sessionWords(state: SessionState, detail?: string): string {
  const suffix = detail && detail.trim().length > 0 ? ` — ${detail.trim()}` : "";
  switch (state) {
    case "ready":
      return `Connected to ProcessLens${suffix}`;
    case "executing":
      return `Connected to ProcessLens and working${suffix}`;
    case "pairing":
    case "connecting":
      return `Connecting to ProcessLens${suffix}`;
    default:
      return `Not connected${suffix}`;
  }
}

/** The streaming state: off, ready to send, or sending. */
export function streamWords(wsUrl: string, connected: boolean): string {
  if (wsUrl.trim().length === 0) return "Off";
  return connected ? "Sending" : "Ready to send";
}
