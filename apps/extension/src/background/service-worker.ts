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
  type RecorderResponse,
  type RecorderStatus,
  type ToWorker,
} from "../shared/messages";
import type { PendingEvent } from "../shared/events";
import type { Condition, RecordedEvent } from "../shared/recipe-types";
import type { EventKind } from "../shared/command-protocol";
import {
  attachDebugger,
  chromeDebuggerTransport,
  detachDebugger,
  onDebuggerDetach,
  type CdpTransport,
} from "../executor/cdp";
import { RunControl } from "../executor/control";
import { runRecipe, validateRecipe } from "../executor/executor";
import { FrameContexts } from "../executor/frames";
import { waitForCondition } from "../executor/assertions";
import { POLICY_BLOCKED_MESSAGE, checkChallengeOrLogin, classifyAttachError } from "../executor/guards";
import {
  armAbandonGuard as armGuard,
  disarmAbandonGuard as disarmGuard,
} from "../executor/abandon-guard";
import { resolverExpression } from "../executor/ladder";
import { ExecutorError, type ExecutorRecipe, type StepReport } from "../executor/types";
import {
  ensureVisible,
  pageIsHidden,
  windowHiddenMessage,
  type VisibilityDeps,
  type VisibilityReport,
} from "../executor/visibility";
import { dispatchCommandStep, prepareCommandStep } from "./command-step";
import { canRemoveStep, RecorderSession, shouldRecordNavigation } from "./recorder-session";
import { REFRESH_PAGE_WARNING, UNRECORDABLE_PAGE_WARNING } from "../popup/words";
import {
  CommandSession,
  type CheckAnswer,
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
  /** The action a navigation is still owed evidence for, so a worker restart does not lose it. */
  arrival?: { stepId: string; at: number } | null;
}

let status: RecorderStatus = { ...EMPTY_STATUS };
const session = new RecorderSession({ maxEvents: MAX_EVENTS });
/**
 * The tab the recording is following: the one that last sent a recorded event. Navigations are
 * only interesting there, so a busy second tab cannot fill the recording with its movements.
 */
let recordedTabId: number | null = null;
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
    session.restore(persisted.events ?? []);
    session.restoreArrival(persisted.arrival ?? null);
    status = { ...status, count: session.count };
  }
  const settings = stored[SETTINGS_KEY] as { wsUrl?: string } | undefined;
  if (settings?.wsUrl) status.wsUrl = settings.wsUrl;
}

async function persist(): Promise<void> {
  const store = sessionStore();
  if (!store) return;
  const persisted: Persisted = {
    status,
    events: [...session.events],
    arrival: session.snapshotArrival(),
  };
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
  // The session owns the order and the step ids: one counter per action for the whole
  // recording, so a page load cannot restart it and mint an id that already exists.
  const event = session.append(pending);
  status = { ...status, count: session.count };
  stream(event);
  return event;
}

/**
 * Keep the action a document is navigating away from as the target of the page that lands.
 * The document cannot observe what happens after it is gone; the worker can, and this is the
 * note that says an observation is owed.
 */
function noteLeaving(): void {
  if (status.state !== "recording") return;
  session.noteLeaving();
}

/** What a freshly loaded page owes the page before it, or null when it owes nothing. */
function pendingObservationTarget(): string | null {
  if (status.state !== "recording") return null;
  return session.takePendingTarget();
}

/**
 * Attach what the page an action navigated to showed, to the action itself.
 *
 * Only the action the session is waiting on is accepted, and only while it carries no evidence:
 * the first observation wins, and a late or unrelated report changes nothing.
 */
function attachArrivalObservation(stepId: string, observation: RecordedEvent["observation"]): boolean {
  if (status.state !== "recording" || !observation) return false;
  const updated = session.attachObservation(stepId, observation);
  if (!updated) return false;
  void chrome.runtime
    .sendMessage({ type: "record:appended", event: updated, status: snapshot() })
    .catch(() => undefined);
  return true;
}

function start(): void {
  status = { ...status, state: "recording", startedAt: new Date().toISOString() };
  // The tab this recording follows is adopted from its first event, not remembered from the
  // last demonstration, and a fresh demonstration owes the last one no evidence.
  recordedTabId = null;
  session.forgetArrival();
  openSocket();
  updateBadge();
}

function stop(): void {
  status = { ...status, state: "idle" };
  closeSocket();
  updateBadge();
}

function clear(): void {
  session.clear();
  status = {
    ...status,
    count: 0,
    // A clear is a fresh start: whatever the last refused start said no longer applies.
    warning: null,
    startedAt: status.state === "recording" ? new Date().toISOString() : null,
  };
  updateBadge();
}

// --- starting a recording on a page the worker can still see (PL-039) --------

/** How long a tab has to answer the recorder's ping before the page counts as unreachable. */
const PING_TIMEOUT_MS = 1000;

/** What starting a recording needs from the browser, so the guard can be driven in a test. */
export interface StartGuardDeps {
  /** The tab a start would record on: the active tab of the current window. */
  activeTab: () => Promise<{ id?: number; url?: string } | undefined>;
  /** One message to a tab; rejects when the page has no listener. */
  ping: (tabId: number, message: unknown) => Promise<unknown>;
  /** How long to wait for the ping's answer. Defaults to one second. */
  timeoutMs?: number;
  /**
   * The tab that sent the start, when the popup is open as a tab of its own rather than as the
   * toolbar popup. That tab is the extension's own page, so it has no content script to answer.
   */
  senderTabId?: number;
}

/** The guard's verdict: either the recording may start, or the plain words the popup shows. */
export type StartGuard = { started: true } | { started: false; warning: string };

/**
 * Whether a page is one a recording is never allowed on: a browser page or the Web Store.
 * A page with no readable URL is not decided here -- the ping decides it instead.
 */
export function unrecordablePage(url: string | undefined): boolean {
  if (!url) return false;
  if (url.startsWith("chrome://")) return true;
  try {
    const parsed = new URL(url);
    return (
      parsed.host === "chromewebstore.google.com" ||
      (parsed.host === "chrome.google.com" && parsed.pathname.startsWith("/webstore"))
    );
  } catch {
    return false;
  }
}

/**
 * Ask the tab's content script whether it is listening, within a one-second timeout.
 * A reload of the extension orphans the content script of every open tab: no answer,
 * no recording -- there is nothing to record with.
 */
async function pingContentScript(deps: StartGuardDeps, tabId: number): Promise<boolean> {
  return new Promise<boolean>((resolve) => {
    const timer = setTimeout(() => resolve(false), deps.timeoutMs ?? PING_TIMEOUT_MS);
    deps
      .ping(tabId, { type: "record:ping" })
      .then((response) => {
        clearTimeout(timer);
        resolve((response as { ok?: boolean } | undefined)?.ok === true);
      })
      .catch(() => {
        clearTimeout(timer);
        resolve(false);
      });
  });
}

/** Decide whether a recording may start on the active tab, and why not when it may not. */
export async function startRecordingGuard(deps: StartGuardDeps): Promise<StartGuard> {
  const tab = await deps.activeTab();
  if (unrecordablePage(tab?.url)) {
    return { started: false, warning: UNRECORDABLE_PAGE_WARNING };
  }
  if (tab?.id === undefined) return { started: false, warning: REFRESH_PAGE_WARNING };
  // The popup opened as a tab is itself the active tab; it is not the page being recorded, so it
  // decides nothing (the e2e harness drives the popup this way).
  if (deps.senderTabId !== undefined && tab.id === deps.senderTabId) return { started: true };
  if (!(await pingContentScript(deps, tab.id))) {
    return { started: false, warning: REFRESH_PAGE_WARNING };
  }
  return { started: true };
}

/**
 * Start a recording on the active tab, or refuse with a warning the popup shows in place of
 * the steps. A refusal writes nothing else: the state stays idle, and after refreshing the
 * page the person presses Start again (PL-039).
 */
export async function startRecordingOnActiveTab(deps: StartGuardDeps): Promise<RecorderStatus> {
  const guard = await startRecordingGuard(deps);
  if (!guard.started) {
    status = { ...status, warning: guard.warning };
    return snapshot();
  }
  status = { ...status, warning: null };
  start();
  return snapshot();
}

/** The real deps: the active tab of the current window, and one message to it. */
function startGuardDeps(tabId?: number): StartGuardDeps {
  return {
    // The popup names the tab it is looking at. Without one (an older popup), fall back to the
    // last focused window's active tab.
    activeTab: async () =>
      tabId !== undefined
        ? await chrome.tabs.get(tabId).catch(() => undefined)
        : (await chrome.tabs.query({ active: true, currentWindow: true }))[0],
    ping: (tabId, message) => chrome.tabs.sendMessage(tabId, message),
  };
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
 * The debugger for the bound tab, kept for the session's life.
 *
 * Attaching per command was measurably too slow: a command could exceed the runner's five-second
 * ack budget after only a handful of CDP round trips, and the record was then parked as
 * "the extension did not answer". One attach for the whole session matches how the attended
 * executor already works (D-006) and keeps each command to the round trips it actually needs.
 */
let commandCdpToken: CdpTransport | null = null;
let commandCdpTabId: number | null = null;
/** The bound tab's frame contexts, re-learnt on every fresh attach (PL-018b). */
let commandFrames = new FrameContexts();
/** The browser-detach listener for the bound tab, or null when no session holds the debugger. */
let commandDetachWatch: { tabId: number; stop: () => void } | null = null;

function stopCommandDetachWatch(): void {
  const watch = commandDetachWatch;
  commandDetachWatch = null;
  watch?.stop();
}

/**
 * Watch the bound tab for a browser-side debugger detach (PL-014b item 6).
 *
 * Chrome detaches on its own when the person cancels the banner, the tab closes, or another
 * debugger takes over. Without this the cached transport would still look alive and every later
 * command would fail only when its next CDP call rejected — the runner would wait out its whole
 * ack budget instead of learning at once. One listener per bound tab: re-attaching to the same tab
 * keeps the existing one.
 */
function watchCommandDebugger(tabId: number): void {
  if (commandDetachWatch?.tabId === tabId) return;
  stopCommandDetachWatch();
  const stop = onDebuggerDetach(tabId, (reason) => {
    // The listener also fires for OUR OWN detach, so `releaseDebugger` removes it first; reaching
    // here means the browser detached. The cached transport is dead: drop it so the next command
    // re-attaches, and tell the session so the in-flight command stops immediately instead of
    // typing into a tab with no debugger.
    stopCommandDetachWatch();
    commandCdpToken = null;
    commandCdpTabId = null;
    commandSession?.notifyDebuggerDetached(reason);
  });
  commandDetachWatch = { tabId, stop };
}

async function ensureDebugger(tabId: number): Promise<CdpTransport> {
  if (commandCdpToken && commandCdpTabId === tabId) {
    watchCommandDebugger(tabId);
    return commandCdpToken;
  }
  if (commandCdpTabId !== null) await releaseDebugger();
  try {
    await attachDebugger(tabId);
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    // An MV3 service worker is suspended and restarted routinely, which resets the flag above to
    // null while Chrome keeps the tab attached. "Already attached" therefore means the debugger is
    // ours from a previous incarnation, not that someone else holds the tab. Any other attach
    // failure is real and propagates.
    if (!/already attached/i.test(message)) throw error;
  }
  // The debugger is ours from here on, so a browser-side detach is worth learning about at once.
  watchCommandDebugger(tabId);
  const cdp = chromeDebuggerTransport(tabId);
  // PL-018b: listen for execution contexts BEFORE enabling Runtime. `Runtime.enable` reports every
  // existing context, but only on the call that turns reporting on. A debugger that a restarted
  // service worker found already attached has Runtime on already, so it is turned off first, and
  // the enable then reports the top frame and every iframe again.
  commandFrames.stop();
  commandFrames = new FrameContexts();
  commandFrames.listen(cdp);
  await cdp.send("Runtime.disable").catch(() => undefined);
  await cdp.send("Runtime.enable");
  await cdp.send("Page.enable");
  commandCdpTabId = tabId;
  commandCdpToken = cdp;
  return cdp;
}

/** Drop the debugger. The next command attaches again. */
async function releaseDebugger(): Promise<void> {
  // Remove the listener BEFORE detaching: our own detach fires it, and a session going idle must
  // not be told the browser detached it.
  stopCommandDetachWatch();
  const tabId = commandCdpTabId;
  commandCdpTabId = null;
  commandCdpToken = null;
  if (tabId !== null) await detachDebugger(tabId);
}

/**
 * Perform one command's step: guard, resolve, then dispatch.
 *
 * The step body itself lives in `command-step`, dependency-injected, so the prepare phase can be
 * driven in a test with a fake CDP; this keeps only what needs Chrome — the bound tab, the
 * debugger, and the error classification. A policy-blocked attach becomes the readable sentence
 * instead of Chrome's raw error.
 */
async function runCommandStep(
  step: unknown,
  opts: {
    sentInput: () => void;
    phase: "prepare" | "dispatch";
    allowedOrigins?: string[];
    beforeInput: () => void;
  },
): Promise<StepReport> {
  const tabId = commandBoundTabId;
  if (tabId === null || tabId < 0) throw new Error("no tab is bound to this session");

  let attached = false;
  /** The last report `ensureVisible` produced, for the `window_hidden` conversion (PL-045). */
  let visibleReport: VisibilityReport | undefined;
  try {
    const cdp = await ensureDebugger(tabId);
    attached = true;
    // Command mode: the runner states the deadline for every step it sends, and the session's own
    // probe polices each input at the last moment (R4, F1, B3).
    const body = {
      cdp,
      allowedOrigins: opts.allowedOrigins,
      beforeInput: opts.beforeInput,
      onFirstInput: opts.sentInput,
      frames: commandFrames,
      ensureVisible: async () => {
        const report = await ensureVisible(workTabVisibility(cdp, tabId));
        visibleReport = report;
        return report;
      },
    };
    return opts.phase === "prepare"
      ? await prepareCommandStep(step, body)
      : await dispatchCommandStep(step, body);
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    if (classifyAttachError(message) === "policy_blocked") throw new Error(POLICY_BLOCKED_MESSAGE);
    // PL-045: the full report goes to the worker console; a person reading the run only sees the
    // `window_hidden` detail, which carries the same one-line summary.
    if (visibleReport && error instanceof ExecutorError && error.event === "window_hidden") {
      console.warn("[processlens] the work window stayed hidden", visibleReport);
    }
    // PL-040: the window was hidden while the step ran (the target never settled, the form never
    // drew). The step's own message hides the cause, so it is reported as the run-level event.
    if (error instanceof ExecutorError && !error.event && commandCdpToken) {
      if (await pageIsHidden(commandCdpToken)) {
        if (visibleReport) {
          console.warn("[processlens] the work window stayed hidden", visibleReport);
        }
        throw new ExecutorError(
          windowHiddenMessage(visibleReport),
          error.stepId,
          "park",
          "window_hidden",
        );
      }
    }
    throw error;
  } finally {
    // The debugger stays attached between commands; a failure that may have torn it down drops the
    // cached transport so the next command re-attaches.
    if (!attached) commandCdpToken = null;
  }
}

/** PL-040: how the session checks and raises the work tab. PL-045: and what Chrome says about it. */
function workTabVisibility(cdp: CdpTransport, tabId: number): VisibilityDeps {
  return {
    isHidden: () => pageIsHidden(cdp),
    windowFacts: async () => {
      try {
        const tab = await chrome.tabs.get(tabId);
        const win = await chrome.windows.get(tab.windowId);
        return {
          state: win.state ?? "unknown",
          focused: win.focused === true,
          tabActive: tab.active === true,
        };
      } catch {
        return undefined;
      }
    },
    bringForward: async () => {
      const tab = await chrome.tabs.update(tabId, { active: true });
      if (tab?.windowId === undefined) return;
      const win = await chrome.windows.get(tab.windowId);
      await chrome.windows.update(
        tab.windowId,
        win.state === "minimized" ? { state: "normal", focused: true } : { focused: true },
      );
    },
    sleep: (ms: number) => new Promise((resolve) => setTimeout(resolve, ms)),
  };
}

/**
 * Probe the bound tab for a challenge or a login wall (C3).
 *
 * A `check` carries no step, so the guard gets a minimal one: with no targets declared, any visible
 * password field is a login this check is not performing — exactly the wall this is looking for —
 * and the step id is only a label (the guard's reasons name the marker, not the step). A probe
 * that itself throws changes nothing: the poll's answer already stands and a wall is never guessed
 * from a failed evaluation, so the caller keeps the "no" it had.
 */
async function probeWallForCheck(
  cdp: CdpTransport,
): Promise<{ event: EventKind; detail: string } | null> {
  try {
    const guard = await checkChallengeOrLogin({
      cdp,
      step: { id: "check", action: "check", targets: [] },
      allowedOrigins: undefined,
    });
    if (guard.ok || !guard.event) return null;
    return { event: guard.event, detail: guard.reason };
  } catch {
    return null;
  }
}

/**
 * Evaluate one `check` command's condition against the bound tab.
 *
 * B1: a check that could not be evaluated is a *refusal*, not a "no". Returning `false` for a
 * missing tab, a throwing evaluate, or a malformed condition would reach the runner as a real
 * answered "no" — a failed duplicate search would then take the `else` branch and create the
 * lead a second time, and a failed fresh-confirmation probe would report "absent".
 *
 * C2: a poll that never evaluated cleanly is the same kind of not-answered. `waitForCondition`
 * flags it `unanswered`, and it becomes a refusal here rather than `passed: false`.
 *
 * C3: a clean `passed: false` also probes the page for a wall. A challenge or a login the recipe
 * does not describe is reported as the answer's event, so the session can send it before the
 * condition reply (R6) and the run pauses with the wall's reason. The answer itself is unchanged.
 */
async function checkCommandCondition(
  condition: unknown,
  timeoutMs: number,
  aborted: () => boolean,
): Promise<CheckAnswer> {
  const tabId = commandBoundTabId;
  if (tabId === null || tabId < 0) {
    return { ok: false, reason: "no tab is bound to this session" };
  }
  let attached = false;
  try {
    const cdp = await ensureDebugger(tabId);
    attached = true;
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
    // A completed poll is the only thing that answers. A poll ended by the withdrawal is not an
    // answer either, so the caller refuses rather than reporting a "no".
    if (outcome.aborted === true) {
      return { ok: false, reason: outcome.reason ?? "the command was withdrawn" };
    }
    // C2: the poll timed out without ever evaluating the condition cleanly (the last attempt threw
    // a transient navigation error). Nobody saw the page, so there is no "no" to report.
    if (outcome.unanswered === true) {
      return { ok: false, reason: outcome.reason ?? "the condition could not be evaluated" };
    }
    if (outcome.ok) return { ok: true, passed: true };
    // C3: the condition was evaluated and did not hold. A wall under the answer explains it, so ask
    // the page before reporting the "no". The reply still carries `passed: false` either way.
    const wall = await probeWallForCheck(cdp);
    return wall
      ? { ok: true, passed: false, event: wall.event, eventDetail: wall.detail }
      : { ok: true, passed: false };
  } catch (error) {
    // The attach failed, the evaluate threw, or the condition is one the executor does not
    // understand: none of those is a page that said "no".
    return { ok: false, reason: error instanceof Error ? error.message : String(error) };
  } finally {
    if (!attached) commandCdpToken = null;
  }
}

/**
 * PL-026: tell the API which version this extension is running.
 *
 * The service worker calls this every time the command session reaches `ready` -- after the first
 * pair, after a reconnect, after a restore that outlived a worker restart -- so the API can tell a
 * person whose extension is older than the floor their admin set. The version is the manifest's
 * own, read here and nowhere else.
 *
 * The hub URL comes from the stored session, which is the same value the socket was opened against
 * and the one a restored worker still has. A report is a courtesy: a session with no stored URL, a
 * navigator that cannot fetch, or a refused request leaves the session exactly as it was, because
 * the run must never depend on this call.
 */
async function reportExtensionVersion(): Promise<void> {
  try {
    const version = chrome.runtime?.getManifest?.().version;
    if (typeof version !== "string" || version === "") return;
    const hubUrl = (await commandStorage().get())?.hubUrl;
    if (typeof hubUrl !== "string" || hubUrl === "") return;
    const fetchFn = (globalThis as { fetch?: typeof fetch }).fetch;
    if (typeof fetchFn !== "function") return;
    await fetchFn(`${hubUrl.replace(/\/+$/, "")}/api/v1/executor/version`, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "X-ProcessLens-Client": "extension",
      },
      body: JSON.stringify({ version }),
    });
  } catch {
    // A report that did not land is not a run that cannot continue.
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
    // PL-037: the session works in a window of its own. Binding the ACTIVE tab took over whatever
    // the person was looking at -- the dashboard, or a new-tab page, which is a chrome:// URL the
    // debugger may not attach to. A background TAB is no better: Chrome stops drawing hidden tabs,
    // and an app like Odoo never finishes rendering there. A separate visible window keeps drawing
    // while the person works in their own tabs, and lets them watch the run.
    // Chrome refuses bounds it thinks are off screen, so the window takes the right half of the
    // person's own window, then no bounds at all. It never falls back to a tab the person is using.
    bindActiveTab: async () => {
      const attempts: chrome.windows.CreateData[] = [];
      try {
        const own = await chrome.windows.getLastFocused();
        if (own?.width && own.height && own.left !== undefined && own.top !== undefined) {
          const width = Math.max(500, Math.floor(own.width / 2));
          attempts.push({
            url: "about:blank",
            focused: true,
            type: "normal",
            left: own.left + own.width - width,
            top: own.top,
            width,
            height: own.height,
          });
        }
      } catch {
        // no window to measure: the plain window below
      }
      attempts.push({ url: "about:blank", focused: true, type: "normal" });
      let tabId: number | null = null;
      for (const data of attempts) {
        try {
          const win = await chrome.windows.create(data);
          tabId = win?.tabs?.[0]?.id ?? null;
          if (tabId !== null) break;
        } catch (error) {
          console.warn("ProcessLens: could not open the work window", error);
        }
      }
      if (tabId === null) {
        const tab = await chrome.tabs.create({ url: "about:blank", active: true });
        tabId = tab?.id ?? null;
      }
      commandBoundTabId = tabId;
      return tabId;
    },
    // B2: after a worker restart, adopt the STORED bound tab — never the active one, which at
    // restore time is whatever the person is looking at. A tab that no longer exists is reported
    // so the session can announce `tab_closed`.
    restoreBoundTab: async (tabId: number) => {
      try {
        await chrome.tabs.get(tabId);
      } catch {
        return false;
      }
      commandBoundTabId = tabId;
      return true;
    },
    runStep: runCommandStep,
    checkCondition: checkCommandCondition,
    // PL-027: the abandon guard runs on the bound tab's debugger, the same one every command uses.
    armAbandonGuard: async () => {
      const tabId = commandBoundTabId;
      if (tabId === null || tabId < 0) throw new Error("no tab is bound to this session");
      await armGuard(await ensureDebugger(tabId));
    },
    disarmAbandonGuard: async () => {
      const tabId = commandBoundTabId;
      if (tabId === null || tabId < 0) return [];
      return disarmGuard(await ensureDebugger(tabId));
    },
    // PL-026: the one call the extension makes for the version check. The session decides when
    // (every time it reaches `ready`); this decides where and as whom.
    reportVersion: () => void reportExtensionVersion(),
    onState: (state: SessionState) => {
      commandSessionState = state;
      broadcastCommandSession();
      // A session that is no longer attached must not keep the debugger on the person's tab.
      if (state === "disconnected" || state === "idle") void releaseDebugger();
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

/**
 * A top-level navigation nobody's click caused is part of the recording (requirement 6).
 *
 * `chrome.webNavigation` is the honest source: it reports the transition, so a typed URL, a
 * bookmark, a reload and a back/forward are facts about how the workflow was done, while a
 * link's own navigation is the click already in the recording and a redirect is nobody's step.
 * Only the tab the recording is following is watched.
 */
chrome.webNavigation?.onCommitted?.addListener((details) => {
  const recordable = shouldRecordNavigation(details, {
    recording: status.state === "recording",
    // The recording follows one tab, adopted from its first event. Until there is one, no tab
    // is followed: a navigation elsewhere is not part of anything being demonstrated.
    recordedTab: recordedTabId !== null && details.tabId === recordedTabId,
  });
  if (!recordable) return;

  const event = session.appendNavigate(details.url);
  status = { ...status, count: session.count };
  updateBadge();
  void persist().then(() => broadcast());
  void chrome.runtime
    .sendMessage({ type: "record:appended", event, status: snapshot() })
    .catch(() => undefined);
});

/**
 * Whether this sender is the page an action navigated to: the top frame of the recorded tab.
 *
 * Every frame of every page runs the content script, so answering any asker would let an
 * advertisement, a chat widget or a later, unrelated load hand the worker the confirmation that
 * belongs to the save this recording is waiting on. Only the document the person navigated to
 * is the one that saw it.
 */
function senderIsTheRecordedPage(sender?: chrome.runtime.MessageSender): boolean {
  if (!sender || sender.frameId !== 0) return false;
  const tabId = sender.tab?.id;
  return recordedTabId !== null && tabId === recordedTabId;
}

async function handle(message: ToWorker, sender?: chrome.runtime.MessageSender): Promise<RecorderResponse> {
  const sendingTabId = sender?.tab?.id;
  switch (message.type) {
    case "record:start":
      // PL-039: the ping first. A tab whose content script the extension reload orphaned
      // records nothing, so the start is refused with the refresh warning instead.
      await startRecordingOnActiveTab({
        ...startGuardDeps(message.tabId),
        senderTabId: sendingTabId,
      });
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
        if (sendingTabId !== undefined) recordedTabId = sendingTabId;
        const event = appendEvent(message.event);
        broadcast();
        void chrome.runtime
          .sendMessage({ type: "record:appended", event, status: snapshot() })
          .catch(() => undefined);
      }
      break;
    case "record:remove":
      // Only a stopped recording offers its steps for deletion: while it runs the list is still
      // being built, so the request is refused silently. The response below carries the recording
      // as it now stands, which is what the popup redraws itself from.
      if (canRemoveStep(status.state)) {
        const removed = session.remove(message.seq);
        if (removed) status = { ...status, count: session.count };
      }
      break;
    case "record:leaving":
      noteLeaving();
      break;
    case "record:observation":
      attachArrivalObservation(message.stepId, message.observation);
      break;
    case "record:pending-observation":
    case "record:get":
      break;
  }
  const observationTarget =
    message.type === "record:pending-observation" && senderIsTheRecordedPage(sender)
      ? pendingObservationTarget()
      : null;
  await persist();
  updateBadge();
  broadcast();
  return {
    ok: true,
    status: snapshot(),
    events: [...session.events],
    observationTarget,
  };
}

chrome.runtime.onMessage.addListener((message: unknown, sender, sendResponse) => {
  if (!isToWorker(message)) return undefined;
  if (message.type.startsWith("executor:")) {
    void handleExecutor(message)
      .then((payload) => sendResponse(payload))
      .catch((error: unknown) => sendResponse({ ok: false, error: String(error) }));
    return true;
  }
  void handle(message, sender)
    .then((current) => sendResponse(current))
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
