import { type CSSProperties, useEffect, useState } from "react";

import { apiPut, errorText } from "../lib/api";
import { useJson } from "../lib/useJson";
import {
  ADD_A_SITE,
  DAILY_LIMITS_SENTENCE,
  DAILY_LIMITS_TITLE,
  LIMITS_READ_ONLY,
  LIMITS_SAVED,
  NO_LIMITS,
  refusalWords,
  ROWS_PER_DAY_LABEL,
  SAVE_LIMITS,
  SITE_LABEL,
} from "../lib/words";
import type { SiteLimit, SiteLimits } from "../types";
import "./settings.css";

const LIMITS_PATH = "/api/v1/settings/site-limits";

/** The blur-in stagger for the sections (DESIGN.md §7). */
function slot(index: number): CSSProperties {
  return { "--i": index } as CSSProperties;
}

/**
 * Settings, with its one section: the daily limit per site (PL-080).
 *
 * Everyone sees the limits. An admin -- the tenant's owner, or the admin panel's token -- also gets
 * add, edit and remove; every other person reads the same rows with a sentence saying who can
 * change them. The whole list is saved at once, so a site removed on screen is a site removed from
 * the setting, and no other setting is touched (the API writes one `jsonb_set`).
 */
export function Settings(): JSX.Element {
  const limits = useJson<SiteLimits>(LIMITS_PATH);
  const [rows, setRows] = useState<SiteLimit[]>([]);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  // The loaded answer is the starting point; once the person has changed something, a reload must
  // not throw their edits away.
  useEffect(() => {
    if (limits.data !== null && Array.isArray(limits.data.sites) && !dirty) {
      setRows(limits.data.sites.map((site) => ({ ...site })));
    }
  }, [limits.data, dirty]);

  const canChange = limits.data?.can_change === true;

  function edit(index: number, patch: Partial<SiteLimit>): void {
    setRows((current) =>
      current.map((site, position) => (position === index ? { ...site, ...patch } : site)),
    );
    setDirty(true);
    setSaved(false);
  }

  function add(): void {
    setRows((current) => [...current, { origin: "", limit: 25 }]);
    setDirty(true);
    setSaved(false);
  }

  function remove(index: number): void {
    setRows((current) => current.filter((_site, position) => position !== index));
    setDirty(true);
    setSaved(false);
  }

  async function save(): Promise<void> {
    setBusy(true);
    setProblem(null);
    setSaved(false);
    try {
      const answer = await apiPut<SiteLimits>(LIMITS_PATH, {
        sites: rows.map((site) => ({ origin: site.origin, limit: site.limit })),
      });
      setRows(answer.sites.map((site) => ({ ...site })));
      setDirty(false);
      setSaved(true);
    } catch (failure) {
      setProblem(refusalWords(errorText(failure)));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="settings">
      <section className="panel in" id="daily-limits" style={slot(1)}>
        <div className="panel-h">
          <h3>{DAILY_LIMITS_TITLE}</h3>
          {!canChange || !dirty ? null : <span className="run-count">Not saved</span>}
        </div>
        <p className="run-note">{DAILY_LIMITS_SENTENCE}</p>

        {limits.data === null ? (
          <p className="run-note">{limits.error ?? "Loading your limits…"}</p>
        ) : rows.length === 0 ? (
          <p className="run-note">{NO_LIMITS}</p>
        ) : canChange ? (
          <table className="limits-table">
            <thead>
              <tr>
                <th>{SITE_LABEL}</th>
                <th>{ROWS_PER_DAY_LABEL}</th>
                <th aria-label="Actions" />
              </tr>
            </thead>
            <tbody>
              {rows.map((site, index) => (
                <tr key={`${index}-${site.origin}`}>
                  <td>
                    <input
                      type="text"
                      aria-label={SITE_LABEL}
                      placeholder="https://www.example.com"
                      value={site.origin}
                      disabled={busy}
                      onChange={(event) => edit(index, { origin: event.target.value })}
                    />
                  </td>
                  <td>
                    <input
                      type="number"
                      aria-label={ROWS_PER_DAY_LABEL}
                      min={1}
                      max={10000}
                      value={site.limit}
                      disabled={busy}
                      onChange={(event) => edit(index, { limit: Number(event.target.value) })}
                    />
                  </td>
                  <td>
                    <button
                      type="button"
                      className="btn quiet limits-remove"
                      disabled={busy}
                      onClick={() => remove(index)}
                    >
                      Remove
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <ul className="limits-read">
            {rows.map((site) => (
              <li key={site.origin}>
                <b>{site.origin}</b>
                <span>{site.limit} rows a day</span>
              </li>
            ))}
          </ul>
        )}

        {!canChange ? <p className="run-note">{LIMITS_READ_ONLY}</p> : null}

        {!canChange || limits.data === null ? null : (
          <div className="actions">
            <button type="button" className="btn quiet limits-add" disabled={busy} onClick={add}>
              {ADD_A_SITE}
            </button>
            <button
              type="button"
              className="btn primary limits-save"
              disabled={busy || !dirty}
              onClick={() => void save()}
            >
              {SAVE_LIMITS}
            </button>
            {saved ? (
              <span className="run-count" role="status">
                {LIMITS_SAVED}
              </span>
            ) : null}
          </div>
        )}

        {problem === null ? null : (
          <p className="refusal limits-problem" role="status">
            {problem}
          </p>
        )}
      </section>
    </div>
  );
}
