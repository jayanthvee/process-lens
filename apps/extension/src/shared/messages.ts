// The message protocol between the popup, the content scripts, and the worker.
import type { RecordedEvent } from "./recipe-types";
import type { PendingEvent } from "./events";

export type RecordingState = "idle" | "recording";

export interface RecorderStatus {
  state: RecordingState;
  count: number;
  startedAt: string | null;
  wsUrl: string;
  wsConnected: boolean;
}

export const EMPTY_STATUS: RecorderStatus = {
  state: "idle",
  count: 0,
  startedAt: null,
  wsUrl: "",
  wsConnected: false,
};

/** Sent from a content script or the popup to the service worker. */
export type ToWorker =
  | { type: "record:event"; event: PendingEvent }
  | { type: "record:start" }
  | { type: "record:stop" }
  | { type: "record:clear" }
  | { type: "record:get" }
  | { type: "record:set-ws"; wsUrl: string };

/** Sent from the service worker to content scripts and the popup. */
export type FromWorker =
  | { type: "record:state"; status: RecorderStatus }
  | { type: "record:appended"; event: RecordedEvent; status: RecorderStatus };

export function isToWorker(message: unknown): message is ToWorker {
  return (
    typeof message === "object" &&
    message !== null &&
    typeof (message as { type?: unknown }).type === "string" &&
    (message as { type: string }).type.startsWith("record:")
  );
}
