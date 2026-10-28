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
  SAVE_BY_ITSELF,
  SAVE_BY_ITSELF_CHOICE,
  compileRecording,
  defaultCommitChoice,
  defaultCommitStepId,
  defaultRecordKeyColumn,
  hasSaveClick,
  hiddenSeqs as hiddenStepSeqs,
  makeRecipeRequest,
  readShape,
  recordedValues,
  saveTask,
  shapeRecipeRequest,
  updateDetected,
  type CleanOutcome,
  type TaskShape,
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
import { rememberSignInEmail, taughtSignIn } from "./signInEmails";

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

// PL-118 item 5: the name box starts as "<site> task" (or empty), never as a previous
// recording's name. "Recorded on <host>" was the old wording the ticket calls out.
function defaultName(events: RecordedEvent[]): string {
  try {
    const host = new URL(events[0]?.url ?? "").hostname;
    return host.length > 0 ? `${host} task` : "";
  } catch {
    return "";
  }
}

// PL-125: the name a person typed survives switching panel tabs, which unmounts this screen.
const NAME_KEY = "processlens.teach.name";

function storedName(): string {
  try {
    return window.sessionStorage.getItem(NAME_KEY) ?? "";
  } catch {
    return "";
  }
}

function storeName(value: string | null): void {
  try {
    if (value === null) window.sessionStorage.removeItem(NAME_KEY);
    else window.sessionStorage.setItem(NAME_KEY, value);
  } catch {
    // A private window may refuse storage; the name then lasts as long as this screen.
  }
}

// PL-125: after Stop nothing is the model's to hide; the page's own values are hidden by their
// flag (`hiddenSeqs`), and this marker says "Stop has happened" without any model answer.
const STOPPED: CleanOutcome = { ok: true, available: false, steps: [] };

function quoted(value: unknown): string {
  return `“${value === null || value === undefined ? "" : String(value)}”`;
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
  const [name, setName] = useState(storedName);
  // PL-118 item 5: a name the person typed survives a refresh; a suggested name follows the
  // site until they touch it. Start over clears it, so a previous recording's name never returns.
  const [nameEdited, setNameEdited] = useState(() => storedName() !== "");
  // PL-118 item 5: Start over asks first, on this screen, before it throws steps away.
  const [confirmingOver, setConfirmingOver] = useState(false);
  const [keyColumn, setKeyColumn] = useState("");
  const [saving, setSaving] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  // PL-107: which recorded click saves the work; "" is "Nothing gets saved"; the PL-118 sentinel
  // means the app saves by itself when the form is left.
  const [commitStepId, setCommitStepId] = useState("");
  const [commitEdited, setCommitEdited] = useState(false);
  // PL-109 item 5/6: the decision is shown, and "Change" opens the question only when asked.
  const [changingKey, setChangingKey] = useState(false);
  const [changingCommit, setChangingCommit] = useState(false);
  // PL-123: what teaching said about the stopped recording, and which hidden steps the person
  // brought back. Nothing is hidden until Stop has asked.
  const [cleaned, setCleaned] = useState<CleanOutcome | null>(null);
  const [broughtBack, setBroughtBack] = useState<Set<number>>(new Set());
  const [showingHidden, setShowingHidden] = useState(false);
  // PL-125: what the stopped recording teaches, the person's answer about the save, and whether
  // the recorded steps are open under the questions.
  const [shape, setShape] = useState<TaskShape | null>(null);
  const [saveAnswer, setSaveAnswer] = useState<"button" | "leave">("leave");
  const [showingRecorded, setShowingRecorded] = useState(false);

  const recording = status.state === "recording";
  // PL-107 item 12: what the recorder is doing about a code page, if it is on one.
  const codeState = status.code ?? null;
  // PL-123: after Stop, teaching may have decided some recorded steps were the page's own doing.
  // They are hidden, not deleted: Show lists them and Bring back returns any of them. PL-124: the
  // page's own values are hidden by their flag, and a value the person typed is never hidden.
  const hiddenSeqs = hiddenStepSeqs(events, cleaned, broughtBack);
  const visibleEvents = events.filter((event) => !hiddenSeqs.has(event.seq));
  // PL-125: a stopped recording that opened an item is a task with a shape. The panel then asks
  // three plain questions about it instead of showing every recorded step for the person to tidy.
  const shapeMode = !recording && status.signIn !== true && shape !== null && shape.opened;
  const hiddenEvents = events.filter((event) => hiddenSeqs.has(event.seq));
  const values = recordedValues(visibleEvents);
  // PL-107: one choice per recorded click, labelled by the click's own sentence.
  const clickChoices: { id: string; label: string }[] = [];
  for (const event of visibleEvents) {
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
      setConfirmingOver(false);
      setName((current) =>
        nameEdited && current.trim() !== "" ? current : defaultName(next.events),
      );
    }
  }

  useEffect(() => {
    let live = true;
    void send({ type: "record:get" }).then((answer) => {
      if (!live) return;
      apply(answer);
      // PL-125: switching panel tabs unmounts this screen; a stopped recording's questions are
      // read again rather than lost.
      if (answer && answer.status?.state !== "recording" && answer.events.length > 0) {
        void askShape(answer.events);
      }
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
      setKeyColumn(defaultRecordKeyColumn(visibleEvents) ?? "");
    }
    // `values` is derived from the visible events, and the key is the only thing this effect owns.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [events, cleaned, broughtBack]);

  // The save choice follows the recording: the app's own save when it has no Save click, else the
  // last click, until the person picks another.
  useEffect(() => {
    setCommitStepId((current) =>
      commitEdited
        ? current
        : status.signIn === true
          ? defaultCommitStepId(visibleEvents) ?? ""
          : defaultCommitChoice(visibleEvents),
    );
    // Only re-derive on a new recording, a cleaning decision, or a sign-in decision; a deliberate
    // answer is kept.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [events, cleaned, broughtBack, status.signIn]);

  /**
   * Start (or continue) teaching. PL-118 item 5: this never clears. A stopped recording is kept
   * and Continue teaching adds to it; throwing it away is Start over's explicit, confirmed job.
   */
  async function start(): Promise<void> {
    setProblem(null);
    setNote(null);
    setConfirmingOver(false);
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
    const stopped = answer?.events ?? events;
    if (stopped.length === 0) {
      setCleaned(null);
      setShape(null);
      return;
    }
    setBroughtBack(new Set());
    setShowingHidden(false);
    setShowingRecorded(false);
    await askShape(stopped);
  }

  /**
   * PL-125: read what the recording teaches. No model is asked anything at teaching: the page's
   * own values are hidden by their flag, and the shape picks the few steps the task needs.
   */
  async function askShape(stopped: RecordedEvent[]): Promise<void> {
    setCleaned(STOPPED);
    const config = apiConfig();
    const read = await readShape(stopped, { baseUrl: config.base, headers: config.headers });
    setShape(read);
    if (read?.save) setSaveAnswer(read.save);
  }

  /** PL-107 item 12: the panel's yes to reading a code from Gmail, for this and later tasks. */
  async function allowCodes(): Promise<void> {
    apply(await send({ type: "record:code-consent" }));
  }

  async function remove(seq: number): Promise<void> {
    const answer = await send({ type: "record:remove", seq });
    apply(answer);
  }

  /** PL-118 item 5: Start over asks on this screen before the steps are thrown away. */
  function askStartOver(): void {
    setProblem(null);
    setNote(null);
    setConfirmingOver(true);
  }

  /**
   * PL-103: a step that cannot be repeated offers Start over, which is the popup's Clear: the
   * recording is emptied so the person teaches it again from the start, on the list this time.
   * Only the confirmed yes reaches here.
   */
  async function startOver(): Promise<void> {
    setConfirmingOver(false);
    const answer = await send({ type: "record:clear" });
    apply(answer);
    setCleaned(null);
    setShape(null);
    setBroughtBack(new Set());
    setShowingHidden(false);
    setName("");
    setNameEdited(false);
    storeName(null);
  }

  async function save(): Promise<void> {
    if (saving) return;
    const trimmed = name.trim();
    if (trimmed === "") {
      setProblem("Give the task a name first.");
      return;
    }
    if (visibleEvents.length === 0) {
      setProblem(NO_EVENTS);
      return;
    }
    if (shapeMode && shape?.kind === null) {
      setProblem(shape.problem ?? NO_EVENTS);
      return;
    }
    if (!shapeMode && values.length > 0 && keyColumn === "") {
      setProblem("Choose which value is different for every row.");
      return;
    }

    setSaving(true);
    setProblem(null);
    setNote("Turning your recording into a task…");
    try {
      const selfSave = commitStepId === SAVE_BY_ITSELF;
      const intents: Record<string, string> = {};
      if (cleaned?.ok === true) {
        for (const step of cleaned.steps) {
          if (step.keep && step.intent.trim() !== "") intents[step.id] = step.intent;
        }
      }
      const request = shapeMode
        ? shapeRecipeRequest(events, { name: trimmed, save: saveAnswer })
        : makeRecipeRequest(visibleEvents, {
        name: trimmed,
        recordKeyColumn: keyColumn,
        // PL-107: the person chooses which click saves; "" is "Nothing gets saved", which
        // compiles a read-only recipe (no commit step). PL-118: the sentinel is the app's own
        // save when the form is left, and the compiler builds its leave/re-open commit.
        // PL-117: a sign-in saves nothing, so it never sends a commit choice at all.
        commitStepId:
          status.signIn === true || selfSave || commitStepId === "" ? null : commitStepId,
        saveByItself: selfSave && status.signIn !== true,
        intents,
      });
      if (!shapeMode && values.length === 0) {
        // No column to key on: every Run once carries a fresh id, so the ledger sees new work.
        request.record_key_column = "";
        request.record_key = "{{row.run_once_id}}";
        request.sample_row = { run_once_id: "x" };
      }
      if (status.signIn === true) {
        // PL-113 S2: a sign-in recipe ends by waiting for the page after the code page, so the
        // compile is told that this was a sign-in and where it landed.
        request.sign_in = true;
        const after = status.signInAfterUrl ?? "";
        if (after !== "") request.sign_in_after_path = after;
      }
      const config = apiConfig();
      const compiled = await compileRecording(request, {
        baseUrl: config.base,
        headers: config.headers,
      });
      if (!compiled.ok) {
        setProblem(
          compileRefusalSentence(compiled.problems, visibleEvents, {
            nothingSaved: status.signIn === true || commitStepId === "",
          }),
        );
        setNote(null);
        return;
      }
      const signIn =
        status.signIn === true ? taughtSignIn(values, keyColumn) : null;
      const saved = await saveTask(
        {
          name: trimmed,
          recipe: compiled.recipe,
          ...(signIn === null ? {} : { sign_in: true, sign_in_column: signIn.column }),
        },
        { baseUrl: config.base, headers: config.headers },
      );
      if (!saved.ok) {
        const plain = plainSentences(saved.problems.join("\n")).shown[0];
        setProblem(plain ?? "Could not save the task.");
        setNote(null);
        return;
      }
      if (signIn !== null) {
        // The taught address is the first choice in Tasks, and the list stays in this Chrome.
        await rememberSignInEmail(
          extensionChrome()?.storage?.local,
          saved.workflowId,
          signIn.email,
        );
      }
      setNote(`${trimmed} is ready. It is in Tasks.`);
      setName(trimmed);
      setNameEdited(true);
    } catch (failure) {
      setProblem(failure instanceof Error ? failure.message : "Could not reach ProcessLens.");
      setNote(null);
    } finally {
      setSaving(false);
    }
  }

  const lines = stepLines(visibleEvents);
  // PL-118 item 2: the app's own save is offered whenever the recording has no Save click. A
  // sign-in saves nothing (PL-113 S2), so it is never offered there.
  const selfSaveOffered =
    visibleEvents.length > 0 && status.signIn !== true && !hasSaveClick(visibleEvents);
  // PL-118 item 4: an edit's key line names the key column and the exact change.
  const isUpdate = updateDetected(visibleEvents, keyColumn);
  // PL-118 item 5: the count is the lines the person sees; a code typed for them is named, not
  // counted as a step.
  const hiddenCodeSteps = status.code?.state === "typed" ? 1 : 0;
  // PL-109 item 5/6: the labels the decisions name.
  const keyLabel = values.find((value) => value.column === keyColumn)?.label ?? keyColumn;
  const commitLabel =
    clickChoices.find((choice) => choice.id === commitStepId)?.label.replace(/^Clicked /, "") ?? "";
  // PL-103: a step that cannot be repeated says so on its own row, with its fix. Like the popup,
  // the check runs once the recording is stopped: while it is still being built there is nothing
  // to judge yet.
  const problems = recording ? new Map<number, StepProblem>() : stepProblems(visibleEvents);
  // PL-107: a plain click's own words are already the "Which click saves your work?" choices, so
  // the step list shows the other steps and any step that needs a fix (a click's included). That
  // keeps one line per step on screen instead of the same sentence twice.
  const clickSeqs = new Set(
    visibleEvents.filter((event) => event.step?.action === "click").map((event) => event.seq),
  );
  const listLines = lines.filter((line) => !clickSeqs.has(line.seq) || problems.has(line.seq));
  // PL-109 item 1: the value the person typed, shown where the step names the field.
  const typedBySeq = new Map<number, string>();
  for (const event of visibleEvents) {
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
        <button
          type="button"
          className="btn primary"
          disabled={recording}
          onClick={() => void start()}
        >
          {!recording && lines.length > 0 ? "Continue teaching" : "Start teaching"}
        </button>
        <button type="button" className="btn quiet" disabled={!recording} onClick={() => void stop()}>
          Stop
        </button>
        {lines.length > 0 && !recording ? (
          <button type="button" className="btn quiet" onClick={askStartOver}>
            Start over
          </button>
        ) : null}
      </div>
      {confirmingOver ? (
        <div className="panel-confirm" role="alertdialog" aria-label="Throw away these steps?">
          <p className="panel-note">Throw away these {lines.length} steps?</p>
          <button
            type="button"
            className="btn primary"
            onClick={() => void startOver()}
          >
            Yes
          </button>
          <button
            type="button"
            className="btn quiet"
            onClick={() => setConfirmingOver(false)}
          >
            Keep them
          </button>
        </div>
      ) : null}
      <p className="panel-count">
        {lines.length} step{lines.length === 1 ? "" : "s"} recorded
      </p>
      {hiddenCodeSteps > 0 ? (
        <p className="panel-note">+{hiddenCodeSteps} hidden code step</p>
      ) : null}
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
      {shapeMode && shape !== null ? (
        <div className="panel-shape" aria-label="What this task does">
          {shape.kind === null ? (
            <p className="panel-problem" role="status">
              {shape.problem}
            </p>
          ) : (
            <>
              <p className="panel-label">Which item?</p>
              {shape.kind === "update" ? (
                <p className="panel-note">
                  Each row opens the {shape.item?.label.toLowerCase() || "item"} it names. You
                  opened <b>{quoted(shape.item?.text)}</b>.
                </p>
              ) : (
                <p className="panel-note">
                  Each row adds a new one. A row whose{" "}
                  <b>{shape.item?.label || "key"}</b> is already on the list is skipped.
                </p>
              )}
              <p className="panel-label">What changes?</p>
              <ul className="panel-changes">
                {shape.changes.map((change) => (
                  <li key={change.column} className="panel-note">
                    <b>{change.label}</b> — you typed {quoted(change.value)}
                    {change.was === null || change.was === undefined || change.was === ""
                      ? ""
                      : ` (it was ${quoted(change.was)})`}
                  </li>
                ))}
              </ul>
              <p className="panel-label">How is it saved?</p>
              {shape.save === "button" ? (
                <div className="panel-save-choice" role="radiogroup">
                  <label className="panel-note">
                    <input
                      type="radio"
                      name="panel-save-answer"
                      checked={saveAnswer === "button"}
                      onChange={() => setSaveAnswer("button")}
                    />{" "}
                    The <b>{shape.save_label?.replace(/^Click /, "") || "Save"}</b> click saves it
                  </label>
                  <label className="panel-note">
                    <input
                      type="radio"
                      name="panel-save-answer"
                      checked={saveAnswer === "leave"}
                      onChange={() => setSaveAnswer("leave")}
                    />{" "}
                    It saves when I leave the page
                  </label>
                </div>
              ) : (
                <p className="panel-note">
                  It saves when you leave the page. Each row is checked afterwards, so a change
                  that didn't save is never marked done.
                </p>
              )}
            </>
          )}
          <button
            type="button"
            className="panel-change"
            onClick={() => setShowingRecorded((current) => !current)}
          >
            {showingRecorded ? "Hide what was recorded" : `Show what was recorded (${lines.length})`}
          </button>
        </div>
      ) : null}
      {shapeMode && !showingRecorded ? null : lines.length === 0 ? (
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

      {shapeMode || hiddenEvents.length === 0 ? null : (
        <div className="panel-hidden">
          <p className="panel-note">
            Hidden: {hiddenEvents.length} step{hiddenEvents.length === 1 ? "" : "s"} the page
            did by itself.{" "}
            <button
              type="button"
              className="panel-change"
              aria-label={showingHidden ? "Hide the steps the page did by itself" : "Show the steps the page did by itself"}
              onClick={() => setShowingHidden((current) => !current)}
            >
              {showingHidden ? "Hide" : "Show"}
            </button>
          </p>
          {!showingHidden ? null : (
            <ol className="panel-steps">
              {hiddenEvents.map((event) => {
                const sentence = stepLines([event])[0]?.sentence ?? "This step";
                return (
                  <li key={event.seq} className="panel-step is-hidden">
                    <div className="panel-step-line">
                      <span>{sentence}</span>
                      <button
                        type="button"
                        className="panel-step-fix"
                        onClick={() =>
                          setBroughtBack((current) => new Set(current).add(event.seq))
                        }
                      >
                        Bring back
                      </button>
                    </div>
                  </li>
                );
              })}
            </ol>
          )}
        </div>
      )}

      {visibleEvents.length === 0 || recording || shapeMode ? null : status.signIn === true ? (
        // PL-117: a sign-in saves nothing, so there is no save click to offer or send.
        <div className="panel-commit">
          <p className="panel-note">A sign-in saves nothing.</p>
        </div>
      ) : clickChoices.length === 0 && !selfSaveOffered ? null : (
        <div className="panel-commit">
          {!changingCommit ? (
            <p className="panel-note">
              {commitStepId === "" ? (
                <>Nothing gets saved.</>
              ) : commitStepId === SAVE_BY_ITSELF ? (
                <>{SAVE_BY_ITSELF_CHOICE}.</>
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
              {selfSaveOffered ? (
                // PL-113 item 2 / PL-118 item 2: no click in the recording changes data. Say so
                // plainly and offer the app's own save instead of letting a tab stand in.
                <p className="panel-note">
                  We didn't see a Save click. Some apps save by themselves. Click Save while
                  teaching, or choose how your work is saved.
                </p>
              ) : null}
              <label className="panel-label" htmlFor="panel-commit">
                Which click saves your work?
              </label>
              <select
                id="panel-commit"
                className="panel-input"
                value={commitStepId}
                onChange={(event) => {
                  setCommitStepId(event.target.value);
                  setCommitEdited(true);
                  // PL-112: an old refusal was about the old answer; it is no longer true.
                  setProblem(null);
                }}
              >
                {clickChoices.map((choice) => (
                  <option key={choice.id} value={choice.id}>
                    {choice.label}
                  </option>
                ))}
                {selfSaveOffered ? (
                  <option value={SAVE_BY_ITSELF}>{SAVE_BY_ITSELF_CHOICE}</option>
                ) : null}
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
          autoComplete="off"
          spellCheck={false}
          onChange={(event) => {
            setName(event.target.value);
            setNameEdited(true);
            storeName(event.target.value);
          }}
        />
        {/* PL-116: a sign-in runs with one email; the duplicate-key line describes a spreadsheet
            task and is noise here. The key is still worked out and sent on save, unchanged. A
            sign-in that typed no identifiable value still needs the question -- hiding it would
            leave Save disabled with no way out -- so only its note is left out. */}
        {shapeMode || values.length === 0 || (status.signIn === true && keyColumn !== "") ? null : (
          <div className="panel-key">
            {keyColumn !== "" && !changingKey ? (
              <p className="panel-note">
                {isUpdate ? (
                  <>
                    We'll skip a row if this <b>{keyLabel.toLowerCase()}</b> already got this
                    exact change.{" "}
                  </>
                ) : (
                  <>
                    We'll use <b>{keyLabel}</b> so nothing gets added twice.{" "}
                  </>
                )}
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
                {status.signIn === true ? null : (
                  <p className="panel-note">We use it so nothing gets added twice.</p>
                )}
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
          disabled={
            saving ||
            visibleEvents.length === 0 ||
            (shapeMode ? shape?.kind === null : values.length > 0 && keyColumn === "")
          }
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
