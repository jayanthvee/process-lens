// CDP event dispatch: turning a step into trusted browser input.
//
// Synthetic script events are ignored by modern apps, so every action here is a
// real CDP input event: text through `Input.insertText`, a click through
// `Input.dispatchMouseEvent` at the element's box center, a select through a
// value update in the page, and a navigation through `Page.navigate` followed
// by a wait for the document to load. The element is always addressed by the
// marker the resolver stamped on it.
import type { CdpTransport } from "./cdp";
import { measureExpression, type MeasureResult } from "./ladder";
import { evaluate, markerExpression } from "./page";
import { ExecutorError } from "./types";

/** The CDP key codes the executor needs, beyond printable characters. */
const KEY_CODES: Record<string, number> = {
  "\n": 13,
  "\r": 13,
  "\t": 9,
  Backspace: 8,
  Delete: 46,
  Enter: 13,
};

/**
 * The seam a command's owner uses to police its own input.
 *
 * Both hooks are optional so the attended run and the unit tests keep working
 * unchanged. `beforeInput` is called immediately before EVERY trusted input CDP
 * call, so a command that has been withdrawn, or that has since expired, is
 * refused before it can touch the page (R4, B3) — a check made before the guards
 * and the resolve would let a slow prepare type into a dead command.
 * `onFirstInput` fires once, immediately before the first such call, so the
 * caller can acknowledge the commit at the moment input actually reaches the
 * page rather than after the whole action (F2).
 */
export interface InputHooks {
  beforeInput?: () => void;
  onFirstInput?: () => void;
}

/**
 * Fold the hooks into one per-call probe: every call runs `beforeInput` (which
 * may throw), and the first call also runs `onFirstInput`.
 */
function inputGate(hooks: InputHooks | undefined): () => void {
  let announced = false;
  return () => {
    hooks?.beforeInput?.();
    if (!announced) {
      announced = true;
      hooks?.onFirstInput?.();
    }
  };
}

/**
 * Scroll a marked element into view, re-measure it, and hit-test the box
 * center. Throws a `park` error when the box has no area or a click at its
 * center would not land on the target.
 */
export async function measureTarget(
  cdp: CdpTransport,
  token: string,
  contextId?: number,
): Promise<MeasureResult> {
  const expression = `(() => { const measureTargetInPage = ${measureExpression()}; return measureTargetInPage({ token: ${JSON.stringify(
    token,
  )} }); })()`;
  const result = (await evaluate(
    cdp,
    expression,
    contextId !== undefined ? { contextId } : {},
  )) as MeasureResult | undefined;
  if (!result || result.ok !== true) {
    throw new ExecutorError(
      `the target is not clickable: ${result?.reason ?? "measurement failed"}`,
      undefined,
      "park",
    );
  }
  return result;
}

/**
 * Attach the marker and report whether the element is safe to type into. A
 * password field is refused outright: its value must never be sent as keystrokes
 * from a recipe. Runs in the page.
 */
export async function prepareField(
  cdp: CdpTransport,
  token: string,
  contextId?: number,
  clearFirst: boolean = true,
): Promise<{ ok: boolean; reason?: string }> {
  const expression = `(() => {
    const el = ${markerExpression(token)};
    if (!el) return { ok: false, reason: "the fill target is no longer in the document" };
    const tag = (el.tagName || "").toUpperCase();
    if (tag === "INPUT") {
      const type = (el.getAttribute("type") || "text").toLowerCase();
      const autocomplete = (el.getAttribute("autocomplete") || "").toLowerCase();
      if (type === "password" || autocomplete.includes("password")) {
        return { ok: false, reason: "password" };
      }
    }
    el.focus();
    // Selecting is part of *clearing*: with clear_first false the field is
    // focused and the text already in it is left alone.
    const clearFirst = ${clearFirst === false ? "false" : "true"};
    if (clearFirst && typeof el.select === "function") el.select();
    return { ok: true };
  })()`;
  const result = (await evaluate(
    cdp,
    expression,
    contextId !== undefined ? { contextId } : {},
  )) as { ok?: boolean; reason?: string } | undefined;
  return { ok: result?.ok === true, reason: result?.reason };
}

/** Focus the stamped element and select its current text, ready to replace. */
export async function focusAndSelect(cdp: CdpTransport, token: string, contextId?: number): Promise<boolean> {
  const prepared = await prepareField(cdp, token, contextId);
  return prepared.ok;
}

/**
 * Read a stamped element's current value (or text) back out of the page, so a
 * fill can prove the field actually kept what was typed into it.
 */
async function readFieldValue(
  cdp: CdpTransport,
  token: string,
  contextId?: number,
): Promise<string> {
  const expression = `(() => {
    const el = ${markerExpression(token)};
    if (!el) return null;
    if (typeof el.value === "string") return el.value;
    return el.textContent === null || el.textContent === undefined ? "" : String(el.textContent);
  })()`;
  const result = (await evaluate(
    cdp,
    expression,
    contextId !== undefined ? { contextId } : {},
  )) as string | null;
  if (result === null) {
    throw new ExecutorError(
      "the fill target is no longer in the document, so the typed value could not be verified",
      undefined,
      "park",
    );
  }
  return result;
}

/** Press Backspace once, used to clear a selection before typing. */
async function pressBackspace(cdp: CdpTransport, beforeInput: () => void): Promise<void> {
  beforeInput();
  await cdp.send("Input.dispatchKeyEvent", {
    type: "keyDown",
    key: "Backspace",
    windowsVirtualKeyCode: KEY_CODES["Backspace"],
    nativeVirtualKeyCode: KEY_CODES["Backspace"],
  });
  beforeInput();
  await cdp.send("Input.dispatchKeyEvent", {
    type: "keyUp",
    key: "Backspace",
    windowsVirtualKeyCode: KEY_CODES["Backspace"],
    nativeVirtualKeyCode: KEY_CODES["Backspace"],
  });
}

/**
 * Fill a field: check it is safe to type into, clear what is there when asked,
 * then insert the whole value with one `Input.insertText`. One insert keeps
 * `\n` and `\t` characters — a key-event loop would turn them into Enter and
 * Tab — and the value is read back afterwards, so a fill that did not stick is
 * a failure rather than a reported success. A password field is refused, not
 * typed into.
 */
export async function dispatchFill(
  cdp: CdpTransport,
  token: string,
  value: string,
  options: { clearFirst?: boolean; contextId?: number; hooks?: InputHooks } = {},
): Promise<void> {
  const clearFirst = options.clearFirst !== false;
  const gate = inputGate(options.hooks);
  const prepared = await prepareField(cdp, token, options.contextId, clearFirst);
  if (!prepared.ok) {
    if (prepared.reason === "password") {
      throw new ExecutorError(
        "refusing to type into a password field",
        undefined,
        "park",
      );
    }
    throw new ExecutorError(
      `the fill target could not be focused: ${prepared.reason ?? "unknown reason"}`,
      undefined,
      "park",
    );
  }
  if (clearFirst) await pressBackspace(cdp, gate);
  gate();
  await cdp.send("Input.insertText", { text: value });

  const actual = await readFieldValue(cdp, token, options.contextId);
  if (actual !== value) {
    throw new ExecutorError(
      `the fill did not stick: the recipe typed ${JSON.stringify(
        value,
      )} but the field holds ${JSON.stringify(actual)}`,
      undefined,
      "park",
    );
  }
}

/**
 * Click an element at its measured box center with a real mouse press and
 * release. The element is scrolled into view and re-measured first, and the
 * box center is hit-tested, so the click cannot land on something else.
 */
export async function dispatchClick(
  cdp: CdpTransport,
  token: string,
  contextId?: number,
  hooks?: InputHooks,
): Promise<MeasureResult> {
  const box = await measureTarget(cdp, token, contextId);

  // The click is dispatched at the top document's coordinates. An element inside
  // a subframe cannot be clicked from here without landing on whatever the top
  // document shows at those coordinates, so the step is parked instead.
  if (box.topFrame !== true) {
    throw new ExecutorError(
      "refusing to click: the target is inside a frame, so a click at its coordinates would land on the top document instead",
      undefined,
      "park",
    );
  }

  const gate = inputGate(hooks);
  const base = { x: box.x, y: box.y, button: "left", clickCount: 1 };
  gate();
  await cdp.send("Input.dispatchMouseEvent", { type: "mouseMoved", x: box.x, y: box.y });
  gate();
  await cdp.send("Input.dispatchMouseEvent", { type: "mousePressed", ...base });
  gate();
  await cdp.send("Input.dispatchMouseEvent", { type: "mouseReleased", ...base });
  return box;
}

/**
 * Choose an option in a dropdown. A native `<select>` is matched on an exact
 * text (or value, or index); a text match may fall back to a substring only when
 * exactly one option contains it. An ambiguous or absent match is a refusal, not
 * a guess. A custom combobox is opened with a click at its center; the caller
 * then resolves and clicks the option.
 */
export async function dispatchSelect(
  cdp: CdpTransport,
  token: string,
  value: string,
  optionMatch: "text" | "value" | "index" = "text",
  contextId?: number,
  hooks?: InputHooks,
): Promise<{ matched: boolean; value: string | null; reason?: string; custom?: boolean }> {
  // The native-select path writes through `Runtime.evaluate`, and that write IS this step's
  // input, so the gate runs before it: a withdrawn or expired command must not change the field.
  const gate = inputGate(hooks);
  gate();
  const expression = `(() => {
    const el = ${markerExpression(token)};
    if (!el) return { matched: false, reason: "the select target is no longer in the document" };
    if (el.tagName && el.tagName.toUpperCase() === "SELECT") {
      const wanted = String(${JSON.stringify(value)});
      const match = ${JSON.stringify(optionMatch)};
      const options = Array.from(el.options || []);
      const textOf = (o) => (o.textContent || "").replace(/\\s+/g, " ").trim();
      let option = null;
      if (match === "value") {
        option = options.find((o) => o.value === wanted) || null;
      } else if (match === "index") {
        option = options.find((o) => String(o.index) === wanted) || null;
      } else {
        option = options.find((o) => textOf(o) === wanted) || null;
        if (!option) {
          const needle = wanted.toLowerCase();
          const partial = options.filter((o) => textOf(o).toLowerCase().includes(needle));
          if (partial.length === 1) option = partial[0];
          else if (partial.length > 1) {
            return { matched: false, reason: "more than one option contains that text" };
          }
        }
      }
      if (!option) return { matched: false, reason: "no option matched" };
      el.value = option.value;
      el.dispatchEvent(new Event("input", { bubbles: true }));
      el.dispatchEvent(new Event("change", { bubbles: true }));
      return { matched: true, value: el.value };
    }
    // A custom combobox: open it; the caller dispatches the option click.
    return { matched: false, custom: true };
  })()`;
  const result = (await evaluate(
    cdp,
    expression,
    contextId !== undefined ? { contextId } : {},
  )) as { matched?: boolean; value?: string | null; reason?: string; custom?: boolean } | undefined;
  return {
    matched: result?.matched === true,
    value: result?.value ?? null,
    reason: result?.reason,
    custom: result?.custom === true,
  };
}

/** How long to wait for a navigation's load event before giving up. */
export const DEFAULT_NAVIGATE_TIMEOUT_MS = 8000;

/**
 * Watch a navigation's own document load, with the events buffered from before
 * the navigation was even sent.
 *
 * Two races are closed here. The old wait subscribed only after `Page.navigate`
 * replied, so a fast page's `Page.loadEventFired` could arrive with no listener
 * registered and be dropped — the wait then burned its whole timeout on a page
 * that had already loaded. And a late load event from the *previous* document
 * could settle the wait, because the main frame's id never changes across a
 * navigation. Buffering from before the send fixes the first; requiring the
 * `Page.frameNavigated` whose `frame.loaderId` is the one the navigate reply
 * returned, and a load event *after* it, fixes the second.
 */
interface LoadWatch {
  /** Resolve once the document named by `loaderId` has navigated and then loaded. */
  arm(loaderId: string): Promise<boolean>;
  /** Stop watching. Idempotent, and safe after the watch has already settled. */
  cancel(): void;
}

function startLoadWatcher(cdp: CdpTransport, timeoutMs: number): LoadWatch {
  const on = cdp.on;
  const events: Array<{ method: string; params: Record<string, unknown> }> = [];
  let wanted: string | null = null;
  let settled = false;
  let resolve: (ok: boolean) => void = () => undefined;
  let timer: ReturnType<typeof setTimeout> | undefined;
  const done = new Promise<boolean>((accept) => {
    resolve = accept;
  });
  const covered = (): boolean => {
    if (wanted === null) return false;
    const navigated = events.findIndex(
      (event) =>
        event.method === "Page.frameNavigated" &&
        (event.params["frame"] as { loaderId?: string } | undefined)?.loaderId === wanted,
    );
    if (navigated < 0) return false;
    return events.some(
      (event, index) => index > navigated && event.method === "Page.loadEventFired",
    );
  };
  const finish = (ok: boolean): void => {
    if (settled) return;
    settled = true;
    if (timer !== undefined) clearTimeout(timer);
    offNav?.();
    offLoad?.();
    resolve(ok);
  };
  const record = (method: string, params: Record<string, unknown>): void => {
    events.push({ method, params });
    if (covered()) finish(true);
  };
  const offNav = on ? on.call(cdp, "Page.frameNavigated", (p) => record("Page.frameNavigated", p)) : undefined;
  const offLoad = on ? on.call(cdp, "Page.loadEventFired", (p) => record("Page.loadEventFired", p)) : undefined;
  return {
    arm(loaderId: string): Promise<boolean> {
      wanted = loaderId;
      if (covered()) {
        finish(true);
        return done;
      }
      timer = setTimeout(() => finish(false), timeoutMs);
      return done;
    },
    cancel(): void {
      finish(false);
    },
  };
}

/**
 * Navigate the tab to a URL and wait for the document to arrive.
 *
 * `Page.navigate` reports a refusal in its result rather than rejecting, so the
 * result is inspected: a non-empty `errorText` fails the step. The load watch is
 * armed from before the send, so the document's own load event cannot be missed,
 * and the next step never runs against the document being replaced. A reply with
 * no `loaderId` was a same-document navigation, and there is no load to await.
 */
export async function dispatchNavigate(
  cdp: CdpTransport,
  url: string,
  timeoutMs: number = DEFAULT_NAVIGATE_TIMEOUT_MS,
  hooks?: InputHooks,
): Promise<void> {
  // `Page.navigate` sends no `Input.*` event, but it is still the step's action: a withdrawn or
  // expired command must not navigate the person's tab (B3). `onFirstInput` is not fired — nothing
  // has been typed or clicked, and a navigate is never a commit step.
  hooks?.beforeInput?.();
  const watch = typeof cdp.on === "function" ? startLoadWatcher(cdp, timeoutMs) : null;
  try {
    const result = (await cdp.send("Page.navigate", { url })) as
      | { errorText?: string; frameId?: string; loaderId?: string }
      | undefined;
    const errorText = typeof result?.errorText === "string" ? result.errorText.trim() : "";
    if (errorText !== "") {
      throw new ExecutorError(
        `the browser refused to navigate to ${url}: ${errorText}`,
        undefined,
        "park",
      );
    }
    const loaderId = typeof result?.loaderId === "string" ? result.loaderId : "";
    if (loaderId === "" || !watch) return;
    const loaded = await watch.arm(loaderId);
    if (!loaded) {
      throw new ExecutorError(
        `the page did not finish loading ${url} within ${timeoutMs}ms`,
        undefined,
        "park",
      );
    }
  } finally {
    watch?.cancel();
  }
}
