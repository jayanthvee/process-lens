import { type CSSProperties, useState } from "react";

import { apiPost, errorText } from "../lib/api";
import { useJson } from "../lib/useJson";
import { plainReason, refusalWords, rowOutcome } from "../lib/words";
import type { NeedsHelpRow, OperatorInfo } from "../types";
import {
  DESTINATION_CHECK_LABEL,
  DESTINATION_CHECK_WHY,
  actionBody,
  actionGate,
  operatorNote,
  recordActionLabel,
  recordActionPath,
  type RecordAction,
} from "./needsHelpActions";
import "./needsHelp.css";

/** The blur-in stagger for the sections (DESIGN.md §7). */
function slot(index: number): CSSProperties {
  return { "--i": index } as CSSProperties;
}

/**
 * The rows that need a person: every row `GET /api/v1/needs-help` returns, newest first (the route
 * already orders them), each with its outcome in plain words and one action -- Try again, or
 * Dismiss.
 *
 * The safety rule is the ticket's requirement 6 and it is unchanged from PL-015: a row that may
 * already have been saved (`commit_attempted`) keeps both buttons disabled until the person ticks
 * "I checked Odoo and it isn't there", and only then does the request carry
 * `destination_checked_by` -- the operator id from `GET /api/v1/operator`. A row that never reached
 * a commit needs no tick and sends an empty body. The Shell already renders this page's primary
 * action slot, so every button this page draws itself is quiet.
 */
export function NeedsHelp(): JSX.Element {
  const help = useJson<NeedsHelpRow[]>("/api/v1/needs-help");
  const operator = useJson<OperatorInfo>("/api/v1/operator");

  // The ticks belong to one person's visit: nothing on this page ever ticks the box itself, and a
  // reload leaves every box clear.
  const [confirmed, setConfirmed] = useState<Record<string, boolean>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [errors, setErrors] = useState<Record<string, string>>({});

  const rows = Array.isArray(help.data) ? help.data : [];
  const operatorId = operator.data?.operator_id ?? null;
  // A refusal from the operator route (503, or a host we could not reach) is the API's own words.
  const operatorProblem = operator.error;

  function ticket(recordId: string, checked: boolean): void {
    setConfirmed((current) => ({ ...current, [recordId]: checked }));
  }

  async function act(row: NeedsHelpRow, action: RecordAction): Promise<void> {
    const gate = actionGate({
      state: row.state,
      commitAttempted: row.commit_attempted === true,
      confirmed: confirmed[row.record_id] === true,
      operatorId,
      operatorUnavailable: operatorProblem !== null,
    });
    if (gate.disabled || gate.shown === false || busy !== null) return;
    setBusy(row.record_id);
    setErrors((current) => {
      const next = { ...current };
      delete next[row.record_id];
      return next;
    });
    try {
      await apiPost(
        recordActionPath(row.record_id, action),
        actionBody({
          commitAttempted: row.commit_attempted === true,
          confirmed: confirmed[row.record_id] === true,
          operatorId,
        }),
      );
      help.reload();
    } catch (failure) {
      // The API's own `detail` text, translated so no engine word reaches the person. Nothing is
      // retried.
      setErrors((current) => ({ ...current, [row.record_id]: refusalWords(errorText(failure)) }));
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="needs-help">
      {operatorProblem === null ? null : (
        <p className="operator-note in" style={slot(1)} role="status">
          {operatorNote(operatorProblem)}
        </p>
      )}

      <section className="panel in" style={slot(operatorProblem === null ? 1 : 2)}>
        <div className="panel-h">
          <h3>Needs your help</h3>
          {rows.length === 0 ? null : <span>{rows.length} waiting</span>}
        </div>
        {rows.length === 0 ? (
          <p className="panel-note">
            {help.error ??
              "Nothing needs you right now \u2014 the rows that need your decision will appear here."}
          </p>
        ) : (
          <ul className="rows">
            {rows.map((row) => {
              const outcome = rowOutcome(row.state);
              const ticked = confirmed[row.record_id] === true;
              const gate = actionGate({
                state: row.state,
                commitAttempted: row.commit_attempted === true,
                confirmed: ticked,
                operatorId,
                operatorUnavailable: operatorProblem !== null,
              });
              const refusal = errors[row.record_id];
              const boxId = `checked-odoo-${row.record_id}`;
              return (
                <li key={row.record_id}>
                  <div>
                    {/* The row's key is the person's or thing's name for it: data, rendered as
                        text. The reason is the one sentence that says why it needs a person. */}
                    <b>{row.row}</b>
                    <p>{plainReason(row.reason, row.state)}</p>
                    <small>{row.task_name}</small>
                  </div>
                  <div className="row-actions">
                    <span className={`res ${outcome.tone}`}>{outcome.label}</span>
                    {gate.shown === false ? null : (
                      <span className="record-buttons">
                        <button
                          type="button"
                          className="btn quiet record-requeue"
                          disabled={gate.disabled || busy !== null}
                          onClick={() => void act(row, "requeue")}
                        >
                          {recordActionLabel("requeue")}
                        </button>
                        <button
                          type="button"
                          className="btn quiet record-dismiss"
                          disabled={gate.disabled || busy !== null}
                          onClick={() => void act(row, "dismiss")}
                        >
                          {recordActionLabel("dismiss")}
                        </button>
                      </span>
                    )}
                    {refusal === undefined ? null : (
                      <span className="action-error" role="status">
                        {refusal}
                      </span>
                    )}
                  </div>
                  {/* The destination check belongs on its own line under the whole row, not in
                      the narrow action column: it is a sentence, and squeezing it into the
                      right-hand slot makes the row half empty and twice as tall. */}
                  {gate.needsConfirmation ? (
                    <div className="destination-check">
                      <span className="destination-check-line">
                        <input
                          id={boxId}
                          className="destination-check-box"
                          type="checkbox"
                          checked={ticked}
                          onChange={(event) => ticket(row.record_id, event.currentTarget.checked)}
                        />
                        <label className="destination-check-label" htmlFor={boxId}>
                          {DESTINATION_CHECK_LABEL}
                        </label>
                      </span>
                      <p className="destination-check-why">{DESTINATION_CHECK_WHY}</p>
                    </div>
                  ) : null}
                </li>
              );
            })}
          </ul>
        )}
      </section>
    </div>
  );
}
