/**
 * PL-105: the panel's Tasks list. A task with spreadsheet columns opens the existing run flow;
 * a task taught with no columns is run once, with no upload and no batch to prepare.
 *
 * PL-112: a sign-in task is run from one picked email instead of a spreadsheet. Run sends that one
 * row through the same run-once route, the code page is filled from Gmail exactly as PL-102 does,
 * and a failed code read is said in plain words on this same row.
 */

import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { apiPost, errorText } from "../lib/api";
import { knownBrowser } from "../lib/browser";
import { useJson } from "../lib/useJson";
import {
  CODE_FROM_EMAIL_BUTTON,
  CODE_FROM_EMAIL_OFFER,
  codeFromEmailWords,
  isDefaultTaskName,
  siteWords,
  taskTitleWords,
} from "../lib/words";
import type { RunCreated, TaskRow } from "../types";
import { extensionChrome } from "./extension";
import {
  ANOTHER_EMAIL,
  looksLikeAnEmail,
  readSignInEmails,
  rememberSignInEmail,
  signInEmails,
} from "./signInEmails";

/** PL-120: how long the Undo line stays after a card leaves the list. */
const UNDO_MS = 10_000;

/** PL-120: the question a task asks before it leaves the list. */
function removeQuestion(name: string): string {
  return `Remove "${name}" from the list? Its past runs stay.`;
}

export function PanelTasks(): JSX.Element {
  const navigate = useNavigate();
  const tasks = useJson<TaskRow[]>("/api/v1/tasks");
  const rows = Array.isArray(tasks.data) ? tasks.data : null;
  const [running, setRunning] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  // PL-112: the addresses typed before, per task, from this Chrome's own storage.
  const [storedEmails, setStoredEmails] = useState<Record<string, string[]>>({});
  const [selected, setSelected] = useState<Record<string, string>>({});
  const [another, setAnother] = useState<Record<string, string>>({});
  const [rowProblems, setRowProblems] = useState<Record<string, string>>({});
  // PL-120: a removed task is hidden at once, and its Undo stays on screen for a moment.
  const [confirming, setConfirming] = useState<string | null>(null);
  const [hidden, setHidden] = useState<string[]>([]);
  const [lastRemoved, setLastRemoved] = useState<{ workflow_id: string; name: string } | null>(
    null,
  );
  const [undoProblem, setUndoProblem] = useState<string | null>(null);

  useEffect(() => {
    const storage = extensionChrome()?.storage?.local;
    if (storage === undefined || rows === null) return;
    let live = true;
    void (async () => {
      const loaded: Record<string, string[]> = {};
      for (const task of rows) {
        if (task.sign_in !== true) continue;
        loaded[task.workflow_id] = await readSignInEmails(storage, task.workflow_id);
      }
      if (live) setStoredEmails(loaded);
    })();
    return () => {
      live = false;
    };
  }, [rows]);

  function emailsFor(task: TaskRow): string[] {
    return signInEmails(task.sign_in_email, storedEmails[task.workflow_id] ?? []);
  }

  function selectedFor(task: TaskRow): string {
    const chosen = selected[task.workflow_id];
    if (chosen !== undefined) return chosen;
    return emailsFor(task)[0] ?? ANOTHER_EMAIL;
  }

  function setRowProblem(task: TaskRow, message: string | null): void {
    setRowProblems((current) => {
      const next = { ...current };
      if (message === null) delete next[task.workflow_id];
      else next[task.workflow_id] = message;
      return next;
    });
  }

  useEffect(() => {
    if (lastRemoved === null) return;
    const timer = window.setTimeout(() => setLastRemoved(null), UNDO_MS);
    return () => window.clearTimeout(timer);
  }, [lastRemoved]);

  function askRemove(task: TaskRow): void {
    setConfirming(task.workflow_id);
    setRowProblem(task, null);
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
      setRowProblem(task, null);
    } catch (failure) {
      // The API's sentence stays on the task's own card (a 409 says "Stop its run first.").
      setRowProblem(task, errorText(failure));
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

  async function runSignIn(task: TaskRow): Promise<void> {
    if (running !== null) return;
    const column = task.sign_in_column ?? task.inputs?.[0] ?? "";
    if (column === "") {
      setRowProblem(task, "This task doesn't say which field the email goes in.");
      return;
    }
    const chosen = selectedFor(task);
    const email = (chosen === ANOTHER_EMAIL ? another[task.workflow_id] ?? "" : chosen).trim();
    if (!looksLikeAnEmail(email)) {
      setRowProblem(task, "Type the email this run should use.");
      return;
    }

    setRunning(task.workflow_id);
    setProblem(null);
    setRowProblem(task, null);
    const storage = extensionChrome()?.storage?.local;
    try {
      const emails = await rememberSignInEmail(storage, task.workflow_id, email);
      if (emails.length > 0) {
        setStoredEmails((current) => ({ ...current, [task.workflow_id]: emails }));
      }
      const browser = knownBrowser();
      const created = await apiPost<RunCreated>(
        `/api/v1/tasks/${task.workflow_id}/run-once`,
        {
          ...(browser === null ? {} : { browser_id: browser }),
          values: { [column]: email },
        },
      );
      navigate(`/runs/${created.run_id}`);
    } catch (failure) {
      setRowProblem(task, errorText(failure));
    } finally {
      setRunning(null);
    }
  }

  async function readFromGmail(task: TaskRow): Promise<void> {
    const runId = task.last_run?.run_id;
    if (runId === undefined || running !== null) return;
    setRunning(task.workflow_id);
    setProblem(null);
    try {
      await apiPost(`/api/v1/runs/${runId}/code-from-email`, { remember: true });
      navigate(`/runs/${runId}`);
    } catch (failure) {
      setRowProblem(task, errorText(failure));
    } finally {
      setRunning(null);
    }
  }

  /** What the row says when this task's last run could not read its code. */
  function codeProblem(task: TaskRow): { sentence?: string; offer?: boolean } | null {
    const last = task.last_run;
    if (last === null || last === undefined) return null;
    if (last.status !== "paused" || last.pause_reason !== "code_required") return null;
    switch (last.pause_detail) {
      case "none":
        // PL-121: say what the run page says (PL-119). Gmail's feed lists only unread mail, so a
        // code the person already opened is invisible to ProcessLens; the inbox is not at fault.
        return { sentence: codeFromEmailWords("none") };
      case "ambiguous":
        return { sentence: codeFromEmailWords("ambiguous") };
      case "not_allowed":
        return { offer: true };
      default:
        return { sentence: codeFromEmailWords(last.pause_detail ?? "") };
    }
  }

  if (rows === null) {
    return <p className="panel-note">{tasks.error ?? "Loading your tasks…"}</p>;
  }

  const visible = rows.filter((task) => !hidden.includes(task.workflow_id));

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
      {lastRemoved === null ? null : (
        <p className="panel-note panel-removed" role="status">
          Removed "{lastRemoved.name}".{" "}
          <button type="button" className="panel-undo" onClick={() => void undo()}>
            Undo
          </button>
        </p>
      )}
      {undoProblem === null ? null : (
        <p className="panel-problem" role="status">
          {undoProblem}
        </p>
      )}
      <ul className="panel-list">
        {visible.map((task) => {
          const inputs = task.inputs ?? [];
          const message = codeProblem(task);
          // PL-115 item 2: a placeholder name reads as "Untitled task · <site>". Renaming is not
          // offered here yet: services/api serves no PATCH /tasks/{id}, so a Save could only 404
          // (T1 review B1); the report lists it under "Needs PL-113/114".
          const untitled = isDefaultTaskName(task.name);
          const site = siteWords(task.app);
          const title = taskTitleWords(task.name, task.app);
          const classes = ["panel-task"];
          if (task.sign_in === true) classes.push("panel-task-signin");
          const question = removeQuestion(task.name);
          return (
            <li className={classes.join(" ")} key={task.workflow_id}>
              <div className="panel-task-text">
                <b>{title}</b>
                {untitled || site === null ? null : <small>{site}</small>}
              </div>
              {task.sign_in === true ? (
                <div className="panel-signin">
                  <label className="panel-label" htmlFor={`sign-in-email-${task.workflow_id}`}>
                    Email for this run
                  </label>
                  <select
                    id={`sign-in-email-${task.workflow_id}`}
                    className="panel-input"
                    value={selectedFor(task)}
                    onChange={(event) => {
                      setSelected((current) => ({
                        ...current,
                        [task.workflow_id]: event.target.value,
                      }));
                      setRowProblem(task, null);
                    }}
                  >
                    {emailsFor(task).map((email) => (
                      <option key={email} value={email}>
                        {email}
                      </option>
                    ))}
                    <option value={ANOTHER_EMAIL}>Another email…</option>
                  </select>
                  {selectedFor(task) === ANOTHER_EMAIL ? (
                    <input
                      className="panel-input"
                      type="email"
                      aria-label="Another email"
                      placeholder="name@example.com"
                      autoComplete="off"
                      value={another[task.workflow_id] ?? ""}
                      onChange={(event) =>
                        setAnother((current) => ({
                          ...current,
                          [task.workflow_id]: event.target.value,
                        }))
                      }
                    />
                  ) : null}
                  <button
                    type="button"
                    className="btn quiet"
                    disabled={running !== null}
                    onClick={() => void runSignIn(task)}
                  >
                    Run
                  </button>
                  {message?.sentence === undefined ? null : (
                    <p className="panel-problem" role="status">
                      {message.sentence}
                    </p>
                  )}
                  {message?.offer !== true ? null : (
                    <div className="panel-code">
                      <p className="panel-note">{CODE_FROM_EMAIL_OFFER}</p>
                      <button
                        type="button"
                        className="btn quiet"
                        disabled={running !== null}
                        onClick={() => void readFromGmail(task)}
                      >
                        {CODE_FROM_EMAIL_BUTTON}
                      </button>
                    </div>
                  )}
                </div>
              ) : inputs.length === 0 ? (
                <button
                  type="button"
                  className="btn quiet"
                  disabled={running !== null}
                  onClick={() => void runOnce(task)}
                >
                  Run
                </button>
              ) : (
                <Link className="btn quiet" to={`/tasks/${task.workflow_id}/run`}>
                  Run with a spreadsheet
                </Link>
              )}
              <button
                type="button"
                className="btn quiet panel-task-remove"
                onClick={() => askRemove(task)}
              >
                Remove
              </button>
              {confirming === task.workflow_id ? (
                <div className="panel-confirm" role="alertdialog" aria-label={question}>
                  <p className="panel-note">{question}</p>
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
              ) : null}
              {rowProblems[task.workflow_id] === undefined ? null : (
                <p className="panel-problem" role="status">
                  {rowProblems[task.workflow_id]}
                </p>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}
