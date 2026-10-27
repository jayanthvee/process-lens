// PL-107 item 12: the worker's half of a teaching-time code page.
//
// Consent comes first, then one plain "reading" state while Gmail is looked at, then either the
// one code (handed to the page, never to a panel state) or the reason there was no single code.
import { describe, expect, it, vi } from "vitest";

import { codeForTeaching, enteredCodeState } from "../src/background/teach-code";
import type { MailMessage } from "../src/shared/email-code";
import type { TeachCodeState } from "../src/shared/messages";

const T0 = Date.parse("2026-10-26T10:00:00Z");
const SITE = "https://app.example.test/login";

function message(code: string, at: number, from = "A <one@a.test>"): MailMessage {
  return { from, subject: "Your sign-in code", snippet: `Your code is ${code}.`, receivedAt: at };
}

function clock(): { now: () => number; sleep: (ms: number) => Promise<void> } {
  let time = T0;
  return {
    now: () => time,
    sleep: async (ms) => {
      time += ms;
    },
  };
}

function lookup(
  overrides: {
    allowed?: boolean;
    messages?: MailMessage[] | null;
    unreadable?: boolean;
    stop?: boolean;
    lookMs?: number;
    reading?: TeachCodeState[];
  } = {},
) {
  const states: TeachCodeState[] = overrides.reading ?? [];
  const read = vi.fn(async () => {
    if (overrides.unreadable === true) throw new Error("Gmail is unreachable");
    return overrides.messages === undefined ? [] : overrides.messages;
  });
  const say = vi.fn((state: TeachCodeState) => states.push(state));
  return {
    states,
    read,
    say,
    deps: {
      allowed: async () => overrides.allowed ?? true,
      read,
      ...clock(),
      stop: () => overrides.stop ?? false,
      say,
    },
  };
}

describe("reading the code for a person who is teaching", () => {
  it("says it is reading, then hands back the one code, without putting it in a state", async () => {
    const run = lookup({ messages: [message("482913", T0 + 1_000)] });

    const answer = await codeForTeaching(run.deps, { since: T0, site: SITE, lookMs: 20_000 });

    expect(answer).toEqual({ status: "found", code: "482913" });
    expect(run.states[0]).toEqual({ state: "looking" });
    expect(JSON.stringify(run.states)).not.toContain("482913");
  });

  it("asks for a yes first, and does not read Gmail when it has none", async () => {
    const run = lookup({ allowed: false, messages: [message("482913", T0 + 1_000)] });

    const answer = await codeForTeaching(run.deps, { since: T0, site: SITE, lookMs: 20_000 });

    expect(answer).toEqual({ status: "not_allowed" });
    expect(run.read).not.toHaveBeenCalled();
    expect(run.states).toEqual([{ state: "offer" }]);
  });

  it("says two codes could not be told apart rather than guessing", async () => {
    const run = lookup({
      messages: [message("111111", T0 + 1_000, "A <one@a.test>"), message("222222", T0 + 2_000, "B <two@b.test>")],
    });

    const answer = await codeForTeaching(run.deps, { since: T0, site: SITE, lookMs: 20_000 });

    expect(answer).toEqual({ status: "ambiguous" });
    expect(run.states.at(-1)).toEqual({ state: "missed", reason: "ambiguous" });
  });

  it("says there was no code email yet", async () => {
    const run = lookup({ messages: [] });

    const answer = await codeForTeaching(run.deps, { since: T0, site: SITE, lookMs: 20_000 });

    expect(answer).toEqual({ status: "none" });
    expect(run.states.at(-1)).toEqual({ state: "missed", reason: "none" });
  });

  it("says at once when this Chrome has no Gmail", async () => {
    const run = lookup({ messages: null });

    const answer = await codeForTeaching(run.deps, { since: T0, site: SITE, lookMs: 20_000 });

    expect(answer).toEqual({ status: "no_gmail" });
    expect(run.states.at(-1)).toEqual({ state: "missed", reason: "no_gmail" });
  });

  it("says the code could not be read rather than leaving the panel waiting", async () => {
    const run = lookup({ unreadable: true });

    const answer = await codeForTeaching(run.deps, { since: T0, site: SITE, lookMs: 20_000 });

    expect(answer).toEqual({ status: "could_not_read" });
    expect(run.states.at(-1)).toEqual({ state: "missed", reason: "could_not_read" });
  });

  it("says nothing once the recording has stopped", async () => {
    const run = lookup({ stop: true });

    const answer = await codeForTeaching(run.deps, { since: T0, site: SITE, lookMs: 20_000 });

    expect(answer).toEqual({ status: "stopped" });
    expect(run.say).not.toHaveBeenCalled();
  });

  it("turns the page's answer into the panel's last word", () => {
    expect(enteredCodeState(true)).toEqual({ state: "typed" });
    expect(enteredCodeState(false)).toEqual({ state: "missed", reason: "not_found" });
  });
});
