/**
 * PL-105: teaching, in the side panel. The popup's three steps are here: start and stop the
 * recorder, remove a step the person did not mean, and save the recording as a task. The compile
 * and save calls are the ones make-recipe.ts already makes; only the markup is React.
 *
 * A recording with no typed values is saved with a fresh-key template (`{{row.run_once_id}}`) and
 * a one-cell sample row, so the task can be run once from Tasks without a spreadsheet.
 */

import { useEffect, useState } from "react";

import { apiConfig } from "../lib/api";
import {
  compileRecording,
  defaultCommitStepId,
  defaultRecordKeyColumn,
  makeRecipeRequest,
  recordedValues,
  saveTask,
} from "../../../extension/src/popup/make-recipe";
import {
  FIX_LABEL,
  stepLines,
  stepProblems,
  type StepProblem,
} from "../../../extension/src/popup/flow";
import { plainSentences } from "../../../extension/src/popup/words";
import type { RecordedEvent } from "../../../extension/src/shared/recipe-types";
import {
  EMPTY_STATUS,
  type RecorderStatus,
  type ToWorker,
} from "../../../extension/src/shared/messages";
import { extensionChrome, type ExtensionMessageListener } from "./extension";

interface RecorderResponse {
  ok: boolean;
  status: RecorderStatus;
  events: RecordedEvent[];
}

interface RecorderMessage {
  type?: string;
  status?: RecorderStatus;
  event?: RecordedEvent;
}

const NO_EVENTS =
  "Nothing has been recorded yet. Stop teaching first, then save it as a task.";

function defaultName(events: RecordedEvent[]): string {
  try {
    const host = new URL(events[0]?.url ?? "").hostname;
    return host.length > 0 ? `Recorded on ${host}` : "Recorded task";
  } catch {
    return "Recorded task";
  }
}

function mergeEvent(events: RecordedEvent[], event: RecordedEvent): RecordedEvent[] {
  const index = events.findIndex((candidate) => candidate.seq === event.seq);
  if (index < 0) return [...events, event];
  const next = events.slice();
  next[index] = event;
  return next;
}

export function PanelTeach(): JSX.Element {
  const [status, setStatus] = useState<RecorderStatus>({ ...EMPTY_STATUS });
  const [events, setEvents] = useState<RecordedEvent[]>([]);
  const [name, setName] = useState("");
  const [keyColumn, setKeyColumn] = useState("");
  const [saving, setSaving] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);

  const recording = status.state === "recording";
  const values = recordedValues(events);

  async function send(message: ToWorker): Promise<RecorderResponse | null> {
    const chrome = extensionChrome();
    if (chrome?.runtime === undefined) return null;
    try {
      const answer = (await chrome.runtime.sendMessage(message)) as RecorderResponse | undefined;
      return answer ?? null;
    } catch {
      setProblem("ProcessLens's recorder is not answering. Reload the extension and try again.");
      return null;
    }
  }

  function apply(next: RecorderResponse | null): void {
    if (next === null) return;
    if (next.status !== undefined) setStatus(next.status);
    if (Array.isArray(next.events)) {
      setEvents(next.events);
      setName((current) =>
        current.trim() === "" ? defaultName(next.events) : current,
      );
    }
  }

  useEffect(() => {
    let live = true;
    void send({ type: "record:get" }).then((answer) => {
      if (live) apply(answer);
    });
    const chrome = extensionChrome();
    const listener: ExtensionMessageListener = (message) => {
      const incoming = message as RecorderMessage | null;
      if (incoming === null || typeof incoming !== "object") return;
      if (incoming.type === "record:state" && incoming.status !== undefined) {
        setStatus(incoming.status);
      }
      if (incoming.type === "record:appended" && incoming.event !== undefined) {
        setEvents((current) => mergeEvent(current, incoming.event as RecordedEvent));
      }
    };
    chrome?.runtime?.onMessage?.addListener(listener);
    return () => {
      live = false;
      chrome?.runtime?.onMessage?.removeListener?.(listener);
    };
    // The worker answers once on mount; a later answer is not this effect's business.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // The key follows the recording: an email-ish column is offered first, else the first value.
  useEffect(() => {
    if (values.length === 0) {
      if (keyColumn !== "") setKeyColumn("");
      return;
    }
    if (!values.some((value) => value.column === keyColumn)) {
      setKeyColumn(defaultRecordKeyColumn(events) ?? values[0]?.column ?? "");
    }
    // `values` is derived from `events`, and the key is the only thing this effect owns.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [events]);

  async function start(): Promise<void> {
    setProblem(null);
    setNote(null);
    const chrome = extensionChrome();
    const tabs = await chrome?.tabs?.query({ active: true, currentWindow: true });
    const tabId = tabs?.[0]?.id;
    const answer = await send({
      type: "record:start",
      ...(tabId !== undefined ? { tabId } : {}),
    });
    apply(answer);
  }

  async function stop(): Promise<void> {
    const answer = await send({ type: "record:stop" });
    apply(answer);
  }

  async function remove(seq: number): Promise<void> {
    const answer = await send({ type: "record:remove", seq });
    apply(answer);
  }

  /**
   * PL-103: a step that cannot be repeated offers Start over, which is the popup's Clear: the
   * recording is emptied so the person teaches it again from the start, on the list this time.
   */
  async function startOver(): Promise<void> {
    setProblem(null);
    setNote(null);
    const answer = await send({ type: "record:clear" });
    apply(answer);
  }

  async function save(): Promise<void> {
    if (saving) return;
    const trimmed = name.trim();
    if (trimmed === "") {
      setProblem("Give the task a name first.");
      return;
    }
    if (events.length === 0) {
      setProblem(NO_EVENTS);
      return;
    }
    if (values.length > 0 && keyColumn === "") {
      setProblem("Choose which column identifies each row first.");
      return;
    }

    setSaving(true);
    setProblem(null);
    setNote("Turning your recording into a task…");
    try {
      const request = makeRecipeRequest(events, {
        name: trimmed,
        recordKeyColumn: keyColumn,
        // The recording's last click is the save, exactly as the popup preselected it. A null
        // commit would compile that click as a plain click: no write-ahead and no save check, so
        // a crash mid-save could create the record twice.
        commitStepId: defaultCommitStepId(events),
      });
      if (values.length === 0) {
        // No column to key on: every Run once carries a fresh id, so the ledger sees new work.
        request.record_key_column = "";
        request.record_key = "{{row.run_once_id}}";
        request.sample_row = { run_once_id: "x" };
      }
      const config = apiConfig();
      const compiled = await compileRecording(request, {
        baseUrl: config.base,
        headers: config.headers,
      });
      if (!compiled.ok) {
        const plain = plainSentences(compiled.problems.join("\n")).shown[0];
        setProblem(plain ?? "Could not save this as a task.");
        setNote(null);
        return;
      }
      const saved = await saveTask(
        { name: trimmed, recipe: compiled.recipe },
        { baseUrl: config.base, headers: config.headers },
      );
      if (!saved.ok) {
        const plain = plainSentences(saved.problems.join("\n")).shown[0];
        setProblem(plain ?? "Could not save the task.");
        setNote(null);
        return;
      }
      setNote(`${trimmed} is ready. It is in Tasks.`);
      setName(trimmed);
    } catch (failure) {
      setProblem(failure instanceof Error ? failure.message : "Could not reach ProcessLens.");
      setNote(null);
    } finally {
      setSaving(false);
    }
  }

  const lines = stepLines(events);
  // PL-103: a step that cannot be repeated says so on its own row, with its fix. Like the popup,
  // the check runs once the recording is stopped: while it is still being built there is nothing
  // to judge yet.
  const problems = recording ? new Map<number, StepProblem>() : stepProblems(events);

  return (
    <section className="panel-card" aria-label="Teach">
      <h1 className="panel-title">Teach a task</h1>
      <p className="panel-state" role="status">
        {recording ? "Recording" : "Not recording"}
      </p>
      <p className="panel-note">
        Do the job once in your browser. ProcessLens watches, then you save it as a task.
      </p>
      <div className="panel-actions">
        <button type="button" className="btn primary" disabled={recording} onClick={() => void start()}>
          Start teaching
        </button>
        <button type="button" className="btn quiet" disabled={!recording} onClick={() => void stop()}>
          Stop
        </button>
      </div>
      <p className="panel-count">
        {status.count} step{status.count === 1 ? "" : "s"} recorded
      </p>
      {status.warning === null ? null : (
        <p className="panel-problem" role="status">
          {status.warning}
        </p>
      )}
      {lines.length === 0 ? (
        <p className="panel-note">The steps you take will appear here.</p>
      ) : (
        <ol className="panel-steps">
          {lines.map((line) => {
            const problem = problems.get(line.seq);
            return (
              <li
                key={line.seq}
                className={problem === undefined ? "panel-step" : "panel-step is-problem"}
              >
                <div className="panel-step-line">
                  <span>{line.sentence}</span>
                  <button
                    type="button"
                    className="panel-remove"
                    aria-label="Remove this step"
                    hidden={recording}
                    onClick={() => void remove(line.seq)}
                  >
                    ×
                  </button>
                </div>
                {problem === undefined ? null : (
                  <p className="panel-step-problem">
                    {problem.sentence}{" "}
                    <button
                      type="button"
                      className="panel-step-fix"
                      onClick={() => {
                        if (problem.fix === "remove") void remove(line.seq);
                        else void startOver();
                      }}
                    >
                      {FIX_LABEL[problem.fix]}
                    </button>
                  </p>
                )}
              </li>
            );
          })}
        </ol>
      )}

      <div className="panel-save">
        <label className="panel-label" htmlFor="panel-task-name">
          What should this task be called?
        </label>
        <input
          id="panel-task-name"
          className="panel-input"
          type="text"
          value={name}
          placeholder="New lead"
          autoComplete="off"
          spellCheck={false}
          onChange={(event) => setName(event.target.value)}
        />
        {values.length === 0 ? null : (
          <>
            <label className="panel-label" htmlFor="panel-task-key">
              Which column identifies each row?
            </label>
            <select
              id="panel-task-key"
              className="panel-input"
              value={keyColumn}
              onChange={(event) => setKeyColumn(event.target.value)}
            >
              {values.map((value) => (
                <option key={value.column} value={value.column}>
                  {value.label}
                </option>
              ))}
            </select>
          </>
        )}
        <button
          type="button"
          className="btn primary"
          disabled={saving || events.length === 0 || (values.length > 0 && keyColumn === "")}
          onClick={() => void save()}
        >
          Save as task
        </button>
        {note === null ? null : (
          <p className="panel-note" role="status">
            {note}
          </p>
        )}
        {problem === null ? null : (
          <p className="panel-problem" role="status">
            {problem}
          </p>
        )}
      </div>
    </section>
  );
}
