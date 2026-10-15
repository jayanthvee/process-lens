import { type CSSProperties } from "react";
import { Link, useParams } from "react-router-dom";

import { stepWordsList, type RecipeStep } from "../lib/steps";
import { useJson } from "../lib/useJson";
import { relativeTime, runStatusWords } from "../lib/words";
import type { RunSummary, TaskRow } from "../types";
import "./tasks.css";

/**
 * `GET /api/v1/recipes/{id}`: the stored recipe version, with its document under `recipe` and the
 * task's name beside it. The shape is in `services/api/recipes.py` (`RecipeVersionDetail`); the
 * pages own only the fields they read.
 */
interface RecipeResponse {
  id: string;
  workflow_id: string;
  workflow_name: string;
  version: number;
  name: string;
  recipe: { steps?: RecipeStep[] } | null;
}

/** The blur-in stagger for the sections (DESIGN.md §7). */
function slot(index: number): CSSProperties {
  return { "--i": index } as CSSProperties;
}

/** How many of a run's rows are finished: the ones added and the ones already there. */
function doneCount(counts: Record<string, number>): number {
  return (counts.verified ?? 0) + (counts.skipped ?? 0);
}

function totalCount(counts: Record<string, number>): number {
  return Object.values(counts).reduce((sum, value) => sum + value, 0);
}

/**
 * One task, read only for now: its steps in plain words and its runs. The editor arrives in a later
 * ticket, so this page shows and changes nothing.
 */
export function TaskDetail(): JSX.Element {
  const { id } = useParams<{ id: string }>();

  // The page needs the task's name, its site and the id of the recipe version it is running, and
  // `GET /api/v1/tasks` carries all three: one task out of that list is the whole lookup.
  const tasks = useJson<TaskRow[]>("/api/v1/tasks");
  const task = (Array.isArray(tasks.data) ? tasks.data : []).find(
    (entry) => entry.workflow_id === id,
  ) ?? null;
  const recipe = useJson<RecipeResponse>(
    task === null ? null : `/api/v1/recipes/${task.recipe_version_id}`,
  );
  // The runs list has no task id, only the name it was run under, so a task's runs are matched on
  // that name. It is the same string `GET /api/v1/tasks` shows, so this stays simple on purpose.
  const runs = useJson<RunSummary[]>("/api/v1/runs");

  if (task === null) {
    const settled = Array.isArray(tasks.data);
    return (
      <div className="task-detail">
        <p className="lede in" style={slot(1)}>
          {settled ? "This task could not be found." : (tasks.error ?? "Loading this task…")}
        </p>
      </div>
    );
  }

  const steps = stepWordsList(recipe.data?.recipe?.steps);
  const mine = (Array.isArray(runs.data) ? runs.data : []).filter(
    (run) => run.recipe_name === task.name,
  );

  return (
    <div className="task-detail">
      <section className="hero in" style={slot(1)}>
        <div>
          <h1 className="big">{task.name}</h1>
          <p className="lede">{task.app ?? "No site yet"}</p>
        </div>
      </section>

      <section className="two">
        <div className="panel in" style={slot(2)}>
          <div className="panel-h">
            <h3>Steps</h3>
          </div>
          {steps.length === 0 ? (
            <p className="panel-note">
              {recipe.error ?? "The steps of this task will appear here."}
            </p>
          ) : (
            <ol className="steps">
              {steps.map((step, index) => (
                <li key={index}>
                  <b>{step.title}</b>
                  {step.detail === undefined ? null : <small>{step.detail}</small>}
                </li>
              ))}
            </ol>
          )}
          <p className="panel-note">You cannot change these steps yet.</p>
        </div>

        <div className="panel in" style={slot(3)}>
          <div className="panel-h">
            <h3>Runs</h3>
            <Link to="/runs">See all</Link>
          </div>
          {mine.length === 0 ? (
            <p className="panel-note">This task has not run yet.</p>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>Started</th>
                  <th>Result</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {mine.map((run) => {
                  const outcome = runStatusWords(run.status);
                  return (
                    <tr key={run.id}>
                      <td>{relativeTime(run.created_at)}</td>
                      <td>
                        <span className={`res ${outcome.tone}`}>{outcome.label}</span>
                        <small>
                          {doneCount(run.counts ?? {})} of {totalCount(run.counts ?? {})} done
                        </small>
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
          )}
        </div>
      </section>
    </div>
  );
}
