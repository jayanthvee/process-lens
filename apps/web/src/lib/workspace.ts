/**
 * The workspace's own state: whether an admin has paused runs, and whether this person's Chrome
 * extension is still new enough.
 *
 * One read, in one place, so the shell's banners and every button that starts a run cannot disagree
 * about whether runs may start.
 */

import type { WorkspaceState } from "../types";
import { useJson, type JsonState } from "./useJson";

/**
 * `GET /api/v1/workspace/state`. It needs no admin token: it reports on the operator this page is
 * already signed in as. The API answers 503 when it knows no operator identity, and the page treats
 * that exactly like any other failure -- it says nothing.
 */
export const WORKSPACE_PATH = "/api/v1/workspace/state";

/**
 * Slower than the live pages on purpose. A run's progress moves every second and the person
 * watching it should see it move; this state changes only when an admin flips a switch, so asking
 * every two seconds would be noise for a switch that may not move for weeks.
 *
 * It is also only a courtesy. The API refuses to start a run while it is frozen (409), so the
 * banner is a warning to a person, never the guard that keeps a frozen run from starting -- which is
 * why a banner that arrives a few seconds late costs nothing but one refused click.
 */
export const WORKSPACE_POLL_MS = 15000;

/** The workspace state, asked for slowly while the shell is on screen. */
export function useWorkspaceState(): JsonState<WorkspaceState> {
  return useJson<WorkspaceState>(WORKSPACE_PATH, WORKSPACE_POLL_MS);
}

/**
 * Whether runs may start at all, for the buttons that start one.
 *
 * A `null` state means the API never answered -- it is still loading, or it refused. That is not a
 * reason to switch the page off: an API that cannot say must not disable anything silently, and the
 * API's own gate still refuses a run it will not accept (D-024: every run write goes through it).
 */
export function runsBlocked(state: WorkspaceState | null): boolean {
  if (state === null) return false;
  return state.runs_frozen || state.extension_out_of_date;
}
