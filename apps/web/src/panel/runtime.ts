/**
 * PL-105: where the panel's API and its worker session point.
 *
 * `main.tsx` fills this in before the first render: the hub address the person last connected to,
 * else the one the build was handed. A hosted address baked into a handed-out build beats a
 * stored loopback one, so a tester who once used the local popup is not stuck on 127.0.0.1. The
 * access code is the one the build was handed and is never logged, printed, or put in a URL.
 */

import { configureApi } from "../lib/api";

export const LAST_HUB_KEY = "processlens.lastHub";
export const DEFAULT_HUB_URL = "http://127.0.0.1:8000";

/** The hostnames this laptop answers to: a hub stored with one of them is a local server. */
const LOOPBACK_HOSTS = new Set(["127.0.0.1", "localhost", "[::1]", "::1", "0.0.0.0"]);

/** Whether an address belongs to this machine, rather than to a hosted ProcessLens. */
export function isLocalHub(url: string): boolean {
  try {
    return LOOPBACK_HOSTS.has(new URL(url).hostname.toLowerCase());
  } catch {
    return false;
  }
}

/**
 * The hub to boot with: the last one the person connected to, unless it is a loopback address and
 * this copy was handed a hosted one. A tester who once used the local popup keeps
 * `http://127.0.0.1:8000` in storage; a built-for-the-team panel must not stay pointed at their
 * own laptop. A build with no hosted address (the default local one) still follows the last hub.
 */
export function chooseHubUrl(baked: string, stored: string): string {
  const last = stored.trim();
  if (last === "") return baked;
  if (isLocalHub(last) && !isLocalHub(baked)) return baked;
  return last;
}

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
