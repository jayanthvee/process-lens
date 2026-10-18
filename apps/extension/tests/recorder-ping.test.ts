// PL-039: the content script answers the service worker's `record:ping`.
//
// Reloading the extension orphans the content script in every open tab: the worker can no longer
// reach the page, and starting a recording there would capture nothing. The ping is how the worker
// asks "are you listening?" before it starts, so the content script must answer `{ ok: true }`
// through `sendResponse`, and keep doing everything it did before.
import { describe, expect, it, vi } from "vitest";

type Listener = (
  message: unknown,
  sender: unknown,
  sendResponse: (response: unknown) => void,
) => unknown;

const listeners: Listener[] = [];

(globalThis as Record<string, unknown>)["chrome"] = {
  runtime: {
    id: "test-extension",
    onMessage: { addListener: (listener: Listener) => void listeners.push(listener) },
    // The bootstrap's one round trip: the worker answers with no pending observation.
    sendMessage: () => Promise.resolve(undefined),
  },
};

const recorder = await import("../src/content/recorder");

describe("the content script answers record:ping (PL-039)", () => {
  it("answers { ok: true } through sendResponse", () => {
    const listener = listeners[0];
    expect(listener).toBeDefined();
    const sendResponse = vi.fn();
    const answer = listener?.({ type: "record:ping" }, {}, sendResponse);
    expect(sendResponse).toHaveBeenCalledWith({ ok: true });
    // The answer went out synchronously: no async channel is held open.
    expect(answer).toBeUndefined();
  });

  it("keeps driving the recording state from record:state, as before", () => {
    const listener = listeners[0];
    const sendResponse = vi.fn();
    listener?.(
      { type: "record:state", status: { state: "recording", count: 0 } },
      {},
      sendResponse,
    );
    expect(sendResponse).not.toHaveBeenCalled();
    expect(
      recorder.shouldResetStepIds(
        { state: "recording", count: 0 },
        { state: "idle", count: 0 },
      ),
    ).toBe(true);
  });
});
