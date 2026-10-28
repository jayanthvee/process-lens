// PL-102: a run that reaches a "we emailed you a code" page reads the code from the person's own
// Gmail in this Chrome, so nobody has to open their inbox, copy six digits and paste them back.
//
// These are the pure parts: reading Gmail's feed, choosing the one code to type, and keeping codes
// out of anything ProcessLens writes down. None of them ever logs, stores or returns more than the
// one code.

import { afterEach, describe, expect, it } from "vitest";

import { findCode, parseGmailFeed, scrubCodes, type MailMessage } from "../src/shared/email-code";

const T0 = Date.parse("2026-10-25T10:00:00Z");
const MIN = 60_000;

function mail(over: Partial<MailMessage>): MailMessage {
  return {
    from: "NCPL One <no-reply@ncplone.example>",
    subject: "Your login verification code",
    snippet: "Your verification code is 482913. It expires in 10 minutes.",
    receivedAt: T0 + MIN,
    ...over,
  };
}

const FEED = `<?xml version="1.0" encoding="UTF-8"?>
<feed version="0.3" xmlns="http://purl.org/atom/ns#">
<title>Gmail - Inbox for person@example.test</title>
<fullcount>2</fullcount>
<entry>
<title>Your NCPL One Login Verification Code</title>
<summary>Hi Lakshmi, your code is 982707. Do not share it &amp; it expires in 10 minutes.</summary>
<issued>2026-10-25T10:01:00Z</issued>
<author><name>NCPL One</name><email>no-reply@ncplone.example</email></author>
</entry>
<entry>
<title>Team lunch &lt;Friday&gt;</title>
<summary>Who&#39;s in?</summary>
<issued>2026-10-25T09:00:00Z</issued>
<author><name>Sam</name><email>sam@example.test</email></author>
</entry>
</feed>`;

describe("reading Gmail's feed", () => {
  const realParser = globalThis.DOMParser;
  afterEach(() => {
    globalThis.DOMParser = realParser;
  });

  it("reads each message's sender, subject, snippet and time", () => {
    const messages = parseGmailFeed(FEED);

    expect(messages).toHaveLength(2);
    expect(messages[0]).toEqual({
      from: "NCPL One <no-reply@ncplone.example>",
      subject: "Your NCPL One Login Verification Code",
      snippet: "Hi Lakshmi, your code is 982707. Do not share it & it expires in 10 minutes.",
      receivedAt: Date.parse("2026-10-25T10:01:00Z"),
    });
    expect(messages[1]!.subject).toBe("Team lunch <Friday>");
    expect(messages[1]!.snippet).toBe("Who's in?");
  });

  it("works without DOMParser, which a service worker does not have", () => {
    // @ts-expect-error -- the service worker has no DOMParser; the reader must not need one.
    delete globalThis.DOMParser;

    expect(parseGmailFeed(FEED)).toHaveLength(2);
  });

  it("reads an empty or broken feed as no messages", () => {
    expect(parseGmailFeed("")).toEqual([]);
    expect(parseGmailFeed("<html>Sign in</html>")).toEqual([]);
  });
});

describe("choosing the code", () => {
  const site = "app.gohighlevel.com";

  it("takes the code from a message that arrived after the code page appeared", () => {
    expect(findCode([mail({})], { since: T0, site })).toEqual({ status: "found", code: "482913" });
  });

  it("ignores a code that arrived before the code page appeared", () => {
    const old = mail({ receivedAt: T0 - 10 * MIN });

    expect(findCode([old], { since: T0, site })).toEqual({ status: "none" });
  });

  it("allows ten seconds of clock difference between Gmail and this computer", () => {
    const early = mail({ receivedAt: T0 - 9_000 });

    expect(findCode([early], { since: T0, site })).toEqual({ status: "found", code: "482913" });
  });

  it("ignores messages that do not talk about a code", () => {
    const invoice = mail({
      subject: "Invoice 20431",
      snippet: "Your order 553201 has shipped. Total $1299.00",
    });

    expect(findCode([invoice], { since: T0, site })).toEqual({ status: "none" });
  });

  it("does not take a date, a time, a price or a phone number for the code", () => {
    const busy = mail({
      subject: "Your security code",
      snippet: "Requested 2026-10-25 at 10:01 from 555-0142. Charge $4500. Your code: 731904",
    });

    expect(findCode([busy], { since: T0, site })).toEqual({ status: "found", code: "731904" });
  });

  it("reads Google's G-123456 style as the digits", () => {
    const google = mail({ subject: "G-552190 is your Google verification code", snippet: "" });

    expect(findCode([google], { since: T0, site })).toEqual({ status: "found", code: "552190" });
  });

  it("finds the code in the subject when the snippet has none", () => {
    const subjectOnly = mail({ subject: "Login code 4417", snippet: "Use it to sign in." });

    expect(findCode([subjectOnly], { since: T0, site })).toEqual({ status: "found", code: "4417" });
  });

  it("takes the newest code from the same sender when the person asked twice", () => {
    const first = mail({ snippet: "Your verification code is 111111.", receivedAt: T0 + MIN });
    const second = mail({ snippet: "Your verification code is 222222.", receivedAt: T0 + 2 * MIN });

    expect(findCode([first, second], { since: T0, site })).toEqual({
      status: "found",
      code: "222222",
    });
  });

  it("prefers the message whose sender or subject names the site", () => {
    const other = mail({
      from: "Bank <alerts@bank.example>",
      snippet: "Your verification code is 999999.",
      receivedAt: T0 + 2 * MIN,
    });
    const ours = mail({
      from: "HighLevel <noreply@gohighlevel.com>",
      snippet: "Your verification code is 123456.",
    });

    expect(findCode([other, ours], { since: T0, site })).toEqual({
      status: "found",
      code: "123456",
    });
  });

  it("takes the only code there is when no sender names the site (a white-label login)", () => {
    // NCPL's GoHighLevel sign-in emails come from "NCPL One", not from gohighlevel.
    expect(findCode([mail({})], { since: T0, site })).toEqual({ status: "found", code: "482913" });
  });

  it("does not guess between two different codes from senders that do not name the site", () => {
    const a = mail({ from: "NCPL One <a@ncplone.example>", snippet: "Your code is 111111." });
    const b = mail({ from: "Bank <alerts@bank.example>", snippet: "Your code is 222222." });

    expect(findCode([a, b], { since: T0, site })).toEqual({ status: "ambiguous" });
  });

  it("finds nothing in an empty inbox", () => {
    expect(findCode([], { since: T0, site })).toEqual({ status: "none" });
  });
});

describe("keeping codes out of what is written down", () => {
  it("removes the code from a step name made of an email's text", () => {
    const name =
      "Clicked unread, NCPL One, Your NCPL One Login Verification Code, Your code is 982707";

    const clean = scrubCodes(name);

    expect(clean).not.toContain("982707");
    expect(clean).toContain("NCPL One");
  });

  it("removes G-123456 style codes", () => {
    expect(scrubCodes("G-552190 is your Google verification code")).not.toContain("552190");
  });

  it("leaves numbers alone in text that is not about a code", () => {
    expect(scrubCodes("Order 553201 for Acme")).toBe("Order 553201 for Acme");
  });
});
