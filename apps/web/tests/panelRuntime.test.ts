/**
 * PL-105 review: a handed-out build must not stay stuck on a stored loopback address. A tester who
 * once pointed the popup at 127.0.0.1 kept that value in `chrome.storage.local`, and the panel used
 * to prefer it over the hosted address baked into the build.
 */

import { describe, expect, it } from "vitest";

import { chooseHubUrl, DEFAULT_HUB_URL, isLocalHub } from "../src/panel/runtime";

const HOSTED = "https://processlens.example.com";

describe("the hub the panel boots with", () => {
  it("prefers the hosted address baked into the build over a stored 127.0.0.1", () => {
    expect(chooseHubUrl(HOSTED, "http://127.0.0.1:8000")).toBe(HOSTED);
    expect(chooseHubUrl(HOSTED, "http://localhost:8000")).toBe(HOSTED);
  });

  it("keeps the last hub the person connected to when the build baked in no hosted address", () => {
    expect(chooseHubUrl(DEFAULT_HUB_URL, HOSTED)).toBe(HOSTED);
    expect(chooseHubUrl(DEFAULT_HUB_URL, "http://127.0.0.1:9000")).toBe(
      "http://127.0.0.1:9000",
    );
  });

  it("still lets a person's own hosted hub beat the one baked into the build", () => {
    expect(chooseHubUrl(HOSTED, "https://team.example.net")).toBe("https://team.example.net");
  });

  it("uses the baked address when nothing is stored", () => {
    expect(chooseHubUrl(HOSTED, "  ")).toBe(HOSTED);
  });

  it("recognizes the loopback names a laptop's hub can use", () => {
    for (const url of [
      "http://127.0.0.1:8000",
      "http://localhost:8000",
      "http://[::1]:8000",
      "http://0.0.0.0:8000",
    ]) {
      expect(isLocalHub(url), url).toBe(true);
    }
    expect(isLocalHub(HOSTED)).toBe(false);
  });
});
