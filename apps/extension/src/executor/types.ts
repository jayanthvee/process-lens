// The shapes the executor reads from a recipe, and the results it reports back.
//
// These mirror the parts of the recipe format the executor acts on: the four
// actions it can perform (navigate, fill, select, click), the ordered target
// ladder, the post-condition it verifies, and the value sources it can resolve.
import type { Condition, FrameStep, LocatorRung, ValueSource } from "../shared/recipe-types";

/** The actions the attended executor performs itself. */
export type ExecutedAction = "navigate" | "fill" | "select" | "click";

/** One recipe step, narrowed to the fields the executor reads. */
export interface ExecutorStep {
  id: string;
  action: string;
  name?: string;
  commit?: boolean;
  assert_after?: Condition;
  timeout_ms?: number;
  on_failure?: "park" | "fail";
  targets?: LocatorRung[];
  within?: string;
  frame?: FrameStep[];
  value?: ValueSource;
  clear_first?: boolean;
  option_match?: "text" | "value" | "index";
  url?: string;
  condition?: Condition;
}

/** A whole recipe, as far as the executor is concerned. */
export interface ExecutorRecipe {
  format_version: number;
  meta: {
    name?: string;
    allowed_origins?: string[];
    [key: string]: unknown;
  };
  steps: ExecutorStep[];
}

/** The input row a recipe runs against, and any variables already bound. */
export interface RunContext {
  row: Record<string, unknown>;
  variables?: Record<string, unknown>;
}

/** How a step ended, for the status stream and the run log. */
export type StepOutcome = "done" | "failed" | "skipped";

export interface StepReport {
  id: string;
  action: string;
  outcome: StepOutcome;
  rung?: number;
  by?: string;
  detail?: string;
}

export type RunState = "idle" | "running" | "paused" | "aborted" | "done" | "failed";

/**
 * How a run that stopped should be treated by the ledger.
 *
 * `park` means the record needs a person: a bad input, a password field, an
 * unreachable target, an unknown frame, or a condition that never held. `fail`
 * is reserved for a provable rejection; the extension does not decide that, so
 * it parks by default. Neither is "retry blindly".
 */
export type FailureDisposition = "park" | "fail";

export interface RunStatus {
  state: RunState;
  /** The id of the step being run, or the last one run. */
  stepId: string | null;
  /** How many steps have finished. */
  completed: number;
  total: number;
  message: string;
}

/**
 * Thrown when a step cannot be completed. A `park` disposition means a person
 * must look at the record; the caller must not retry it blindly.
 */
export class ExecutorError extends Error {
  constructor(
    message: string,
    readonly stepId?: string,
    readonly disposition: FailureDisposition = "park",
  ) {
    super(message);
    this.name = "ExecutorError";
  }
}
