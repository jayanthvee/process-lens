/**
 * PL-121 item 3: the panel's "no code" sentence is PL-119's.
 *
 * A sign-in whose last run paused with `code_required` and `none` used to tell the person to check
 * that the inbox was signed in to Gmail. Gmail's feed lists only unread mail, so a code the person
 * already opened is invisible to ProcessLens; the run page already says that (PL-119), and the task
 * row now says the same sentence instead of blaming the inbox.
 */

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { MemoryRouter } from "react-router-dom";

import { configureApi } from "../src/lib/api";
import { PanelTasks } from "../src/panel/PanelTasks";
import type { TaskRow } from "../src/types";
import { createFetchStub, settle, type FetchStub } from "./helpers";

const PL119_SENTENCE =
  "No new code email arrived. If you already opened it in Gmail, ProcessLens can't see it — type the code here.";
const OLD_SENTENCE = "Check that this inbox is signed in to Gmail in this Chrome";

function signInTask(): TaskRow {
  return {
    workflow_id: "w-signin",
    recipe_version_id: "rv-1",
    name: "Sign in to Goodlabs",
    app: "goodlabs.com",
    last_run: {
      run_id: "run-9",
      status: "paused",
      started_at: "2026-10-28T06:07:07Z",
      counts: {},
      pause_reason: "code_required",
      pause_detail: "none",
    },
    inputs: ["email_or_phone_number"],
    sign_in: true,
    sign_in_column: "email_or_phone_number",
    sign_in_email: "person@example.com",
  };
}

let stub: FetchStub;

beforeEach(() => {
  configureApi({ base: "https://hub.example", headers: {} });
  stub = createFetchStub({ "/api/v1/tasks": { body: [signInTask()] } });
});

afterEach(() => {
  stub.restore();
  cleanup();
  configureApi({ base: "", headers: {} });
});

describe("a sign-in row whose code was not found", () => {
  it("says PL-119's sentence, not that the inbox is signed out", async () => {
    render(
      <MemoryRouter initialEntries={["/tasks"]}>
        <PanelTasks />
      </MemoryRouter>,
    );
    await settle();

    expect(screen.getByText(PL119_SENTENCE)).toBeTruthy();
    expect(screen.queryByText(new RegExp(OLD_SENTENCE))).toBeNull();
  });
});
