import { type CSSProperties, useState } from "react";

import { apiPost, errorText } from "../lib/api";
import { APPROVALS_PATH, approvalBody, approvalLines } from "../lib/approvals";
import { useJson } from "../lib/useJson";
import { operatorNote, refusalWords } from "../lib/words";
import type { OperatorInfo, RunDetail as RunDetailPayload } from "../types";
import "./approvalPanel.css";

type Decision = "approved" | "rejected";

/** The blur-in stagger for the section (DESIGN.md §7). */
function slot(index: number): CSSProperties {
  return { "--i": index } as CSSProperties;
}

/**
 * PL-015's approval decision, carried forward: the run is stopped at a gate, and this is where a
 * person looks at the exact values and says yes or no.
 *
 * It lives in one component because the decision belongs on whichever screen the run stopped on
 * (PL-038): the run page and the "Try one row" card render the same panel, with the same words, so
 * a person never has to know to go to another page. `onDecided` is the owner of the run's data:
 * both pages reload the run, which is how the panel and the card move on.
 */
export function ApprovalPanel({
  detail,
  onDecided,
}: {
  detail: RunDetailPayload;
  onDecided: () => void;
}): JSX.Element | null {
  // The operator identity, and the decision on the request the run is stopped at: a request is
  // answered as the person the API names, and when the API names nobody every decision is off and
  // the panel says why.
  const operator = useJson<OperatorInfo>("/api/v1/operator");
  const [deciding, setDeciding] = useState<Decision | null>(null);
  const [decisionRefusal, setDecisionRefusal] = useState<string | null>(null);

  const request = detail.open_approval;
  const shown = approvalLines(request?.shown);
  const operatorProblem = operator.error;
  const decisionsOff = operatorProblem !== null;

  async function decide(decision: Decision): Promise<void> {
    if (request === null || request === undefined) return;
    setDeciding(decision);
    setDecisionRefusal(null);
    try {
      await apiPost(
        APPROVALS_PATH,
        approvalBody({
          requestId: request.id,
          decision,
          operatorId: operator.data?.operator_id ?? null,
        }),
      );
      onDecided();
    } catch (failure) {
      setDecisionRefusal(refusalWords(errorText(failure)));
    } finally {
      setDeciding(null);
    }
  }

  if (request === null || request === undefined) return null;

  return (
    <section className="panel in" style={slot(3)}>
      <div className="panel-h">
        <h3>Waiting for your decision</h3>
        <span className="res live">Waiting</span>
      </div>
      <div className="approval">
        <p className="approval-ask">
          This run stopped before saving one row. Check these values, then say whether to go ahead.
        </p>
        {shown.length === 0 ? null : (
          <dl className="approval-values">
            {shown.map((line, index) => (
              <div className="approval-line" key={`${line.label}-${index}`}>
                <dt>{line.label}</dt>
                <dd>{line.value}</dd>
              </div>
            ))}
          </dl>
        )}
        {decisionsOff ? (
          <p className="operator-note" role="status">
            {operatorNote(operatorProblem)}
          </p>
        ) : null}
        <div className="actions">
          <button
            type="button"
            className="btn primary approval-approved"
            disabled={deciding !== null || decisionsOff}
            onClick={() => void decide("approved")}
          >
            Go ahead
          </button>
          <button
            type="button"
            className="btn quiet approval-rejected"
            disabled={deciding !== null || decisionsOff}
            onClick={() => void decide("rejected")}
          >
            No, stop this row
          </button>
          {decisionRefusal === null ? null : (
            <span className="refusal" role="status">
              {decisionRefusal}
            </span>
          )}
        </div>
      </div>
    </section>
  );
}
