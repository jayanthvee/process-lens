import { type CSSProperties, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { ApprovalPanel } from "../components/ApprovalPanel";
import { apiPost, errorText } from "../lib/api";
import { POLL_INTERVAL_MS, useJson } from "../lib/useJson";
import {
  isOpenStatus,
  minutesSavedWords,
  pauseReasonWords,
  plainReason,
  refusalWords,
  rowOutcome,
  runActions,
  runStatusWords,
} from "../lib/words";
import type { ExecutorSession, OperatorInfo, RecordRow, RunDetail as RunDetailPayload } from "../types";
import "./runs.css";

type RunAction = "pause" | "resume" | "cancel";

/** The blur-in stagger for the sections (DESIGN.md §7). */
function slot(index: number): CSSProperties {
  return { "--i": index } as CSSProperties;
}

/** The rows of a run that are finished: added rows plus rows that were already there. */
function doneCount(counts: Record<string, number>): number {
  return (counts.verified ?? 0) + (counts.skipped ?? 0);
}

/** The run's rows. `GET /api/v1/runs/{id}` counts every state and carries the explicit total. */
function totalCount(counts: Record<string, number>): number {
  const explicit = counts.total;
  if (typeof explicit === "number") return explicit;
  return Object.values(counts).reduce((sum, value) => sum + value, 0);
}

/** A time a person can read, only when the API sent one. */
function stamp(iso: string | null | undefined): string | null {
  if (iso === null || iso === undefined || iso.trim() === "") return null;
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return null;
  return at.toLocaleString();
}

/**
 * What the reason column says for one row.
 *
 * A row that finished has nothing to explain, so an empty reason stays empty instead of being
 * filled in with a sentence that is not true about it. A row that needs a person always says
 * something, so the fallback belongs there and only there.
 */
function reasonWords(row: RecordRow, needsHelp: boolean): string | null {
  const written = (row.reason ?? "").trim();
  if (written !== "") return plainReason(row.reason, row.state);
  return needsHelp ? plainReason(null, row.state) : null;
}

/** One run: live progress, its buttons, and what every row ended up as. */
export function RunDetail(): JSX.Element {
  const { id } = useParams<{ id: string }>();
  const runPath = id === undefined ? null : `/api/v1/runs/${id}`;
  const recordsPath = id === undefined ? null : `/api/v1/runs/${id}/records`;

  // The poll starts only once the run is known to be open. Starting at 2 s would give a run that
  // is already finished one more request two seconds after the page loaded, for nothing
  // (PL-015's "does not poll a run that is already finished", carried forward).
  const [pollMs, setPollMs] = useState<number | null>(null);
  const run = useJson<RunDetailPayload>(runPath, pollMs);
  const records = useJson<RecordRow[]>(recordsPath, pollMs);
  // Whether Chrome is attached right now: a queued run with no session is waiting for the person
  // to open the extension, not for the engine (PL-033 requirement 4).
  const session = useJson<ExecutorSession>("/api/v1/executor/session", pollMs);

  const detail = run.data;
  const rows = records.data ?? [];
  const open = isOpenStatus(detail?.status ?? null);

  useEffect(() => {
    const next = isOpenStatus(detail?.status ?? null) ? POLL_INTERVAL_MS : null;
    setPollMs((current) => (current === next ? current : next));
  }, [detail]);

  const [busy, setBusy] = useState<RunAction | null>(null);
  const [refusal, setRefusal] = useState<{ action: RunAction; text: string } | null>(null);
  // The operator identity: the approval panel answers a decision as the person the API names, and
  // the row actions below need the same identity before they can put a row back in the queue.
  const operator = useJson<OperatorInfo>("/api/v1/operator");

  async function act(action: RunAction): Promise<void> {
    if (id === undefined) return;
    setBusy(action);
    setRefusal(null);
    try {
      await apiPost(`/api/v1/runs/${id}/${action}`);
      run.reload();
    } catch (failure) {
      // The API's own `detail` text, translated so no engine word reaches the person, shown next to
      // the button that caused it.
      setRefusal({ action, text: refusalWords(errorText(failure)) });
    } finally {
      setBusy(null);
    }
  }

  if (detail === null || typeof detail.status !== "string") {
    return (
      <div className="run-detail">
        <p className="lede in" style={slot(1)}>
          {run.error ?? "Loading this run…"}
        </p>
      </div>
    );
  }

  const status = runStatusWords(detail.status, session.data?.attached);
  const buttons = runActions(detail.status);
  // Every count comes from the API, and a run with no records counts as nothing at all.
  const counts = (detail.counts ?? {}) as Record<string, number>;
  const done = doneCount(counts);
  const total = totalCount(counts);
  const alreadyThere = counts.skipped ?? 0;
  const pauseReason = pauseReasonWords(detail.pause_reason);
  const started = stamp(detail.started_at);
  const finished = stamp(detail.finished_at);
  const request = detail.open_approval;

  // "About 52 minutes saved": the rows this run finished would have taken about three minutes each
  // by hand, less the wall-clock the run itself took. Only a finished run has both times.
  const minutes =
    detail.started_at != null && detail.finished_at != null
      ? (new Date(detail.finished_at).getTime() - new Date(detail.started_at).getTime()) / 60000
      : null;
  const saved = !open && minutes !== null ? minutesSavedWords(done * 3 - minutes) : null;

  return (
    <div className="run-detail">
      <section className="panel in" style={slot(1)}>
        <div className="panel-h">
          <h3>Progress</h3>
          {open ? (
            <span className="live">
              <i /> Live
            </span>
          ) : null}
        </div>
        <div className="run">
          <b>
            {done} of {total} done
          </b>
          <div className="track" aria-hidden="true">
            <i style={{ width: `${total === 0 ? 0 : Math.round((done / total) * 100)}%` }} />
          </div>
          <div className="meta">
            <span className={`res ${status.tone}`}>{status.label}</span>
            <span>{alreadyThere} already there</span>
          </div>
        </div>
        <p className="pause-reason">{pauseReason ?? "Not paused."}</p>
        {started === null && finished === null ? null : (
          <p className="times">
            {started === null ? null : (
              <span>
                Started <time dateTime={detail.started_at ?? undefined}>{started}</time>
              </span>
            )}
            {finished === null ? null : (
              <span>
                Finished <time dateTime={detail.finished_at ?? undefined}>{finished}</time>
              </span>
            )}
          </p>
        )}
        <div className="actions">
          {buttons.pause ? (
            <span className="run-action">
              <button
                type="button"
                className="btn quiet run-action-pause"
                disabled={busy !== null}
                onClick={() => void act("pause")}
              >
                Pause
              </button>
              {/* The refusal sits in the same element as the button that caused it. */}
              {refusal?.action === "pause" ? (
                <span className="refusal action-error" role="status">
                  {refusal.text}
                </span>
              ) : null}
            </span>
          ) : null}
          {buttons.resume ? (
            <span className="run-action">
              <button
                type="button"
                className="btn quiet run-action-resume"
                disabled={busy !== null}
                onClick={() => void act("resume")}
              >
                Resume
              </button>
              {refusal?.action === "resume" ? (
                <span className="refusal action-error" role="status">
                  {refusal.text}
                </span>
              ) : null}
            </span>
          ) : null}
          {buttons.cancel ? (
            <span className="run-action">
              <button
                type="button"
                className="btn quiet run-action-cancel"
                disabled={busy !== null}
                onClick={() => void act("cancel")}
              >
                Cancel
              </button>
              {refusal?.action === "cancel" ? (
                <span className="refusal action-error" role="status">
                  {refusal.text}
                </span>
              ) : null}
            </span>
          ) : null}
          <a className="btn quiet export" href={`/api/v1/runs/${detail.id}/results.csv`}>
            Export CSV
          </a>
        </div>
      </section>

      {saved === null ? null : (
        <section className="saved-fig in" style={slot(2)}>
          <b>{saved}</b>
          <span>Time saved</span>
          <small>vs. about 3 minutes a row by hand</small>
        </section>
      )}

      {/* PL-015's approval decision, carried forward in one shared panel: the run is stopped at a
          gate, and this is where a person looks at the exact values and says yes or no. */}
      {request === null ? null : <ApprovalPanel detail={detail} onDecided={run.reload} />}

      <section className="panel in" style={slot(3)}>
        <div className="panel-h">
          <h3>What happened to each row</h3>
          <Link to="/needs-help">Needs help</Link>
        </div>
        {rows.length === 0 ? (
          <p className="panel-note">
            {records.error ?? "The rows of this run will appear here."}
          </p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Row</th>
                <th>Result</th>
                <th>Reason</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => {
                const outcome = rowOutcome(row.state);
                const reason = reasonWords(row, outcome.tone === "help");
                return (
                  <tr key={row.id}>
                    {/* A row's key is the person's or thing's name for that row; it is data, so it
                        is rendered as text and never as markup. */}
                    <td>
                      <b>{row.record_key}</b>
                    </td>
                    <td>
                      <span className={`res ${outcome.tone}`}>{outcome.label}</span>
                    </td>
                    <td>{reason ?? "—"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}
