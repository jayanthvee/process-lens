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
 * page variable. Anything else is reported, never guessed.
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
    case "input":
      return context.row[record.column ?? ""];
    case "page":
      return context.variables?.[record.variable ?? ""];
    default:
      throw new ExecutorError(
        `the attended executor cannot resolve a value source of kind ${record.kind}`,
      );
  }
}

/** Refuse to navigate anywhere outside the recipe's allowed origins. */
export function assertAllowedOrigin(url: string, allowed: string[] | undefined): void {
  const origin = originOf(url);
  if (!origin) throw new ExecutorError(`the navigate step has no usable URL: ${url}`);
  if (allowed && allowed.length > 0 && !allowed.includes(origin)) {
    throw new ExecutorError(`refusing to navigate to ${origin}: it is not in allowed_origins`);
  }
}

async function contextForFrame(
  cdp: CdpTransport,
  step: ExecutorStep,
  frames: FrameContexts | undefined,
): Promise<number | undefined> {
  if (!step.frame || step.frame.length === 0 || !frames) return undefined;
  const tree = await readFrameTree(cdp);
  if (!tree) return undefined;
  const node = selectFrame(tree, step.frame);
  if (!node) throw new ExecutorError(`the step's frame path did not resolve`, step.id);
  return frames.contextIdFor(node.frameId);
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
): Promise<StepReport> {
  const contextId = await contextForFrame(cdp, step, deps.frames);

  if (step.action === "navigate") {
    const url = step.url ?? "";
    assertAllowedOrigin(url, allowedOrigins);
    await dispatchNavigate(cdp, url);
    return { id: step.id, action: step.action, outcome: "done" };
  }

  if (step.action === "fill") {
    const value = resolveValue(step.value, context);
    const { result, token } = await resolveStep(cdp, step, deps, contextId);
    if (!result.ok) {
      throw new ExecutorError(
        `the fill target for step ${step.id} could not be resolved: ${result.error ?? "no rung matched"}`,
        step.id,
      );
    }
    await dispatchFill(cdp, token, value === undefined ? "" : String(value), {
      clearFirst: step.clear_first,
      contextId,
    });
    await verifyCondition(cdp, step, step.assert_after, deps, contextId);
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
      );
    }
    const chosen = await dispatchSelect(cdp, token, wanted, step.option_match ?? "text", contextId);
    if (!chosen.matched) {
      // A custom combobox: open it, then click the option the resolver finds.
      await dispatchClick(cdp, token, { x: result.x, y: result.y }, contextId);
      const option = await resolveStep(cdp, step, deps, contextId, [
        { by: "role", role: "option", name: wanted },
        { by: "text_fuzzy", value: wanted },
      ]);
      if (!option.result.ok) {
        throw new ExecutorError(
          `the select step ${step.id} found no option matching ${wanted}`,
          step.id,
        );
      }
      await dispatchClick(
        cdp,
        option.token,
        { x: option.result.x, y: option.result.y },
        contextId,
      );
    }
    await verifyCondition(cdp, step, step.assert_after, deps, contextId);
    return { id: step.id, action: step.action, outcome: "done", rung: result.rung, by: result.by };
  }

  // click
  const { result, token } = await resolveStep(cdp, step, deps, contextId);
  if (!result.ok) {
    throw new ExecutorError(
      `the click target for step ${step.id} could not be resolved: ${result.error ?? "no rung matched"}`,
      step.id,
    );
  }
  await dispatchClick(cdp, token, { x: result.x, y: result.y }, contextId);
  await verifyCondition(cdp, step, step.assert_after, deps, contextId);
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
  const steps = recipe.steps ?? [];
  const allowedOrigins = recipe.meta?.allowed_origins;
  const reports: StepReport[] = [];
  let completed = 0;

  const emit = (state: RunState, stepId: string | null, message: string): void => {
    deps.onStatus?.({
      state,
      stepId,
      completed,
      total: steps.length,
      message,
    });
  };

  emit("running", null, `running ${steps.length} step(s)`);

  try {
    for (const step of steps) {
      await control.checkpoint();
      emit("running", step.id, `running ${step.action}`);

      if (!EXECUTED_ACTIONS.has(step.action)) {
        reports.push({
          id: step.id,
          action: step.action,
          outcome: "skipped",
          detail: "the attended executor performs navigate, fill, select, and click",
        });
        completed += 1;
        continue;
      }

      const report = await runStep(deps.cdp, step, context, allowedOrigins, deps);
      reports.push(report);
      completed += 1;
      emit("running", step.id, `${step.action} done`);
    }
    emit("done", null, "run finished");
    return { state: "done", steps: reports };
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    const aborted = control.aborted || /aborted/.test(message);
    const state = aborted ? "aborted" : "failed";
    emit(state, null, message);
    return { state, steps: reports, error: message };
  }
}
