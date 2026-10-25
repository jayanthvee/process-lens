import { type CSSProperties, useState } from "react";
import { Link } from "react-router-dom";

import { apiPost, errorText } from "../lib/api";
import { useJson } from "../lib/useJson";
import {
  ADD_WORD_DOCUMENT,
  ADD_WORD_DOCUMENT_SENTENCE,
  DOWNLOAD_CSV,
  READING_DOCUMENT,
  USE_FOR_A_TASK,
  refusalWords,
  relativeTime,
} from "../lib/words";
import type { SpreadsheetRow } from "../types";
import "./spreadsheets.css";

/** What the API answers after reading a Word document: a preview, the notes, and a download token. */
interface DocumentPreview {
  columns: string[];
  rows: Record<string, string>[];
  notes: string[];
  csv_token: string;
}

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

/** The file's bytes, base64-encoded, without the data URL's prefix. */
function readBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = typeof reader.result === "string" ? reader.result : "";
      const comma = result.indexOf(",");
      resolve(comma === -1 ? result : result.slice(comma + 1));
    };
    reader.onerror = () => reject(reader.error ?? new Error("Could not read the file."));
    reader.readAsDataURL(file);
  });
}

/**
 * The Word-document area: choose a file, see the first rows and the notes, then download the CSV
 * or carry on to a task. The API does the reading and cleaning; this component only shows it.
 */
function AddWordDocument(): JSX.Element {
  const [preview, setPreview] = useState<DocumentPreview | null>(null);
  const [uploadedName, setUploadedName] = useState<string>("");
  const [reading, setReading] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  async function add(file: File): Promise<void> {
    setReading(true);
    setProblem(null);
    setPreview(null);
    try {
      const content = await readBase64(file);
      const answer = await apiPost<DocumentPreview>("/api/v1/documents/to-csv", {
        file_name: file.name,
        content_base64: content,
      });
      setUploadedName(file.name);
      setPreview(answer);
    } catch (failure) {
      setProblem(refusalWords(errorText(failure)));
    } finally {
      setReading(false);
    }
  }

  return (
    <section className="panel in" style={slot(1)}>
      <div className="panel-h">
        <h3>{ADD_WORD_DOCUMENT}</h3>
      </div>
      <div className="doc-add">
        <p className="doc-sentence">{ADD_WORD_DOCUMENT_SENTENCE}</p>
        <label className="btn quiet doc-choose">
          Choose a file
          <input
            className="doc-file"
            type="file"
            accept=".docx,.pdf"
            aria-label={ADD_WORD_DOCUMENT}
            onChange={(event) => {
              const input = event.currentTarget;
              const file = input.files?.[0] ?? null;
              input.value = "";
              if (file !== null) void add(file);
            }}
          />
        </label>
        {reading && <p className="doc-reading">{READING_DOCUMENT}</p>}
        {problem !== null && <p className="doc-problem">{problem}</p>}
      </div>
      {preview !== null && (
        <div className="doc-preview">
          <table className="sheet-table doc-table">
            <thead>
              <tr>
                {preview.columns.map((column) => (
                  <th key={column} scope="col">
                    {column}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {preview.rows.map((row, index) => (
                <tr key={index}>
                  {preview.columns.map((column) => (
                    <td key={column}>{row[column] ?? ""}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
          {preview.notes.length > 0 && (
            <ul className="doc-notes">
              {preview.notes.map((note) => (
                <li key={note}>{note}</li>
              ))}
            </ul>
          )}
          <p className="doc-actions">
            <a
              className="btn primary"
              href={`/api/v1/documents/to-csv/${preview.csv_token}.csv`}
              download
            >
              {DOWNLOAD_CSV}
            </a>
            <Link
              className="btn quiet"
              // PL-076: the cleaned CSV travels to the task list with the token, and the name
              // rides along so the list can say which document's rows are waiting.
              to={`/tasks?csv=${encodeURIComponent(preview.csv_token)}${
                uploadedName ? `&name=${encodeURIComponent(uploadedName)}` : ""
              }`}
            >
              {USE_FOR_A_TASK}
            </Link>
          </p>
        </div>
      )}
    </section>
  );
}

/**
 * The Spreadsheets page: a Word document can be turned into a CSV here, and every upload, the task
 * it was for, and the run that used it are listed below.
 *
 * Read-only apart from the document area, and one table rather than cards (DESIGN.md §4). The API
 * has already named the file, the task and the newest run, so this page only decides how it reads.
 */
export function Spreadsheets(): JSX.Element {
  const { data, error } = useJson<SpreadsheetRow[]>("/api/v1/spreadsheets");
  // A refusal (or a stubbed answer that is not a list) shows one sentence instead of throwing.
  const rows = Array.isArray(data) ? data : null;

  if (rows === null) {
    return (
      <div className="spreadsheets">
        <AddWordDocument />
        <p className="lede in" style={slot(2)}>
          {error ?? "Loading your spreadsheets…"}
        </p>
      </div>
    );
  }

  return (
    <div className="spreadsheets">
      <AddWordDocument />
      {rows.length === 0 ? (
        <section className="panel in" style={slot(2)}>
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
      ) : (
        <section className="panel in" style={slot(2)}>
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
      )}
    </div>
  );
}
