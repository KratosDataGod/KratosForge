// Remote windows (src/remote.ts, docs/vscode.md "Paths"): in a Remote-SSH, WSL, Dev Container,
// Codespaces or tunnel window the extension says once that it runs cells only in a local window, and nothing
// it does contacts KratosForge. The extension runs here against a stand-in for VS Code's API.
const { test } = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const http = require("node:http");
const Module = require("node:module");
const os = require("node:os");
const path = require("node:path");
const { remoteKind, remoteMessage } = require("../out/remote.js");

test("each kind of remote window is named as people call it; a local window is none", () => {
  assert.strictEqual(remoteKind(undefined), undefined);
  assert.strictEqual(remoteKind(""), undefined);
  assert.strictEqual(remoteKind("ssh-remote"), "Remote-SSH");
  assert.strictEqual(remoteKind("wsl"), "WSL");
  assert.strictEqual(remoteKind("dev-container"), "Dev Container");
  assert.strictEqual(remoteKind("attached-container"), "Dev Container");
  assert.strictEqual(remoteKind("codespaces"), "Codespaces");
  assert.strictEqual(remoteKind("tunnel"), "Remote Tunnel");
  assert.strictEqual(remoteKind("something-new"), "remote");
  assert.match(remoteMessage("WSL"), /only in a local VS Code window.*This is a WSL window, so it doesn't contact KratosForge/);
});

// The parts of VS Code's API the extension uses, recording what it shows.
function fakeVscode(remoteName, notebookUri, cancelled = false) {
  const shown = [];
  const commands = {};
  const runs = [];
  let controller;
  const notebook = { uri: notebookUri, isUntitled: false };
  const vscode = {
    env: { remoteName, openExternal: async (t) => { shown.push(["open", String(t)]); return true; } },
    StatusBarAlignment: { Left: 1 },
    ProgressLocation: { Notification: 15 },
    NotebookCellKind: { Markup: 1, Code: 2 },
    Uri: { parse: (s) => ({ toString: () => s }) },
    NotebookCellOutput: class { constructor(items) { this.items = items; } },
    NotebookCellOutputItem: class {
      static error(e) { return { error: e }; }
      static text(t, mime) { return { text: t, mime }; }
      constructor(bytes, mime) { this.bytes = bytes; this.mime = mime; }
    },
    window: {
      createStatusBarItem: () => ({ show() {}, dispose() {} }),
      showWarningMessage: async (m) => { shown.push(["warning", m]); },
      showInformationMessage: async (m) => { shown.push(["info", m]); },
      withProgress: async () => { shown.push(["progress"]); },
      get activeNotebookEditor() { return { notebook }; },
    },
    workspace: { getWorkspaceFolder: () => undefined },
    commands: { registerCommand: (id, fn) => { commands[id] = fn; return { dispose() {} }; } },
    notebooks: {
      createNotebookController: () => (controller = {
        dispose() {},
        createNotebookCellExecution: () => {
          const run = { outputs: [], start() {}, end(ok) { run.ok = ok; }, clearOutput() {}, replaceOutput(o) { run.outputs.push(o); },
            token: { onCancellationRequested(cb) { if (cancelled) cb(); return { dispose() { run.disposed = true; } }; } } };
          runs.push(run);
          return run;
        },
      }),
    },
  };
  return { vscode, shown, commands, runs, notebook, controller: () => controller };
}

function load(fake) {
  const load = Module._load;
  Module._load = function (request, ...rest) { return request === "vscode" ? fake.vscode : load.call(this, request, ...rest); };
  try {
    const file = require.resolve("../out/extension.js");
    delete require.cache[file];
    return require(file);
  } finally {
    Module._load = load;
  }
}

// Every way the extension could reach KratosForge: its port file and any HTTP request.
function watchConnections(t) {
  const tried = [];
  const request = http.request;
  const get = http.get;
  t.mock.method(http, "request", (...a) => { tried.push(a[0]); return request(...a); });
  t.mock.method(http, "get", (...a) => { tried.push(a[0]); return get(...a); });
  const read = fs.readFileSync;
  t.mock.method(fs, "readFileSync", (p, ...a) => { if (String(p).endsWith("editor-port")) tried.push(String(p)); return read(p, ...a); });
  return tried;
}

const context = () => ({ subscriptions: [], extension: { packageJSON: { version: "0.0.0-test" } },
  secrets: { get: async () => "kfp_kept", store: async () => {}, delete: async () => {} } });

for (const remoteName of ["ssh-remote", "wsl", "dev-container", "codespaces", "tunnel"]) {
  test(`in a ${remoteName} window it says once that it runs only in a local window, and contacts nothing`, async (t) => {
    const tried = watchConnections(t);
    const fake = fakeVscode(remoteName, { scheme: "vscode-remote", fsPath: "/home/ana/nb/a.ipynb", toString: () => "vscode-remote://x/a.ipynb" });
    const ext = load(fake);
    ext.activate(context());
    const cells = [{ kind: 1 }, { kind: 2, document: { getText: () => "print(1)" } }, { kind: 2, document: { getText: () => "x" } }];
    await fake.controller().executeHandler(cells, fake.notebook, fake.controller());
    await fake.controller().executeHandler(cells, fake.notebook, fake.controller());
    await fake.controller().interruptHandler(fake.notebook);
    for (const id of ["kratosforge.pair", "kratosforge.unpair", "kratosforge.openInApp"]) await fake.commands[id]();
    const kind = remoteKind(remoteName);
    assert.deepStrictEqual(fake.shown, [["warning", remoteMessage(kind)]], "said more than once, or something else");
    assert.strictEqual(fake.runs.length, 2, "one cell per run says why");
    for (const run of fake.runs) {
      assert.strictEqual(run.ok, false);
      assert.strictEqual(run.outputs[0].items[0].error.message, remoteMessage(kind));
    }
    assert.deepStrictEqual(tried, [], "a remote window reached for KratosForge");
  });
}

test("in a local window the same calls do reach for KratosForge (so the check above means something)", async (t) => {
  const tried = watchConnections(t);
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "kf-local-"));
  const saved = { LOCALAPPDATA: process.env.LOCALAPPDATA, HOME: process.env.HOME };
  Object.assign(process.env, { LOCALAPPDATA: home, HOME: home });
  t.after(() => { for (const [k, v] of Object.entries(saved)) { if (v === undefined) delete process.env[k]; else process.env[k] = v; } });
  const fake = fakeVscode(undefined, { scheme: "file", fsPath: path.join(home, "nb", "a.ipynb"), toString: () => "file:///a.ipynb" });
  const ext = load(fake);
  ext.activate(context());
  await fake.controller().executeHandler([{ kind: 2, document: { getText: () => "x" } }], fake.notebook, fake.controller());
  assert.ok(tried.length > 0, "the local window never looked for KratosForge");
  assert.ok(!fake.shown.some(([, m]) => /local VS Code window/.test(m || "")));
});

test("a cell stopped before anything was sent says so, and nothing reaches for KratosForge", async (t) => {
  const tried = watchConnections(t);
  const fake = fakeVscode(undefined, { scheme: "file", fsPath: path.join(os.tmpdir(), "nb", "a.ipynb"), toString: () => "file:///a.ipynb" }, true);
  const ext = load(fake);
  ext.activate(context());
  const cells = [{ kind: 2, document: { getText: () => "x" } }, { kind: 2, document: { getText: () => "y" } }];
  await fake.controller().executeHandler(cells, fake.notebook, fake.controller());
  assert.strictEqual(fake.runs.length, 1, "the cells after a stopped one don't run");
  assert.strictEqual(fake.runs[0].ok, false);
  assert.deepStrictEqual(fake.runs[0].outputs[0].items[0], { text: ext.STOPPED_BEFORE, mime: "text/plain" });
  assert.ok(fake.runs[0].disposed, "the Stop listener is let go when the cell ends");
  assert.deepStrictEqual(tried, []);
});
