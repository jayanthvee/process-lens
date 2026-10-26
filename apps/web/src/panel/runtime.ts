/**
 * PL-105: where the panel's API and its worker session point.
 *
 * `main.tsx` fills this in before the first render: the hub address the build was handed, unless
 * the person last connected to another one (`LAST_HUB_KEY` wins), and the access code the build
 * was handed. The code is never logged, printed, or put in a URL.
 */

import { configureApi } from "../lib/api";

export const LAST_HUB_KEY = "processlens.lastHub";
export const DEFAULT_HUB_URL = "http://127.0.0.1:8000";

export interface PanelRuntimeConfig {
  hubUrl: string;
  accessCode: string;
}

let current: PanelRuntimeConfig = { hubUrl: DEFAULT_HUB_URL, accessCode: "" };

function headersFor(accessCode: string): Record<string, string> {
  return accessCode === "" ? {} : { authorization: `Bearer ${accessCode}` };
}

/** Configure the API client, then keep the same values for the worker session. */
export function setPanelRuntime(config: PanelRuntimeConfig): void {
  const hubUrl = config.hubUrl.trim().replace(/\/+$/, "") || DEFAULT_HUB_URL;
  const accessCode = config.accessCode.trim();
  current = { hubUrl, accessCode };
  configureApi({ base: hubUrl, headers: headersFor(accessCode) });
}

export function panelRuntime(): PanelRuntimeConfig {
  return { ...current };
}
