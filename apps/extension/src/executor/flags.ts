// PL-108 experiment: the switch that turns on dispatching a step's input from inside the page
// instead of through `chrome.debugger`.
//
// Off by default. Nothing ships to testers until T1 and the owner decide from the measurement
// table, so every caller reads this one function and treats anything but an explicit "yes" as the
// debugger path.

/** The console/storage key a person or a test flips to try the in-page path. */
export const IN_PAGE_DISPATCH_KEY = "processlens.inPageDispatch";

/** Which path a step's input took. */
export type DispatchPath = "debugger" | "in-page";

/** The default when the flag was never set: the debugger, exactly as today. */
export const DEFAULT_DISPATCH_PATH: DispatchPath = "debugger";

/**
 * Normalise a stored flag value. Only an explicit yes is "in-page": `true`, `"in-page"` or `"on"`.
 * Anything else — unset, `false`, `"off"`, `"0"` — is the debugger.
 */
export function dispatchPathFrom(value: unknown): DispatchPath {
  if (value === true) return "in-page";
  if (typeof value === "string") {
    const token = value.trim().toLowerCase();
    if (token === "in-page" || token === "inpage" || token === "on" || token === "true" || token === "1") {
      return "in-page";
    }
  }
  return DEFAULT_DISPATCH_PATH;
}

/** Whether the in-page dispatcher is on for this value. */
export function inPageDispatchEnabled(value: unknown): boolean {
  return dispatchPathFrom(value) === "in-page";
}
