/** PL-105: the side panel's API client talks to the hub address, with the team's access code. */

import { afterEach, describe, expect, it } from "vitest";

import { apiGet, apiPost, configureApi } from "../src/lib/api";

const BASE = "https://hub.example";
const AUTH = { authorization: "Bearer team-code" };

interface Sent {
  url: string;
  headers: Record<string, string>;
}

const original = globalThis.fetch;

function recordFetch(): Sent[] {
  const sent: Sent[] = [];
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    sent.push({ url: String(input), headers: (init?.headers ?? {}) as Record<string, string> });
    return { ok: true, status: 200, text: async () => "[]" } as unknown as Response;
  }) as typeof fetch;
  return sent;
}

afterEach(() => {
  globalThis.fetch = original;
  configureApi({ base: "", headers: {} });
});

describe("a configured API client", () => {
  it("sends a read to the hub address with the access header", async () => {
    const sent = recordFetch();
    configureApi({ base: BASE, headers: AUTH });

    await apiGet("/api/v1/tasks");

    expect(sent[0]?.url).toBe(`${BASE}/api/v1/tasks`);
    expect(sent[0]?.headers["authorization"]).toBe("Bearer team-code");
  });

  it("sends a write the same way, and a trailing slash on the base is not doubled", async () => {
    const sent = recordFetch();
    configureApi({ base: `${BASE}/`, headers: AUTH });

    await apiPost("/api/v1/runs", { mode: "batch" });

    expect(sent[0]?.url).toBe(`${BASE}/api/v1/runs`);
    expect(sent[0]?.headers["authorization"]).toBe("Bearer team-code");
    expect(sent[0]?.headers["Content-Type"]).toBe("application/json");
  });

  it("keeps the website's relative paths when nothing is configured", async () => {
    const sent = recordFetch();

    await apiGet("/api/v1/tasks");

    expect(sent[0]?.url).toBe("/api/v1/tasks");
    expect(sent[0]?.headers["authorization"]).toBeUndefined();
  });
});
