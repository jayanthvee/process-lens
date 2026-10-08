// CDP event dispatch: turning a step into trusted browser input.
//
// Synthetic script events are ignored by modern apps, so every action here is a
// real CDP input event: keystrokes through `Input.dispatchKeyEvent`, a click
// through `Input.dispatchMouseEvent` at the element's box center, a select
// through a value update in the page, and a navigation through `Page.navigate`.
// The element is always addressed by the marker the resolver stamped on it.
import type { CdpTransport } from "./cdp";
import { evaluate, markerExpression } from "./page";

/** The CDP key codes the executor needs, beyond printable characters. */
const KEY_CODES: Record<string, number> = {
  "\n": 13,
  "\r": 13,
  "\t": 9,
  Backspace: 8,
  Delete: 46,
  Enter: 13,
};

/** Focus the stamped element and select its current text, ready to replace. */
export async function focusAndSelect(cdp: CdpTransport, token: string, contextId?: number): Promise<boolean> {
  const expression = `(() => {
    const el = ${markerExpression(token)};
    if (!el) return false;
    el.focus();
    if (typeof el.select === "function") el.select();
    return true;
  })()`;
  return (await evaluate(cdp, expression, contextId !== undefined ? { contextId } : {})) === true;
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
 * Fill a field: focus it, clear what is there, then type the value one key at a
 * time so the page sees the same events a person would produce.
 */
export async function dispatchFill(
  cdp: CdpTransport,
  token: string,
  value: string,
  options: { clearFirst?: boolean; contextId?: number } = {},
): Promise<void> {
  const clearFirst = options.clearFirst !== false;
  const focused = await focusAndSelect(
    cdp,
    token,
    options.contextId,
  );
  if (!focused) throw new Error("the fill target could not be focused");
  if (clearFirst) await pressBackspace(cdp);
  for (const char of Array.from(value)) await typeCharacter(cdp, char);
}

/**
 * Click an element at its box center with a real mouse press and release. The
 * element is scrolled into view first so the measured center is on screen.
 */
export async function dispatchClick(
  cdp: CdpTransport,
  token: string,
  position: { x: number; y: number },
  contextId?: number,
): Promise<void> {
  const scrollExpression = `(() => {
    const el = ${markerExpression(token)};
    if (!el) return false;
    // jsdom has no layout, so scrollIntoView is absent there; guard it.
    if (typeof el.scrollIntoView === "function") {
      el.scrollIntoView({ block: "center", inline: "center" });
    }
    return true;
  })()`;
  const scrolled = await evaluate(
    cdp,
    scrollExpression,
    contextId !== undefined ? { contextId } : {},
  );
  if (scrolled !== true) throw new Error("the click target could not be scrolled into view");

  const base = { x: position.x, y: position.y, button: "left", clickCount: 1 };
  await cdp.send("Input.dispatchMouseEvent", { type: "mouseMoved", x: position.x, y: position.y });
  await cdp.send("Input.dispatchMouseEvent", { type: "mousePressed", ...base });
  await cdp.send("Input.dispatchMouseEvent", { type: "mouseReleased", ...base });
}

/**
 * Choose an option in a dropdown. A native `<select>` gets its value set and its
 * `input`/`change` events fired; a custom combobox is opened with a click at its
 * center (the caller then resolves and clicks the option).
 */
export async function dispatchSelect(
  cdp: CdpTransport,
  token: string,
  value: string,
  optionMatch: "text" | "value" | "index" = "text",
  contextId?: number,
): Promise<{ matched: boolean; value: string | null }> {
  const expression = `(() => {
    const el = ${markerExpression(token)};
    if (!el) return { matched: false, value: null };
    if (el.tagName && el.tagName.toUpperCase() === "SELECT") {
      const wanted = String(${JSON.stringify(value)});
      const match = ${JSON.stringify(optionMatch)};
      let option = null;
      for (const candidate of Array.from(el.options || [])) {
        const text = (candidate.textContent || "").replace(/\\s+/g, " ").trim();
        if (match === "value" && candidate.value === wanted) option = candidate;
        else if (match === "index" && String(candidate.index) === wanted) option = candidate;
        else if (match === "text" && text === wanted) option = candidate;
        if (option) break;
      }
      if (!option && match === "text") {
        for (const candidate of Array.from(el.options || [])) {
          const text = (candidate.textContent || "").replace(/\\s+/g, " ").trim().toLowerCase();
          if (text.includes(wanted.toLowerCase())) { option = candidate; break; }
        }
      }
      if (!option) return { matched: false, value: null };
      el.value = option.value;
      el.dispatchEvent(new Event("input", { bubbles: true }));
      el.dispatchEvent(new Event("change", { bubbles: true }));
      return { matched: true, value: el.value };
    }
    // A custom combobox: open it; the caller dispatches the option click.
    return { matched: false, value: null, custom: true };
  })()`;
  const result = (await evaluate(
    cdp,
    expression,
    contextId !== undefined ? { contextId } : {},
  )) as { matched?: boolean; value?: string | null } | undefined;
  return { matched: result?.matched === true, value: result?.value ?? null };
}

/** Navigate the tab to a URL. */
export async function dispatchNavigate(cdp: CdpTransport, url: string): Promise<void> {
  await cdp.send("Page.navigate", { url });
}
