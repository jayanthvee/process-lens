/**
 * PL-086: the team access code, asked once per browser when ProcessLens is hosted.
 *
 * The page asks `GET /api/v1/access` before it draws anything. On a laptop the answer is "no door"
 * and the app appears as it always has. Hosted, a person who has not given the code sees one card:
 * the code, and Continue. The API answers a right code with an HttpOnly cookie, so this page never
 * keeps the code itself. An API too old to know the route counts as "no door".
 */

import { useEffect, useState, type FormEvent, type ReactNode } from "react";

import { apiGet, apiPost, ApiError, errorText } from "../lib/api";
import "./accessGate.css";

interface AccessState {
  required: boolean;
  granted: boolean;
}

export const ACCESS_TITLE = "Sign in to ProcessLens";
export const ACCESS_LEDE = "Enter your team's access code. You only need to do this once on this browser.";
export const ACCESS_HELP = "Ask the person who set up ProcessLens for the code.";

type Gate = "checking" | "open" | "asking";

export function AccessGate({ children }: { children: ReactNode }): JSX.Element | null {
  const [gate, setGate] = useState<Gate>("checking");
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    apiGet<AccessState>("/api/v1/access")
      .then((state) => {
        if (live) setGate(state.required && !state.granted ? "asking" : "open");
      })
      .catch((error: unknown) => {
        // 404: an API from before PL-086, which has no door. Anything else: let the app load and
        // show its own "could not reach the API" states, which say more than this card could.
        if (live) setGate(error instanceof ApiError && error.status === 401 ? "asking" : "open");
      });
    return () => {
      live = false;
    };
  }, []);

  async function submit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (code.trim() === "") return;
    setBusy(true);
    setProblem(null);
    try {
      await apiPost<AccessState>("/api/v1/access", { code });
      setCode("");
      setGate("open");
    } catch (error) {
      setProblem(errorText(error));
    } finally {
      setBusy(false);
    }
  }

  if (gate === "open") return <>{children}</>;
  if (gate === "checking") return null;

  return (
    <main className="access-gate">
      <form className="panel access-card" onSubmit={(event) => void submit(event)}>
        <p className="access-brand">ProcessLens</p>
        <h1 className="access-title">{ACCESS_TITLE}</h1>
        <p className="access-lede">{ACCESS_LEDE}</p>
        <label className="access-field">
          <span>Access code</span>
          <input
            type="password"
            name="access-code"
            autoComplete="current-password"
            autoFocus
            value={code}
            onChange={(event) => setCode(event.target.value)}
            aria-invalid={problem !== null}
            aria-describedby="access-problem"
          />
        </label>
        <p id="access-problem" className="access-problem" role="alert">
          {problem ?? ""}
        </p>
        <button className="btn primary access-go" type="submit" disabled={busy || code.trim() === ""}>
          {busy ? "Checking…" : "Continue"}
        </button>
        <p className="access-help">{ACCESS_HELP}</p>
      </form>
    </main>
  );
}
