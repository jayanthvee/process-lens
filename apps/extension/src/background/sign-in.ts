// PL-112 item 4: a recorded page that asks for an emailed code makes the demonstration a sign-in.
//
// The record is kept on the recorder's status, which is what the panel reads when it saves the
// task. It is a fact about the recording, not about the code: no code, field or address is stored
// here.
import type { RecorderStatus } from "../shared/messages";

/** Mark the recording as a sign-in, keeping every other field as it is. */
export function markSignIn(status: RecorderStatus): RecorderStatus {
  return status.signIn === true ? status : { ...status, signIn: true };
}

/** A new teaching, or a cleared one, is not a sign-in until a code page says so. */
export function clearSignIn(status: RecorderStatus): RecorderStatus {
  return status.signIn === false || status.signIn === undefined
    ? status
    : { ...status, signIn: false };
}
