/**
 * The decisions the row actions make: when the safety gate lets an action through, what the request
 * body carries, the two endpoint paths, and the one call that sends it.
 *
 * This is the same shape PL-015's `src/logic.ts` had, so `needsHelp.test.tsx` can call these
 * functions directly as well as through the page. A commit-attempted row may already have been
 * saved, so the gate is the one place a person can get past the ledger's duplicate protection and
 * it is never one click (ticket PL-023 requirement 6; the safety rule is unchanged from PL-015).
 * `sendRecordAction` is shared with a run's own rows (PL-038), so both screens send the same path
 * and the same body.
 */

import { apiPost } from "../lib/api";
import { isNotFoundReason } from "../lib/words";
import type { RecordActionBody } from "../types";

/** The exact words the confirmation carries. The ticket fixes them. */
export const DESTINATION_CHECK_LABEL = "I checked Odoo and it isn't there";

/**
 * Why the box is there, in one sentence, next to it.
 */
export const DESTINATION_CHECK_WHY =
  "We may already have saved this row before it went wrong, so look it up in Odoo and tick the box only if it is not there.";

/**
 * The wording of the operator problem lives in `lib/words.ts`, because the run page's decision
 * panel needs the same sentence. Re-exported here so this page's imports stay in one place.
 */
export { OPERATOR_NOTE, operatorNote } from "../lib/words";

export type RecordAction = "requeue" | "dismiss";

/**
 * The words on the button that asks for one action.
 *
 * A row whose search found nothing is skipped by the person, not dismissed by the system: the
 * button says so, while still using the existing parked -> skipped dismiss (PL-106 item 7 review).
 */
export function recordActionLabel(action: RecordAction, reason?: string | null): string {
  if (action === "dismiss" && isNotFoundReason(reason)) return "Skip it";
  return action === "requeue" ? "Try again" : "Dismiss";
}

/** The path the API already serves, built from the record the row carries. */
export function recordActionPath(recordId: string, action: RecordAction): string {
  return `/api/v1/records/${encodeURIComponent(recordId)}/${action}`;
}

export interface RecordActions {
  requeue: boolean;
  dismiss: boolean;
}

/** Try again and Dismiss appear for the rows this page lists: the ones that need a person. */
export function recordActions(state: string): RecordActions {
  const actionable = state === "parked" || state === "failed";
  return { requeue: actionable, dismiss: actionable };
}

export interface ActionGateInput {
  state: string;
  commitAttempted: boolean;
  confirmed: boolean;
  operatorId: string | null;
  /** True when `GET /api/v1/operator` refused: the page has nobody to record, so nothing may act. */
  operatorUnavailable?: boolean;
}

export interface ActionGate {
  /** Whether this row offers an action at all. */
  shown: boolean;
  /** A commit-attempted row needs the destination check ticked first. */
  needsConfirmation: boolean;
  /** Only the checked-destination path has to send the operator id. */
  needsOperator: boolean;
  disabled: boolean;
}

/**
 * The one place the page can get past the ledger's duplicate protection, so it is never one click.
 * A commit-attempted row keeps both buttons disabled until the confirmation is ticked, clearing the
 * tick disables them again, and a page with no operator identity disables every action on it.
 */
export function actionGate(input: ActionGateInput): ActionGate {
  const actions = recordActions(input.state);
  if (actions.requeue === false) {
    return { shown: false, needsConfirmation: false, needsOperator: false, disabled: true };
  }
  const needsConfirmation = input.commitAttempted === true;
  const needsOperator = needsConfirmation;
  const confirmationMissing = needsConfirmation && input.confirmed !== true;
  const operatorMissing = input.operatorId === null || input.operatorId === "";
  return {
    shown: true,
    needsConfirmation,
    needsOperator,
    disabled:
      input.operatorUnavailable === true || confirmationMissing || (needsOperator && operatorMissing),
  };
}

export interface ActionBodyInput {
  commitAttempted: boolean;
  confirmed: boolean;
  operatorId: string | null;
  reason?: string | null;
}

/**
 * The exact body the record endpoints get. `destination_checked_by` is sent only after the check
 * has been confirmed, and the id is never invented; a row that never reached a commit sends `{}`.
 */
export function actionBody(input: ActionBodyInput): RecordActionBody {
  const body: RecordActionBody = {};
  const reason = input.reason === undefined || input.reason === null ? "" : input.reason.trim();
  if (reason !== "") body.reason = reason;
  if (input.commitAttempted === true) {
    if (input.confirmed !== true) {
      throw new Error("A commit-attempted row needs the destination check confirmed first.");
    }
    if (input.operatorId === null || input.operatorId === "") {
      throw new Error("A commit-attempted row needs the API's operator identity.");
    }
    body.destination_checked_by = input.operatorId;
  }
  return body;
}

/**
 * The reason to send with an action, when it must not be the API's own default.
 *
 * A not-found row's dismiss keeps the reason that names the site, so the row still reads "Skipped"
 * rather than "Already there" once a person skips it (PL-106 item 7). Every other action sends no
 * reason, exactly as before: a normal dismiss still gets the API's own default sentence, and a
 * requeue never carries one.
 */
export function actionReason(action: RecordAction, reason?: string | null): string | undefined {
  return action === "dismiss" && isNotFoundReason(reason) ? reason ?? undefined : undefined;
}

/**
 * Send one record action and wait for the API's answer.
 *
 * Both screens that offer Try again and Dismiss go through this one call (PL-038), so the path and
 * the body -- including the destination check that lets a commit-attempted row past the ledger's
 * duplicate protection -- cannot drift between the Needs help page and a run's rows.
 */
export async function sendRecordAction(
  recordId: string,
  action: RecordAction,
  input: ActionBodyInput,
): Promise<void> {
  await apiPost(recordActionPath(recordId, action), actionBody(input));
}
