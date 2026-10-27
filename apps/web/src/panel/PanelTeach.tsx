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
import { compileRefusalSentence, plainSentences, stepSentence } from "../../../extension/src/popup/words";
import {
  CODE_FROM_EMAIL_BUTTON,
  CODE_FROM_EMAIL_LATER,
  CODE_FROM_EMAIL_OFFER,
  READING_CODE_FROM_GMAIL,
  codeFromEmailWords,
} from "../lib/words";
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
  // PL-107: which recorded click saves the work; "" is "Nothing gets saved".
  const [commitStepId, setCommitStepId] = useState("");
  // PL-109 item 5/6: the decision is shown, and "Change" opens the question only when asked.
  const [changingKey, setChangingKey] = useState(false);
  const [changingCommit, setChangingCommit] = useState(false);

  const recording = status.state === "recording";
  // PL-107 item 12: what the recorder is doing about a code page, if it is on one.
  const codeState = status.code ?? null;
  const values = recordedValues(events);
  // PL-107: one choice per recorded click, labelled by the click's own sentence.
  const clickChoices: { id: string; label: string }[] = [];
  for (const event of events) {
    const step = event?.step ?? null;
    if (step === null || step.action !== "click" || !step.id) continue;
    clickChoices.push({ id: step.id, label: stepSentence(step, event.kind) });
  }

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
      // PL-109 item 5: decide the key when a value can identify a row; otherwise ask, never pick
      // the first field just because it is first.
      setKeyColumn(defaultRecordKeyColumn(events) ?? "");
    }
    // `values` is derived from `events`, and the key is the only thing this effect owns.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [events]);

  // The save choice follows the recording: the last click until the person picks another.
  useEffect(() => {
    const fallback = defaultCommitStepId(events) ?? "";
    setCommitStepId((current) =>
      clickChoices.some((choice) => choice.id === current) ? current : fallback,
    );
    // Only re-derive on a new recording; a deliberate "Nothing gets saved" is kept until then.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [events]);

  async function start(): Promise<void> {
    setProblem(null);
    setNote(null);
    // PL-107: a new teaching starts empty, never adding to the last recording.
    apply(await send({ type: "record:clear" }));
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

  /** PL-107 item 12: the panel's yes to reading a code from Gmail, for this and later tasks. */
  async function allowCodes(): Promise<void> {
    apply(await send({ type: "record:code-consent" }));
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
      setProblem("Choose which value is different for every row.");
      return;
    }

    setSaving(true);
    setProblem(null);
    setNote("Turning your recording into a task…");
    try {
      const request = makeRecipeRequest(events, {
        name: trimmed,
        recordKeyColumn: keyColumn,
        // PL-107: the person chooses which click saves; "" is "Nothing gets saved", which
        // compiles a read-only recipe (no commit step). The last click stays the default.
        commitStepId: commitStepId === "" ? null : commitStepId,
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
        setProblem(
          compileRefusalSentence(compiled.problems, events, { nothingSaved: commitStepId === "" }),
        );
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
  // PL-109 item 5/6: the labels the decisions name.
  const keyLabel = values.find((value) => value.column === keyColumn)?.label ?? keyColumn;
  const commitLabel =
    clickChoices.find((choice) => choice.id === commitStepId)?.label.replace(/^Clicked /, "") ?? "";
  // PL-103: a step that cannot be repeated says so on its own row, with its fix. Like the popup,
  // the check runs once the recording is stopped: while it is still being built there is nothing
  // to judge yet.
  const problems = recording ? new Map<number, StepProblem>() : stepProblems(events);
  // PL-107: a plain click's own words are already the "Which click saves your work?" choices, so
  // the step list shows the other steps and any step that needs a fix (a click's included). That
  // keeps one line per step on screen instead of the same sentence twice.
  const clickSeqs = new Set(
    events.filter((event) => event.step?.action === "click").map((event) => event.seq),
  );
  const listLines = lines.filter((line) => !clickSeqs.has(line.seq) || problems.has(line.seq));
  // PL-109 item 1: the value the person typed, shown where the step names the field.
  const typedBySeq = new Map<number, string>();
  for (const event of events) {
    const step = event?.step ?? null;
    if (step === null || (step.action !== "fill" && step.action !== "select")) continue;
    const source = step.value;
    if (source && source.kind === "constant") typedBySeq.set(event.seq, String(source.value));
  }

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
        {lines.length > 0 && !recording ? (
          <button type="button" className="btn quiet" onClick={() => void startOver()}>
            Start over
          </button>
        ) : null}
      </div>
      <p className="panel-count">
        {status.count} step{status.count === 1 ? "" : "s"} recorded
      </p>
      {status.warning === null ? null : (
        <p className="panel-problem" role="status">
          {status.warning}
        </p>
      )}
      {codeState?.state === "looking" ? (
        <p className="panel-note" role="status">
          {READING_CODE_FROM_GMAIL}
        </p>
      ) : null}
      {codeState?.state === "offer" ? (
        <div className="panel-code">
          <p className="panel-note">{CODE_FROM_EMAIL_OFFER}</p>
          <button type="button" className="btn quiet" onClick={() => void allowCodes()}>
            {CODE_FROM_EMAIL_BUTTON}
          </button>
          <p className="panel-note">{CODE_FROM_EMAIL_LATER}</p>
        </div>
      ) : null}
      {codeState?.state === "typed" ? (
        <p className="panel-note" role="status">
          The code from your Gmail was typed for you.
        </p>
      ) : null}
      {codeState?.state === "missed" ? (
        <p className="panel-problem" role="status">
          {codeFromEmailWords(codeState.reason)}
        </p>
      ) : null}
      {lines.length === 0 ? (
        <p className="panel-note">The steps you take will appear here.</p>
      ) : listLines.length === 0 ? null : (
        <ol className="panel-steps">
          {listLines.map((line) => {
            const problem = problems.get(line.seq);
            return (
              <li
                key={line.seq}
                className={problem === undefined ? "panel-step" : "panel-step is-problem"}
              >
                <div className="panel-step-line">
                  <span>
                    {line.sentence}
                    {typedBySeq.has(line.seq) ? ` — “${typedBySeq.get(line.seq)}”` : ""}
                  </span>
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

      {clickChoices.length === 0 ? null : (
        <div className="panel-commit">
          {!changingCommit ? (
            <p className="panel-note">
              {commitStepId === "" ? (
                <>Nothing gets saved.</>
              ) : (
                <>
                  The <b>{commitLabel}</b> click saves your work.
                </>
              )}{" "}
              <button
                type="button"
                className="panel-change"
                aria-label="Change which click saves your work"
                onClick={() => setChangingCommit(true)}
              >
                Change
              </button>
            </p>
          ) : (
            <>
              <label className="panel-label" htmlFor="panel-commit">
                Which click saves your work?
              </label>
              <select
                id="panel-commit"
                className="panel-input"
                value={commitStepId}
                onChange={(event) => {
                  setCommitStepId(event.target.value);
                  // PL-112: an old refusal was about the old answer; it is no longer true.
                  setProblem(null);
                }}
              >
                {clickChoices.map((choice) => (
                  <option key={choice.id} value={choice.id}>
                    {choice.label}
                  </option>
                ))}
                <option value="">Nothing gets saved</option>
              </select>
            </>
          )}
        </div>
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
          <div className="panel-key">
            {keyColumn !== "" && !changingKey ? (
              <p className="panel-note">
                We'll use <b>{keyLabel}</b> so nothing gets added twice.{" "}
                <button
                  type="button"
                  className="panel-change"
                  aria-label="Change the value that is different for every row"
                  onClick={() => setChangingKey(true)}
                >
                  Change
                </button>
              </p>
            ) : (
              <>
                <label className="panel-label" htmlFor="panel-task-key">
                  Which value is different for every row?
                </label>
                <p className="panel-note">We use it so nothing gets added twice.</p>
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
          </div>
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
