// The popup's three-step flow (PL-033): Teach, Save, Open in ProcessLens.
//
// The markup holds one card per step, in order. Only the current step's button is gold: the other
// cards' primary buttons are demoted to quiet ones while the person is on a different step. The
// module is chrome-free and DOM-only, so the tests can drive it directly.
import type { RecordedEvent, RecordedStep } from "../shared/recipe-types";
import { STEPS_EMPTY, stepSentence } from "./words";

/** The three steps of the popup, in the order a person walks them. */
export type PopupStep = "teach" | "save" | "open";

/** The cards of the flow, in order. */
export const STEP_CARDS: readonly PopupStep[] = ["teach", "save", "open"];

/**
 * The step the person is on: teaching while the recorder runs, then saving once there is a
 * recording, and opening the saved task on the dashboard after a save. A recording in progress
 * always wins — a saved task does not pull the person away from teaching.
 */
export function currentStep(input: {
  recording: boolean;
  hasEvents: boolean;
  savedTaskUrl: string | null;
}): PopupStep {
  if (input.recording) return "teach";
  if (input.savedTaskUrl !== null) return "open";
  if (input.hasEvents) return "save";
  return "teach";
}

/**
 * Put the flow's cards in the state one step calls for: the current card is marked and its primary
 * button is gold; every other card's primary button is quiet, and the Open card stays off the
 * screen until a task has been saved.
 */
export function applyStep(root: ParentNode, step: PopupStep): void {
  for (const card of Array.from(root.querySelectorAll<HTMLElement>(".step-card"))) {
    const isCurrent = card.dataset.step === step;
    card.classList.toggle("is-current", isCurrent);
    if (card.dataset.step === "open") card.hidden = !isCurrent;
    for (const button of Array.from(card.querySelectorAll<HTMLButtonElement>(".button--flow"))) {
      button.classList.toggle("button--primary", isCurrent);
      button.classList.toggle("button--quiet", !isCurrent);
    }
  }
}

/** The task's page on the dashboard, from the ProcessLens address the person connected to. */
export function taskUrl(baseUrl: string, workflowId: string): string {
  return `${baseUrl.replace(/\/+$/, "")}/tasks/${workflowId}`;
}

/**
 * Open the task's page in a real tab. A plain `<a href>` does nothing in an extension popup, so
 * every link goes through `chrome.tabs.create` (PL-033 requirement 2).
 */
export function openTaskTab(
  tabs: { create(options: { url: string }): unknown },
  url: string,
): void {
  tabs.create({ url });
}

/** What auto-connect needs: where the session is, how to connect, and which address to use. */
export interface AutoConnectDeps {
  sessionState(): Promise<string>;
  connect(hubUrl: string): Promise<unknown>;
  hubUrl(): string;
}

/**
 * Connect by itself when the popup opens and no session is there (PL-033 requirement 4).
 *
 * A session that is ready, working, or already on its way is left alone; an idle or disconnected
 * one is connected to the address in the ProcessLens address field, which defaults to
 * `http://127.0.0.1:8000`. Returns whether a connect was sent, exactly once.
 */
export async function autoConnect(deps: AutoConnectDeps): Promise<boolean> {
  const state = await deps.sessionState();
  if (state !== "idle" && state !== "disconnected") return false;
  await deps.connect(deps.hubUrl());
  return true;
}

/** The remove button's only label: it must say what it does on its own. */
export const REMOVE_STEP_LABEL = "Remove this step";

/** One line of the live step list: the event it stands for, and its one plain sentence. */
export interface StepLine {
  seq: number;
  sentence: string;
}

/** A comparable signature of a step's first target, so a click and its submit fold together. */
function firstTargetKey(step: RecordedStep | null): string {
  const first = step?.targets?.[0];
  return first ? JSON.stringify(first) : "";
}

/**
 * The step lines the live list shows: one per recorded interaction, in order.
 *
 * An `observation` is evidence about the action before it and an `unrecordable` is the opposite of
 * a step, so neither gets a line of its own; a `navigate` does, because the person moved the page.
 * The seq is the event's own, so a line can be deleted from the recording it belongs to.
 */
export function stepLines(events: readonly RecordedEvent[]): StepLine[] {
  const lines: StepLine[] = [];
  let previous: RecordedEvent | null = null;
  for (const event of events) {
    if (!event) continue;
    if (!event.step && event.kind !== "navigate") continue;
    // PL-107: a `submit` right after a `click` on the same control is the click's own line, not a
    // second "Clicked …" the person has to delete by hand. The compiler's `collapse_submits` folds
    // the same pair; the live list shows one line, and removing it removes both events.
    if (
      event.kind === "submit" &&
      previous !== null &&
      previous.kind === "click" &&
      firstTargetKey(previous.step) === firstTargetKey(event.step)
    ) {
      continue;
    }
    lines.push({ seq: event.seq, sentence: stepSentence(event.step ?? null, event.kind) });
    previous = event;
  }
  return lines;
}

/** PL-103: a click that landed on a page area; nothing replays it. */
export const EMPTY_SPACE_PROBLEM = "This click lands on empty space, so it can't be repeated.";

/** PL-103: teaching began on a form a New button had already opened. */
export const MID_FORM_PROBLEM =
  "Teaching started on a form that was already open, so each row would have nowhere to type. Start over: begin on the list and press New while recording.";

/** What fixes a problem: remove that one step, or record the task again from the start. */
export type StepFix = "remove" | "start-over";

export interface StepProblem {
  sentence: string;
  fix: StepFix;
}

/** Roles that say nothing about what a click does, so a long name on one is page text. */
const AREA_ROLES = new Set(["generic", "none", "presentation", "group", "region", "main", "form"]);
const AREA_NAME_LENGTH = 100;

/** A page whose address says it is a new, unsaved record (Odoo's `/new`, `view_type=form`). */
const NEW_RECORD_URL = /\/new(?:[/?#]|$)|[?&#]view_type=form(?!.*[?&#]id=)|\/create(?:[/?#]|$)/i;

function isAreaClick(step: RecordedEvent["step"]): boolean {
  const record = step as { action?: string; targets?: Array<{ by?: string; role?: string; name?: string }> } | null;
  if (record?.action !== "click") return false;
  const first = record.targets?.[0];
  return (
    first?.by === "role" &&
    AREA_ROLES.has(first.role ?? "") &&
    (first.name ?? "").length >= AREA_NAME_LENGTH
  );
}

/**
 * PL-103: the steps that cannot be repeated, by seq, each with one plain sentence and its fix.
 * Checked after Stop, so a person fixes the recording where they see it, before saving.
 */
export function stepProblems(events: readonly RecordedEvent[]): Map<number, StepProblem> {
  const problems = new Map<number, StepProblem>();
  const steps = events.filter((event) => event && (event.step || event.kind === "navigate"));
  const first = steps[0];
  if (first && first.kind === "fill" && NEW_RECORD_URL.test(first.url ?? "")) {
    problems.set(first.seq, { sentence: MID_FORM_PROBLEM, fix: "start-over" });
  }
  for (const event of steps) {
    if (isAreaClick(event.step ?? null)) {
      problems.set(event.seq, { sentence: EMPTY_SPACE_PROBLEM, fix: "remove" });
    }
  }
  return problems;
}

/** The fix button's words. Exported so the panel's copy cannot drift from the popup's. */
export const FIX_LABEL: Record<StepFix, string> = { remove: "Remove", "start-over": "Start over" };

/** What the live step list needs to draw itself. */
export interface StepListOptions {
  /** True while the recorder runs: a step's × is hidden until Stop. */
  recording: boolean;
  /** What a click on a step's × should do; nothing when the popup has nowhere to send it. */
  onRemove?: (seq: number) => void;
  /** Why the last start did not happen (PL-039); shown in place of the steps when set. */
  warning?: string | null;
  /** PL-103: what a step's Start over should do: clear the steps, ready to record again. */
  onStartOver?: () => void;
}

/**
 * Draw the live list of recorded steps into `list`.
 *
 * Each line carries a quiet × that removes that step (PL-043). While the recording is still
 * running the × is hidden: the list is being built, and the worker refuses a removal either way.
 * The sentence is the element's own words, never a step id.
 */
export function renderStepList(
  list: HTMLElement,
  events: readonly RecordedEvent[],
  options: StepListOptions,
): void {
  const warning = options.warning ?? null;
  if (warning !== null && warning.trim().length > 0) {
    const item = document.createElement("li");
    item.className = "steps__warning";
    item.textContent = warning;
    list.replaceChildren(item);
    return;
  }
  const lines = stepLines(events);
  if (lines.length === 0) {
    const empty = document.createElement("li");
    empty.className = "steps__empty";
    empty.textContent = STEPS_EMPTY;
    list.replaceChildren(empty);
    return;
  }
  // PL-103: problems are shown once the recording is stopped, on the row they belong to.
  const problems = options.recording ? new Map<number, StepProblem>() : stepProblems(events);
  list.replaceChildren(...lines.map((line) => stepItem(line, options, problems.get(line.seq))));
}

function stepItem(line: StepLine, options: StepListOptions, problem?: StepProblem): HTMLLIElement {
  const item = document.createElement("li");
  item.className = "steps__item";
  item.dataset["seq"] = String(line.seq);
  const text = document.createElement("span");
  text.className = "steps__text";
  text.textContent = line.sentence;
  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "steps__remove";
  remove.setAttribute("aria-label", REMOVE_STEP_LABEL);
  remove.textContent = "×";
  remove.hidden = options.recording;
  remove.addEventListener("click", () => options.onRemove?.(line.seq));
  item.append(text, remove);
  if (problem) {
    item.classList.add("steps__item--problem");
    const note = document.createElement("p");
    note.className = "steps__problem";
    note.textContent = problem.sentence;
    const fix = document.createElement("button");
    fix.type = "button";
    fix.className = "steps__fix";
    fix.textContent = FIX_LABEL[problem.fix];
    fix.addEventListener("click", () => {
      if (problem.fix === "remove") options.onRemove?.(line.seq);
      else options.onStartOver?.();
    });
    note.append(" ", fix);
    item.append(note);
  }
  return item;
}
