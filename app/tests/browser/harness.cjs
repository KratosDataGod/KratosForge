// Opens the real app UI (app/ui/index.html) in Chromium with the desktop shell replaced by a stub,
// so pages can be driven and screenshotted without Windows, Tauri or a Fabric tenant.
// Engine calls listed in `engine` go to the real Python engine (python -m fabric_desktop.server);
// every other call is answered by `mocks` (a method name -> function(params) map) or fails, as
// it does when the engine can't reach Fabric.
//
// Needs Playwright and a Chromium: NODE_PATH="$(npm root -g)" node --test app/tests/browser
const path = require("node:path");
const fs = require("node:fs");
const os = require("node:os");
const readline = require("node:readline");
const { spawn } = require("node:child_process");

let playwright = null;
try { playwright = require("playwright"); } catch { /* not installed: tests skip */ }

const ROOT = path.join(__dirname, "..", "..", "..");
const INDEX = path.join(ROOT, "app", "ui", "index.html");

// The Python engine, one JSON request per line, as the shell runs it.
function startEngine() {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "fd-engine-"));
  const python = process.env.FABRIC_DESKTOP_PYTHON || (process.platform === "win32" ? "python" : "python3");
  const proc = spawn(python, ["-m", "fabric_desktop.server"], {
    cwd: path.join(ROOT, "sidecar"),
    env: { ...process.env, LOCALAPPDATA: home, HOME: home, USERPROFILE: home },
    stdio: ["pipe", "pipe", "inherit"],
  });
  const waiting = new Map();
  let next = 1;
  readline.createInterface({ input: proc.stdout }).on("line", (line) => {
    const reply = JSON.parse(line);
    const done = waiting.get(reply.id);
    waiting.delete(reply.id);
    if (done) done(reply);
  });
  return {
    call(method, params) {
      const id = next++;
      return new Promise((resolve) => {
        waiting.set(id, resolve);
        proc.stdin.write(JSON.stringify({ id, method, params }) + "\n");
      });
    },
    stop() { proc.kill(); fs.rmSync(home, { recursive: true, force: true }); },
  };
}

async function openApp({ mocks = {}, engine = [], storage = {}, viewport = { width: 1440, height: 940 } } = {}) {
  const browser = await playwright.chromium.launch({ executablePath: process.env.CHROMIUM_PATH || undefined });
  const page = await browser.newPage({ viewport });
  const calls = [];
  mocks = { environment: () => ({ python: "Python 3 (test)", user: null }), ...mocks };
  const real = engine.length ? startEngine() : null;
  await page.exposeFunction("__fdCall", async (method, params) => {
    calls.push({ method, params });
    if (real && engine.includes(method)) {
      const reply = await real.call(method, params);
      return reply.error ? { error: reply.error } : { result: reply.result };
    }
    try {
      const fn = mocks[method];
      if (!fn) return { error: `${method} isn't available in this test` };
      return { result: await fn(params || {}) };
    } catch (err) {
      return { error: String(err.message || err) };
    }
  });
  await page.addInitScript(({ storage }) => {
    for (const [k, v] of Object.entries(storage)) localStorage.setItem(k, v);
    const shell = {
      check_components: { app: { version: "0.0.0-test", commit: "" }, sidecar: { version: "test", editable: true },
        latest: { channel: "stable" }, python_ok: true, python: "Python 3 (test)" },
    };
    window.__TAURI__ = {
      core: {
        invoke: async (cmd, args) => {
          // The real shell answers after every script has loaded; so does this one.
          if (document.readyState === "loading") await new Promise((r) => addEventListener("DOMContentLoaded", r));
          if (cmd !== "rpc") return shell[cmd] ?? null;
          const out = await window.__fdCall(args.method, args.params);
          if (out.error) throw out.error;
          return out.result;
        },
      },
      event: { listen: async () => () => {} },
    };
  }, { storage });
  const errors = [];
  page.on("pageerror", (err) => errors.push(String(err)));
  await page.goto(`file://${INDEX}`);
  await page.waitForFunction(() => typeof openSqlQuery === "function");
  return {
    page, calls, errors,
    async close() { await browser.close(); real?.stop(); },
  };
}

module.exports = { playwright, openApp };
