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
 * The hub to boot with.
 *
 * A hosted address baked into a handed-out build is that deployment's own address, so nothing
 * stored on the machine may point the panel somewhere else: neither the loopback address an
 * earlier local popup left behind, nor an older team address someone connected to once.
 *
 * A build with no hosted address (the default local one `make demo` produces) has nothing to
 * defend, so the last hub the person connected to wins — that is how a laptop moves between
 * ports, and how a local build follows the hub it was pointed at.
 */
export function chooseHubUrl(baked: string, stored: string): string {
  const build = baked.trim();
  const last = stored.trim();
  if (build !== "" && !isLocalHub(build)) return build;
  return last === "" ? baked : last;
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
