// A CDP transport that answers `Runtime.evaluate` by evaluating in the jsdom
// document and records every command the executor sends.
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

export function fakeCdp(
  handler?: (method: string, params: Record<string, unknown>) => unknown,
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
      return {};
    },
  };
  return { cdp, calls, callsTo: (method) => calls.filter((call) => call.method === method) };
}
