/**
 * The API client. Every path is relative, so the page talks to the API it was served by and never
 * to a host it made up.
 */

import type { OperatorInfo } from "./types";

export interface ApiSuccess<T> {
  ok: true;
  data: T;
}

export interface ApiFailure {
  ok: false;
  status: number;
  /** The API's own `detail` text where there is one; otherwise a plain sentence. */
  detail: string;
}

export type ApiResult<T> = ApiSuccess<T> | ApiFailure;

export interface ApiClientOptions {
  fetchImpl?: typeof fetch;
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function parseJson(text: string): unknown {
  if (text.trim() === "") return null;
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

function detailFromPayload(status: number, payload: unknown): string {
  if (payload !== null && typeof payload === "object" && "detail" in payload) {
    const detail = (payload as { detail: unknown }).detail;
    if (typeof detail === "string" && detail.trim() !== "") return detail;
    if (detail !== undefined && detail !== null) return JSON.stringify(detail);
  }
  return `The API refused the request (HTTP ${status}).`;
}

export class ApiClient {
  readonly #fetchImpl: typeof fetch;

  constructor(options: ApiClientOptions = {}) {
    const impl = options.fetchImpl;
    this.#fetchImpl =
      impl ?? ((input: RequestInfo | URL, init?: RequestInit) => fetch(input, init));
  }

  get<T>(path: string): Promise<ApiResult<T>> {
    return this.#request<T>("GET", path);
  }

  /** A POST with no body sends no Content-Type, which is what the API's front door allows. */
  post<T>(path: string, body?: unknown): Promise<ApiResult<T>> {
    return this.#request<T>("POST", path, body);
  }

  async #request<T>(method: "GET" | "POST", path: string, body?: unknown): Promise<ApiResult<T>> {
    const headers: Record<string, string> = { Accept: "application/json" };
    const init: RequestInit = { method, headers };
    if (body !== undefined) {
      headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(body);
    }
    let response: Response;
    try {
      response = await this.#fetchImpl(path, init);
    } catch (error) {
      return { ok: false, status: 0, detail: `Could not reach the API: ${errorMessage(error)}` };
    }
    let text = "";
    try {
      text = await response.text();
    } catch {
      text = "";
    }
    const payload = parseJson(text);
    if (response.ok !== true) {
      return { ok: false, status: response.status, detail: detailFromPayload(response.status, payload) };
    }
    return { ok: true, data: payload as T };
  }
}

/** `GET /api/v1/operator`, narrowed: 200 carries the id, 503 carries the reason. */
export function readOperatorId(result: ApiResult<OperatorInfo>): string | null {
  if (result.ok === false) return null;
  const id = result.data.operator_id;
  return typeof id === "string" && id !== "" ? id : null;
}

export function operatorProblemText(result: ApiResult<OperatorInfo>): string | null {
  if (result.ok === true) {
    return readOperatorId(result) === null ? "The API returned no operator id." : null;
  }
  return result.detail;
}
