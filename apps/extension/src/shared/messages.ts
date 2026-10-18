// The message protocol between the popup, the content scripts, and the worker.
import type { Condition, RecordedEvent } from "./recipe-types";
import type { PendingEvent } from "./events";
// Type-only, so the shared protocol does not depend on the worker at run time.
import type { SessionState } from "../background/command-session";

export type RecordingState = "idle" | "recording";

export interface RecorderStatus {
  state: RecordingState;
  count: number;
  startedAt: string | null;
  wsUrl: string;
  wsConnected: boolean;
  /**
   * PL-039: why a start did not happen, in the popup's own words. Null when the worker has
   * nothing to say; the popup shows it in place of the steps until the next start clears it.
   */
  warning: string | null;
}

export const EMPTY_STATUS: RecorderStatus = {
  state: "idle",
  count: 0,
  startedAt: null,
  wsUrl: "",
  wsConnected: false,
  warning: null,
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
  // The document is unloading right after an action it recorded and has not observed yet; the
  // worker keeps that action as the target the arriving page reports against.
  | { type: "record:leaving" }
  // A freshly loaded page asking whether it owes an observation to the page before it.
  | { type: "record:pending-observation" }
  // What the arriving page showed, as the evidence for the action that navigated to it.
  | { type: "record:observation"; stepId: string; observation: Condition }
  | { type: "executor:start"; recipe: unknown; row: Record<string, unknown> }
  | { type: "executor:pause" }
  | { type: "executor:resume" }
  | { type: "executor:abort" }
  | { type: "executor:connect"; hubUrl: string }
  | { type: "executor:disconnect" }
  | { type: "executor:session" }
  | { type: "executor:get" };

/** Sent from the service worker to content scripts and the popup. */
export type FromWorker =
  | { type: "record:state"; status: RecorderStatus }
  | { type: "record:appended"; event: RecordedEvent; status: RecorderStatus }
  | { type: "executor:state"; status: ExecutorStatus }
  | { type: "executor:session-state"; state: SessionState; detail?: string };

/**
 * What the worker answers for any recorder message.
 *
 * `observationTarget` is filled in for `record:pending-observation` and is null otherwise: the
 * step id the loaded page should watch for, or null when nothing is owed across the navigation.
 */
export interface RecorderResponse {
  ok: boolean;
  status: RecorderStatus;
  events: RecordedEvent[];
  observationTarget?: string | null;
}

export function isToWorker(message: unknown): message is ToWorker {
  if (typeof message !== "object" || message === null) return false;
  const type = (message as { type?: unknown }).type;
  if (typeof type !== "string") return false;
  return type.startsWith("record:") || type.startsWith("executor:");
}
