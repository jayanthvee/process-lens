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

/** PL-123: the sentence a parked row carries when the work window has stopped painting. */
export const NOT_DRAWING_MESSAGE =
  "The ProcessLens window isn't drawing. Bring it to the front and press Try again.";

/**
 * PL-123: ask Chrome to keep the bound tab painting while its window is covered.
 *
 * The two commands that do not move the person's screen come first: a tab whose lifecycle is
 * `active` and whose focus is emulated keeps drawing even while its window is covered.
 * `Page.bringToFront` is deliberately not here: it raises the window over whatever the person is
 * doing, so it is only sent after a first count has shown that nothing drew (see
 * :func:`ensureDrawing`). A command Chrome refuses is reported by omission, never thrown: a page
 * that cannot be focused yet is not the reason a step failed.
 */
export const FOCUS_METHODS: { method: string; params: Record<string, unknown> }[] = [
  { method: "Page.setWebLifecycleState", params: { state: "active" } },
  { method: "Emulation.setFocusEmulationEnabled", params: { enabled: true } },
];

export async function keepDrawing(cdp: CdpTransport): Promise<string[]> {
  const kept: string[] = [];
  for (const entry of FOCUS_METHODS) {
    try {
      await cdp.send(entry.method, entry.params);
      kept.push(entry.method);
    } catch {
      // Chrome may refuse one of these on a page that is still loading; the other may work.
    }
  }
  return kept;
}

/** PL-123: how many frames count as "the page is drawing". A covered tab draws none at all. */
export const DRAWING_FRAMES = 2;

/**
 * PL-123: count the animation frames a page draws.
 *
 * The answer comes back as soon as {@link DRAWING_FRAMES} frames have drawn, so a page that is
 * painting costs two frames rather than the whole wait; a page that draws nothing is counted for
 * `waitMs` and answers its (low) count. The wait is the same 2 s limit either way. A page that
 * cannot be evaluated answers -1, which is "unknown", not "not drawing".
 */
export async function paintingFrames(cdp: CdpTransport, waitMs = 2000): Promise<number> {
  const limit = Math.max(0, waitMs);
  try {
    const reply = (await cdp.send("Runtime.evaluate", {
      expression: `new Promise((resolve) => {
        let frames = 0;
        let settled = false;
        const finish = () => {
          if (settled) return;
          settled = true;
          resolve(frames);
        };
        const tick = () => {
          if (settled) return;
          frames += 1;
          if (frames >= ${DRAWING_FRAMES}) {
            finish();
            return;
          }
          requestAnimationFrame(tick);
        };
        requestAnimationFrame(tick);
        setTimeout(finish, ${limit + 250});
      })`,
      awaitPromise: true,
      returnByValue: true,
    })) as { result?: { value?: unknown } } | undefined;
    const value = reply?.result?.value;
    return typeof value === "number" && Number.isFinite(value) ? value : -1;
  } catch {
    return -1;
  }
}

/**
 * PL-123: keep the tab drawing before a step, and report when it has not painted for 2 s.
 *
 * A window that is covered or in the background can sit blank while every file has loaded. The
 * sequence is: set the lifecycle and focus emulation, count; only when nothing drew, raise the
 * window and count again; only then park with the drawing sentence -- never with "not found",
 * because the element is there and the page simply has not rendered it.
 */
export async function ensureDrawing(
  cdp: CdpTransport,
  waitMs = 2000,
): Promise<{ drawing: boolean; frames: number; kept: string[]; message?: string }> {
  const kept = await keepDrawing(cdp);
  const first = await paintingFrames(cdp, waitMs);
  if (first === -1 || first >= DRAWING_FRAMES) {
    return { drawing: true, frames: first, kept };
  }
  try {
    await cdp.send("Page.bringToFront", {});
    kept.push("Page.bringToFront");
  } catch {
    // A window that cannot be raised is reported by the count that follows.
  }
  const second = await paintingFrames(cdp, waitMs);
  if (second === -1 || second > 0) {
    return { drawing: true, frames: second, kept };
  }
  return { drawing: false, frames: second, kept, message: NOT_DRAWING_MESSAGE };
}

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
