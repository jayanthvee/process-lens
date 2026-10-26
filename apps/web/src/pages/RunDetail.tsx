import { Fragment, type CSSProperties, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { ApprovalPanel } from "../components/ApprovalPanel";
import { DestinationCheck } from "../components/DestinationCheck";
import { WaitingRun } from "../components/WaitingRun";
import { apiPost, errorText } from "../lib/api";
import { useSessionPath } from "../lib/browser";
import { POLL_INTERVAL_MS, useJson } from "../lib/useJson";
import {
  RUNNING_AGAIN,
  CODE_FROM_EMAIL_BUTTON,
  CODE_FROM_EMAIL_LATER,
  CODE_FROM_EMAIL_OFFER,
  CODE_NOT_FOUND,
  CODE_STILL_ASKED,
  LOOKING_IN_GMAIL,
  codeFromEmailWords,
  ENTERING_CODE,
  codePromptWords,
  isOpenStatus,
  minutesSavedWords,
  pauseReasonWords,
  plainReason,
  refusalWords,
  siteCapReached,
  siteCapWords,
  rowOutcome,
  runActions,
  runStatusWords,
  timeComparisonWords,
} from "../lib/words";
import type { ExecutorSession, OperatorInfo, RecordRow, RunDetail as RunDetailPayload } from "../types";
import {
  actionGate,
  recordActionLabel,
  sendRecordAction,
  type RecordAction,
} from "./needsHelpActions";
import "./runs.css";

type RunAction = "pause" | "resume" | "cancel";

/**
 * PL-061: the time comparison the API now sends on a finished run. Declared on top of the shared
 * payload type here, so this screen can read the new fields while the rest of the app catches up.
 */
type RunDetailTime = RunDetailPayload & {
  manual_seconds_per_row?: number | null;
  automated_seconds_per_row?: number | null;
  saved_seconds?: number | null;
};

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
  const run = useJson<RunDetailTime>(runPath, pollMs);
  const records = useJson<RecordRow[]>(recordsPath, pollMs);
  // Whether Chrome is attached right now: a queued run with no session is waiting for the person
  // to open the extension, not for the engine (PL-033 requirement 4).
  const session = useJson<ExecutorSession>(useSessionPath(), pollMs);

  const detail = run.data;
  const rows = records.data ?? [];
  const open = isOpenStatus(detail?.status ?? null);
  const time = {
    manualSecondsPerRow: detail?.manual_seconds_per_row ?? null,
    automatedSecondsPerRow: detail?.automated_seconds_per_row ?? null,
    savedSeconds: detail?.saved_seconds ?? null,
  };

  useEffect(() => {
    const next = isOpenStatus(detail?.status ?? null) ? POLL_INTERVAL_MS : null;
    setPollMs((current) => (current === next ? current : next));
  }, [detail]);

  // A run that finished once. If it is open again, a row was put back in the queue, and the status
  // says "Running again" instead of pretending this is the first time (PL-038).
  const [wasFinished, setWasFinished] = useState(false);
  useEffect(() => {
    if (detail !== null && typeof detail.status === "string" && !isOpenStatus(detail.status)) {
      setWasFinished(true);
    }
  }, [detail]);

  const [busy, setBusy] = useState<RunAction | null>(null);
  const [refusal, setRefusal] = useState<{ action: RunAction; text: string } | null>(null);
  // The operator identity: the approval panel answers a decision as the person the API names, and
  // the row actions below need the same identity before they can put a row back in the queue.
  const operator = useJson<OperatorInfo>("/api/v1/operator");

  // The row actions: the same two Needs help offers, with the same safety tick (PL-038). The ticks
  // belong to one person's visit: nothing on this page ticks a box itself, and a reload leaves
  // every box clear.
  const [confirmed, setConfirmed] = useState<Record<string, boolean>>({});
  const [rowBusy, setRowBusy] = useState<string | null>(null);
  const [rowErrors, setRowErrors] = useState<Record<string, string>>({});
  const [batchBusy, setBatchBusy] = useState(false);
  const [batchError, setBatchError] = useState<string | null>(null);
  // PL-068: a run paused for the site's verification code takes the code right here. The state
  // lives with every other hook, above the loading return, and the value is cleared on send.
  const [code, setCode] = useState("");
  const [codeBusy, setCodeBusy] = useState(false);
  const [codeProblem, setCodeProblem] = useState<string | null>(null);
  const [lookingInGmail, setLookingInGmail] = useState(false);
  // PL-041: the in-flight flag for Try all again. It is a ref because the second click of a double
  // click arrives before `batchBusy` re-renders the button off.
  const tryingAll = useRef(false);

  const operatorId = operator.data?.operator_id ?? null;
  const operatorProblem = operator.error;

  function gateFor(row: RecordRow) {
    return actionGate({
      state: row.state,
      commitAttempted: row.commit_attempted === true,
      confirmed: confirmed[row.id] === true,
      operatorId,
      operatorUnavailable: operatorProblem !== null,
    });
  }

  function ticket(rowId: string, checked: boolean): void {
    setConfirmed((current) => ({ ...current, [rowId]: checked }));
  }

  async function rowAct(row: RecordRow, action: RecordAction): Promise<void> {
    const gate = gateFor(row);
    if (gate.disabled || gate.shown === false || rowBusy !== null || batchBusy) return;
    setRowBusy(row.id);
    setRowErrors((current) => {
      const next = { ...current };
      delete next[row.id];
      return next;
    });
    try {
      await sendRecordAction(row.id, action, {
        commitAttempted: row.commit_attempted === true,
        confirmed: confirmed[row.id] === true,
        operatorId,
      });
      // The page shows the API's answer, never its own guess at what a row became.
      records.reload();
      run.reload();
    } catch (failure) {
      setRowErrors((current) => ({ ...current, [row.id]: refusalWords(errorText(failure)) }));
    } finally {
      setRowBusy(null);
    }
  }

  /**
   * Put every row this page can try again back in the queue, one request at a time. A row that may
   * already have been saved is never swept up here: it needs its own tick, and the page names it.
   */
  async function tryAllAgain(): Promise<void> {
    if (tryingAll.current) return;
    const eligible = rows.filter((row) => {
      const gate = gateFor(row);
      return gate.shown && !gate.disabled;
    });
    if (eligible.length === 0) return;
    tryingAll.current = true;
    setBatchBusy(true);
    setBatchError(null);
    try {
      for (const row of eligible) {
        await sendRecordAction(row.id, "requeue", {
          commitAttempted: row.commit_attempted === true,
          confirmed: confirmed[row.id] === true,
          operatorId,
        });
      }
    } catch (failure) {
      setBatchError(refusalWords(errorText(failure)));
    } finally {
      tryingAll.current = false;
      setBatchBusy(false);
      // Even a refusal part-way through leaves some rows re-queued, so reload either way.
      records.reload();
      run.reload();
    }
  }

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

  const status = wasFinished && isOpenStatus(detail.status)
    ? RUNNING_AGAIN
    : runStatusWords(detail.status, session.data?.attached);
  const buttons = runActions(detail.status);
  // Every count comes from the API, and a run with no records counts as nothing at all.
  const counts = (detail.counts ?? {}) as Record<string, number>;
  const done = doneCount(counts);
  const total = totalCount(counts);
  const alreadyThere = counts.skipped ?? 0;
  const pauseReason = pauseReasonWords(detail.pause_reason);
  // PL-067: a `site_daily_cap` pause names the site and its limit, and the Resume button stays
  // disabled while the count stands. The action still sits on this screen, per the owner's rule.
  const siteCap = detail.site_cap ?? null;
  const capSentence = siteCap ? siteCapWords(siteCap) : null;
  const capReached = siteCap !== null && siteCapReached(siteCap);
  const wantsCode = detail.pause_reason === "code_required";
  const runId = detail.id;

  async function sendCode(): Promise<void> {
    const value = code.trim();
    if (codeBusy || !/^[0-9A-Za-z]{4,10}$/.test(value)) return;
    setCodeBusy(true);
    setCodeProblem(null);
    try {
      const answer = (await apiPost(`/api/v1/runs/${runId}/code`, { code: value })) as {
        entered?: boolean;
        resumed?: boolean;
      };
      if (answer?.entered !== true) setCodeProblem(CODE_NOT_FOUND);
      // PL-102: a code that took the page on resumes the run by itself; one that did not says so.
      else if (answer.resumed === false) setCodeProblem(CODE_STILL_ASKED);
    } catch (failure) {
      setCodeProblem(refusalWords(errorText(failure)));
    } finally {
      // The code is never kept: not in state, not on screen, not anywhere after the send.
      setCode("");
      setCodeBusy(false);
      run.reload();
    }
  }
  // PL-102: the person's yes to reading the code from their Gmail. The work window reads and types
  // it; the code never reaches this page or the API.
  async function readCodeFromEmail(): Promise<void> {
    if (codeBusy) return;
    setCodeBusy(true);
    setLookingInGmail(true);
    setCodeProblem(null);
    try {
      const answer = (await apiPost(`/api/v1/runs/${runId}/code-from-email`, {
        remember: true,
      })) as { entered?: boolean; reason?: string; resumed?: boolean };
      if (answer?.entered !== true) setCodeProblem(codeFromEmailWords(String(answer?.reason ?? "")));
      else if (answer.resumed === false) setCodeProblem(CODE_STILL_ASKED);
    } catch (failure) {
      setCodeProblem(refusalWords(errorText(failure)));
    } finally {
      setCodeBusy(false);
      setLookingInGmail(false);
      run.reload();
    }
  }
  const started = stamp(detail.started_at);
  const finished = stamp(detail.finished_at);
  const request = detail.open_approval;

  // The rows a person can put back in the queue from here, and the ones that must be checked in
  // Odoo first. The batch action is offered only for a finished run, and only when it would do
  // more than one row's work (PL-038).
  const rowGates = rows.map((row) => ({ row, gate: gateFor(row) }));
  const tryable = rowGates.filter(({ gate }) => gate.shown && !gate.disabled).map(({ row }) => row);
  const needCheck = rowGates
    .filter(({ row, gate }) => gate.shown && gate.needsConfirmation && confirmed[row.id] !== true)
    .map(({ row }) => row);
  const showBatch = !open && tryable.length >= 2;

  // "About 52 minutes saved": the rows this run finished would have taken about three minutes each
  // by hand, less the wall-clock the run itself took. Only a finished run has both times.
  const minutes =
    detail.started_at != null && detail.finished_at != null
      ? (new Date(detail.finished_at).getTime() - new Date(detail.started_at).getTime()) / 60000
      : null;
  // PL-061: the API measures both sides, so the figures come from it when it sent them. The old
  // three-minute arithmetic stays only as the fallback for a payload that predates the fields.
  const saved =
    !open && time.savedSeconds !== null
      ? minutesSavedWords(time.savedSeconds / 60)
      : !open && minutes !== null
        ? minutesSavedWords(done * 3 - minutes)
        : null;
  // The one comparison line, under the outcome: by hand, ProcessLens, and the saving. A finished
  // run that has done no row has nothing to compare yet.
  const comparison =
    open || done === 0
      ? null
      : timeComparisonWords({
          manualSecondsPerRow: time.manualSecondsPerRow,
          automatedSecondsPerRow: time.automatedSecondsPerRow,
          savedSeconds: time.savedSeconds,
        });

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
        {/* A queued run that is behind another task's run on the same app says so here, first:
            the status "Queued" was the whole story on screen and it was not the whole story
            (PL-041). */}
        {detail.waiting_behind === null || detail.waiting_behind === undefined ? null : (
          <WaitingRun waiting={detail.waiting_behind} onCancelled={run.reload} />
        )}
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
          {comparison === null ? null : (
            <p className="times time-compare">{comparison}</p>
          )}
        </div>
        <p className="pause-reason">{capSentence ?? pauseReason ?? "Not paused."}</p>
        {capReached ? (
          <p className="pause-reason cap-note">
            The count resets at midnight UTC. This button works again then.
          </p>
        ) : null}
        {!wantsCode ? null : (
          <div className="code-prompt" role="status">
            <p className="code-prompt-sentence">{codePromptWords(null)}</p>
            <form
              className="code-prompt-form"
              onSubmit={(event) => {
                event.preventDefault();
                void sendCode();
              }}
            >
              <label className="code-prompt-label" htmlFor="verification-code">
                Verification code
              </label>
              <input
                id="verification-code"
                className="code-prompt-input"
                autoComplete="one-time-code"
                inputMode="numeric"
                maxLength={10}
                value={code}
                disabled={codeBusy}
                onChange={(event) => setCode(event.target.value)}
              />
              <button type="submit" className="btn quiet code-prompt-continue" disabled={codeBusy}>
                Continue
              </button>
            </form>
            <div className="code-prompt-email">
              <p className="code-prompt-note">{CODE_FROM_EMAIL_OFFER}</p>
              <button
                type="button"
                className="btn code-prompt-from-email"
                disabled={codeBusy}
                onClick={() => void readCodeFromEmail()}
              >
                {CODE_FROM_EMAIL_BUTTON}
              </button>
              <p className="code-prompt-note">{CODE_FROM_EMAIL_LATER}</p>
            </div>
            {codeBusy ? (
              <p className="code-prompt-note">{lookingInGmail ? LOOKING_IN_GMAIL : ENTERING_CODE}</p>
            ) : null}
            {codeProblem === null ? null : (
              <p className="code-prompt-note code-prompt-problem">{codeProblem}</p>
            )}
          </div>
        )}
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
                disabled={busy !== null || capReached}
                onClick={() => void act("resume")}
              >
                Resume
              </button>
              {capReached ? (
                <span className="refusal cap-note" role="status">
                  {capSentence}
                </span>
              ) : null}
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
          <small>
            {time.manualSecondsPerRow === null
              ? "vs. about 3 minutes a row by hand (estimated)"
              : "vs. the time each row took by hand"}
          </small>
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
        {showBatch ? (
          <div className="row-batch">
            <button
              type="button"
              className="btn quiet run-try-all"
              disabled={batchBusy || rowBusy !== null}
              onClick={() => void tryAllAgain()}
            >
              Try all {tryable.length} again
            </button>
            {needCheck.map((row) => (
              <span className="row-batch-note" key={row.id}>
                {row.record_key} needs a check first.
              </span>
            ))}
            {batchError === null ? null : (
              <span className="refusal" role="status">
                {batchError}
              </span>
            )}
          </div>
        ) : null}
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
                <th />
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => {
                const outcome = rowOutcome(row.state);
                const reason = reasonWords(row, outcome.tone === "help");
                const gate = gateFor(row);
                const ticked = confirmed[row.id] === true;
                const boxId = `checked-odoo-${row.id}`;
                return (
                  <Fragment key={row.id}>
                    <tr>
                      {/* A row's key is the person's or thing's name for that row; it is data, so
                          it is rendered as text and never as markup. */}
                      <td>
                        <b>{row.record_key}</b>
                      </td>
                      <td>
                        <span className={`res ${outcome.tone}`}>{outcome.label}</span>
                      </td>
                      <td>{reason ?? "—"}</td>
                      <td className="act">
                        {gate.shown === false ? null : (
                          <span className="record-buttons">
                            <button
                              type="button"
                              className="btn quiet record-requeue"
                              disabled={gate.disabled || rowBusy !== null || batchBusy}
                              onClick={() => void rowAct(row, "requeue")}
                            >
                              {recordActionLabel("requeue")}
                            </button>
                            <button
                              type="button"
                              className="btn quiet record-dismiss"
                              disabled={gate.disabled || rowBusy !== null || batchBusy}
                              onClick={() => void rowAct(row, "dismiss")}
                            >
                              {recordActionLabel("dismiss")}
                            </button>
                          </span>
                        )}
                        {rowErrors[row.id] === undefined ? null : (
                          <span className="refusal" role="status">
                            {rowErrors[row.id]}
                          </span>
                        )}
                      </td>
                    </tr>
                    {/* The same tick Needs help shows, under the row it belongs to: the commit
                        window's safety rule is not optional, and it is never one click. */}
                    {gate.needsConfirmation ? (
                      <tr className="row-check">
                        <td colSpan={4}>
                          <DestinationCheck
                            id={boxId}
                            checked={ticked}
                            onChange={(checked) => ticket(row.id, checked)}
                          />
                        </td>
                      </tr>
                    ) : null}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}
