import { type CSSProperties } from "react";
import { Link } from "react-router-dom";

import { useJson } from "../lib/useJson";
import { useSessionPath } from "../lib/browser";
import { relativeTime, runStatusWords } from "../lib/words";
import type { AppRow, ExecutorSession } from "../types";
import "./apps.css";

/** The blur-in stagger: the status line and the cards rise 70 ms apart (DESIGN.md §7). */
function slot(index: number): CSSProperties {
  return { "--i": index } as CSSProperties;
}

/** The two things the Chrome status can say, in the words a person can act on. */
const CHROME_CONNECTED = "Chrome is connected";
const CHROME_AWAY =
  "Chrome isn't connected — open the ProcessLens extension and press Connect";

/**
 * A site's newest run in one line: when it ran, then how it went in plain words (DESIGN.md §2).
 * A site nothing has run on says so rather than leaving the line empty.
 */
function lastRunWords(app: AppRow): string {
  if (app.last_run_at === null) return "Not run yet";
  const outcome = runStatusWords(app.last_run_status ?? "");
  return `Last run ${relativeTime(app.last_run_at)} · ${outcome.label}`;
}

/**
 * The Apps page: the sites the taught tasks may use, and whether Chrome is here to run them.
 *
 * Read-only, and one card per site. The API has already grouped the tasks by site and named the
 * app, so this page only decides how it reads; the words all come from `lib/words.ts`.
 */
export function Apps(): JSX.Element {
  const { data, error } = useJson<AppRow[]>("/api/v1/apps");
  // The same session read the sidebar's status line uses: one source, and no new route.
  const session = useJson<ExecutorSession>(useSessionPath());
  // A refusal (or a stubbed answer that is not a list) shows one sentence instead of throwing.
  const rows = Array.isArray(data) ? data : null;
  const attached = session.data?.attached === true;
  // Until the session is known, the page says nothing rather than guessing "not connected".
  const chromeLine = session.data === null ? null : attached ? CHROME_CONNECTED : CHROME_AWAY;

  return (
    <div className="apps">
      {chromeLine === null ? null : (
        <p className="apps-chrome in" style={slot(1)}>
          {chromeLine}
        </p>
      )}
      {rows === null ? (
        <p className="lede in" style={slot(2)}>
          {error ?? "Loading your apps…"}
        </p>
      ) : rows.length === 0 ? (
        <section className="panel in" style={slot(2)}>
          <div className="empty">
            <p className="empty-sentence">Apps appear here once you teach a task on one.</p>
            <p className="empty-action">
              <Link className="btn quiet" to="/teach">
                Teach a task
              </Link>
            </p>
          </div>
        </section>
      ) : (
        <div className="app-cards">
          {rows.map((app, index) => (
            <article className="panel app-card in" style={slot(index + 2)} key={app.origin}>
              <header className="app-h">
                <h3>{app.name}</h3>
                <small>{app.host}</small>
              </header>
              <p className="app-used-by">Used by</p>
              <ul className="app-tasks">
                {app.tasks.map((task) => (
                  <li key={task.id}>
                    <Link to={`/tasks/${task.id}`}>{task.name}</Link>
                  </li>
                ))}
              </ul>
              <p className="app-run">{lastRunWords(app)}</p>
            </article>
          ))}
        </div>
      )}
    </div>
  );
}
