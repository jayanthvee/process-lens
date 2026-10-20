/**
 * Test-only helpers: the router-wrapping render, a fake `fetch`, and the two ways to look at it.
 *
 * Every other test file imports these, so a page test never builds its own router or touches the
 * real network.
 */

import { act, render, type RenderResult } from "@testing-library/react";
import type { ReactElement } from "react";
import { MemoryRouter } from "react-router-dom";

import { App } from "../src/App";

export interface RecordedRequest {
  method: string;
  path: string;
  /** The query string, with its leading `?`, exactly as the page sent it. */
  search: string;
  body: unknown;
  /** The form a request carried, when it was a `FormData` upload (PL-076). */
  form?: FormData;
}

export interface StubResponse {
  status?: number;
  body: unknown;
}

export type StubRoutes = Record<string, StubResponse | ((call: RecordedRequest) => StubResponse)>;

export interface FetchStub {
  /** The paths asked for, in order. */
  calls: string[];
  /** Every request, with its method and its parsed body. */
  requests: RecordedRequest[];
  /** Put the real `fetch` back. */
  restore: () => void;
}

/** A `Response` good enough for the API client: status, ok, and a JSON text body.
 *
 * `blob()` answers the same bytes as a CSV blob, so the document-to-CSV fetch (PL-076) behaves as
 * it does in a browser: the page uploads the blob through the normal file path.
 */
export function jsonResponse(status: number, body: unknown): Response {
  const text = body === undefined ? "" : typeof body === "string" ? body : JSON.stringify(body);
  return {
    ok: status >= 200 && status < 300,
    status,
    text: async () => text,
    blob: async () => new Blob([text], { type: "text/csv" }),
  } as unknown as Response;
}

function pick(routes: StubRoutes, path: string): StubRoutes[string] | undefined {
  const exact = routes[path];
  if (exact !== undefined) return exact;
  const prefixes = Object.keys(routes)
    .filter((key) => path.startsWith(key))
    .sort((left, right) => right.length - left.length);
  const longest = prefixes[0];
  return longest === undefined ? undefined : routes[longest];
}

/**
 * Replace `fetch` with a stub that answers from `routes`. A key matches the path exactly, or as the
 * longest prefix of it, so `/api/v1/runs` also answers `/api/v1/runs/abc`. An unmatched path is a
 * 404 with the API's own detail shape.
 */
export function createFetchStub(routes: StubRoutes = {}): FetchStub {
  const original = globalThis.fetch;
  const requests: RecordedRequest[] = [];
  const calls: string[] = [];

  const stub = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const raw =
      typeof input === "string" ? input : input instanceof URL ? input.toString() : String(input);
    const path = new URL(raw, "http://localhost").pathname;
    const search = new URL(raw, "http://localhost").search;
    const method = (init?.method ?? "GET").toUpperCase();
    const rawBody = init?.body;
    const body: unknown =
      typeof rawBody === "string" && rawBody !== "" ? (JSON.parse(rawBody) as unknown) : null;
    const call: RecordedRequest = {
      method,
      path,
      search,
      body,
      ...(rawBody instanceof FormData ? { form: rawBody } : {}),
    };
    requests.push(call);
    calls.push(path);
    const entry = pick(routes, path);
    const answer = entry === undefined ? { body: { detail: "Not found" } } : entry;
    const resolved = typeof answer === "function" ? answer(call) : answer;
    return jsonResponse(resolved.status ?? 200, resolved.body);
  };

  globalThis.fetch = stub as unknown as typeof fetch;
  return {
    calls,
    requests,
    restore: () => {
      globalThis.fetch = original;
    },
  };
}

/** How many times a path was asked for. */
export function callsTo(stub: FetchStub, path: string): number {
  return stub.calls.filter((call) => call === path).length;
}

/** Let the queue of promises and the pending renders finish, so assertions see the settled page. */
export async function settle(rounds = 3): Promise<void> {
  for (let round = 0; round < rounds; round += 1) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }
}

/** Render the whole app, at one path, inside a router. */
export function renderApp(path = "/"): RenderResult {
  const element: ReactElement = (
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>
  );
  return render(element);
}
