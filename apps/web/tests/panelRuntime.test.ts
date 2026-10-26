/**
 * PL-105 review: a handed-out build must not stay stuck on an address stored on the machine. A
 * tester who once pointed the popup at 127.0.0.1 kept that value in `chrome.storage.local`, and the
 * panel used to prefer it over the hosted address baked into the build.
 */

import { describe, expect, it } from "vitest";

import { chooseHubUrl, DEFAULT_HUB_URL, isLocalHub } from "../src/panel/runtime";

const HOSTED = "https://processlens.example.com";

describe("the hub the panel boots with", () => {
  it("prefers the hosted address baked into the build over a stored 127.0.0.1", () => {
    expect(chooseHubUrl(HOSTED, "http://127.0.0.1:8000")).toBe(HOSTED);
    expect(chooseHubUrl(HOSTED, "http://localhost:8000")).toBe(HOSTED);
  });

  it("prefers the baked address over any other stored one, including an older team hub", () => {
    // A handed-out build is that deployment's own: an address someone connected to once (or a
    // previous deployment) must not move it somewhere else.
    expect(chooseHubUrl(HOSTED, "https://team.example.net")).toBe(HOSTED);
    expect(chooseHubUrl(HOSTED, "https://old-deployment.example.com")).toBe(HOSTED);
  });

  it("keeps the last hub the person connected to when the build baked in no hosted address", () => {
    expect(chooseHubUrl(DEFAULT_HUB_URL, HOSTED)).toBe(HOSTED);
    expect(chooseHubUrl(DEFAULT_HUB_URL, "http://127.0.0.1:9000")).toBe(
      "http://127.0.0.1:9000",
    );
  });

  it("uses the baked address when nothing is stored", () => {
    expect(chooseHubUrl(HOSTED, "  ")).toBe(HOSTED);
    expect(chooseHubUrl(DEFAULT_HUB_URL, "")).toBe(DEFAULT_HUB_URL);
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
