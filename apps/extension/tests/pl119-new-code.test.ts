// PL-119: a run types the new emailed code, never one already used.
//
// The owner's run (fc15039b): Goodlabs emailed code A while the task was being taught, and
// ProcessLens typed it. The run started a minute later, read Gmail before code B arrived, found A
// as the only fresh code and typed it again; Goodlabs answered "Verification failed". These tests
// use that shape: sender "Goodlabs <team@...>", subject "Your Login Code", a six-digit code.
import { describe, expect, it } from "vitest";

import { lookForCode } from "../src/background/code-lookup";
import { typeEmailedCode } from "../src/background/code-entry";
import { codeForTeaching } from "../src/background/teach-code";
import { SaltedCodeMemory, USED_CODE_TTL_MS } from "../src/background/used-codes";
import { CLOCK_SLACK_MS, findCode, type MailMessage } from "../src/shared/email-code";

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
  set: (at: number) => void;
}

function clock(from: number): Clock {
  let time = from;
  return {
    now: () => time,
    sleep: async (ms) => {
      time += ms;
    },
    set: (at) => {
      time = at;
    },
  };
}

/** What the inbox held at the moment of the read: a later email has not arrived yet. */
function inbox(messages: MailMessage[], time: { now: () => number }): () => Promise<MailMessage[]> {
  return async () => messages.filter((message) => message.receivedAt <= time.now());
}

describe("only a code that arrived after the code page counts", () => {
  it("allows ten seconds of clock difference, and no more", () => {
    expect(CLOCK_SLACK_MS).toBe(10_000);
    expect(findCode([mail("111111", T0 - 9_000)], { since: T0, site: SITE })).toEqual({
      status: "found",
      code: "111111",
    });
    expect(findCode([mail("222222", T0 - 11_000)], { since: T0, site: SITE })).toEqual({
      status: "none",
    });
  });
});

describe("a code typed while teaching is remembered", () => {
  it("types only the new code in the run, not the teaching code", async () => {
    const time = clock(T0 - 20_000);
    const used = new SaltedCodeMemory({ salt: "teaching-salt", now: time.now });

    // Teaching: the code page is watched from the last action, less 30 s of slack. A arrived at
    // 01:18:20 minus 28 s, so the teaching look types A and hands it to the page.
    const taught = await codeForTeaching(
      {
        allowed: async () => true,
        read: inbox([mail("111111", T0 - 28_000)], time),
        now: time.now,
        sleep: time.sleep,
        stop: () => false,
        say: () => undefined,
        used,
      },
      { since: T0 - 30_000, site: SITE, lookMs: 5_000 },
    );

    expect(taught).toEqual({ status: "found", code: "111111" });
    expect(await used.isUsed("111111")).toBe(true);

    // The run starts a minute later. B arrives 8 s after its code page; A is older and already
    // typed, so the run must wait for B.
    time.set(T0);
    const typed: string[] = [];
    const answer = await typeEmailedCode(
      {
        read: inbox([mail("111111", T0 - 28_000), mail("222222", T0 + 8_000)], time),
        now: time.now,
        sleep: time.sleep,
        enter: async (code) => {
          typed.push(code);
          return { entered: true, reason: "submitted", cleared: true };
        },
        used,
      },
      { since: T0, site: SITE, lookMs: 30_000, stop: () => false },
    );

    expect(typed).toEqual(["222222"]);
    expect(answer).toEqual({ entered: true, reason: "submitted", cleared: true });
  });

  it("skips a code typed a moment earlier even inside the ten seconds", async () => {
    const time = clock(T0);
    const used = new SaltedCodeMemory({ salt: "moment-salt", now: time.now });
    await used.remember("111111");

    const answer = await lookForCode(
      {
        read: inbox([mail("111111", T0 - 5_000), mail("222222", T0 + 8_000)], time),
        now: time.now,
        sleep: time.sleep,
        isUsed: (code) => used.isUsed(code),
      },
      { since: T0, site: SITE, lookMs: 30_000, stop: () => false },
    );

    expect(answer).toEqual({ status: "found", code: "222222" });
  });
});

describe("worker restarted, nothing remembered", () => {
  it("waits for the new email because the old one is outside the ten seconds", async () => {
    const time = clock(T0);
    const reads: number[] = [];

    const answer = await lookForCode(
      {
        read: async () => {
          reads.push(time.now());
          return [mail("111111", T0 - 28_000), mail("222222", T0 + 8_000)].filter(
            (message) => message.receivedAt <= time.now(),
          );
        },
        now: time.now,
        sleep: time.sleep,
      },
      { since: T0, site: SITE, lookMs: 30_000, stop: () => false },
    );

    expect(answer).toEqual({ status: "found", code: "222222" });
    expect(reads.length).toBeGreaterThan(1);
  });
});

describe("the memory of typed codes", () => {
  it("forgets an entry after fifteen minutes", async () => {
    let time = T0;
    const used = new SaltedCodeMemory({ salt: "ttl-salt", now: () => time });
    await used.remember("111111");

    expect(await used.isUsed("111111")).toBe(true);
    time += USED_CODE_TTL_MS;
    expect(await used.isUsed("111111")).toBe(false);
  });
});
