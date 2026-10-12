// The attended executor: replay a compiled recipe against the tab in front of a
// person, one step at a time, with real CDP input and a verified post-condition.
//
// The executor decides nothing. It performs the four actions it was given
// (navigate, fill, select, click) in order, resolves each target by climbing the
// 5-rung ladder, verifies `assert_after` on every step that carries one, and
// stops fail-closed the moment a condition does not hold or the run is aborted.
// It makes no model calls and holds no workflow state of its own.
//
// PL-014 split the per-step mechanics into `step-runner.ts`, so the command
// session can prepare a step (resolve the target, send no input) and dispatch it
// separately. This file keeps the run loop, the recipe validation, and the
// post-condition verification.
import type { CdpTransport } from "./cdp";
import { RunControl } from "./control";
import { conditionTimeout, waitForCondition, type ConditionContext } from "./assertions";
import {
  assertionContext,
  frameContextId,
  performStep,
  resolveStepTarget,
  type StepRunDeps,
} from "./step-runner";
import {
  ExecutorError,
  type ExecutorRecipe,
  type ExecutorStep,
  type FailureDisposition,
  type RunContext,
  type RunState,
  type RunStatus,
  type StepReport,
} from "./types";

export {
  assertAllowedOrigin,
  frameContextId,
  resetTokens,
  resolveValue,
  type ResolveFailure,
  type ResolvedTarget,
  type StepRunDeps,
} from "./step-runner";

/** The actions the attended executor performs itself. */
const EXECUTED_ACTIONS = new Set(["navigate", "fill", "select", "click"]);

export interface RunDeps {
  cdp: CdpTransport;
  control?: RunControl;
  onStatus?: (status: RunStatus) => void;
  /** The page-side resolver source. Tests may inject a stub. */
  resolverSource?: string;
  /** Execution contexts, for steps that name a frame. */
  frames?: StepRunDeps["frames"];
}

export interface RunSummary {
  state: "done" | "failed" | "aborted";
  steps: StepReport[];
  error?: string;
  /** How the ledger should treat a record whose run stopped. */
  disposition?: FailureDisposition;
  /**
   * True when the run was aborted while a commit step was in flight, so whether
   * the destination took the change is unknown and must be reconciled.
   */
  commitOutcomeUnknown?: boolean;
}

/**
 * Validate a recipe before any step runs. Fail-closed: a recipe with an action
 * the attended executor does not perform is refused outright rather than run
 * part-way. Returns a list of problems; an empty list means it is runnable.
 */
export function validateRecipe(recipe: ExecutorRecipe | undefined | null): string[] {
  const problems: string[] = [];
  if (!recipe || typeof recipe !== "object") {
    return ["no recipe was supplied"];
  }
  if (!Array.isArray(recipe.steps) || recipe.steps.length === 0) {
    return ["the recipe has no steps"];
  }
  for (const step of recipe.steps) {
    const action = step?.action;
    if (typeof action !== "string" || !EXECUTED_ACTIONS.has(action)) {
      problems.push(
        `step ${step?.id ?? "?"} has action ${JSON.stringify(action)}, which the attended executor does not perform; it performs navigate, fill, select, and click only`,
      );
      continue;
    }
    if ((action === "click" || action === "fill" || action === "select") && !(step.targets ?? []).length) {
      problems.push(`step ${step.id} has action ${action} but no target ladder`);
    }
    if (action === "navigate" && !step.url) {
      problems.push(`step ${step.id} is a navigate with no url`);
    }
  }
  return problems;
}

function conditionContext(deps: RunDeps): ConditionContext {
  return assertionContext(deps);
}

/**
 * Verify a step's `assert_after`, polling the page up to its timeout. A
 * condition that never holds throws, so the run stops fail-closed rather than
 * reporting a commit the destination may not have accepted.
 */
async function verifyCondition(
  cdp: CdpTransport,
  step: ExecutorStep,
  condition: ExecutorStep["assert_after"],
  deps: RunDeps,
  aborted?: () => boolean,
): Promise<void> {
  if (!condition) return;
  const contextId = await frameContextId(cdp, step, deps.frames);
  const timeoutMs = conditionTimeout(condition, step.timeout_ms);
  const outcome = await waitForCondition(cdp, condition, conditionContext(deps), {
    timeoutMs,
    ...(aborted ? { aborted } : {}),
    ...(contextId !== undefined ? { contextId } : {}),
  });
  if (!outcome.ok) {
    // An abort ends the poll immediately; it is reported as an abort, not as a
    // condition that failed to hold.
    if (outcome.aborted === true) {
      throw new ExecutorError(
        `the run was aborted while waiting for the post-condition of step ${step.id}`,
        step.id,
      );
    }
    throw new ExecutorError(
      `the post-condition for step ${step.id} did not hold within ${timeoutMs}ms`,
      step.id,
    );
  }
}

/**
 * Replay a recipe against the attached tab. Returns a summary; it never throws
 * for an ordinary step failure — the run stops and the summary says why.
 */
export async function runRecipe(
  recipe: ExecutorRecipe,
  context: RunContext,
  deps: RunDeps,
): Promise<RunSummary> {
  const control = deps.control ?? new RunControl();
  const steps = recipe?.steps ?? [];
  const allowedOrigins = recipe?.meta?.allowed_origins;
  const reports: StepReport[] = [];
  const trace = { commitInFlight: false };
  let completed = 0;
  let currentStepId: string | null = null;

  const emit = (state: RunState, stepId: string | null, message: string): void => {
    deps.onStatus?.({
      state,
      stepId,
      completed,
      total: steps.length,
      message,
    });
  };

  // Fail-closed: a recipe the executor cannot perform in full is refused before
  // step 1 runs, so a run never stops half-way through an unknown action.
  const problems = validateRecipe(recipe);
  if (problems.length > 0) {
    const message = `refusing to start: ${problems.join("; ")}`;
    emit("failed", null, message);
    return { state: "failed", steps: [], error: message, disposition: "park" };
  }

  emit("running", null, `running ${steps.length} step(s)`);

  try {
    for (const step of steps) {
      await control.checkpoint();
      currentStepId = step.id;
      emit("running", step.id, `running ${step.action}`);

      // A committing action must be marked in flight before its dispatch, so an
      // abort or a debugger detach during it can be reported as an unknown
      // outcome rather than a clean stop.
      const committing = step.commit === true && step.action !== "navigate";
      if (committing) {
        // Resolve the target first: a locator that cannot be resolved must never
        // be reported as a commit the destination may have seen.
        const resolved = await resolveStepTarget(step, deps);
        if (!resolved.ok) {
          throw new ExecutorError(
            `the ${step.action} target for step ${step.id} could not be resolved: ${resolved.error}`,
            step.id,
            "park",
          );
        }
        trace.commitInFlight = true;
      }

      const report = await performStep(step, context, allowedOrigins, deps);
      await verifyCondition(deps.cdp, step, step.assert_after, deps, () => control.aborted);
      if (committing) trace.commitInFlight = false;
      reports.push(report);
      completed += 1;
      emit("running", step.id, `${step.action} done`);
    }
    emit("done", null, "run finished");
    return { state: "done", steps: reports };
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    // The abort is `control.aborted` and nothing else: the run stops aborting the
    // moment the person clicks cancel (or the debugger detaches), never because
    // a step's message happened to contain the word "aborted".
    const aborted = control.aborted;

    if (aborted) {
      // An abort during a commit's dispatch means the destination may or may not
      // have taken the change; the record is parked for reconciliation, not
      // retried blindly.
      const unknown = trace.commitInFlight;
      const reason = control.abortReason;
      const finalMessage = unknown
        ? `commit outcome unknown: the run stopped while commit step ${currentStepId ?? "?"} was in flight` +
          (reason ? ` (${reason})` : "") +
          "; reconcile the destination before any retry"
        : (reason ?? message);
      emit("aborted", null, finalMessage);
      return {
        state: "aborted",
        steps: reports,
        error: finalMessage,
        disposition: "park",
        commitOutcomeUnknown: unknown,
      };
    }

    const disposition: FailureDisposition =
      error instanceof ExecutorError ? error.disposition : "park";
    emit("failed", null, message);
    return { state: "failed", steps: reports, error: message, disposition };
  }
}
