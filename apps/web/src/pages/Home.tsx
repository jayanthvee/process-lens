import { type CSSProperties, useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { useJson } from "../lib/useJson";
import {
  estimatedRowsNote,
  isOpenStatus,
  plainReason,
  relativeTime,
  runStatusWords,
  savedFigureWords,
} from "../lib/words";
import type { HomeSummary, TaskRow } from "../types";
import "./home.css";

/**
 * PL-061: the real time comparison the API now sends with the week. Declared on top of the shared
 * summary type here, so Home can read it while the rest of the app catches up.
 */
type HomeSummaryTime = HomeSummary & {
  saved_seconds?: number;
  estimated_rows?: number;
};

/** The blur-in stagger: the hero and the panels rise in 70 ms apart (DESIGN.md §7). */
function slot(index: number): CSSProperties {
  return { "--i": index } as CSSProperties;
}

/**
 * The one quiet button a task row offers. The run flow itself arrives in a later ticket, so both
 * "Run again" and "Try one row" open the task page rather than starting a run.
 */
function taskAction(task: TaskRow): { label: string; to: string } {
  if (task.last_run !== null && task.last_run.status === "running") {
    return { label: "Open", to: `/runs/${task.last_run.run_id}` };
  }
  return task.last_run === null
    ? { label: "Try one row", to: `/tasks/${task.workflow_id}` }
    : { label: "Run again", to: `/tasks/${task.workflow_id}` };
}

/** A task's result in plain words, or how many of its rows need a person (DESIGN.md §2). */
function taskResult(task: TaskRow): { label: string; tone: string } {
  const run = task.last_run;
  if (run === null) return { label: "Ready", tone: "idle" };
  const failed = run.counts.failed ?? 0;
  const parked = run.counts.parked ?? 0;
  // A run that is still going leads with its own status; a finished one leads with any row that
  // needs a person.
  if (isOpenStatus(run.status)) {
    const outcome = runStatusWords(run.status);
    return { label: outcome.label, tone: outcome.tone };
  }
  if (failed > 0) return { label: "Didn't work", tone: "help" };
  if (parked > 0) return { label: `${parked} needs help`, tone: "help" };
  const outcome = runStatusWords(run.status);
  return { label: outcome.label, tone: outcome.tone };
}

/**
 * Home: the week at a glance. The Shell already renders the page's one primary action
 * ("Teach a new task"), so every button this page draws itself is quiet.
 */
export function Home(): JSX.Element {
  // A run that is still going keeps the page live. The moment none is, `pollMs` is null and
  // `useJson` clears the interval, so a finished week makes no more requests.
  const [live, setLive] = useState(false);
  const { data, error } = useJson<HomeSummaryTime>("/api/v1/home", live ? 2000 : null);

  useEffect(() => {
    setLive((data?.running?.length ?? 0) > 0);
  }, [data]);

  // A body that is not a week's summary -- a refusal, or a Shell test that never asked for one --
  // shows one sentence instead of throwing.
  const counts = data?.counts;
  if (data === null || counts === undefined) {
    return (
      <div className="home">
        <p className="lede">{error ?? "Loading your week…"}</p>
      </div>
    );
  }

  const running = data.running ?? [];
  const needsHelp = data.needs_help ?? [];
  const tasks = data.tasks ?? [];
  const nothing = counts.done === 0 && counts.needs_help === 0;
  // PL-061: the figure is the API's own saved seconds, and the note says when part of it had to be
  // estimated because a task never measured itself. A payload without the new fields keeps the old
  // figure and the old note.
  const savedSeconds =
    data.saved_seconds !== undefined ? data.saved_seconds : (data.minutes_saved ?? 0) * 60;
  const estimateNote = estimatedRowsNote(data.estimated_rows);

  return (
    <div className="home">
      <section className="hero">
        <div>
          <h1 className="big in" style={slot(1)}>
            {nothing ? (
              "Nothing has run yet."
            ) : (
              <>
                {counts.done} rows done this week.
                <br />
                <span>{counts.needs_help} need you.</span>
              </>
            )}
          </h1>
          <p className="lede in" style={slot(2)}>
            {counts.already_there === 0
              ? "Nothing was entered twice."
              : `${counts.already_there} rows were already there.`}
          </p>
          {nothing ? (
            <p className="empty-action in" style={slot(2)}>
              <Link className="btn quiet" to="/teach">
                Teach a new task
              </Link>
            </p>
          ) : null}
        </div>
        <div className="saved-fig in" style={slot(2)}>
          <b>{savedFigureWords(savedSeconds)}</b>
          <span>Time saved this week</span>
          <small>
            {estimateNote ??
              (data.saved_seconds === undefined
                ? "vs. about 3 minutes a row by hand"
                : "vs. the time each row took by hand")}
          </small>
        </div>
      </section>

      <section className="two">
        <div className="panel in" style={slot(3)}>
          <div className="panel-h">
            <h3>Needs your help</h3>
            <Link to="/needs-help">See all</Link>
          </div>
          {needsHelp.length === 0 ? (
            <p className="panel-note">Nothing needs you right now.</p>
          ) : (
            <ul className="rows">
              {needsHelp.map((row) => (
                <li key={row.record_id}>
                  <div>
                    {/* The mock's row shape: what it is, why it needs a person, then what it came
                        from. The reason is the one sentence that tells the person what to do. */}
                    <b>{row.row}</b>
                    <p>{plainReason(row.reason, row.state)}</p>
                    <small>{row.task_name}</small>
                  </div>
                  <Link className="btn quiet" to="/needs-help">
                    Open
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="panel in" style={slot(4)}>
          <div className="panel-h">
            <h3>Running now</h3>
            {running.length > 0 ? (
              <span className="live">
                <i /> Live
              </span>
            ) : null}
          </div>
          {running.length === 0 ? (
            <p className="panel-note">Nothing is running right now.</p>
          ) : (
            running.map((run) => (
              <div className="run" key={run.run_id}>
                <b>{run.task_name}</b>
                <div className="track" aria-hidden="true">
                  <i
                    style={{
                      width: `${run.total === 0 ? 0 : Math.round((run.done / run.total) * 100)}%`,
                    }}
                  />
                </div>
                <div className="meta">
                  <span>
                    {run.done} of {run.total} done
                  </span>
                  <span>{run.already_there} already there</span>
                </div>
              </div>
            ))
          )}
        </div>
      </section>

      <section className="panel in" style={slot(5)}>
        <div className="panel-h">
          <h3>My tasks</h3>
          <Link to="/tasks">See all</Link>
        </div>
        {tasks.length === 0 ? (
          <p className="panel-note">No tasks yet.</p>
        ) : (
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
              {tasks.map((task) => {
                const action = taskAction(task);
                const result = taskResult(task);
                return (
                  <tr key={task.workflow_id}>
                    <td>
                      <b>{task.name}</b>
                      <small>{task.app ?? "—"}</small>
                    </td>
                    <td>{relativeTime(task.last_run?.started_at ?? null)}</td>
                    <td>
                      <span className={`res ${result.tone}`}>{result.label}</span>
                    </td>
                    <td className="act">
                      <Link className="btn quiet" to={action.to}>
                        {action.label}
                      </Link>
                    </td>
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
