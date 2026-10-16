// The abandon guard (PL-027): a record that leaves the tab without reaching the commit step must
// not take its half-filled form with it.
//
// The reason this exists is a real page's behaviour. Odoo autosaves a dirty form when the page is
// unloaded -- on the way out it posts what it holds, so a person who navigated away after typing
// half a record does not lose it. That is a kindness to a person and a hole in this product: a
// record the runner PARKED (a step could not be resolved, an approval was refused, the person
// cancelled) has not been committed, and it must not reach the destination. Left alone, the next
// record's opening navigate unloads the page and Odoo saves the abandoned form: a lead exists that
// no commit step created, which is exactly the invariant ("nothing reaches the destination without
// the commit step") this guard restores.
//
// It works by intercepting the tab's own network layer for as long as the page is dirty: every
// request is paused and every one that is not a read (GET/HEAD) is failed with `BlockedByClient`.
// That covers `sendBeacon` and `keepalive` fetches too, both of which are ordinary requests. The
// guard is released the moment the next record's navigate has loaded a new document, because from
// there the abandoned document no longer exists and has nothing to say.
//
// Nothing here decides anything about a record: the runner arms it when a record leaves the tab,
// and the executor releases it on arrival.
import type { CdpTransport } from "./cdp";

/** One request the guard refused to let leave the page. */
export interface BlockedRequest {
  method: string;
  url: string;
}

/** How many blocked requests are remembered; a page in a loop must not grow this without bound. */
export const BLOCKED_LIMIT = 50;

/** CDP's own reason for "the client refused this request". */
export const BLOCKED_ERROR_REASON = "BlockedByClient";

/** The methods that change nothing, and are therefore allowed through a dirty page. */
const READ_METHODS = new Set(["GET", "HEAD"]);

/**
 * The request types a page can leave a record through, and the only ones worth intercepting.
 *
 * CDP can pause *every* request of a tab, and the requirement is that no request which is not a
 * read gets out. Pausing all of them means continuing all of them, and a page load is hundreds of
 * requests: measured against real Odoo, that flood starved the extension's single service-worker
 * thread badly enough that its own hub socket timed out and the run paused `executor_disconnected`
 * -- the guard broke the very run it was protecting. These are the types a write can travel in:
 * a form post (`Document`), an RPC (`XHR`, `Fetch`), `navigator.sendBeacon` and `keepalive`
 * (`Ping`), a CORS preflight, and anything unclassified (`Other`). A stylesheet, an image or a
 * script request cannot create a record, so letting those through costs the invariant nothing.
 */
const WRITE_RESOURCE_TYPES = ["Document", "XHR", "Fetch", "Ping", "Preflight", "Other"];

let armed = false;
let blocked: BlockedRequest[] = [];
let unsubscribe: (() => void) | null = null;

/** Whether the guard is currently intercepting this tab's requests. For tests and diagnostics. */
export function guardArmed(): boolean {
  return armed;
}

/** What the guard has refused since it was armed. */
export function blockedRequests(): BlockedRequest[] {
  return [...blocked];
}

/** Forget everything. Only tests need this: the state belongs to the one bound tab. */
export function resetAbandonGuard(): void {
  armed = false;
  blocked = [];
  unsubscribe = null;
}

function remember(entry: BlockedRequest): void {
  if (blocked.length >= BLOCKED_LIMIT) return;
  blocked.push(entry);
}

/**
 * Arm the guard on one tab.
 *
 * Every request is paused at the request stage, so a `POST` a page makes on its way out -- a
 * `sendBeacon`, a `keepalive` fetch, a form submit, a jQuery `$.post` -- is answered with
 * `BlockedByClient` instead of reaching the destination. Reads are passed straight through: a page
 * that is still loading a stylesheet is not leaking a record.
 *
 * Idempotent: arming an armed guard changes nothing.
 */
export async function armAbandonGuard(cdp: CdpTransport): Promise<void> {
  if (armed) return;
  const handle = (params: Record<string, unknown>): void => {
    const requestId = params["requestId"];
    if (typeof requestId !== "string") return;
    const request = (params["request"] ?? {}) as { method?: unknown; url?: unknown };
    const method = String(request.method ?? "GET").toUpperCase();
    if (READ_METHODS.has(method)) {
      void cdp.send("Fetch.continueRequest", { requestId }).catch(() => undefined);
      return;
    }
    remember({ method, url: String(request.url ?? "") });
    void cdp
      .send("Fetch.failRequest", { requestId, errorReason: BLOCKED_ERROR_REASON })
      .catch(() => undefined);
  };

  const listener = cdp.on?.("Fetch.requestPaused", handle) ?? null;
  try {
    // One pattern per write-capable type, all methods: the guard's job is to catch the request
    // nobody thought of, and these are the types it can arrive in.
    await cdp.send("Fetch.enable", {
      patterns: WRITE_RESOURCE_TYPES.map((resourceType) => ({
        urlPattern: "*",
        requestStage: "Request",
        resourceType,
      })),
    });
  } catch (error) {
    listener?.();
    throw error;
  }
  unsubscribe = listener;
  armed = true;
}

/**
 * Release the guard, and report what it refused while it held.
 *
 * Called when the next record's navigate has arrived (the abandoned document is gone) and when the
 * run ends or is cancelled. Idempotent, and never throws: a tab whose debugger has already gone
 * away has no interception left to release, and that is not an error.
 */
export async function disarmAbandonGuard(cdp: CdpTransport): Promise<BlockedRequest[]> {
  const collected = [...blocked];
  blocked = [];
  unsubscribe?.();
  unsubscribe = null;
  if (!armed) return collected;
  armed = false;
  try {
    await cdp.send("Fetch.disable");
  } catch {
    // The tab detached, or the debugger is gone: nothing is intercepting any more either way.
  }
  return collected;
}
