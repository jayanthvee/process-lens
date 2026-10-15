/**
 * The pure decisions the Needs help page makes: when the safety gate lets an action through, what
 * the request body carries, and the two endpoint paths.
 *
 * This is the same shape PL-015's `src/logic.ts` had, so `needsHelp.test.tsx` can call these
 * functions directly as well as through the page. A commit-attempted row may already have been
 * saved, so the gate is the one place a person can get past the ledger's duplicate protection and
 * it is never one click (ticket PL-023 requirement 6; the safety rule is unchanged from PL-015).
 */

import type { RecordActionBody } from "../types";

/** The exact words the confirmation carries. The ticket fixes them. */
export const DESTINATION_CHECK_LABEL = "I checked Odoo and it isn't there";

/** Why the box is there, in one sentence, next to it. */
export const DESTINATION_CHECK_WHY =
  "We may already have saved this row before it went wrong, so look it up in Odoo and tick the box only if it is not there.";

/**
 * Said once at the top of the page when `GET /api/v1/operator` refuses. The API's own `detail` text
 * follows it verbatim, so the person can see the words the server used.
 */
export const OPERATOR_PROBLEM =
  "The API has no operator identity, so every action on this page is off";

/** What to do about it, keeping the API's own variable name in view. */
export const OPERATOR_NOTE =
  "Set PROCESSLENS_OPERATOR_ID and restart the API, then reload this page.";

export type RecordAction = "requeue" | "dismiss";

/** The words on the button that asks for one action. */
export function recordActionLabel(action: RecordAction): string {
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
