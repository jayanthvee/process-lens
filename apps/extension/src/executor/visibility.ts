// The work page must be drawn for a step to run (PL-040).
//
// Chrome stops drawing a tab that is in the background or whose window is minimized or fully
// covered: `requestAnimationFrame` never fires, and an app like Odoo never renders its form. A step
// there fails with "19 element(s)" or "no animation frame arrived", which says nothing about the
// real cause and spends one of the row's tries. So before a step the session asks whether the page
// is hidden; if it is, it brings the work window to the front and waits a moment. A page that stays
// hidden refuses the step with the `window_hidden` event, which pauses the run for a person instead
// of failing the row.
//
// PL-045: the refusal now carries evidence. `ensureVisible` reports what it tried, what Chrome said
// when the raise failed, and the window's state, focus and tab activity before and after. The
// `window_hidden` detail gets one short line built from that report, so the next miss says why.
import type { CdpTransport } from "./cdp";

export const WINDOW_HIDDEN_MESSAGE =
  "the ProcessLens window is hidden behind other windows, so the page stopped drawing";

/** What Chrome says about the work window at one moment. */
export interface WindowFacts {
  /** `normal`, `minimized`, `maximized`, `fullscreen`, or `unknown` when Chrome declined to say. */
  state: string;
  focused: boolean;
  tabActive: boolean;
}

/** What `ensureVisible` found and tried, so a miss can explain itself (PL-045). */
export interface VisibilityReport {
  /** True when the page is (or becomes) visible. */
  visible: boolean;
  /** True only when the page was hidden and the window was raised. */
  tried: boolean;
  /** The message a `bringForward` that threw carried. */
  error?: string;
  /** The window before the raise; absent when it could not be read. */
  before?: WindowFacts;
  /** The window at the end of the wait; absent when it could not be read. */
  after?: WindowFacts;
  /** How long the page was polled, in milliseconds. */
  waitedMs: number;
}

export interface VisibilityDeps {
  /** True only when the page reports itself hidden. A page that cannot answer is not hidden. */
  isHidden: () => Promise<boolean>;
  /** Make the work tab active and raise its window. Errors are reported, not thrown. */
  bringForward: () => Promise<void>;
  sleep: (ms: number) => Promise<void>;
  /** Chrome's view of the work window. A read that fails is simply absent from the report. */
  windowFacts?: () => Promise<WindowFacts | undefined>;
}

/** Ask the page itself: `document.visibilityState` is what Chrome's drawing follows. */
export async function pageIsHidden(cdp: CdpTransport): Promise<boolean> {
  try {
    const reply = (await cdp.send("Runtime.evaluate", {
      expression: "document.visibilityState",
      returnByValue: true,
    })) as { result?: { value?: unknown } } | undefined;
    return reply?.result?.value === "hidden";
  } catch {
    return false;
  }
}

/** Read the window facts without letting a broken read break the visibility check. */
async function readWindowFacts(deps: VisibilityDeps): Promise<WindowFacts | undefined> {
  if (!deps.windowFacts) return undefined;
  try {
    return await deps.windowFacts();
  } catch {
    return undefined;
  }
}

function yesNo(value: boolean | undefined): string {
  if (value === undefined) return "unknown";
  return value ? "yes" : "no";
}

/**
 * The `window_hidden` detail: the plain message, plus one short line of evidence when a report
 * exists. It names the window and what Chrome said; it never includes a URL or page text.
 */
export function windowHiddenMessage(report?: VisibilityReport): string {
  // T1: a report from a check that found the page visible (it hid later, mid-step) has no
  // evidence about the miss, so the plain message is the honest one.
  if (!report || !report.tried) return WINDOW_HIDDEN_MESSAGE;
  const before = report.before?.state ?? "unknown";
  const after = report.after?.state ?? "unknown";
  const facts = report.after ?? report.before;
  return (
    `${WINDOW_HIDDEN_MESSAGE} (window ${before}→${after}, ` +
    `focused ${yesNo(facts?.focused)}, tab active ${yesNo(facts?.tabActive)}, ` +
    `raise error: ${report.error ?? "none"}, waited ${report.waitedMs}ms)`
  );
}

/**
 * True when the page is (or becomes) visible. A hidden page is brought forward once, then polled
 * every `stepMs` for up to `waitMs`. The report says what was tried even when it worked, and why
 * it may not have.
 */
export async function ensureVisible(
  deps: VisibilityDeps,
  waitMs = 2000,
  stepMs = 100,
): Promise<VisibilityReport> {
  if (!(await deps.isHidden())) {
    return { visible: true, tried: false, waitedMs: 0 };
  }

  const before = await readWindowFacts(deps);
  let error: string | undefined;
  try {
    await deps.bringForward();
  } catch (caught) {
    // A window that cannot be raised is reported below as hidden, with Chrome's reason kept.
    error = caught instanceof Error ? caught.message : String(caught);
  }

  let waitedMs = 0;
  let visible = false;
  while (waitedMs < waitMs) {
    await deps.sleep(stepMs);
    waitedMs += stepMs;
    if (!(await deps.isHidden())) {
      visible = true;
      break;
    }
  }

  const after = await readWindowFacts(deps);
  return {
    visible,
    tried: true,
    ...(error !== undefined ? { error } : {}),
    ...(before ? { before } : {}),
    ...(after ? { after } : {}),
    waitedMs,
  };
}
