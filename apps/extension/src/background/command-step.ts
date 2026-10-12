// The body of one command's step, split out of the service worker (PL-014b item 10).
//
// The service worker keeps what only it can know: the bound tab, the debugger attach, the error
// classification and the `attached` flag. The step itself — the guards, the target resolve, the
// destructive check, and the perform — lives here, dependency-injected, so a test can drive the
// REAL prepare path against a fake CDP and prove that a prepare sends no `Input.*` at all, rather
// than re-implementing the path in the test and testing the copy.
//
// The split is a seam, not a rewrite: a prepare does exactly what the service worker used to do
// before its early return, and a dispatch does exactly what it used to do after it.
import type { CdpTransport } from "../executor/cdp";
import { checkChallengeOrLogin, checkDestructive, checkOrigin } from "../executor/guards";
import {
  performStep,
  resolveStepTarget,
  type ResolvedTarget,
  type StepRunDeps,
} from "../executor/step-runner";
import { ExecutorError, type ExecutorStep, type StepReport } from "../executor/types";

/** What one command step's body needs from its owner. */
export interface CommandStepOptions {
  cdp: CdpTransport;
  allowedOrigins?: string[];
  /** Runs immediately before every trusted input; throws to refuse (F1, B3). */
  beforeInput?: () => void;
  /** Fires once, after the first trusted input event (R2). */
  onFirstInput?: () => void;
}

function stepDeps(opts: CommandStepOptions): StepRunDeps {
  // Command mode: the runner names the deadline for every step it sends, so the extension never
  // falls back to a default of its own for a command-mode step (R4).
  return {
    cdp: opts.cdp,
    commandMode: true,
    ...(opts.beforeInput ? { beforeInput: opts.beforeInput } : {}),
  };
}

/**
 * The origin and challenge/login guards, judged against the page the tab is on NOW.
 *
 * R6: each guard that has a run-level meaning carries its event on the error, so the session sends
 * the event before the refusal it causes. Without this a challenge would park the record instead
 * of pausing the run for a person.
 */
async function guardsOnCurrentPage(step: ExecutorStep, opts: CommandStepOptions): Promise<void> {
  const guardContext = { cdp: opts.cdp, step, allowedOrigins: opts.allowedOrigins };
  const origin = await checkOrigin(guardContext);
  if (!origin.ok) {
    throw new ExecutorError(origin.reason, step.id, "park", origin.event);
  }
  const challenge = await checkChallengeOrLogin(guardContext);
  if (!challenge.ok) {
    throw new ExecutorError(challenge.reason, step.id, "park", challenge.event);
  }
}

/**
 * The half of a step that sends no input: the guards, the target resolve, and the destructive check.
 *
 * A `navigate` is the exception (PL-014b item 5). Its target URL is checked against
 * `allowed_origins` inside the navigate itself, and its pre-command origin and challenge/login
 * guards would judge the page the tab is LEAVING — after an off-origin park that is the previous
 * record's page, so the next record's opening navigate was refused for a page it was about to
 * leave. A navigate therefore runs no pre-command guard here; the guards run on the page it lands
 * on instead, in `dispatchCommandStep`.
 */
async function guardAndResolve(
  step: ExecutorStep,
  opts: CommandStepOptions,
): Promise<{ rung?: number; resolvedTarget?: ResolvedTarget }> {
  // `navigate` carries a url and no target ladder, so it is never resolved and never guarded here.
  if (step.action === "navigate") return {};

  await guardsOnCurrentPage(step, opts);

  const resolved = await resolveStepTarget(step, stepDeps(opts));
  if (!resolved.ok) {
    throw new ExecutorError(
      `the ${step.action} target could not be resolved: ${resolved.error}`,
      step.id,
      "park",
    );
  }

  // The destructive check runs AFTER resolution on purpose: it judges the accessible name of the
  // control the ladder actually matched, not what the ladder happened to declare. A recipe can
  // point at a plain `#danger` and the guard still sees "Delete lead".
  const destructive = checkDestructive(
    { cdp: opts.cdp, step, allowedOrigins: opts.allowedOrigins },
    resolved.name,
  );
  if (!destructive.ok) {
    throw new ExecutorError(destructive.reason, step.id, "park", destructive.event);
  }
  return { rung: resolved.rung, resolvedTarget: resolved };
}

/**
 * The prepare phase of one command's step: the guards, the resolve, and the destructive check, and
 * nothing else.
 *
 * R2: a prepare proves the target resolves, is hit-testable and passes every guard, and sends NO
 * input; the runner writes `submitting` only after this returns. Anything typed or clicked here
 * would make a mere locator failure look like a commit attempt and dispatch every commit twice.
 */
export async function prepareCommandStep(step: unknown, opts: CommandStepOptions): Promise<StepReport> {
  const executorStep = step as ExecutorStep;
  const { rung } = await guardAndResolve(executorStep, opts);
  return { id: executorStep.id, action: executorStep.action, outcome: "done", rung };
}

/**
 * The dispatch phase: the same proof as a prepare, then the real perform.
 *
 * The element the guards just approved is the one acted on: the resolved target is handed to the
 * perform, so there is no second resolve and no second measure — those round trips are the
 * dominant cost of a command against the runner's five-second ack budget.
 */
export async function dispatchCommandStep(step: unknown, opts: CommandStepOptions): Promise<StepReport> {
  const executorStep = step as ExecutorStep;
  const deps = stepDeps(opts);

  if (executorStep.action === "navigate") {
    // Item 5: no pre-command guard — the target URL is checked against `allowed_origins` inside the
    // navigate. The guards then judge the page the navigate LANDS on, so a permitted navigate
    // cannot be refused for the page the tab is leaving.
    const report = await performStep(
      executorStep,
      { row: {} },
      opts.allowedOrigins,
      deps,
      opts.onFirstInput,
    );
    await guardsOnCurrentPage(executorStep, opts);
    return report;
  }

  const { resolvedTarget } = await guardAndResolve(executorStep, opts);
  // The same element the guards approved, handed straight to the perform.
  if (resolvedTarget) {
    return await performStep(
      executorStep,
      { row: {} },
      opts.allowedOrigins,
      deps,
      opts.onFirstInput,
      resolvedTarget,
    );
  }
  return await performStep(executorStep, { row: {} }, opts.allowedOrigins, deps, opts.onFirstInput);
}
