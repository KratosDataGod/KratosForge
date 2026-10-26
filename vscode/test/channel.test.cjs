// The extension's client (src/channel.ts) against the real KratosForge engine and its editor
// channel: pairing waits for the person (answered here as the app would), then cells run in the
// notebook's session and stream their output; a write to OneLake asks in the app.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const readline = require("node:readline");
const { spawn } = require("node:child_process");
const { Channel, ChannelError, readPortFile } = require("../out/channel.js");

const SIDECAR = path.join(__dirname, "..", "..", "sidecar");
let engine, home, dir, file;

function startEngine() {
  home = fs.mkdtempSync(path.join(os.tmpdir(), "kf-vscode-"));
  const python = process.env.FABRIC_DESKTOP_PYTHON || (process.platform === "win32" ? "python" : "python3");
  const proc = spawn(python, ["-m", "fabric_desktop.server"], { cwd: SIDECAR,
    env: { ...process.env, LOCALAPPDATA: home, HOME: home, USERPROFILE: home, ProgramData: path.join(home, "pd") },
    stdio: ["pipe", "pipe", "inherit"] });
  const waiting = new Map();
  let next = 1;
  readline.createInterface({ input: proc.stdout }).on("line", (line) => {
    const reply = JSON.parse(line);
    waiting.get(reply.id)?.(reply);
    waiting.delete(reply.id);
  });
  return {
    call: (method, params = {}) => new Promise((resolve, reject) => {
      const id = next++;
      waiting.set(id, (r) => (r.error ? reject(new Error(r.error)) : resolve(r.result)));
      proc.stdin.write(JSON.stringify({ id, method, params }) + "\n");
    }),
    stop: () => proc.kill(),
  };
}

// The person, in the app: pairs the first window that asks (or denies it).
async function answer(approve, keep = "session") {
  for (let i = 0; i < 200; i++) {
    const { pairing } = await engine.call("editor_pending");
    if (pairing.length) return engine.call("editor_decide_pairing", { request_id: pairing[0].id, approve, keep });
    await new Promise((r) => setTimeout(r, 50));
  }
  throw new Error("no pairing request reached the app");
}

before(async () => {
  engine = startEngine();
  dir = path.join(home, "FabricDesktop");
  await engine.call("save_notebook", { name: "Daily", cells: [{ type: "code", source: "x = 1" }] });
  file = (await engine.call("notebook_file_path", { name: "Daily" })).path;
});

after(() => engine?.stop());

test("with the switch off there's nothing to talk to, and the message says what to do", async () => {
  assert.strictEqual(readPortFile(dir), null);
  await assert.rejects(new Channel(dir).status(), (err) => err instanceof ChannelError && err.code === "not_listening"
    && /Let VS Code run notebooks is off/.test(err.message));
});

test("a denied window stays unpaired", async () => {
  await engine.call("editor_set_enabled", { enabled: true });
  const ch = new Channel(dir);
  const asked = ch.pair("C:\\repos\\other", "KratosForge for VS Code 0.1.0", () => {}, () => false, 50);
  await answer(false);
  await assert.rejects(asked, /denied/);
  assert.strictEqual(ch.token, "");
});

test("a paired window runs cells in the notebook's session and streams their output", async () => {
  const ch = new Channel(dir);
  let code = "";
  const paired = ch.pair(home, "KratosForge for VS Code 0.1.0", (c) => { code = c; }, () => false, 50);
  await answer(true, "8h");
  assert.deepStrictEqual((await paired).keep, "8h");
  assert.match(code, /^[A-Z0-9]{3}-[A-Z0-9]{3}$/);
  assert.deepStrictEqual(await ch.notebook(file), { name: "Daily" });
  const seen = [];
  const last = await ch.run(file, "import time\nfor i in range(3):\n    print(i, flush=True)\n    time.sleep(0.4)\n41 + 1", (s) => seen.push(s));
  assert.strictEqual(last.status, "ok", JSON.stringify(seen.at(-1)));
  const outputs = seen.filter((s) => Array.isArray(s.outputs)).at(-1).outputs;
  assert.deepStrictEqual(outputs.map((o) => o.type), ["stream", "text"]);
  assert.strictEqual(outputs[0].text, "0\n1\n2\n");
  assert.strictEqual(outputs[1].text, "42");
  assert.ok(seen.length > 2, "output arrived while the cell ran");
  const vars = await engine.call("kernel_variables", { notebook: "Daily" });
  assert.ok(vars.some((v) => v.name === "i"), "the app's session has the cell's variables");
  // Stop
  const running = ch.run(file, "import time\nfor _ in range(300):\n    time.sleep(0.1)", () => {});
  await new Promise((r) => setTimeout(r, 500));
  await ch.interrupt(file);
  assert.strictEqual((await running).status, "cancelled");
  // The token works for a new window too (kept 8 hours), until the pairing ends.
  const again = new Channel(dir);
  again.token = ch.token;
  assert.strictEqual((await again.status()).app, "KratosForge");
  await ch.unpair();
  await assert.rejects(again.status(), (err) => err.code === "not_paired");
});

test("a file KratosForge doesn't know isn't run", async () => {
  const ch = new Channel(dir);
  const paired = ch.pair(home, "t", () => {}, () => false, 50);
  await answer(true);
  await paired;
  const stray = path.join(home, "stray.ipynb");
  fs.writeFileSync(stray, '{"cells": []}');
  await assert.rejects(ch.start(stray, "print(1)"), (err) => err.status === 404);
});

test("a window reaches only notebooks in the folder it paired from", async () => {
  const other = fs.mkdtempSync(path.join(os.tmpdir(), "kf-other-"));
  const ch = new Channel(dir);
  const paired = ch.pair(other, "t", () => {}, () => false, 50);
  await answer(true);
  await paired;
  await assert.rejects(ch.start(file, "print(1)"), (err) => err.status === 403 && /paired from/.test(err.message));
});

test("a program that took the port gets neither the hello nor a token", async () => {
  const fake = fs.mkdtempSync(path.join(os.tmpdir(), "kf-fake-"));
  const seen = [];
  const http = require("node:http");
  const server = http.createServer((req, res) => {
    seen.push(req.headers);
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ ok: true, result: { proof: "00".repeat(32), id: "pq_x", code: "AAA-BBB", expires_in: 1 } }));
  });
  await new Promise((r) => server.listen(0, "127.0.0.1", r));
  fs.writeFileSync(path.join(fake, "editor-port"), `${server.address().port} the-real-hello`);
  const ch = new Channel(fake);
  ch.token = "kfp_secret";
  await assert.rejects(ch.ask("C:\\repos", "t"), (err) => err.code === "not_kratosforge");
  await assert.rejects(ch.status(), (err) => err.code === "not_kratosforge");
  server.close();
  assert.ok(seen.length >= 1);
  for (const h of seen) {
    assert.strictEqual(h["x-kratosforge-hello"], undefined);
    assert.strictEqual(h.authorization, undefined);
  }
});
