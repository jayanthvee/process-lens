// PL-114 item 7: what teaching records for a file upload, as the code actually behaves.
//
// The ticket asked for this to be confirmed rather than assumed. The recorder ignores the file
// input's own `input` and `change` events (`recorder.ts`: `if (type === "file") return;`), so the
// chosen file is never part of the recording. What a person's click on the control records is a
// plain click -- which on replay opens a file dialog and attaches nothing.
//
// This file pins that finding so the gap cannot be mistaken for "uploads work".
import { beforeEach, describe, expect, it } from "vitest";

import { attachRecorder } from "../src/content/recorder";
import type { PendingEvent } from "../src/shared/events";
import { resetStepIds } from "../src/shared/events";

const PAGE = "https://app.example.test/documents";

function harness(): { events: PendingEvent[]; detach: () => void } {
  const events: PendingEvent[] = [];
  const detach = attachRecorder(document, {
    send: (event) => events.push(event),
    isActive: () => true,
    url: () => PAGE,
    now: () => "2026-10-27T10:00:00.000Z",
  });
  return { events, detach };
}

beforeEach(() => {
  document.body.innerHTML = "";
  resetStepIds();
});

describe("a file upload while teaching (PL-114 item 7)", () => {
  it("records no upload_file step, and keeps no file name", () => {
    document.body.innerHTML = `
      <label for="scan">Attach the scan</label>
      <input type="file" id="scan" />`;
    const recorded = harness();

    const input = document.getElementById("scan") as HTMLInputElement;
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.dispatchEvent(new Event("change", { bubbles: true }));
    recorded.detach();

    // The finding, recorded here rather than assumed: teaching produces no upload step at all.
    const actions = recorded.events.map((event) => String(event.step?.action ?? ""));
    expect(actions).not.toContain("upload_file");
    expect(JSON.stringify(recorded.events)).not.toContain("scan.pdf");
  });
});
