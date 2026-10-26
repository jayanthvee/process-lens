// PL-102: the look in Gmail for an emailed code, driven by a fake inbox and a fake clock.
import { describe, expect, it, vi } from "vitest";

import { lookForCode, readGmailFeeds } from "../src/background/code-lookup";
import type { MailMessage } from "../src/shared/email-code";

const T0 = Date.parse("2026-10-25T10:00:00Z");
const site = "https://app.gohighlevel.com/login";

function code(digits: string, at: number, from = "NCPL One <no-reply@ncplone.example>"): MailMessage {
  return { from, subject: "Your login code", snippet: `Your code is ${digits}.`, receivedAt: at };
}

function clock(): { now: () => number; sleep: (ms: number) => Promise<void> } {
  let time = T0;
  return {
    now: () => time,
    sleep: async (ms: number) => {
      time += ms;
    },
  };
}

describe("looking for the code", () => {
  it("waits for the email to arrive, then takes its code", async () => {
    const time = clock();
    const inbox: MailMessage[] = [];
    const read = vi.fn(async () => {
      if (time.now() >= T0 + 10_000) inbox.splice(0, inbox.length, code("482913", T0 + 9_000));
      return [...inbox];
    });

    const answer = await lookForCode(
      { read, ...time },
      { since: T0, site, lookMs: 60_000, stop: () => false },
    );

    expect(answer).toEqual({ status: "found", code: "482913" });
    expect(read).toHaveBeenCalledTimes(3);
  });

  it("gives up when the time is up", async () => {
    const answer = await lookForCode(
      { read: async () => [], ...clock() },
      { since: T0, site, lookMs: 20_000, stop: () => false },
    );

    expect(answer).toEqual({ status: "none" });
  });

  it("says at once when there is no Gmail in this Chrome", async () => {
    const read = vi.fn(async () => null);

    const answer = await lookForCode(
      { read, ...clock() },
      { since: T0, site, lookMs: 60_000, stop: () => false },
    );

    expect(answer).toEqual({ status: "no_gmail" });
    expect(read).toHaveBeenCalledTimes(1);
  });

  it("ends ambiguous when two codes nobody can tell apart never get a third", async () => {
    const both = [
      code("111111", T0 + 1_000),
      code("222222", T0 + 2_000, "Bank <alerts@bank.example>"),
    ];

    const answer = await lookForCode(
      { read: async () => both, ...clock() },
      { since: T0, site, lookMs: 15_000, stop: () => false },
    );

    expect(answer).toEqual({ status: "ambiguous" });
  });

  it("stops when the run is cancelled", async () => {
    let stopped = false;
    const read = vi.fn(async () => {
      stopped = true;
      return [];
    });

    const answer = await lookForCode(
      { read, ...clock() },
      { since: T0, site, lookMs: 60_000, stop: () => stopped },
    );

    expect(answer).toEqual({ status: "stopped" });
  });
});

const FEED = (title: string) =>
  `<feed xmlns="http://purl.org/atom/ns#"><entry><title>${title}</title><summary>Your code is 482913</summary>` +
  "<issued>2026-10-25T10:01:00Z</issued><author><name>NCPL One</name><email>a@b.example</email></author></entry></feed>";

describe("reading every signed-in Gmail", () => {
  it("reads each account until one is not signed in", async () => {
    const fetcher = vi.fn(async (url: string, _init: RequestInit) => {
      const account = Number(/\/u\/(\d)\//.exec(url)![1]);
      const text = account < 2 ? FEED(`Account ${account} code`) : "<html>Sign in</html>";
      return { ok: true, text: async () => text };
    });

    const messages = await readGmailFeeds(fetcher);

    expect(messages?.map((message) => message.subject)).toEqual([
      "Account 0 code",
      "Account 1 code",
    ]);
    expect(fetcher).toHaveBeenCalledTimes(3);
    expect(fetcher.mock.calls[0]![1]).toMatchObject({ credentials: "include", cache: "no-store" });
  });

  it("is null when no account is signed in", async () => {
    const fetcher = async () => ({ ok: true, text: async () => "<html>Sign in</html>" });

    expect(await readGmailFeeds(fetcher)).toBeNull();
  });

  it("is null when Gmail cannot be reached", async () => {
    const fetcher = async () => {
      throw new TypeError("Failed to fetch");
    };

    expect(await readGmailFeeds(fetcher)).toBeNull();
  });
});
