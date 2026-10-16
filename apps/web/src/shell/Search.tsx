import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { useJson } from "../lib/useJson";
import type { TaskRow } from "../types";
import { Icon } from "./Icons";

/**
 * The header's search: a real, focusable panel over the task names.
 *
 * `Ctrl K` (or `Cmd K`) opens it, `Escape` closes it, and the typing filters the tasks the API
 * knows about. It only asks for the list while it is open.
 */
export function Search(): JSX.Element {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const { data } = useJson<TaskRow[]>(open ? "/api/v1/tasks" : null);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent): void => {
      if (event.key.toLowerCase() === "k" && (event.ctrlKey || event.metaKey)) {
        event.preventDefault();
        setOpen(true);
        return;
      }
      if (event.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  const tasks = data ?? [];
  const wanted = query.trim().toLowerCase();
  const matches = tasks.filter((task) => task.name.toLowerCase().includes(wanted));

  return (
    <>
      <button
        type="button"
        className="search"
        aria-label="Search tasks"
        aria-expanded={open}
        onClick={() => setOpen(true)}
      >
        <Icon name="search" />
        Search
        <kbd>Ctrl K</kbd>
      </button>
      {open ? (
        <div className="search-panel" role="dialog" aria-label="Search tasks">
          <input
            type="search"
            aria-label="Task name"
            placeholder="Type a task name"
            value={query}
            autoFocus
            onChange={(event) => setQuery(event.target.value)}
          />
          {matches.length === 0 ? (
            <p>No task has that name yet.</p>
          ) : (
            <ul className="rows">
              {matches.slice(0, 8).map((task) => (
                <li key={task.workflow_id}>
                  <Link to={`/tasks/${task.workflow_id}`} onClick={() => setOpen(false)}>
                    {task.name}
                  </Link>
                  <small>{task.app ?? ""}</small>
                </li>
              ))}
            </ul>
          )}
        </div>
      ) : null}
    </>
  );
}
