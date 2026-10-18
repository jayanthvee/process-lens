import { type CSSProperties } from "react";
import { Link } from "react-router-dom";

import { useJson } from "../lib/useJson";
import { relativeTime } from "../lib/words";
import type { SpreadsheetRow } from "../types";
import "./spreadsheets.css";

/** The blur-in stagger: the header and the table rise 70 ms apart (DESIGN.md §7). */
function slot(index: number): CSSProperties {
  return { "--i": index } as CSSProperties;
}

/** "20 rows" -- one row is a row, not "1 rows". */
function rowsWords(count: number): string {
  return `${count} row${count === 1 ? "" : "s"}`;
}

/** "Used in 3 runs" -- one run is a run. */
function runsWords(count: number): string {
  return `Used in ${count} run${count === 1 ? "" : "s"}`;
}

/** "Uploaded 2 hours ago by Jay", or without the name when nobody is known. */
function uploadedWords(sheet: SpreadsheetRow): string {
  const when = `Uploaded ${relativeTime(sheet.uploaded_at)}`;
  return sheet.uploaded_by === null ? when : `${when} by ${sheet.uploaded_by}`;
}

/**
 * The Spreadsheets page: every upload, the task it was for, and the run that used it.
 *
 * Read-only, and one table rather than cards (DESIGN.md §4). The API has already named the file,
 * the task and the newest run, so this page only decides how it reads; a pasted batch arrives as
 * "Pasted rows" and nothing has run on it yet as "Not used yet".
 */
export function Spreadsheets(): JSX.Element {
  const { data, error } = useJson<SpreadsheetRow[]>("/api/v1/spreadsheets");
  // A refusal (or a stubbed answer that is not a list) shows one sentence instead of throwing.
  const rows = Array.isArray(data) ? data : null;

  if (rows === null) {
    return (
      <div className="spreadsheets">
        <p className="lede in" style={slot(1)}>
          {error ?? "Loading your spreadsheets…"}
        </p>
      </div>
    );
  }

  if (rows.length === 0) {
    return (
      <div className="spreadsheets">
        <section className="panel in" style={slot(1)}>
          <div className="empty">
            <p className="empty-sentence">
              Spreadsheets appear here once you upload one to run a task.
            </p>
            <p className="empty-action">
              <Link className="btn quiet" to="/tasks">
                Run a task
              </Link>
            </p>
          </div>
        </section>
      </div>
    );
  }

  return (
    <div className="spreadsheets">
      <section className="panel in" style={slot(1)}>
        <div className="panel-h">
          <h3>Your spreadsheets</h3>
          <span className="count">{rows.length} uploaded</span>
        </div>
        <table className="sheet-table">
          <thead>
            <tr>
              <th>File</th>
              <th>Task</th>
              <th className="sheet-rows">Rows</th>
              <th className="sheet-uploaded">Uploaded</th>
              <th>Runs</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((sheet) => (
              <tr key={sheet.id}>
                <th scope="row">{sheet.file_name}</th>
                <td>
                  <Link to={`/tasks/${sheet.task_id}`}>{sheet.task_name}</Link>
                </td>
                <td className="sheet-rows">{rowsWords(sheet.row_count)}</td>
                <td className="sheet-uploaded">{uploadedWords(sheet)}</td>
                <td>
                  {sheet.runs > 0 && sheet.last_run_id !== null ? (
                    <Link to={`/runs/${sheet.last_run_id}`}>{runsWords(sheet.runs)}</Link>
                  ) : (
                    "Not used yet"
                  )}
                  {/* PL-041 extra: the next thing a person wants from an upload is to run it,
                      and the row says which of the two it would be. Quiet: this page's one
                      primary action is elsewhere (DESIGN.md §1). */}
                  <Link className="btn quiet sheet-run" to={`/tasks/${sheet.task_id}/run`}>
                    {sheet.runs > 0 ? "Run again" : "Run it"}
                  </Link>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </div>
  );
}
