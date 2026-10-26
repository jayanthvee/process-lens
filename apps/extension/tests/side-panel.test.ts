// @vitest-environment node

// PL-105: everything an employee does lives in a Chrome side panel, not a popup and not a website.
//
// The panel is the web app built a second time, into the extension's own dist. These tests read
// the manifest a person loads and the folder the build writes, so what is asserted is the package.
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { afterEach, describe, expect, it } from "vitest";

import { buildExtension } from "../scripts/release-lib.mjs";

const ROOT = resolve(fileURLToPath(import.meta.url), "../..");
const SCRATCH = resolve(ROOT, "../../.cache");
const PANEL_PATH = "panel/panel.html";

interface Manifest {
  permissions?: string[];
  side_panel?: { default_path?: string };
  action?: { default_popup?: string };
}

function manifest(): Manifest {
  return JSON.parse(readFileSync(join(ROOT, "manifest.json"), "utf8")) as Manifest;
}

let scratch: string | null = null;

afterEach(() => {
  if (scratch !== null) rmSync(scratch, { recursive: true, force: true });
  scratch = null;
});

describe("the manifest", () => {
  it("asks for the side panel and points it at the panel page", () => {
    expect(manifest().permissions).toContain("sidePanel");
    expect(manifest().side_panel?.default_path).toBe(PANEL_PATH);
  });

  it("has no popup, so a click on the icon opens the panel", () => {
    expect(manifest().action?.default_popup).toBeUndefined();
  });
});

describe("the built extension", () => {
  it("carries the panel page and the script it loads", async () => {
    mkdirSync(SCRATCH, { recursive: true });
    scratch = mkdtempSync(join(SCRATCH, "pl105-"));
    const dist = join(scratch, "dist");
    await buildExtension({ dist });

    const page = join(dist, PANEL_PATH);
    expect(existsSync(page)).toBe(true);
    const html = readFileSync(page, "utf8");
    const script = /<script[^>]+src="([^"]+)"/.exec(html)?.[1];
    expect(script, "the panel page loads a script").toBeTruthy();
    // An extension page has no server behind it: the script must sit next to the page, relative.
    expect(script?.startsWith("/")).toBe(false);
    expect(existsSync(join(dirname(page), script ?? ""))).toBe(true);
  }, 120_000);
});
