/**
 * PL-087: which Chrome this dashboard tab is in, as its ProcessLens extension says.
 *
 * The page asks the extension's content script (a `postMessage` to itself) and keeps the answer.
 * A run started here carries it, so it is driven by this Chrome and nobody else's, and the
 * "Chrome is connected" lines ask about this Chrome only. With no extension in this browser there
 * is no answer, and everything behaves as before: any connected Chrome may take the run.
 *
 * The content script may load after the page, so the question is asked again a few times.
 */

import { useEffect, useState } from "react";

export const WHICH_BROWSER = "processlens:which-browser";
export const BROWSER_ANSWER = "processlens:browser";
const SHAPE = /^[A-Za-z0-9_-]{8,64}$/;
const ASK_AGAIN_MS = [0, 1000, 3000, 8000];

let known: string | null = null;
let started = false;
const listeners = new Set<() => void>();

function heard(event: MessageEvent): void {
  if (event.source !== window || event.origin !== window.location.origin) return;
  const data = event.data as { type?: unknown; browserId?: unknown } | null;
  if (data === null || typeof data !== "object" || data.type !== BROWSER_ANSWER) return;
  if (typeof data.browserId !== "string" || !SHAPE.test(data.browserId)) return;
  if (known === data.browserId) return;
  known = data.browserId;
  for (const listener of listeners) listener();
}

/** Start asking, once per page. */
export function askWhichBrowser(): void {
  if (started || typeof window === "undefined") return;
  started = true;
  window.addEventListener("message", heard);
  for (const delay of ASK_AGAIN_MS) {
    setTimeout(() => {
      if (known === null) window.postMessage({ type: WHICH_BROWSER }, window.location.origin);
    }, delay);
  }
}

/** The browser id, when the extension has answered; `null` until then or without one. */
export function knownBrowser(): string | null {
  return known;
}

/** The session read for this Chrome: `?browser=` once the extension has said which it is. */
export function sessionPath(browser: string | null): string {
  return browser === null
    ? "/api/v1/executor/session"
    : `/api/v1/executor/session?browser=${encodeURIComponent(browser)}`;
}

/** `sessionPath` that follows the answer when it arrives. */
export function useSessionPath(): string {
  const [browser, setBrowser] = useState<string | null>(known);
  useEffect(() => {
    askWhichBrowser();
    const update = (): void => setBrowser(known);
    listeners.add(update);
    update();
    return () => {
      listeners.delete(update);
    };
  }, []);
  return sessionPath(browser);
}

/** Tests only: forget the answer. */
export function resetBrowserForTests(value: string | null = null): void {
  known = value;
  for (const listener of listeners) listener();
}
