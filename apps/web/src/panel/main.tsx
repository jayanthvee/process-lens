/**
 * PL-105: the side panel's entry point.
 *
 * It runs before the first render, because every page below asks the API for something: it finds
 * the hub the person last connected to (else the one the build was handed), configures the API
 * client with the team's access header, and asks the worker which Chrome this is. A hosted address
 * baked into a handed-out build beats a stored loopback one, so an earlier local popup does not
 * pin the panel to 127.0.0.1. Then it renders the panel inside a `MemoryRouter`: an extension
 * page has no server behind deep links.
 *
 * The access code is a secret. It is read from the build, put in a header, and never logged,
 * printed, or made part of a URL.
 */

import "@fontsource/public-sans/latin-300.css";
import "@fontsource/public-sans/latin-400.css";
import "@fontsource/public-sans/latin-500.css";
import "@fontsource/public-sans/latin-600.css";
import "../styles/tokens.css";
import "../styles/global.css";

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";

import { setBrowserId } from "../lib/browser";
import { extensionChrome } from "./extension";
import { PanelApp } from "./PanelApp";
import { chooseHubUrl, DEFAULT_HUB_URL, LAST_HUB_KEY, setPanelRuntime } from "./runtime";

declare const __PROCESSLENS_HUB_URL__: string | undefined;
declare const __PROCESSLENS_ACCESS_CODE__: string | undefined;

const BAKED_HUB_URL: string =
  typeof __PROCESSLENS_HUB_URL__ === "string" && __PROCESSLENS_HUB_URL__.trim() !== ""
    ? __PROCESSLENS_HUB_URL__.trim()
    : DEFAULT_HUB_URL;

const BAKED_ACCESS_CODE: string =
  typeof __PROCESSLENS_ACCESS_CODE__ === "string" ? __PROCESSLENS_ACCESS_CODE__.trim() : "";

async function lastHubUrl(): Promise<string> {
  const local = extensionChrome()?.storage?.local;
  if (local === undefined) return "";
  try {
    const stored = await local.get([LAST_HUB_KEY]);
    const value = stored[LAST_HUB_KEY];
    return typeof value === "string" ? value.trim() : "";
  } catch {
    return "";
  }
}

async function thisBrowser(): Promise<string | null> {
  const chrome = extensionChrome();
  if (chrome?.runtime === undefined) return null;
  try {
    const answer = (await chrome.runtime.sendMessage({ type: "executor:whoami" })) as
      | { browserId?: unknown }
      | undefined;
    return typeof answer?.browserId === "string" ? answer.browserId : null;
  } catch {
    return null;
  }
}

async function boot(): Promise<void> {
  // The last hub the person connected to wins, except that a hosted build's own address beats a
  // stored loopback one: a tester who once used the local popup is not stuck on 127.0.0.1.
  setPanelRuntime({
    hubUrl: chooseHubUrl(BAKED_HUB_URL, await lastHubUrl()),
    accessCode: BAKED_ACCESS_CODE,
  });
  const browser = await thisBrowser();
  if (browser !== null) setBrowserId(browser);

  const container = document.getElementById("root");
  if (container === null) return;
  createRoot(container).render(
    <StrictMode>
      <MemoryRouter initialEntries={["/teach"]}>
        <PanelApp />
      </MemoryRouter>
    </StrictMode>,
  );
}

void boot();

