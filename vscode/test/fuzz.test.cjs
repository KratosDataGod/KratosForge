// Hostile answers at the extension's readers of what comes from "KratosForge" (WR-507): the editor-port
// file, the channel's replies (src/channel.ts) and cell outputs (src/outputs.ts). Whatever is listening
// on the port, and whatever it sends once it has proven it knows the hello, each call either returns
// what its caller reads or fails with a ChannelError that has a name and words a person can act on,
// never a TypeError or a value of the wrong shape; and every output becomes items VS Code can show.
//
// The fuzz is seeded (SEEDS), so a failure names its seed and the same answers come out on every run.
// The links the extension opens are covered in links.test.cjs; the app's side of the channel and the
// kratosforge:// readers in sidecar/tests/test_trust_surface_fuzz.py and deep_link.rs.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const crypto = require("node:crypto");
const fs = require("node:fs");
const http = require("node:http");
const os = require("node:os");
const path = require("node:path");
const { Channel, ChannelError, readPortFile } = require("../out/channel.js");
const { convert, failed } = require("../out/outputs.js");

const SEEDS = 40;
const HELLO = "hello_" + "a".repeat(32);

// A small seeded generator (mulberry32), so each seed gives the same values every run.
function rng(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
const pick = (r, list) => list[Math.floor(r() * list.length)];

const STRINGS = ["", " ", "x", "ok", "running", "paused", "error", "idle", "paired", "denied", "expired", "waiting",
  "session", "8h", "7d", "kfp_" + "A".repeat(43), "kfp_short", "Bearer x", "a\r\nX-Injected: 1", "\u0000", "\u202e",
  "\ud800", "%", "../..", "<script>alert(1)</script>", "x".repeat(5000), "[object Object]", "__proto__", "constructor"];
const NUMBERS = [0, -1, 1, 1.5, 2 ** 53, -(2 ** 53), Number.MAX_VALUE, 1e308, 65536, 3];

// Any JSON value, a few levels deep, from pieces that have broken readers before.
function value(r, depth = 0) {
  const k = r();
  if (k < 0.12) return null;
  if (k < 0.2) return r() < 0.5;
  if (k < 0.35) return pick(r, NUMBERS);
  if (k < 0.6 || depth > 3) return pick(r, STRINGS);
  if (k < 0.75) return Array.from({ length: Math.floor(r() * 4) }, () => value(r, depth + 1));
  const keys = ["ok", "result", "error", "code", "message", "status", "outputs", "version", "execution_count", "seconds",
    "id", "expires_in", "state", "token", "keep", "name", "proof", "api", "type", "text", "mime", "data", "columns", "rows",
    "__proto__", "constructor"];
  const out = {};
  for (let i = Math.floor(r() * 5); i > 0; i--) out[pick(r, keys)] = value(r, depth + 1);
  return out;
}

// A reply body: mostly JSON shaped like the channel's own, sometimes not JSON at all.
function body(r) {
  const k = r();
  if (k < 0.1) return pick(r, ["", "{", "not json", "null", "[]", "\"ok\"", "5", "true", "{\"ok\":true}", "\u0000\u0000"]);
  if (k < 0.45) return JSON.stringify({ ok: true, result: value(r) });
  if (k < 0.8) return JSON.stringify({ ok: false, error: value(r) });
  return JSON.stringify(value(r));
}

// "KratosForge": proves it knows the hello, then answers each other call with `reply()`.
let server, port, home, dir, reply = () => ({ status: 200, body: "{}" });
before(async () => {
  home = fs.mkdtempSync(path.join(os.tmpdir(), "kf-fuzz-"));
  dir = path.join(home, "FabricDesktop");
  fs.mkdirSync(dir);
  server = http.createServer((req, res) => {
    req.resume();
    req.on("end", () => {
      const url = new URL(req.url, "http://127.0.0.1");
      if (url.pathname === "/v1/hello") {
        const proof = crypto.createHmac("sha256", HELLO).update(url.searchParams.get("nonce") || "").digest("hex");
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ ok: true, result: { proof, api: 1 } }));
        return;
      }
      const { status, body: text } = reply();
      res.writeHead(status, { "Content-Type": "application/json" });
      res.end(text);
    });
  });
  await new Promise((r) => server.listen(0, "127.0.0.1", r));
  port = server.address().port;
  fs.writeFileSync(path.join(dir, "editor-port"), `${port} ${HELLO}`);
});
after(() => server.close());

// A call either gives its caller what it reads, or a ChannelError a person can act on.
async function closed(call, check) {
  let got;
  try {
    got = await call();
  } catch (err) {
    assert.ok(err instanceof ChannelError, `not a ChannelError: ${err && err.stack}`);
    assert.ok(typeof err.code === "string" && /^[a-z_]+$/.test(err.code), `code ${String(err.code)}`);
    assert.ok(typeof err.message === "string" && err.message.length > 20 && !err.message.includes("[object"), err.message);
    return;
  }
  check(got);
}

const isSnapshot = (s) => {
  assert.ok(s && typeof s === "object" && typeof s.status === "string", JSON.stringify(s));
  if (s.outputs !== undefined) assert.ok(Array.isArray(s.outputs) && s.outputs.every((o) => o && typeof o === "object" && !Array.isArray(o)));
  for (const k of ["version", "execution_count"]) if (s[k] !== undefined) assert.ok(Number.isInteger(s[k]) && s[k] >= 0, `${k} ${s[k]}`);
  if (s.seconds !== undefined) assert.ok(Number.isFinite(s.seconds) && s.seconds >= 0);
  assert.doesNotThrow(() => failed(s.status, s.outputs ?? []));
};

test("every channel call fails closed on whatever an answer holds", { timeout: 120_000 }, async () => {
  for (let seed = 0; seed < SEEDS; seed++) {
    const r = rng(seed);
    reply = () => ({ status: pick(r, [200, 200, 200, 400, 401, 403, 404, 409, 413, 500, 503]), body: body(r) });
    const channel = new Channel(dir);
    channel.timeout = 5000;
    channel.token = "kfp_" + "t".repeat(43);
    const at = `seed ${seed}`;
    await closed(() => channel.status(), (s) => assert.ok(s && typeof s === "object" && !Array.isArray(s), at));
    await closed(() => channel.notebook("C:\\n\\x.ipynb"), (n) => assert.ok(typeof n?.name === "string" && n.name, at));
    await closed(() => channel.ask("w", "c"), (a) => {
      assert.ok(typeof a.id === "string" && a.id && typeof a.code === "string" && a.code, at);
      assert.ok(Number.isFinite(a.expires_in) && a.expires_in > 0, at);
    });
    await closed(() => channel.collect("pq_1"), (c) => {
      assert.ok(typeof c.state === "string", at);
      if (c.token !== undefined) assert.match(c.token, /^[A-Za-z0-9_-]{16,512}$/, at);
      if (c.keep !== undefined) assert.ok(["session", "8h", "7d"].includes(c.keep), at);
    });
    await closed(() => channel.pair("w", "c", (code) => assert.strictEqual(typeof code, "string"), () => false, 1),
      (p) => assert.match(p.token, /^[A-Za-z0-9_-]{16,512}$/, at));
    await closed(() => channel.start("C:\\n\\x.ipynb", "x = 1"), isSnapshot);
    await closed(() => channel.poll("C:\\n\\x.ipynb"), isSnapshot);
    let last = 0;
    await closed(() => channel.run("C:\\n\\x.ipynb", "x = 1", (s) => { isSnapshot(s); last++; }), (s) => { isSnapshot(s); assert.ok(last <= 50); });
  }
});

test("an editor-port file that isn't the app's port and hello is no port", () => {
  const r = rng(7);
  const bad = ["", "\n", "0 h", "65536 hello_aaaaaaaaaaaaaaaa", "-1 x", "1.5 hello_aaaaaaaaaaaaaaaa", "80", "80 ", "x y",
    `80 ${"a".repeat(5000)}`, "80 hello\r\nX: 1", "80 hel\u0000lo_aaaaaaaaaaaaaaaa", "80 \u202ehello_aaaaaaaaaaaa", "80 sh ort", "80 a b"];
  for (let i = 0; i < 200; i++) bad.push(Array.from({ length: Math.floor(r() * 30) }, () => String.fromCharCode(Math.floor(r() * 0x2100))).join(""));
  const d = fs.mkdtempSync(path.join(os.tmpdir(), "kf-port-"));
  for (const text of bad) {
    fs.writeFileSync(path.join(d, "editor-port"), text);
    const got = readPortFile(d);
    if (got) {
      assert.ok(Number.isInteger(got.port) && got.port > 0 && got.port < 65536, JSON.stringify(text));
      assert.match(got.hello, /^[A-Za-z0-9_-]{1,256}$/, JSON.stringify(text));
    }
  }
  fs.writeFileSync(path.join(d, "editor-port"), `51234 ${HELLO}\n`);
  assert.deepStrictEqual(readPortFile(d), { port: 51234, hello: HELLO });
});

test("a port file whose hello isn't one is refused by name before anything is sent", async () => {
  const d = fs.mkdtempSync(path.join(os.tmpdir(), "kf-port-"));
  fs.writeFileSync(path.join(d, "editor-port"), `${port} hello\r\nX-Injected: 1`);
  await assert.rejects(new Channel(d).status(), (err) => err instanceof ChannelError && err.code === "not_listening");
});

const IMAGE = new Set(["image/png", "image/jpeg", "image/gif", "image/webp", "image/svg+xml"]);
const MIMES = new Set([...IMAGE, "text/plain", "text/html", "text/markdown", "application/json", "application/vnd.code.notebook.stdout",
  "application/vnd.vegalite.v5+json", "application/vnd.plotly.v1+json"]);

test("every output, however it's shaped, becomes items VS Code can show", () => {
  for (let seed = 0; seed < SEEDS * 25; seed++) {
    const r = rng(seed);
    const out = value(r);
    if (out && typeof out === "object" && !Array.isArray(out) && r() < 0.8) {
      out.type = pick(r, ["stream", "text", "markdown", "html", "image", "json", "table", "vegalite", "plotly", "error", "widget", "new", 5, null]);
      if (out.type === "image" && r() < 0.5) out.mime = pick(r, ["text/html", "application/javascript", "image/png", "IMAGE/PNG", "image/svg+xml", " image/png", 5]);
      if (out.type === "table" && r() < 0.5) { out.columns = [value(r), "a", 5]; out.rows = [[value(r), value(r)], value(r)]; }
    }
    let got;
    assert.doesNotThrow(() => { got = convert(out); }, `seed ${seed}`);
    if ("error" in got) {
      for (const k of ["name", "message"]) assert.strictEqual(typeof got.error[k], "string", `seed ${seed}`);
      assert.ok(got.error.stack === undefined || typeof got.error.stack === "string");
      continue;
    }
    assert.ok(Array.isArray(got.items) && got.items.length, `seed ${seed}`);
    for (const item of got.items) {
      assert.ok(MIMES.has(item.mime), `seed ${seed}: mime ${item.mime}`);
      assert.ok(typeof item.text === "string" || item.bytes instanceof Uint8Array, `seed ${seed}`);
      if (item.bytes) assert.ok(IMAGE.has(item.mime), `seed ${seed}: bytes as ${item.mime}`);
      if (item.text !== undefined) assert.ok(!item.text.includes("[object Object]") || JSON.stringify(out).includes("[object Object]"), `seed ${seed}`);
    }
  }
});

test("an image output only ever reaches VS Code as an image", () => {
  const data = Buffer.from("<script>alert(1)</script>").toString("base64");
  for (const mime of ["text/html", "application/javascript", "application/vnd.code.notebook.stdout", "IMAGE/PNG", " image/png", "image/png\n"]) {
    for (const item of convert({ type: "image", mime, data }).items) assert.ok(!item.bytes || IMAGE.has(item.mime), mime);
    assert.ok(convert({ type: "image", mime, data }).items.every((i) => i.mime === "text/plain" || IMAGE.has(i.mime)), mime);
  }
});
