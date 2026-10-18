/**
 * "This task stopped working the last two times (...). Record it again in the extension to fix it."
 *
 * A saved task never changes, so a task recorded before a fix keeps failing the same way and every
 * run needs a person. The task list and the run page both say so (PL-041), in the reason's own
 * plain words, and offer the one action that fixes it rather than sending the person elsewhere to
 * work out what to do.
 */

import { Link } from "react-router-dom";

import { plainReason } from "../lib/words";
import type { KeepsFailing } from "../types";
import "./keepsFailingWarning.css";

interface KeepsFailingWarningProps {
  /** The API's own `keeps_failing`: the reason both of the last two runs stopped for. */
  keepsFailing: KeepsFailing;
}

/** The reason as the sentence's aside: `plainReason`'s sentence without its closing full stop. */
function aside(reason: string): string {
  return plainReason(reason).replace(/\.$/, "");
}

export function KeepsFailingWarning({ keepsFailing }: KeepsFailingWarningProps): JSX.Element {
  return (
    <div className="keeps-failing" role="status">
      <p className="keeps-failing-sentence">
        This task stopped working the last two times ({aside(keepsFailing.reason)}). Record it again
        in the extension to fix it.
      </p>
      <Link className="btn quiet keeps-failing-teach" to="/teach">
        How to record it again
      </Link>
    </div>
  );
}
