// The abandon guard (PL-027): a record that leaves the tab without reaching the commit step must
// not take its half-filled form with it.
//
// The reason this exists is a real page's behaviour. Odoo autosaves a dirty form when the page is
// unloaded -- on the way out it posts what it holds, so a person who navigated away after typing
// half a record does not lose it. That is a kindness to a person and a hole in this product: a
// record the runner PARKED (a step could not be resolved, an approval was refused, the person
// cancelled) has not been committed, and it must not reach the destination. Left alone, the next
// record's opening navigate unloads the page and Odoo saves the abandoned form: a lead exists that
// no commit step created.
//
// How it works: the dirty document is retired at once, with the network shut. Every URL is
// blocked in the browser's own network stack (`Network.setBlockedURLs`), the tab is sent to
// `about:blank` -- which runs the page's unload handlers, whose save requests fail inside the
// browser -- and only once the blank page has arrived is the block lifted. The next record then
// starts from a clean tab.
//
// An earlier version paused every request with `Fetch.enable` and answered each one from the
// extension. On real Odoo that stalled the extension's single service-worker thread (a page whose
// blocked RPCs it retries is a stream of paused requests, each a round trip through the worker)
// until the hub socket timed out. Blocking in the network stack costs the extension nothing per
// request, and the window it is open for is one navigation to a blank page.
//
// Nothing here decides anything about a record: the runner asks for it when a record leaves the
// tab without committing, and the next navigate collects what it refused for the ledger.
import type { CdpTransport } from "./cdp";

/** One request the guard refused to let leave the page. */
export interface BlockedRequest {
  method: string;
  url: string;
}

/** How many blocked requests are remembered; a page in a loop must not grow this without bound. */
export const BLOCKED_LIMIT = 50;

/** Where the abandoned document is sent: a page with no handlers and no network of its own. */
export const BLANK_URL = "about:blank";

/** How long the tab may take to reach the blank page before the guard gives up on it. */
export const RETIRE_TIMEOUT_MS = 10_000;

const RETIRE_POLL_MS = 100;

/** The methods that change nothing; a read the dying page makes is not worth reporting. */
const READ_METHODS = new Set(["GET", "HEAD"]);

/** True between a retirement and the next record's navigate collecting its report. */
let armed = false;
let blocked: BlockedRequest[] = [];

/** Whether a retirement's report is waiting to be collected. For tests and diagnostics. */
export function guardArmed(): boolean {
  return armed;
}

/** What the guard has refused since the last report was collected. */
export function blockedRequests(): BlockedRequest[] {
  return [...blocked];
}

/** Forget everything. Only tests need this: the state belongs to the one bound tab. */
export function resetAbandonGuard(): void {
  armed = false;
  blocked = [];
}

function remember(entry: BlockedRequest): void {
  if (blocked.length >= BLOCKED_LIMIT) return;
  blocked.push(entry);
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function tabHref(cdp: CdpTransport): Promise<string> {
  try {
    const reply = (await cdp.send("Runtime.evaluate", {
      expression: "location.href",
      returnByValue: true,
    })) as { result?: { value?: unknown } } | undefined;
    const value = reply?.result?.value;
    return typeof value === "string" ? value : "";
  } catch {
    // A document mid-replacement has no context to evaluate in yet; the next poll asks again.
    return "";
  }
}

/** Wait until the top frame shows the blank page: its own event, or its URL read back. */
async function waitForBlank(cdp: CdpTransport, timeoutMs: number): Promise<boolean> {
  let arrived = false;
  const off =
    cdp.on?.("Page.frameNavigated", (params) => {
      const frame = (params["frame"] ?? {}) as { parentId?: unknown; url?: unknown };
      if (frame.parentId === undefined && frame.url === BLANK_URL) arrived = true;
    }) ?? null;
  try {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      if (arrived || (await tabHref(cdp)) === BLANK_URL) return true;
      await delay(RETIRE_POLL_MS);
    }
    return arrived;
  } finally {
    off?.();
  }
}

/**
 * Retire the tab's dirty document with the network shut, and keep what was refused.
 *
 * Throws when the browser refuses to block, or the tab never reaches the blank page: the runner
 * then treats the tab as unsafe rather than letting the next navigate unload the form unguarded.
 * The block is lifted on every path, so a failure never leaves the person's tab offline.
 *
 * Idempotent: a tab already retired and not yet collected is left alone.
 */
export async function armAbandonGuard(
  cdp: CdpTransport,
  timeoutMs: number = RETIRE_TIMEOUT_MS,
): Promise<void> {
  if (armed) return;
  const offRequest =
    cdp.on?.("Network.requestWillBeSent", (params) => {
      const request = (params["request"] ?? {}) as { method?: unknown; url?: unknown };
      const method = String(request.method ?? "GET").toUpperCase();
      const url = String(request.url ?? "");
      if (READ_METHODS.has(method) || url === BLANK_URL) return;
      remember({ method, url });
    }) ?? null;
  // A page that asks "leave this page?" would hold the blank navigation forever; leaving is
  // exactly what is wanted, and the save it would have made is blocked either way.
  const offDialog =
    cdp.on?.("Page.javascriptDialogOpening", (params) => {
      if (String(params["type"] ?? "") !== "beforeunload") return;
      void cdp.send("Page.handleJavaScriptDialog", { accept: true }).catch(() => undefined);
    }) ?? null;
  try {
    await cdp.send("Network.enable");
    await cdp.send("Network.setBlockedURLs", { urls: ["*"] });
    await cdp.send("Page.navigate", { url: BLANK_URL });
    if (!(await waitForBlank(cdp, timeoutMs))) {
      throw new Error(`the tab did not reach ${BLANK_URL} within ${timeoutMs}ms`);
    }
    armed = true;
  } finally {
    offDialog?.();
    offRequest?.();
    await cdp.send("Network.setBlockedURLs", { urls: [] }).catch(() => undefined);
    // Network events are not needed past the retirement, and every one of them would otherwise
    // be delivered to the service worker for the rest of the run.
    await cdp.send("Network.disable").catch(() => undefined);
  }
}

/**
 * Collect what the guard refused, for the ledger.
 *
 * Called when the next record's navigate has arrived and when the run ends or is cancelled. The
 * network was already reopened when the blank page arrived, so there is nothing to release here.
 * Idempotent, and never throws.
 */
export async function disarmAbandonGuard(_cdp: CdpTransport): Promise<BlockedRequest[]> {
  const collected = [...blocked];
  blocked = [];
  armed = false;
  return collected;
}
