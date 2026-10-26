/**
 * PL-102: look in the person's Gmail for the code a site just emailed, for a little while.
 *
 * Gmail's inbox feed is read with the person's own Gmail sign-in in this Chrome, for each signed-in
 * account in turn (`/mail/u/0/`, `/mail/u/1/`, ...). Nothing is opened, marked read or kept: the
 * messages live for one look, and only the chosen code leaves this module.
 */

import { findCode, parseGmailFeed, type MailMessage } from "../shared/email-code";

export type LookupAnswer =
  | { status: "found"; code: string }
  | { status: "none" | "ambiguous" | "no_gmail" | "stopped" };

export interface LookupDeps {
  /** The messages in every signed-in Gmail inbox, or null when no Gmail answers in this Chrome. */
  read: () => Promise<MailMessage[] | null>;
  now: () => number;
  sleep: (ms: number) => Promise<void>;
}

export interface LookupOptions {
  /** When the code page appeared. */
  since: number;
  /** The address of the page asking for the code. */
  site: string;
  /** How long to keep looking for the email to arrive. */
  lookMs: number;
  /** How often to look again. */
  everyMs?: number;
  /** True once the run is cancelled or the command withdrawn. */
  stop: () => boolean;
}

/** How many signed-in Google accounts are tried, `u/0` to `u/4`. */
export const GMAIL_ACCOUNTS = 5;
const EVERY_MS = 5_000;

/**
 * Look until one code is found or the time is up. With no Gmail in this Chrome there is nothing
 * to wait for, so that is said at once. Two codes nobody can tell apart are not guessed between:
 * the look goes on in case the site's own email arrives, and ends `ambiguous` if it does not.
 */
export async function lookForCode(deps: LookupDeps, options: LookupOptions): Promise<LookupAnswer> {
  const every = options.everyMs ?? EVERY_MS;
  const deadline = deps.now() + options.lookMs;
  let last: "none" | "ambiguous" = "none";
  for (;;) {
    if (options.stop()) return { status: "stopped" };
    const messages = await deps.read();
    if (messages === null) return { status: "no_gmail" };
    const answer = findCode(messages, { since: options.since, site: options.site });
    if (answer.status === "found") return answer;
    last = answer.status;
    if (deps.now() + every > deadline) return { status: last };
    await deps.sleep(every);
  }
}

type Fetch = (url: string, init: RequestInit) => Promise<{ ok: boolean; text: () => Promise<string> }>;

/**
 * Every signed-in account's inbox feed. Accounts are numbered from 0 with no gaps, so the first
 * number that answers with something other than a feed is the end. None at all: null.
 */
export async function readGmailFeeds(fetcher: Fetch): Promise<MailMessage[] | null> {
  const messages: MailMessage[] = [];
  let feeds = 0;
  for (let account = 0; account < GMAIL_ACCOUNTS; account += 1) {
    let text = "";
    try {
      const response = await fetcher(`https://mail.google.com/mail/u/${account}/feed/atom`, {
        credentials: "include",
        cache: "no-store",
      });
      if (!response.ok) break;
      text = await response.text();
    } catch {
      break;
    }
    if (!/<feed[\s>]/i.test(text)) break;
    feeds += 1;
    messages.push(...parseGmailFeed(text));
  }
  return feeds === 0 ? null : messages;
}
