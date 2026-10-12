import { defineConfig } from "vitest/config";

/**
 * One config for the build and the tests, so `vite build` writes the page the API serves at `/`
 * and `vitest run` uses the same environment the page runs in.
 */
export default defineConfig({
  // The API serves the page at `/`, so relative asset URLs keep the page working whatever host and
  // port the operator binds.
  base: "./",
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
  test: {
    environment: "happy-dom",
    include: ["tests/**/*.test.ts"],
    globals: true,
  },
});
