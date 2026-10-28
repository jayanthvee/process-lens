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

/** What a `value_equals` check read from the page, for the try card (PL-118). */
export interface ReadBackValue {
  /** The field's own name, from the selector's description or its first named rung. */
  label: string;
  /** The value the page actually shows, exactly as it reads it. */
  value: string;
}

/** The page-side number a formatted value carries, or null when it is not a number. */
export const PAGE_NUMBER_SOURCE = `(() => {
  const numberIn = (text) => {
    const compact = String(text).trim().replace(/\\s+/g, "");
    // PL-118 review S1: a comma groups thousands only before exactly three digits
    // ("1,299.00"). "799,50" and "1,5" use it as a decimal mark, so they are not numbers under
    // this reading and fall back to the text comparison -- 15 must never match a page showing 1,5.
    const match =
      /^[^0-9,+-]*([+-]?(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\\.[0-9]+)?)[^0-9,]*$/.exec(compact);
    if (!match) return null;
    const value = Number(match[1].replace(/,/g, ""));
    return Number.isFinite(value) ? value : null;
  };
  return numberIn;
})()`;

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
 * PL-018c: a page expression that evaluates to the page's visible text, with whitespace
 * collapsed exactly as the three text checks have always collapsed it.
 *
 * `document.body.innerText` never contains text rendered inside a shadow root, so a
 * confirmation inside a web component used to read as absent and — worse — `text_absent`
 * passed on a page that was showing an error inside one. This expression takes the light
 * DOM as before and then collects every OPEN shadow root (nested ones included) with the
 * same walk `markerExpression` uses in `executor/page.ts`, appending the `innerText`
 * (falling back to `textContent`, which is what jsdom offers) of each element child of
 * the root. `STYLE`, `SCRIPT` and `TEMPLATE` children are skipped: their text is source,
 * not what the page shows. Closed roots are invisible to page script and are never read;
 * iframes are out of scope here. The parts are joined with a single space, then the same
 * `\s+`-collapse and trim is applied to the whole string.
 */
export const PAGE_TEXT_SOURCE = `(() => {
  const parts = [];
  const skip = new Set(["STYLE", "SCRIPT", "TEMPLATE"]);
  const seen = new Set();
  // An element that is not rendered (\`hidden\`, \`display: none\`) reports its whole text content as
  // \`innerText\`, so a hidden "Saved" status would read as showing. \`checkVisibility\` is Chrome's
  // answer; where it is missing (jsdom) the element counts as rendered, as before.
  const rendered = (el) => typeof el.checkVisibility !== "function" || el.checkVisibility();
  // PL-018b: a document's text, then that of every same-origin iframe inside it (in its light tree
  // or in a shadow root). A cross-origin frame's document can't be read and is skipped. Reading
  // every frame, not only the step's own, is safe here because a commit's check is probed BEFORE
  // the commit: a confirmation already showing in any frame parks the row instead of verifying it.
  const readDocument = (doc) => {
    if (!doc || seen.has(doc)) return;
    seen.add(doc);
    if (doc.body) parts.push(doc.body.innerText || doc.body.textContent || "");
    const roots = [];
    const frames = [];
    const collect = (root) => {
      for (const el of Array.from(root.querySelectorAll("*"))) {
        if (el.tagName === "IFRAME" || el.tagName === "FRAME") frames.push(el);
        if (el.shadowRoot) {
          roots.push(el.shadowRoot);
          collect(el.shadowRoot);
        }
      }
    };
    collect(doc);
    for (const shadow of roots) {
      for (const el of Array.from(shadow.children)) {
        if (skip.has(el.tagName) || !rendered(el)) continue;
        parts.push(el.innerText || el.textContent || "");
      }
    }
    for (const frame of frames) {
      if (!rendered(frame)) continue;
      let inner = null;
      try {
        inner = frame.contentDocument;
      } catch (error) {
        inner = null;
      }
      readDocument(inner);
    }
  };
  readDocument(document);
  return parts.join(" ").replace(/\\s+/g, " ").trim();
})()`;

/** The predicate keys a condition may carry, in the order they are evaluated. */
const PREDICATE_KEYS = [
  "text_visible",
  "text_absent",
  "text_count_increases",
  "url_matches",
  "element_present",
  "element_absent",
  "value_equals",
] as const;

/**
 * A page expression that evaluates the condition to a boolean.
 *
 * PL-075: every predicate key in the condition counts. The expression used to return at the first
 * key it recognised, so a condition saying "the text is visible AND the URL matches" passed on the
 * strength of the text alone — a fail-open for any caller that paired two checks. A condition with
 * several keys now holds only when all of them hold. `timeout_ms` is not a predicate.
 *
 * The predicate set is closed: `text_visible`, `text_absent`, `text_count_increases`,
 * `url_matches`, `element_present`, `element_absent`, `value_equals`. A condition carrying none of
 * them raises, so one the executor does not understand can never pass silently.
 */
/** The field's own name for a read-back, from the selector's description or a named rung. */
function readBackLabel(spec: {
  selector?: { description?: unknown; targets?: unknown };
} | undefined): string {
  const description = spec?.selector?.description;
  if (typeof description === "string" && description.trim() !== "") return description.trim();
  const targets = Array.isArray(spec?.selector?.targets) ? spec.selector.targets : [];
  for (const raw of targets) {
    const target = raw as Record<string, unknown>;
    const by = target["by"];
    if (by === "role" && typeof target["name"] === "string" && target["name"].trim() !== "") {
      return target["name"].trim();
    }
    if (by === "label" && typeof target["text"] === "string" && target["text"].trim() !== "") {
      return target["text"].trim();
    }
    if (
      (by === "testid" || by === "text_fuzzy") &&
      typeof target["value"] === "string" &&
      target["value"].trim() !== ""
    ) {
      return target["value"].trim();
    }
  }
  return "The field";
}

/**
 * Read the value a `value_equals` condition points at, exactly as the page shows it (PL-118).
 *
 * The check itself is a boolean; the try card needs the page's own value so it can say "now 799"
 * or "still 910, not saved". This reads the same selector the condition uses and returns the
 * actual text (or input value), never the row's file value. Null when the field is not there.
 */
export async function readValueEquals(
  cdp: CdpTransport,
  condition: Condition,
  ctx: ConditionContext,
  options: { contextId?: number } = {},
): Promise<ReadBackValue | null> {
  const record = condition as Record<string, unknown>;
  const spec = record["value_equals"] as
    | { selector?: { targets?: unknown; within?: unknown; description?: unknown } }
    | undefined;
  if (!spec || typeof spec !== "object" || Array.isArray(spec)) return null;
  const selector = spec.selector ?? {};
  const call = resolverCallExpression(ctx.resolverSource, {
    targets: selector.targets ?? [],
    within: selector.within ?? null,
    token: "condition-probe",
  });
  const raw = await evaluate(
    cdp,
    `(() => {
      const result = ${call};
      if (!result || result.ok !== true) return null;
      const el = document.querySelector('[data-pl-target="condition-probe"]');
      if (!el) return null;
      return String(el.value !== undefined ? el.value : el.textContent || "");
    })()`,
    options,
  );
  if (typeof raw !== "string") return null;
  return { label: readBackLabel(spec), value: raw };
}

export function conditionExpression(condition: Condition, ctx: ConditionContext): string {
  const record = condition as Record<string, unknown>;
  const keys = PREDICATE_KEYS.filter((key) => key in record);
  if (keys.length === 0) {
    throw new Error(
      `the executor does not understand the condition: ${JSON.stringify(condition)}`,
    );
  }
  const parts = keys.map((key) => predicateExpression(record, key, ctx));
  if (parts.length === 1) return parts[0] as string;
  return `(() => ${parts.map((part) => `(${part})`).join(" && ")})()`;
}

/** One predicate's page expression. */
function predicateExpression(
  record: Record<string, unknown>,
  key: (typeof PREDICATE_KEYS)[number],
  ctx: ConditionContext,
): string {
  const normal = (value: unknown): string =>
    `String(${JSON.stringify(value)}).replace(/\\s+/g, " ").trim()`;

  if (key === "text_visible") {
    const wanted = record["text_visible"];
    if (typeof wanted !== "string" || wanted.trim() === "") {
      // Fail-closed (F3): `text_visible: ""` would make `includes("")` true for every page, so an
      // empty or non-string needle would verify a commit the destination never made.
      throw new Error(
        `text_visible needs a non-empty string, got ${JSON.stringify(wanted)}`,
      );
    }
    return `(() => { const text = ${PAGE_TEXT_SOURCE}; return text.includes(${normal(
      wanted,
    )}); })()`;
  }
  if (key === "text_absent") {
    return `(() => { const text = ${PAGE_TEXT_SOURCE}; return !text.includes(${normal(
      record["text_absent"],
    )}); })()`;
  }
  if (key === "text_count_increases") {
    const wanted = record["text_count_increases"];
    if (typeof wanted !== "string" || wanted.trim() === "") {
      throw new Error(
        `text_count_increases needs a non-empty string, got ${JSON.stringify(wanted)}`,
      );
    }
    // PL-032: the runner checks a commit's assertion just before the save (it must be false) and
    // again after it. The first evaluation in this document remembers how often the text appears;
    // the condition holds once it appears more often than that. The baseline lives on the page, so
    // a save that reloads the document loses it and the check fails closed -- it never passes
    // without a baseline taken before the click. A pass forgets the baseline, so the next record
    // starts from a fresh count.
    return `(() => {
      const needle = ${normal(wanted)};
      const text = ${PAGE_TEXT_SOURCE};
      let count = 0;
      for (let at = text.indexOf(needle); at !== -1; at = text.indexOf(needle, at + needle.length)) count += 1;
      const store = (window.__processLensCounts = window.__processLensCounts || {});
      if (!(needle in store)) { store[needle] = count; return false; }
      // The lowest count seen is the baseline: a suggestion list that still showed the name when
      // the first check ran must not raise it, or the new item could never be counted.
      if (count < store[needle]) { store[needle] = count; return false; }
      if (count > store[needle]) { delete store[needle]; return true; }
      return false;
    })()`;
  }
  if (key === "url_matches") {
    return `(() => { try { return new RegExp(${JSON.stringify(
      record["url_matches"],
    )}).test(location.href); } catch { return false; } })()`;
  }
  if (key === "element_present" || key === "element_absent") {
    const selector = selectorOf(record as Condition, key);
    const negate = key === "element_absent" ? "!" : "";
    const call = resolverCallExpression(ctx.resolverSource, {
      ...(selector as Record<string, unknown>),
      token: "condition-probe",
    });
    return `(() => { const result = ${call}; return ${negate}(result && result.ok === true); })()`;
  }
  if (key === "value_equals") {
    const spec = record["value_equals"] as {
      selector?: { targets?: unknown; within?: unknown };
      case_insensitive?: boolean;
      value?: unknown;
    };
    // The runner resolves the value source before it sends a condition, so `value` is already a
    // literal; `ctx.valueEquals` stays the fallback for a caller that supplies the value itself.
    const declared = spec?.value;
    // Fail-closed (F3): a non-string declared value would stringify into something nobody asked
    // for, and comparing against it could pass. Only a string compares; a missing value is the
    // caller's own literal, and if that is absent too the condition cannot be judged at all.
    let expected: string;
    if (declared === undefined) {
      if (typeof ctx.valueEquals !== "string" || ctx.valueEquals === "") {
        throw new Error("value_equals has no value to compare against");
      }
      expected = ctx.valueEquals;
    } else if (typeof declared === "string") {
      expected = declared;
    } else {
      throw new Error(
        `value_equals needs a string value, got ${JSON.stringify(declared)}`,
      );
    }
    const insensitive = spec?.case_insensitive === true;
    const call = resolverCallExpression(ctx.resolverSource, {
      targets: spec?.selector?.targets ?? [],
      within: spec?.selector?.within ?? null,
      token: "condition-probe",
    });
    // PL-118: a read-back compares what a person reads. An app formats a number its own way
    // ("$ 799.00", "1,299.00"), so two values that are the same number are equal. Text is
    // trimmed, and compared without case when the recipe asks for it. Anything that is not a
    // number falls back to the text comparison, so a word never matches a number by accident.
    return `(() => {
      const numberIn = ${PAGE_NUMBER_SOURCE};
      const result = ${call};
      if (!result || result.ok !== true) return false;
      const el = document.querySelector('[data-pl-target="condition-probe"]');
      if (!el) return false;
      const actual = String(el.value !== undefined ? el.value : el.textContent || "");
      const wanted = ${normal(expected)};
      const aNumber = numberIn(actual);
      const bNumber = numberIn(wanted);
      if (aNumber !== null && bNumber !== null) return aNumber === bNumber;
      const a = ${insensitive ? "actual.trim().toLowerCase()" : "actual.trim()"};
      const b = ${insensitive ? normal(expected) + ".toLowerCase()" : normal(expected)};
      return a === b;
    })()`;
  }
  throw new Error(`the executor does not understand the predicate ${key}`);
}

/**
 * Whether a thrown `evaluate` error is TRANSIENT — that is, the page navigated
 * under the poll and the next poll will simply evaluate in the new document.
 *
 * Exactly three messages qualify, matched case-insensitively (C2):
 *
 * - "Execution context was destroyed" and "Cannot find context with specified
 *   id": Chrome's execution-context errors while a redirect tears down the old
 *   document. A Save that lands on a new URL does exactly this.
 * - "Inspected target navigated or closed": what Chrome returns when a
 *   navigation interrupts `Runtime.evaluate` itself.
 *
 * The throw says nothing about the condition — only that *this* poll happened to
 * run mid-navigation — so the poll can be retried: sleep `pollMs` and re-evaluate
 * in the new document. Refusing the whole check here would park every record on a
 * CRM that redirects after saving.
 *
 * The match is deliberately NARROW. A bare "was destroyed" also matches
 * "the target was destroyed" (a dead target, not a navigation), and retrying a
 * persistent error only burns the poll's whole timeout and then lets the caller
 * report a "no" no poll ever observed. A detach, a malformed condition, or a
 * missing tab must reach the caller and refuse the check.
 */
export function isTransientEvaluateError(error: unknown): boolean {
  const message = error instanceof Error ? error.message : String(error ?? "");
  return /execution context was destroyed|cannot find context with specified id|inspected target navigated or closed/i.test(
    message,
  );
}

export interface WaitOptions {
  timeoutMs?: number;
  pollMs?: number;
  sleep?: (ms: number) => Promise<void>;
  now?: () => number;
  contextId?: number;
  /**
   * Asked at the top of every poll; when it answers true the wait ends at once
   * with `ok: false`, so an abort does not have to wait out the timeout.
   */
  aborted?: () => boolean;
}

export interface WaitResult {
  ok: boolean;
  polls: number;
  elapsedMs: number;
  /** True when the `aborted` probe ended the poll rather than the timeout. */
  aborted?: boolean;
  /**
   * C2: true when the poll timed out without the condition ever being evaluated
   * cleanly — the LAST attempt before the timeout threw a transient navigation
   * error rather than answering. `ok` stays false, but this is NOT an answered
   * "no": no poll saw the page long enough to judge the condition, so the caller
   * must treat it as "could not evaluate" (a refusal) rather than reporting
   * `passed: false`. An answered "no" would take the runner's `else` branch on a
   * page that merely kept redirecting (R1). Absent when the last attempt was a
   * clean false.
   */
  unanswered?: boolean;
  /** Why the poll stopped early, when it was not the condition holding. */
  reason?: string;
  /** How many polls ended in a transient evaluate error and were retried. */
  transientRetries?: number;
}

const defaultSleep = (ms: number): Promise<void> =>
  new Promise((done) => setTimeout(done, ms));

/**
 * Poll a condition until it holds or the timeout elapses. Checks immediately,
 * then waits `pollMs` between checks. Returns `ok: false` on timeout, and also
 * as soon as the `aborted` probe answers true — an aborted run must not sit in
 * a poll for the rest of its timeout.
 *
 * A *transient* evaluate error (the page navigated mid-poll) counts toward the
 * timeout and the loop sleeps `pollMs` and tries again, so a commit whose
 * redirect destroys the execution context still verifies. Any other throw is
 * rethrown and refuses the check.
 *
 * C2: `ok: false` on timeout is an answered "no" ONLY when the last attempt
 * before the timeout evaluated cleanly. When that attempt was a transient error
 * instead, the result carries `unanswered: true` — a "could not evaluate" the
 * caller must refuse, never report as `passed: false`.
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
  let transientRetries = 0;
  // C2: whether the LAST evaluation attempt before the timeout answered cleanly. Only a clean false
  // is a real "no"; a last attempt that threw transiently means the condition was never judged.
  let lastAttemptClean = false;

  for (;;) {
    polls += 1;
    if (options.aborted && options.aborted()) {
      return {
        ok: false,
        polls,
        elapsedMs: now() - started,
        aborted: true,
        transientRetries,
        reason: "the run was aborted",
      };
    }
    const expression = conditionExpression(condition, ctx);
    let ok = false;
    let clean = false;
    try {
      ok =
        (await evaluate(
          cdp,
          expression,
          options.contextId !== undefined ? { contextId: options.contextId } : {},
        )) === true;
      clean = true;
    } catch (error) {
      // A transient error is a poll that answered "not yet": the page navigated under us and the
      // next attempt evaluates in the new document. It counts toward the timeout like any other
      // poll, and the loop keeps going. A persistent error still refuses the check.
      if (!isTransientEvaluateError(error)) throw error;
      transientRetries += 1;
    }
    lastAttemptClean = clean;
    const elapsedMs = now() - started;
    if (ok) return { ok: true, polls, elapsedMs, transientRetries };
    if (elapsedMs >= timeoutMs) {
      // C2 (R1): the last attempt decides whether this is a real "no". A clean false is an answered
      // "no"; a transient error means the condition was never evaluated, so the result says so and
      // the caller refuses the check instead of reporting `passed: false`.
      if (!lastAttemptClean) {
        return {
          ok: false,
          polls,
          elapsedMs,
          transientRetries,
          unanswered: true,
          reason: `the condition could not be evaluated: the last poll failed with a transient navigation error (${transientRetries} of ${polls} polls)`,
        };
      }
      return {
        ok: false,
        polls,
        elapsedMs,
        transientRetries,
        reason: `the condition did not hold within ${timeoutMs}ms`,
      };
    }
    await sleep(pollMs);
  }
}
