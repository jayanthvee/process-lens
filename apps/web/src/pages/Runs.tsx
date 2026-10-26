import { type CSSProperties, useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { useJson } from "../lib/useJson";
import { isOpenStatus, relativeTime, runStatusWords } from "../lib/words";
import type { RunSummary } from "../types";
import "./runs.css";

/** The blur-in stagger: the header and the table rise 70 ms apart (DESIGN.md §7). */
function slot(index: number): CSSProperties {
  return { "--i": index } as CSSProperties;
}

/** The rows of a run that are finished: added rows plus rows that were already there. */
function doneCount(counts: Record<string, number>): number {
  return (counts.verified ?? 0) + (counts.skipped ?? 0);
}

/** The run's rows: the per-state counts sum to the total (there is no `total` key in this list). */
function totalCount(counts: Record<string, number>): number {
  return Object.values(counts).reduce((sum, value) => sum + value, 0);
}

/** Every run, live and past. */
export function Runs(): JSX.Element {
  // A run that is still going keeps the list live; the moment none is, `pollMs` is null and the
  // interval stops, so a settled list makes no further requests.
  const [live, setLive] = useState(false);
  const { data, error } = useJson<RunSummary[]>("/api/v1/runs", live ? 2000 : null);

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
            <p className="empty-sentence">
              Nothing has run yet. When a task runs, it appears here with what it did.
            </p>
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
        <table className="drop-second">
          <thead>
            <tr>
              <th>Task</th>
              <th>Started</th>
              <th>Status</th>
              <th>Progress</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {rows.map((run) => {
              const counts = run.counts ?? {};
              const outcome = runStatusWords(run.status, undefined, counts);
              const done = doneCount(counts);
              const total = totalCount(counts);
              return (
                <tr key={run.id}>
                  <td>
                    <b>{run.recipe_name ?? "This task"}</b>
                  </td>
                  <td>{relativeTime(run.created_at)}</td>
                  <td>
                    <span className={`res ${outcome.tone}`}>{outcome.label}</span>
                  </td>
                  <td className="progress-cell">
                    {done} of {total} done
                    <span className="track" aria-hidden="true">
                      <i style={{ width: `${total === 0 ? 0 : Math.round((done / total) * 100)}%` }} />
                    </span>
                  </td>
                  <td className="act">
                    <Link className="btn quiet" to={`/runs/${run.id}`}>
                      Open
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
