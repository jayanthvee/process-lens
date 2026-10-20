// PL-052: the pieces of a releasable extension, as plain functions.
//
// A coworker's copy has to have the same id as everyone else's, has to know where the hosted
// ProcessLens is, and has to arrive as a file. This module holds the three things the build
// scripts and their tests share: the extension id a public key gives, the address validation, and
// the build/package steps themselves. The `.mjs` scripts beside it are thin command lines.
import { spawnSync } from "node:child_process";
import { createHash, generateKeyPairSync } from "node:crypto";
import { cp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { build } from "esbuild";

/** The repository's `apps/extension` folder: what a build reads from unless told otherwise. */
export const EXTENSION_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");

/** The address a build takes, when the owner wants a handed-out copy to know where ProcessLens is. */
export const HUB_URL_ENV = "PROCESSLENS_HUB_URL";

/** Where the popup points when the build is given nothing: the API on this machine. */
export const DEFAULT_HUB_URL = "http://127.0.0.1:8000";

/** One plain line, so a wrong address is a sentence and not a stack trace. */
export const HUB_URL_PROBLEM =
  "PROCESSLENS_HUB_URL must be an origin like https://processlens.up.railway.app, with no path.";

/**
 * Chrome's extension id from a public key: the first 32 hex digits of the key's SHA-256, with
 * `0-9a-f` mapped onto `a-p`. The same mapping Chrome applies, so the id printed here is the id
 * Chrome gives the extension built with this key.
 */
export function extensionIdFromPublicKey(derBase64) {
  const der = Buffer.from(String(derBase64).replace(/\s+/g, ""), "base64");
  const hex = createHash("sha256").update(der).digest("hex").slice(0, 32);
  return hex.replace(/[0-9a-f]/g, (digit) =>
    String.fromCharCode("a".charCodeAt(0) + Number.parseInt(digit, 16)),
  );
}

/**
 * The address to bake in, from the environment. Unset means this machine; anything that is not a
 * bare `http(s)://host[:port]` origin is refused rather than quietly trimmed.
 */
export function hubUrlFromEnv(env = process.env) {
  const raw = String(env[HUB_URL_ENV] ?? "").trim();
  if (raw === "") return { ok: true, url: DEFAULT_HUB_URL };

  let parsed;
  try {
    parsed = new URL(raw);
  } catch {
    return { ok: false, problem: HUB_URL_PROBLEM };
  }
  const isOrigin =
    (parsed.protocol === "http:" || parsed.protocol === "https:") &&
    parsed.host !== "" &&
    parsed.username === "" &&
    parsed.password === "" &&
    parsed.pathname === "/" &&
    parsed.search === "" &&
    parsed.hash === "";
  if (!isOrigin) return { ok: false, problem: HUB_URL_PROBLEM };
  return { ok: true, url: parsed.origin };
}

/** The committed public key, or `null` when there is none (the e2e harness's own build). */
export function readPublicKey(path) {
  if (!existsSync(path)) return null;
  const key = readFileSync(path, "utf8").replace(/\s+/g, "");
  return key === "" ? null : key;
}

/** The manifest a handed-out build ships: today's manifest, with the public key when there is one. */
export function manifestWithKey(manifest, publicKey) {
  return publicKey === null || publicKey === undefined
    ? { ...manifest }
    : { ...manifest, key: publicKey };
}

/** Whether git would ignore this path, asked of git itself rather than of a copy of its rules. */
export function gitIgnored(root, path) {
  const absolute = resolve(root, path);
  const answer = spawnSync("git", ["check-ignore", "-q", "--", absolute], {
    cwd: root,
    encoding: "utf8",
  });
  // A path outside the work tree, or one git would track, answers "no" either way.
  return answer.status === 0;
}

/**
 * Build the loadable extension into `dist`.
 *
 * A public key makes the manifest carry `key`, so the extension id is the same on every machine.
 * No public key leaves the manifest exactly as it is committed, which is what the e2e harness
 * loads. `hubUrl` is baked into the popup as its starting address.
 */
export async function buildExtension(options = {}) {
  const root = options.root ?? EXTENSION_ROOT;
  const dist = options.dist ?? join(root, "dist");
  const hubUrl = options.hubUrl ?? DEFAULT_HUB_URL;
  const publicKeyPath = options.publicKeyPath ?? join(root, "extension-key.pub");

  await rm(dist, { recursive: true, force: true });
  await mkdir(dist, { recursive: true });

  const bundle = (entry, outfile, format) =>
    build({
      entryPoints: [resolve(root, entry)],
      outfile: resolve(dist, outfile),
      bundle: true,
      format,
      target: "chrome116",
      sourcemap: true,
      logLevel: "silent",
      // Only the popup reads the address; the worker is told it by the popup.
      define:
        outfile === "popup/popup.js" ? { __PROCESSLENS_HUB_URL__: JSON.stringify(hubUrl) } : {},
    });

  // A content script is not an ES module; the worker and the popup are.
  await bundle("src/content/recorder.ts", "content/recorder.js", "iife");
  await bundle("src/background/service-worker.ts", "background/service-worker.js", "esm");
  await bundle("src/popup/popup.ts", "popup/popup.js", "esm");

  const publicKey = readPublicKey(publicKeyPath);
  if (publicKey === null) {
    await cp(resolve(root, "manifest.json"), resolve(dist, "manifest.json"));
  } else {
    const manifest = JSON.parse(readFileSync(resolve(root, "manifest.json"), "utf8"));
    await writeFile(
      resolve(dist, "manifest.json"),
      `${JSON.stringify(manifestWithKey(manifest, publicKey), null, 2)}\n`,
      "utf8",
    );
  }

  await cp(resolve(root, "src/popup/popup.html"), resolve(dist, "popup/popup.html"));
  await cp(resolve(root, "src/popup/popup.css"), resolve(dist, "popup/popup.css"));

  // The popup's design tokens sit next to its stylesheet, and its fonts are bundled, never
  // fetched: the four Public Sans weights are copied out of @fontsource/public-sans so the
  // popup's CSP never has to allow a remote font.
  await cp(resolve(root, "src/popup/tokens.css"), resolve(dist, "popup/tokens.css"));

  const fontFiles = resolve(root, "node_modules/@fontsource/public-sans/files");
  await mkdir(resolve(dist, "popup/fonts"), { recursive: true });
  for (const weight of [300, 400, 500, 600]) {
    const file = `public-sans-latin-${weight}-normal.woff2`;
    await cp(resolve(fontFiles, file), resolve(dist, "popup/fonts", file));
  }

  return { dist, hubUrl, publicKey, extensionId: publicKey === null ? null : extensionIdFromPublicKey(publicKey) };
}

const CRC_TABLE = (() => {
  const table = new Uint32Array(256);
  for (let index = 0; index < 256; index += 1) {
    let value = index;
    for (let bit = 0; bit < 8; bit += 1) {
      value = value & 1 ? 0xedb88320 ^ (value >>> 1) : value >>> 1;
    }
    table[index] = value >>> 0;
  }
  return table;
})();

function crc32(data) {
  let value = 0xffffffff;
  for (const byte of data) value = CRC_TABLE[(value ^ byte) & 0xff] ^ (value >>> 8);
  return (value ^ 0xffffffff) >>> 0;
}

function listFiles(dir, prefix = "") {
  const out = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = join(dir, entry.name);
    const name = prefix === "" ? entry.name : `${prefix}/${entry.name}`;
    if (entry.isDirectory()) out.push(...listFiles(full, name));
    else if (statSync(full).isFile()) out.push({ name, full });
  }
  return out;
}

/**
 * A zip of a folder's contents, with the files at the archive's root (no leading folder), so
 * Chrome's "Load unpacked" works on the unzipped folder. Stored, not deflated: a plain zip any
 * tool reads, written with no dependency.
 */
export function zipDirectory(dir) {
  const files = listFiles(dir).sort((left, right) => (left.name < right.name ? -1 : 1));
  const chunks = [];
  const central = [];
  let offset = 0;

  for (const file of files) {
    const data = readFileSync(file.full);
    const name = Buffer.from(file.name, "utf8");
    const crc = crc32(data);

    const local = Buffer.alloc(30);
    local.writeUInt32LE(0x04034b50, 0);
    local.writeUInt16LE(20, 4);
    local.writeUInt16LE(0, 6);
    local.writeUInt16LE(0, 8);
    local.writeUInt16LE(0, 10);
    local.writeUInt16LE(0x21, 12);
    local.writeUInt32LE(crc, 14);
    local.writeUInt32LE(data.length, 18);
    local.writeUInt32LE(data.length, 22);
    local.writeUInt16LE(name.length, 26);
    local.writeUInt16LE(0, 28);
    chunks.push(local, name, data);

    const entry = Buffer.alloc(46);
    entry.writeUInt32LE(0x02014b50, 0);
    entry.writeUInt16LE(20, 4);
    entry.writeUInt16LE(20, 6);
    entry.writeUInt16LE(0, 8);
    entry.writeUInt16LE(0, 10);
    entry.writeUInt16LE(0, 12);
    entry.writeUInt16LE(0x21, 14);
    entry.writeUInt32LE(crc, 16);
    entry.writeUInt32LE(data.length, 20);
    entry.writeUInt32LE(data.length, 24);
    entry.writeUInt16LE(name.length, 28);
    entry.writeUInt16LE(0, 30);
    entry.writeUInt16LE(0, 32);
    entry.writeUInt16LE(0, 34);
    entry.writeUInt16LE(0, 36);
    entry.writeUInt32LE(0, 38);
    entry.writeUInt32LE(offset, 42);
    central.push(entry, name);

    offset += local.length + name.length + data.length;
  }

  const centralDirectory = Buffer.concat(central);
  const end = Buffer.alloc(22);
  end.writeUInt32LE(0x06054b50, 0);
  end.writeUInt16LE(0, 4);
  end.writeUInt16LE(0, 6);
  end.writeUInt16LE(files.length, 8);
  end.writeUInt16LE(files.length, 10);
  end.writeUInt32LE(centralDirectory.length, 12);
  end.writeUInt32LE(offset, 16);
  end.writeUInt16LE(0, 20);

  return Buffer.concat([...chunks, centralDirectory, end]);
}

/** The entry names of a zip, read back from its central directory. */
export function readZipEntries(zipPath) {
  const buffer = readFileSync(zipPath);
  let end = -1;
  for (let index = buffer.length - 22; index >= 0; index -= 1) {
    if (buffer.readUInt32LE(index) === 0x06054b50) {
      end = index;
      break;
    }
  }
  if (end < 0) throw new Error(`${zipPath} is not a zip`);
  const count = buffer.readUInt16LE(end + 10);
  let at = buffer.readUInt32LE(end + 16);
  const names = [];
  for (let index = 0; index < count; index += 1) {
    if (buffer.readUInt32LE(at) !== 0x02014b50) throw new Error("bad central directory");
    const nameLength = buffer.readUInt16LE(at + 28);
    const extraLength = buffer.readUInt16LE(at + 30);
    const commentLength = buffer.readUInt16LE(at + 32);
    names.push(buffer.subarray(at + 46, at + 46 + nameLength).toString("utf8"));
    at += 46 + nameLength + extraLength + commentLength;
  }
  return names;
}

/**
 * Build, validate and zip: the one file a coworker is handed.
 *
 * Validation is the repository's own `validate-package.mjs`, run against this build, so a package
 * that is missing a file the manifest names is never written.
 */
export async function packageExtension(options = {}) {
  const root = options.root ?? EXTENSION_ROOT;
  const dist = options.dist ?? join(root, "dist");
  const outDir = options.outDir ?? join(root, "release");

  await buildExtension({ ...options, root, dist });

  const check = spawnSync(process.execPath, [join(root, "scripts/validate-package.mjs")], {
    cwd: root,
    encoding: "utf8",
    env: { ...process.env, PROCESSLENS_DIST: dist },
  });
  if (check.status !== 0) {
    throw new Error(`the built extension did not validate:\n${check.stdout}${check.stderr}`);
  }

  const manifest = JSON.parse(readFileSync(join(dist, "manifest.json"), "utf8"));
  await mkdir(outDir, { recursive: true });
  const zipPath = join(outDir, `processlens-${manifest.version}.zip`);
  await writeFile(zipPath, zipDirectory(dist));
  return { zipPath, entries: readZipEntries(zipPath) };
}

/**
 * A fresh RSA 2048 key pair, written somewhere git ignores.
 *
 * Chrome derives the extension's id from the public key, so the private half only has to stay
 * private: it is written once, into `private/`, and never read by a build. A path git would commit
 * is refused outright -- a key in the repository is a key that has leaked.
 */
export async function makeKey(options = {}) {
  const root = options.root ?? EXTENSION_ROOT;
  const privatePath = options.privatePath ?? join(root, "private/extension-key.pem");
  const publicPath = options.publicPath ?? join(root, "extension-key.pub");

  if (existsSync(privatePath)) {
    throw new Error(`${privatePath} already exists; move it aside before making a new key`);
  }
  if (existsSync(publicPath)) {
    throw new Error(
      `${publicPath} already exists; a new public key would change the id every copy is pinned to`,
    );
  }
  if (!gitIgnored(root, privatePath)) {
    throw new Error(
      `${privatePath} is not ignored by gitignore, so a private key must not be written there`,
    );
  }

  const { publicKey, privateKey } = generateKeyPairSync("rsa", {
    modulusLength: 2048,
    publicKeyEncoding: { type: "spki", format: "der" },
    privateKeyEncoding: { type: "pkcs8", format: "pem" },
  });
  const publicKeyBase64 = publicKey.toString("base64");

  await mkdir(dirname(privatePath), { recursive: true });
  await writeFile(privatePath, privateKey, { encoding: "utf8", mode: 0o600 });
  await mkdir(dirname(publicPath), { recursive: true });
  await writeFile(publicPath, `${publicKeyBase64}\n`, "utf8");

  return {
    id: extensionIdFromPublicKey(publicKeyBase64),
    publicKeyBase64,
    privatePath,
    publicPath,
  };
}
