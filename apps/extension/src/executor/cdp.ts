// The CDP transport: one narrow seam between the executor and chrome.debugger.
//
// The executor never touches `chrome.debugger` directly. It sends CDP commands
// and subscribes to CDP events through this interface, so every dispatch and
// every assertion can be unit-tested against a fake transport, and the one real
// implementation is a thin adapter over the browser API.

/** A CDP command channel, plus the event feed the executor listens to. */
export interface CdpTransport {
  /** Send one CDP command and resolve with its result. */
  send(method: string, params?: Record<string, unknown>): Promise<unknown>;
  /** Subscribe to a CDP event; returns an unsubscribe function. */
  on?(event: string, handler: (params: Record<string, unknown>) => void): () => void;
  /** The tab or target this transport is attached to, for diagnostics. */
  readonly targetId?: number;
}

/** A minimal view of the chrome.debugger API we depend on. */
interface DebuggerApi {
  attach(target: { tabId: number }, version: string, callback?: () => void): void;
  detach(target: { tabId: number }, callback?: () => void): void;
  sendCommand(
    target: { tabId: number },
    method: string,
    params?: object,
    callback?: (result?: unknown) => void,
  ): void;
  onEvent: {
    addListener(
      listener: (source: { tabId?: number }, method: string, params?: unknown) => void,
    ): void;
    removeListener(
      listener: (source: { tabId?: number }, method: string, params?: unknown) => void,
    ): void;
  };
  onDetach: {
    addListener(listener: (source: { tabId?: number }, reason: string) => void): void;
    removeListener(listener: (source: { tabId?: number }, reason: string) => void): void;
  };
}

function debuggerApi(): DebuggerApi {
  const api = (globalThis as { chrome?: { debugger?: DebuggerApi } }).chrome?.debugger;
  if (!api) throw new Error("chrome.debugger is unavailable in this context");
  return api;
}

function lastError(): string | null {
  const runtime = (globalThis as { chrome?: { runtime?: { lastError?: { message?: string } } } })
    .chrome?.runtime;
  return runtime?.lastError?.message ?? null;
}

/**
 * Attach the debugger to a tab. Fails if another debugger is already attached
 * (the browser refuses a second attachment), which the executor reports rather
 * than retrying.
 */
export function attachDebugger(tabId: number): Promise<void> {
  return new Promise((resolve, reject) => {
    debuggerApi().attach({ tabId }, "1.3", () => {
      const error = lastError();
      if (error) reject(new Error(`cannot attach the debugger: ${error}`));
      else resolve();
    });
  });
}

/** Detach the debugger. Detaching when not attached is treated as success. */
export function detachDebugger(tabId: number): Promise<void> {
  return new Promise((resolve) => {
    debuggerApi().detach({ tabId }, () => {
      void lastError();
      resolve();
    });
  });
}

/**
 * A transport bound to one attached tab. `on` filters the global debugger event
 * feed down to this tab, so two tabs never see each other's CDP events.
 */
export function chromeDebuggerTransport(tabId: number): CdpTransport {
  const api = debuggerApi();
  return {
    targetId: tabId,
    send(method: string, params: Record<string, unknown> = {}): Promise<unknown> {
      return new Promise((resolve, reject) => {
        api.sendCommand({ tabId }, method, params, (result) => {
          const error = lastError();
          if (error) reject(new Error(`${method} failed: ${error}`));
          else resolve(result);
        });
      });
    },
    on(event: string, handler: (params: Record<string, unknown>) => void): () => void {
      const listener = (source: { tabId?: number }, method: string, params?: unknown): void => {
        if (source?.tabId !== tabId || method !== event) return;
        handler((params as Record<string, unknown>) ?? {});
      };
      api.onEvent.addListener(listener);
      return () => api.onEvent.removeListener(listener);
    },
  };
}
