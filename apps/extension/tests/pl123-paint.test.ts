/**
 * PL-123 work window: the executor keeps the bound tab drawing, counts the frames until two have
 * drawn, raises the window only when nothing drew, and parks with words about drawing rather than
 * about a missing element.
 */

import { describe, expect, it } from "vitest";

import type { CdpTransport } from "../src/executor/cdp";
import {
  DRAWING_FRAMES,
  FOCUS_METHODS,
  NOT_DRAWING_MESSAGE,
  ensureDrawing,
  keepDrawing,
  paintingFrames,
} from "../src/executor/visibility";

interface Sent {
  method: string;
  params?: Record<string, unknown>;
}

interface Script {
  /** The frame counts the page answers with, one per Runtime.evaluate, in order. */
  frames: number[];
  /** Commands Chrome refuses. */
  refuse?: string[];
}

function cdp(script: Script): { cdp: CdpTransport; sent: Sent[] } {
  const sent: Sent[] = [];
  const counts = [...script.frames];
  const transport: CdpTransport = {
    send: async (method, params) => {
      sent.push({ method, params });
      if ((script.refuse ?? []).includes(method)) throw new Error(`${method} is unavailable`);
      if (method === "Runtime.evaluate") {
        const value = counts.length > 1 ? counts.shift() : (counts[0] ?? 0);
        return { result: { value } };
      }
      return {};
    },
  };
  return { cdp: transport, sent };
}

describe("keeping the work window drawing", () => {
  it("sets the lifecycle and focus emulation, and does not raise the window yet", async () => {
    const { cdp: transport, sent } = cdp({ frames: [10], refuse: ["Page.setWebLifecycleState"] });

    const kept = await keepDrawing(transport);

    expect(sent.map((call) => call.method)).toEqual(FOCUS_METHODS.map((entry) => entry.method));
    expect(kept).toEqual(["Emulation.setFocusEmulationEnabled"]);
    expect(sent.map((call) => call.method)).not.toContain("Page.bringToFront");
  });

  it("counts the frames the page draws", async () => {
    const { cdp: transport } = cdp({ frames: [DRAWING_FRAMES] });
    expect(await paintingFrames(transport, 2000)).toBe(DRAWING_FRAMES);
  });

  it("raises the window only after nothing drew, then counts again and carries on", async () => {
    // First count: nothing. After the raise: drawing.
    const { cdp: transport, sent } = cdp({ frames: [0, 4] });

    const result = await ensureDrawing(transport, 2000);

    expect(result.drawing).toBe(true);
    expect(result.frames).toBe(4);
    expect(result.kept).toContain("Page.bringToFront");
    const evaluated = sent.filter((call) => call.method === "Runtime.evaluate");
    expect(evaluated).toHaveLength(2);
  });

  it("parks a page that still draws nothing with the drawing sentence, not 'not found'", async () => {
    const { cdp: transport } = cdp({ frames: [0, 0] });

    const result = await ensureDrawing(transport, 2000);

    expect(result.drawing).toBe(false);
    expect(result.frames).toBe(0);
    expect(result.message).toBe(NOT_DRAWING_MESSAGE);
    expect(result.message).not.toMatch(/not found/i);
  });

  it("leaves a page that is already painting alone: no raise at all", async () => {
    const { cdp: transport, sent } = cdp({ frames: [3] });

    const result = await ensureDrawing(transport, 2000);

    expect(result.drawing).toBe(true);
    expect(sent.map((call) => call.method)).not.toContain("Page.bringToFront");
  });

  it("treats a page that cannot answer as unknown, never as 'not drawing'", async () => {
    const transport: CdpTransport = {
      send: async (method) => {
        if (method === "Runtime.evaluate") throw new Error("the page is gone");
        return {};
      },
    };
    const result = await ensureDrawing(transport, 2000);
    expect(result.drawing).toBe(true);
  });
});
