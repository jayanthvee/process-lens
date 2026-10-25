import { type CSSProperties, useEffect, useRef, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";

import { ApprovalPanel } from "../components/ApprovalPanel";
import { KeepsFailingWarning } from "../components/KeepsFailingWarning";
import { WaitingRun } from "../components/WaitingRun";
import { apiGet, apiPost, apiPostFile, apiPut, errorText } from "../lib/api";
import { knownBrowser, useSessionPath } from "../lib/browser";
import { POLL_INTERVAL_MS, useJson } from "../lib/useJson";
import {
  DOCUMENT_ROWS_GONE,
  isOpenStatus,
  pauseReasonWords,
  refusalWords,
  runStatusWords,
} from "../lib/words";
import { runsBlocked, useWorkspaceState } from "../lib/workspace";
import type {
  BatchCheck,
  BatchUpload,
  ExecutorSession,
  MappingEntry,
  RunCreated,
  RunDetail as RunDetailPayload,
  TaskRow,
} from "../types";
import "./taskRun.css";

/**
 * "Run this task", in three steps on one page: add the spreadsheet, check it, and try one row
 * before the rest of them run.
 *
 * Only the step a person is on offers a button, and only one of those is a primary button
 * (DESIGN.md §1). A step that is done keeps its result and offers nothing.
 */
function slot(index: number): CSSProperties {
  return { "--i": index } as CSSProperties;
}

/**
 * How a matched column reads: three plain words, never the API's own confidence name.
 *
 * `chosen` is the column the page is showing for this task column, which may be one the person
 * picked rather than the one the check guessed at. A column a person picked is `Matched` whatever
 * the guess's own confidence was: they chose it deliberately, and saying "Check this" about their
 * own choice would be asking them to check something they just decided.
 */
function matchWords(
  chosen: string | null,
  confidence: MappingEntry["confidence"],
  auto: string | null,
): {
  label: string;
  tone: string;
} {
  if (chosen === null) return { label: "Not found", tone: "idle" };
  if (chosen !== auto) return { label: "Matched", tone: "ok" };
  if (confidence === "exact") return { label: "Matched", tone: "ok" };
  return { label: "Check this", tone: "live" };
}

/** The check's sentence: the rows that are ready, and the rows that need a look. */
function readyWords(ready: number, needLook: number): string {
  const rows = ready === 1 ? "row" : "rows";
  const look = needLook === 1 ? "row needs" : "rows need";
  return `${ready} ${rows} ready \u00b7 ${needLook} ${look} a look`;
}

export function TaskRun(): JSX.Element {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  // PL-076: a cleaned document from the Spreadsheets page arrives as ?csv=<token>. Once the task
  // is known, the CSV is fetched and handed to the very same `add` a picked file goes through,
  // so the person lands on the check step with the rows already loaded.
  const [params] = useSearchParams();
  const csvToken = params.get("csv");
  const csvName = params.get("name") ?? "";

  // Whether an admin has paused runs, or this person's extension is too old. The buttons below
  // stand down while that is true; the shell's banner is what says why.
  const workspace = useWorkspaceState();
  const blocked = runsBlocked(workspace.data);

  // The task carries the two ids the whole flow needs: the task itself and the version it runs.
  const tasks = useJson<TaskRow[]>("/api/v1/tasks");
  const task =
    (Array.isArray(tasks.data) ? tasks.data : []).find((entry) => entry.workflow_id === id) ?? null;

  const fileInput = useRef<HTMLInputElement | null>(null);
  // The carry-in runs once: a re-render must not fetch the same document again.
  const carriedIn = useRef(false);
  const [batch, setBatch] = useState<BatchUpload | null>(null);
  const [step, setStep] = useState<number>(1);
  // The columns a person changed. Anything they left alone stays exactly as the check chose it.
  const [edits, setEdits] = useState<Record<string, string | null>>({});
  const [dragging, setDragging] = useState(false);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [runId, setRunId] = useState<string | null>(null);
  // The check answer after the person confirmed their mapping. The check writes each row's verdict
  // (ready, or needs a look) onto the row, and a run is built only from the rows it marked ready.
  // So if the person changes a column, a run started from the first answer would leave out a row
  // their change just made ready. Continue saves the mapping, asks again, and only then goes on.
  const [confirmed, setConfirmed] = useState<BatchCheck | null>(null);
  // True only while Continue's second check is in flight, so the panel says "Checking…" for the
  // right reason and not while a run is starting.
  const [rechecking, setRechecking] = useState(false);
  // PL-041: the in-flight flags for the two buttons that start a run. They are refs, not state,
  // because the second click of a double click arrives before a state update re-renders the page.
  const tryingIt = useRef(false);
  const runningRest = useRef(false);

  // Step 2 is one request: the API matches the columns and decides which rows are ready.
  const checkPath =
    batch === null || task === null
      ? null
      : `/api/v1/batches/${batch.batch_id}/check?recipe_version_id=${task.recipe_version_id}`;
  const check = useJson<BatchCheck>(checkPath);
  // What the page shows: the confirmed answer once there is one, the first answer until then.
  const answer = confirmed ?? check.data;
  const entries: MappingEntry[] | null = answer?.mapping ?? null;

  const chosenFor = (entry: MappingEntry): string | null => {
    const edited = edits[entry.column];
    return edited === undefined ? entry.chosen : edited;
  };
  // The task's own columns that must have a spreadsheet column before anything can run.
  const missing = (entries ?? []).filter((entry) => entry.required && chosenFor(entry) === null);
  const canContinue = entries !== null && missing.length === 0;

  // The preview run, followed live until it is no longer open. `useJson` polls on its own, and the
  // interval is switched off the moment the run stops being open -- a finished run is never asked
  // about again (PL-015's rule, carried forward). The Chrome session is asked on the same clock,
  // so a queued run says "Waiting for Chrome" only while the extension really is away.
  const [pollMs, setPollMs] = useState<number | null>(null);
  const runPath = runId === null ? null : `/api/v1/runs/${runId}`;
  const run = useJson<RunDetailPayload>(runPath, pollMs);
  const session = useJson<ExecutorSession>(useSessionPath(), pollMs);
  useEffect(() => {
    const next = isOpenStatus(run.data?.status ?? null) ? POLL_INTERVAL_MS : null;
    setPollMs((current) => (current === next ? current : next));
  }, [run.data]);
  const runOpen = runId !== null && isOpenStatus(run.data?.status ?? null);
  const runDone = runId !== null && run.data !== null && !isOpenStatus(run.data.status);

  const ready = answer?.rows_ok ?? null;
  const needLook = answer?.rows_need_a_look ?? [];
  const first = answer?.first_ready ?? null;
  // "Try one row" is one of the ready rows, so the rest of them are the others.
  const rowsLeft = ready === null ? 0 : Math.max(0, ready - 1);
  const columns = answer?.columns ?? [];

  async function add(file: File): Promise<void> {
    if (task === null) return;
    setBusy(true);
    setProblem(null);
    const form = new FormData();
    form.append("workflow_id", task.workflow_id);
    form.append("file", file);
    try {
      const added = await apiPostFile<BatchUpload>("/api/v1/batches", form);
      setBatch(added);
      setEdits({});
      setConfirmed(null);
      setStep(2);
    } catch (failure) {
      setProblem(refusalWords(errorText(failure)));
    } finally {
      setBusy(false);
    }
  }

  // PL-076: carry the cleaned document in. The fetch is the API's own download route; its bytes
  // become a File and go through `add`, exactly as a picked file does. A token that has expired
  // (or a fetch that fails) says so once in the upload area, and the picker stays usable.
  useEffect(() => {
    if (csvToken === null || task === null || carriedIn.current) return;
    carriedIn.current = true;
    const name = `${csvName.replace(/\.(docx|pdf)$/i, "") || "document"}.csv`;
    void (async () => {
      setBusy(true);
      setProblem(null);
      try {
        const response = await fetch(
          `/api/v1/documents/to-csv/${encodeURIComponent(csvToken)}.csv`,
        );
        if (!response.ok) throw new Error("the document's rows are gone");
        const blob = await response.blob();
        await add(new File([blob], name, { type: "text/csv" }));
      } catch {
        setProblem(DOCUMENT_ROWS_GONE);
      } finally {
        setBusy(false);
      }
    })();
    // `add` is recreated each render but reads only state this effect already depends on.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [csvToken, csvName, task]);

  async function continueOn(): Promise<void> {
    if (batch === null || entries === null) return;
    setBusy(true);
    setProblem(null);
    const mapping: Record<string, string | null> = {};
    for (const entry of entries) mapping[entry.column] = chosenFor(entry);
    try {
      // The mapping is saved on the batch before anything runs: the run reads the row as the task
      // sees it.
      await apiPut(`/api/v1/batches/${batch.batch_id}/mapping`, { mapping });
      // Ask again with the mapping the person just confirmed. Answering it also rewrites the rows'
      // verdicts, so the run that follows is built from the columns they chose, not the ones the
      // check guessed at.
      if (checkPath === null) return;
      setRechecking(true);
      const fresh = await apiGet<BatchCheck>(checkPath);
      setConfirmed(fresh);
      setStep(3);
    } catch (failure) {
      setProblem(refusalWords(errorText(failure)));
    } finally {
      setRechecking(false);
      setBusy(false);
    }
  }

  async function create(mode: "preview" | "batch"): Promise<string | null> {
    if (task === null || batch === null) return null;
    setBusy(true);
    setProblem(null);
    const body: Record<string, unknown> = {
      workflow_id: task.workflow_id,
      recipe_version_id: task.recipe_version_id,
      input_batch_id: batch.batch_id,
      mode,
    };
    if (mode === "preview") body.limit = 1;
    // PL-087: the run goes to this browser's Chrome, when the extension has said which it is.
    const browser = knownBrowser();
    if (browser !== null) body.browser_id = browser;
    try {
      const created = await apiPost<RunCreated>("/api/v1/runs", body);
      return created.run_id;
    } catch (failure) {
      setProblem(refusalWords(errorText(failure)));
      return null;
    } finally {
      setBusy(false);
    }
  }

  async function tryIt(): Promise<void> {
    // One click, one run (PL-041). `busy` is set by `create`, but a second click can land before
    // React re-renders and the button turns itself off, so the ref is the guard that holds.
    if (tryingIt.current) return;
    tryingIt.current = true;
    try {
      const created = await create("preview");
      if (created !== null) setRunId(created);
    } finally {
      tryingIt.current = false;
    }
  }

  async function resumeIt(): Promise<void> {
    if (runId === null) return;
    setBusy(true);
    setProblem(null);
    try {
      await apiPost(`/api/v1/runs/${runId}/resume`);
      run.reload();
    } catch (failure) {
      setProblem(refusalWords(errorText(failure)));
    } finally {
      setBusy(false);
    }
  }

  async function runTheRest(): Promise<void> {
    // The same guard as Try it: two clicks in one tick must still start one batch run (PL-041).
    if (runningRest.current) return;
    runningRest.current = true;
    try {
      const created = await create("batch");
      if (created !== null) navigate(`/runs/${created}`);
    } finally {
      runningRest.current = false;
    }
  }

  if (task === null) {
    const settled = Array.isArray(tasks.data);
    return (
      <div className="task-run">
        <p className="lede in" style={slot(1)}>
          {settled ? "This task could not be found." : (tasks.error ?? "Loading this task…")}
        </p>
      </div>
    );
  }

  const outcome =
    run.data === null ? null : runStatusWords(run.data.status, session.data?.attached);

  return (
    <div className="task-run">
      <section className="hero in" style={slot(1)}>
        <div>
          <h1 className="big">{task.name}</h1>
          <p className="lede">Your spreadsheet, one step at a time.</p>
          {/* PL-041: the last two runs both stopped the same way, so this task needs recording
              again. Said here, before a spreadsheet is added, with the fix beside it. */}
          {task.keeps_failing == null ? null : (
            <KeepsFailingWarning keepsFailing={task.keeps_failing} />
          )}
        </div>
      </section>

      <section className="panel in" style={slot(2)}>
        <div className="panel-h">
          <h3>1. Add your spreadsheet</h3>
          {batch === null ? null : (
            <span className="run-count">
              {batch.row_count} rows, {batch.columns.length} columns
            </span>
          )}
        </div>
        {batch === null ? (
          <div
            className={`run-drop${dragging ? " over" : ""}`}
            onDragOver={(event) => {
              event.preventDefault();
              setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={(event) => {
              event.preventDefault();
              setDragging(false);
              const dropped = event.dataTransfer?.files?.[0];
              if (dropped !== undefined) void add(dropped);
            }}
          >
            <p className="run-note">
              A .csv, .tsv, .xlsx, .docx or .pdf file, up to 2,000 rows and 5 MB.
            </p>
            <p className="run-choose">
              <button
                type="button"
                className="btn primary run-choose-file"
                disabled={busy}
                onClick={() => fileInput.current?.click()}
              >
                Choose a file
              </button>
              <span className="run-or">or drop it here</span>
            </p>
          </div>
        ) : (
          <p className="run-note">{batch.columns.join(", ")}</p>
        )}
        {/* The one file input: the button above and a drop both hand their file to `add`. */}
        <input
          className="run-file"
          type="file"
          accept=".csv,.tsv,.xlsx,.docx,.pdf"
          aria-label="Your spreadsheet"
          ref={fileInput}
          onChange={(event) => {
            const picked = event.target.files?.[0];
            if (picked !== undefined) void add(picked);
          }}
        />
        {problem === null || batch !== null ? null : (
          <p className="refusal run-problem" role="status">
            {problem}
          </p>
        )}
      </section>

      {batch === null ? null : (
        <section className="panel in" style={slot(3)}>
          <div className="panel-h">
            <h3>2. Check it</h3>
            {step === 3 && runId === null ? (
              // A quiet way back, and only before a run exists: once one has started, its rows are
              // already built from the mapping that was saved, so changing the columns would be a
              // change nothing would act on.
              <button type="button" className="btn quiet run-change" onClick={() => setStep(2)}>
                Change the columns
              </button>
            ) : null}
            {check.loading || rechecking ? <span className="run-count">Checking…</span> : null}
          </div>
          {entries === null ? (
            <p className="run-note">{check.error ?? "Checking your spreadsheet against this task…"}</p>
          ) : entries.length === 0 ? (
            <p className="run-note">This task needs no columns, so every row is ready.</p>
          ) : (
            <table className="run-mapping">
              <thead>
                <tr>
                  <th>What this task needs</th>
                  <th>Your column</th>
                  <th>Match</th>
                </tr>
              </thead>
              <tbody>
                {entries.map((entry) => {
                  const chosen = chosenFor(entry);
                  const words = matchWords(chosen, entry.confidence, entry.chosen);
                  return (
                    <tr key={entry.column}>
                      <td>
                        <b>{entry.label}</b>
                        {entry.required ? <small>Needed</small> : null}
                      </td>
                      <td>
                        <select
                          className={`run-select${chosen === null ? " missing" : ""}`}
                          aria-label={entry.label}
                          value={chosen ?? ""}
                          // Once the run flow has moved on, this reads rather than edits: the run
                          // uses the mapping that was saved, so a change here would be a change
                          // nothing would act on. "Change the columns" below opens it again.
                          disabled={step === 3}
                          onChange={(event) =>
                            setEdits((current) => ({
                              ...current,
                              [entry.column]: event.target.value === "" ? null : event.target.value,
                            }))
                          }
                        >
                          <option value="">Not in my file</option>
                          {columns.map((column) => (
                            <option key={column} value={column}>
                              {column}
                            </option>
                          ))}
                        </select>
                        {entry.alternatives.length === 0 ? null : (
                          <small>Also looked like: {entry.alternatives.join(", ")}</small>
                        )}
                      </td>
                      <td>
                        <span className={`res ${words.tone}`}>{words.label}</span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
          {ready === null ? null : (
            <p className="run-summary">{readyWords(ready, needLook.length)}</p>
          )}
          {needLook.length === 0 ? null : (
            <ul className="run-look">
              {needLook.map((entry) => (
                <li key={entry.row}>
                  <b>Row {entry.row}</b> {entry.reason}
                </li>
              ))}
            </ul>
          )}
          {step === 2 && entries !== null ? (
            <div className="actions">
              <button
                type="button"
                className="btn primary run-continue"
                disabled={!canContinue || busy}
                onClick={() => void continueOn()}
              >
                Continue
              </button>
              {missing.length === 0 ? null : (
                <span className="run-count">
                  Choose a column for {missing.map((entry) => entry.label).join(", ")}.
                </span>
              )}
            </div>
          ) : null}
          {problem === null ? null : (
            <p className="refusal run-problem" role="status">
              {problem}
            </p>
          )}
        </section>
      )}

      {step < 3 || answer === null ? null : (
        <section className="panel in" style={slot(4)}>
          <div className="panel-h">
            <h3>3. Try one row</h3>
            {outcome === null ? null : (
              <span className={`res ${outcome.tone}`}>{outcome.label}</span>
            )}
          </div>
          {/* PL-041: the row is queued behind another task's run on this app. The card says which
              one, and offers the one action that unblocks it, without leaving this page. */}
          {run.data?.waiting_behind == null ? null : (
            <WaitingRun waiting={run.data.waiting_behind} onCancelled={run.reload} />
          )}
          {first === null ? (
            <p className="run-note">No row is ready yet. Fix the rows above, then try again.</p>
          ) : (
            <div className="run-first">
              <p className="run-about">About to add:</p>
              <dl className="run-values">
                {(entries ?? []).map((entry) => (
                  <div className="run-value" key={entry.column}>
                    <dt>{entry.label}</dt>
                    <dd>{String(first.values[entry.column] ?? "")}</dd>
                  </div>
                ))}
              </dl>
            </div>
          )}
          {run.data === null || run.data.open_approval === null ? (
            <div className="actions">
              {runId === null ? (
                <button
                  type="button"
                  className="btn primary run-try"
                  disabled={busy || first === null || blocked}
                  onClick={() => void tryIt()}
                >
                  Try it
                </button>
              ) : runOpen && run.data?.status === "paused" ? (
                // PL-040 review: a row that paused for anything but a decision says why, with
                // the one button that carries on, here in the card that started it.
                <>
                  <span className="run-count run-paused">
                    {pauseReasonWords(run.data.pause_reason) ?? "This row is paused."}
                  </span>
                  <button
                    type="button"
                    className="btn primary run-resume"
                    disabled={busy}
                    onClick={() => void resumeIt()}
                  >
                    Resume
                  </button>
                </>
              ) : runOpen ? (
                <>
                  <span className="live">
                    <i /> Live
                  </span>
                  {/* The wait named: the row is being added in the bound Chrome window, not
                      stuck here. A stopped run's decision replaces this row with the panel. */}
                  <span className="run-count">Adding this row in the ProcessLens window…</span>
                </>
              ) : runDone && rowsLeft > 0 ? (
                <button
                  type="button"
                  className="btn primary run-rest"
                  disabled={busy || blocked}
                  onClick={() => void runTheRest()}
                >
                  Run the other {rowsLeft} rows
                </button>
              ) : runDone ? (
                <span className="run-count">That was the only row.</span>
              ) : (
                <span className="run-count">Starting this row…</span>
              )}
            </div>
          ) : (
            // The run stopped at an approval gate. The decision belongs here, in the card that
            // started the row, with the same panel the run page shows (PL-038's rule).
            <ApprovalPanel detail={run.data} onDecided={run.reload} />
          )}
          {problem === null ? null : (
            <p className="refusal run-problem" role="status">
              {problem}
            </p>
          )}
        </section>
      )}
    </div>
  );
}
