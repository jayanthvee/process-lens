// PL-119 item 3: after a typed code is refused, ProcessLens looks once more for a newer email and
// types that; never a third code for the same code page, and the pause is the same as today when
// nothing newer arrives.
import { describe, expect, it } from "vitest";

import { MAX_CODES_PER_PAGE, typeEmailedCode } from "../src/background/code-entry";
import { SaltedCodeMemory } from "../src/background/used-codes";
import type { MailMessage } from "../src/shared/email-code";

const T0 = Date.parse("2026-10-28T01:18:20Z");
const SITE = "https://app.goodlabs.example/login";
const FROM = "Goodlabs <team@goodlabs.example>";

function mail(digits: string, at: number): MailMessage {
  return {
    from: FROM,
    subject: "Your Login Code",
    snippet: `Your login code is ${digits}. It expires in 10 minutes.`,
    receivedAt: at,
  };
}

interface Clock {
  now: () => number;
  sleep: (ms: number) => Promise<void>;
}

function clock(from: number): Clock {
  let time = from;
  return {
    now: () => time,
    sleep: async (ms) => {
      time += ms;
    },
  };
}

describe("a refused code gets one more look", () => {
  it("types the newer code once after the first is refused, and no third", async () => {
    const time = clock(T0);
    const used = new SaltedCodeMemory({ salt: "refused-salt", now: time.now });
    const inbox = [
      mail("111111", T0 + 1_000),
      mail("222222", T0 + 8_000),
      mail("333333", T0 + 12_000),
    ];
    const typed: string[] = [];

    const answer = await typeEmailedCode(
      {
        read: async () => inbox.filter((message) => message.receivedAt <= time.now()),
        now: time.now,
        sleep: time.sleep,
        enter: async (code) => {
          typed.push(code);
          return { entered: true, reason: "submitted", cleared: code === "222222" };
        },
        used,
      },
      { since: T0, site: SITE, lookMs: 30_000, stop: () => false },
    );

    expect(MAX_CODES_PER_PAGE).toBe(2);
    expect(typed).toEqual(["111111", "222222"]);
    expect(answer).toEqual({ entered: true, reason: "submitted", cleared: true });
  });

  it("stops after two codes when the second is refused too", async () => {
    const time = clock(T0);
    const used = new SaltedCodeMemory({ salt: "twice-salt", now: time.now });
    const inbox = [
      mail("111111", T0 + 1_000),
      mail("222222", T0 + 8_000),
      mail("333333", T0 + 12_000),
    ];
    const typed: string[] = [];

    const answer = await typeEmailedCode(
      {
        read: async () => inbox.filter((message) => message.receivedAt <= time.now()),
        now: time.now,
        sleep: time.sleep,
        enter: async (code) => {
          typed.push(code);
          return { entered: true, reason: "submitted", cleared: false };
        },
        used,
      },
      { since: T0, site: SITE, lookMs: 30_000, stop: () => false },
    );

    expect(typed).toEqual(["111111", "222222"]);
    expect(answer).toEqual({ entered: true, reason: "submitted", cleared: false });
  });

  it("keeps today's pause when no newer code arrives", async () => {
    const time = clock(T0);
    const used = new SaltedCodeMemory({ salt: "none-salt", now: time.now });
    const typed: string[] = [];

    const answer = await typeEmailedCode(
      {
        read: async () => [mail("111111", T0 + 1_000)].filter((m) => m.receivedAt <= time.now()),
        now: time.now,
        sleep: time.sleep,
        enter: async (code) => {
          typed.push(code);
          return { entered: true, reason: "submitted", cleared: false };
        },
        used,
      },
      { since: T0, site: SITE, lookMs: 12_000, stop: () => false },
    );

    expect(typed).toEqual(["111111"]);
    expect(answer).toEqual({ entered: true, reason: "submitted", cleared: false });
  });

  it("does not type the same code again on a later look", async () => {
    const time = clock(T0);
    const used = new SaltedCodeMemory({ salt: "again-salt", now: time.now });
    const typed: string[] = [];
    const deps = {
      read: async () => [mail("111111", T0 + 1_000)].filter((m) => m.receivedAt <= time.now()),
      now: time.now,
      sleep: time.sleep,
      enter: async (code: string) => {
        typed.push(code);
        return { entered: true, reason: "submitted", cleared: true };
      },
      used,
    };

    await typeEmailedCode(deps, { since: T0, site: SITE, lookMs: 12_000, stop: () => false });
    const again = await typeEmailedCode(deps, {
      since: T0,
      site: SITE,
      lookMs: 1_000,
      stop: () => false,
    });

    expect(typed).toEqual(["111111"]);
    expect(again).toEqual({ entered: false, reason: "none" });
  });
});
