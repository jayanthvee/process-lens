// CDP event dispatch: turning a step into trusted browser input.
//
// Synthetic script events are ignored by modern apps, so every action here is a
// real CDP input event: text through `Input.insertText`, a click through
// `Input.dispatchMouseEvent` at the element's box center, a select through a
// value update in the page, and a navigation through `Page.navigate` followed
// by a wait for the document to load. The element is always addressed by the
// marker the resolver stamped on it.
import type { CdpTransport } from "./cdp";
import { hitTestExpression, measureExpression, settleExpression, type MeasureResult } from "./ladder";
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
  Tab: 9,
  Escape: 27,
};

/**
 * The keys a `press` step may send, each with what its CDP key event carries: the DOM `key` and
 * `code`, the virtual key code (from `KEY_CODES`), and — for the chorded forms — the `modifiers`
 * bitmask. `text` is what the keydown of a key that types a character carries in a real browser:
 * an Enter keydown arrives with `"\r"`, a Tab with `"\t"`, and a widget that listens for Enter
 * usually reads it, so a pair without it is ignored. The keys that only type characters are not
 * here: they are typing, and a fill already carries the resulting value.
 */
interface PressKeySpec {
  key: string;
  code: string;
  vk: number;
  modifiers?: number;
  text?: string;
}

const PRESS_KEYS: Record<string, PressKeySpec> = {
  Enter: { key: "Enter", code: "Enter", vk: 13, text: "\r" },
  Tab: { key: "Tab", code: "Tab", vk: 9, text: "\t" },
  Escape: { key: "Escape", code: "Escape", vk: 27 },
  Backspace: { key: "Backspace", code: "Backspace", vk: 8 },
  Delete: { key: "Delete", code: "Delete", vk: 46 },
  "Control+Enter": { key: "Enter", code: "Enter", vk: 13, modifiers: 2, text: "\r" },
  "Meta+Enter": { key: "Enter", code: "Enter", vk: 13, modifiers: 4, text: "\r" },
};

/** The keys a `press` step may name, in the schema's order. */
export const PRESS_KEY_NAMES: string[] = Object.keys(PRESS_KEYS);

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
  await sendInput(cdp, "Input.dispatchKeyEvent", {
    type: "keyDown",
    key: "Backspace",
    windowsVirtualKeyCode: KEY_CODES["Backspace"],
    nativeVirtualKeyCode: KEY_CODES["Backspace"],
  });
  beforeInput();
  await sendInput(cdp, "Input.dispatchKeyEvent", {
    type: "keyUp",
    key: "Backspace",
    windowsVirtualKeyCode: KEY_CODES["Backspace"],
    nativeVirtualKeyCode: KEY_CODES["Backspace"],
  });
}

/**
 * Press one key on the element the resolver stamped.
 *
 * A real key event pair — `keyDown` then `keyUp`, never a `char` — is what a widget's own handler
 * sees, and the pair is what makes it act: accept a suggestion, move focus with intent, dismiss a
 * listbox. `token` names the element the key is destined for; CDP delivers a key event to whatever
 * the page has focused, which a fill or the widget itself put there, so it is not part of the
 * event. Both events go through the gate immediately before their own send, so a withdrawn or
 * expired command presses no key at all (PL-017b). A key the executor cannot name correctly is
 * refused before anything is sent, rather than pressed with a guessed code.
 */
export async function dispatchPress(
  cdp: CdpTransport,
  token: string,
  key: string,
  contextId?: number,
  hooks?: InputHooks,
): Promise<void> {
  const spec = PRESS_KEYS[key];
  if (!spec) {
    throw new ExecutorError(
      `the key ${JSON.stringify(key)} is not one the executor can press; it presses ${PRESS_KEY_NAMES.join(
        ", ",
      )}`,
      undefined,
      "park",
    );
  }
  const gate = inputGate(hooks);
  const base: Record<string, unknown> = {
    key: spec.key,
    code: spec.code,
    windowsVirtualKeyCode: spec.vk,
    nativeVirtualKeyCode: spec.vk,
  };
  if (spec.modifiers !== undefined) base["modifiers"] = spec.modifiers;
  gate();
  await sendInput(cdp, "Input.dispatchKeyEvent", {
    type: "keyDown",
    ...base,
    ...(spec.text !== undefined ? { text: spec.text } : {}),
  });
  gate();
  await sendInput(cdp, "Input.dispatchKeyEvent", { type: "keyUp", ...base });
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
  await sendInput(cdp, "Input.insertText", { text: value });

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

/** How long a click may wait for its target to hold still when the caller names no deadline. */
export const DEFAULT_CLICK_SETTLE_MS = 2000;

/** The floor for the landing listener's window, so the settle wait never spends it. */
export const DEFAULT_LANDING_WINDOW_MS = 2000;

/** How long after input the executor waits before a following click may check its target's box. */
export const INPUT_QUIET_MS = 300;

/**
 * When the executor last sent input to the page -- a fill, a press or a select -- or `null` when it
 * has sent none in this session. The extension binds one tab and runs one session at a time, so one
 * clock is the session's. A click is deliberately not one of them (T1's ruling).
 */
let lastInputAt: number | null = null;

/** Note the moment input was sent. The post-input quiet window is measured from here. */
function noteInputSent(): void {
  lastInputAt = Date.now();
}

/** Forget the last input. Only tests need this. */
export function resetInputClock(): void {
  lastInputAt = null;
}

/** A pause. The quiet window is a wait, not a retry: nothing is sent or pressed during it. */
function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/** Send one trusted input event and note its moment for the quiet window. */
async function sendInput(cdp: CdpTransport, method: string, params: Record<string, unknown>): Promise<unknown> {
  const result = await cdp.send(method, params);
  noteInputSent();
  return result;
}

/**
 * Wait for a marked element to stop moving, in the page, before any input. Throws a `park` error —
 * sending nothing — when it never settles.
 */
async function settleTarget(
  cdp: CdpTransport,
  token: string,
  deadlineMs: number,
  contextId?: number,
): Promise<MeasureResult> {
  const expression = `(${settleExpression()})({ token: ${JSON.stringify(token)}, deadline_ms: ${JSON.stringify(
    deadlineMs,
  )} })`;
  const result = (await evaluate(
    cdp,
    expression,
    contextId !== undefined ? { contextId } : {},
  )) as MeasureResult | undefined;
  if (!result || result.ok !== true) {
    throw new ExecutorError(
      result?.reason ?? "the target could not be settled",
      undefined,
      "park",
    );
  }
  return result;
}

/**
 * Re-measure the marked element's box and hit-test its center, in the page. Returns a miss (never
 * throws) so the caller can re-measure within the step's deadline.
 */
async function hitTestTarget(
  cdp: CdpTransport,
  token: string,
  contextId?: number,
): Promise<MeasureResult> {
  const expression = `(${hitTestExpression()})({ token: ${JSON.stringify(token)} })`;
  const result = (await evaluate(
    cdp,
    expression,
    contextId !== undefined ? { contextId } : {},
  )) as MeasureResult | undefined;
  return result ?? { ok: false, x: 0, y: 0, width: 0, height: 0, hit: "", topFrame: false };
}

/** The window property the one-shot landing listener records its verdict on. */
const LANDING_KEY = "__plClickLanding";

/**
 * Install the one-shot capture-phase `mousedown` listener. It records whether the press landed on
 * the marked element (or inside it) and removes itself after one event; a timer removes it too and
 * leaves the result `null`. Returns nothing.
 */
function landingInstallExpression(token: string, deadlineMs: number): string {
  return `(() => {
    const el = ${markerExpression(token)};
    const previous = window.${LANDING_KEY};
    if (previous && typeof previous.cleanup === "function") previous.cleanup();
    const state = { token: ${JSON.stringify(token)}, result: null, cleanup: null };
    const onDown = (event) => {
      // A press inside an open shadow root is retargeted to the host here, so the target's own
      // tree is consulted through the composed path as well: the press landed on the target if
      // the target is anywhere on that path, host or no host.
      const path = typeof event.composedPath === "function" ? event.composedPath() : [];
      state.result =
        el && (event.target === el || el.contains(event.target) || path.includes(el))
          ? "target"
          : "other";
      state.cleanup();
    };
    const timer = setTimeout(() => state.cleanup(), ${JSON.stringify(Math.max(0, deadlineMs))});
    state.cleanup = () => {
      document.removeEventListener("mousedown", onDown, true);
      clearTimeout(timer);
    };
    window.${LANDING_KEY} = state;
    document.addEventListener("mousedown", onDown, true);
  })()`;
}

/** Read the landing verdict: "target", "other", or null when nothing was seen. */
async function readLanding(
  cdp: CdpTransport,
  scope: { contextId?: number },
): Promise<string | null> {
  try {
    const value = await evaluate(
      cdp,
      `(() => { const s = window.${LANDING_KEY}; return s && typeof s.result === "string" ? s.result : null; })()`,
      scope,
    );
    return typeof value === "string" ? value : null;
  } catch {
    // A click that navigated destroys the context the listener lived in: that is "no result".
    return null;
  }
}

/** Remove the landing listener and its timer. Idempotent, and quiet when the document is gone. */
async function removeLanding(cdp: CdpTransport, scope: { contextId?: number }): Promise<void> {
  try {
    await evaluate(
      cdp,
      `(() => { const s = window.${LANDING_KEY}; if (s && typeof s.cleanup === "function") s.cleanup(); delete window.${LANDING_KEY}; })()`,
      scope,
    );
  } catch {
    // The document may have been replaced by the click; there is nothing left to clean.
  }
}

/** Whether a `Page.frameNavigated` arrived while the press was being sent. */
function watchNavigation(cdp: CdpTransport): {
  navigated(): boolean;
  whenNavigated(ms: number): Promise<boolean>;
  stop(): void;
} {
  let seen = false;
  let waiters: Array<(value: boolean) => void> = [];
  const off = cdp.on?.call(cdp, "Page.frameNavigated", () => {
    seen = true;
    const pending = waiters;
    waiters = [];
    for (const resolve of pending) resolve(true);
  });
  return {
    navigated: () => seen,
    // A null landing means the document is gone; the frameNavigated event that explains it can
    // arrive a moment after the read, so wait for it (bounded by the step deadline) instead of
    // racing it. It is a wait, not a retry: nothing is clicked again.
    whenNavigated(ms: number): Promise<boolean> {
      if (seen) return Promise.resolve(true);
      return new Promise<boolean>((resolve) => {
        const timer = setTimeout(() => resolve(false), Math.max(0, ms));
        waiters.push((value) => {
          clearTimeout(timer);
          resolve(value);
        });
      });
    },
    stop: () => off?.(),
  };
}

async function waitOutQuietWindow(remaining: () => number): Promise<void> {
  // A page's reaction to input can be delayed -- the messy fixture opens its listbox 120 ms after
  // the keystroke that triggered it -- so a click whose stability check would start within
  // INPUT_QUIET_MS of the executor's own last input waits the remainder out first. The wait is
  // bounded by the step's own deadline; when the deadline is nearer, the check runs with what is
  // left. It sends nothing and presses nothing.
  if (lastInputAt === null) return;
  const quietMs = INPUT_QUIET_MS - (Date.now() - lastInputAt);
  if (quietMs <= 0) return;
  const waitMs = Math.min(quietMs, Math.max(0, remaining()));
  if (waitMs > 0) await delay(waitMs);
}

/**
 * Click an element with a real mouse press and release, so that the element the guards approved is
 * the element acted on.
 *
 * When the executor sent input moments ago -- a fill, a press or a select -- the click first waits
 * out what is left of a short quiet window (INPUT_QUIET_MS, measured from that input and bounded by
 * the step's own deadline), because a page's reaction to input can be delayed: a listbox that opens
 * 120 ms after the keystroke would otherwise be measured as though the page had already settled.
 * The wait sends nothing and presses nothing.
 *
 * The order of the guards is then: the target is scrolled into view and measured on two consecutive
 * animation frames until it holds still (R1); the one-shot capture `mousedown` listener is armed
 * and the pointer is moved to the settled point; and only THEN is the box re-measured and its
 * center hit-tested (R2), so the hit test is the last check before the press and the window in
 * which a late reflow can invalidate the checked point is the single press send -- a page that
 * reacts to the move itself is seen by the check that follows it. When the final check fails, the
 * click re-settles within the SAME step deadline and checks again: a press that was never sent is
 * never retried, and the deadline is never raised. A target that keeps moving or stays covered
 * until the deadline parks with NO press sent. The armed listener is read after the release (R3,
 * unchanged): a press that landed on something else throws a `park` error. That error is thrown
 * AFTER the first input, so the command session reports it `lost` (F2), never `rejected` -- this
 * function has no notion of a commit step and needs none. When the page navigated and took its
 * listener with it, the result is missing; that counts as landed only when a `Page.frameNavigated`
 * arrived after the press.
 */
export async function dispatchClick(
  cdp: CdpTransport,
  token: string,
  contextId?: number,
  hooks?: InputHooks,
  deadlineMs: number = DEFAULT_CLICK_SETTLE_MS,
): Promise<MeasureResult> {
  const scope = contextId !== undefined ? { contextId } : {};
  const gate = inputGate(hooks);
  const started = Date.now();
  const remaining = (): number => deadlineMs - (Date.now() - started);
  await waitOutQuietWindow(remaining);

  // R1 then R2, in a loop: wait for the box to hold still, arm the landing listener and move the
  // pointer, then hit-test the point LAST -- immediately before the press -- and if the point is
  // covered, re-settle and check again within the same deadline. Reaching here past the deadline
  // parks -- and nothing was pressed before the loop breaks, so the session reports a covered
  // target `not_dispatched`, not `lost`.
  //
  // The armed section is wrapped: a step that throws before its verdict (a withdrawn command at the
  // gate, a settle that failed on a later attempt) removes the listener on its way out, so no
  // listener and no timer can outlive the step. The paths that reach a verdict set `settled` and
  // pay no extra round trip -- the listener removed itself the moment the press landed, or the
  // document the click navigated to took it away.
  const landingWindowMs = Math.max(deadlineMs, DEFAULT_LANDING_WINDOW_MS);
  let settled = false;
  try {
    let movedTo: { x: number; y: number } | null = null;
    let box: MeasureResult;
    for (;;) {
      box = await settleTarget(cdp, token, Math.max(1, remaining()), contextId);
      // The click is dispatched in top-viewport coordinates. A same-origin frame's box arrives
      // already translated (PL-018b); a cross-origin frame's offset can't be read, so a click
      // there would land somewhere unrelated and the step is parked instead.
      if (box.topFrame !== true) {
        throw new ExecutorError(
          "refusing to click: the target is inside a cross-origin frame, so its position on the page is unknown",
          undefined,
          "park",
        );
      }
      // Arm the landing listener and move the pointer BEFORE the final hit test, so that hit test is
      // the last check before the press (T1's ruling): the window in which a late reflow can
      // invalidate the checked point is then the single press send, and a page that reacts to the
      // move itself is seen by the check that follows it. Re-arming per attempt cleans the previous
      // listener up first; a duplicate move to a point the pointer already holds is skipped.
      await evaluate(cdp, landingInstallExpression(token, landingWindowMs), scope);
      if (movedTo === null || Math.abs(movedTo.x - box.x) > 1 || Math.abs(movedTo.y - box.y) > 1) {
        gate();
        await cdp.send("Input.dispatchMouseEvent", { type: "mouseMoved", x: box.x, y: box.y });
        movedTo = { x: box.x, y: box.y };
      }
      const hit = await hitTestTarget(cdp, token, contextId);
      if (hit.ok) {
        box = { ...box, x: hit.x, y: hit.y };
        break;
      }
      if (remaining() <= 0) {
        // The point never came back within the step's own deadline: nothing was pressed, and the
        // `finally` below leaves no listener armed.
        throw new ExecutorError(
          hit.reason ?? "the target is covered by another element",
          undefined,
          "park",
        );
      }
      // Otherwise re-settle within the same deadline and check again. Nothing was pressed, so this
      // is a re-check, never a retry of a sent press.
    }

    const base = { x: box.x, y: box.y, button: "left", clickCount: 1 };
    const navigation = typeof cdp.on === "function" ? watchNavigation(cdp) : null;
    let landed: string | null;
    let navigatedByClick = false;
    try {
      gate();
      await cdp.send("Input.dispatchMouseEvent", { type: "mousePressed", ...base });
      gate();
      await cdp.send("Input.dispatchMouseEvent", { type: "mouseReleased", ...base });
      landed = await readLanding(cdp, scope);
      // Only a MISSING result needs the navigation check: when the press landed somewhere the
      // listener could see, its verdict stands whatever else happens.
      if (landed === null && navigation) {
        navigatedByClick = await navigation.whenNavigated(Math.max(0, remaining()));
      }
    } finally {
      navigation?.stop();
    }
    if (landed === "target") {
      settled = true;
      return box;
    }
    // A click that navigated takes its document, and the listener with it: no result is expected
    // then, and the navigation this very click caused is what stands in for it.
    if (landed === null && navigatedByClick) {
      settled = true;
      return box;
    }
    throw new ExecutorError("the click landed on a different element", undefined, "park");
  } finally {
    if (!settled) await removeLanding(cdp, scope);
  }
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
  // The page write above IS this step's input, so the quiet window is measured from it -- but only
  // when it actually wrote: a custom combobox (opened by the caller's click) and a select that
  // matched nothing changed no value, and marking them would make the NEXT click wait for input
  // that never happened.
  if (result?.matched === true) noteInputSent();
  return {
    matched: result?.matched === true,
    value: result?.value ?? null,
    reason: result?.reason,
    custom: result?.custom === true,
  };
}

/** How long to wait for a navigation's load event before giving up. */
export const DEFAULT_NAVIGATE_TIMEOUT_MS = 8000;

/** How often the arrival check re-reads the tab's URL while it waits. */
export const NAVIGATE_ARRIVAL_POLL_MS = 100;

/**
 * How long a same-document reply is given to show up in the URL before a real navigation is asked
 * for again. A same-document reply means the browser started no document load, so if the URL was
 * going to move it has already moved; the retry is the point of the wait, not the wait itself.
 */
export const SAME_DOCUMENT_GRACE_MS = 1000;

/**
 * Whether the tab is showing the page the step asked for.
 *
 * Origin and path are what identify the page; the query is not compared, because a real page
 * normalizes its own (Odoo rewrites `view_type` and appends `action`), and a trailing slash is not
 * either for the same reason. A hash is compared, but only when the request named one -- a step
 * that pins a fragment is proved, and one that does not is not refused for a fragment the page
 * added itself.
 */
export function isSameDestination(requested: string, actual: string): boolean {
  let want: URL;
  let got: URL;
  try {
    want = new URL(requested);
    got = new URL(actual);
  } catch {
    return false;
  }
  if (want.origin !== got.origin) return false;
  const strip = (path: string): string => path.replace(/\/+$/, "");
  if (strip(want.pathname) !== strip(got.pathname)) return false;
  return want.hash === "" || want.hash === got.hash;
}

/** The top frame's live URL, or null when it cannot be read just now. */
async function readLocation(cdp: CdpTransport): Promise<string | null> {
  try {
    // A bare expression, exactly as the guards read `location.origin`: the value is a string, not
    // a function, so a transport that forwards expressions verbatim answers it.
    const value = await evaluate(cdp, "location.href");
    return typeof value === "string" && value.length > 0 ? value : null;
  } catch {
    // A page mid-navigation tears its execution context down; the next poll reads the new one.
    return null;
  }
}

/** Wait until the tab shows `url`; returns the URL it found, or null on timeout. */
async function awaitArrival(
  cdp: CdpTransport,
  url: string,
  timeoutMs: number,
): Promise<string | null> {
  const deadline = Date.now() + Math.max(0, timeoutMs);
  for (;;) {
    const seen = await readLocation(cdp);
    if (seen !== null && isSameDestination(url, seen)) return seen;
    const left = deadline - Date.now();
    if (left <= 0) return null;
    await delay(Math.min(NAVIGATE_ARRIVAL_POLL_MS, left));
  }
}

/**
 * Accept a `beforeunload` dialog raised while this navigate runs.
 *
 * The transport is bound to exactly one tab, and that tab is the one this run is driving, so a
 * dialog arriving here belongs to this run's own navigate. Accepting it is what a person pressing
 * "Leave" does; without it the renderer stays blocked, every `Runtime.evaluate` fails, and the
 * navigate looks like a page that never moved. No other dialog type is touched: a run must not
 * answer a question the person was never asked.
 */
function acceptBeforeUnload(cdp: CdpTransport): () => void {
  if (typeof cdp.on !== "function") return () => undefined;
  return cdp.on("Page.javascriptDialogOpening", (params) => {
    if (String(params["type"] ?? "") !== "beforeunload") return;
    void cdp
      .send("Page.handleJavaScriptDialog", { accept: true })
      .catch(() => undefined);
  });
}

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

/** What `Page.navigate` answered, kept so a refusal can say what the browser said. */
interface NavigateReply {
  frameId: string;
  loaderId: string;
}

/**
 * Ask the browser to load `url` and wait for its document to arrive.
 *
 * `Page.navigate` reports a refusal in its result rather than rejecting, so the result is
 * inspected: a non-empty `errorText` fails the step. The load watch is armed from before the
 * send, so the document's own load event cannot be missed, and the next step never runs against
 * the document being replaced. A reply with no `loaderId` was a same-document navigation, and
 * there is no load to await -- whether the tab actually arrived is decided by the caller, which
 * reads the URL back rather than trusting this reply.
 */
async function sendNavigate(
  cdp: CdpTransport,
  url: string,
  timeoutMs: number,
): Promise<NavigateReply> {
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
    const frameId = typeof result?.frameId === "string" ? result.frameId : "";
    const loaderId = typeof result?.loaderId === "string" ? result.loaderId : "";
    if (loaderId === "" || !watch) return { frameId, loaderId };
    const loaded = await watch.arm(loaderId);
    if (!loaded) {
      throw new ExecutorError(
        `the page did not finish loading ${url} within ${timeoutMs}ms`,
        undefined,
        "park",
      );
    }
    return { frameId, loaderId };
  } finally {
    watch?.cancel();
  }
}

/**
 * Navigate the tab to a URL, and prove it arrived.
 *
 * A reply from `Page.navigate` is not proof: a same-document reply leaves the previous document in
 * place, and a step that then runs against the wrong page reports success while doing the wrong
 * thing (PL-027: the first navigate of every replayed row reported `ok` while the tab still showed
 * the page the demonstration had left, so the step after it could not resolve its target and the
 * whole batch parked). The tab's own URL is therefore read back, and the step refuses when it does
 * not match -- never reporting a success it cannot show.
 *
 * A reply with no `loaderId` did not replace the document, so one real navigation is asked for
 * again inside the step's own deadline before refusing; the URL decides either way.
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
  const deadline = Date.now() + timeoutMs;
  const stopDialogs = acceptBeforeUnload(cdp);
  try {
    const reply = await sendNavigate(cdp, url, timeoutMs);
    const sameDocument = reply.loaderId === "";
    // A reply with a loaderId waited for the new document's own load event, so the tab's URL has
    // had its chance; the check then gets all the time that is left. A same-document reply replaced
    // nothing, so the tab may still be showing what the last step left: it gets a short grace, and
    // the rest of the step's deadline is kept for one real navigation.
    const left = Math.max(0, deadline - Date.now());
    const share = sameDocument ? Math.min(SAME_DOCUMENT_GRACE_MS, Math.floor(left / 2)) : left;
    if ((await awaitArrival(cdp, url, share)) !== null) return;

    if (sameDocument && deadline - Date.now() > 0) {
      await sendNavigate(cdp, url, deadline - Date.now());
      if ((await awaitArrival(cdp, url, deadline - Date.now())) !== null) return;
    }

    const seen = (await readLocation(cdp)) ?? "a page that could not be read";
    throw new ExecutorError(
      `the navigate to ${url} did not arrive: the tab is showing ${seen} ` +
        `(Page.navigate answered frameId ${JSON.stringify(reply.frameId)}, ` +
        `loaderId ${JSON.stringify(reply.loaderId)}, no error)`,
      undefined,
      "park",
    );
  } finally {
    stopDialogs();
  }
}
