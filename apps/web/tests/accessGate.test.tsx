/**
 * PL-086: a hosted ProcessLens asks for the team access code once, before anything else draws.
 */

import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { ACCESS_TITLE, AccessGate } from "../src/shell/AccessGate";
import { createFetchStub, settle, type FetchStub, type StubRoutes } from "./helpers";

let stub: FetchStub | null = null;

afterEach(() => {
  stub?.restore();
  stub = null;
});

function renderGate(routes: StubRoutes): void {
  stub = createFetchStub(routes);
  render(
    <AccessGate>
      <p>the app</p>
    </AccessGate>,
  );
}

describe("the access gate", () => {
  it("shows the app at once when there is no door (a laptop)", async () => {
    renderGate({ "/api/v1/access": { body: { required: false, granted: true } } });
    await settle();

    expect(screen.getByText("the app")).toBeTruthy();
    expect(screen.queryByText(ACCESS_TITLE)).toBeNull();
  });

  it("shows the app when this browser already gave the code", async () => {
    renderGate({ "/api/v1/access": { body: { required: true, granted: true } } });
    await settle();

    expect(screen.getByText("the app")).toBeTruthy();
  });

  it("treats an API without the route as no door", async () => {
    renderGate({ "/api/v1/access": { status: 404, body: { detail: "Not Found" } } });
    await settle();

    expect(screen.getByText("the app")).toBeTruthy();
  });

  it("asks for the code, says so when it is wrong, and opens when it is right", async () => {
    let tries = 0;
    renderGate({
      "/api/v1/access": (call) => {
        if (call.method === "GET") return { body: { required: true, granted: false } };
        tries += 1;
        return (call.body as { code: string }).code === "right"
          ? { body: { required: true, granted: true } }
          : { status: 401, body: { detail: "That access code is not right." } };
      },
    });
    await settle();

    expect(screen.getByText(ACCESS_TITLE)).toBeTruthy();
    expect(screen.queryByText("the app")).toBeNull();
    const input = screen.getByLabelText("Access code");
    const go = screen.getByRole("button", { name: "Continue" });

    fireEvent.change(input, { target: { value: "wrong" } });
    fireEvent.click(go);
    await settle();
    expect(screen.getByRole("alert").textContent).toBe("That access code is not right.");
    expect(screen.queryByText("the app")).toBeNull();

    fireEvent.change(input, { target: { value: "right" } });
    fireEvent.click(go);
    await settle();
    expect(screen.getByText("the app")).toBeTruthy();
    expect(tries).toBe(2);
  });
});
