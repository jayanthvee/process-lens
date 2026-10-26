import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";

/**
 * PL-105: the side panel's build, beside the website's.
 *
 * The extension owns the output folder (`dist/panel/`), so `buildExtension` tells this config
 * where to write and which hub address and access code to bake in. `base: "./"` is the whole
 * point: an extension page is loaded from a folder, not served from a site root, so its script
 * must sit beside it, relative.
 */
const root = fileURLToPath(new URL(".", import.meta.url));
const outDir = process.env["PROCESSLENS_PANEL_OUT"] || "panel-dist";
const hubUrl = process.env["PROCESSLENS_PANEL_HUB_URL"] || "http://127.0.0.1:8000";
const accessCode = process.env["PROCESSLENS_PANEL_ACCESS_CODE"] || "";

export default defineConfig({
  plugins: [react()],
  base: "./",
  define: {
    __PROCESSLENS_HUB_URL__: JSON.stringify(hubUrl),
    __PROCESSLENS_ACCESS_CODE__: JSON.stringify(accessCode),
  },
  build: {
    outDir,
    emptyOutDir: false,
    target: "chrome116",
    modulePreload: { polyfill: false },
    rollupOptions: {
      input: `${root}panel.html`,
    },
  },
  logLevel: "warn",
});
