// Builds the loadable extension into dist/.
//
// The content script is bundled as a classic script (content scripts are not
// ES modules); the service worker and popup are bundled as ES modules.
import { build } from "esbuild";
import { cp, mkdir, rm } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const dist = resolve(root, "dist");

async function run() {
  await rm(dist, { recursive: true, force: true });
  await mkdir(dist, { recursive: true });

  await build({
    entryPoints: [resolve(root, "src/content/recorder.ts")],
    outfile: resolve(dist, "content/recorder.js"),
    bundle: true,
    format: "iife",
    target: "chrome116",
    sourcemap: true,
    logLevel: "info",
  });

  await build({
    entryPoints: [resolve(root, "src/background/service-worker.ts")],
    outfile: resolve(dist, "background/service-worker.js"),
    bundle: true,
    format: "esm",
    target: "chrome116",
    sourcemap: true,
    logLevel: "info",
  });

  await build({
    entryPoints: [resolve(root, "src/popup/popup.ts")],
    outfile: resolve(dist, "popup/popup.js"),
    bundle: true,
    format: "esm",
    target: "chrome116",
    sourcemap: true,
    logLevel: "info",
  });

  await cp(resolve(root, "manifest.json"), resolve(dist, "manifest.json"));
  await cp(resolve(root, "src/popup/popup.html"), resolve(dist, "popup/popup.html"));
  await cp(resolve(root, "src/popup/popup.css"), resolve(dist, "popup/popup.css"));

  console.log(`built extension into ${dist}`);
}

run().catch((error) => {
  console.error(error);
  process.exit(1);
});
