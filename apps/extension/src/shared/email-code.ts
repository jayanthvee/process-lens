/**
 * PL-102: the one-time code a site emailed the person, read from their own Gmail in this Chrome.
 *
 * Three pure parts: reading Gmail's inbox feed, choosing the one code to type, and keeping codes
 * out of anything ProcessLens writes down. Nothing here logs, stores or returns more than the one
 * code, and nothing calls a model (D-017): the choice is a fixed set of rules.
 */

export interface MailMessage {
  /** "Name <address>", or the address alone. */
  from: string;
  subject: string;
  snippet: string;
  /** Milliseconds since the epoch. */
  receivedAt: number;
}

export type CodeAnswer =
  | { status: "found"; code: string }
  | { status: "none" }
  | { status: "ambiguous" };

/** Words that make a message, or a piece of text, about a one-time code. */
const CODE_WORDS =
  /\b(verification|verify|one[- ]time|passcode|otp|security code|login code|sign[- ]in code|log[- ]in code|confirmation code|code)\b/i;

/**
 * Gmail's clock and this computer's may differ; a code sent this much before the page counts.
 *
 * PL-119: ten seconds, not a minute. The owner's run read Gmail before the new email arrived and
 * found the code emailed during teaching, 28 s before the code page, still "fresh". A code that
 * arrived before the page the person is looking at is not the code the site just sent.
 */
export const CLOCK_SLACK_MS = 10_000;

/**
 * A run of 4-8 digits standing alone, or Google's `G-123456`. A run joined to more digits by `-`,
 * `/`, `:` or `.` is part of a date, a time, a phone number or an amount; one after a currency sign
 * is a price.
 */
const CANDIDATE = /(^|[^0-9A-Za-z])(G-)?(\d{4,8})(?![0-9A-Za-z])/g;

function candidates(text: string): string[] {
  const found: string[] = [];
  for (const match of text.matchAll(CANDIDATE)) {
    const lead = match[1] ?? "";
    const google = match[2] ?? "";
    const digits = match[3] ?? "";
    const start = (match.index ?? 0) + lead.length + google.length;
    const end = start + digits.length;
    const before = text.slice(Math.max(0, start - 2), start);
    const after = text.slice(end, end + 2);
    if (!google) {
      if (/[$€£¥₹]\s?$/.test(before)) continue;
      if (/\d[-/:.]$/.test(before) || /^[-/:.,]\d/.test(after)) continue;
    }
    found.push(digits);
  }
  return found;
}

/** The code in one message, or null. The subject is read first: many senders put it there. */
export function codeInMessage(message: MailMessage): string | null {
  for (const text of [message.subject, message.snippet]) {
    if (!CODE_WORDS.test(text) && !CODE_WORDS.test(message.subject)) continue;
    const found = candidates(text);
    if (found.length > 0) return found[0]!;
  }
  return null;
}

/** The words of a site's address that a sender would use: `app.gohighlevel.com` → `gohighlevel`. */
function siteWords(site: string): string[] {
  const host = site.replace(/^[a-z]+:\/\//i, "").split(/[/:]/)[0]!.toLowerCase();
  const generic = new Set(["www", "app", "my", "login", "auth", "accounts", "secure", "portal"]);
  const parts = host.split(".");
  // The last part is the top-level domain (and a two-letter country part before it, as in co.uk).
  const named = parts.slice(0, -1).filter((part) => part.length >= 3 && !generic.has(part));
  return named;
}

function namesSite(message: MailMessage, words: string[]): boolean {
  const said = `${message.from} ${message.subject}`.toLowerCase().replace(/[^a-z0-9]/g, "");
  return words.some((word) => said.includes(word.replace(/[^a-z0-9]/g, "")));
}

/** Who sent it, by address when there is one, so "Name <a@b>" and "a@b" are the same sender. */
function senderOf(message: MailMessage): string {
  const address = /<([^>]+)>/.exec(message.from)?.[1] ?? message.from;
  return address.trim().toLowerCase();
}

/**
 * The one code to type, from messages that arrived after the code page appeared.
 *
 * A message whose sender or subject names the site wins, newest first. With none, the only code
 * there is wins (a white-label sign-in such as "NCPL One" does not name GoHighLevel). Different codes
 * from different senders, none naming the site, are `ambiguous`: ProcessLens does not guess.
 */
export function findCode(
  messages: MailMessage[],
  options: { since: number; site: string },
): CodeAnswer {
  const fresh = messages
    .filter((message) => message.receivedAt >= options.since - CLOCK_SLACK_MS)
    .map((message) => ({ message, code: codeInMessage(message) }))
    .filter((entry): entry is { message: MailMessage; code: string } => entry.code !== null)
    .sort((a, b) => b.message.receivedAt - a.message.receivedAt);
  if (fresh.length === 0) return { status: "none" };

  const words = siteWords(options.site);
  const ours = fresh.find((entry) => namesSite(entry.message, words));
  if (ours) return { status: "found", code: ours.code };

  // The newest code from each sender: a person who asked twice has two codes from one sender, and
  // only the newest one works.
  const bySender = new Map<string, string>();
  for (const entry of fresh) {
    const sender = senderOf(entry.message);
    if (!bySender.has(sender)) bySender.set(sender, entry.code);
  }
  const codes = new Set(bySender.values());
  if (codes.size === 1) return { status: "found", code: fresh[0]!.code };
  return { status: "ambiguous" };
}

/** Text that talks about a code loses its 4-8 digit runs, so no code reaches a recording. */
export function scrubCodes(text: string): string {
  if (!CODE_WORDS.test(text)) return text;
  return text.replace(/(^|[^0-9A-Za-z])(G-)?\d{4,8}(?![0-9A-Za-z])/g, "$1…");
}

const ENTITIES: Record<string, string> = {
  amp: "&",
  lt: "<",
  gt: ">",
  quot: '"',
  apos: "'",
};

function decode(text: string): string {
  return text
    .replace(/<!\[CDATA\[([\s\S]*?)\]\]>/g, "$1")
    .replace(/&#x([0-9a-f]+);/gi, (_, hex: string) => String.fromCodePoint(parseInt(hex, 16)))
    .replace(/&#(\d+);/g, (_, dec: string) => String.fromCodePoint(Number(dec)))
    .replace(/&([a-z]+);/gi, (whole, name: string) => ENTITIES[name.toLowerCase()] ?? whole)
    .replace(/\s+/g, " ")
    .trim();
}

function tag(block: string, name: string): string {
  const match = new RegExp(`<${name}(?:\\s[^>]*)?>([\\s\\S]*?)</${name}>`, "i").exec(block);
  return match ? decode(match[1]!) : "";
}

/**
 * Gmail's inbox feed (`/mail/u/<n>/feed/atom`) as messages. Plain string reading, because a service
 * worker has no DOMParser. Anything that is not the feed (a sign-in page) reads as no messages.
 */
export function parseGmailFeed(xml: string): MailMessage[] {
  if (!/<feed[\s>]/i.test(xml)) return [];
  const messages: MailMessage[] = [];
  for (const match of xml.matchAll(/<entry(?:\s[^>]*)?>([\s\S]*?)<\/entry>/gi)) {
    const block = match[1]!;
    const author = tag(block, "author");
    const name = tag(block, "name");
    const email = tag(block, "email");
    const when = Date.parse(tag(block, "issued") || tag(block, "modified"));
    if (!Number.isFinite(when)) continue;
    messages.push({
      from: name && email ? `${name} <${email}>` : email || name || author,
      subject: tag(block, "title"),
      snippet: tag(block, "summary"),
      receivedAt: when,
    });
  }
  return messages;
}

/** The webmail hosts a person reads codes from. A plain search page is not mail. */
const WEBMAIL_HOSTS = new Set([
  "mail.google.com",
  "outlook.live.com",
  "outlook.office.com",
  "mail.yahoo.com",
]);

/**
 * PL-107: whether a URL is a person's own inbox. Teaching never records the inbox tab: going to
 * Gmail/Outlook/Yahoo for a code is not a step of the task being taught.
 */
export function isEmailHost(url: string): boolean {
  let hostname = "";
  try {
    hostname = new URL(url).hostname.toLowerCase();
  } catch {
    return false;
  }
  return WEBMAIL_HOSTS.has(hostname);
}
