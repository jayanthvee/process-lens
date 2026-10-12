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
  type FromWorker,
  type RecorderStatus,
  type ToWorker,
} from "../shared/messages";
import type { PendingEvent } from "../shared/events";
import type { Condition, RecordedEvent } from "../shared/recipe-types";
import { attachDebugger, chromeDebuggerTransport, detachDebugger, onDebuggerDetach } from "../executor/cdp";
import { RunControl } from "../executor/control";
import { runRecipe, validateRecipe } from "../executor/executor";
import { FrameContexts } from "../executor/frames";
import { waitForCondition } from "../executor/assertions";
import {
  POLICY_BLOCKED_MESSAGE,
  checkChallengeOrLogin,
  checkDestructive,
  checkOrigin,
  classifyAttachError,
} from "../executor/guards";
import { resolverExpression } from "../executor/ladder";
import { performStep, resolveStepTarget } from "../executor/step-runner";
import { ExecutorError, type ExecutorRecipe, type ExecutorStep, type StepReport } from "../executor/types";
import {
  CommandSession,
  type SessionDeps,
  type SessionState,
  type StoredSession,
  type WebSocketLike,
} from "./command-session";

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

// --- the command session (PL-014) --------------------------------------------
// The hub-driven session lives beside the attended run. It shares the debugger,
// the guards, and the step-runner, but it owns its own socket, its own state
// machine, and its own bound tab. A session refuses a command while one is
// executing, and the runner sends one at a time.

const COMMAND_SESSION_KEY = "processlens:command-session";

let commandSession: CommandSession | null = null;
let commandSessionState: SessionState = "idle";
let commandSessionDetail: string | undefined;
let commandBoundTabId: number | null = null;

function broadcastCommandSession(): void {
  const message: FromWorker = {
    type: "executor:session-state",
    state: commandSessionState,
    ...(commandSessionDetail !== undefined ? { detail: commandSessionDetail } : {}),
  };
  void chrome.runtime.sendMessage(message).catch(() => undefined);
}

function commandStorage(): SessionDeps["storage"] {
  return {
    async get(): Promise<StoredSession | null> {
      const store = chrome.storage?.session;
      if (!store) return null;
      const stored = (await store.get(COMMAND_SESSION_KEY)) as Record<string, unknown>;
      return (stored[COMMAND_SESSION_KEY] as StoredSession | undefined) ?? null;
    },
    async set(value: StoredSession | null): Promise<void> {
      const store = chrome.storage?.session;
      if (!store) return;
      if (value === null) await store.remove(COMMAND_SESSION_KEY);
      else await store.set({ [COMMAND_SESSION_KEY]: value });
    },
  };
}

/**
 * Perform one command's step: attach, guard, prepare, then dispatch. Every guard
 * runs before any input and refuses fail-closed; a policy-blocked attach becomes
 * the readable sentence instead of Chrome's raw error.
 */
async function runCommandStep(
  step: unknown,
  opts: { sentInput: () => void; phase: "prepare" | "dispatch"; allowedOrigins?: string[] },
): Promise<StepReport> {
  const executorStep = step as ExecutorStep;
  const tabId = commandBoundTabId;
  if (tabId === null || tabId < 0) throw new Error("no tab is bound to this session");

  let attached = false;
  try {
    await attachDebugger(tabId);
    attached = true;
    const cdp = chromeDebuggerTransport(tabId);
    await cdp.send("Runtime.enable");
    await cdp.send("Page.enable");
    const deps = { cdp };

    const guardContext = { cdp, step: executorStep, allowedOrigins: opts.allowedOrigins };
    const origin = await checkOrigin(guardContext);
    if (!origin.ok) throw new ExecutorError(origin.reason, executorStep.id, "park");
    const challenge = await checkChallengeOrLogin(guardContext);
    if (!challenge.ok) throw new ExecutorError(challenge.reason, executorStep.id, "park");
    const destructive = checkDestructive(guardContext);
    if (!destructive.ok) throw new ExecutorError(destructive.reason, executorStep.id, "park");

    // `navigate` carries a url and no target ladder, so it is never resolved: it is performed.
    const needsTarget = executorStep.action !== "navigate";
    let rung: number | undefined;
    if (needsTarget) {
      const resolved = await resolveStepTarget(executorStep, deps);
      if (!resolved.ok) {
        throw new ExecutorError(
          `the ${executorStep.action} target could not be resolved: ${resolved.error}`,
          executorStep.id,
          "park",
        );
      }
      rung = resolved.rung;
    }

    // R2: a `prepare` stops here. It has proved the target resolves and is hit-testable, and it
    // has sent no input; the runner writes `submitting` only after this returns.
    if (opts.phase === "prepare") {
      return { id: executorStep.id, action: executorStep.action, outcome: "done", rung };
    }

    return await performStep(executorStep, { row: {} }, opts.allowedOrigins, deps, opts.sentInput);
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    if (classifyAttachError(message) === "policy_blocked") throw new Error(POLICY_BLOCKED_MESSAGE);
    throw error;
  } finally {
    if (attached) await detachDebugger(tabId);
  }
}

/** Evaluate one `check` command's condition against the bound tab. */
async function checkCommandCondition(
  condition: unknown,
  timeoutMs: number,
  aborted: () => boolean,
): Promise<boolean> {
  const tabId = commandBoundTabId;
  if (tabId === null || tabId < 0) return false;
  let attached = false;
  try {
    await attachDebugger(tabId);
    attached = true;
    const cdp = chromeDebuggerTransport(tabId);
    await cdp.send("Runtime.enable");
    await cdp.send("Page.enable");
    const outcome = await waitForCondition(
      cdp,
      condition as Condition,
      { resolverSource: resolverExpression() },
      {
        timeoutMs,
        sleep: async (ms: number): Promise<void> => {
          if (aborted()) throw new Error("the command was withdrawn");
          await new Promise<void>((done) => setTimeout(done, ms));
        },
      },
    );
    return outcome.ok;
  } catch {
    return false;
  } finally {
    if (attached) await detachDebugger(tabId);
  }
}

function getCommandSession(): CommandSession {
  if (commandSession) return commandSession;
  const deps: SessionDeps = {
    createSocket: (url: string, protocols: string[]): WebSocketLike =>
      new WebSocket(url, protocols) as unknown as WebSocketLike,
    storage: commandStorage(),
    now: () => Date.now(),
    setInterval: (fn: () => void, ms: number) => {
      const id: ReturnType<typeof setInterval> = setInterval(fn, ms);
      return () => clearInterval(id);
    },
    bindActiveTab: async () => {
      const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
      const tabId = tabs[0]?.id ?? null;
      commandBoundTabId = tabId;
      return tabId;
    },
    runStep: runCommandStep,
    checkCondition: checkCommandCondition,
    onState: (state: SessionState) => {
      commandSessionState = state;
      broadcastCommandSession();
    },
  };
  commandSession = new CommandSession(deps);
  return commandSession;
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
    case "executor:connect":
      await getCommandSession().connect(message.hubUrl);
      break;
    case "executor:disconnect":
      await getCommandSession().disconnect();
      break;
    case "executor:session":
      // A popup opening is a chance to re-attach a session that outlived a
      // worker restart, using the resume token in storage.
      if (commandSessionState === "idle" || commandSessionState === "disconnected") {
        await getCommandSession().restore();
      }
      break;
    default:
      break;
  }
  return {
    ok: true,
    status: { ...executorStatus },
    session: { state: commandSessionState, detail: commandSessionDetail },
  };
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
  // Re-attach a session that outlived the previous worker, using its resume token.
  void getCommandSession().restore();
});

void load().then(updateBadge);
void getCommandSession().restore();
