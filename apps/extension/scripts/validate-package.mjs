// Checks that the built extension is a structurally loadable Manifest V3 package:
// every path the manifest references exists, the permissions are the ones the
// recorder needs, and no page is missing its script.
import { existsSync, readFileSync, statSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const dist = resolve(root, "dist");

const problems = [];
const notes = [];

function require_(condition, message) {
  if (!condition) problems.push(message);
}

function fileAt(relativePath) {
  // Always interpreted relative to dist/, so a caller cannot escape it.
  const full = resolve(dist, relativePath.replace(/^[/\\]+/, ""));
  const present = existsSync(full) && statSync(full).isFile() && statSync(full).size > 0;
  require_(present, `missing or empty: ${relativePath}`);
  return present;
}

const manifestPath = resolve(dist, "manifest.json");
require_(existsSync(manifestPath), "dist/manifest.json is missing");
if (!existsSync(manifestPath)) {
  console.error(problems.join("\n"));
  process.exit(1);
}

const manifest = JSON.parse(readFileSync(manifestPath, "utf8"));

require_(manifest.manifest_version === 3, "manifest_version must be 3");
require_(typeof manifest.name === "string" && manifest.name.length > 0, "name is required");
require_(typeof manifest.version === "string" && manifest.version.length > 0, "version is required");

const REQUIRED_PERMISSIONS = ["activeTab", "storage", "debugger", "webNavigation"];
const permissions = manifest.permissions ?? [];
for (const permission of REQUIRED_PERMISSIONS) {
  require_(permissions.includes(permission), `permission missing: ${permission}`);
}

require_(Boolean(manifest.background?.service_worker), "background.service_worker is required");
if (manifest.background?.service_worker) fileAt(manifest.background.service_worker);

const scripts = manifest.content_scripts ?? [];
require_(scripts.length > 0, "at least one content script is required");
for (const entry of scripts) {
  for (const js of entry.js ?? []) fileAt(js);
  require_(Array.isArray(entry.matches) && entry.matches.length > 0, "content script needs matches");
}

const popup = manifest.action?.default_popup;
require_(Boolean(popup), "action.default_popup is required");
if (popup) {
  fileAt(popup);
  const html = readFileSync(resolve(dist, popup), "utf8");
  for (const match of html.matchAll(/(?:src|href)="([^"]+)"/g)) {
    const target = match[1];
    if (target.startsWith("http")) continue;
    fileAt(join(dirname(popup), target));
  }
}

const icons = manifest.icons ?? {};
if (Object.keys(icons).length === 0) {
  notes.push("no icons declared; Chrome loads the extension with a default icon");
}

console.log(`manifest: ${manifest.name} ${manifest.version} (MV3)`);
console.log(`permissions: ${permissions.join(", ")}`);
console.log(`content scripts: ${scripts.length}`);
console.log(`service worker: ${manifest.background.service_worker}`);
console.log(`popup: ${popup}`);
for (const note of notes) console.log(`note: ${note}`);

if (problems.length > 0) {
  console.error("\npackage validation failed:");
  for (const problem of problems) console.error(`  - ${problem}`);
  process.exit(1);
}
console.log("\npackage validation passed");
