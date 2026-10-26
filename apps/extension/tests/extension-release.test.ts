// @vitest-environment node

// PL-052: an extension a coworker can install.
//
// The id has to be the same on every machine, the address has to be the hosted ProcessLens, and
// there has to be a file to hand over. These tests drive the build scripts themselves, so what is
// asserted is the package a person loads, not a copy of the logic.
//
// Every build here writes under `.cache/` (gitignored) and is removed afterwards; no test touches
// the real `dist/` and none ever writes or reads a private key.
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { afterEach, describe, expect, it } from "vitest";

import {
  DEFAULT_HUB_URL,
  buildExtension,
  extensionIdFromPublicKey,
  gitIgnored,
  hubUrlFromEnv,
  makeKey,
  packageExtension,
  readZipEntries,
} from "../scripts/release-lib.mjs";

const ROOT = resolve(fileURLToPath(import.meta.url), "../..");
const SCRATCH = resolve(ROOT, "../../.cache");

/**
 * A public key generated once, for these tests only. Its private half was discarded the moment it
 * was printed, so nothing here can ever sign for it.
 */
const TEST_PUBLIC_KEY =
  "MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAv4aWUhwE4g2AjSKXj9TLGALk9WCzaTXxeGiZ" +
  "3Whf9ay9EANlQiJXXhMyY5lSXMUhef1GK6hcdxabD+QYMS3+jbn4bnZLfxA0lQKi0tTxV79rORW0kiVEOC" +
  "PkrVE0hq/UYHLnu243SWGv88DH8Xv6jEExahlCkjKpaXzB0AQcvaNNKoua4YNb95xJ9m8vQLAG0eGMHjy5" +
  "OvXak215UNmhqs2cnPa6uIjjMsM/3ZO8KHy+St6ISecyGX2QlJ3HwrQ0hNXpMdCV8XID/78o+AZ8gSrHUhG" +
  "rUR3z3P360QCIYjYfYf5un97xhNSmJF2rbuMsaxTSqnCcXDdUtvTuKErWOQIDAQAB";

/** The id that key gives: the first 32 hex digits of its SHA-256, with 0-9 mapped to a-j. */
const TEST_ID = "hmofkglcjjcecceaafpceigjjeoaoiib";

let scratch: string | null = null;

function scratchDir(): string {
  if (scratch === null) {
    mkdirSync(SCRATCH, { recursive: true });
    scratch = mkdtempSync(join(SCRATCH, "pl052-"));
  }
  return scratch;
}

afterEach(() => {
  if (scratch !== null) rmSync(scratch, { recursive: true, force: true });
  scratch = null;
});

function keyFile(base64 = TEST_PUBLIC_KEY): string {
  const path = join(scratchDir(), "extension-key.pub");
  writeFileSync(path, `${base64}\n`, "utf8");
  return path;
}

function manifestOf(dist: string): Record<string, unknown> {
  return JSON.parse(readFileSync(join(dist, "manifest.json"), "utf8")) as Record<string, unknown>;
}

describe("the extension id", () => {
  it("turns a known public key into its known id", () => {
    expect(extensionIdFromPublicKey(TEST_PUBLIC_KEY)).toBe(TEST_ID);
  });

  it("is Chrome's twenty a-p characters, so a coworker's copy is never refused", () => {
    expect(extensionIdFromPublicKey(TEST_PUBLIC_KEY)).toMatch(/^[a-p]{32}$/);
  });
});

describe("the key in the built manifest", () => {
  it("is there when a public key file is given", async () => {
    const dist = join(scratchDir(), "with-key");
    await buildExtension({ dist, publicKeyPath: keyFile(), hubUrl: DEFAULT_HUB_URL });

    expect(manifestOf(dist)["key"]).toBe(TEST_PUBLIC_KEY);
  });

  it("is absent without a key file, and the manifest is byte-identical to today's", async () => {
    const dist = join(scratchDir(), "without-key");
    await buildExtension({ dist, publicKeyPath: join(scratchDir(), "no-such.pub") });

    expect(manifestOf(dist)).not.toHaveProperty("key");
    // The e2e harness loads this build, so its manifest has to stay exactly what Chrome saw before.
    expect(readFileSync(join(dist, "manifest.json"))).toEqual(
      readFileSync(join(ROOT, "manifest.json")),
    );
  });
});

describe("the hub address baked into the popup", () => {
  it("is the hosted address when the build is given one", async () => {
    const dist = join(scratchDir(), "hosted");
    await buildExtension({ dist, hubUrl: "https://processlens.up.railway.app" });

    const popup = readFileSync(join(dist, "popup/popup.js"), "utf8");
    expect(popup).toContain('"https://processlens.up.railway.app"');
  });

  it("carries the team access code when the build is given one, and none otherwise (PL-086)", async () => {
    const hosted = join(scratchDir(), "hosted-code");
    await buildExtension({ dist: hosted, hubUrl: "https://x.example", accessCode: " team-code " });
    expect(readFileSync(join(hosted, "popup/popup.js"), "utf8")).toContain('"team-code"');

    const local = join(scratchDir(), "local-code");
    const built = await buildExtension({ dist: local });
    expect(built.hasAccessCode).toBe(false);
    expect(readFileSync(join(local, "popup/popup.js"), "utf8")).not.toContain("team-code");
  });

  it("is this machine when no address is given", async () => {
    const dist = join(scratchDir(), "local");
    await buildExtension({ dist });

    const popup = readFileSync(join(dist, "popup/popup.js"), "utf8");
    expect(popup).toContain('"http://127.0.0.1:8000"');
  });
});

describe("the address from the environment", () => {
  it("takes an origin and nothing else", () => {
    expect(hubUrlFromEnv({ PROCESSLENS_HUB_URL: "https://x.example" })).toEqual({
      ok: true,
      url: "https://x.example",
    });
    expect(hubUrlFromEnv({ PROCESSLENS_HUB_URL: "http://127.0.0.1:8100" })).toEqual({
      ok: true,
      url: "http://127.0.0.1:8100",
    });
  });

  it("falls back to this machine when it is unset or empty", () => {
    expect(hubUrlFromEnv({})).toEqual({ ok: true, url: DEFAULT_HUB_URL });
    expect(hubUrlFromEnv({ PROCESSLENS_HUB_URL: "   " })).toEqual({
      ok: true,
      url: DEFAULT_HUB_URL,
    });
  });

  it("refuses an address with a path, in one plain line", () => {
    const answer = hubUrlFromEnv({ PROCESSLENS_HUB_URL: "https://x.example/path" });
    expect(answer.ok).toBe(false);
    if (!answer.ok) {
      expect(answer.problem).toBe(
        "PROCESSLENS_HUB_URL must be an origin like https://processlens.up.railway.app, with no path.",
      );
      expect(answer.problem.split("\n")).toHaveLength(1);
    }
  });

  it("refuses a scheme that is not http or https", () => {
    for (const bad of ["ftp://x.example", "x.example", "https://", "https://x.example?q=1"]) {
      expect(hubUrlFromEnv({ PROCESSLENS_HUB_URL: bad }).ok, bad).toBe(false);
    }
  });
});

describe("the package to hand out", () => {
  it("is a zip with the built manifest at its root", async () => {
    const out = join(scratchDir(), "release");
    const built = await packageExtension({ outDir: out, publicKeyPath: keyFile() });

    expect(built.zipPath.endsWith(".zip")).toBe(true);
    expect(built.zipPath).toContain("release");
    const entries = readZipEntries(built.zipPath);
    expect(entries).toContain("manifest.json");
    expect(entries.every((name) => !name.startsWith("dist/"))).toBe(true);
    expect(entries).toContain("popup/popup.html");
    expect(entries).toContain("background/service-worker.js");
    // The private key never travels with the package.
    expect(entries.some((name) => name.endsWith(".pem"))).toBe(false);
  });

  it("is named for the manifest's version, its day and its commit", async () => {
    const out = join(scratchDir(), "release-version");
    const version = String(manifestOf(ROOT)["version"]);
    const built = await packageExtension({
      outDir: out,
      stamp: { date: "20261025", commit: "abc1234" },
    });

    expect(built.zipPath.endsWith(`processlens-${version}-20261025-abc1234.zip`)).toBe(true);
    expect(existsSync(built.zipPath)).toBe(true);
  });
});

describe("making a key", () => {
  it("writes the private key under private/ and the public key beside the extension", async () => {
    const privatePath = join(scratchDir(), "key.pem");
    const publicPath = join(scratchDir(), "extension-key.pub");
    const made = await makeKey({ root: ROOT, privatePath, publicPath });

    expect(existsSync(made.privatePath)).toBe(true);
    expect(readFileSync(made.privatePath, "utf8")).toContain("PRIVATE KEY");
    expect(made.publicKeyBase64).toBe(made.publicKeyBase64.trim());
    expect(readFileSync(publicPath, "utf8").trim()).toBe(made.publicKeyBase64);
    expect(extensionIdFromPublicKey(made.publicKeyBase64)).toBe(made.id);
    // The printed id is the id Chrome will give the extension built with that public key.
    expect(made.id).toMatch(/^[a-p]{32}$/);
  });

  it("never overwrites a key that already exists", async () => {
    const privatePath = join(scratchDir(), "key.pem");
    const publicPath = join(scratchDir(), "extension-key.pub");
    await makeKey({ root: ROOT, privatePath, publicPath });

    // Running it twice would change the id every copy of the extension is pinned to.
    await expect(makeKey({ root: ROOT, privatePath, publicPath })).rejects.toThrow(/already exists/);
  });

  it("refuses to write a private key where git would commit it", async () => {
    // `private/` is ignored, and so is every `*.pem`; a name git would track gets no key at all.
    expect(gitIgnored(ROOT, "private/extension-key.pem")).toBe(true);
    expect(gitIgnored(ROOT, "apps/extension/extension-key.key")).toBe(false);

    await expect(
      makeKey({
        root: ROOT,
        privatePath: join(ROOT, "apps/extension/extension-key.key"),
        publicPath: join(scratchDir(), "extension-key.pub"),
      }),
    ).rejects.toThrow(/gitignore/);
  });
});

