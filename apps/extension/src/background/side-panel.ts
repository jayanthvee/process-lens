/**
 * PL-105: a click on the toolbar icon opens the side panel.
 *
 * Chrome keeps this behavior per extension, so setting it when the extension is installed and
 * when the browser starts is enough; a worker that is later revived writes nothing. The API is
 * absent from a very old Chrome and from the test double, and a missing panel must never stop the
 * recorder from loading, so every call is optional and every refusal is swallowed.
 */

export interface SidePanelApi {
  setPanelBehavior?(behavior: { openPanelOnActionClick: boolean }): Promise<void> | void;
}

export interface PanelRuntimeEvents {
  onInstalled?: { addListener(listener: () => void): void };
  onStartup?: { addListener(listener: () => void): void };
}

export interface PanelChrome {
  sidePanel?: SidePanelApi;
  runtime?: PanelRuntimeEvents;
}

export const OPEN_PANEL_ON_CLICK = { openPanelOnActionClick: true } as const;

/** Ask Chrome to open the panel when the toolbar icon is clicked. */
export function openPanelOnActionClick(chrome: PanelChrome): void {
  try {
    void Promise.resolve(
      chrome.sidePanel?.setPanelBehavior?.({ ...OPEN_PANEL_ON_CLICK }),
    ).catch(() => undefined);
  } catch {
    // An older Chrome has no sidePanel API; the click does nothing rather than throwing.
  }
}

/**
 * Wire that ask to the two moments Chrome forgets it: a fresh install/update, and browser start.
 * The worker calls this once at the top level.
 */
export function registerPanelBehavior(chrome: PanelChrome): void {
  chrome.runtime?.onInstalled?.addListener(() => openPanelOnActionClick(chrome));
  chrome.runtime?.onStartup?.addListener(() => openPanelOnActionClick(chrome));
}
