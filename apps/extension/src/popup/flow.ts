// The popup's three-step flow (PL-033): Teach, Save, Open in ProcessLens.
//
// The markup holds one card per step, in order. Only the current step's button is gold: the other
// cards' primary buttons are demoted to quiet ones while the person is on a different step. The
// module is chrome-free and DOM-only, so the tests can drive it directly.
import { STEPS_EMPTY } from "./words";

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

/**
 * The live list of steps: the recorder's warning in place of the steps when a start was refused
 * (PL-039), else one sentence per recorded step, else the empty line.
 */
export function renderStepList(
  steps: HTMLOListElement,
  sentences: string[],
  warning: string | null,
): void {
  if (warning !== null && warning.trim().length > 0) {
    const item = document.createElement("li");
    item.className = "steps__warning";
    item.textContent = warning;
    steps.replaceChildren(item);
    return;
  }
  if (sentences.length === 0) {
    const empty = document.createElement("li");
    empty.className = "steps__empty";
    empty.textContent = STEPS_EMPTY;
    steps.replaceChildren(empty);
    return;
  }
  steps.replaceChildren(
    ...sentences.map((sentence) => {
      const item = document.createElement("li");
      item.className = "steps__item";
      item.textContent = sentence;
      return item;
    }),
  );
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
