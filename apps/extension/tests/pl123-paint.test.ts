/**
 * PL-123 work window: the executor keeps the bound tab drawing, and a page that has not painted
 * for 2 s says so in words that are about drawing, not about a missing element.
 */

import { describe, expect, it } from "vitest";

import type { CdpTransport } from "../src/executor/cdp";
import {
  NOT_DRAWING_MESSAGE,
  ensureDrawing,
  keepDrawing,
  paintingFrames,
} from "../src/executor/visibility";

interface Sent {
  method: string;
  params?: Record<string, unknown>;
}

function cdpAnswering(frames: number, refuse: string[] = []): { cdp: CdpTransport; sent: Sent[] } {
  const sent: Sent[] = [];
  const cdp: CdpTransport = {
    send: async (method, params) => {
      sent.push({ method, params });
      if (refuse.includes(method)) throw new Error(`${method} is unavailable`);
      if (method === "Runtime.evaluate") return { result: { value: frames } };
      return {};
    },
  };
  return { cdp, sent };
}

describe("keeping the work window drawing", () => {
  it("tries the lifecycle, focus and front commands and reports the accepted ones", async () => {
    const { cdp, sent } = cdpAnswering(24, ["Page.setWebLifecycleState"]);

    const kept = await keepDrawing(cdp);

    expect(sent.map((call) => call.method)).toEqual([
      "Page.setWebLifecycleState",
      "Emulation.setFocusEmulationEnabled",
      "Page.bringToFront",
    ]);
    expect(kept).toEqual(["Emulation.setFocusEmulationEnabled", "Page.bringToFront"]);
  });

  it("counts the animation frames a page draws", async () => {
    const { cdp } = cdpAnswering(17);
    expect(await paintingFrames(cdp, 2000)).toBe(17);
  });

  it("parks a page that drew no frame with the drawing sentence, not 'not found'", async () => {
    const { cdp } = cdpAnswering(0);

    const result = await ensureDrawing(cdp, 2000);

    expect(result.drawing).toBe(false);
    expect(result.frames).toBe(0);
    expect(result.message).toBe(NOT_DRAWING_MESSAGE);
    expect(result.message).not.toMatch(/not found/i);
  });

  it("leaves a drawing page alone", async () => {
    const { cdp } = cdpAnswering(5);
    const result = await ensureDrawing(cdp, 2000);
    expect(result.drawing).toBe(true);
    expect(result.message).toBeUndefined();
  });

  it("treats a page that cannot answer as unknown, never as 'not drawing'", async () => {
    const cdp: CdpTransport = {
      send: async (method) => {
        if (method === "Runtime.evaluate") throw new Error("the page is gone");
        return {};
      },
    };
    const result = await ensureDrawing(cdp, 2000);
    expect(result.drawing).toBe(true);
  });
});
