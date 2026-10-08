// Assertion verification: does the page satisfy the step's post-condition?
//
// A commit step carries `assert_after`, a condition on what the page shows once
// the destination accepted the change. The executor polls that condition up to
// its timeout and reports the answer; it never sleeps a fixed amount, and it is
// fail-closed — a condition that is still false at the timeout is a failure the
// caller must handle, not a reason to continue.
import type { Condition } from "../shared/recipe-types";
import type { CdpTransport } from "./cdp";
import { evaluate, resolverCallExpression } from "./page";

/** How long to wait between checks. */
export const DEFAULT_POLL_MS = 250;
/** How long to keep checking when neither the condition nor the step sets one. */
export const DEFAULT_TIMEOUT_MS = 8000;

export interface ConditionContext {
  /** The page-side resolver source, injected into the evaluated expression. */
  resolverSource: string;
  /** A `value_equals` condition compares against this already-resolved value. */
  valueEquals?: string | null;
}

/** A condition's own timeout, or the fallback when it carries none. */
export function conditionTimeout(
  condition: Condition | undefined,
  fallbackMs: number = DEFAULT_TIMEOUT_MS,
): number {
  const own = condition ? (condition as { timeout_ms?: unknown }).timeout_ms : undefined;
  return typeof own === "number" && own > 0 ? own : fallbackMs;
}

/** The selector a condition's `element_present`/`element_absent` carries. */
function selectorOf(condition: Condition, key: string): unknown {
  return (condition as Record<string, unknown>)[key];
}

/**
 * A page expression that evaluates the condition to a boolean.
 *
 * The predicate set is closed: `text_visible`, `text_absent`, `url_matches`,
 * `element_present`, `element_absent`, `value_equals`. Anything else raises, so
 * a condition the executor does not understand can never pass silently.
 */
export function conditionExpression(condition: Condition, ctx: ConditionContext): string {
  const record = condition as Record<string, unknown>;
  const normal = (value: unknown): string =>
    `String(${JSON.stringify(value)}).replace(/\\s+/g, " ").trim()`;

  if ("text_visible" in record) {
    return `(() => { const text = document.body ? document.body.innerText || document.body.textContent || "" : ""; return text.replace(/\\s+/g, " ").trim().includes(${normal(
      record["text_visible"],
    )}); })()`;
  }
  if ("text_absent" in record) {
    return `(() => { const text = document.body ? document.body.innerText || document.body.textContent || "" : ""; return !text.replace(/\\s+/g, " ").trim().includes(${normal(
      record["text_absent"],
    )}); })()`;
  }
  if ("url_matches" in record) {
    return `(() => { try { return new RegExp(${JSON.stringify(
      record["url_matches"],
    )}).test(location.href); } catch { return false; } })()`;
  }
  if ("element_present" in record || "element_absent" in record) {
    const key = "element_present" in record ? "element_present" : "element_absent";
    const selector = selectorOf(condition, key);
    const negate = key === "element_absent" ? "!" : "";
    const call = resolverCallExpression(ctx.resolverSource, {
      ...(selector as Record<string, unknown>),
      token: "condition-probe",
    });
    return `(() => { const result = ${call}; return ${negate}(result && result.ok === true); })()`;
  }
  if ("value_equals" in record) {
    const spec = record["value_equals"] as {
      selector?: { targets?: unknown; within?: unknown };
      case_insensitive?: boolean;
    };
    const expected = ctx.valueEquals ?? "";
    const insensitive = spec?.case_insensitive === true;
    const call = resolverCallExpression(ctx.resolverSource, {
      targets: spec?.selector?.targets ?? [],
      within: spec?.selector?.within ?? null,
      token: "condition-probe",
    });
    return `(() => {
      const result = ${call};
      if (!result || result.ok !== true) return false;
      const el = document.querySelector('[data-pl-target="condition-probe"]');
      if (!el) return false;
      const actual = String(el.value !== undefined ? el.value : el.textContent || "");
      const a = ${insensitive ? "actual.trim().toLowerCase()" : "actual.trim()"};
      const b = ${insensitive ? normal(expected) + ".toLowerCase()" : normal(expected)};
      return a === b;
    })()`;
  }
  throw new Error(`the executor does not understand the condition: ${JSON.stringify(condition)}`);
}

export interface WaitOptions {
  timeoutMs?: number;
  pollMs?: number;
  sleep?: (ms: number) => Promise<void>;
  now?: () => number;
  contextId?: number;
}

export interface WaitResult {
  ok: boolean;
  polls: number;
  elapsedMs: number;
}

const defaultSleep = (ms: number): Promise<void> =>
  new Promise((done) => setTimeout(done, ms));

/**
 * Poll a condition until it holds or the timeout elapses. Checks immediately,
 * then waits `pollMs` between checks. Returns `ok: false` on timeout.
 */
export async function waitForCondition(
  cdp: CdpTransport,
  condition: Condition,
  ctx: ConditionContext,
  options: WaitOptions = {},
): Promise<WaitResult> {
  const timeoutMs = options.timeoutMs ?? conditionTimeout(condition);
  const pollMs = options.pollMs ?? DEFAULT_POLL_MS;
  const sleep = options.sleep ?? defaultSleep;
  const now = options.now ?? (() => Date.now());

  const started = now();
  let polls = 0;

  for (;;) {
    polls += 1;
    const expression = conditionExpression(condition, ctx);
    const ok =
      (await evaluate(cdp, expression, options.contextId !== undefined ? { contextId: options.contextId } : {})) ===
      true;
    const elapsedMs = now() - started;
    if (ok) return { ok: true, polls, elapsedMs };
    if (elapsedMs >= timeoutMs) return { ok: false, polls, elapsedMs };
    await sleep(pollMs);
  }
}
