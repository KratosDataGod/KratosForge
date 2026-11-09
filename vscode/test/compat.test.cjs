// Which extension works with which app (docs/vscode.md, "Versions"): the extension's client against the real
// engine, with the engine's channel API set to what an older or newer KratosForge would report. Each row of the
// table in docs/vscode.md is a test here.
//
// - The extension sends the API version it speaks in its hello; the app answers with its own.
// - The app turns away an extension older than OLDEST_CLIENT_API (an extension from before versions were
//   sent counts as 1). The extension turns away an app that reports a newer API than its own.
// - Either way the window says which one to update, and sends no hello and no token to an app it turned away.
const { test, after } = require("node:test");
const assert = require("node:assert");
const crypto = require("node:crypto");
const fs = require("node:fs");
const http = require("node:http");
const os = require("node:os");
const path = require("node:path");
const readline = require("node:readline");
const { spawn } = require("node:child_process");
const { Channel, ChannelError, API_VERSION, readPortFile } = require("../out/channel.js");

const SIDECAR = path.join(__dirname, "..", "..", "sidecar");
const started = [];
after(() => { for (const e of started) e.stop(); });

// The real engine, with its channel's versions set as `api` and `oldest` say. `old` drops the version from the
// hello's answer, as an app from before versions were reported answered.
function engine({ api = null, oldest = null, old = false } = {}) {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "kf-compat-"));
  const python = process.env.FABRIC_DESKTOP_PYTHON || (process.platform === "win32" ? "python" : "python3");
  const patch = [
    "import runpy",
    "from fabric_desktop import editor_channel as ec",
    api === null ? "" : `ec.API_VERSION = ${api}`,
    oldest === null ? "" : `ec.OLDEST_CLIENT_API = ${oldest}`,
    old ? [
      "_route = ec.Channel.route",
      "def route(self, method, path, *a):",
      "    out = _route(self, method, path, *a)",
      "    if path == '/v1/hello': out.pop('api', None)",
      "    return out",
      "ec.Channel.route = route",
    ].join("\n") : "",
    "runpy.run_module('fabric_desktop.server', run_name='__main__', alter_sys=True)",
  ].join("\n");
  const proc = spawn(python, ["-c", patch], { cwd: SIDECAR,
    env: { ...process.env, LOCALAPPDATA: home, HOME: home, USERPROFILE: home, ProgramData: path.join(home, "pd") },
    stdio: ["pipe", "pipe", "inherit"] });
  const waiting = new Map();
  let next = 1;
  readline.createInterface({ input: proc.stdout }).on("line", (line) => {
    let reply;
    try { reply = JSON.parse(line); } catch { return; }
    waiting.get(reply.id)?.(reply);
    waiting.delete(reply.id);
  });
  const e = {
    dir: path.join(home, "FabricDesktop"),
    call: (method, params = {}) => new Promise((resolve, reject) => {
      const id = next++;
      waiting.set(id, (r) => (r.error ? reject(new Error(r.error)) : resolve(r.result)));
      proc.stdin.write(JSON.stringify({ id, method, params }) + "\n");
    }),
    stop: () => proc.kill(),
  };
  started.push(e);
  return e;
}

async function listening(e) {
  await e.call("editor_set_enabled", { enabled: true });
  const where = readPortFile(e.dir);
  assert.ok(where, "the app wrote editor-port");
  return where;
}

// Pairs a window as the person would in the app, and returns its channel.
async function paired(e) {
  const ch = new Channel(e.dir);
  const asked = ch.pair(e.dir, "KratosForge for VS Code (test)", () => {}, () => false, 50);
  for (let i = 0; i < 200; i++) {
    const { pairing } = await e.call("editor_pending");
    if (pairing.length) {
      await e.call("editor_decide_pairing", { request_id: pairing[0].id, approve: true, keep: "session" });
      break;
    }
    await new Promise((r) => setTimeout(r, 50));
  }
  await asked;
  return ch;
}

// The hello as an extension sends it, with whatever `api` it sends (or none, as before versions were sent).
function hello(port, api) {
  const nonce = crypto.randomBytes(24).toString("hex");
  const q = api === undefined ? "" : `&api=${encodeURIComponent(api)}`;
  return new Promise((resolve, reject) => {
    http.get({ host: "127.0.0.1", port, path: `/v1/hello?nonce=${nonce}${q}`, headers: { Host: `127.0.0.1:${port}` } }, (res) => {
      const chunks = [];
      res.on("data", (c) => chunks.push(c));
      res.on("end", () => resolve({ status: res.statusCode, body: JSON.parse(Buffer.concat(chunks).toString("utf8")), nonce }));
    }).on("error", reject);
  });
}

test("this extension and this app: paired, and the status call goes through", async () => {
  const e = engine();
  await listening(e);
  const ch = await paired(e);
  assert.strictEqual((await ch.status()).app, "KratosForge");
});

test("this extension and an app from before versions were reported: taken as API 1, so it works", async () => {
  const e = engine({ old: true });
  const where = await listening(e);
  assert.strictEqual((await hello(where.port, API_VERSION)).body.result.api, undefined);
  const ch = await paired(e);
  assert.strictEqual((await ch.status()).app, "KratosForge");
});

test("this extension and a newer app that still serves it: the window stops and says to update the extension", async () => {
  const e = engine({ api: API_VERSION + 1, oldest: API_VERSION });
  await listening(e);
  const ch = new Channel(e.dir);
  await assert.rejects(ch.pair(e.dir, "t", () => {}, () => false, 50),
    (err) => err instanceof ChannelError && err.code === "app_too_new" && /kratosforge\.vsix/.test(err.message));
  assert.deepStrictEqual((await e.call("editor_pending")).pairing, [], "a pairing request reached the app");
});

test("this extension and a newer app that no longer serves it: the app says to update the extension", async () => {
  const e = engine({ api: API_VERSION + 1, oldest: API_VERSION + 1 });
  await listening(e);
  const ch = new Channel(e.dir);
  await assert.rejects(ch.pair(e.dir, "t", () => {}, () => false, 50),
    (err) => err instanceof ChannelError && err.code === "extension_too_old" && /kratosforge\.vsix/.test(err.message));
  assert.deepStrictEqual((await e.call("editor_pending")).pairing, [], "a pairing request reached the app");
});

test("an extension from before versions were sent: served while the app serves API 1, told to update after", async () => {
  const today = engine();
  let where = await listening(today);
  let got = await hello(where.port, undefined);
  assert.strictEqual(got.status, 200);
  assert.strictEqual(got.body.result.api, API_VERSION);
  const newer = engine({ api: API_VERSION + 1, oldest: API_VERSION + 1 });
  where = await listening(newer);
  got = await hello(where.port, undefined);
  assert.strictEqual(got.status, 426);
  assert.strictEqual(got.body.error.code, "extension_too_old");
  assert.ok(!JSON.stringify(got.body).includes("proof"));
});

test("a garbled version in the hello is refused, and proves nothing", async () => {
  const e = engine();
  const where = await listening(e);
  for (const api of ["abc", "1.5", "-1", "", "²", "9999999"]) {
    const got = await hello(where.port, api);
    if (api === "") {  // "api=" is no version at all: an extension from before versions were sent
      assert.strictEqual(got.status, 200, "api=");
      continue;
    }
    assert.strictEqual(got.status, 400, api);
    assert.strictEqual(got.body.error.code, "invalid_input", api);
    assert.ok(!JSON.stringify(got.body).includes("proof"), api);
  }
});

test("a missing or garbled editor-port: the window says the app isn't listening and sends nothing", async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "kf-port-"));
  const ch = new Channel(dir);
  ch.token = "kfp_secret";
  for (const text of [null, "", "garbled", "0 hello", "70000 hello", "1234", "12.5 hello"]) {
    if (text === null) fs.rmSync(path.join(dir, "editor-port"), { force: true });
    else fs.writeFileSync(path.join(dir, "editor-port"), text);
    await assert.rejects(ch.status(), (err) => err instanceof ChannelError && err.code === "not_listening", String(text));
  }
});

test("a garbled answer to the hello: no token is sent, and the window says what it found", async () => {
  const sent = [];
  let answer;
  const server = http.createServer((req, res) => {
    const url = new URL(req.url, "http://x");
    if (url.pathname !== "/v1/hello") sent.push(req.headers.authorization || req.headers["x-kratosforge-hello"]);
    const nonce = url.searchParams.get("nonce") || "";
    const proof = crypto.createHmac("sha256", "the-hello").update(nonce).digest("hex");
    const body = answer(proof);
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(typeof body === "string" ? body : JSON.stringify(body));
  });
  await new Promise((r) => server.listen(0, "127.0.0.1", r));
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "kf-garbled-"));
  fs.writeFileSync(path.join(dir, "editor-port"), `${server.address().port} the-hello`);
  const ch = new Channel(dir);
  ch.token = "kfp_secret";
  const cases = [
    [() => "not json", "not_kratosforge"],
    [() => ({ ok: true }), "not_kratosforge"],
    [() => ({ ok: true, result: { proof: "00".repeat(32), api: 1 } }), "not_kratosforge"],
    [() => ({ ok: true, result: { proof: "zz", api: 1 } }), "not_kratosforge"],
    ...[0, -1, 1.5, "1", null, true, [1], {}].map((api) => [(proof) => ({ ok: true, result: { proof, api } }), "bad_reply"]),
    [(proof) => ({ ok: true, result: { proof, api: API_VERSION + 1 } }), "app_too_new"],
  ];
  try {
    for (const [make, code] of cases) {
      answer = make;
      await assert.rejects(ch.status(), (err) => err instanceof ChannelError && err.code === code, `${make.toString()} -> ${code}`);
    }
  } finally {
    server.close();
  }
  assert.deepStrictEqual(sent, [], "a token or the hello went to an app the window turned away");
});
