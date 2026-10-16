// A CDP transport that answers `Runtime.evaluate` by evaluating in the jsdom
// document and records every command the executor sends.
//
// It also simulates the input commands the executor produces, the way a real browser does:
// `Input.insertText` writes into the focused element, a Backspace key event clears it, and a
// `Input.dispatchMouseEvent` dispatches a real DOM mouse event on the element at that point.
// Without the mouse simulation the PL-017b landing listener (a capture-phase `mousedown` on the
// document) would never see a press, and a click that landed correctly would be read as "no
// result" — a limitation of the fake, not of the executor. `Runtime.evaluate` awaits a returned
// promise when `awaitPromise` is set, so the page-side stability wait resolves.
import type { CdpTransport } from "../../src/executor/cdp";

export interface RecordedCall {
  method: string;
  params: Record<string, unknown>;
}

export interface FakeCdp {
  cdp: CdpTransport;
  calls: RecordedCall[];
  callsTo(method: string): RecordedCall[];
  /** Fire a CDP event to the transport's subscribers (for the navigation case). */
  emit(event: string, params?: Record<string, unknown>): void;
}

export interface FakeCdpOptions {
  simulateInput?: boolean;
  /**
   * Where a press or a release lands. Defaults to whatever `elementFromPoint` reports at the
   * point (the marked element, when a layout stub is installed) and falls back to `document.body`.
   * A test that wants a press to land on something else overrides this.
   */
  pressAt?: (params: Record<string, unknown>, at: Element | null) => Element | null;
  /**
   * Model a click that replaces the document: no DOM mouse event is dispatched (so the landing
   * listener records nothing) and a `Page.frameNavigated` is emitted on the release, exactly as a
   * navigated document would leave no result behind.
   */
  navigateOnPress?: boolean;
}

/** The element a real `Input.*` command would act on: whatever the page focused. */
function focused(): HTMLInputElement | HTMLTextAreaElement | null {
  const doc = globalThis.document;
  if (!doc) return null;
  const active = doc.activeElement as HTMLInputElement | HTMLTextAreaElement | null;
  if (!active) return null;
  const tag = active.tagName?.toUpperCase();
  return tag === "INPUT" || tag === "TEXTAREA" ? active : null;
}

function simulateInput(method: string, params: Record<string, unknown>): void {
  if (method === "Input.insertText") {
    const el = focused();
    if (el) el.value = `${el.value}${String(params["text"] ?? "")}`;
    return;
  }
  if (method === "Input.dispatchKeyEvent") {
    const el = focused();
    if (!el) return;
    if (params["type"] === "keyDown" && params["key"] === "Backspace") el.value = "";
    const text = params["text"];
    if (params["type"] === "keyDown" && typeof text === "string") el.value += text;
  }
}

/** The element at a point, per the page's own hit test; null when there is no hit test. */
function elementAt(x: number, y: number): Element | null {
  const doc = globalThis.document;
  if (!doc || typeof doc.elementFromPoint !== "function") return null;
  try {
    return doc.elementFromPoint(x, y);
  } catch {
    return null;
  }
}

/** A DOM mouse event a real CDP mouse command would produce, on the element it lands on. */
function dispatchMouse(
  type: string,
  params: Record<string, unknown>,
  options: FakeCdpOptions,
): void {
  const doc = globalThis.document;
  if (!doc) return;
  const rawX = Number(params["x"]);
  const rawY = Number(params["y"]);
  const x = Number.isFinite(rawX) ? rawX : 0;
  const y = Number.isFinite(rawY) ? rawY : 0;
  const at = elementAt(x, y);
  const landed = options.pressAt ? options.pressAt(params, at) : at;
  const target = landed ?? doc.body;
  target.dispatchEvent(
    new MouseEvent(type, {
      bubbles: true,
      cancelable: true,
      // A real mouse event over a shadow control is dispatched at the inner target and retargets
      // as it crosses the boundary; composed models that, so the document-level landing listener
      // sees it (and its `composedPath` names the inner element).
      composed: true,
      button: 0,
      clientX: x,
      clientY: y,
    }),
  );
}

export function fakeCdp(
  handler?: (method: string, params: Record<string, unknown>) => unknown,
  options: FakeCdpOptions = {},
): FakeCdp {
  const calls: RecordedCall[] = [];
  const handlers = new Map<string, Set<(params: Record<string, unknown>) => void>>();
  /**
   * Where the fake tab is, as its own `location.href`.
   *
   * A browser that accepted a navigate is showing the page it was sent to, and the executor reads
   * that URL back to prove a navigate arrived (PL-027). Modelling it here is what makes the fake a
   * browser rather than a command logger: a test that wants a tab which did NOT arrive overrides
   * this with its own `Runtime.evaluate` handler.
   */
  let locationHref = typeof globalThis.location?.href === "string" ? globalThis.location.href : "";
  const emit = (event: string, params: Record<string, unknown> = {}): void => {
    for (const listener of handlers.get(event) ?? []) listener(params);
  };
  const cdp: CdpTransport = {
    on(event: string, listener: (params: Record<string, unknown>) => void): () => void {
      const set = handlers.get(event) ?? new Set();
      set.add(listener);
      handlers.set(event, set);
      return () => set.delete(listener);
    },
    async send(method: string, params: Record<string, unknown> = {}): Promise<unknown> {
      calls.push({ method, params });
      if (method === "Page.navigate" && typeof params["url"] === "string") {
        locationHref = params["url"];
      }
      if (handler) {
        const handled = handler(method, params);
        if (handled !== undefined) return handled;
      }
      if (method === "Runtime.evaluate") {
        const expression = String(params["expression"] ?? "");
        // The location probe answers from the tab's own URL, exactly as a browser would.
        if (expression.trim() === "location.href") {
          return { result: { value: locationHref } };
        }
        let value: unknown = new Function(`return (${expression})`)();
        // The page-side stability wait returns a promise; `awaitPromise` must be honoured, or
        // the executor would read the unresolved promise as a failed measurement.
        if (params["awaitPromise"] && value && typeof (value as PromiseLike<unknown>).then === "function") {
          value = await value;
        }
        return { result: { value } };
      }
      if (method === "Input.dispatchMouseEvent") {
        const type = String(params["type"]);
        if (options.navigateOnPress) {
          // A navigating click: the document goes away, so no listener sees the press.
          if (type === "mouseReleased") emit("Page.frameNavigated", { frame: {} });
          return {};
        }
        if (type === "mousePressed") dispatchMouse("mousedown", params, options);
        else if (type === "mouseReleased") dispatchMouse("mouseup", params, options);
        else if (type === "mouseMoved") dispatchMouse("mousemove", params, options);
        return {};
      }
      // Opt-in: a test that simulates input in its own handler keeps that control.
      if (options.simulateInput) simulateInput(method, params);
      return {};
    },
  };
  return { cdp, calls, callsTo: (method) => calls.filter((call) => call.method === method), emit };
}
