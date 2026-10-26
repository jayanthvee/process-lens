/**
 * PL-105: the side panel. The website is built a second time, for the extension, with only the
 * three places an employee needs: Teach, Tasks and Runs. There is no Router inside: the tests wrap
 * this in a `MemoryRouter`, and `main.tsx` does too, because an extension page has no server
 * behind a deep link.
 *
 * The connection line is the panel's whole settings screen. It reads the hub's session for this
 * Chrome, and Connect is right there when the answer is no.
 */

import { useState } from "react";
import { NavLink, Navigate, Route, Routes } from "react-router-dom";

import { RunDetail } from "../pages/RunDetail";
import { Runs } from "../pages/Runs";
import { TaskRun } from "../pages/TaskRun";
import { useSessionPath } from "../lib/browser";
import { useJson } from "../lib/useJson";
import type { ExecutorSession } from "../types";
import { extensionChrome } from "./extension";
import { panelRuntime } from "./runtime";
import { PanelTasks } from "./PanelTasks";
import { PanelTeach } from "./PanelTeach";
import "./panel.css";

function ConnectionLine(): JSX.Element {
  const session = useJson<ExecutorSession>(useSessionPath(), 2000);
  const [connecting, setConnecting] = useState(false);
  const attached = session.data?.attached === true;

  async function connect(): Promise<void> {
    if (connecting) return;
    setConnecting(true);
    const chrome = extensionChrome();
    const runtime = panelRuntime();
    try {
      await chrome?.runtime?.sendMessage({
        type: "executor:connect",
        hubUrl: runtime.hubUrl,
        accessCode: runtime.accessCode,
      });
    } finally {
      setConnecting(false);
      session.reload();
    }
  }

  return (
    <div className={attached ? "panel-connect on" : "panel-connect"}>
      <span>{attached ? "Connected" : "Not connected"}</span>
      {attached ? null : (
        <button
          type="button"
          className="btn quiet"
          disabled={connecting}
          onClick={() => void connect()}
        >
          Connect
        </button>
      )}
    </div>
  );
}

export function PanelApp(): JSX.Element {
  return (
    <div className="panel-shell">
      <ConnectionLine />
      <nav className="panel-nav" aria-label="ProcessLens">
        <NavLink to="/teach">Teach</NavLink>
        <NavLink to="/tasks">Tasks</NavLink>
        <NavLink to="/runs">Runs</NavLink>
      </nav>
      <main className="panel-main">
        <Routes>
          <Route path="/teach" element={<PanelTeach />} />
          <Route path="/tasks" element={<PanelTasks />} />
          <Route path="/tasks/:id/run" element={<TaskRun />} />
          <Route path="/runs" element={<Runs />} />
          <Route path="/runs/:id" element={<RunDetail panel />} />
          {/* An extension page has no server behind a deep link: anything else lands on Tasks. */}
          <Route path="*" element={<Navigate to="/tasks" replace />} />
        </Routes>
      </main>
    </div>
  );
}
