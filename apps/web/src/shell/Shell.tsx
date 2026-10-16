import type { ReactNode } from "react";
import { Link, Outlet, useLocation } from "react-router-dom";

import { Banners } from "./Banners";
import { Header } from "./Header";
import { Icon, IconSprite } from "./Icons";
import { Sidebar } from "./Sidebar";

interface PageChrome {
  title: string;
  /** The page's one primary action. At most one screen has one, and it is the near-black button. */
  action?: () => ReactNode;
  match: (path: string) => boolean;
}

/**
 * The one primary action this ticket can offer: teaching the next task opens the Teach page. Pages
 * that own their own primary button leave this unset.
 */
function teachAction(): ReactNode {
  return (
    <Link className="btn primary" to="/teach">
      <Icon name="plus" />
      Teach a new task
    </Link>
  );
}

/* The order matters: a detail route is tested before its list route, and the run flow before the
   task page it belongs to. */
const PAGES: PageChrome[] = [
  { match: (path) => path === "/", title: "Home", action: teachAction },
  { match: (path) => path === "/needs-help", title: "Needs help" },
  { match: (path) => /^\/tasks\/[^/]+\/run$/.test(path), title: "Run this task" },
  { match: (path) => path.startsWith("/tasks/"), title: "Task" },
  { match: (path) => path === "/tasks", title: "Tasks", action: teachAction },
  { match: (path) => path.startsWith("/runs/"), title: "Run" },
  { match: (path) => path === "/runs", title: "Runs" },
  { match: (path) => path === "/teach", title: "Teach a new task" },
  { match: (path) => path === "/spreadsheets", title: "Spreadsheets" },
  { match: (path) => path === "/documents", title: "Documents" },
  { match: (path) => path === "/saved-answers", title: "Saved answers" },
  { match: (path) => path === "/apps", title: "Apps" },
  { match: (path) => path === "/settings", title: "Settings" },
  { match: (path) => path === "/help", title: "Help" },
];

/**
 * The app shell: the sidebar and the content column, with the header above whichever page the
 * router chose.
 */
export function Shell(): JSX.Element {
  const { pathname } = useLocation();
  const page = PAGES.find((entry) => entry.match(pathname));
  const action = page?.action;

  return (
    <div className="app">
      <IconSprite />
      <Sidebar />
      <main>
        <Header title={page?.title ?? "ProcessLens"} action={action === undefined ? null : action()} />
        <Banners />
        <Outlet />
      </main>
    </div>
  );
}
