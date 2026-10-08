// End-to-end smoke test in a real Chromium browser.
//
// Loads the built extension as an unpacked extension, opens a page with a form,
// starts recording through the popup, performs an interaction, and reads back what
// the worker stored. This is the "loads cleanly and captures events" check that
// unit tests cannot make on their own.
//
// Run:  node scripts/e2e-smoke.mjs
// Notes: Google Chrome (branded) ignores command-line unpacked-extension loading,
// so this tries each Chromium-family browser it finds and uses the first that
// loads the extension. Set CHROME_PATH to pick one yourself.
import { spawn } from "node:child_process";
import { createServer } from "node:http";
import { existsSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const dist = resolve(root, "dist");
const DEBUG_PORT = 9337;
const SECRET = "SECRET-DO-NOT-CAPTURE";

const BROWSER_CANDIDATES = [
  process.env.CHROME_PATH,
  "C:/Program Files/Chromium/Application/chrome.exe",
  "C:/Program Files/Google/Chrome/Application/chrome.exe",
  "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
  "C:/Program Files/Microsoft/Edge/Application/msedge.exe",
].filter(Boolean);

const sleep = (ms) => new Promise((done) => setTimeout(done, ms));

/** A very small CDP client over the browser-level WebSocket, with flat sessions. */
class Cdp {
  constructor(socket) {
    this.socket = socket;
    this.nextId = 1;
    this.pending = new Map();
    socket.addEventListener("message", (event) => {
      const message = JSON.parse(event.data);
      if (message.id && this.pending.has(message.id)) {
        const { resolve: ok, reject } = this.pending.get(message.id);
        this.pending.delete(message.id);
        if (message.error) reject(new Error(JSON.stringify(message.error)));
        else ok(message.result);
      }
    });
  }

  send(method, params = {}, sessionId) {
    const id = this.nextId++;
    const payload = { id, method, params };
    if (sessionId) payload.sessionId = sessionId;
    return new Promise((ok, reject) => {
      this.pending.set(id, { resolve: ok, reject });
      this.socket.send(JSON.stringify(payload));
      setTimeout(() => {
        if (this.pending.delete(id)) reject(new Error(`${method} timed out`));
      }, 15000);
    });
  }

  async evaluate(sessionId, expression) {
    const result = await this.send(
      "Runtime.evaluate",
      { expression, awaitPromise: true, returnByValue: true },
      sessionId,
    );
    if (result.exceptionDetails) {
      throw new Error(result.exceptionDetails.exception?.description ?? "evaluate failed");
    }
    return result.result?.value;
  }
}

async function waitForDevTools(port) {
  for (let attempt = 0; attempt < 60; attempt += 1) {
    try {
      const response = await fetch(`http://127.0.0.1:${port}/json/version`);
      if (response.ok) return (await response.json()).webSocketDebuggerUrl;
    } catch {
      // not up yet
    }
    await sleep(500);
  }
  throw new Error("the browser's DevTools endpoint never came up");
}

async function findExtensionWorker(port) {
  for (let attempt = 0; attempt < 30; attempt += 1) {
    const response = await fetch(`http://127.0.0.1:${port}/json/list`);
    const targets = await response.json();
    const worker = targets.find(
      (target) =>
        target.type === "service_worker" && String(target.url).includes("service-worker.js"),
    );
    if (worker) return worker;
    await sleep(500);
  }
  return null;
}

const PAGE_HTML = `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Recorder smoke</title></head>
<body><h1>Recorder smoke</h1></body></html>`;

const INTERACTION = `
(() => {
  const form = document.createElement('form');
  form.setAttribute('aria-label', 'New lead');
  form.innerHTML = [
    '<label for="email">Email</label>',
    '<input id="email" data-testid="lead-email">',
    '<label for="pw">Password</label>',
    '<input id="pw" type="password">',
    '<label for="interest">Interest</label>',
    '<select id="interest" data-testid="lead-interest">',
    '<option>Interest</option><option>Data course</option></select>',
    '<button id="save" type="submit">Save</button>',
  ].join('');
  document.body.appendChild(form);

  const email = document.getElementById('email');
  email.value = 'ravi@example.com';
  email.dispatchEvent(new Event('input', { bubbles: true }));
  email.dispatchEvent(new Event('focusout', { bubbles: true }));

  const pw = document.getElementById('pw');
  pw.value = '${SECRET}';
  pw.dispatchEvent(new Event('input', { bubbles: true }));
  pw.dispatchEvent(new Event('focusout', { bubbles: true }));

  const interest = document.getElementById('interest');
  interest.value = 'Data course';
  interest.dispatchEvent(new Event('change', { bubbles: true }));

  document.getElementById('save').dispatchEvent(new MouseEvent('click', { bubbles: true }));
  return 'interaction done';
})()`;

async function runSmoke(browserPath) {
  const server = createServer((_request, response) => {
    response.writeHead(200, { "content-type": "text/html; charset=utf-8" });
    response.end(PAGE_HTML);
  });
  await new Promise((done) => server.listen(0, "127.0.0.1", done));
  const pageUrl = `http://127.0.0.1:${server.address().port}/form.html`;

  const profile = mkdtempSync(join(tmpdir(), "pl-smoke-"));
  const child = spawn(
    browserPath,
    [
      "--headless=new",
      "--disable-gpu",
      "--no-first-run",
      "--no-default-browser-check",
      `--user-data-dir=${profile}`,
      `--remote-debugging-port=${DEBUG_PORT}`,
      `--disable-extensions-except=${dist}`,
      `--load-extension=${dist}`,
      pageUrl,
    ],
    { stdio: ["ignore", "pipe", "pipe"] },
  );

  const result = { browser: browserPath, ok: false, reason: "", events: [] };
  let socket;
  try {
    const wsUrl = await waitForDevTools(DEBUG_PORT);
    socket = new WebSocket(wsUrl);
    await new Promise((done, fail) => {
      socket.addEventListener("open", done, { once: true });
      socket.addEventListener("error", fail, { once: true });
    });
    const cdp = new Cdp(socket);
    await sleep(2500); // give the browser time to install the extension

    const worker = await findExtensionWorker(DEBUG_PORT);
    if (!worker) {
      result.reason = "the extension's service worker never started (extension not loaded)";
      return result;
    }
    const extensionId = new URL(worker.url).host;
    result.extensionId = extensionId;

    const page = (await cdp.send("Target.getTargets")).targetInfos.find(
      (target) => target.type === "page" && target.url.startsWith("http://127.0.0.1"),
    );
    if (!page) throw new Error("the smoke page target was not found");
    const pageSession = (
      await cdp.send("Target.attachToTarget", { targetId: page.targetId, flatten: true })
    ).sessionId;
    await cdp.send("Runtime.enable", {}, pageSession);

    const popup = await cdp.send("Target.createTarget", {
      url: `chrome-extension://${extensionId}/popup/popup.html`,
    });
    const popupSession = (
      await cdp.send("Target.attachToTarget", { targetId: popup.targetId, flatten: true })
    ).sessionId;
    await cdp.send("Runtime.enable", {}, popupSession);
    await sleep(700);

    await cdp.evaluate(popupSession, "document.getElementById('start').click(); 'started'");
    await sleep(600);

    await cdp.evaluate(pageSession, INTERACTION);
    await sleep(1200);

    const stored = await cdp.evaluate(
      popupSession,
      "(async () => { const r = await chrome.runtime.sendMessage({type:'record:get'}); return JSON.stringify(r); })()",
    );
    const events = JSON.parse(stored).events ?? [];
    result.events = events;

    const problems = [];
    const kinds = events.map((event) => event.kind);
    if (events.length === 0) problems.push("no events were captured");
    if (!kinds.includes("click")) problems.push("no click event");
    if (!kinds.includes("fill")) problems.push("no fill event");
    if (!kinds.includes("select")) problems.push("no select event");
    if (JSON.stringify(events).includes(SECRET)) problems.push("a password value was captured");
    result.reason = problems.join("; ");
    result.ok = problems.length === 0;
    return result;
  } catch (error) {
    result.reason = error instanceof Error ? error.message : String(error);
    return result;
  } finally {
    try {
      socket?.close();
    } catch {
      // already closed
    }
    child.kill();
    server.close();
    await sleep(500);
    try {
      rmSync(profile, { recursive: true, force: true });
    } catch {
      // best effort
    }
  }
}

async function main() {
  if (!existsSync(resolve(dist, "manifest.json"))) {
    console.error("Build first: npm run build");
    process.exit(2);
  }

  const found = BROWSER_CANDIDATES.filter((candidate) => existsSync(candidate));
  if (found.length === 0) {
    console.error("SKIP: no Chromium-family browser found; set CHROME_PATH to run this check.");
    process.exit(2);
  }

  for (const browser of found) {
    console.log(`\n=== ${browser}`);
    const result = await runSmoke(browser);
    if (result.ok) {
      console.log(`loaded extension: ${result.extensionId}`);
      console.log(`captured ${result.events.length} event(s):`);
      for (const event of result.events) {
        console.log(`  #${event.seq} ${event.kind} -> ${event.step?.action ?? "no step"}`);
      }
      console.log("\nend-to-end smoke passed in a real browser");
      process.exit(0);
    }
    console.log(`  not usable: ${result.reason}`);
  }

  console.error("\nend-to-end smoke FAILED in every available browser");
  process.exit(1);
}

await main();
