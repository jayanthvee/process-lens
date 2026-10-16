/**
 * The two things the shell has to say before any page says anything: an admin paused runs, or this
 * person's Chrome extension is too old to keep going.
 *
 * Each is one slim bar at the top of every page, and only while it is true. A bar that is merely
 * empty when nothing is wrong would be furniture (DESIGN.md §5: panels are for groups of rows, never
 * for decoration), and a failed read is not a thing to report -- the API's own gate still refuses a
 * run it will not accept, so there is nothing a person needs to do about this page not knowing.
 *
 * Neither bar carries a button: the sentence is the whole message, and the page's own action stays
 * the one primary button on the screen (DESIGN.md §1).
 */

import { useWorkspaceState } from "../lib/workspace";

interface BarProps {
  /** The state colour, and the word beside its dot -- a colour alone never carries the meaning. */
  word: string;
  sentence: string;
  /** Only the pause bar announces itself: it reports a change, the other is a standing reminder. */
  announce?: boolean;
}

function Bar({ word, sentence, announce = false }: BarProps): JSX.Element {
  return (
    <div className="banner" role={announce ? "status" : undefined}>
      {/* `help` is the design's "needs your help" colour, and both of these are that: something is
          waiting on a person. `live` would say "running", which is the opposite of the news. */}
      <span className="res help">{word}</span>
      <p>{sentence}</p>
    </div>
  );
}

export function Banners(): JSX.Element | null {
  const workspace = useWorkspaceState();
  const frozen = workspace.data?.runs_frozen === true;
  const stale = workspace.data?.extension_out_of_date === true;

  if (!frozen && !stale) return null;

  return (
    <div className="banners">
      {/* Paused first: it is why nothing will start, whatever the extension is doing. */}
      {frozen ? <Bar word="Paused" sentence="An admin paused all runs. Nothing new will start." announce /> : null}
      {stale ? (
        <Bar
          word="Out of date"
          sentence="Your Chrome extension is out of date. Update it to keep running tasks."
        />
      ) : null}
    </div>
  );
}
