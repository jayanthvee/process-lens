/**
 * The popup's plain words, in one chrome-free module so a test can read them directly.
 *
 * It mirrors `apps/web/src/lib/words.ts`: the engine's words never reach a person's eyes
 * (DESIGN.md §2), so every sentence the popup shows is built here. Nothing in this file touches
 * `chrome`, the DOM or the worker: the shared types come in with `import type` only, so importing
 * it never pulls the service worker, the recorder or the executor into a page.
 */
import type { RecordedEvent, RecordedStep } from "../shared/recipe-types";
import type { SessionState } from "../background/command-session";

/** The empty live list's one sentence, shown in `#steps` until something is recorded. */
export const STEPS_EMPTY = "The steps you take will appear here.";

/**
 * PL-039: the page's content script never answered the ping, so the recording was not started.
 * Reloading the extension orphans the content script in every open tab; a refresh re-attaches it.
 */
export const REFRESH_PAGE_WARNING =
  "Refresh this page (press F5), then start recording again. ProcessLens was updated and can't see this page yet.";

/** PL-039: the page is one a recording is never allowed on, so there is nothing to refresh for. */
export const UNRECORDABLE_PAGE_WARNING =
  "ProcessLens can't record on this page. Open your app's page first.";

/**
 * PL-106 item 1: why "Save as task" is greyed out, one sentence per reason, right under the button
 * in `#make-go-why`. `syncGo` writes the first one that applies; a button that is ready says
 * nothing there.
 */
export const GO_WHY_NOTHING_RECORDED = "Record some steps first.";
export const GO_WHY_NO_VALUES =
  "Type at least one value while teaching, so each row has something to fill in.";
export const GO_WHY_NO_KEY = "Pick the value that is different for every row.";

/**
 * PL-109 items 5 and 6: the decision the popup shows in place of a question. The label between
 * the two pieces is the person's own (their field's name, their click's name), in bold.
 */
export const KEY_DECIDED_BEFORE = "We'll use ";
export const KEY_DECIDED_AFTER = " so nothing gets added twice.";
export const COMMIT_DECIDED_BEFORE = "The ";
export const COMMIT_DECIDED_AFTER = " click saves your work.";

/** PL-107 item 6 / PL-109 item 6: the answer that saves nothing, and its stand-alone line. */
export const NOTHING_SAVED_CHOICE = "Nothing gets saved";
export const NOTHING_SAVED_LINE = "Nothing gets saved.";

/** A click with no name of its own: never its step id. */
export const UNNAMED_CLICK = "This click";

/** A warning on an event that carries no step (an `unrecordable` or a bare `navigate`). */
export const UNNAMED_STEP = "A step the recorder could not name";

/**
 * Every word of the engine that a person must never read, and the plain wording each becomes.
 *
 * The first twelve are PL-023 requirement 7's list, copied from `apps/web/src/lib/words.ts` so the
 * two cannot drift; requirement 7 says "any of", so the list is a floor and this one is a
 * superset. The rest are the phrases DESIGN.md §2 names in the same table but requirement 7's
 * twelve omit, and they are here because the popup really does receive them: a guard refuses with
 * "… is not in allowed_origins" and "set allow_destructive_control …" (`src/executor/guards.ts`),
 * and a session passes that refusal to this module as its detail. Whole tokens for `v1`/`v2`, so an
 * ordinary word is not a hit.
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
  // DESIGN.md §2 names these too, and the worker hands them to the popup verbatim.
  { engine: "allowed origins", pattern: /allowed[\s_-]*origins?/i, plain: "sites this task may use" },
  {
    engine: "allow_destructive_control",
    pattern: /allow[\s_-]*destructive[\s_-]*control/i,
    plain: "never shown",
  },
  // PL-031's compiler warnings, in the voice the compile route really sends. None of them is a
  // sentence a person can act on, so they stay off the save result and reach support under
  // More → "Notes for support".
  { engine: "skipped the fill", pattern: /skipped the fill/i, plain: "not shown" },
  { engine: "bound a literal", pattern: /bound a literal/i, plain: "not shown" },
  { engine: "templated", pattern: /templated/i, plain: "not shown" },
  {
    engine: "derived the input schema",
    pattern: /derived the input schema/i,
    plain: "not shown",
  },
  { engine: "dropped container click", pattern: /dropped container click/i, plain: "not shown" },
  { engine: "folded the retried fill", pattern: /folded the retried fill/i, plain: "not shown" },
  { engine: "assertion for", pattern: /assertion for/i, plain: "not shown" },
  { engine: "this save is checked", pattern: /this save is checked/i, plain: "not shown" },
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

/**
 * A block of the engine's own sentences, with the ones a person can read kept and the rest counted.
 *
 * The compile route answers with sentences written for an engineer, in the same voice as the
 * worker's detail: `assertion for click-2 taken from the recorded observation`, or `navigate step 7
 * goes to …, whose origin … is not in allowed_origins`. `#make-warnings` would otherwise print a
 * step id and a schema key straight onto the screen.
 *
 * The sentences it cannot show are not silently dropped: the caller reports how many there were, so
 * a person still learns that the engine had more to say.
 */
export function plainSentences(text: string | null | undefined): { shown: string[]; hidden: number } {
  const shown: string[] = [];
  let hidden = 0;
  for (const line of (text ?? "").split("\n")) {
    const sentence = line.trim();
    if (sentence.length === 0) continue;
    if (engineWordsIn(sentence).length === 0) shown.push(sentence);
    else hidden += 1;
  }
  return { shown, hidden };
}

/** A compile refusal that no assertion could be inferred for the chosen save click (PL-107). */
const NO_ASSERTION_REFUSAL =
  /(?:^|\b)(?:no|without)\s+assert(?:_after|ion)\b|assertion[^.]*could\s*(?:not|n't)\s+be\s+inferred|commit step must carry an assertion/i;

/** The step id a refusal names, as "click-2" or "fill-3". */
const STEP_ID = /\b([a-z]+-\d+)\b/;

/** The recording's last click, the step the compiler treats as the save. */
function lastClickStep(events: RecordedEvent[]): RecordedStep | null {
  let last: RecordedStep | null = null;
  for (const event of events) {
    const step = event?.step ?? null;
    if (step !== null && step.action === "click") last = step;
  }
  return last;
}

/** The name of the click a refusal's step id points at, or the recording's last click. */
function clickNameIn(text: string, events: RecordedEvent[]): string | null {
  const id = STEP_ID.exec(text)?.[1];
  const named = id
    ? (events.find((event) => event?.step?.id === id)?.step ?? null)
    : null;
  const step = named ?? lastClickStep(events);
  return step ? ownWords(step) : null;
}

/**
 * PL-107: one plain sentence for a recording the compiler refused.
 *
 * A save click the compiler could not check ("no assertion could be inferred") is the case a person
 * fixes by answering "Which click saves your work?" differently, so the sentence names the click and
 * says so. A schema or step-name refusal keeps PL-103's plain sentence. Anything else is said in the
 * same shape, so the panel never shows a bare "Could not save this as a task.".
 */
export function compileRefusalSentence(problems: string[], events: RecordedEvent[]): string {
  const text = problems.join("\n");
  if (NO_ASSERTION_REFUSAL.test(text)) {
    const click = clickNameIn(text, events) ?? "that click";
    return `ProcessLens couldn't see anything change after “${click}”, so it can't check that click worked. Pick a different answer for “Which click saves your work?”, or choose “Nothing gets saved”.`;
  }
  const { shown } = plainSentences(text);
  if (shown.length > 0) return shown[0]!;
  return "This recording can't be saved yet: something in it can't be repeated. Start over and teach it again.";
}

/**
 * The worker's own detail, when it is free of the engine's words.
 *
 * The service worker's detail is written for an engineer: a guard refuses with
 * "the page origin … is not in allowed_origins" or "set allow_destructive_control on the step to
 * permit it", and a session reports the raw reason underneath its state. DESIGN.md §2 keeps those
 * off the screen, so the detail is appended only when it carries none of `ENGINE_WORDS`; the state
 * itself still says what happened, in plain words, either way.
 */
export function plainDetail(detail: string | undefined | null): string {
  const text = (detail ?? "").trim();
  if (text.length === 0) return "";
  return engineWordsIn(text).length === 0 ? text : "";
}

/** The connection to ProcessLens, with the worker's own detail when it has one. */
export function sessionWords(state: SessionState, detail?: string): string {
  const shown = plainDetail(detail);
  const suffix = shown.length > 0 ? ` — ${shown}` : "";
  switch (state) {
    case "ready":
      return `Connected to ProcessLens${suffix}`;
    case "executing":
      return `Connected to ProcessLens and working${suffix}`;
    case "pairing":
    case "connecting":
      return `Connecting to ProcessLens${suffix}`;
    default:
      // PL-033 requirement 4: the one sentence a person can act on when the popup opens and
      // nobody is home.
      return `ProcessLens isn't running${suffix}`;
  }
}

/** The step count as the Teach card shows it: "15 steps recorded". */
export function stepCountWords(count: number): string {
  return `${count} step${count === 1 ? "" : "s"} recorded`;
}

/** The sentence under the save result when the recording holds no search to check against. */
export const DUPLICATES_BLOCKED =
  "I couldn't check for duplicates because you didn't search first.";

/** The save result's plain notes: at most two sentences, and nothing about the engine. */
export function plainSaveNotes(input: {
  duplicatesBlocked: boolean;
  parkLabels: string[];
}): string[] {
  const notes: string[] = [];
  if (input.duplicatesBlocked) notes.push(DUPLICATES_BLOCKED);
  const labels = input.parkLabels.filter((label) => label.trim().length > 0);
  if (labels.length === 1) {
    notes.push(`Stops and asks when ${labels[0]} is empty.`);
  } else if (labels.length > 1) {
    const list = `${labels.slice(0, -1).join(", ")} or ${labels[labels.length - 1]}`;
    notes.push(`Stops and asks when ${list} is empty.`);
  }
  return notes.slice(0, 2);
}

/** The streaming state: off, ready to send, or sending. */
export function streamWords(wsUrl: string, connected: boolean): string {
  if (wsUrl.trim().length === 0) return "Off";
  return connected ? "Sending" : "Ready to send";
}
