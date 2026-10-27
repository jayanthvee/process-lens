import { type CSSProperties, useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { apiPost, errorText } from "../lib/api";
import { APPROVALS_PATH, approvalBody } from "../lib/approvals";
import { useJson } from "../lib/useJson";
import {
  isOpenStatus,
  refusalWords,
  runLineWords,
  runStatusLabel,
  runTimeWords,
  savedTimeWords,
  taskTitleWords,
} from "../lib/words";
import type { ApprovalRequest, OperatorInfo, RunSummary } from "../types";
import "./runs.css";

/** The blur-in stagger: the header and the list rise 70 ms apart (DESIGN.md §7). */
function slot(index: number): CSSProperties {
  return { "--i": index } as CSSProperties;
}

/**
 * The decision a run stopped for an approval is waiting on, drawn on the run's own line.
 *
 * The request comes from the run's own approvals route, so the words here are about this run and
 * not about "an approval somewhere". A decision is sent as the operator the API names; without one
 * there is nobody to record it, so both buttons stay off rather than sending an anonymous answer.
 */
function WaitingDecision({ runId, onDone }: { runId: string; onDone: () => void }): JSX.Element {
  const requests = useJson<ApprovalRequest[]>(`/api/v1/runs/${runId}/approvals`);
  const operator = useJson<OperatorInfo>("/api/v1/operator");

  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  const request = Array.isArray(requests.data) ? (requests.data[0] ?? null) : null;
  const operatorId = operator.data?.operator_id ?? null;
  const off = busy || request === null || operatorId === null;

  async function decide(decision: "approved" | "rejected"): Promise<void> {
    if (request === null || operatorId === null || busy) return;
    setBusy(true);
    setProblem(null);
    try {
      await apiPost(
        APPROVALS_PATH,
        approvalBody({ requestId: request.id, decision, operatorId }),
      );
      // The list shows the API's answer, never its own guess at what the run became.
      onDone();
    } catch (failure) {
      setProblem(refusalWords(errorText(failure)));
    } finally {
      setBusy(false);
    }
  }

  return (
    <span className="decision-buttons">
      <button
        type="button"
        className="btn quiet run-line-go"
        disabled={off}
        onClick={() => void decide("approved")}
      >
        Go ahead
      </button>
      <button
        type="button"
        className="btn quiet run-line-no"
        disabled={off}
        onClick={() => void decide("rejected")}
      >
        No, stop this row
      </button>
      {problem === null ? null : (
        <span className="run-line-error" role="status">
          {problem}
        </span>
      )}
    </span>
  );
}

/**
 * The two clicks a run stopped for any other reason offers where it stands: carry on, or let it
 * stop. Both reload the list, so what the API then reports is what the person reads.
 */
function PausedActions({ runId, onDone }: { runId: string; onDone: () => void }): JSX.Element {
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  async function act(action: "resume" | "cancel"): Promise<void> {
    if (busy) return;
    setBusy(true);
    setProblem(null);
    try {
      await apiPost(`/api/v1/runs/${runId}/${action}`);
      onDone();
    } catch (failure) {
      setProblem(refusalWords(errorText(failure)));
    } finally {
      setBusy(false);
    }
  }

  return (
    <span className="decision-buttons">
      <button
        type="button"
        className="btn quiet run-line-resume"
        disabled={busy}
        onClick={() => void act("resume")}
      >
        Resume
      </button>
      <button
        type="button"
        className="btn quiet run-line-stop"
        disabled={busy}
        onClick={() => void act("cancel")}
      >
        Stop
      </button>
      {problem === null ? null : (
        <span className="run-line-error" role="status">
          {problem}
        </span>
      )}
    </span>
  );
}

/**
 * The pauses whose next step is not a plain Resume: a code has to be typed, the work window has to
 * be brought forward, or the site's daily limit still stands. Those open the run, where the one
 * button that fits sits (PL-115 item 7 keeps every other run's action on its own line).
 */
const PAUSES_THAT_NEED_THE_RUN = new Set(["code_required", "window_hidden", "site_daily_cap"]);

/** Every run, live and past: one line each, saying what it did and what it needs. */
export function Runs(): JSX.Element {
  // A run that is still going keeps the list live; the moment none is, `pollMs` is null and the
  // interval stops, so a settled list makes no further requests.
  const [live, setLive] = useState(false);
  const { data, error, reload } = useJson<RunSummary[]>("/api/v1/runs", live ? 2000 : null);

  // The API always answers with a list, but a refusal or a stubbed response is not one, so the
  // page never assumes the shape and shows one sentence instead of throwing.
  const rows = Array.isArray(data) ? data : null;

  useEffect(() => {
    setLive((rows ?? []).some((run) => isOpenStatus(run.status)));
  }, [rows]);

  if (rows === null) {
    return (
      <div className="runs">
        <p className="lede in" style={slot(1)}>
          {error ?? "Loading your runs…"}
        </p>
      </div>
    );
  }

  if (rows.length === 0) {
    return (
      <div className="runs">
        <section className="panel in" style={slot(1)}>
          <div className="empty">
            <p className="empty-sentence">No runs yet. Pick a task in Tasks and press Run.</p>
            <p className="empty-action">
              <Link className="btn quiet" to="/tasks">
                Go to your tasks
              </Link>
            </p>
          </div>
        </section>
      </div>
    );
  }

  return (
    <div className="runs">
      <section className="panel in" style={slot(1)}>
        <div className="panel-h">
          <h3>All runs</h3>
          {live ? (
            <span className="live">
              <i /> Live
            </span>
          ) : null}
        </div>
        <ul className="run-list">
          {rows.map((run) => {
            const badge = runStatusLabel(run.status);
            const saved = savedTimeWords(run);
            return (
              <li className="run-line" key={run.id}>
                <b className="run-line-name">{taskTitleWords(run.recipe_name, null)}</b>
                <span className={"res " + badge.tone}>{badge.label}</span>
                <p className="run-line-sentence">{runLineWords(run)}</p>
                <div className="run-line-foot">
                  <time dateTime={run.created_at ?? undefined}>
                    {runTimeWords(run.created_at)}
                  </time>
                  <div className="run-line-actions">
                    {/* The click a waiting run needs belongs where the person reads about it. */}
                    {run.status !== "paused" ? null : run.pause_reason === "awaiting_approval" ? (
                      <WaitingDecision runId={run.id} onDone={reload} />
                    ) : PAUSES_THAT_NEED_THE_RUN.has(run.pause_reason ?? "") ? null : (
                      <PausedActions runId={run.id} onDone={reload} />
                    )}
                    <Link className="btn quiet" to={`/runs/${run.id}`}>
                      Open
                    </Link>
                  </div>
                </div>
                {saved === null ? null : <p className="run-line-saved">{saved}</p>}
              </li>
            );
          })}
        </ul>
      </section>
    </div>
  );
}
