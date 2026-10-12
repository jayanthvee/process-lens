/** The run buttons follow the status, and the pause reason is written for a person. */

import { describe, expect, it } from "vitest";
import * as logic from "../src/logic";
import * as view from "../src/view";
import { sampleRunDetail } from "./helpers";

function header(status: string, pauseReason: string | null = null): HTMLElement {
  return view.renderRunHeader({
    run: sampleRunDetail({ status, pause_reason: pauseReason }),
    errors: new Map<string, string>(),
    onAction: () => {},
  });
}

function labels(node: HTMLElement): string[] {
  return Array.from(node.querySelectorAll("button")).map((buttonNode) => buttonNode.textContent ?? "");
}

describe("run buttons follow the status", () => {
  it("offers Pause and Cancel for an open run, and Resume only when it is paused", () => {
    expect(labels(header("queued"))).toEqual(["Pause", "Cancel"]);
    expect(labels(header("running"))).toEqual(["Pause", "Cancel"]);
    expect(labels(header("paused"))).toEqual(["Pause", "Resume", "Cancel"]);
  });

  it("offers nothing once the run is finished", () => {
    expect(labels(header("completed"))).toEqual([]);
    expect(labels(header("cancelled"))).toEqual([]);
    expect(labels(header("failed"))).toEqual([]);
  });

  it("says the same thing in logic", () => {
    expect(logic.runActions("running")).toEqual({ pause: true, resume: false, cancel: true });
    expect(logic.runActions("paused")).toEqual({ pause: true, resume: true, cancel: true });
    expect(logic.runActions("completed")).toEqual({ pause: false, resume: false, cancel: false });
    expect(logic.runActions("cancelled")).toEqual({ pause: false, resume: false, cancel: false });
    expect(logic.runActions("failed")).toEqual({ pause: false, resume: false, cancel: false });
    expect(logic.runActions("queued")).toEqual({ pause: true, resume: false, cancel: true });
  });

  it("calls the action the button names", () => {
    const seen: logic.RunAction[] = [];
    const node = view.renderRunHeader({
      run: sampleRunDetail({ status: "paused" }),
      errors: new Map<string, string>(),
      onAction: (action) => {
        seen.push(action);
      },
    });
    node.querySelector<HTMLButtonElement>(".run-action-resume")?.click();
    node.querySelector<HTMLButtonElement>(".run-action-pause")?.click();
    expect(seen).toEqual(["resume", "pause"]);
  });
});

describe("the pause reason in words a person understands", () => {
  it("maps login_required and challenge to an actionable sentence", () => {
    expect(logic.humanizePauseReason("login_required")).toBe(
      "Log in to the site in the bound tab, then resume.",
    );
    const challenge = logic.humanizePauseReason("challenge");
    expect(challenge).not.toBeNull();
    expect(challenge).toContain("bound tab");
    expect(challenge).toContain("resume");
  });

  it("humanizes a reason it has never heard of", () => {
    const text = logic.humanizePauseReason("network_error");
    expect(text).toContain("Network error");
    expect(text).toContain("resume");
  });

  it("renders the reason, the status and the counts", () => {
    const node = header("paused", "login_required");
    expect(node.querySelector(".pause-reason")?.textContent).toBe(
      "Log in to the site in the bound tab, then resume.",
    );
    expect(node.querySelector(".run-status")?.textContent).toBe("Paused");
    expect(node.querySelector(".count-parked .count-value")?.textContent).toBe("1");
    expect(node.querySelector(".count-total .count-label")?.textContent).toBe("Total: ");
  });

  it("shows a plain sentence when there is no reason", () => {
    expect(header("running").querySelector(".pause-reason")?.textContent).toBe("Not paused.");
  });
});

describe("the results link", () => {
  it("points at the run's results.csv on this origin", () => {
    const link = header("completed").querySelector<HTMLAnchorElement>(".results-link");
    expect(link?.getAttribute("href")).toBe("/api/v1/runs/run-1/results.csv");
  });
});
