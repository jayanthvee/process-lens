/** The API client: a good read, the API's own refusal text, and a body that is sent as sent. */

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { ApiError, apiGet, apiPost, errorText } from "../src/lib/api";
import { callsTo, createFetchStub, type FetchStub } from "./helpers";

let stub: FetchStub;

afterEach(() => {
  stub.restore();
});

describe("apiGet", () => {
  it("reads the JSON the API sends", async () => {
    stub = createFetchStub({ "/api/v1/operator": { body: { operator_id: "abc" } } });

    await expect(apiGet<{ operator_id: string }>("/api/v1/operator")).resolves.toEqual({
      operator_id: "abc",
    });
    expect(callsTo(stub, "/api/v1/operator")).toBe(1);
  });

  it("throws the API's own detail text, with its status", async () => {
    stub = createFetchStub({
      "/api/v1/records/rec-1/requeue": {
        status: 409,
        body: { detail: "a commit was attempted, so a person must confirm the destination" },
      },
    });

    const failure = await apiGet("/api/v1/records/rec-1/requeue").catch((error: unknown) => error);

    expect(failure).toBeInstanceOf(ApiError);
    expect((failure as ApiError).status).toBe(409);
    expect((failure as ApiError).detail).toBe(
      "a commit was attempted, so a person must confirm the destination",
    );
    expect(errorText(failure)).toBe(
      "a commit was attempted, so a person must confirm the destination",
    );
  });

  it("has a sentence of its own when the API sends no detail", async () => {
    stub = createFetchStub({ "/api/v1/tasks": { status: 500, body: {} } });

    const failure = (await apiGet("/api/v1/tasks").catch((error: unknown) => error)) as ApiError;

    expect(failure.status).toBe(500);
    expect(failure.detail).toContain("HTTP 500");
  });

  it("says it could not reach the API when the call itself fails", async () => {
    stub = createFetchStub({
      "/api/v1/tasks": () => {
        throw new Error("connection refused");
      },
    });

    const failure = (await apiGet("/api/v1/tasks").catch((error: unknown) => error)) as ApiError;

    expect(failure).toBeInstanceOf(ApiError);
    expect(failure.status).toBe(0);
    expect(failure.detail).toBe("Could not reach the API.");
  });
});

describe("apiPost", () => {
  it("sends the body as JSON and reads the answer", async () => {
    stub = createFetchStub({
      "/api/v1/records/rec-1/requeue": { body: { record_id: "rec-1", state: "pending" } },
    });

    const answer = await apiPost<{ state: string }>("/api/v1/records/rec-1/requeue", {
      destination_checked_by: "operator-1",
    });

    expect(answer.state).toBe("pending");
    expect(stub.requests).toEqual([
      {
        method: "POST",
        path: "/api/v1/records/rec-1/requeue",
        body: { destination_checked_by: "operator-1" },
      },
    ]);
  });

  it("sends no body when there is nothing to send", async () => {
    stub = createFetchStub({ "/api/v1/records/rec-1/dismiss": { body: { state: "skipped" } } });

    await apiPost("/api/v1/records/rec-1/dismiss");

    expect(stub.requests[0]?.body).toBeNull();
  });
});

describe("errorText", () => {
  it("falls back to one plain sentence for anything else", () => {
    expect(errorText(new Error("boom"))).toBe("boom");
    expect(errorText(undefined)).toBe("Something went wrong.");
    expect(errorText("see the log")).toBe("Something went wrong.");
  });
});

beforeEach(() => {
  // Every test builds its own stub; this keeps the type checker honest about the shared variable.
  stub = createFetchStub({});
});
