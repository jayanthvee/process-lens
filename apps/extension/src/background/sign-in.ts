// PL-112 item 4: a recorded page that asks for an emailed code makes the demonstration a sign-in.
//
// PL-113 S2 adds the page the demonstration reached *after* the code page: a sign-in recipe ends by
// waiting for it, and the compiler can only write that wait when teaching recorded it. It is a page
// address, never a code: a code is never kept here, logged, or shown.
import type { RecorderStatus } from "../shared/messages";

/** Mark the recording as a sign-in, keeping every other field as it is. */
export function markSignIn(status: RecorderStatus): RecorderStatus {
  return status.signIn === true ? status : { ...status, signIn: true };
}

/**
 * Note the page a recording is on, for the sign-in's two addresses.
 *
 * The first page a sign-in recording reaches after it has been marked is the page the code was
 * asked on; the next different page is the one the site landed on after the code was accepted.
 * Both are page addresses the recording already carries -- never a code, never a field's value.
 * A recording that is not a sign-in, or a page already seen, changes nothing.
 */
export function noteSignInPage(
  status: RecorderStatus,
  url: string | null | undefined,
): RecorderStatus {
  const page = typeof url === "string" ? url.trim() : "";
  if (!page || status.signIn !== true) return status;
  if (!status.signInCodeUrl) return { ...status, signInCodeUrl: page };
  if (page === status.signInCodeUrl) return status;
  if (status.signInAfterUrl) return status;
  return { ...status, signInAfterUrl: page };
}

/** A new teaching, or a cleared one, is not a sign-in until a code page says so. */
export function clearSignIn(status: RecorderStatus): RecorderStatus {
  if (status.signIn === false || status.signIn === undefined) return status;
  const { signInCodeUrl: _code, signInAfterUrl: _after, ...rest } = status;
  return { ...rest, signIn: false };
}
