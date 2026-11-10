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
const { Channel, ChannelError, MAX_REPLY, MAX_REQUEST, readPortFile } = require("../out/channel.js");

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
  // Attach the rejection check before answering, so the denial is never unhandled.
  const denied = assert.rejects(asked, /denied/);
  await answer(false);
  await denied;
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

test("a proof isn't remembered: a program that takes the port later gets no token", async () => {
  const fake = fs.mkdtempSync(path.join(os.tmpdir(), "kf-swap-"));
  const crypto = require("node:crypto");
  const http = require("node:http");
  const hello = "the-real-hello";
  let impostor = false;
  const tokens = [];
  const server = http.createServer((req, res) => {
    const url = new URL(req.url, "http://x");
    let result = { app: "KratosForge" };
    if (url.pathname === "/v1/hello") {
      // Until the swap it answers as the app would; after it, as a program that only has the file.
      const key = impostor ? "guess" : hello;
      result = { proof: crypto.createHmac("sha256", key).update(url.searchParams.get("nonce")).digest("hex") };
    } else if (impostor) tokens.push(req.headers.authorization);
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ ok: true, result }));
  });
  await new Promise((r) => server.listen(0, "127.0.0.1", r));
  fs.writeFileSync(path.join(fake, "editor-port"), `${server.address().port} ${hello}`);
  const ch = new Channel(fake);
  ch.token = "kfp_secret";
  await ch.status(); // proven against "the app"
  impostor = true; // same port, same file
  await assert.rejects(ch.status(), (err) => err.code === "not_kratosforge");
  server.close();
  assert.deepStrictEqual(tokens, []);
});

test("an app that speaks a newer channel API gets no token, and the window says which to update", async () => {
  const fake = fs.mkdtempSync(path.join(os.tmpdir(), "kf-api-"));
  const crypto = require("node:crypto");
  const http = require("node:http");
  const { API_VERSION } = require("../out/channel.js");
  const hello = "the-real-hello";
  let answer = "newer";
  const asked = [], tokens = [];
  const server = http.createServer((req, res) => {
    const url = new URL(req.url, "http://x");
    if (url.pathname !== "/v1/hello") {
      tokens.push(req.headers.authorization);
      res.writeHead(200, { "Content-Type": "application/json" });
      return res.end(JSON.stringify({ ok: true, result: {} }));
    }
    asked.push(url.searchParams.get("api"));
    if (answer === "too_old") {
      res.writeHead(426, { "Content-Type": "application/json" });
      return res.end(JSON.stringify({ ok: false, error: { code: "extension_too_old", message: "update" } }));
    }
    const proof = crypto.createHmac("sha256", hello).update(url.searchParams.get("nonce")).digest("hex");
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ ok: true, result: { proof, api: answer === "newer" ? API_VERSION + 1 : "1" } }));
  });
  await new Promise((r) => server.listen(0, "127.0.0.1", r));
  fs.writeFileSync(path.join(fake, "editor-port"), `${server.address().port} ${hello}`);
  const ch = new Channel(fake);
  ch.token = "kfp_secret";
  try {
    await assert.rejects(ch.status(), (err) => err.code === "app_too_new" && /extension/.test(err.message));
    answer = "too_old";
    await assert.rejects(ch.status(), (err) => err.code === "extension_too_old" && /kratosforge\.vsix/.test(err.message));
    answer = "garbled";
    await assert.rejects(ch.status(), (err) => err.code === "bad_reply");
  } finally {
    server.close();
  }
  assert.deepStrictEqual(tokens, []);
  assert.deepStrictEqual(asked, [String(API_VERSION), String(API_VERSION), String(API_VERSION)]);
});

// A fake app that proves itself like the real one, for what the real engine can't be made to do on cue.
async function fakeApp(handle) {
  const crypto = require("node:crypto");
  const http = require("node:http");
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "kf-fake-"));
  const hello = "the-real-hello";
  const server = http.createServer((req, res) => {
    const url = new URL(req.url, "http://x");
    const reply = (status, payload) => {
      res.writeHead(status, { "Content-Type": "application/json" });
      res.end(JSON.stringify(payload));
    };
    if (handle(url, req, res, reply)) return;
    if (url.pathname === "/v1/hello") {
      return reply(200, { ok: true, result: { proof: crypto.createHmac("sha256", hello).update(url.searchParams.get("nonce")).digest("hex"), api: 1 } });
    }
    reply(200, { ok: true, result: { app: "KratosForge" } });
  });
  await new Promise((r) => server.listen(0, "127.0.0.1", r));
  fs.writeFileSync(path.join(dir, "editor-port"), `${server.address().port} ${hello}`);
  const ch = new Channel(dir);
  ch.token = "kfp_t";
  return { ch, close: () => new Promise((r) => server.close(r)) };
}

test("a busy app is asked again a few times, then the window says it's busy", async () => {
  let busy = 3;
  const seen = [];
  const busyReply = (reply) => reply(503, { ok: false, error: { code: "busy", message: "x" } });
  const app = await fakeApp((url, req, res, reply) => {
    seen.push(url.pathname);
    if (busy > 0) { busy--; busyReply(reply); return true; }
    return false;
  });
  try {
    assert.strictEqual((await app.ch.status()).app, "KratosForge"); // 3 turned away, the 4th served
    assert.ok(seen.filter((p) => p === "/v1/hello").length >= 4);
    busy = 100;
    await assert.rejects(app.ch.status(), (err) => err.code === "busy" && /too many/.test(err.message));
  } finally {
    await app.close();
  }
});

test("a cell whose session ends mid-run says so instead of ending ok", async () => {
  let polls = 0;
  const app = await fakeApp((url, req, res, reply) => {
    if (url.pathname === "/v1/run" && req.method === "POST") {
      reply(200, { ok: true, result: { status: "running", version: 3, outputs: [{ type: "stream", text: "half" }] } });
      return true;
    }
    if (url.pathname === "/v1/run") {
      polls++;
      reply(200, { ok: true, result: polls === 1 ? { status: "running", version: 4, outputs: [] }
        : { status: "idle", version: 0, outputs: [] } });
      return true;
    }
    return false;
  });
  try {
    const seen = [];
    await assert.rejects(app.ch.run("C:\\nb\\a.ipynb", "x = 1", (s) => seen.push(s.status)), (err) => err.code === "restarted");
    assert.deepStrictEqual(seen, ["running", "running"]);
  } finally {
    await app.close();
  }
});

test("a connection the app drops mid-call is reported as lost, not as a raw socket error", async () => {
  const app = await fakeApp((url, req) => {
    if (url.pathname === "/v1/status") { req.socket.destroy(); return true; }
    return false;
  });
  try {
    await assert.rejects(app.ch.status(), (err) => err.code === "connection_lost" && /Run the cell again/.test(err.message));
  } finally {
    await app.close();
  }
});

test("an answer larger than the extension accepts is cut off and reported, not held", async () => {
  const app = await fakeApp((url, req, res) => {
    if (url.pathname === "/v1/status") {  // sent in pieces, with no length said up front
      res.writeHead(200, { "Content-Type": "application/json" });
      const piece = Buffer.alloc(1024 * 1024, 0x20);
      let sent = 0;
      const more = () => {
        while (sent <= MAX_REPLY + piece.length && !res.destroyed) {
          sent += piece.length;
          if (!res.write(piece)) { res.once("drain", more); return; }
        }
        res.end();
      };
      res.on("error", () => {});
      more();
      return true;
    }
    if (url.pathname === "/v1/sessions") {  // says its length up front
      res.writeHead(200, { "Content-Type": "application/json", "Content-Length": MAX_REPLY + 1 });
      res.write("{");
      return true;
    }
    return false;
  });
  try {
    await assert.rejects(app.ch.status(), (err) => err instanceof ChannelError && err.code === "too_large");
    await assert.rejects(app.ch.request("GET", "/v1/sessions"), (err) => err instanceof ChannelError && err.code === "too_large");
  } finally {
    await app.close();
  }
});

test("a cell larger than the app takes is refused before anything is sent, in words about the cell", async () => {
  const seen = [];
  const app = await fakeApp((url) => { seen.push(url.pathname); return false; });
  try {
    const big = "x = '" + "é".repeat(MAX_REQUEST / 2) + "'";  // under 1 Mi characters, over 1 MiB as UTF-8
    await assert.rejects(app.ch.run("C:\\nb\\a.ipynb", big, () => {}),
      (err) => err instanceof ChannelError && err.code === "cell_too_large" && /This cell is 1\.1 MB.*up to 1 MB.*Split it/.test(err.message));
    assert.deepStrictEqual(seen, [], "the hello or the cell went out");
  } finally {
    await app.close();
  }
  // The limit is the app's own.
  const src = fs.readFileSync(path.join(SIDECAR, "fabric_desktop", "editor_channel.py"), "utf8");
  assert.strictEqual(eval(src.match(/^MAX_BODY_BYTES = (.+)$/m)[1]), MAX_REQUEST);
});

test("a running cell's poll that drops is asked again, and the cell isn't started twice", async () => {
  let posts = 0;
  let polls = 0;
  const app = await fakeApp((url, req, res, reply) => {
    if (url.pathname === "/v1/run" && req.method === "POST") {
      posts++;
      reply(200, { ok: true, result: { status: "running", version: 1, outputs: [] } });
      return true;
    }
    if (url.pathname === "/v1/run") {
      polls++;
      if (polls <= 2) { req.socket.destroy(); return true; } // two answers lost on the way back
      reply(200, { ok: true, result: { status: "ok", version: 2, outputs: [{ type: "stream", text: "done" }] } });
      return true;
    }
    return false;
  });
  app.ch.pollPause = 10;
  try {
    const last = await app.ch.run("C:\\nb\\a.ipynb", "x = 1", () => undefined);
    assert.strictEqual(last.status, "ok");
    assert.strictEqual(posts, 1);
    assert.strictEqual(polls, 3);
  } finally {
    await app.close();
  }
});

test("a poll that keeps dropping gives up after a few tries and says the connection was lost", async () => {
  let polls = 0;
  const app = await fakeApp((url, req, res, reply) => {
    if (url.pathname === "/v1/run" && req.method === "POST") {
      reply(200, { ok: true, result: { status: "running", version: 1, outputs: [] } });
      return true;
    }
    if (url.pathname === "/v1/run") { polls++; req.socket.destroy(); return true; }
    return false;
  });
  app.ch.pollPause = 10;
  try {
    await assert.rejects(app.ch.run("C:\\nb\\a.ipynb", "x = 1", () => undefined), (err) => err.code === "connection_lost");
    assert.strictEqual(polls, 4);
  } finally {
    await app.close();
  }
});

test("starting a cell is never sent twice, even when its answer is lost", async () => {
  let posts = 0;
  const app = await fakeApp((url, req) => {
    if (url.pathname === "/v1/run" && req.method === "POST") { posts++; req.socket.destroy(); return true; }
    return false;
  });
  try {
    await assert.rejects(app.ch.run("C:\\nb\\a.ipynb", "x = 1", () => undefined), (err) => err.code === "connection_lost");
    assert.strictEqual(posts, 1);
  } finally {
    await app.close();
  }
});

test("an app that doesn't answer in time is reported in plain words, as a channel error", async () => {
  const app = await fakeApp((url) => url.pathname === "/v1/status"); // never answers
  app.ch.timeout = 200;
  try {
    await assert.rejects(app.ch.status(), (err) => err instanceof ChannelError && err.code === "timed_out"
      && /didn't answer in time/.test(err.message));
  } finally {
    app.close();
  }
});

test("any other connection failure is a channel error that names it, not a raw socket error", async () => {
  // Something on the port that answers with bytes that aren't HTTP at all.
  const net = require("node:net");
  const socks = new Set();
  const server = net.createServer((sock) => { socks.add(sock); sock.end("not http\r\n\r\n"); });
  await new Promise((r) => server.listen(0, "127.0.0.1", r));
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "kf-bad-"));
  fs.writeFileSync(path.join(dir, "editor-port"), `${server.address().port} hello`);
  try {
    await assert.rejects(new Channel(dir).status(), (err) => err instanceof ChannelError && err.code === "unreachable"
      && /couldn't reach KratosForge on this PC \(HPE_/.test(err.message) && /restart KratosForge/.test(err.message));
  } finally {
    for (const sock of socks) sock.destroy();
    await new Promise((r) => server.close(r));
  }
});

test("an answer cut off after it began is reported as lost, not left waiting", async () => {
  const app = await fakeApp((url, req, res) => {
    if (url.pathname === "/v1/status") {
      res.writeHead(200, { "Content-Type": "application/json", "Content-Length": "100" });
      res.write('{"ok": true, "res');
      setTimeout(() => req.socket.destroy(), 50);
      return true;
    }
    return false;
  });
  try {
    await assert.rejects(app.ch.status(), (err) => err instanceof ChannelError && err.code === "connection_lost");
  } finally {
    await app.close();
  }
});

test("an answer that stalls after it began times out, not left waiting", async () => {
  const app = await fakeApp((url, req, res) => {
    if (url.pathname === "/v1/status") {
      res.writeHead(200, { "Content-Type": "application/json", "Content-Length": "100" });
      res.write('{"ok": true, "res'); // and nothing more
      return true;
    }
    return false;
  });
  app.ch.timeout = 200;
  try {
    await assert.rejects(app.ch.status(), (err) => err instanceof ChannelError && err.code === "timed_out");
  } finally {
    app.close();
  }
});

test("a poll with no answer at all is asked again only once", async () => {
  let polls = 0;
  const app = await fakeApp((url, req, res, reply) => {
    if (url.pathname === "/v1/run" && req.method === "POST") {
      reply(200, { ok: true, result: { status: "running", version: 1, outputs: [] } });
      return true;
    }
    if (url.pathname === "/v1/run") { polls++; return true; } // never answers
    return false;
  });
  app.ch.timeout = 200;
  app.ch.pollPause = 10;
  try {
    await assert.rejects(app.ch.run("C:\\nb\\a.ipynb", "x = 1", () => undefined), (err) => err.code === "timed_out");
    assert.strictEqual(polls, 2);
  } finally {
    app.close();
  }
});

test("Stop pressed while a cell's start is on its way interrupts it once the app says it started", async () => {
  const seen = [];
  let stop = false;
  const app = await fakeApp((url, req, res, reply) => {
    if (url.pathname === "/v1/run" && req.method === "POST") {
      seen.push("start");
      stop = true; // the person presses Stop before this answer arrives
      reply(200, { ok: true, result: { status: "running", version: 1, outputs: [] } });
      return true;
    }
    if (url.pathname === "/v1/interrupt") {
      seen.push("interrupt");
      reply(200, { ok: true, result: { interrupted: true } });
      return true;
    }
    if (url.pathname === "/v1/run") {
      seen.push("poll");
      reply(200, { ok: true, result: { status: "error", version: 2, outputs: [{ type: "error", ename: "KeyboardInterrupt", evalue: "" }] } });
      return true;
    }
    return false;
  });
  try {
    const last = await app.ch.run("C:\\nb\\a.ipynb", "x = 1", () => undefined, () => stop);
    assert.strictEqual(last.status, "error");
    assert.deepStrictEqual(seen, ["start", "interrupt", "poll"]);
  } finally {
    await app.close();
  }
});

test("Stop pressed before a cell is sent (while pairing, say) means it isn't sent at all", async () => {
  const seen = [];
  const app = await fakeApp((url) => { if (url.pathname !== "/v1/hello") seen.push(url.pathname); return false; });
  try {
    const shown = [];
    const last = await app.ch.run("C:\\nb\\a.ipynb", "x = 1", (s) => shown.push(s), () => true);
    assert.strictEqual(last.status, "cancelled");
    assert.deepStrictEqual(seen, []);
    assert.deepStrictEqual(shown, []);
  } finally {
    await app.close();
  }
});

test("a cell nobody stopped is never interrupted by the run itself", async () => {
  const seen = [];
  const app = await fakeApp((url, req, res, reply) => {
    if (url.pathname === "/v1/hello") return false;
    seen.push(url.pathname);
    if (url.pathname === "/v1/run" && req.method === "POST") {
      reply(200, { ok: true, result: { status: "running", version: 1, outputs: [] } });
      return true;
    }
    if (url.pathname === "/v1/run") {
      reply(200, { ok: true, result: { status: "ok", version: 2, outputs: [] } });
      return true;
    }
    return false;
  });
  try {
    await app.ch.run("C:\\nb\\a.ipynb", "x = 1", () => undefined, () => false);
    assert.ok(!seen.includes("/v1/interrupt"));
  } finally {
    await app.close();
  }
});
