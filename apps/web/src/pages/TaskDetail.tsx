import { type CSSProperties, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import {
  columnsOf,
  diffSummary,
  flattenSteps,
  problemsInPlainWords,
  removeStep,
  renameStep,
  setSaveCheck,
  setValueSource,
  stepName,
  type EditResult,
  type RecipeDocument,
  type Step,
} from "../lib/editRecipe";
import { stepWords, stepWordsList, type RecipeStep } from "../lib/steps";
import { useJson } from "../lib/useJson";
import { relativeTime, runStatusWords } from "../lib/words";
import { runsBlocked, useWorkspaceState } from "../lib/workspace";
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
  recipe: RecipeDocument | null;
}

/** `POST /api/v1/recipes`: the version just stored, with the document it stored. */
interface SavedRecipe {
  id: string;
  version: number;
  recipe: RecipeDocument;
}

/** The API refused the edited document; `problems` are its own reasons, still in engine words. */
class RefusedByApi extends Error {
  readonly problems: string[];

  constructor(problems: string[]) {
    super("the API refused the edited steps");
    this.problems = problems;
  }
}

/** Post the edited document as the task's next version, with the change reason the diff produced. */
async function postEditedSteps(
  workflowId: string,
  document: RecipeDocument,
  reason: string,
): Promise<SavedRecipe> {
  const query = `workflow_id=${encodeURIComponent(workflowId)}&change_reason=${encodeURIComponent(reason)}`;
  const response = await fetch(`/api/v1/recipes?${query}`, {
    method: "POST",
    headers: { Accept: "application/json", "Content-Type": "application/json" },
    body: JSON.stringify(document),
  });
  const text = await response.text().catch(() => "");
  let payload: unknown = null;
  try {
    payload = text.trim() === "" ? null : (JSON.parse(text) as unknown);
  } catch {
    payload = null;
  }
  if (!response.ok) {
    const detail = (payload as { detail?: { problems?: unknown } } | null)?.detail;
    const problems = detail?.problems;
    throw new RefusedByApi(Array.isArray(problems) ? problems.map(String) : []);
  }
  return payload as SavedRecipe;
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

function objectOf(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

/** Which choice a save step's current check is, so the control starts where the document is. */
function checkChoice(step: Step): string {
  const check = objectOf(step.assert_after);
  if (check === null) return "keep";
  if ("url_matches" in check) return "url_matches";
  if ("text_visible" in check) return "text_visible";
  return "keep";
}

function checkText(step: Step): string {
  const check = objectOf(step.assert_after);
  const text = check?.text_visible;
  return typeof text === "string" ? text : "";
}

/** The typed path behind a recorded address check, when it is a plain one. */
function checkPath(step: Step): string {
  const check = objectOf(step.assert_after);
  const pattern = check?.url_matches;
  if (typeof pattern !== "string") return "";
  return pattern.endsWith("$") ? pattern.slice(0, -1).replace(/\\(.)/g, "$1") : pattern;
}

function constantText(step: Step): string {
  const value = objectOf(step.value);
  return value?.kind === "constant" && typeof value.value === "string" ? value.value : "";
}

function columnOf(step: Step): string {
  const value = objectOf(step.value);
  return value?.kind === "input" && typeof value.column === "string" ? value.column : "";
}

/**
 * One task: its steps in plain words and its runs. The steps can be edited in place -- renamed,
 * removed, and their value and save check changed -- and saving stores the edited document as the
 * task's next version, so a run already going keeps the steps it started with. Element targets and
 * the order of steps are not part of this editor.
 */
export function TaskDetail(): JSX.Element {
  const { id } = useParams<{ id: string }>();

  // Whether an admin has paused runs, or this person's extension is too old. The page's one
  // primary action stands down while that is true; the shell's banner is what says why.
  const workspace = useWorkspaceState();
  const blocked = runsBlocked(workspace.data);

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

  // The loaded version is the editor's starting point; an edit works on a copy until it is saved.
  const loadedDocument = recipe.data?.recipe ?? null;
  const [document, setDocument] = useState<RecipeDocument | null>(null);
  const [version, setVersion] = useState<number | null>(null);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<RecipeDocument | null>(null);
  const [refusals, setRefusals] = useState<Record<string, string>>({});
  const [problems, setProblems] = useState<string[]>([]);
  const [savedVersion, setSavedVersion] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [fixedText, setFixedText] = useState<Record<string, boolean>>({});
  const [checks, setChecks] = useState<Record<string, string>>({});

  useEffect(() => {
    if (loadedDocument === null) return;
    setDocument(loadedDocument);
    setVersion(recipe.data?.version ?? null);
  }, [loadedDocument, recipe.data?.version]);

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

  // The steps on screen: the saved document once an edit has been saved, otherwise the loaded one.
  const steps = stepWordsList((document ?? loadedDocument)?.steps);
  const mine = (Array.isArray(runs.data) ? runs.data : []).filter(
    (run) => run.recipe_name === task.name,
  );

  function beginEdit(): void {
    if (document === null) return;
    setDraft(JSON.parse(JSON.stringify(document)) as RecipeDocument);
    setRefusals({});
    setProblems([]);
    setSavedVersion(null);
    setFixedText({});
    setChecks({});
    setEditing(true);
  }

  function cancelEdit(): void {
    setDraft(null);
    setEditing(false);
    setRefusals({});
    setProblems([]);
    setFixedText({});
    setChecks({});
  }

  /** Run one edit against the draft: keep the new document, or show the refusal on that row. */
  function apply(stepId: string, attempt: EditResult): void {
    if (attempt.ok) {
      setDraft(attempt.document);
      setRefusals((current) => {
        const next = { ...current };
        delete next[stepId];
        return next;
      });
      return;
    }
    setRefusals((current) => ({ ...current, [stepId]: attempt.refusal }));
  }

  async function saveEdits(): Promise<void> {
    if (draft === null || document === null || task === null) return;
    setBusy(true);
    setProblems([]);
    try {
      const answer = await postEditedSteps(
        task.workflow_id,
        draft,
        diffSummary(document, draft),
      );
      setDocument(answer.recipe ?? draft);
      setVersion(answer.version);
      setSavedVersion(answer.version);
      setEditing(false);
      setDraft(null);
      setRefusals({});
    } catch (failure) {
      setProblems(
        failure instanceof RefusedByApi
          ? problemsInPlainWords(failure.problems)
          : ["The steps could not be saved. Try again."],
      );
    } finally {
      setBusy(false);
    }
  }

  const editingRows = draft === null ? [] : flattenSteps(draft);
  const columns = draft === null ? [] : columnsOf(draft);

  return (
    <div className="task-detail">
      <section className="hero in" style={slot(1)}>
        <div>
          <h1 className="big">{task.name}</h1>
          <p className="lede">{task.app ?? "No site yet"}</p>
        </div>
        {/* The page's one obvious next action, and its only primary button (DESIGN.md §1). While
            runs are blocked it is the same control, standing down: a link would still open the run
            flow and offer a person a button the API will refuse. */}
        {blocked ? (
          <button type="button" className="btn primary task-run-link" disabled>
            Run this task
          </button>
        ) : (
          <Link className="btn primary task-run-link" to={`/tasks/${task.workflow_id}/run`}>
            Run this task
          </Link>
        )}
      </section>

      {savedVersion !== null && (
        <p className="saved-note in" role="status" style={slot(2)}>
          Saved as version {savedVersion}. Runs already started keep the old steps.
        </p>
      )}

      <section className="two">
        <div className="panel in" style={slot(editing ? 3 : 2)}>
          <div className="panel-h">
            <h3>Steps</h3>
            {!editing && document !== null && (
              <button type="button" className="btn quiet edit-start" onClick={beginEdit}>
                Edit steps
              </button>
            )}
            {version !== null && <span className="count">Version {version}</span>}
          </div>

          {editing ? (
            <>
              {problems.length > 0 && (
                <div className="edit-problems" role="alert">
                  <b>That change wasn&apos;t saved.</b>
                  <ul>
                    {problems.map((problem) => (
                      <li key={problem}>{problem}</li>
                    ))}
                  </ul>
                </div>
              )}

              <ol className="steps editing">
                {editingRows.map((entry, index) => {
                  const place = index + 1;
                  const stepId = String(entry.step.id ?? `step-${place}`);
                  const words = stepWords(entry.step as RecipeStep);
                  const isValueStep = entry.step.action === "fill" || entry.step.action === "select";
                  const isSaveStep = entry.step.commit === true;
                  const refusal = refusals[stepId];
                  const showingFixedText =
                    fixedText[stepId] === true || columnOf(entry.step) === "";
                  const currentColumn = columnOf(entry.step);
                  const chosenCheck = checks[stepId] ?? checkChoice(entry.step);

                  return (
                    <li key={stepId} className="edit-row">
                      <div className="edit-head">
                        <b>{words.title}</b>
                        {words.detail === undefined ? null : <small>{words.detail}</small>}
                        <button
                          type="button"
                          className="btn quiet edit-remove"
                          onClick={() => apply(stepId, removeStep(draft as RecipeDocument, stepId))}
                        >
                          Remove step {place}
                        </button>
                      </div>

                      <div className="edit-controls">
                        <label>
                          <span>Name for step {place}</span>
                          <input
                            key={`name-${stepId}-${stepName(entry.step)}`}
                            aria-label={`Name for step ${place}`}
                            defaultValue={stepName(entry.step)}
                            onBlur={(event) =>
                              apply(
                                stepId,
                                renameStep(draft as RecipeDocument, stepId, event.target.value),
                              )
                            }
                            onKeyDown={(event) => {
                              if (event.key === "Enter") event.currentTarget.blur();
                            }}
                          />
                        </label>

                        {isValueStep && (
                          <label>
                            <span>Comes from for step {place}</span>
                            <select
                              aria-label={`Comes from for step ${place}`}
                              value={showingFixedText ? "fixed" : `column:${currentColumn}`}
                              onChange={(event) => {
                                const chosen = event.target.value;
                                if (chosen === "fixed") {
                                  setFixedText((current) => ({ ...current, [stepId]: true }));
                                  return;
                                }
                                setFixedText((current) => ({ ...current, [stepId]: false }));
                                apply(
                                  stepId,
                                  setValueSource(draft as RecipeDocument, stepId, {
                                    kind: "input",
                                    column: chosen.slice("column:".length),
                                  }),
                                );
                              }}
                            >
                              {columns.map((column) => (
                                <option key={column} value={`column:${column}`}>
                                  The {column} column
                                </option>
                              ))}
                              <option value="fixed">Fixed text</option>
                            </select>
                          </label>
                        )}

                        {isValueStep && showingFixedText && (
                          <label>
                            <span>Fixed text for step {place}</span>
                            <input
                              key={`fixed-${stepId}-${constantText(entry.step)}`}
                              aria-label={`Fixed text for step ${place}`}
                              defaultValue={constantText(entry.step)}
                              placeholder="The same every time"
                              onBlur={(event) =>
                                apply(
                                  stepId,
                                  setValueSource(draft as RecipeDocument, stepId, {
                                    kind: "constant",
                                    value: event.target.value,
                                  }),
                                )
                              }
                              onKeyDown={(event) => {
                                if (event.key === "Enter") event.currentTarget.blur();
                              }}
                            />
                          </label>
                        )}

                        {isSaveStep && (
                          <>
                            <label>
                              <span>How we know it saved for step {place}</span>
                              <select
                                aria-label={`How we know it saved for step ${place}`}
                                value={chosenCheck}
                                onChange={(event) => {
                                  const chosen = event.target.value;
                                  setChecks((current) => ({ ...current, [stepId]: chosen }));
                                  if (chosen === "keep") {
                                    apply(
                                      stepId,
                                      setSaveCheck(draft as RecipeDocument, stepId, { kind: "keep" }),
                                    );
                                  }
                                }}
                              >
                                <option value="keep">Keep what was recorded</option>
                                <option value="text_visible">The page shows this text</option>
                                <option value="url_matches">The address becomes</option>
                              </select>
                            </label>

                            {chosenCheck === "text_visible" && (
                              <label>
                                <span>Text for step {place}</span>
                                <input
                                  key={`text-${stepId}-${checkText(entry.step)}`}
                                  aria-label={`Text for step ${place}`}
                                  defaultValue={checkText(entry.step)}
                                  placeholder="What the page shows"
                                  onBlur={(event) =>
                                    apply(
                                      stepId,
                                      setSaveCheck(draft as RecipeDocument, stepId, {
                                        kind: "text_visible",
                                        text: event.target.value,
                                      }),
                                    )
                                  }
                                  onKeyDown={(event) => {
                                    if (event.key === "Enter") event.currentTarget.blur();
                                  }}
                                />
                              </label>
                            )}

                            {chosenCheck === "url_matches" && (
                              <label>
                                <span>Address for step {place}</span>
                                <input
                                  key={`path-${stepId}-${checkPath(entry.step)}`}
                                  aria-label={`Address for step ${place}`}
                                  defaultValue={checkPath(entry.step)}
                                  placeholder="For example /leads/9"
                                  onBlur={(event) =>
                                    apply(
                                      stepId,
                                      setSaveCheck(draft as RecipeDocument, stepId, {
                                        kind: "url_matches",
                                        path: event.target.value,
                                      }),
                                    )
                                  }
                                  onKeyDown={(event) => {
                                    if (event.key === "Enter") event.currentTarget.blur();
                                  }}
                                />
                              </label>
                            )}
                          </>
                        )}
                      </div>

                      {refusal !== undefined && (
                        <p className="edit-refusal" role="alert">
                          {refusal}
                        </p>
                      )}
                    </li>
                  );
                })}
              </ol>

              <div className="edit-actions">
                <button
                  type="button"
                  className="btn quiet edit-save"
                  onClick={saveEdits}
                  disabled={busy}
                >
                  Save
                </button>
                <button type="button" className="btn quiet" onClick={cancelEdit} disabled={busy}>
                  Cancel
                </button>
              </div>

              <p className="panel-note">
                To change what a step clicks, teach that part again.
              </p>
            </>
          ) : steps.length === 0 ? (
            <p className="panel-note">
              {recipe.error ?? "The steps of this task will appear here."}
            </p>
          ) : (
            <>
              <ol className="steps">
                {steps.map((step, index) => (
                  <li key={index}>
                    <b>{step.title}</b>
                    {step.detail === undefined ? null : <small>{step.detail}</small>}
                  </li>
                ))}
              </ol>
              <p className="panel-note">
                To change what a step clicks, teach that part again.
              </p>
            </>
          )}
        </div>

        <div className="panel in" style={slot(editing ? 4 : 3)}>
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
