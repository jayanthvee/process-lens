// The popup: the start/stop/clear controls, the live count, and the export.
// It holds no recording state itself — every action goes to the service worker.
import {
  EMPTY_EXECUTOR_STATUS,
  EMPTY_STATUS,
  type ExecutorStatus,
  type RecorderStatus,
  type ToWorker,
} from "../shared/messages";
import type { RecordedEvent } from "../shared/recipe-types";

interface RecorderResponse {
  ok: boolean;
  status: RecorderStatus;
  events: RecordedEvent[];
}

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

// The attended-execution controls. The run itself lives in the service worker;
// this popup only sends the start/pause/resume/abort messages and shows where the
// run is. The recipe and its input row are loaded into the browser session by
// whoever prepared the run, under the keys read below.
const runStartButton = element<HTMLButtonElement>("run-start");
const runPauseButton = element<HTMLButtonElement>("run-pause");
const runResumeButton = element<HTMLButtonElement>("run-resume");
const runAbortButton = element<HTMLButtonElement>("run-abort");
const runStateEl = element<HTMLParagraphElement>("run-state");

const RECIPE_KEY = "processlens:executor-recipe";
const ROW_KEY = "processlens:executor-row";

async function send(message: ToWorker): Promise<RecorderResponse> {
  const response = (await chrome.runtime.sendMessage(message)) as RecorderResponse | undefined;
  return response ?? { ok: true, status: { ...EMPTY_STATUS }, events: [] };
}

function render(status: RecorderStatus): void {
  const recording = status.state === "recording";
  stateEl.textContent = recording ? "Recording" : "Idle";
  stateEl.dataset["state"] = status.state;
  countEl.textContent = String(status.count);
  streamEl.textContent = status.wsUrl ? (status.wsConnected ? "connected" : "configured") : "off";
  startButton.disabled = recording;
  stopButton.disabled = !recording;
  if (document.activeElement !== wsEl) wsEl.value = status.wsUrl;
}

async function refresh(): Promise<void> {
  const response = await send({ type: "record:get" });
  render(response.status);
}

function download(events: RecordedEvent[]): void {
  const blob = new Blob([`${JSON.stringify(events, null, 2)}\n`], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = "recording.json";
  link.click();
  URL.revokeObjectURL(url);
  noteEl.textContent = `Exported ${events.length} event(s).`;
}

startButton.addEventListener("click", () => {
  void send({ type: "record:start" }).then((response) => render(response.status));
});

stopButton.addEventListener("click", () => {
  void send({ type: "record:stop" }).then((response) => render(response.status));
});

clearButton.addEventListener("click", () => {
  void send({ type: "record:clear" }).then((response) => render(response.status));
});

wsEl.addEventListener("change", () => {
  void send({ type: "record:set-ws", wsUrl: wsEl.value.trim() }).then((response) =>
    render(response.status),
  );
});

exportButton.addEventListener("click", () => {
  void send({ type: "record:get" }).then((response) => download(response.events));
});

function renderExecutor(status: ExecutorStatus): void {
  const progress = status.total > 0 ? ` (${status.completed}/${status.total})` : "";
  runStateEl.textContent = status.state === "idle"
    ? "Idle"
    : `${status.state}${progress} — ${status.message}`.trim();
  runStateEl.dataset["state"] = status.state;
  const busy = status.state === "running" || status.state === "paused";
  runPauseButton.disabled = status.state !== "running";
  runResumeButton.disabled = status.state !== "paused";
  runAbortButton.disabled = !busy;
}

async function sendExecutor(message: ToWorker): Promise<{ ok?: boolean; error?: string; status?: ExecutorStatus }> {
  const response = (await chrome.runtime.sendMessage(message)) as
    | { ok?: boolean; error?: string; status?: ExecutorStatus }
    | undefined;
  return response ?? {};
}

async function startExecutor(): Promise<void> {
  const store = chrome.storage?.session;
  const stored = store ? ((await store.get([RECIPE_KEY, ROW_KEY])) as Record<string, unknown>) : {};
  const recipe = stored[RECIPE_KEY];
  if (!recipe) {
    noteEl.textContent = "No recipe is loaded into this browser session.";
    return;
  }
  const response = await sendExecutor({
    type: "executor:start",
    recipe,
    row: (stored[ROW_KEY] as Record<string, unknown>) ?? {},
  });
  if (response.status) renderExecutor(response.status);
  if (response.error) noteEl.textContent = response.error;
}

runStartButton.addEventListener("click", () => {
  void startExecutor();
});
runPauseButton.addEventListener("click", () => {
  void sendExecutor({ type: "executor:pause" });
});
runResumeButton.addEventListener("click", () => {
  void sendExecutor({ type: "executor:resume" });
});
runAbortButton.addEventListener("click", () => {
  void sendExecutor({ type: "executor:abort" });
});

chrome.runtime.onMessage.addListener((message: unknown) => {
  const incoming = message as { type?: string; status?: RecorderStatus | ExecutorStatus };
  if (incoming?.type === "record:state" && incoming.status) render(incoming.status as RecorderStatus);
  if (incoming?.type === "executor:state" && incoming.status) {
    renderExecutor(incoming.status as ExecutorStatus);
  }
});

renderExecutor({ ...EMPTY_EXECUTOR_STATUS });
void refresh();
