import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    environment: "jsdom",
    include: ["tests/**/*.test.ts"],
    globals: true,
    // A release test builds the whole extension, including the panel's Vite build.
    testTimeout: 60_000,
  },
});
