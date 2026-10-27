// PL-113 S1: keep looking for a code page for as long as the page is still changing.
//
// A site answers Continue with a loading state and a server call, so its code boxes can render
// well after the action. A single settle of 250 ms missed them, and because typing a code is never
// recorded, no later action ever probed again: the demonstration was saved with no sign-in mark.
//
// The watch here runs after each recorded action. It looks again a settle after the page's last
// change, keeps doing so until `windowMs` after the last action, and asks about the page at most
// once (the ask marks `attempted`, exactly as a single probe does). The Gmail lookup starts from
// the last recorded action, less 30 s of slack, so a code email that arrived before the page
// settled is still found.
import { teachCodeOnPage, type TeachCodePageDeps, type TeachCodePageState } from "./teach-code";

/** How long after the last recorded action the page may keep changing and still be watched. */
export const CODE_WATCH_WINDOW_MS = 15_000;

/** How long the page must be still before the code page is looked for. */
export const CODE_WATCH_SETTLE_MS = 300;

/** How far before the last recorded action a code email may have arrived. */
export const CODE_EMAIL_SLACK_MS = 30_000;

export interface CodeWatchDeps extends TeachCodePageDeps {
  /** When the last recorded action happened, in milliseconds. */
  lastActionAt: () => number;
  /** Overrides for the three constants above; a test drives time with them. */
  windowMs?: number;
  settleMs?: number;
  slackMs?: number;
}

export interface CodeWatchHandle {
  /** Stop watching: a stop, a new recording, or a code page already found. */
  stop: () => void;
}

/**
 * Watch this page for a code prompt after an action.
 *
 * A page that is not asking for a code is left alone. The watch ends at the first code page found,
 * when `windowMs` has passed since the last action, or when the caller stops it. It never throws:
 * a probe that fails is a page that is not ready, not an error a person should see.
 */
export function watchCodePage(
  doc: Document,
  deps: CodeWatchDeps,
  state: TeachCodePageState,
): CodeWatchHandle {
  const settleMs = deps.settleMs ?? CODE_WATCH_SETTLE_MS;
  const windowMs = deps.windowMs ?? CODE_WATCH_WINDOW_MS;
  const slackMs = deps.slackMs ?? CODE_EMAIL_SLACK_MS;
  const view = doc.defaultView ?? (globalThis as unknown as Window);
  const deadline = deps.now() + windowMs;

  let stopped = false;
  let timer: ReturnType<typeof setTimeout> | null = null;

  const clear = (): void => {
    if (timer !== null) {
      clearTimeout(timer);
      timer = null;
    }
  };

  const stop = (): void => {
    if (stopped) return;
    stopped = true;
    clear();
    observer?.disconnect();
    view.removeEventListener?.("popstate", changed);
    view.removeEventListener?.("hashchange", changed);
  };

  const look = (): void => {
    if (stopped) return;
    if (deps.now() > deadline) {
      stop();
      return;
    }
    // The lookup starts from the action that opened the page, not from this probe: the code email
    // may already have arrived while the site was still rendering.
    void teachCodeOnPage({ ...deps, now: () => deps.lastActionAt() - slackMs }, state)
      .catch(() => undefined)
      .then(() => {
        if (stopped) return;
        if (state.attempted) {
          // A code page was found (or asked about): the watch has done its job.
          stop();
        }
        // Otherwise wait for the page's next change: this watch is about a page still changing.
      });
  };

  const schedule = (): void => {
    if (stopped || state.attempted) return;
    clear();
    if (deps.now() > deadline) {
      stop();
      return;
    }
    timer = setTimeout(() => {
      timer = null;
      look();
    }, settleMs);
  };

  function changed(): void {
    schedule();
  }

  const Observer = (
    view as unknown as { MutationObserver?: typeof MutationObserver }
  ).MutationObserver;
  const observer = typeof Observer === "function" ? new Observer(() => schedule()) : null;
  observer?.observe(doc.documentElement ?? doc, {
    childList: true,
    subtree: true,
    attributes: true,
    characterData: true,
  });
  view.addEventListener?.("popstate", changed);
  view.addEventListener?.("hashchange", changed);

  schedule();

  return { stop };
}
