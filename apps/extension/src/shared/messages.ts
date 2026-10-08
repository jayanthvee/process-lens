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

/** Where an attended execution is, for the status line. */
export type ExecutorState = "idle" | "running" | "paused" | "aborted" | "done" | "failed";

export interface ExecutorStatus {
  state: ExecutorState;
  stepId: string | null;
  completed: number;
  total: number;
  message: string;
}

export const EMPTY_EXECUTOR_STATUS: ExecutorStatus = {
  state: "idle",
  stepId: null,
  completed: 0,
  total: 0,
  message: "",
};

/** Sent from a content script or the popup to the service worker. */
export type ToWorker =
  | { type: "record:event"; event: PendingEvent }
  | { type: "record:start" }
  | { type: "record:stop" }
  | { type: "record:clear" }
  | { type: "record:get" }
  | { type: "record:set-ws"; wsUrl: string }
  | { type: "executor:start"; recipe: unknown; row: Record<string, unknown> }
  | { type: "executor:pause" }
  | { type: "executor:resume" }
  | { type: "executor:abort" }
  | { type: "executor:get" };

/** Sent from the service worker to content scripts and the popup. */
export type FromWorker =
  | { type: "record:state"; status: RecorderStatus }
  | { type: "record:appended"; event: RecordedEvent; status: RecorderStatus }
  | { type: "executor:state"; status: ExecutorStatus };

export function isToWorker(message: unknown): message is ToWorker {
  if (typeof message !== "object" || message === null) return false;
  const type = (message as { type?: unknown }).type;
  if (typeof type !== "string") return false;
  return type.startsWith("record:") || type.startsWith("executor:");
}
