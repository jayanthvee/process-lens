// PL-107 item 12: the worker's half of a code page reached while the person teaches.
//
// The same rules a run follows (PL-102): the person's yes comes first, the inbox is read for a
// little while, one code is chosen, and nothing about it is kept. The only difference is where
// the code goes -- back to the page that is being taught, which types it exactly as a run does.
import { lookForCode } from "./code-lookup";
import type { MailMessage } from "../shared/email-code";
import type { TeachCodeAnswer, TeachCodeState } from "../shared/messages";

/** How long a teaching-time look waits for the email, the same as a run's look. */
export const TEACH_CODE_LOOK_MS = 60_000;

export interface TeachCodeLookupDeps {
  /** Whether the person has said yes to reading codes from their Gmail in this Chrome. */
  allowed: () => Promise<boolean>;
  /** Every signed-in inbox's messages, or null when this Chrome has no Gmail. */
  read: () => Promise<MailMessage[] | null>;
  now: () => number;
  sleep: (ms: number) => Promise<void>;
  /** True once the recording has stopped; a stopped look types nothing. */
  stop: () => boolean;
  /** Put one plain state in front of the person, for the panel to show. Never the code. */
  say: (state: TeachCodeState) => void;
}

export interface TeachCodeAsk {
  /** When the code page appeared. */
  since: number;
  /** The address of the page asking for the code, for choosing whose email it is. */
  site: string;
  lookMs?: number;
}

/**
 * Read the one code a site emailed, or say why there is none.
 *
 * `found` means the page may type the code now; `not_allowed` means the panel should offer the
 * PL-102 yes; `none`, `ambiguous` and `no_gmail` mean the person types it themselves. The one
 * code is in the answer and nowhere else: not in a panel state, not in a log line.
 */
export async function codeForTeaching(
  deps: TeachCodeLookupDeps,
  ask: TeachCodeAsk,
): Promise<TeachCodeAnswer> {
  if (deps.stop()) return { status: "stopped" };
  if (!(await deps.allowed())) {
    deps.say({ state: "offer" });
    return { status: "not_allowed" };
  }
  deps.say({ state: "looking" });
  let answer: Awaited<ReturnType<typeof lookForCode>>;
  try {
    answer = await lookForCode(
      { read: deps.read, now: deps.now, sleep: deps.sleep },
      {
        since: ask.since,
        site: ask.site,
        lookMs: ask.lookMs ?? TEACH_CODE_LOOK_MS,
        stop: deps.stop,
      },
    );
  } catch {
    // A read that fails is not a missing email: say it could not be read, and let the person type.
    deps.say({ state: "missed", reason: "could_not_read" });
    return { status: "could_not_read" };
  }
  if (answer.status === "found") {
    // The lookup is done; the page's own report changes this if it could not type the code.
    deps.say({ state: "typed" });
    return answer;
  }
  if (answer.status === "stopped") return { status: "stopped" };
  deps.say({ state: "missed", reason: answer.status });
  return { status: answer.status };
}

/** The panel's last word after the page tried to type the code. Never carries the code. */
export function enteredCodeState(entered: boolean): TeachCodeState {
  return entered ? { state: "typed" } : { state: "missed", reason: "not_found" };
}
