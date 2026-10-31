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
    home,
    stop() { proc.kill(); fs.rmSync(home, { recursive: true, force: true }); },
  };
}

// The policy the shell sets (tauri.conf.json app.security.csp), with a fresh style nonce added the way
// Tauri adds one: to style-src, and to each <style> in index.html (the editors read it from #style-nonce).
function appCsp(nonce) {
  const conf = JSON.parse(fs.readFileSync(path.join(ROOT, "app", "src-tauri", "tauri.conf.json"), "utf8"));
  const csp = { ...conf.app.security.csp };
  csp["style-src"] = `${csp["style-src"]} 'nonce-${nonce}'`;
  return Object.entries(csp).map(([k, v]) => `${k} ${[].concat(v).join(" ")}`).join("; ");
}

// Serves app/ui from an http origin with the shell's CSP header, so 'self' means the app's own files
// and anything the policy blocks fails here as it would in the packaged app (HD-020).
async function serveWithCsp(page) {
  const nonce = require("node:crypto").randomBytes(16).toString("base64");
  const policy = appCsp(nonce);
  const types = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".png": "image/png",
    ".svg": "image/svg+xml", ".json": "application/json", ".woff2": "font/woff2" };
  await page.route("http://app.test/**", async (route) => {
    const rel = decodeURIComponent(new URL(route.request().url()).pathname).replace(/^\/+/, "");
    const file = path.join(ROOT, "app", "ui", rel);
    if (!file.startsWith(path.join(ROOT, "app", "ui")) || !fs.existsSync(file)) return route.fulfill({ status: 404 });
    let body = fs.readFileSync(file);
    if (rel === "index.html") body = body.toString("utf8").replace(/<style\b/g, `<style nonce="${nonce}"`);
    await route.fulfill({ status: 200, body, headers: {
      "content-type": types[path.extname(file)] || "application/octet-stream", "content-security-policy": policy } });
  });
  return "http://app.test/index.html";
}

// holdCheck: the component and update check doesn't answer until the test calls
// page.evaluate(() => __fdReleaseCheck()), as a slow release check would (UX-020).
// csp: serve the page with the shell's Content Security Policy; `violations` collects what it blocks.
// Use csp for a test that reloads the page and reads localStorage back: from file://, Chromium
// sometimes loses the page's localStorage across a reload under load (back-forward.test.cjs).
// search: added to the page's address, as the shell opens a pop-out window with ?popout=<section>.
// Shell commands (anything but rpc) are recorded in window.__fdShellCalls; window.__fdEmit(event, payload)
// delivers an event to the page's listeners, as the shell's emit does.
async function openApp({ mocks = {}, engine = [], storage = {}, viewport = { width: 1440, height: 940 }, holdCheck = false,
  csp = false, search = "" } = {}) {
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
          if (cmd !== "rpc") { window.__fdShellCalls.push({ cmd, args }); return shell[cmd] ?? null; }
          const out = await window.__fdCall(args.method, args.params);
          if (out.error) throw out.error;
          return out.result;
        },
      },
      event: { listen: async (name, fn) => { (listeners[name] ||= []).push(fn); return () => {}; } },
    };
    window.__fdShellCalls = [];
    const listeners = {};
    window.__fdEmit = (name, payload) => (listeners[name] || []).forEach((fn) => fn({ event: name, payload }));
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
  const violations = [];
  page.on("pageerror", (err) => errors.push(String(err)));
  if (csp) {
    await page.exposeFunction("__fdCspViolation", (v) => { violations.push(v); });
    await page.addInitScript(() => document.addEventListener("securitypolicyviolation", (e) =>
      window.__fdCspViolation(`${e.violatedDirective} blocked ${e.blockedURI || "inline"} (${e.sourceFile || "?"}:${e.lineNumber})`)));
  }
  await page.goto((csp ? await serveWithCsp(page) : `file://${INDEX}`) + search);
  await page.waitForFunction(() => typeof openSqlQuery === "function");
  return {
    page, calls, errors, violations, engineHome: real?.home,
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

// The app reads Source control's repositories once, 1.5 s after it starts (git.js). A test that counts the
// calls one action makes waits for that read first, so it isn't counted when the page starts slowly.
async function startupReads(app) {
  for (const end = Date.now() + 10000; !app.calls.some((c) => c.method === "git_info"); await new Promise((r) => setTimeout(r, 25))) {
    if (Date.now() > end) throw new Error("the app's startup read of Source control (git_info) never came");
  }
}

// Before axe looks: no motion, and no animation still running. Dialogs and panels fade in, and axe
// measures contrast on the colors as drawn, so mid-way it sees faded text (#1297).
async function settleForAxe(page) {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.waitForFunction(() => document.getAnimations().length === 0);
}

module.exports = { playwright, openApp, sqlRunMocks, startupReads, settleForAxe };
