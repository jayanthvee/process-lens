/**
 * PL-068 on the run page: when the site asks for a verification code the run pauses, and the
 * person types the code right there. The screens never keep it beyond the send.
 */

import { fireEvent, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import {
  CODE_FROM_EMAIL_BUTTON,
  CODE_FROM_EMAIL_LATER,
  CODE_FROM_EMAIL_OFFER,
  CODE_NOT_FOUND,
  CODE_STILL_ASKED,
  ENTERING_CODE,
  LOOKING_IN_GMAIL,
  codeFromEmailWords,
  codePromptWords,
} from "../src/lib/words";
import { createFetchStub, renderApp, settle, type StubRoutes } from "./helpers";

const SHELL: StubRoutes = {
  "/api/v1/needs-help": { body: [] },
  "/api/v1/executor/session": { body: { attached: true } },
  "/api/v1/operator": { body: { operator_id: "0f8fad5b-d9cb-469f-a165-70867728950e" } },
};

function pausedRun(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    id: "run-1",
    tenant_id: "t-1",
    workflow_id: "wf-1",
    recipe_version_id: "rv-1",
    input_batch_id: "b-1",
    mode: "batch",
    status: "paused",
    pause_reason: "code_required",
    cancel_requested_at: null,
    started_at: "2026-10-15T09:00:00Z",
    finished_at: null,
    counts: { total: 3, verified: 0, skipped: 0, parked: 0, failed: 0, in_progress: 3 },
    open_approval: null,
    waiting_behind: null,
    ...overrides,
  };
}

describe("the code words", () => {
  it("names the page's own heading", () => {
    expect(codePromptWords("Enter the 6-digit code we emailed you")).toBe(
      "The site is asking for a verification code (Enter the 6-digit code we emailed you). Enter the code it sent you.",
    );
    expect(ENTERING_CODE).toBe("Entering the code…");
    expect(CODE_NOT_FOUND).toContain("Type it in the work window, then press Resume.");
  });
});

describe("the paused run's code panel", () => {
  it("shows the banner, an input and Continue, and Resume stays available", async () => {
    const stub = createFetchStub({
      ...SHELL,
      "/api/v1/runs/run-1/records": { body: [] },
      "/api/v1/runs/run-1": { body: pausedRun() },
    });
    renderApp("/runs/run-1");
    await settle();

    expect(screen.getByText(/The site is asking for a verification code/)).toBeTruthy();
    expect(screen.getByLabelText(/verification code/i)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Continue" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Resume" })).toBeTruthy();
    stub.restore();
  });

  it("sends the code, says it is entering it, and clears the box", async () => {
    const stub = createFetchStub({
      ...SHELL,
      "/api/v1/runs/run-1/records": { body: [] },
      "/api/v1/runs/run-1": { body: pausedRun() },
      "/api/v1/runs/run-1/code": { body: { entered: true, reason: "submitted" } },
    });
    renderApp("/runs/run-1");
    await settle();

    const input = screen.getByLabelText(/verification code/i) as HTMLInputElement;
    fireEvent.change(input, { target: { value: "246810" } });
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    await settle();

    const posted = stub.requests.find((request) => request.path.endsWith("/code"));
    expect(posted?.body).toEqual({ code: "246810" });
    expect(input.value).toBe("");
    expect(screen.queryByText(ENTERING_CODE)).toBeNull();
    stub.restore();
  });

  it("says where to type it when the page could not be found", async () => {
    const stub = createFetchStub({
      ...SHELL,
      "/api/v1/runs/run-1/records": { body: [] },
      "/api/v1/runs/run-1": { body: pausedRun() },
      "/api/v1/runs/run-1/code": { body: { entered: false, reason: "not_found" } },
    });
    renderApp("/runs/run-1");
    await settle();

    const input = screen.getByLabelText(/verification code/i);
    fireEvent.change(input, { target: { value: "246810" } });
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    await settle();

    expect(screen.getByText(CODE_NOT_FOUND)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Resume" })).toBeTruthy();
    stub.restore();
  });
});

describe("reading the code from Gmail (PL-102)", () => {
  it("offers it right beside the code box, and says it will do it by itself from then on", async () => {
    const stub = createFetchStub({
      ...SHELL,
      "/api/v1/runs/run-1/records": { body: [] },
      "/api/v1/runs/run-1": { body: pausedRun() },
    });
    renderApp("/runs/run-1");
    await settle();

    expect(screen.getByText(CODE_FROM_EMAIL_OFFER)).toBeTruthy();
    expect(screen.getByRole("button", { name: CODE_FROM_EMAIL_BUTTON })).toBeTruthy();
    expect(screen.getByText(CODE_FROM_EMAIL_LATER)).toBeTruthy();
    stub.restore();
  });

  it("asks the work window to read it, remembering the yes, with no code on this page", async () => {
    const stub = createFetchStub({
      ...SHELL,
      "/api/v1/runs/run-1/records": { body: [] },
      "/api/v1/runs/run-1": { body: pausedRun() },
      "/api/v1/runs/run-1/code-from-email": {
        body: { entered: true, reason: "submitted", resumed: true },
      },
    });
    renderApp("/runs/run-1");
    await settle();

    fireEvent.click(screen.getByRole("button", { name: CODE_FROM_EMAIL_BUTTON }));
    await settle();

    const posted = stub.requests.find((request) => request.path.endsWith("/code-from-email"));
    expect(posted?.body).toEqual({ remember: true });
    expect(screen.queryByText(LOOKING_IN_GMAIL)).toBeNull();
    stub.restore();
  });

  it("says why when there was no single code to type", async () => {
    const stub = createFetchStub({
      ...SHELL,
      "/api/v1/runs/run-1/records": { body: [] },
      "/api/v1/runs/run-1": { body: pausedRun() },
      "/api/v1/runs/run-1/code-from-email": {
        body: { entered: false, reason: "ambiguous", resumed: false },
      },
    });
    renderApp("/runs/run-1");
    await settle();

    fireEvent.click(screen.getByRole("button", { name: CODE_FROM_EMAIL_BUTTON }));
    await settle();

    expect(screen.getByText(codeFromEmailWords("ambiguous"))).toBeTruthy();
    stub.restore();
  });

  it("says so when a typed code left the site still asking", async () => {
    const stub = createFetchStub({
      ...SHELL,
      "/api/v1/runs/run-1/records": { body: [] },
      "/api/v1/runs/run-1": { body: pausedRun() },
      "/api/v1/runs/run-1/code": { body: { entered: true, reason: "submitted", resumed: false } },
    });
    renderApp("/runs/run-1");
    await settle();

    fireEvent.change(screen.getByLabelText(/verification code/i), { target: { value: "246810" } });
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    await settle();

    expect(screen.getByText(CODE_STILL_ASKED)).toBeTruthy();
    stub.restore();
  });
});
