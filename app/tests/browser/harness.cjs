// Opens the real app UI (app/ui/index.html) in Chromium with the desktop shell replaced by a stub,
// so pages can be driven and screenshotted without Windows, Tauri or a Fabric tenant.
// Engine calls listed in `engine` go to the real Python engine (python -m fabric_desktop.server);
// every other call is answered by `mocks` (a method name -> function(params) map) or fails, as
// it does when the engine can't reach Fabric.
//
// Needs Playwright and a Chromium: NODE_PATH="$(npm root -g)" node --test --test-concurrency=1 "app/tests/browser/*.test.cjs"
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

// holdCheck: the component and update check doesn't answer until the test calls
// page.evaluate(() => __fdReleaseCheck()), as a slow release check would (UX-020).
async function openApp({ mocks = {}, engine = [], storage = {}, viewport = { width: 1440, height: 940 }, holdCheck = false } = {}) {
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
  // The one-time KratosForge announcement covers the window; only its own test wants it.
  storage = { "fd.rename.kratosforge.seen": "1", ...storage };
  await page.addInitScript(({ storage, holdCheck }) => {
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
    if (holdCheck) {
      const held = new Promise((resolve) => { window.__fdReleaseCheck = resolve; });
      const invoke = window.__TAURI__.core.invoke;
      window.__TAURI__.core.invoke = async (cmd, args) => {
        if (cmd === "check_components") await held;
        return invoke(cmd, args);
      };
    }
  }, { storage, holdCheck });
  const errors = [];
  page.on("pageerror", (err) => errors.push(String(err)));
  await page.goto(`file://${INDEX}`);
  await page.waitForFunction(() => typeof openSqlQuery === "function");
  return {
    page, calls, errors,
    async close() { await browser.close(); real?.stop(); },
  };
}

// Engine calls for the SQL page's managed runs (SQL-003) that finish at once: `answer(params)` returns
// { columns, rows } for a query, or throws to fail it with that message.
function sqlRunMocks(answer) {
  const done = new Map();
  let next = 1;
  return {
    sql_start: (params) => {
      const id = `run${next++}`;
      let status;
      try {
        const { columns, rows } = answer(params);
        done.set(id, { columns, rows });
        status = { id, state: "completed", target: "lakehouse", rows: rows.length, columns, seconds: 0.01 };
      } catch (err) {
        status = { id, state: "failed", error: String(err.message || err), error_kind: "sql", rows: 0, columns: [], seconds: 0.01 };
      }
      return status;
    },
    sql_status: ({ execution }) => ({ id: execution, state: "completed", rows: done.get(execution).rows.length, seconds: 0.01 }),
    sql_page: ({ execution, offset = 0, limit = 1000 }) => {
      const { columns, rows } = done.get(execution);
      const page = rows.slice(offset, offset + limit);
      return { id: execution, offset, limit, rows: page, columns, total: rows.length,
        has_previous: offset > 0, has_next: offset + page.length < rows.length };
    },
  };
}

module.exports = { playwright, openApp, sqlRunMocks };
