// PL-107 item 12: the page's half of a teaching-time code fill.
//
// Reaching a code page while recording is not something the person should have to fetch a code
// from their inbox for. With their yes, the worker reads the one code and this types it exactly
// as a run does -- `enterCodeInPage`, which also presses the page's own continue.
//
// `quietly` is the recorder's off-switch for that moment, so neither the fill nor the press the
// extension makes becomes a step in the recording. The code itself is never kept: it lives in
// this call only.
import type { TeachCodeAnswer } from "../shared/messages";

export interface TeachCodePageDeps {
  /** Whether this page is asking for a code (and whether it uses split boxes). */
  probe: () => { found: boolean; split: boolean };
  /** Type the code exactly as a run does, and press the page's own continue. */
  enter: (input: { code: string }) => { entered: boolean; reason: string };
  /** Ask the worker for the one code from the person's email. */
  ask: (query: { since: number; site: string }) => Promise<TeachCodeAnswer>;
  /** Tell the worker what the page did, so the panel can say it. Never the code. */
  report: (answer: { entered: boolean; reason: string }) => void;
  /** Do the entry where the recorder cannot see it. */
  quietly: <T>(work: () => T) => T;
  now: () => number;
  site: () => string;
}

export interface TeachCodePageState {
  /** Whether this document has already asked for the code on this page. */
  attempted: boolean;
}

/**
 * Fill this page's code field from the person's email, once.
 *
 * A page that is not asking for a code is left alone. A page that is asked about once is not
 * asked about again for every later action; a retry (the person's yes, arriving late) clears
 * `attempted` and calls this again. The answer may be no code at all, and then the person types
 * it -- this never invents one.
 */
export async function teachCodeOnPage(
  deps: TeachCodePageDeps,
  state: TeachCodePageState,
): Promise<void> {
  if (state.attempted) return;
  if (!deps.probe().found) return;
  state.attempted = true;
  const answer = await deps.ask({ since: deps.now(), site: deps.site() });
  if (answer.status !== "found") return;
  const entered = deps.quietly(() => deps.enter({ code: answer.code }));
  deps.report(entered);
}
