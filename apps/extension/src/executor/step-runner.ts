// The per-step work, split out of the run loop (PL-014).
//
// The attended run loop and the command session need the same step mechanics but
// in different shapes. A run performs a step and immediately verifies its
// post-condition; a command session must be able to *prepare* a step (resolve
// and hit-test the target, and send no input) and only later *dispatch* it, when
// the runner says so. Both call the functions here, so there is one
// implementation of resolving, selecting, filling, clicking, and navigating.
//
// `resolveStepTarget` is the prepare half; `performStep` is the dispatch half.
// `performStep` deliberately does not verify `assert_after` ------ the caller owns
// that, because in command mode the runner sends a separate `check`.
import type { ValueSource } from "../shared/recipe-types";
import type { ConditionContext } from "./assertions";
import type { CdpTransport } from "./cdp";
import { dispatchClick, dispatchFill, dispatchNavigate, dispatchSelect, measureTarget } from "./dispatch";
import { destructiveMatch } from "./guards";
import { readFrameTree, selectFrame, type FrameContexts } from "./frames";
import {
  resolverExpression,
  type ResolveQuery,
  type ResolveResult,
  type ResolveRung,
} from "./ladder";
import { evaluate, markerExpression, resolverCallExpression } from "./page";
import { ExecutorError, type ExecutorStep, type RunContext, type StepReport } from "./types";

/** The seams a single step needs: a CDP channel, the resolver source, and any frame contexts. */
export interface StepRunDeps {
  cdp: CdpTransport;
  /** The page-side resolver source. Tests may inject a stub. */
  resolverSource?: string;
  /** Execution contexts, for steps that name a frame. */
  frames?: FrameContexts;
}

/** A step's target, resolved, hit-tested, and stamped with its marker. */
export interface ResolvedTarget {
  ok: true;
  token: string;
  rung: number;
  by: string;
  tag: string;
  /** The matched element's accessible name, for the destructive-control guard. */
  name: string;
}

/** Why a step's target could not be resolved. */
export interface ResolveFailure {
  ok: false;
  error: string;
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
export async function frameContextId(
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

/** The condition-evaluation context for a run's dependencies. */
export function assertionContext(deps: StepRunDeps): ConditionContext {
  return { resolverSource: deps.resolverSource ?? resolverExpression() };
}

/**
 * Resolve the listbox a custom combobox controls, so the option search can be
 * scoped to it.
 *
 * A control that names its listbox (`aria-controls`/`aria-owns`) is trusted to
 * that element; otherwise the one open `[role=listbox]` is used. Searching the
 * whole document for a `role=option` would let an option belonging to another
 * listbox be clicked ------ the wrong record's value ------ so a listbox that cannot be
 * resolved is a refusal, not a document-wide search.
 */
async function resolveListboxDescriptor(
  cdp: CdpTransport,
  token: string,
  contextId: number | undefined,
): Promise<{ ok: true; descriptor: string } | { ok: false; reason: string }> {
  const expression = `(() => {
    const el = ${markerExpression(token)};
    if (!el) return { ok: false, reason: "the select target is no longer in the document" };
    const firstId = (value) => String(value || "").split(/\\s+/).filter(Boolean)[0] || "";
    const id = firstId(el.getAttribute("aria-controls")) || firstId(el.getAttribute("aria-owns"));
    if (id) {
      if (!document.getElementById(id)) {
        return { ok: false, reason: "the listbox the control names is not in the document" };
      }
      return { ok: true, descriptor: "listbox:" + id };
    }
    const open = Array.from(document.querySelectorAll('[role="listbox"]')).filter((box) => {
      if (box.hasAttribute("hidden")) return false;
      const style = window.getComputedStyle ? window.getComputedStyle(box) : null;
      return !style || (style.display !== "none" && style.visibility !== "hidden");
    });
    if (open.length === 0) return { ok: false, reason: "the combobox has no listbox to search" };
    if (open.length > 1) return { ok: false, reason: "more than one listbox is open" };
    const box = open[0];
    if (!box.id) {
      return { ok: false, reason: "the open listbox has no id to scope the option search to" };
    }
    return { ok: true, descriptor: "listbox:" + box.id };
  })()`;
  const result = (await evaluate(
    cdp,
    expression,
    contextId !== undefined ? { contextId } : {},
  )) as { ok?: boolean; descriptor?: string; reason?: string } | undefined;
  if (result?.ok === true && typeof result.descriptor === "string") {
    return { ok: true, descriptor: result.descriptor };
  }
  return { ok: false, reason: result?.reason ?? "the combobox's listbox could not be resolved" };
}

/** Climb the 5-rung ladder for one step and return the resolver's result. */
async function resolveStep(
  cdp: CdpTransport,
  step: ExecutorStep,
  deps: StepRunDeps,
  contextId: number | undefined,
  ladder: ResolveRung[] = (step.targets ?? []) as ResolveRung[],
  preset?: ResolvedTarget,
): Promise<{ result: ResolveResult; token: string }> {
  // A target may be handed in already resolved and hit-tested by the guard phase. Resolving it a
  // second time doubles the CDP round trips for every step, and those round trips are the dominant
  // cost of a command against the runner's five-second ack budget. Reusing the guard's element is
  // also the more honest thing to act on: it is exactly the element the guards approved.
  if (preset) {
    return {
      result: {
        ok: true,
        rung: preset.rung,
        by: preset.by,
        name: preset.name,
        marker: preset.token,
        x: 0,
        y: 0,
        tag: preset.tag,
        attempts: [],
      },
      token: preset.token,
    };
  }
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

/**
 * Resolve and hit-test one step's target, and attach the marker. Sends no input:
 * this is the `prepare` half of a step, so the runner can learn where a click
 * would land before it authorises the click.
 */
export async function resolveStepTarget(
  step: ExecutorStep,
  deps: StepRunDeps,
): Promise<ResolvedTarget | ResolveFailure> {
  try {
    const contextId = await frameContextId(deps.cdp, step, deps.frames);
    if ((step.targets ?? []).length === 0) {
      return { ok: false, error: `step ${step.id} has no target ladder` };
    }
    const { result, token } = await resolveStep(deps.cdp, step, deps, contextId);
    if (!result.ok) {
      return { ok: false, error: result.error ?? "no rung matched" };
    }
    // A click lands at a measured box center, so its target is hit-tested here.
    // A fill or a select acts on the element itself and needs no box; measuring
    // one would refuse an off-screen field that a focus can still reach.
    if (step.action === "click") {
      try {
        await measureTarget(deps.cdp, token, contextId);
      } catch (error) {
        return { ok: false, error: error instanceof Error ? error.message : String(error) };
      }
    }
    return {
      ok: true,
      token,
      rung: result.rung,
      by: result.by,
      tag: result.tag,
      name: result.name ?? "",
    };
  } catch (error) {
    return { ok: false, error: error instanceof Error ? error.message : String(error) };
  }
}

/**
 * The accessible name carried by a resolver result. `resolveStep` returns a `ResolveResult`, whose
 * `name` is set by the page-side resolver when a rung matched.
 */
function nameOf(result: ResolveResult): string {
  const name = (result as { name?: unknown }).name;
  return typeof name === "string" ? name : "";
}

/**
 * Perform one step's action: navigate, fill, select, or click. It does NOT
 * verify `assert_after` ------ the caller does that, because in command mode a
 * separate `check` command carries the post-condition.
 *
 * `onInputDispatched` fires once, immediately after the trusted input event ------ for
 * a click after the mouse release, for a fill or a select after the last
 * keystroke or change. It is the extension's acknowledgement that the
 * destination has been handed the input (R2), and it is never fired for a step
 * that refused before sending anything.
 */
export async function performStep(
  step: ExecutorStep,
  context: RunContext,
  allowedOrigins: string[] | undefined,
  deps: StepRunDeps,
  onInputDispatched?: () => void,
  preset?: ResolvedTarget,
): Promise<StepReport> {
  const contextId = await frameContextId(deps.cdp, step, deps.frames);

  if (step.action === "navigate") {
    const url = step.url ?? "";
    assertAllowedOrigin(url, allowedOrigins);
    await dispatchNavigate(deps.cdp, url);
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
    const { result, token } = await resolveStep(deps.cdp, step, deps, contextId, undefined, preset);
    if (!result.ok) {
      throw new ExecutorError(
        `the fill target for step ${step.id} could not be resolved: ${result.error ?? "no rung matched"}`,
        step.id,
        "park",
      );
    }
    await dispatchFill(deps.cdp, token, text, {
      clearFirst: step.clear_first,
      contextId,
    });
    onInputDispatched?.();
    return { id: step.id, action: step.action, outcome: "done", rung: result.rung, by: result.by };
  }

  if (step.action === "select") {
    const value = resolveValue(step.value, context);
    const wanted = value === undefined ? "" : String(value);
    const { result, token } = await resolveStep(deps.cdp, step, deps, contextId, undefined, preset);
    if (!result.ok) {
      throw new ExecutorError(
        `the select target for step ${step.id} could not be resolved: ${result.error ?? "no rung matched"}`,
        step.id,
        "park",
      );
    }
    const chosen = await dispatchSelect(deps.cdp, token, wanted, step.option_match ?? "text", contextId);
    if (!chosen.matched && !chosen.custom) {
      throw new ExecutorError(
        `the select step ${step.id} could not choose ${JSON.stringify(wanted)}: ${chosen.reason ?? "no option matched"}`,
        step.id,
        "park",
      );
    }
    if (!chosen.matched && chosen.custom) {
      // A custom combobox: open it, then click the option inside the listbox it
      // owns. The search is `role=option` only, scoped to that listbox: a
      // document-wide search, or a fuzzy text fallback, could click an option of
      // some other listbox on the page.
      await dispatchClick(deps.cdp, token, contextId);
      const listbox = await resolveListboxDescriptor(deps.cdp, token, contextId);
      if (!listbox.ok) {
        throw new ExecutorError(
          `the select step ${step.id} could not find the listbox for ${wanted}: ${listbox.reason}`,
          step.id,
          "park",
        );
      }
      const option = await resolveStep(deps.cdp, step, deps, contextId, [
        { by: "role", role: "option", name: wanted, within: listbox.descriptor },
      ]);
      if (!option.result.ok) {
        throw new ExecutorError(
          `the select step ${step.id} found no option matching ${wanted} inside its listbox`,
          step.id,
          "park",
        );
      }
      await dispatchClick(deps.cdp, option.token, contextId);
    }
    onInputDispatched?.();
    return { id: step.id, action: step.action, outcome: "done", rung: result.rung, by: result.by };
  }

  // click
  const { result, token } = await resolveStep(deps.cdp, step, deps, contextId, undefined, preset);
  if (!result.ok) {
    throw new ExecutorError(
      `the click target for step ${step.id} could not be resolved: ${result.error ?? "no rung matched"}`,
      step.id,
      "park",
    );
  }
  // The destructive-control invariant is checked HERE, at the last moment before the click, using
  // the accessible name of the element the ladder actually matched. That covers both the command
  // mode (whose handler checks it earlier, on the same name) and the local test run, and it cannot
  // be bypassed by a recipe that points at a bare CSS selector.
  if (!step.allow_destructive_control) {
    const blocked = destructiveMatch(nameOf(result));
    if (blocked) {
      throw new ExecutorError(
        `refusing to click a destructive control ("${blocked}"): set allow_destructive_control on step ${step.id} to permit it`,
        step.id,
        "park",
      );
    }
  }
  await dispatchClick(deps.cdp, token, contextId);
  onInputDispatched?.();
  return { id: step.id, action: step.action, outcome: "done", rung: result.rung, by: result.by };
}
