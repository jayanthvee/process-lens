import { useLocation } from "react-router-dom";

import { EmptyState } from "../shell/EmptyState";

/**
 * Every screen this ticket does not build yet: one sentence about what will appear here, and
 * "Coming soon". The sentence is chosen from the path, so the router only has to render this.
 */
const SENTENCES: Record<string, string> = {
  "/teach": "Teaching a new task from a recording is coming next.",
  "/spreadsheets": "Your spreadsheets and the problems found in them will appear here.",
  "/documents": "Your PDFs, and what was read from them, will appear here.",
  "/saved-answers": "The answers you save for later runs will appear here.",
  "/apps": "The sites each task may use will appear here.",
  "/help": "Short guides for each screen will appear here.",
};

const FALLBACK = "This screen will be built in a later release.";

export function ComingSoon(): JSX.Element {
  const { pathname } = useLocation();
  return <EmptyState sentence={SENTENCES[pathname] ?? FALLBACK} />;
}
