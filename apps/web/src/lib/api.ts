/**
 * The API client. Every path is relative, so the page talks to the API it was served by and never
 * to a host it made up.
 *
 * A refusal carries the API's own `detail` text, so a page can show it next to the button that
 * caused it instead of inventing a sentence of its own.
 */

export class ApiError extends Error {
  readonly status: number;
  readonly detail: string;

  constructor(status: number, detail: string) {
    super(detail);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

const REFUSED = "The API refused the request";

function parseJson(text: string): unknown {
  if (text.trim() === "") return null;
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return null;
  }
}

function detailFromPayload(status: number, payload: unknown): string {
  if (payload !== null && typeof payload === "object" && "detail" in payload) {
    const detail = (payload as { detail: unknown }).detail;
    if (typeof detail === "string" && detail.trim() !== "") return detail;
  }
  return `${REFUSED} (HTTP ${status}).`;
}

async function request<T>(
  method: "GET" | "POST" | "PUT",
  path: string,
  body?: unknown,
  signal?: AbortSignal,
  form?: FormData,
): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  const init: RequestInit = { method, headers };
  if (signal !== undefined) init.signal = signal;
  if (form !== undefined) {
    // A form is sent as it stands: the browser adds `Content-Type` itself, with the multipart
    // boundary. Setting the header here would leave the boundary out and the API would refuse it.
    init.body = form;
  } else if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  let response: Response;
  try {
    response = await fetch(path, init);
  } catch (error) {
    if (signal !== undefined && signal.aborted) throw error;
    throw new ApiError(0, "Could not reach the API.");
  }
  let text = "";
  try {
    text = await response.text();
  } catch {
    text = "";
  }
  if (!response.ok) {
    throw new ApiError(response.status, detailFromPayload(response.status, parseJson(text)));
  }
  return parseJson(text) as T;
}

/** `GET` one JSON path, or throw an `ApiError` carrying the API's own explanation. */
export function apiGet<T>(path: string, signal?: AbortSignal): Promise<T> {
  return request<T>("GET", path, undefined, signal);
}

/** `POST` a JSON body (omit it when the API sends nothing) and read the JSON answer. */
export function apiPost<T>(path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  return request<T>("POST", path, body, signal);
}

/**
 * `POST` a form: a file, and the fields beside it. The browser sets `Content-Type` with the
 * multipart boundary, so this never sets it.
 */
export function apiPostFile<T>(path: string, form: FormData, signal?: AbortSignal): Promise<T> {
  return request<T>("POST", path, undefined, signal, form);
}

/** `PUT` a JSON body and read the JSON answer. */
export function apiPut<T>(path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  return request<T>("PUT", path, body, signal);
}

/** The API's `detail` text, so a refusal can be shown next to the button that caused it. */
export function errorText(error: unknown): string {
  if (error instanceof ApiError) return error.detail;
  if (error instanceof Error && error.message.trim() !== "") return error.message;
  return "Something went wrong.";
}
