// The popup: the start/stop/clear controls, the live count, and the export.
// It holds no recording state itself — every action goes to the service worker.
import {
  EMPTY_STATUS,
  type RecorderStatus,
  type ToWorker,
} from "../shared/messages";
import type { SessionState } from "../background/command-session";
import type { RecordedEvent } from "../shared/recipe-types";
import { createMakeRecipeForm } from "./make-recipe";
import { applyStep, autoConnect, currentStep, openTaskTab, renderStepList } from "./flow";
import {
  recordingWords,
  sessionWords,
  stepCountWords,
  streamWords,
} from "./words";

interface RecorderResponse {
  ok: boolean;
  status: RecorderStatus;
  events: RecordedEvent[];
}

/** Where the hub session is, as the worker reports it. */
interface SessionInfo {
  state: SessionState;
  detail?: string;
}

/**
 * Where the popup points when the person has not typed an address.
 *
 * A handed-out build bakes in the hub it was built for: `npm run build` replaces
 * `__PROCESSLENS_HUB_URL__` with the address it was given (`PROCESSLENS_HUB_URL`), so a
 * coworker's copy already knows where ProcessLens is. A build given no address -- the e2e
 * harness's own, and a developer's -- keeps this machine.
 */
declare const __PROCESSLENS_HUB_URL__: string | undefined;

const DEFAULT_HUB_URL: string =
  typeof __PROCESSLENS_HUB_URL__ === "string" && __PROCESSLENS_HUB_URL__.trim() !== ""
    ? __PROCESSLENS_HUB_URL__.trim()
    : "http://127.0.0.1:8000";

function element<T extends HTMLElement>(id: string): T {
  const found = document.getElementById(id);
  if (!found) throw new Error(`the popup is missing #${id}`);
  return found as T;
}

const stateEl = element<HTMLParagraphElement>("state");
const countEl = element<HTMLElement>("count");
const streamEl = element<HTMLElement>("stream");
const noteEl = element<HTMLParagraphElement>("note");
const wsEl = element<HTMLInputElement>("ws");
const startButton = element<HTMLButtonElement>("start");
const stopButton = element<HTMLButtonElement>("stop");
const clearButton = element<HTMLButtonElement>("clear");
const exportButton = element<HTMLButtonElement>("export");

// The hub session controls: a hub URL, connect and disconnect, and the state the
// worker reports. The session itself lives in the service worker.
const hubEl = element<HTMLInputElement>("hub");
const connectButton = element<HTMLButtonElement>("connect");
const disconnectButton = element<HTMLButtonElement>("disconnect");
const sessionStateEl = element<HTMLParagraphElement>("session-state");
const stepsEl = element<HTMLOListElement>("steps");

// The "Make recipe" form: after a Stop it compiles the worker's recording and
// writes the recipe and its row where Run reads them. It is built once; the hub
// URL is re-read on every submit, so the field below takes effect without a rebuild.
const makeGoButton = element<HTMLButtonElement>("make-go");
// The flow's last step: the saved task's page, opened in a real tab (PL-033 requirement 2).
const openTaskButton = element<HTMLButtonElement>("open-task");
const makeRecipeDeps = {
  root: document,
  storage: sessionStorageAdapter(),
  baseUrl: DEFAULT_HUB_URL,
  onNotice: (message: string) => {
    noteEl.textContent = message;
  },
  onSaved: (taskUrl: string) => {
    savedTaskUrl = taskUrl;
    syncFlow();
  },
};
const makeRecipeForm = createMakeRecipeForm(makeRecipeDeps);

/**
 * chrome.storage.session holds the recipe and the row, the two keys the Run
 * button reads. A build without that storage area still opens the popup — the
 * form just cannot keep what it compiled.
 */
function sessionStorageAdapter(): {
  get(keys: string[]): Promise<Record<string, unknown>>;
  set(items: Record<string, unknown>): Promise<void>;
} {
  return {
    get: async (keys) => {
      const store = chrome.storage?.session;
      return store ? ((await store.get(keys)) as Record<string, unknown>) : {};
    },
    set: async (items) => {
      const store = chrome.storage?.session;
      if (store) await store.set(items);
    },
  };
}

/** The recording as the worker last reported it, kept current from `record:appended`. */
let recordedEvents: RecordedEvent[] = [];
/** Whether the recorder is running right now, as the last status reported it. */
let recordingNow = false;
/**
 * PL-039: why the last start did not happen, in the worker's own words, or null. It shows in
 * place of the steps until the next start clears it.
 */
let statusWarning: string | null = null;
/** The saved task's page on the dashboard, set once a save has gone through (PL-033). */
let savedTaskUrl: string | null = null;

/**
 * Put the three cards in the state the person's progress calls for: Teach while teaching, Save
 * once there is a recording, Open in ProcessLens once a task has been saved. Only the current
 * step's button is gold (PL-033 requirement 1).
 */
function syncFlow(): void {
  applyStep(
    document,
    currentStep({
      recording: recordingNow,
      hasEvents: recordedEvents.length > 0,
      savedTaskUrl,
    }),
  );
}

async function send(message: ToWorker): Promise<RecorderResponse> {
  const response = (await chrome.runtime.sendMessage(message)) as RecorderResponse | undefined;
  return response ?? { ok: true, status: { ...EMPTY_STATUS }, events: [] };
}

/**
 * The live list of recorded steps: one plain sentence per step, and one sentence of its own while
 * there is nothing recorded. Each line's × asks the worker to delete that step (PL-043); it is
 * hidden while the recording is still running.
 */
function renderSteps(): void {
  renderStepList(stepsEl, recordedEvents, {
    recording: recordingNow,
    onRemove: removeStep,
    warning: statusWarning,
  });
}

/**
 * Delete one recorded step, and redraw from the recording the worker answers with: the list, the
 * count and the save form's columns all follow from that one answer.
 */
function removeStep(seq: number): void {
  void send({ type: "record:remove", seq }).then((response) => {
    render(response.status);
    recordedEvents = response.events ?? [];
    renderSteps();
    makeRecipeForm.refresh(recordedEvents);
  });
}

function render(status: RecorderStatus): void {
  const recording = status.state === "recording";
  recordingNow = recording;
  statusWarning = status.warning ?? null;
  stateEl.textContent = recordingWords(recording ? "recording" : "idle");
  stateEl.dataset["state"] = status.state;
  countEl.textContent = stepCountWords(status.count);
  streamEl.textContent = streamWords(status.wsUrl, status.wsConnected);
  startButton.disabled = recording;
  stopButton.disabled = !recording;
  if (document.activeElement !== wsEl) wsEl.value = status.wsUrl;
  // The × is shown only once the recording has stopped, so the list follows the state too.
  renderSteps();
  syncFlow();
}

async function refresh(): Promise<void> {
  const response = await send({ type: "record:get" });
  render(response.status);
  recordedEvents = response.events ?? [];
  renderSteps();
  makeRecipeForm.refresh(recordedEvents);
  syncFlow();
}

/**
 * Merge an event the worker reported into the list the form offers.
 *
 * A recording is still moving while the popup is open: the page an action navigated to can
 * report its confirmation a moment after Stop, and the worker sends that as `record:appended`
 * even though it replaces an event already in the list rather than adding one. Keeping the list
 * current is what stops the form compiling a recording that is missing its observation.
 */
function mergeEvent(event: RecordedEvent): void {
  const index = recordedEvents.findIndex((candidate) => candidate.seq === event.seq);
  if (index >= 0) recordedEvents[index] = event;
  else recordedEvents.push(event);
  renderSteps();
  makeRecipeForm.refresh(recordedEvents);
}

function download(events: RecordedEvent[]): void {
  const blob = new Blob([`${JSON.stringify(events, null, 2)}\n`], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = "recording.json";
  link.click();
  URL.revokeObjectURL(url);
  noteEl.textContent = `Saved ${events.length} step${events.length === 1 ? "" : "s"} to a file.`;
}

startButton.addEventListener("click", () => {
  // The popup's own window is the one the person is looking at, so its active tab is the page.
  void chrome.tabs
    .query({ active: true, currentWindow: true })
    .then((tabs) => tabs[0]?.id)
    .catch(() => undefined)
    .then((tabId) => send({ type: "record:start", ...(tabId !== undefined ? { tabId } : {}) }))
    .then((response) => render(response.status));
});

stopButton.addEventListener("click", () => {
  void send({ type: "record:stop" }).then((response) => {
    render(response.status);
    // The freshly stopped recording is what the form offers to compile.
    recordedEvents = response.events ?? [];
    renderSteps();
    makeRecipeForm.refresh(recordedEvents);
  });
});

clearButton.addEventListener("click", () => {
  void send({ type: "record:clear" }).then((response) => {
    render(response.status);
    recordedEvents = response.events ?? [];
    renderSteps();
    makeRecipeForm.refresh(recordedEvents);
  });
});

makeGoButton.addEventListener("click", () => {
  makeRecipeDeps.baseUrl = hubEl.value.trim() || DEFAULT_HUB_URL;
  void makeRecipeForm.submit();
});

openTaskButton.addEventListener("click", () => {
  if (savedTaskUrl === null) return;
  // A plain `<a href>` does nothing in an extension popup; the tab API is the way out.
  openTaskTab(chrome.tabs, savedTaskUrl);
});

wsEl.addEventListener("change", () => {
  void send({ type: "record:set-ws", wsUrl: wsEl.value.trim() }).then((response) =>
    render(response.status),
  );
});

exportButton.addEventListener("click", () => {
  void send({ type: "record:get" }).then((response) => download(response.events));
});

async function sendExecutor(
  message: ToWorker,
): Promise<{ ok?: boolean; error?: string; session?: SessionInfo }> {
  const response = (await chrome.runtime.sendMessage(message)) as
    | { ok?: boolean; error?: string; session?: SessionInfo }
    | undefined;
  return response ?? {};
}

function renderSession(session: SessionInfo | undefined): void {
  const state = session?.state ?? "idle";
  sessionNow = state;
  sessionStateEl.textContent = sessionWords(state, session?.detail);
  sessionStateEl.dataset["state"] = state;
  // A connected session's dot is the one that is "on"; the word carries the meaning either way.
  sessionStateEl.classList.toggle("status--on", state === "ready" || state === "executing");
  connectButton.disabled = state !== "idle" && state !== "disconnected";
  disconnectButton.disabled = state === "idle";
}

/** Where the hub session is, as this page last saw it, so auto-connect asks nobody twice. */
let sessionNow: SessionState = "idle";

async function refreshSession(): Promise<void> {
  const response = await sendExecutor({ type: "executor:session" });
  renderSession(response.session);
}

/**
 * Connect by itself when the popup opens, if no session is there (PL-033 requirement 4). The
 * address is the one in the ProcessLens address field, which defaults to `http://127.0.0.1:8000`.
 */
async function connectOnOpen(): Promise<void> {
  const connected = await autoConnect({
    sessionState: async () => sessionNow,
    connect: async (hubUrl) => {
      const response = await sendExecutor({ type: "executor:connect", hubUrl });
      if (response.error) noteEl.textContent = response.error;
    },
    hubUrl: () => hubEl.value.trim() || DEFAULT_HUB_URL,
  });
  if (connected) await refreshSession();
}

connectButton.addEventListener("click", () => {
  const hubUrl = hubEl.value.trim() || DEFAULT_HUB_URL;
  void sendExecutor({ type: "executor:connect", hubUrl }).then((response) => {
    renderSession(response.session);
    if (response.error) noteEl.textContent = response.error;
  });
});

disconnectButton.addEventListener("click", () => {
  void sendExecutor({ type: "executor:disconnect" }).then((response) => renderSession(response.session));
});

chrome.runtime.onMessage.addListener((message: unknown) => {
  const incoming = message as {
    type?: string;
    status?: RecorderStatus;
    state?: SessionState;
    detail?: string;
    event?: RecordedEvent;
  };
  if (incoming?.type === "record:state" && incoming.status) render(incoming.status as RecorderStatus);
  if (incoming?.type === "record:appended" && incoming.event) mergeEvent(incoming.event);
  if (incoming?.type === "executor:session-state" && incoming.state) {
    renderSession({ state: incoming.state, detail: incoming.detail });
  }
});

renderSession({ state: "idle" });
void refresh();
// Connect first, then say so: the person opens the popup and reads either "Connected to
// ProcessLens" or "ProcessLens isn't running", without pressing anything.
void refreshSession().then(connectOnOpen);
