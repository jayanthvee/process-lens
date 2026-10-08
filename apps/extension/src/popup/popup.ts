// The popup: the start/stop/clear controls, the live count, and the export.
// It holds no recording state itself — every action goes to the service worker.
import { EMPTY_STATUS, type RecorderStatus, type ToWorker } from "../shared/messages";
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

chrome.runtime.onMessage.addListener((message: unknown) => {
  const incoming = message as { type?: string; status?: RecorderStatus };
  if (incoming?.type === "record:state" && incoming.status) render(incoming.status);
});

void refresh();
