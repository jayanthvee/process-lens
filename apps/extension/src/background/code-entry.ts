// PL-119 item 3: read the emailed code and type it, with one more look when the site refuses it.
//
// A code page gets at most two codes. If the page still asks for a code after the first one is
// typed (`cleared` is false), that code is remembered as used and the look goes on for the rest of
// the same look time. A newer email is typed once. If there is none, or the second code is refused
// too, the answer is the last typing -- the run pauses exactly as it did before this ticket.
import { lookForCode } from "./code-lookup";
import type { CodeMemory } from "./used-codes";
import type { MailMessage } from "../shared/email-code";

/** The most codes ProcessLens types for one code page. */
export const MAX_CODES_PER_PAGE = 2;

/** What typing a code did. `cleared` says whether the code page went away afterwards. */
export interface TypedCode {
  entered: boolean;
  reason: string;
  cleared?: boolean;
}

export interface CodeEntryDeps {
  /** Every signed-in inbox's messages, or null when this Chrome has no Gmail. */
  read: () => Promise<MailMessage[] | null>;
  now: () => number;
  sleep: (ms: number) => Promise<void>;
  /** Type one code into the page. The answer never carries the code. */
  enter: (code: string) => Promise<TypedCode>;
  /** Salted hashes of codes already typed, during teaching or an earlier look. */
  used: CodeMemory;
}

export interface CodeEntryAsk {
  /** When the code page appeared. */
  since: number;
  /** The address of the page asking for the code. */
  site: string;
  /** How long the whole entry may take, including the second look. */
  lookMs: number;
  /** True once the run is cancelled or the command withdrawn. */
  stop: () => boolean;
}

/**
 * Find the one new code, type it, and -- when the page refuses it -- look once more.
 *
 * The used-code memory is shared with the teaching path, so a code typed while the task was taught
 * is never typed again in the run. The second look is bounded by the same deadline, so a site that
 * refuses both codes cannot keep the run waiting longer than the look it asked for.
 */
export async function typeEmailedCode(
  deps: CodeEntryDeps,
  ask: CodeEntryAsk,
): Promise<TypedCode> {
  const deadline = deps.now() + ask.lookMs;
  let last: TypedCode | null = null;

  for (let attempt = 0; attempt < MAX_CODES_PER_PAGE; attempt += 1) {
    if (ask.stop()) return last ?? { entered: false, reason: "stopped" };
    const remaining = deadline - deps.now();
    if (remaining <= 0) break;

    const answer = await lookForCode(
      {
        read: deps.read,
        now: deps.now,
        sleep: deps.sleep,
        isUsed: (code) => deps.used.isUsed(code),
      },
      { since: ask.since, site: ask.site, lookMs: remaining, stop: ask.stop },
    );
    if (answer.status !== "found") {
      // With a code already typed, its refusal is the honest answer: the run pauses the way it
      // did before this ticket, not with a new "no email" reason.
      return last ?? { entered: false, reason: answer.status };
    }

    const typed = await deps.enter(answer.code);
    if (typed.entered) await deps.used.remember(answer.code);
    last = typed;

    if (!typed.entered) return typed;
    // Only an explicit refusal earns the second look; an older harness with no `cleared` is done.
    if (typed.cleared !== false) return typed;
  }

  return last ?? { entered: false, reason: "none" };
}
