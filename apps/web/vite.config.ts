import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

/**
 * One config for the build and the tests, so `vite build` writes the page the API serves at `/`
 * and `vitest run` uses the same environment the page runs in.
 */
export default defineConfig({
  plugins: [react()],
  // The API serves the built page from this origin's root, so every asset URL is absolute. A
  // relative base would resolve the assets of a deep route such as `/runs/abc` under `/runs/` and
  // 404 on a refresh.
  base: "/",
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
  test: {
    environment: "happy-dom",
    include: ["tests/**/*.test.ts", "tests/**/*.test.tsx"],
    globals: true,
    // The router test renders every page in one case; under a loaded suite it can pass 5 s.
    testTimeout: 20_000,
  },
});
