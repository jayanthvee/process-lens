// Envelope helpers shared by the recorder and the service worker.
import type {
  Condition,
  FrameStep,
  RecordedEvent,
  RecordedKind,
  RecordedStep,
} from "./recipe-types";

export type PendingEvent = Omit<RecordedEvent, "seq">;

let stepCounter = 0;

/** A provisional step id. The compiler renames steps when it groups them. */
export function nextStepId(action: string): string {
  stepCounter += 1;
  return `${action}-${stepCounter}`;
}

/** Reset the id counter. Only tests need this; ids are unique within a page. */
export function resetStepIds(): void {
  stepCounter = 0;
}

export interface EventInput {
  kind: RecordedKind;
  url: string;
  step: RecordedStep | null;
  frame?: FrameStep[] | undefined;
  at?: string;
  /** Present only on an `observation` event: what the page showed after an action. */
  observation?: Condition | undefined;
  /** The recorder's own caveat about the step this event carries, if any. */
  warning?: string | undefined;
  /** Whether the control this event acted on is required. Present only when true. */
  required?: boolean | undefined;
  /** True on the `fill` a chip-add produced: the value is a chip's value, not typed text. */
  chip?: boolean | undefined;
  /** True on a fill whose field the recorder witnessed offering suggestions. */
  autocomplete?: boolean | undefined;
  /** The id of the fill this event's chip-add fill supersedes. */
  supersedes?: string | undefined;
  /** Why an event carries no step: the machine-readable reason for an unrecordable change. */
  reason?: string | undefined;
  /**
   * True on a `fill` the page produced by itself: the value is real, but nobody typed it. It is
   * copied whenever it was supplied, because `page_set: false` is the recorder saying "a person did
   * type this", which is not the same as saying nothing about it.
   */
  page_set?: boolean | undefined;
  /** PL-031: how many input events the recorder saw for this fill. */
  keystrokes?: number | undefined;
}

export function makeEvent(input: EventInput): PendingEvent {
  const event: PendingEvent = {
    at: input.at ?? new Date().toISOString(),
    kind: input.kind,
    url: input.url,
    step: input.step,
  };
  if (input.observation) event.observation = input.observation;
  if (input.warning) event.warning = input.warning;
  if (input.required) event.required = input.required;
  // The PL-018 envelope carriers. A boolean is copied whenever it was supplied: `autocomplete:
  // false` says the recorder looked and saw no listbox, which is not the same as saying nothing.
  if (input.chip !== undefined) event.chip = input.chip;
  if (input.autocomplete !== undefined) event.autocomplete = input.autocomplete;
  if (input.supersedes !== undefined) event.supersedes = input.supersedes;
  if (input.reason !== undefined) event.reason = input.reason;
  if (input.page_set !== undefined) event.page_set = input.page_set;
  if (input.keystrokes !== undefined) event.keystrokes = input.keystrokes;
  if (input.step && input.frame && input.frame.length > 0) {
    input.step.frame = input.frame;
  }
  return event;
}
