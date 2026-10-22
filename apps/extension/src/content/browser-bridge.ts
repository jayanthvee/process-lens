/**
 * PL-087: the dashboard tab asks which Chrome it is in, and the extension answers.
 *
 * The page posts `{type: "processlens:which-browser"}` to itself. The content script answers with
 * this install's browser id, and only when the page is the ProcessLens address the extension last
 * connected to, in the top frame: no other site ever learns the id.
 */

export const WHICH_BROWSER = "processlens:which-browser";
export const BROWSER_ANSWER = "processlens:browser";

export interface Whoami {
  browserId?: string;
  hubOrigin?: string;
}

export function installBrowserBridge(win: Window, ask: () => Promise<Whoami>): void {
  if (win !== win.top) return;
  win.addEventListener("message", (event: MessageEvent) => {
    if (event.source !== win) return;
    const data = event.data as { type?: unknown } | null;
    if (data === null || typeof data !== "object" || data.type !== WHICH_BROWSER) return;
    void ask()
      .then((who) => {
        if (!who.browserId || !who.hubOrigin || who.hubOrigin !== win.location.origin) return;
        win.postMessage({ type: BROWSER_ANSWER, browserId: who.browserId }, win.location.origin);
      })
      .catch(() => undefined);
  });
}
