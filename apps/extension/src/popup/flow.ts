// The popup's three-step flow (PL-033): Teach, Save, Open in ProcessLens.
//
// The markup holds one card per step, in order. Only the current step's button is gold: the other
// cards' primary buttons are demoted to quiet ones while the person is on a different step. The
// module is chrome-free and DOM-only, so the tests can drive it directly.
import type { RecordedEvent } from "../shared/recipe-types";
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

/**
 * The step lines the live list shows: one per recorded interaction, in order.
 *
 * An `observation` is evidence about the action before it and an `unrecordable` is the opposite of
 * a step, so neither gets a line of its own; a `navigate` does, because the person moved the page.
 * The seq is the event's own, so a line can be deleted from the recording it belongs to.
 */
export function stepLines(events: readonly RecordedEvent[]): StepLine[] {
  const lines: StepLine[] = [];
  for (const event of events) {
    if (!event) continue;
    if (!event.step && event.kind !== "navigate") continue;
    lines.push({ seq: event.seq, sentence: stepSentence(event.step ?? null, event.kind) });
  }
  return lines;
}

/** What the live step list needs to draw itself. */
export interface StepListOptions {
  /** True while the recorder runs: a step's × is hidden until Stop. */
  recording: boolean;
  /** What a click on a step's × should do; nothing when the popup has nowhere to send it. */
  onRemove?: (seq: number) => void;
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
  const lines = stepLines(events);
  if (lines.length === 0) {
    const empty = document.createElement("li");
    empty.className = "steps__empty";
    empty.textContent = STEPS_EMPTY;
    list.replaceChildren(empty);
    return;
  }
  list.replaceChildren(...lines.map((line) => stepItem(line, options)));
}

function stepItem(line: StepLine, options: StepListOptions): HTMLLIElement {
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
  return item;
}
