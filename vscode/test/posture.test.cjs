// What an IT team checks before allowing the extension (docs/vscode.md, "For IT"): it stays off in
// Restricted Mode, keeps tokens only in VS Code's SecretStorage, sends no telemetry and talks to
// nothing but KratosForge on 127.0.0.1. Read from the manifest and the sources, so a change that
// breaks one of these fails here.
const { test } = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");

const root = path.join(__dirname, "..");
const manifest = JSON.parse(fs.readFileSync(path.join(root, "package.json"), "utf8"));
const sources = fs.readdirSync(path.join(root, "src")).filter((f) => f.endsWith(".ts"))
  .map((f) => ({ f, text: fs.readFileSync(path.join(root, "src", f), "utf8") }));
const code = (text) => text.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/.*$/gm, ""); // comments aside

test("it stays off until the workspace is trusted, and runs only on this PC", () => {
  assert.deepStrictEqual(manifest.capabilities.untrustedWorkspaces, { supported: false });
  assert.strictEqual(manifest.capabilities.virtualWorkspaces, false);
  assert.deepStrictEqual(manifest.extensionKind, ["ui"]); // never on a remote, WSL or container host
  assert.deepStrictEqual(manifest.activationEvents, ["onNotebook:jupyter-notebook"]);
});

test("tokens live only in SecretStorage", () => {
  for (const { f, text } of sources) {
    for (const store of ["globalState", "workspaceState", "getConfiguration", "storageUri", "globalStorageUri", "writeFile"]) {
      assert.ok(!code(text).includes(store), `${f} uses ${store}`);
    }
  }
  const ext = code(sources.find((s) => s.f === "extension.ts").text);
  assert.ok(ext.includes("context.secrets.store("), "the kept token goes to SecretStorage");
});

test("no telemetry, and nothing reached but KratosForge on 127.0.0.1", () => {
  assert.strictEqual(manifest.dependencies, undefined, "no runtime dependencies are bundled");
  for (const { f, text } of sources) {
    const c = code(text);
    for (const net of ["node:https", "\"https\"", "node:net", "node:dgram", "node:tls", "fetch(", "XMLHttpRequest", "WebSocket",
      "createTelemetryLogger", "TelemetryReporter", "openExternal(vscode.Uri.parse(\"http"]) {
      assert.ok(!c.includes(net), `${f} uses ${net}`);
    }
  }
  const channel = code(sources.find((s) => s.f === "channel.ts").text);
  assert.deepStrictEqual(channel.match(/host: [^,}]+/g), ["host: \"127.0.0.1\""]);
});
