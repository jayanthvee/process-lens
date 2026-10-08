// The attended executor: replay a compiled recipe against the tab in front of a
// person, one step at a time, with real CDP input and a verified post-condition.
//
// The executor decides nothing. It performs the four actions it was given
// (navigate, fill, select, click) in order, resolves each target by climbing the
// 5-rung ladder, verifies `assert_after` on every step that carries one, and
// stops fail-closed the moment a condition does not hold or the run is aborted.
// It makes no model calls and holds no workflow state of its own.
import type { ValueSource } from "../shared/recipe-types";
import { conditionTimeout, waitForCondition, type ConditionContext } from "./assertions";
import type { CdpTransport } from "./cdp";
import { RunControl } from "./control";
import { dispatchClick, dispatchFill, dispatchNavigate, dispatchSelect } from "./dispatch";
import { readFrameTree, selectFrame, type FrameContexts } from "./frames";
import {
  resolverExpression,
  type ResolveQuery,
  type ResolveResult,
  type ResolveRung,
} from "./ladder";
import { evaluate, resolverCallExpression } from "./page";
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

/** The actions the attended executor performs itself. */
const EXECUTED_ACTIONS = new Set(["navigate", "fill", "select", "click"]);

export interface RunDeps {
  cdp: CdpTransport;
  control?: RunControl;
  onStatus?: (status: RunStatus) => void;
  /** The page-side resolver source. Tests may inject a stub. */
  resolverSource?: string;
  /** Execution contexts, for steps that name a frame. */
  frames?: FrameContexts;
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

let tokenCounter = 0;

function nextToken(): string {
  tokenCounter += 1;
  return `pl-target-${tokenCounter}`;
}

/** Reset the marker counter. Only tests need this. */
export function resetTokens(): void {
  tokenCounter = 0;
}

function originOf(url: string): string {
  try {
    const parsed = new URL(url);
    return `${parsed.protocol}//${parsed.host}`;
  } catch {
    return "";
  }
}

/**
 * Resolve a value source to a concrete value. The attended executor resolves
 * the sources that need no run-time services: a constant, an input column, and a
 * page variable. A named value that the row or the run does not carry is a
 * `park`: the record is missing data, and typing an empty string would submit a
 * blank value to the destination.
 */
export function resolveValue(value: ValueSource | undefined, context: RunContext): unknown {
  if (!value) return undefined;
  const record = value as {
    kind?: string;
    value?: unknown;
    column?: string;
    variable?: string;
    field?: string;
  };
  switch (record.kind) {
    case "constant":
      return record.value;
    case "input": {
      const column = record.column ?? "";
      if (!Object.prototype.hasOwnProperty.call(context.row, column)) {
        throw new ExecutorError(
          `the input column ${JSON.stringify(column)} is missing from this record's row`,
          undefined,
          "park",
        );
      }
      const cell = context.row[column];
      if (cell === undefined || cell === null) {
        throw new ExecutorError(
          `the input column ${JSON.stringify(column)} has no value in this record's row`,
          undefined,
          "park",
        );
      }
      return cell;
    }
    case "page": {
      const variable = record.variable ?? "";
      const bound = context.variables;
      if (!bound || !Object.prototype.hasOwnProperty.call(bound, variable)) {
        throw new ExecutorError(
          `the page variable ${JSON.stringify(variable)} has not been read yet`,
          undefined,
          "park",
        );
      }
      return bound[variable];
    }
    default:
      throw new ExecutorError(
        `the attended executor cannot resolve a value source of kind ${record.kind}`,
        undefined,
        "park",
      );
  }
}

/** Refuse to navigate anywhere outside the recipe's allowed origins. */
export function assertAllowedOrigin(url: string, allowed: string[] | undefined): void {
  const origin = originOf(url);
  if (!origin) throw new ExecutorError(`the navigate step has no usable URL: ${url}`);
  if (allowed && allowed.length > 0 && !allowed.includes(origin)) {
    throw new ExecutorError(
      `refusing to navigate to ${origin}: it is not in allowed_origins`,
      undefined,
      "park",
    );
  }
}

/**
 * The execution context for a step's frame path. A step that names a frame but
 * whose context the page has not reported is a refusal: running it in the top
 * frame would act on the wrong document.
 */
async function contextForFrame(
  cdp: CdpTransport,
  step: ExecutorStep,
  frames: FrameContexts | undefined,
): Promise<number | undefined> {
  if (!step.frame || step.frame.length === 0) return undefined;
  if (!frames) {
    throw new ExecutorError(
      `step ${step.id} targets a frame, but no frame contexts are available`,
      step.id,
      "park",
    );
  }
  const tree = await readFrameTree(cdp);
  const node = tree ? selectFrame(tree, step.frame) : null;
  if (!node) {
    throw new ExecutorError(`the step's frame path did not resolve`, step.id, "park");
  }
  const contextId = frames.contextIdFor(node.frameId);
  if (contextId === undefined) {
    throw new ExecutorError(
      `step ${step.id} targets frame ${node.frameId}, whose execution context the page has not reported`,
      step.id,
      "park",
    );
  }
  return contextId;
}

/** Climb the 5-rung ladder for one step and return the resolver's result. */
async function resolveStep(
  cdp: CdpTransport,
  step: ExecutorStep,
  deps: RunDeps,
  contextId: number | undefined,
  ladder: ResolveRung[] = (step.targets ?? []) as ResolveRung[],
): Promise<{ result: ResolveResult; token: string }> {
  const token = nextToken();
  const query: ResolveQuery = {
    targets: ladder,
    within: step.within ?? null,
    token,
  };
  const expression = resolverCallExpression(deps.resolverSource ?? resolverExpression(), query);
  const result = (await evaluate(
    cdp,
    expression,
    contextId !== undefined ? { contextId } : {},
  )) as ResolveResult;
  return { result, token };
}

function assertionContext(deps: RunDeps): ConditionContext {
  return { resolverSource: deps.resolverSource ?? resolverExpression() };
}

async function verifyCondition(
  cdp: CdpTransport,
  step: ExecutorStep,
  condition: ExecutorStep["assert_after"],
  deps: RunDeps,
  contextId: number | undefined,
): Promise<void> {
  if (!condition) return;
  const timeoutMs = conditionTimeout(condition, step.timeout_ms);
  const outcome = await waitForCondition(cdp, condition, assertionContext(deps), {
    timeoutMs,
    contextId,
  });
  if (!outcome.ok) {
    throw new ExecutorError(
      `the post-condition for step ${step.id} did not hold within ${timeoutMs}ms`,
      step.id,
    );
  }
}

async function runStep(
  cdp: CdpTransport,
  step: ExecutorStep,
  context: RunContext,
  allowedOrigins: string[] | undefined,
  deps: RunDeps,
  trace: { commitInFlight: boolean },
): Promise<StepReport> {
  const contextId = await contextForFrame(cdp, step, deps.frames);
  // A committing action must be marked in flight before its dispatch, so an
  // abort or a debugger detach during it can be reported as an unknown outcome
  // rather than a clean stop.
  const committing = step.commit === true && step.action !== "navigate";
  if (committing) trace.commitInFlight = true;

  if (step.action === "navigate") {
    const url = step.url ?? "";
    assertAllowedOrigin(url, allowedOrigins);
    await dispatchNavigate(cdp, url);
    return { id: step.id, action: step.action, outcome: "done" };
  }

  if (step.action === "fill") {
    const value = resolveValue(step.value, context);
    const text = value === undefined ? "" : String(value);
    if (text === "") {
      throw new ExecutorError(
        `the fill step ${step.id} resolved to an empty value; an empty fill is refused rather than submitted`,
        step.id,
        "park",
      );
    }
    const { result, token } = await resolveStep(cdp, step, deps, contextId);
    if (!result.ok) {
      throw new ExecutorError(
        `the fill target for step ${step.id} could not be resolved: ${result.error ?? "no rung matched"}`,
        step.id,
        "park",
      );
    }
    await dispatchFill(cdp, token, text, {
      clearFirst: step.clear_first,
      contextId,
    });
    await verifyCondition(cdp, step, step.assert_after, deps, contextId);
    if (committing) trace.commitInFlight = false;
    return { id: step.id, action: step.action, outcome: "done", rung: result.rung, by: result.by };
  }

  if (step.action === "select") {
    const value = resolveValue(step.value, context);
    const wanted = value === undefined ? "" : String(value);
    const { result, token } = await resolveStep(cdp, step, deps, contextId);
    if (!result.ok) {
      throw new ExecutorError(
        `the select target for step ${step.id} could not be resolved: ${result.error ?? "no rung matched"}`,
        step.id,
        "park",
      );
    }
    const chosen = await dispatchSelect(cdp, token, wanted, step.option_match ?? "text", contextId);
    if (!chosen.matched && !chosen.custom) {
      throw new ExecutorError(
        `the select step ${step.id} could not choose ${JSON.stringify(wanted)}: ${chosen.reason ?? "no option matched"}`,
        step.id,
        "park",
      );
    }
    if (!chosen.matched && chosen.custom) {
      // A custom combobox: open it, then click the option the resolver finds.
      await dispatchClick(cdp, token, contextId);
      const option = await resolveStep(cdp, step, deps, contextId, [
        { by: "role", role: "option", name: wanted },
        { by: "text_fuzzy", value: wanted },
      ]);
      if (!option.result.ok) {
        throw new ExecutorError(
          `the select step ${step.id} found no option matching ${wanted}`,
          step.id,
          "park",
        );
      }
      await dispatchClick(cdp, option.token, contextId);
    }
    await verifyCondition(cdp, step, step.assert_after, deps, contextId);
    if (committing) trace.commitInFlight = false;
    return { id: step.id, action: step.action, outcome: "done", rung: result.rung, by: result.by };
  }

  // click
  const { result, token } = await resolveStep(cdp, step, deps, contextId);
  if (!result.ok) {
    throw new ExecutorError(
      `the click target for step ${step.id} could not be resolved: ${result.error ?? "no rung matched"}`,
      step.id,
      "park",
    );
  }
  await dispatchClick(cdp, token, contextId);
  await verifyCondition(cdp, step, step.assert_after, deps, contextId);
  if (committing) trace.commitInFlight = false;
  return { id: step.id, action: step.action, outcome: "done", rung: result.rung, by: result.by };
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

      const report = await runStep(deps.cdp, step, context, allowedOrigins, deps, trace);
      reports.push(report);
      completed += 1;
      emit("running", step.id, `${step.action} done`);
    }
    emit("done", null, "run finished");
    return { state: "done", steps: reports };
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    const aborted = control.aborted || /aborted/.test(message);

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
