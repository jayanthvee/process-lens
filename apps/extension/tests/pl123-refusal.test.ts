/** PL-123: a failed save can report the app's own refusal words. */

import { describe, expect, it } from "vitest";

import type { CdpTransport } from "../src/executor/cdp";
import { probeRefusal } from "../src/executor/assertions";

function cdpReturning(value: unknown): CdpTransport {
  return {
    send: async (method) => {
      if (method !== "Runtime.evaluate") return {};
      return { result: { value } };
    },
  };
}

describe("the app's refusal toast", () => {
  it("reads the visible notification text", async () => {
    expect(await probeRefusal(cdpReturning("Invalid fields: Name"))).toBe(
      "Invalid fields: Name",
    );
  });

  it("has nothing to say when no notification is showing", async () => {
    expect(await probeRefusal(cdpReturning(null))).toBeNull();
    expect(await probeRefusal(cdpReturning("   "))).toBeNull();
  });

  it("throws when the page will not answer; the caller drops the refusal", async () => {
    const broken: CdpTransport = {
      send: async () => {
        throw new Error("the page is gone");
      },
    };
    await expect(probeRefusal(broken)).rejects.toThrow();
  });
});
