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

import { apiPatch, apiPost, errorText } from "../lib/api";
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
  // PL-115: a task the recorder left untitled can be named here, on its own card.
  const [renamed, setRenamed] = useState<Record<string, string>>({});
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState<string | null>(null);
  const [renameProblems, setRenameProblems] = useState<Record<string, string>>({});

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

  function startRename(task: TaskRow): void {
    setEditing(task.workflow_id);
    setDraft(renamed[task.workflow_id] ?? "");
    setRenameProblems((current) => {
      const next = { ...current };
      delete next[task.workflow_id];
      return next;
    });
  }

  function cancelRename(): void {
    setEditing(null);
    setDraft("");
  }

  async function saveName(task: TaskRow): Promise<void> {
    const trimmed = draft.trim();
    if (trimmed === "" || saving !== null) return;
    setSaving(task.workflow_id);
    setRenameProblems((current) => {
      const next = { ...current };
      delete next[task.workflow_id];
      return next;
    });
    try {
      await apiPatch<{ workflow_id?: string }>("/api/v1/tasks/" + task.workflow_id, {
        name: trimmed,
      });
      setRenamed((current) => ({ ...current, [task.workflow_id]: trimmed }));
      setEditing(null);
      setDraft("");
      tasks.reload();
    } catch (failure) {
      const detail = errorText(failure);
      setRenameProblems((current) => ({
        ...current,
        // The rename route is not in services/api yet (PL-115 report, "Needs PL-113/114"): a 404
        // is said in a person's words rather than as the API's own "Not Found".
        [task.workflow_id]: /not found/i.test(detail)
          ? "The name wasn't saved. Renaming isn't available on this server yet."
          : detail,
      }));
    } finally {
      setSaving(null);
    }
  }

  function setRowProblem(task: TaskRow, message: string | null): void {
    setRowProblems((current) => {
      const next = { ...current };
      if (message === null) delete next[task.workflow_id];
      else next[task.workflow_id] = message;
      return next;
    });
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
    const chosen = selectedFor(task);
    const email = (chosen === ANOTHER_EMAIL ? another[task.workflow_id] ?? "" : chosen).trim();
    const where = email === "" ? task.sign_in_email ?? "that address" : email;
    switch (last.pause_detail) {
      case "none":
        return {
          sentence:
            `No code arrived at ${where}. Check that this inbox is signed in to Gmail in this ` +
            "Chrome, then press Try again.",
        };
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
          const message = codeProblem(task);
          const shownName = renamed[task.workflow_id] ?? task.name;
          const untitled = isDefaultTaskName(shownName);
          const site = siteWords(task.app);
          const title = untitled ? taskTitleWords(task.name, task.app) : shownName;
          const isEditing = editing === task.workflow_id;
          const classes = ["panel-task"];
          if (task.sign_in === true) classes.push("panel-task-signin");
          if (isEditing) classes.push("panel-task-editing");
          return (
            <li className={classes.join(" ")} key={task.workflow_id}>
              <div className="panel-task-text">
                <b>{title}</b>
                {untitled || site === null ? null : <small>{site}</small>}
                {untitled && !isEditing ? (
                  <button
                    type="button"
                    className="panel-rename"
                    onClick={() => startRename(task)}
                  >
                    Rename
                  </button>
                ) : null}
              </div>
              {isEditing ? (
                <div className="panel-rename-row">
                  <input
                    className="panel-input"
                    type="text"
                    aria-label="Task name"
                    placeholder="Name this task"
                    autoComplete="off"
                    value={draft}
                    onChange={(event) => setDraft(event.target.value)}
                  />
                  <button
                    type="button"
                    className="btn quiet"
                    disabled={draft.trim() === "" || saving === task.workflow_id}
                    onClick={() => void saveName(task)}
                  >
                    Save
                  </button>
                  <button type="button" className="btn quiet" onClick={cancelRename}>
                    Cancel
                  </button>
                  {renameProblems[task.workflow_id] === undefined ? null : (
                    <p className="panel-problem" role="status">
                      {renameProblems[task.workflow_id]}
                    </p>
                  )}
                </div>
              ) : null}
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
                  {rowProblems[task.workflow_id] === undefined ? null : (
                    <p className="panel-problem" role="status">
                      {rowProblems[task.workflow_id]}
                    </p>
                  )}
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
            </li>
          );
        })}
      </ul>
    </section>
  );
}
