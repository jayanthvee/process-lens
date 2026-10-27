import { type CSSProperties } from "react";
import { Link, useSearchParams } from "react-router-dom";

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

  return (
    <div className="tasks">
      {csvLine}
      <section className="panel in" style={slot(1)}>
        <div className="panel-h">
          <h3>Your tasks</h3>
          <span className="count">{rows.length} saved</span>
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
            {rows.map((task) => {
              const action = taskAction(task);
              const result = taskResult(task);
              return (
                <tr key={task.workflow_id}>
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
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </section>
    </div>
  );
}
