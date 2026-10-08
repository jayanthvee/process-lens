// CDP event dispatch: turning a step into trusted browser input.
//
// Synthetic script events are ignored by modern apps, so every action here is a
// real CDP input event: keystrokes through `Input.dispatchKeyEvent`, a click
// through `Input.dispatchMouseEvent` at the element's box center, a select
// through a value update in the page, and a navigation through `Page.navigate`.
// The element is always addressed by the marker the resolver stamped on it.
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
    if (typeof el.select === "function") el.select();
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

/** Dispatch one realistic keystroke: a keyDown carrying the text, then a keyUp. */
async function typeCharacter(cdp: CdpTransport, char: string): Promise<void> {
  const code = KEY_CODES[char];
  const key = char === "\n" ? "Enter" : char === "\t" ? "Tab" : char;
  if (code !== undefined) {
    await cdp.send("Input.dispatchKeyEvent", {
      type: "keyDown",
      key,
      windowsVirtualKeyCode: code,
      nativeVirtualKeyCode: code,
    });
    await cdp.send("Input.dispatchKeyEvent", {
      type: "keyUp",
      key,
      windowsVirtualKeyCode: code,
      nativeVirtualKeyCode: code,
    });
    return;
  }
  await cdp.send("Input.dispatchKeyEvent", { type: "keyDown", text: char, key: char });
  await cdp.send("Input.dispatchKeyEvent", { type: "keyUp", key: char });
}

/** Press Backspace once, used to clear a selection before typing. */
async function pressBackspace(cdp: CdpTransport): Promise<void> {
  await cdp.send("Input.dispatchKeyEvent", {
    type: "keyDown",
    key: "Backspace",
    windowsVirtualKeyCode: KEY_CODES["Backspace"],
    nativeVirtualKeyCode: KEY_CODES["Backspace"],
  });
  await cdp.send("Input.dispatchKeyEvent", {
    type: "keyUp",
    key: "Backspace",
    windowsVirtualKeyCode: KEY_CODES["Backspace"],
    nativeVirtualKeyCode: KEY_CODES["Backspace"],
  });
}

/**
 * Fill a field: check it is safe to type into, clear what is there, then type
 * the value one key at a time so the page sees the same events a person would
 * produce. A password field is refused, not typed into.
 */
export async function dispatchFill(
  cdp: CdpTransport,
  token: string,
  value: string,
  options: { clearFirst?: boolean; contextId?: number } = {},
): Promise<void> {
  const prepared = await prepareField(cdp, token, options.contextId);
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
  if (options.clearFirst !== false) await pressBackspace(cdp);
  for (const char of Array.from(value)) await typeCharacter(cdp, char);
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
): Promise<MeasureResult> {
  const box = await measureTarget(cdp, token, contextId);

  const base = { x: box.x, y: box.y, button: "left", clickCount: 1 };
  await cdp.send("Input.dispatchMouseEvent", { type: "mouseMoved", x: box.x, y: box.y });
  await cdp.send("Input.dispatchMouseEvent", { type: "mousePressed", ...base });
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
): Promise<{ matched: boolean; value: string | null; reason?: string; custom?: boolean }> {
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

/** Navigate the tab to a URL. */
export async function dispatchNavigate(cdp: CdpTransport, url: string): Promise<void> {
  await cdp.send("Page.navigate", { url });
}
