// The service worker: the recorder's single place of state.
//
// It owns the start/stop/clear state, stamps each event with its order, keeps the
// session history, and — when a WebSocket URL is configured — streams events out
// as JSON lines. The content scripts hold no state of their own.
import {
  EMPTY_EXECUTOR_STATUS,
  EMPTY_STATUS,
  isToWorker,
  type ExecutorStatus,
  type RecorderStatus,
  type ToWorker,
} from "../shared/messages";
import type { PendingEvent } from "../shared/events";
import type { RecordedEvent } from "../shared/recipe-types";
import { attachDebugger, chromeDebuggerTransport, detachDebugger, onDebuggerDetach } from "../executor/cdp";
import { RunControl } from "../executor/control";
import { runRecipe, validateRecipe } from "../executor/executor";
import { FrameContexts } from "../executor/frames";
import type { ExecutorRecipe } from "../executor/types";

const STORAGE_KEY = "processlens:recorder";
const SETTINGS_KEY = "processlens:settings";
const MAX_EVENTS = 5000;
const RECONNECT_DELAY_MS = 2000;

interface Persisted {
  status: RecorderStatus;
  events: RecordedEvent[];
}

let status: RecorderStatus = { ...EMPTY_STATUS };
let events: RecordedEvent[] = [];
let sequence = 0;
let socket: WebSocket | null = null;

function sessionStore(): chrome.storage.StorageArea | null {
  return chrome.storage?.session ?? null;
}

async function load(): Promise<void> {
  const store = sessionStore();
  if (!store) return;
  const stored = (await store.get([STORAGE_KEY, SETTINGS_KEY])) as Record<string, unknown>;
  const persisted = stored[STORAGE_KEY] as Persisted | undefined;
  if (persisted) {
    status = { ...EMPTY_STATUS, ...persisted.status, wsConnected: false };
    events = persisted.events ?? [];
    sequence = events.length > 0 ? events[events.length - 1]!.seq : 0;
  }
  const settings = stored[SETTINGS_KEY] as { wsUrl?: string } | undefined;
  if (settings?.wsUrl) status.wsUrl = settings.wsUrl;
}

async function persist(): Promise<void> {
  const store = sessionStore();
  if (!store) return;
  const persisted: Persisted = { status, events };
  await store.set({ [STORAGE_KEY]: persisted });
}

async function persistSettings(): Promise<void> {
  const store = sessionStore();
  if (!store) return;
  await store.set({ [SETTINGS_KEY]: { wsUrl: status.wsUrl } });
}

function snapshot(): RecorderStatus {
  return { ...status };
}

function broadcast(): void {
  const message = { type: "record:state", status: snapshot() };
  void chrome.runtime.sendMessage(message).catch(() => undefined);
  void chrome.tabs
    ?.query({})
    .then((tabs) => {
      for (const tab of tabs) {
        if (tab.id === undefined) continue;
        void chrome.tabs.sendMessage(tab.id, message).catch(() => undefined);
      }
    })
    .catch(() => undefined);
}

function updateBadge(): void {
  const text = status.state === "recording" ? String(status.count) : "";
  void chrome.action?.setBadgeText?.({ text }).catch(() => undefined);
  void chrome.action
    ?.setBadgeBackgroundColor?.({ color: status.state === "recording" ? "#c0392b" : "#4b5563" })
    .catch(() => undefined);
}

function closeSocket(): void {
  if (socket) {
    const current = socket;
    socket = null;
    try {
      current.close();
    } catch {
      // The socket was already closed; nothing to do.
    }
  }
  status = { ...status, wsConnected: false };
}

function openSocket(): void {
  if (!status.wsUrl || status.state !== "recording") return;
  if (typeof WebSocket === "undefined") return;
  closeSocket();
  try {
    const next = new WebSocket(status.wsUrl);
    socket = next;
    next.addEventListener("open", () => {
      status = { ...status, wsConnected: true };
      updateBadge();
    });
    next.addEventListener("close", () => {
      if (socket === next) {
        socket = null;
        status = { ...status, wsConnected: false };
        if (status.state === "recording" && status.wsUrl) {
          setTimeout(openSocket, RECONNECT_DELAY_MS);
        }
      }
    });
    next.addEventListener("error", () => {
      status = { ...status, wsConnected: false };
    });
  } catch {
    status = { ...status, wsConnected: false };
  }
}

function stream(event: RecordedEvent): void {
  if (socket && socket.readyState === WebSocket.OPEN) {
    try {
      socket.send(`${JSON.stringify(event)}\n`);
    } catch {
      // A dropped socket is handled by its close listener; the event stays in history.
    }
  }
}

function appendEvent(pending: PendingEvent): RecordedEvent {
  sequence += 1;
  const event: RecordedEvent = { seq: sequence, ...pending };
  events.push(event);
  if (events.length > MAX_EVENTS) events.splice(0, events.length - MAX_EVENTS);
  status = { ...status, count: events.length };
  stream(event);
  return event;
}

function start(): void {
  status = { ...status, state: "recording", startedAt: new Date().toISOString() };
  openSocket();
  updateBadge();
}

function stop(): void {
  status = { ...status, state: "idle" };
  closeSocket();
  updateBadge();
}

function clear(): void {
  events = [];
  sequence = 0;
  status = { ...status, count: 0, startedAt: status.state === "recording" ? new Date().toISOString() : null };
  updateBadge();
}

// --- attended execution ------------------------------------------------------
// The executor lives behind the same messaging channel as the recorder: the
// popup or a script sends executor:start, and pause/resume/abort steer the run.
// The service worker holds the run state; the content scripts hold none.

let executorStatus: ExecutorStatus = { ...EMPTY_EXECUTOR_STATUS };
let executorControl: RunControl | null = null;
let executorRunning = false;

function broadcastExecutor(): void {
  void chrome.runtime
    .sendMessage({ type: "executor:state", status: { ...executorStatus } })
    .catch(() => undefined);
}

function setExecutorStatus(update: Partial<ExecutorStatus>): void {
  executorStatus = { ...executorStatus, ...update };
  broadcastExecutor();
}

async function startExecutor(recipe: ExecutorRecipe, row: Record<string, unknown>): Promise<void> {
  if (executorRunning) throw new Error("a run is already in progress");

  // Fail-closed before touching the browser: a recipe the executor cannot run in
  // full is refused, so no debugger is attached for a run that cannot finish.
  const problems = validateRecipe(recipe);
  if (problems.length > 0) throw new Error(`refusing to start: ${problems.join("; ")}`);

  const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
  const tabId = tabs[0]?.id;
  if (tabId === undefined) throw new Error("no active tab to run against");

  const control = new RunControl();
  const frames = new FrameContexts();
  executorControl = control;
  executorRunning = true;
  setExecutorStatus({
    state: "running",
    stepId: null,
    completed: 0,
    total: recipe.steps.length,
    message: "attaching the debugger",
  });

  // Everything past this point runs inside the try, so an attach failure still
  // clears the run flags and never leaves the worker locked as "running".
  let attached = false;
  let stopDetachWatch: (() => void) | null = null;
  try {
    await attachDebugger(tabId);
    attached = true;
    const cdp = chromeDebuggerTransport(tabId);

    // If the browser detaches the debugger (the person cancels the banner, the
    // tab closes, another debugger takes over), abort the run rather than send
    // commands into a dead session.
    stopDetachWatch = onDebuggerDetach(tabId, (reason) => {
      control.abort(`the debugger detached from the tab (${reason})`);
    });

    // Listen for execution contexts before enabling the domains, so the first
    // context-created events for the top frame and its frames are not missed.
    frames.listen(cdp);
    await cdp.send("Runtime.enable");
    await cdp.send("Page.enable");

    const summary = await runRecipe(
      recipe,
      { row: row ?? {} },
      {
        cdp,
        control,
        frames,
        onStatus: (status) => {
          setExecutorStatus({
            state: status.state,
            stepId: status.stepId,
            completed: status.completed,
            total: status.total,
            message: status.message,
          });
        },
      },
    );

    setExecutorStatus({
      stepId: null,
      completed: summary.steps.length,
      message: summary.error ?? "run finished",
    });
  } finally {
    stopDetachWatch?.();
    frames.stop();
    if (attached) await detachDebugger(tabId);
    executorRunning = false;
    executorControl = null;
  }
}

function steerExecutor(command: "pause" | "resume" | "abort"): void {
  if (!executorControl) return;
  if (command === "pause") {
    executorControl.pause();
    setExecutorStatus({ state: "paused", message: "paused" });
  } else if (command === "resume") {
    executorControl.resume();
    setExecutorStatus({ state: "running", message: "resumed" });
  } else {
    executorControl.abort();
    setExecutorStatus({ state: "aborted", message: "aborting" });
  }
}

async function handleExecutor(message: ToWorker): Promise<unknown> {
  switch (message.type) {
    case "executor:start":
      await startExecutor(message.recipe as ExecutorRecipe, message.row ?? {});
      break;
    case "executor:pause":
      steerExecutor("pause");
      break;
    case "executor:resume":
      steerExecutor("resume");
      break;
    case "executor:abort":
      steerExecutor("abort");
      break;
    default:
      break;
  }
  return { ok: true, status: { ...executorStatus } };
}

async function handle(message: ToWorker): Promise<RecorderStatus> {
  switch (message.type) {
    case "record:start":
      start();
      break;
    case "record:stop":
      stop();
      break;
    case "record:clear":
      clear();
      break;
    case "record:set-ws":
      status = { ...status, wsUrl: message.wsUrl };
      await persistSettings();
      if (status.state === "recording") {
        if (status.wsUrl) openSocket();
        else closeSocket();
      }
      break;
    case "record:event":
      if (status.state === "recording") {
        const event = appendEvent(message.event);
        broadcast();
        void chrome.runtime
          .sendMessage({ type: "record:appended", event, status: snapshot() })
          .catch(() => undefined);
      }
      break;
    case "record:get":
      break;
  }
  await persist();
  updateBadge();
  broadcast();
  return snapshot();
}

chrome.runtime.onMessage.addListener((message: unknown, _sender, sendResponse) => {
  if (!isToWorker(message)) return undefined;
  if (message.type.startsWith("executor:")) {
    void handleExecutor(message)
      .then((payload) => sendResponse(payload))
      .catch((error: unknown) => sendResponse({ ok: false, error: String(error) }));
    return true;
  }
  void handle(message)
    .then((current) => sendResponse({ ok: true, status: current, events }))
    .catch((error: unknown) => sendResponse({ ok: false, error: String(error) }));
  return true;
});

chrome.runtime.onInstalled.addListener(() => {
  void load().then(updateBadge);
});

chrome.runtime.onStartup.addListener(() => {
  void load().then(updateBadge);
});

void load().then(updateBadge);
