/**
 * PL-105: the panel's Tasks list. A task with spreadsheet columns opens the existing run flow;
 * a task taught with no columns is run once, with no upload and no batch to prepare.
 */

import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { apiPost, errorText } from "../lib/api";
import { knownBrowser } from "../lib/browser";
import { useJson } from "../lib/useJson";
import type { RunCreated, TaskRow } from "../types";

export function PanelTasks(): JSX.Element {
  const navigate = useNavigate();
  const tasks = useJson<TaskRow[]>("/api/v1/tasks");
  const rows = Array.isArray(tasks.data) ? tasks.data : null;
  const [running, setRunning] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);

  async function runOnce(task: TaskRow): Promise<void> {
    if (running !== null) return;
    setRunning(task.workflow_id);
    setProblem(null);
    const browser = knownBrowser();
    try {
      const created = await apiPost<RunCreated>(
        `/api/v1/tasks/${task.workflow_id}/run-once`,
        browser === null ? {} : { browser_id: browser },
      );
      navigate(`/runs/${created.run_id}`);
    } catch (failure) {
      setProblem(errorText(failure));
    } finally {
      setRunning(null);
    }
  }

  if (rows === null) {
    return <p className="panel-note">{tasks.error ?? "Loading your tasks…"}</p>;
  }

  if (rows.length === 0) {
    return (
      <div className="panel-empty">
        <p>The tasks you teach will appear here.</p>
        <Link className="btn quiet" to="/teach">
          Teach your first task
        </Link>
      </div>
    );
  }

  return (
    <section className="panel-card" aria-label="Tasks">
      <h1 className="panel-title">Tasks</h1>
      {problem === null ? null : (
        <p className="panel-problem" role="status">
          {problem}
        </p>
      )}
      <ul className="panel-list">
        {rows.map((task) => {
          const inputs = task.inputs ?? [];
          return (
            <li className="panel-task" key={task.workflow_id}>
              <div className="panel-task-text">
                <b>{task.name}</b>
                {task.app === null || task.app === undefined ? null : <small>{task.app}</small>}
              </div>
              {inputs.length === 0 ? (
                <button
                  type="button"
                  className="btn quiet"
                  disabled={running !== null}
                  onClick={() => void runOnce(task)}
                >
                  Run once
                </button>
              ) : (
                <Link className="btn quiet" to={`/tasks/${task.workflow_id}/run`}>
                  Run with a spreadsheet
                </Link>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}
