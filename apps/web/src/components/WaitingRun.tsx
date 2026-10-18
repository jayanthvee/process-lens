/**
 * "Waiting: your Send invoices run on this app is paused."
 *
 * The database allows one running-or-paused run per app, so a queued run can be stuck behind
 * another task's run on the same site. Nothing on screen used to say so (PL-041). The block sits
 * where the queued run shows, names the run ahead in plain words, and offers the two things a
 * person can do about it: look at it, or stop it.
 */

import { useState } from "react";
import { Link } from "react-router-dom";

import { apiPost, errorText } from "../lib/api";
import { refusalWords } from "../lib/words";
import type { WaitingBehind } from "../types";
import "./waitingRun.css";

interface WaitingRunProps {
  /** The run this one is waiting behind: the API's own `waiting_behind`. */
  waiting: WaitingBehind;
  /** Called once the run ahead has been asked to stop, so the page can ask again. */
  onCancelled: () => void;
}

export function WaitingRun({ waiting, onCancelled }: WaitingRunProps): JSX.Element {
  const [busy, setBusy] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const paused = waiting.status === "paused";

  async function cancelThatRun(): Promise<void> {
    if (busy) return;
    setBusy(true);
    setProblem(null);
    try {
      await apiPost(`/api/v1/runs/${waiting.run_id}/cancel`);
      setStopping(true);
      onCancelled();
    } catch (failure) {
      setProblem(refusalWords(errorText(failure)));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="waiting-run" role="status">
      <p className="waiting-run-sentence">
        {paused ? (
          <>
            Waiting: your <b>{waiting.task_name}</b> run on this app is paused. Only one run per app
            can go at a time.
          </>
        ) : (
          <>
            Waiting for your <b>{waiting.task_name}</b> run on this app to finish.
          </>
        )}
      </p>
      <span className="waiting-run-actions">
        <Link className="btn quiet waiting-run-open" to={`/runs/${waiting.run_id}`}>
          Open it
        </Link>
        {!paused ? null : stopping ? (
          <span className="waiting-run-note">
            Stopping that run. This one starts when it has.
          </span>
        ) : (
          <button
            type="button"
            className="btn quiet waiting-run-cancel"
            disabled={busy}
            onClick={() => void cancelThatRun()}
          >
            Cancel that run
          </button>
        )}
      </span>
      {problem === null ? null : (
        <span className="refusal waiting-run-problem" role="status">
          {problem}
        </span>
      )}
    </div>
  );
}
