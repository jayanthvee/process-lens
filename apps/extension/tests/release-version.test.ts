// @vitest-environment node

// PL-100: every zip handed to a tester says which build it is.
//
// Every release so far was `processlens-0.1.0.zip`: the owner could not tell an old zip from a new
// one, and a tester's report could not say which build it came from. A release now carries its
// version, the day it was built and the commit it was built from -- in the zip's name and in the
// manifest Chrome shows -- and the version itself can be bumped with one command.
//
// Every build here writes under `.cache/` (gitignored) and is removed afterwards.
import {
  cpSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { afterEach, describe, expect, it } from "vitest";

import { bumpVersion, packageExtension } from "../scripts/release-lib.mjs";

const ROOT = resolve(fileURLToPath(import.meta.url), "../..");
const SCRATCH = resolve(ROOT, "../../.cache");

let scratch: string | null = null;

function scratchDir(): string {
  if (scratch === null) {
    mkdirSync(SCRATCH, { recursive: true });
    scratch = mkdtempSync(join(SCRATCH, "pl100-"));
  }
  return scratch;
}

afterEach(() => {
  if (scratch !== null) rmSync(scratch, { recursive: true, force: true });
  scratch = null;
});

function json(path: string): Record<string, unknown> {
  return JSON.parse(readFileSync(path, "utf8")) as Record<string, unknown>;
}

describe("a release", () => {
  it("is named for its version, its day and its commit", async () => {
    const version = String(json(join(ROOT, "manifest.json"))["version"]);
    const out = join(scratchDir(), "release");
    const dist = join(scratchDir(), "dist");

    const built = await packageExtension({
      outDir: out,
      dist,
      stamp: { date: "20261025", commit: "abc1234" },
    });

    expect(
      built.zipPath.endsWith(`processlens-${version}-20261025-abc1234.zip`),
    ).toBe(true);
  });

  it("shows the same stamp in Chrome, as the manifest's version_name", async () => {
    const version = String(json(join(ROOT, "manifest.json"))["version"]);
    const dist = join(scratchDir(), "dist");

    await packageExtension({
      outDir: join(scratchDir(), "release"),
      dist,
      stamp: { date: "20261025", commit: "abc1234" },
    });

    const built = json(join(dist, "manifest.json"));
    expect(built["version"]).toBe(version);
    expect(built["version_name"]).toBe(`${version} (20261025 abc1234)`);
  });

  it("stamps today and the checked-out commit when none is given", async () => {
    const built = await packageExtension({
      outDir: join(scratchDir(), "release"),
      dist: join(scratchDir(), "dist"),
    });

    expect(built.zipPath).toMatch(
      /processlens-\d+\.\d+\.\d+-\d{8}-[0-9a-f]{7,}\.zip$/,
    );
  });
});

describe("bumping the version", () => {
  it("adds one to the patch number in both the manifest and package.json", () => {
    const root = join(scratchDir(), "extension");
    mkdirSync(root, { recursive: true });
    cpSync(join(ROOT, "manifest.json"), join(root, "manifest.json"));
    cpSync(join(ROOT, "package.json"), join(root, "package.json"));
    const [major, minor, patch] = String(
      json(join(root, "manifest.json"))["version"],
    )
      .split(".")
      .map(Number);

    const next = bumpVersion({ root });

    const expected = `${major}.${minor}.${(patch ?? 0) + 1}`;
    expect(next).toBe(expected);
    expect(json(join(root, "manifest.json"))["version"]).toBe(expected);
    expect(json(join(root, "package.json"))["version"]).toBe(expected);
  });

  it("refuses a version it cannot read, and changes nothing", () => {
    const root = join(scratchDir(), "broken");
    mkdirSync(root, { recursive: true });
    cpSync(join(ROOT, "package.json"), join(root, "package.json"));
    const manifest = json(join(ROOT, "manifest.json"));
    const broken = JSON.stringify({ ...manifest, version: "one" });
    writeFileSync(join(root, "manifest.json"), broken);

    expect(() => bumpVersion({ root })).toThrow(/version/);
    expect(json(join(root, "manifest.json"))["version"]).toBe("one");
  });
});
