// The work page must be drawn for a step to run (PL-040).
//
// Chrome stops drawing a tab that is in the background or whose window is minimized or fully
// covered: `requestAnimationFrame` never fires, and an app like Odoo never renders its form. A step
// there fails with "19 element(s)" or "no animation frame arrived", which says nothing about the
// real cause and spends one of the row's tries. So before a step the session asks whether the page
// is hidden; if it is, it brings the work window to the front and waits a moment. A page that stays
// hidden refuses the step with the `window_hidden` event, which pauses the run for a person instead
// of failing the row.
import type { CdpTransport } from "./cdp";

export const WINDOW_HIDDEN_MESSAGE =
  "the ProcessLens window is hidden behind other windows, so the page stopped drawing";

export interface VisibilityDeps {
  /** True only when the page reports itself hidden. A page that cannot answer is not hidden. */
  isHidden: () => Promise<boolean>;
  /** Make the work tab active and raise its window. Errors are swallowed by the caller. */
  bringForward: () => Promise<void>;
  sleep: (ms: number) => Promise<void>;
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

/**
 * True when the page is (or becomes) visible. A hidden page is brought forward once, then polled
 * every `stepMs` for up to `waitMs`.
 */
export async function ensureVisible(
  deps: VisibilityDeps,
  waitMs = 2000,
  stepMs = 100,
): Promise<boolean> {
  if (!(await deps.isHidden())) return true;
  try {
    await deps.bringForward();
  } catch {
    // a window that cannot be raised is reported below as hidden
  }
  for (let waited = 0; waited < waitMs; waited += stepMs) {
    await deps.sleep(stepMs);
    if (!(await deps.isHidden())) return true;
  }
  return false;
}
