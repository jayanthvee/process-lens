// A CDP transport that answers `Runtime.evaluate` by evaluating in the jsdom
// document and records every command the executor sends.
//
// It also simulates the two input commands a fill produces, the way a real browser does:
// `Input.insertText` writes into the focused element, and a Backspace key event clears it.
// Without that, a fill would dispatch correctly and then fail its own value read-back,
// because the fake document would never change — which is a limitation of the fake, not
// of the executor.
import type { CdpTransport } from "../../src/executor/cdp";

export interface RecordedCall {
  method: string;
  params: Record<string, unknown>;
}

export interface FakeCdp {
  cdp: CdpTransport;
  calls: RecordedCall[];
  callsTo(method: string): RecordedCall[];
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

export function fakeCdp(
  handler?: (method: string, params: Record<string, unknown>) => unknown,
  options: { simulateInput?: boolean } = {},
): FakeCdp {
  const calls: RecordedCall[] = [];
  const cdp: CdpTransport = {
    async send(method: string, params: Record<string, unknown> = {}): Promise<unknown> {
      calls.push({ method, params });
      if (handler) {
        const handled = handler(method, params);
        if (handled !== undefined) return handled;
      }
      if (method === "Runtime.evaluate") {
        const expression = String(params["expression"] ?? "");
        const value = new Function(`return (${expression})`)();
        return { result: { value } };
      }
      // Opt-in: a test that simulates input in its own handler keeps that control.
      if (options.simulateInput) simulateInput(method, params);
      return {};
    },
  };
  return { cdp, calls, callsTo: (method) => calls.filter((call) => call.method === method) };
}
