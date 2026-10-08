// Page evaluation: the one place the executor runs JavaScript in the target.
//
// Everything that must happen "inside the page" — resolving a target, focusing
// a field, reading an assertion — goes through `evaluate`, which wraps
// `Runtime.evaluate` and surfaces a page exception as a real error instead of a
// silently missing value.
import type { CdpTransport } from "./cdp";
import { TARGET_MARKER } from "./ladder";

interface EvaluateResult {
  result?: { value?: unknown };
  exceptionDetails?: { exception?: { description?: string }; text?: string };
}

export interface EvaluateOptions {
  /** Run in a specific frame's execution context; the top frame when absent. */
  contextId?: number;
}

/** Evaluate an expression in the target page and return its value. */
export async function evaluate(
  cdp: CdpTransport,
  expression: string,
  options: EvaluateOptions = {},
): Promise<unknown> {
  const params: Record<string, unknown> = {
    expression,
    returnByValue: true,
    awaitPromise: true,
  };
  if (options.contextId !== undefined) params["contextId"] = options.contextId;
  const result = (await cdp.send("Runtime.evaluate", params)) as EvaluateResult;
  if (result?.exceptionDetails) {
    const description =
      result.exceptionDetails.exception?.description ??
      result.exceptionDetails.text ??
      "page evaluation failed";
    throw new Error(description);
  }
  return result?.result?.value;
}

/** Escape a token for use inside a double-quoted attribute selector. */
export function escapeToken(token: string): string {
  return token.replace(/["\\]/g, "\\$&");
}

/** An expression that yields the element the resolver stamped with `token`. */
export function markerExpression(token: string): string {
  return `document.querySelector('[${TARGET_MARKER}="${escapeToken(token)}"]')`;
}

/**
 * Serialize a call to the page-side resolver and evaluate it. The resolver
 * source is injected inline, so the page needs no bundled file and no globals.
 */
export function resolverCallExpression(resolverSource: string, query: unknown): string {
  return `(() => { const resolve = ${resolverSource}; return resolve(${JSON.stringify(query)}); })()`;
}
