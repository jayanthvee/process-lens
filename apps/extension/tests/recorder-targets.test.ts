// What a click is recorded *against*: the control the person meant, never the decoration
// around it. The behaviours here are the recorder bugs the owner found by hand (PL-016
// requirements 7, 8 and 9).
import { beforeEach, describe, expect, it } from "vitest";
import { attachRecorder } from "../src/content/recorder";
import { computeTargets, semanticRole } from "../src/shared/fingerprint";
import type { PendingEvent } from "../src/shared/events";
import { resetStepIds } from "../src/shared/events";
import type { LocatorRung } from "../src/shared/recipe-types";

function harness(): { events: PendingEvent[]; detach: () => void } {
  const events: PendingEvent[] = [];
  const detach = attachRecorder(document, {
    send: (event) => events.push(event),
    isActive: () => true,
    url: () => "https://mail.example.test/inbox",
    now: () => "2026-10-12T00:00:00.000Z",
  });
  return { events, detach };
}

function click(el: Element): void {
  el.dispatchEvent(new Event("click", { bubbles: true }));
}

function cssRungs(targets: LocatorRung[]): string[] {
  return targets
    .filter((rung): rung is Extract<LocatorRung, { by: "css" }> => rung.by === "css")
    .map((rung) => rung.selector);
}

beforeEach(() => {
  document.body.innerHTML = "";
  resetStepIds();
});

describe("a click on a label (requirement 7)", () => {
  it("is recorded against the control the label names", () => {
    document.body.innerHTML =
      "<form aria-label='New lead'>" +
      "<label for='email'>Email</label>" +
      "<input id='email' data-testid='lead-email'>" +
      "</form>";
    const recorded = harness();

    click(document.querySelector("label")!);

    expect(recorded.events).toHaveLength(1);
    const step = recorded.events[0]!.step!;
    expect(step.action).toBe("click");
    expect(step.targets.some((rung) => rung.by === "testid" && rung.value === "lead-email")).toBe(
      true,
    );
    expect(step.targets.some((rung) => rung.by === "role" && rung.role === "textbox")).toBe(true);
  });

  it("never names a role a label element would have", () => {
    document.body.innerHTML = "<form aria-label='New lead'><label for='email'>Email</label><input id='email'></form>";
    const recorded = harness();

    click(document.querySelector("label")!);

    const roles = recorded.events
      .flatMap((event) => event.step?.targets ?? [])
      .filter((rung) => rung.by === "role")
      .map((rung) => (rung as Extract<LocatorRung, { by: "role" }>).role);
    expect(roles).not.toContain("label");
    expect(semanticRole(document.querySelector("label")!)).not.toBe("label");
  });
});

describe("an icon button inside a chip (requirement 8)", () => {
  it("is recorded by its own name, not by the chip around it", () => {
    document.body.innerHTML =
      "<div role='combobox' aria-label='Recipients'>" +
      "<div class='chip' role='option'>alice@example.test " +
      "<button type='button' aria-label='Remove alice@example.test'>&times;</button>" +
      "</div></div>";
    const recorded = harness();

    click(document.querySelector("button")!);

    expect(recorded.events).toHaveLength(1);
    const targets = recorded.events[0]!.step!.targets;
    expect(targets[0]).toMatchObject({ by: "role", role: "button", name: "Remove alice@example.test" });
    const chip = targets.find(
      (rung) => rung.by === "role" && (rung as Extract<LocatorRung, { by: "role" }>).role === "option",
    );
    expect(chip).toBeUndefined();
  });

  it("reads a title-only ✕ by that title, not by the chip it sits in", () => {
    document.body.innerHTML =
      "<div role='option'>bob@example.test " +
      "<button type='button' title='Remove bob@example.test'>&times;</button>" +
      "</div>";
    const recorded = harness();

    click(document.querySelector("button")!);

    const targets = recorded.events[0]!.step!.targets;
    expect(targets[0]).toMatchObject({ by: "role", role: "button", name: "Remove bob@example.test" });
  });

  it("still records the chip itself when the chip is what was clicked", () => {
    document.body.innerHTML =
      "<div role='combobox' aria-label='Recipients'>" +
      "<div class='chip' role='option'>alice@example.test " +
      "<button type='button' aria-label='Remove alice@example.test'>&times;</button>" +
      "</div></div>";
    const recorded = harness();

    click(document.querySelector(".chip")!);

    expect(recorded.events).toHaveLength(1);
    expect(recorded.events[0]!.step!.action).toBe("select");
  });
});

describe("generated ids are not used as selectors (requirement 9)", () => {
  it("drops a Gmail-style id and keeps the other rungs", () => {
    document.body.innerHTML = "<button id=':qq' aria-label='Remove alice@example.test'>&times;</button>";
    const targets = computeTargets(document.querySelector("button")!);

    expect(cssRungs(targets).some((selector) => selector.includes(":qq"))).toBe(false);
    expect(cssRungs(targets)).toEqual(["button"]);
    expect(targets[0]).toMatchObject({ by: "role", role: "button", name: "Remove alice@example.test" });
  });

  it("drops a React id, a long hex id and a numbered row id", () => {
    document.body.innerHTML =
      "<button id=':r1:'>One</button><button id='a3f9c2b41d'>Two</button><button id='row-0042'>Three</button>";

    for (const id of [":r1:", "a3f9c2b41d", "row-0042"]) {
      const targets = computeTargets(document.getElementById(id)!);
      expect(cssRungs(targets).join(" ")).not.toContain(id);
    }
  });

  it("keeps a human id", () => {
    document.body.innerHTML = "<button id='lead-save'>Save</button>";
    expect(cssRungs(computeTargets(document.getElementById("lead-save")!))).toEqual(["#lead-save"]);
  });

  it("still records a step whose id was the only roomy rung, and never silently drops it", () => {
    document.body.innerHTML = "<div id=':1a'><button id=':2b' aria-label='Remove alice@example.test'>&times;</button></div>";
    const recorded = harness();

    click(document.querySelector("button")!);

    expect(recorded.events).toHaveLength(1);
    const targets = recorded.events[0]!.step!.targets;
    expect(targets.length).toBeGreaterThan(0);
    expect(cssRungs(targets)).toEqual(["div > button"]);
    expect(JSON.stringify(targets)).not.toContain(":2b");
  });
});
