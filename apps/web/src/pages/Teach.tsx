import { useEffect, useState, type CSSProperties } from "react";
import { Link } from "react-router-dom";

import { POLL_INTERVAL_MS, useJson } from "../lib/useJson";
import type { TaskRow } from "../types";
import "./teach.css";

/**
 * "Teach a new task", in three plain steps. Teaching itself happens in the Chrome extension, so the
 * page's one job is to say what to do and then notice the task the moment it arrives.
 *
 * The page asks for the task list every two seconds. The first answer is what was already there;
 * the first task that is not in it is the one just taught, and it is named and linked.
 */
function slot(index: number): CSSProperties {
  return { "--i": index } as CSSProperties;
}

export function Teach(): JSX.Element {
  const tasks = useJson<TaskRow[]>("/api/v1/tasks", POLL_INTERVAL_MS);
  const rows = Array.isArray(tasks.data) ? tasks.data : null;
  // The tasks that were here when the page opened, remembered once: everything after is new.
  const [seen, setSeen] = useState<string[] | null>(null);

  useEffect(() => {
    if (rows === null || seen !== null) return;
    setSeen(rows.map((task) => task.workflow_id));
  }, [rows, seen]);

  const found =
    rows === null || seen === null
      ? null
      : (rows.find((task) => !seen.includes(task.workflow_id)) ?? null);

  return (
    <div className="teach">
      <section className="hero in" style={slot(1)}>
        <div>
          <h1 className="big">Teach a task</h1>
          <p className="lede">Do one row by hand, and this page will notice.</p>
        </div>
      </section>

      <section className="panel in" style={slot(2)}>
        <div className="panel-h">
          <h3>How to teach a task</h3>
        </div>
        <ol className="plain">
          <li>
            <b>Open the app you want to automate in Chrome.</b>
            <small>Any app you already use in a browser tab.</small>
          </li>
          <li>
            <b>Press Start teaching in the ProcessLens extension.</b>
            <small>The extension watches what you do next, and takes it down as the task.</small>
          </li>
          <li>
            <b>Do one row by hand, exactly as you would normally.</b>
            <small>Type the values, click Save, and stop when the row is done.</small>
          </li>
        </ol>
      </section>

      <section className="panel in" style={slot(3)}>
        <div className="panel-h">
          <h3>Your new task</h3>
        </div>
        {found === null ? (
          <p className="teach-note">
            {tasks.error ?? "Nothing yet. Do one row by hand, and the task will appear here."}
          </p>
        ) : (
          <p className="teach-got">
            <Link className="teach-found" to={`/tasks/${found.workflow_id}`}>
              Got it: {found.name}
            </Link>
          </p>
        )}
        <p className="teach-note">
          Keep this page open. It looks for your new task on its own, so there is nothing to press.
        </p>
      </section>
    </div>
  );
}
