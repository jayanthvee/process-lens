import { Fragment, type CSSProperties, useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { apiPost, errorText } from "../lib/api";
import { useJson } from "../lib/useJson";
import {
  isDefaultTaskName,
  isOpenStatus,
  pickTaskForCsv,
  relativeTime,
  runStatusLabel,
  siteWords,
  taskTitleWords,
} from "../lib/words";
import { KeepsFailingWarning } from "../components/KeepsFailingWarning";
import type { TaskRow } from "../types";
import "./tasks.css";

/** PL-120: how long the Undo line stays after a task leaves the list. */
const UNDO_MS = 10_000;

/** The blur-in stagger: the header and the table rise 70 ms apart (DESIGN.md §7). */
function slot(index: number): CSSProperties {
  return { "--i": index } as CSSProperties;
}

/**
 * The one quiet button a task row offers. The run flow arrives in a later ticket, so both "Run
 * again" and "Try one row" open the task page rather than starting a run. The Shell already draws
 * the page's one primary action ("Teach a new task"), so these stay quiet.
 */
function taskAction(task: TaskRow): { label: string; to: string } {
  if (task.last_run === null || task.last_run === undefined) {
    return { label: "Try one row", to: `/tasks/${task.workflow_id}` };
  }
  if (task.last_run.status === "running") {
    return { label: "Open", to: `/runs/${task.last_run.run_id}` };
  }
  return { label: "Run again", to: `/tasks/${task.workflow_id}` };
}

/**
 * A task's result in plain words, using only the five status words the owner fixed (PL-115 item 6).
 * A finished run that left rows for a person still says so, after the status, in a count.
 */
function taskResult(task: TaskRow): { label: string; tone: string } {
  const run = task.last_run;
  if (run === null || run === undefined) return { label: "Ready", tone: "idle" };
  const outcome = runStatusLabel(run.status);
  if (isOpenStatus(run.status)) return outcome;
  const needs = (run.counts?.parked ?? 0) + (run.counts?.failed ?? 0);
  if (run.status === "completed" && needs > 0) {
    return {
      label: `${outcome.label} · ${needs} ${needs === 1 ? "needs you" : "need you"}`,
      tone: "help",
    };
  }
  return outcome;
}

/** PL-120: the question a task asks before it leaves the list. */
function removeQuestion(name: string): string {
  return `Remove "${name}" from the list? Its past runs stay.`;
}

/** Every task that has been taught, in one table. */
export function Tasks(): JSX.Element {
  // PL-076: a cleaned document from the Spreadsheets page arrives as ?csv=<token> (and the
  // document's name, so the line can say which rows are waiting). The token rides along into the
  // task a person picks; the run page then uploads those rows as if the file had been picked.
  const [params] = useSearchParams();
  const csv = params.get("csv");
  const csvName = params.get("name") ?? "document.csv";
  const csvQuery =
    csv === null ? "" : `?csv=${encodeURIComponent(csv)}&name=${encodeURIComponent(csvName)}`;
  const csvLine =
    csv === null ? null : (
      <p className="csv-note in" style={slot(0)}>
        {pickTaskForCsv(csvName)}
      </p>
    );
  const carryOn = (to: string): string =>
    csvQuery !== "" && to.startsWith("/tasks/") ? `${to}${csvQuery}` : to;

  const { data, error } = useJson<TaskRow[]>("/api/v1/tasks");
  // A refusal (or a stubbed answer that is not a list) shows one sentence instead of throwing.
  const rows = Array.isArray(data) ? data : null;

  // PL-120: a removed task is hidden at once, and its Undo stays on screen for a moment. Nothing
  // is deleted: restore clears the API's mark, so the task and all its past runs come back.
  const [confirming, setConfirming] = useState<string | null>(null);
  const [hidden, setHidden] = useState<string[]>([]);
  const [problems, setProblems] = useState<Record<string, string>>({});
  const [lastRemoved, setLastRemoved] = useState<{ workflow_id: string; name: string } | null>(
    null,
  );
  const [undoProblem, setUndoProblem] = useState<string | null>(null);

  useEffect(() => {
    if (lastRemoved === null) return;
    const timer = window.setTimeout(() => setLastRemoved(null), UNDO_MS);
    return () => window.clearTimeout(timer);
  }, [lastRemoved]);

  function askRemove(task: TaskRow): void {
    setConfirming(task.workflow_id);
    setProblems((current) => {
      const next = { ...current };
      delete next[task.workflow_id];
      return next;
    });
  }

  async function removeTask(task: TaskRow): Promise<void> {
    setConfirming(null);
    try {
      await apiPost(`/api/v1/tasks/${task.workflow_id}/remove`);
      setHidden((current) =>
        current.includes(task.workflow_id) ? current : [...current, task.workflow_id],
      );
      setLastRemoved({ workflow_id: task.workflow_id, name: task.name });
      setUndoProblem(null);
      setProblems((current) => {
        const next = { ...current };
        delete next[task.workflow_id];
        return next;
      });
    } catch (failure) {
      // The API's sentence stays on the task's own row (a 409 says "Stop its run first.").
      setProblems((current) => ({ ...current, [task.workflow_id]: errorText(failure) }));
    }
  }

  async function undo(): Promise<void> {
    if (lastRemoved === null) return;
    const target = lastRemoved;
    try {
      await apiPost(`/api/v1/tasks/${target.workflow_id}/restore`);
      setHidden((current) => current.filter((id) => id !== target.workflow_id));
      setLastRemoved(null);
      setUndoProblem(null);
    } catch (failure) {
      setUndoProblem(errorText(failure));
    }
  }

  if (rows === null) {
    return (
      <div className="tasks">
        <p className="lede in" style={slot(1)}>
          {error ?? "Loading your tasks…"}
        </p>
      </div>
    );
  }

  if (rows.length === 0) {
    return (
      <div className="tasks">
        {csvLine}
        <section className="panel in" style={slot(1)}>
          <div className="empty">
            <p className="empty-sentence">The tasks you teach will appear here.</p>
            <p className="empty-action">
              <Link className="btn quiet" to="/teach">
                Teach your first task
              </Link>
            </p>
          </div>
        </section>
      </div>
    );
  }

  const visible = rows.filter((task) => !hidden.includes(task.workflow_id));

  return (
    <div className="tasks">
      {csvLine}
      {lastRemoved === null ? null : (
        <p className="task-removed in" style={slot(1)} role="status">
          Removed "{lastRemoved.name}".{" "}
          <button type="button" className="task-undo" onClick={() => void undo()}>
            Undo
          </button>
        </p>
      )}
      {undoProblem === null ? null : (
        <p className="task-undo-problem" role="status">
          {undoProblem}
        </p>
      )}
      <section className="panel in" style={slot(1)}>
        <div className="panel-h">
          <h3>Your tasks</h3>
          <span className="count">{visible.length} saved</span>
        </div>
        <table className="drop-second">
          <thead>
            <tr>
              <th>Task</th>
              <th>Last run</th>
              <th>Result</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {visible.map((task) => {
              const action = taskAction(task);
              const result = taskResult(task);
              const question = removeQuestion(task.name);
              return (
                <Fragment key={task.workflow_id}>
                  <tr>
                    <td>
                      <Link className="task-name" to={`/tasks/${task.workflow_id}`}>
                        {taskTitleWords(task.name, task.app)}
                      </Link>
                      {isDefaultTaskName(task.name) ? null : (
                        <small>{siteWords(task.app) ?? "No site yet"}</small>
                      )}
                      {/* PL-041: the task's last two runs both stopped the same way, so the task
                          itself needs recording again. The warning sits on the task's own row. */}
                      {task.keeps_failing == null ? null : (
                        <KeepsFailingWarning keepsFailing={task.keeps_failing} />
                      )}
                    </td>
                    <td>{relativeTime(task.last_run?.started_at ?? null)}</td>
                    <td>
                      <span className={`res ${result.tone}`}>{result.label}</span>
                    </td>
                    <td className="act">
                      <Link className="btn quiet" to={carryOn(action.to)}>
                        {action.label}
                      </Link>
                      <button
                        type="button"
                        className="btn quiet task-remove"
                        onClick={() => askRemove(task)}
                      >
                        Remove
                      </button>
                    </td>
                  </tr>
                  {confirming === task.workflow_id ? (
                    <tr className="task-confirm-row">
                      <td colSpan={4}>
                        <div
                          className="task-remove-confirm"
                          role="alertdialog"
                          aria-label={question}
                        >
                          <p>{question}</p>
                          <div className="task-remove-actions">
                            <button
                              type="button"
                              className="btn primary"
                              onClick={() => void removeTask(task)}
                            >
                              Remove
                            </button>
                            <button
                              type="button"
                              className="btn quiet"
                              onClick={() => setConfirming(null)}
                            >
                              Keep it
                            </button>
                          </div>
                        </div>
                      </td>
                    </tr>
                  ) : null}
                  {problems[task.workflow_id] === undefined ? null : (
                    <tr className="task-confirm-row">
                      <td colSpan={4}>
                        <p className="task-remove-problem" role="status">
                          {problems[task.workflow_id]}
                        </p>
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </section>
    </div>
  );
}
