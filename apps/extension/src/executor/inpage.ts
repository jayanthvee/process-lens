// PL-108 experiment: dispatch a step's input from *inside* the page, with ordinary DOM events
// (`isTrusted === false`), so a run needs no `chrome.debugger` for that step.
//
// This mirrors the callable interface of `executor/dispatch.ts` — click, fill, press, select and
// upload — but every page action is one `Runtime.evaluate` of a self-contained expression, not a
// sequence of trusted CDP input commands. The expression has no imports and no globals beyond the
// browser's own, exactly like the page-side resolver.
//
// Value setting is React-safe: the value is written through the element's own native setter, so
// React's value tracker sees the change and the framework's `onChange` fires. A contenteditable body
// takes `execCommand("insertText")` with a textContent fallback.
//
// Most sites accept these events. The one step a site ignores falls back to the debugger, which is
// the caller's job (see the per-step fallback in `executor.ts`).
import type { CdpTransport } from "./cdp";
import { DEFAULT_CLICK_SETTLE_MS } from "./dispatch";
import { type MeasureResult } from "./ladder";
import { escapeToken, evaluate } from "./page";
import { ExecutorError } from "./types";

/** One file a step may upload, carried as base64 so it survives the JSON hop into the page. */
export interface InPageFile {
  name: string;
  type: string;
  /** Base64 (no data-URL prefix) of the file's bytes. */
  data: string;
}

/** The answer of the in-page select: a native select, or a custom combobox to open by clicking. */
export interface InPageSelectAnswer {
  matched: boolean;
  value: string | null;
  reason?: string;
  custom?: boolean;
}

/** The page-side element finder, the same light-tree-then-open-shadow-root walk the resolver uses. */
function elementFinder(selectorLiteral: string): string {
  return `
    const selector = '${selectorLiteral}';
    const deep = (root) => {
      const light = document.querySelector(selector);
      if (light) return light;
      const roots = [];
      const collect = (node) => {
        for (const el of Array.from(node.querySelectorAll("*"))) {
          if (el.shadowRoot) { roots.push(el.shadowRoot); collect(el.shadowRoot); }
        }
      };
      collect(document);
      for (const shadow of roots) {
        const found = shadow.querySelector(selector);
        if (found) return found;
      }
      return null;
    };
    const el = deep(document);`;
}

function selectorFor(token: string): string {
  return `[data-pl-target="${escapeToken(token)}"]`;
}

/** The click expression: scroll, hit-test, then a full pointer+mouse+click sequence at the centre. */
function clickExpression(token: string): string {
  return `(() => {${elementFinder(selectorFor(token))}
    if (!el) return { ok: false, x: 0, y: 0, width: 0, height: 0, hit: "", topFrame: true, reason: "the marked element is no longer in the document" };
    if (typeof el.scrollIntoView === "function") { try { el.scrollIntoView({ block: "center", inline: "center" }); } catch (e) {} }
    const rect = el.getBoundingClientRect();
    if (!(rect.width > 0) || !(rect.height > 0)) {
      return { ok: false, x: 0, y: 0, width: rect.width, height: rect.height, hit: "", topFrame: true, reason: "the element has no clickable area (" + rect.width + "x" + rect.height + ")" };
    }
    const x = rect.left + rect.width / 2;
    const y = rect.top + rect.height / 2;
    let target = el;
    if (typeof document.elementFromPoint === "function") {
      const hit = document.elementFromPoint(x, y);
      if (!hit) return { ok: false, x: x, y: y, width: rect.width, height: rect.height, hit: "", topFrame: true, reason: "the hit test found no element at the box center" };
      if (hit !== el && !el.contains(hit)) {
        return { ok: false, x: x, y: y, width: rect.width, height: rect.height, hit: (hit.tagName || "").toLowerCase(), topFrame: true, reason: "the box center is covered by <" + (hit.tagName || "").toLowerCase() + ">" };
      }
      target = hit;
    }
    const fire = (type, init) => {
      const Ctor = (type.indexOf("pointer") === 0 && typeof PointerEvent === "function") ? PointerEvent : MouseEvent;
      target.dispatchEvent(new Ctor(type, Object.assign({ bubbles: true, cancelable: true, composed: true, clientX: x, clientY: y, button: 0 }, init)));
    };
    fire("pointerdown", { buttons: 1, pointerId: 1, pointerType: "mouse", isPrimary: true });
    fire("mousedown", { buttons: 1 });
    if (typeof el.focus === "function") { try { el.focus(); } catch (e) {} }
    fire("pointerup", { buttons: 0, pointerId: 1, pointerType: "mouse", isPrimary: true });
    fire("mouseup", { buttons: 0 });
    fire("click", { buttons: 0, detail: 1 });
    return { ok: true, x: x, y: y, width: rect.width, height: rect.height, hit: (target.tagName || "").toLowerCase(), topFrame: true };
  })()`;
}

/** The fill expression: focus, clear, native-set the value, then input/change. */
function fillExpression(token: string, value: string, clearFirst: boolean): string {
  return `(() => {${elementFinder(selectorFor(token))}
    if (!el) return { ok: false, reason: "the fill target is no longer in the document" };
    const tag = (el.tagName || "").toUpperCase();
    const type = (el.getAttribute("type") || "text").toLowerCase();
    const autocomplete = (el.getAttribute("autocomplete") || "").toLowerCase();
    if (tag === "INPUT" && (type === "password" || autocomplete.indexOf("password") !== -1)) {
      return { ok: false, reason: "password" };
    }
    const wanted = ${JSON.stringify(value)};
    if (typeof el.focus === "function") { try { el.focus(); } catch (e) {} }
    if (tag === "INPUT" || tag === "TEXTAREA") {
      const proto = tag === "INPUT" ? HTMLInputElement.prototype : HTMLTextAreaElement.prototype;
      const descriptor = Object.getOwnPropertyDescriptor(proto, "value");
      const setter = descriptor && descriptor.set;
      if (${clearFirst ? "true" : "false"} && typeof el.select === "function") { try { el.select(); } catch (e) {} }
      if (${clearFirst ? "true" : "false"}) {
        if (setter) setter.call(el, ""); else el.value = "";
        el.dispatchEvent(new Event("input", { bubbles: true }));
      }
      if (setter) setter.call(el, wanted); else el.value = wanted;
      el.dispatchEvent(new Event("input", { bubbles: true }));
      el.dispatchEvent(new Event("change", { bubbles: true }));
      return { ok: String(el.value) === wanted || (tag === "INPUT" && String(el.value) === wanted.replace(/\\n/g, "")), value: String(el.value) };
    }
    if (el.getAttribute("contenteditable") !== null) {
      if (${clearFirst ? "true" : "false"}) el.textContent = "";
      let inserted = false;
      try { inserted = typeof document.execCommand === "function" ? document.execCommand("insertText", false, wanted) : false; } catch (e) { inserted = false; }
      if (!inserted) el.textContent = (${clearFirst ? "true" : "false"} ? "" : (el.textContent || "")) + wanted;
      el.dispatchEvent(new Event("input", { bubbles: true }));
      return { ok: (el.textContent || "").indexOf(wanted) !== -1, value: String(el.textContent) };
    }
    return { ok: false, reason: "the fill target is not a text field" };
  })()`;
}

/** The press expression: keydown/keypress/keyup, and `requestSubmit()` for Enter in a field. */
function pressExpression(token: string, key: string): string {
  return `(() => {${elementFinder(selectorFor(token))}
    if (!el) return { ok: false, reason: "the press target is no longer in the document" };
    const wanted = ${JSON.stringify(key)};
    const parts = wanted.split("+");
    const finalKey = parts[parts.length - 1];
    const codes = { Enter: 13, Tab: 9, Escape: 27, Backspace: 8, Delete: 46 };
    const keyCode = codes[finalKey] || 0;
    if (typeof el.focus === "function") { try { el.focus(); } catch (e) {} }
    const fire = (type) => el.dispatchEvent(new KeyboardEvent(type, { key: finalKey, code: finalKey, keyCode: keyCode, which: keyCode, bubbles: true, cancelable: true, composed: true }));
    fire("keydown");
    if (finalKey.length === 1) fire("keypress");
    fire("keyup");
    if (finalKey === "Enter") {
      const form = (el.form) || (typeof el.closest === "function" ? el.closest("form") : null);
      if (form && typeof form.requestSubmit === "function") { try { form.requestSubmit(); } catch (e) {} }
    }
    return { ok: true };
  })()`;
}

/** The select expression: a native select is set React-safe; a custom combobox is left to a click. */
function selectExpression(token: string, value: string, match: string): string {
  return `(() => {${elementFinder(selectorFor(token))}
    if (!el) return { matched: false, reason: "the select target is no longer in the document" };
    if ((el.tagName || "").toUpperCase() !== "SELECT") return { matched: false, custom: true, value: null };
    const wanted = ${JSON.stringify(value)};
    const mode = ${JSON.stringify(match)};
    const options = Array.from(el.options || []);
    const textOf = (o) => (o.textContent || "").replace(/\\s+/g, " ").trim();
    let option = null;
    if (mode === "value") option = options.find((o) => o.value === wanted) || null;
    else if (mode === "index") option = options.find((o) => String(o.index) === wanted) || null;
    else {
      option = options.find((o) => textOf(o) === wanted) || null;
      if (!option) {
        const partial = options.filter((o) => textOf(o).toLowerCase().indexOf(wanted.toLowerCase()) !== -1);
        if (partial.length === 1) option = partial[0];
        else if (partial.length > 1) return { matched: false, value: null, reason: "more than one option contains that text" };
      }
    }
    if (!option) return { matched: false, value: null, reason: "no option matched" };
    const descriptor = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value");
    if (descriptor && descriptor.set) descriptor.set.call(el, option.value); else el.value = option.value;
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
    return { matched: true, value: el.value };
  })()`;
}

/** The upload expression: build a DataTransfer from base64 files and hand it to the input. */
function uploadExpression(token: string, files: InPageFile[]): string {
  return `(() => {${elementFinder(selectorFor(token))}
    if (!el) return { ok: false, reason: "the upload target is no longer in the document" };
    if (typeof DataTransfer !== "function" || typeof File !== "function") {
      return { ok: false, reason: "this page cannot build a file list without the debugger" };
    }
    const decode = (data) => {
      const binary = atob(data);
      const bytes = new Uint8Array(binary.length);
      for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
      return bytes;
    };
    const transfer = new DataTransfer();
    for (const file of ${JSON.stringify(files)}) {
      transfer.items.add(new File([decode(file.data)], file.name, { type: file.type || "" }));
    }
    el.files = transfer.files;
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
    return { ok: true, count: transfer.files.length };
  })()`;
}

/** Run one page expression and read its answer, surfacing a page exception as a real error. */
async function runExpression(
  cdp: CdpTransport,
  expression: string,
  contextId: number | undefined,
): Promise<unknown> {
  return evaluate(cdp, expression, contextId !== undefined ? { contextId } : {});
}

/**
 * Click from inside the page. Same contract as `dispatchClick`: a settled, hit-tested click, and a
 * `park` error (with `dispatch.ts`'s vocabulary) when the target is gone or covered.
 */
export async function inPageClick(
  cdp: CdpTransport,
  token: string,
  contextId?: number,
  _deadlineMs: number = DEFAULT_CLICK_SETTLE_MS,
): Promise<MeasureResult> {
  const answer = (await runExpression(cdp, clickExpression(token), contextId)) as MeasureResult | undefined;
  if (!answer || answer.ok !== true) {
    throw new ExecutorError(
      answer?.reason ?? "the in-page click could not be dispatched",
      undefined,
      "park",
    );
  }
  return answer;
}

/** Fill a field from inside the page, React-safe. Throws a `park` error when it cannot. */
export async function inPageFill(
  cdp: CdpTransport,
  token: string,
  value: string,
  options: { clearFirst?: boolean; contextId?: number } = {},
): Promise<void> {
  const clearFirst = options.clearFirst !== false;
  const answer = (await runExpression(
    cdp,
    fillExpression(token, value, clearFirst),
    options.contextId,
  )) as { ok?: boolean; reason?: string; value?: string } | undefined;
  if (!answer || answer.ok !== true) {
    if (answer?.reason === "password") {
      throw new ExecutorError("refusing to type into a password field", undefined, "park");
    }
    throw new ExecutorError(
      `the in-page fill did not stick: ${answer?.reason ?? "the field did not take the value"}`,
      undefined,
      "park",
    );
  }
}

/** Press a key from inside the page. `Enter` also asks the field's form to submit. */
export async function inPagePress(
  cdp: CdpTransport,
  token: string,
  key: string,
  contextId?: number,
): Promise<void> {
  const answer = (await runExpression(cdp, pressExpression(token, key), contextId)) as
    | { ok?: boolean; reason?: string }
    | undefined;
  if (!answer || answer.ok !== true) {
    throw new ExecutorError(
      `the in-page press could not be dispatched: ${answer?.reason ?? "unknown reason"}`,
      undefined,
      "park",
    );
  }
}

/** Choose an option from inside the page; a custom combobox answers `custom` for the caller to click. */
export async function inPageSelect(
  cdp: CdpTransport,
  token: string,
  value: string,
  optionMatch: "text" | "value" | "index" = "text",
  contextId?: number,
): Promise<InPageSelectAnswer> {
  const answer = (await runExpression(
    cdp,
    selectExpression(token, value, optionMatch),
    contextId,
  )) as { matched?: boolean; value?: string | null; reason?: string; custom?: boolean } | undefined;
  return {
    matched: answer?.matched === true,
    value: answer?.value ?? null,
    reason: answer?.reason,
    custom: answer?.custom === true,
  };
}

/** Upload files from inside the page through a `DataTransfer`, without a trusted file chooser. */
export async function inPageUpload(
  cdp: CdpTransport,
  token: string,
  files: InPageFile[],
  contextId?: number,
): Promise<void> {
  const answer = (await runExpression(cdp, uploadExpression(token, files), contextId)) as
    | { ok?: boolean; reason?: string }
    | undefined;
  if (!answer || answer.ok !== true) {
    throw new ExecutorError(
      `the in-page upload could not be dispatched: ${answer?.reason ?? "unknown reason"}`,
      undefined,
      "park",
    );
  }
}

/**
 * Install the main-world dialog override at document start: the page's `alert`/`confirm`/`prompt`
 * are captured rather than shown, so a run that reaches one is not blocked by a modal.
 *
 * The override records the last dialog on `window.__plDialogs`; it never answers for the person.
 * Returned as an expression so the caller installs it before the document runs (the ticket's
 * "dialogs via a main-world override installed at document start").
 */
export function dialogOverrideExpression(): string {
  return `(() => {
    if (window.__plDialogsInstalled) return;
    window.__plDialogsInstalled = true;
    window.__plDialogs = [];
    const record = (kind, message, answer) => { try { window.__plDialogs.push({ kind: kind, message: String(message == null ? "" : message), answer: answer }); } catch (e) {} };
    const realAlert = window.alert;
    const realConfirm = window.confirm;
    const realPrompt = window.prompt;
    window.alert = function (message) { record("alert", message, null); };
    window.confirm = function (message) { record("confirm", message, false); return false; };
    window.prompt = function (message, fallback) { record("prompt", message, fallback == null ? "" : String(fallback)); return fallback == null ? "" : String(fallback); };
    window.__plRestoreDialogs = function () { window.alert = realAlert; window.confirm = realConfirm; window.prompt = realPrompt; };
  })()`;
}
