// PL-102: a one-time code is never written into a recording. On the pilot call six single-digit
// boxes were recorded as six "Typed the fill input" steps, and a click in Gmail was named with the
// email's text, code included. The run reads codes from email itself; the recording never needs one.
import { beforeEach, describe, expect, it } from "vitest";

import { attachRecorder } from "../src/content/recorder";
import type { PendingEvent } from "../src/shared/events";
import { resetStepIds } from "../src/shared/events";

function harness(): { events: PendingEvent[]; detach: () => void } {
  const events: PendingEvent[] = [];
  const detach = attachRecorder(document, {
    send: (event) => events.push(event),
    isActive: () => true,
    url: () => "https://app.example.test/verify",
    now: () => "2026-10-25T10:00:00.000Z",
  });
  return { events, detach };
}

function type(field: HTMLInputElement, value: string): void {
  field.focus();
  field.value = value;
  field.dispatchEvent(new Event("input", { bubbles: true }));
  field.dispatchEvent(new Event("change", { bubbles: true }));
  field.dispatchEvent(new Event("focusout", { bubbles: true }));
}

beforeEach(() => {
  document.body.innerHTML = "";
  resetStepIds();
});

describe("code fields", () => {
  it("records nothing typed into six single-character boxes", () => {
    document.body.innerHTML =
      "<h2>Enter the code we emailed you</h2><form>" +
      Array.from({ length: 6 }, (_, i) => `<input class='fill' maxlength='1' id='d${i}'>`).join("") +
      "<button type='submit'>Verify</button></form>";
    const recorded = harness();

    "482913".split("").forEach((digit, i) => {
      type(document.getElementById(`d${i}`) as HTMLInputElement, digit);
    });

    expect(recorded.events.filter((event) => event.kind === "fill")).toEqual([]);
  });

  it("records nothing typed into a field marked one-time-code", () => {
    document.body.innerHTML =
      "<label for='otp'>Code</label><input id='otp' autocomplete='one-time-code'>";
    const recorded = harness();

    type(document.getElementById("otp") as HTMLInputElement, "482913");

    expect(recorded.events.filter((event) => event.kind === "fill")).toEqual([]);
  });

  it("records nothing typed into a numeric field that asks for a verification code", () => {
    document.body.innerHTML =
      "<label for='v'>Verification code</label><input id='v' inputmode='numeric' maxlength='6'>";
    const recorded = harness();

    type(document.getElementById("v") as HTMLInputElement, "482913");

    expect(recorded.events.filter((event) => event.kind === "fill")).toEqual([]);
  });

  it("still records a short number field that is not a code (a quantity)", () => {
    document.body.innerHTML =
      "<label for='q'>Quantity</label><input id='q' inputmode='numeric' maxlength='4'>";
    const recorded = harness();

    type(document.getElementById("q") as HTMLInputElement, "12");

    expect(recorded.events.filter((event) => event.kind === "fill")).toHaveLength(1);
  });
});

describe("step names", () => {
  it("never carry a code from the page's text", () => {
    document.body.innerHTML =
      "<div role='row' tabindex='0' id='mail'>NCPL One, Your NCPL One Login Verification Code, " +
      "Your code is 982707</div>";
    const recorded = harness();

    (document.getElementById("mail") as HTMLElement).click();

    const text = JSON.stringify(recorded.events);
    expect(text).not.toContain("982707");
  });
});
