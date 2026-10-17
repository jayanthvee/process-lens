import { Route, Routes } from "react-router-dom";

import { Apps } from "./pages/Apps";
import { ComingSoon } from "./pages/ComingSoon";
import { Home } from "./pages/Home";
import { NeedsHelp } from "./pages/NeedsHelp";
import { RunDetail } from "./pages/RunDetail";
import { Runs } from "./pages/Runs";
import { TaskDetail } from "./pages/TaskDetail";
import { TaskRun } from "./pages/TaskRun";
import { Tasks } from "./pages/Tasks";
import { Teach } from "./pages/Teach";
import { Shell } from "./shell/Shell";

/**
 * The router. Every sidebar item lands on a real page: the ones this ticket does not build show a
 * designed empty state, so nothing is ever blank.
 *
 * `main.tsx` wraps this in a `BrowserRouter`; the tests wrap it in a `MemoryRouter`, which is why
 * the router lives only here.
 */
export function App(): JSX.Element {
  return (
    <Routes>
      <Route element={<Shell />}>
        <Route path="/" element={<Home />} />
        <Route path="/needs-help" element={<NeedsHelp />} />
        <Route path="/tasks" element={<Tasks />} />
        <Route path="/tasks/:id" element={<TaskDetail />} />
        {/* The run flow is its own page, tested by the router before the task page beside it. */}
        <Route path="/tasks/:id/run" element={<TaskRun />} />
        <Route path="/runs" element={<Runs />} />
        <Route path="/runs/:id" element={<RunDetail />} />
        <Route path="/teach" element={<Teach />} />
        <Route path="/spreadsheets" element={<ComingSoon />} />
        <Route path="/documents" element={<ComingSoon />} />
        <Route path="/saved-answers" element={<ComingSoon />} />
        <Route path="/apps" element={<Apps />} />
        <Route path="/settings" element={<ComingSoon />} />
        <Route path="/help" element={<ComingSoon />} />
        <Route path="*" element={<ComingSoon />} />
      </Route>
    </Routes>
  );
}
