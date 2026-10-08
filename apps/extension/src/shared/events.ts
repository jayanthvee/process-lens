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
}

export function makeEvent(input: EventInput): PendingEvent {
  const event: PendingEvent = {
    at: input.at ?? new Date().toISOString(),
    kind: input.kind,
    url: input.url,
    step: input.step,
  };
  if (input.observation) event.observation = input.observation;
  if (input.step && input.frame && input.frame.length > 0) {
    input.step.frame = input.frame;
  }
  return event;
}
