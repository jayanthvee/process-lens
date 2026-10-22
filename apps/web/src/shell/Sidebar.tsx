import { NavLink } from "react-router-dom";

import { useJson } from "../lib/useJson";
import { useSessionPath } from "../lib/browser";
import type { ExecutorSession, NeedsHelpRow } from "../types";
import { Icon, type IconName } from "./Icons";

interface Item {
  to: string;
  label: string;
  icon: IconName;
  /** Only Home lights up for exactly `/`, so a detail page does not light up two items at once. */
  end?: boolean;
}

const TOP: Item[] = [
  { to: "/", label: "Home", icon: "home", end: true },
  { to: "/needs-help", label: "Needs help", icon: "help" },
];

const AUTOMATE: Item[] = [
  { to: "/tasks", label: "Tasks", icon: "tasks" },
  { to: "/runs", label: "Runs", icon: "hist" },
];

const YOUR_DATA: Item[] = [
  { to: "/spreadsheets", label: "Spreadsheets", icon: "sheet" },
  { to: "/documents", label: "Documents", icon: "doc" },
  { to: "/saved-answers", label: "Saved answers", icon: "map" },
];

const CONNECT: Item[] = [{ to: "/apps", label: "Apps", icon: "apps" }];

const FOOT: Item[] = [
  { to: "/settings", label: "Settings", icon: "gear" },
  { to: "/help", label: "Help", icon: "q" },
];

function itemLink(item: Item, extra?: React.ReactNode): JSX.Element {
  return (
    <NavLink
      key={item.to}
      to={item.to}
      end={item.end === true}
      className={({ isActive }) => (isActive ? "on" : undefined)}
    >
      <Icon name={item.icon} />
      {item.label}
      {extra}
    </NavLink>
  );
}

function Logo(): JSX.Element {
  return (
    <NavLink to="/" className="logo">
      <svg viewBox="0 0 22 22" aria-hidden="true">
        <circle cx="11" cy="11" r="9" fill="none" stroke="currentColor" strokeWidth="1.6" />
        <path
          d="M5.5 13.5c2-3.4 4-5 5.5-5s3.5 1.6 5.5 5"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.6"
          strokeLinecap="round"
        />
      </svg>
      ProcessLens
    </NavLink>
  );
}

/**
 * The sidebar of DESIGN.md §6: the places, then the groups, then settings, help and the extension
 * status at the bottom. The count beside "Needs help" is the same set the Needs help page shows,
 * and it appears only when there is something to count.
 */
export function Sidebar(): JSX.Element {
  const help = useJson<NeedsHelpRow[]>("/api/v1/needs-help");
  const session = useJson<ExecutorSession>(useSessionPath());
  const count = help.data?.length ?? 0;
  const attached = session.data?.attached === true;

  return (
    <aside className="side">
      <Logo />
      <div className="navs">
        <nav className="nav">
          {TOP.map((item) =>
            itemLink(
              item,
              item.to === "/needs-help" && count > 0 ? <span className="n">{count}</span> : null,
            ),
          )}
        </nav>
        <div className="grp">Automate</div>
        <nav className="nav">{AUTOMATE.map((item) => itemLink(item))}</nav>
        <div className="grp">Your data</div>
        <nav className="nav">{YOUR_DATA.map((item) => itemLink(item))}</nav>
        <div className="grp">Connect</div>
        <nav className="nav">{CONNECT.map((item) => itemLink(item))}</nav>
      </div>
      <div className="side-foot">
        <nav className="nav">{FOOT.map((item) => itemLink(item))}</nav>
        <div className={attached ? "ext" : "ext off"}>
          <i aria-hidden="true" />
          {attached ? "Chrome connected" : "Chrome not connected"}
        </div>
      </div>
    </aside>
  );
}
