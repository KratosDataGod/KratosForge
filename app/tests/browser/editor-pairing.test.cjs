// Run cells from VS Code, against the real engine: Settings > Integrations switches the editor
// channel on, a VS Code window (this test, over HTTP) asks to pair, the app shows the request with
// the code the window got, and the person's choice of how long to keep it reaches the engine.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const http = require("node:http");
const path = require("node:path");
const { playwright, openApp } = require("./harness.cjs");

const SHOTS = process.env.SCREENSHOT_DIR;
const skip = !playwright && "Playwright is not installed";
const ENGINE = ["editor_status", "editor_set_enabled", "editor_pending", "editor_decide_pairing", "editor_decide_write",
  "editor_end_pairing", "integrations_status", "integrations_activity", "integrations_pending", "app_settings"];

let app;
const page = () => app.page;

function window(port, hello) {
  return (method, url, body, headers = {}) => new Promise((resolve, reject) => {
    const data = body ? JSON.stringify(body) : null;
    const req = http.request({ host: "127.0.0.1", port, method, path: url,
      headers: { Host: `127.0.0.1:${port}`, "X-KratosForge-Hello": hello,
        ...(data ? { "Content-Type": "application/json", "Content-Length": Buffer.byteLength(data) } : {}), ...headers } },
    (res) => { let text = ""; res.on("data", (c) => { text += c; }); res.on("end", () => resolve({ status: res.statusCode, body: JSON.parse(text) })); });
    req.on("error", reject);
    if (data) req.write(data);
    req.end();
  });
}

before(async () => {
  if (skip) return;
  app = await openApp({ viewport: { width: 1280, height: 900 }, engine: ENGINE });
});

after(async () => { if (app) await app.close(); });

test("A VS Code window pairs only after the person says yes, for as long as they choose", { skip }, async () => {
  await page().evaluate(() => { state.settingsTab = "integrations"; go("settings"); });
  const toggle = page().getByRole("checkbox", { name: "Let VS Code run notebooks" });
  await toggle.check();
  await page().getByText("VS Code windows can ask to pair").waitFor();
  const [port, hello] = fs.readFileSync(path.join(app.engineHome, "FabricDesktop", "editor-port"), "utf8").split(" ");
  const call = window(Number(port), hello);
  const asked = await call("POST", "/v1/pair", { window: "C:\\Users\\chris\\source\\repos\\sales-notebooks", client: "KratosForge for VS Code 0.1.0" });
  assert.strictEqual(asked.status, 200);
  const dialog = page().getByRole("dialog", { name: "Let VS Code run notebooks on this PC?" });
  await dialog.waitFor({ timeout: 15000 });
  assert.strictEqual(await dialog.locator(".editor-pair-code").textContent(), asked.body.result.code);
  await dialog.getByText("C:\\Users\\chris\\source\\repos\\sales-notebooks").waitFor();
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "editor-pairing.png") });
  await dialog.getByRole("combobox", { name: "Keep this pairing for" }).selectOption("8h");
  await dialog.getByRole("button", { name: "Pair this window" }).click();
  await page().getByText(/Paired C:\\Users\\chris\\source\\repos\\sales-notebooks/).waitFor();
  const got = await call("GET", `/v1/pair/${asked.body.result.id}`);
  assert.strictEqual(got.body.result.state, "paired");
  assert.strictEqual(got.body.result.keep, "8h");
  const status = await call("GET", "/v1/status", null, { Authorization: `Bearer ${got.body.result.token}` });
  assert.strictEqual(status.status, 200);
  // Settings lists it; End stops it at once.
  await page().evaluate(() => { go("home"); state.settingsTab = "integrations"; go("settings"); });
  await page().getByRole("button", { name: /End pairing with/ }).click();
  await page().getByText(/isn't paired any more/).waitFor();
  const after = await call("GET", "/v1/status", null, { Authorization: `Bearer ${got.body.result.token}` });
  assert.strictEqual(after.status, 401);
  assert.deepStrictEqual(app.errors, []);
});

test("Deny leaves the window unpaired", { skip }, async () => {
  const [port, hello] = fs.readFileSync(path.join(app.engineHome, "FabricDesktop", "editor-port"), "utf8").split(" ");
  const call = window(Number(port), hello);
  const asked = await call("POST", "/v1/pair", { window: "C:\\other", client: "something" });
  const dialog = page().getByRole("dialog", { name: "Let VS Code run notebooks on this PC?" });
  await dialog.waitFor({ timeout: 15000 });
  await dialog.getByRole("button", { name: "Deny" }).click();
  await page().getByText("Denied. That VS Code window isn't paired.").waitFor();
  assert.strictEqual((await call("GET", `/v1/pair/${asked.body.result.id}`)).body.result.state, "denied");
});
