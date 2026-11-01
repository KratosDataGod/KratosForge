// #1437: the Settings actions in app/ui/settings.js that change something on this PC (see harness.cjs
// for how to run it). Sign out asks first, Cancel sends nothing, and confirming sends sign_out once.
// Delete left-overs (Advanced > OneLake writes > Files on this PC) shows what local_writes found, asks
// in a danger confirm that names the size and what it deletes, Cancel sends nothing, and confirming
// sends local_writes_clear once. New install ID sends telemetry_new_install_id, says so and shows the
// new ID, and usage data stays off as it is by default. Changing a setting sends exactly that change
// to save_app_settings. A refusal from the engine shows a plain message, never the raw Python text or
// [object Object]. Opening Settings sends no write, axe finds nothing serious on the page or the
// confirm, and the page throws no error.
// Settings and usage data come from the real engine (app_settings.py, telemetry.py via service.py);
// sign-in and the local write folders are mocked in the shapes service.sign_out and
// local_writes_service.py return.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const { playwright, openApp, startupReads, axeSource, axeFindings } = require("./harness.cjs");

let app;
const skip = !playwright && "Playwright is not installed";
const page = () => app.page;
const ME = "chris@contoso.com";
const ENGINE = ["app_settings", "save_app_settings", "telemetry_status", "telemetry_new_install_id"];
const READS = ["app_settings", "telemetry_status", "local_writes", "platform_notify_settings", "optimize_status", "readiness_check", "list_workspaces"];
const WRITES = ["sign_out", "local_writes_clear", "telemetry_new_install_id", "save_app_settings", "local_writes_reveal"];
const OLD_ID = "0f8fad5b-d9cb-469f-a165-70867728950e";
const STAGING = "C:\\Users\\chris\\AppData\\Local\\FabricDesktop\\staging";
const TPCH = "C:\\Users\\chris\\AppData\\Local\\Temp\\tpch_sf1_k2j4";
const MB = 1024 * 1024;

const fail = {};  // method -> the engine's error text, for the next calls to it
const failing = (method, fn) => (p) => { if (fail[method]) throw new Error(fail[method]); return fn(p); };
const mocks = {
  readiness_check: () => [],
  // service.sign_out
  sign_out: failing("sign_out", () => ({ user: null })),
  // local_writes_service.py: LocalWritesMixin.local_writes and local_writes_clear
  local_writes: () => ({ folder: STAGING, files: 3, bytes: 12 * MB, newest: "2026-10-31T09:12:00Z",
    uploading: { files: 1, bytes: 4 * MB, newest: "2026-10-31T09:12:00Z", folder: `${STAGING}\\_writes` },
    tpch: [{ folder: TPCH, files: 8, bytes: 20 * MB, newest: "2026-10-30T17:40:00Z" }] }),
  local_writes_clear: failing("local_writes_clear", () => ({ removed: 4, bytes: 32 * MB, in_use: [] })),
};

let baseline;  // the methods the app calls on its own, before Settings opens

before(async () => {
  if (skip) return;
  app = await openApp({ engine: ENGINE, mocks, storage: { "fd.fold.settings.advanced": "0" } });
  // An install ID as telemetry.py keeps it (usage.json in the engine's telemetry folder), so Settings
  // offers New install ID; usage data itself is left at its default.
  const folder = path.join(app.engineHome, "FabricDesktop", "telemetry");
  fs.mkdirSync(folder, { recursive: true });
  fs.writeFileSync(path.join(folder, "usage.json"), JSON.stringify({ install_id: OLD_ID }));
  // The app's startup reads, and its first approval polls 5 s in.
  await startupReads(app);
  for (const end = Date.now() + 15000; !["integrations_pending", "editor_pending"].every((m) => app.calls.some((c) => c.method === m));
    await new Promise((r) => setTimeout(r, 50))) {
    if (Date.now() > end) throw new Error("the app's startup polls never came");
  }
  baseline = new Set(app.calls.map((c) => c.method));
});
after(async () => { if (app) await app.close(); });

// Each test starts signed in, with no dialog or message showing and nothing failing.
beforeEach(async () => {
  if (skip) return;
  for (const k of Object.keys(fail)) delete fail[k];
  await page().evaluate((me) => {
    closeMenu(); closeDialog(); const t = document.getElementById("toast"); t.hidden = true; t.textContent = "";
    Object.assign(state, { user: me, tenant: "72f988bf-86f1-41af-91ab-2d7cd011db47" }); go("home");
  }, ME);
});

const writes = (from) => app.calls.slice(from).filter((c) => WRITES.includes(c.method)).map((c) => ({ method: c.method, params: c.params }));
const pause = (ms) => new Promise((r) => setTimeout(r, ms));
const account = () => page().locator(".page.settings section", { has: page().locator("h2", { hasText: "Account" }) });

// Settings, drawn in full: the account, the usage data and what the local write folders hold.
async function openSettings() {
  await page().evaluate(() => go("settings"));
  await page().waitForFunction(() => {
    const p = document.querySelector(".page.settings");
    return p && /Install ID/.test(p.textContent) && /Files on this PC/.test(p.textContent) && !/Looking\.\.\./.test(p.textContent);
  });
}

async function toastText() {
  const handle = await page().waitForFunction(() => { const t = document.getElementById("toast"); return !t.hidden && t.textContent; });
  return handle.jsonValue();
}

// A plain message: no Python exception name, no traceback words, no object printed as text.
function assertPlain(text) {
  assert.doesNotMatch(text, /\[object Object\]|\b[A-Z][A-Za-z]*Error:|Exception|Traceback|undefined/, text);
}

test("opening Settings sends no write, and usage data is off by default", { skip }, async () => {
  const from = app.calls.length;
  await openSettings();
  await pause(500);
  assert.deepStrictEqual(writes(from), []);
  const made = [...new Set(app.calls.slice(from).map((c) => c.method))];
  assert.deepStrictEqual(made.filter((m) => !READS.includes(m) && !baseline.has(m)), [], made.join(", "));
  const usage = page().locator(".page.settings section", { has: page().locator("h2", { hasText: "Usage data" }) });
  assert.ok(!(await usage.getByRole("checkbox").isChecked()), "usage data is off");
  assert.match(await usage.innerText(), /Nothing \(off\)/);
  assert.match(await usage.innerText(), new RegExp(OLD_ID));
  const saved = await page().evaluate(() => rpc("app_settings"));
  assert.strictEqual(saved.telemetry.value, false);
  assert.strictEqual(saved.telemetry.source, "default");
  assert.match(await page().locator(".setting", { hasText: "Files on this PC" }).innerText(),
    /12\.0 MB in 3 files, 1 waiting to upload or left over \(4\.0 MB\)/);
  assert.deepStrictEqual(app.errors, []);
});

test("Sign out asks first; Cancel sends nothing, and confirming sends sign_out once", { skip }, async () => {
  await openSettings();
  const from = app.calls.length;
  const shellFrom = await page().evaluate(() => window.__fdShellCalls.length);
  assert.match(await account().innerText(), new RegExp(`Signed in as ${ME}`));
  await account().getByRole("button", { name: "Sign out" }).click();
  const dialog = page().getByRole("dialog", { name: "Sign out?" });
  await dialog.waitFor();
  assert.match(await dialog.innerText(), /forgets your sign-in on this PC, in the app and in the Fabric views/);
  assert.match(await dialog.innerText(), /Notebooks and settings stay/);
  await dialog.getByRole("button", { name: "Cancel" }).click();
  await dialog.waitFor({ state: "detached" });
  await pause(300);
  assert.deepStrictEqual(writes(from), []);
  assert.strictEqual(await page().evaluate(() => state.user), ME);

  await account().getByRole("button", { name: "Sign out" }).click();
  await dialog.waitFor();
  await dialog.getByRole("button", { name: "Sign out", exact: true }).click();
  await dialog.waitFor({ state: "detached" });
  assert.strictEqual(await toastText(), "Signed out");
  await page().waitForFunction(() => state.view === "home");
  assert.deepStrictEqual(writes(from), [{ method: "sign_out", params: {} }]);
  assert.strictEqual(await page().evaluate(() => state.user), null);
  const shell = await page().evaluate((n) => window.__fdShellCalls.slice(n).map((c) => c.cmd), shellFrom);
  assert.deepStrictEqual(shell.filter((c) => c === "fabric_view_sign_out"), ["fabric_view_sign_out"]);
  assert.deepStrictEqual(app.errors, []);
});

test("a refused sign-out says so plainly and keeps you signed in", { skip }, async () => {
  fail.sign_out = "PermissionError: [Errno 13] Permission denied: 'C:\\\\Users\\\\chris\\\\AppData\\\\Local\\\\FabricDesktop\\\\msal_cache.bin'";
  await openSettings();
  const from = app.calls.length;
  const shellFrom = await page().evaluate(() => window.__fdShellCalls.length);
  await account().getByRole("button", { name: "Sign out" }).click();
  const dialog = page().getByRole("dialog", { name: "Sign out?" });
  await dialog.getByRole("button", { name: "Sign out", exact: true }).click();
  await dialog.waitFor({ state: "detached" });
  const text = await toastText();
  assert.match(text, /^Sign-out failed: This PC didn't allow access to/, text);
  assertPlain(text);
  await pause(300);
  assert.strictEqual(await toastText(), text, "no Signed out over the refusal");
  assert.deepStrictEqual(writes(from).map((w) => w.method), ["sign_out"]);
  assert.strictEqual(await page().evaluate(() => [state.view, state.user].join(" ")), `settings ${ME}`);
  const shell = await page().evaluate((n) => window.__fdShellCalls.slice(n).map((c) => c.cmd), shellFrom);
  assert.ok(!shell.includes("fabric_view_sign_out"), shell.join(", "));
  assert.deepStrictEqual(app.errors, []);
});

test("Delete left-overs names the size and what it deletes in a danger confirm; Cancel sends nothing, confirming sends local_writes_clear once", { skip }, async () => {
  await openSettings();
  const from = app.calls.length;
  const row = page().locator(".setting", { hasText: "Files on this PC" });
  assert.match(await row.innerText(), new RegExp(`TPC-H data left in the temp folder: 20\\.0 MB, ${TPCH.replace(/\\/g, "\\\\")}`));
  const del = row.getByRole("button", { name: "Delete left-overs" });
  assert.ok(await del.isEnabled());
  await del.click();
  const dialog = page().getByRole("dialog", { name: "Delete left-overs?" });
  await dialog.waitFor();
  const said = await dialog.innerText();
  assert.ok(said.includes(`This deletes 32.0 MB from this PC: 3 files in ${STAGING}; TPC-H data in ${TPCH}.`), said);
  assert.match(said, /Your tables in OneLake aren't touched/);
  const ok = dialog.getByRole("button", { name: "Delete", exact: true });
  assert.match(await ok.getAttribute("class"), /danger-solid/);
  assert.strictEqual(await page().evaluate(() => document.activeElement?.textContent), "Cancel", "a danger confirm starts on Cancel");
  await dialog.getByRole("button", { name: "Cancel" }).click();
  await dialog.waitFor({ state: "detached" });
  await pause(300);
  assert.deepStrictEqual(writes(from), []);

  await del.click();
  await dialog.waitFor();
  await ok.click();
  await dialog.waitFor({ state: "detached" });
  assert.strictEqual(await toastText(), "Deleted 32.0 MB.");
  assert.deepStrictEqual(writes(from), [{ method: "local_writes_clear", params: {} }]);
  assert.deepStrictEqual(app.errors, []);
});

test("a refused Delete left-overs says why plainly", { skip }, async () => {
  fail.local_writes_clear = "RuntimeError: A notebook is running, and its writes may still be uploading from this folder. Delete left-overs once it has finished.";
  await openSettings();
  const from = app.calls.length;
  await page().locator(".setting", { hasText: "Files on this PC" }).getByRole("button", { name: "Delete left-overs" }).click();
  const dialog = page().getByRole("dialog", { name: "Delete left-overs?" });
  await dialog.getByRole("button", { name: "Delete", exact: true }).click();
  await dialog.waitFor({ state: "detached" });
  const text = await toastText();
  assert.match(text, /^Not deleted: A notebook is running, and its writes may still be uploading/, text);
  assertPlain(text);
  assert.deepStrictEqual(writes(from).map((w) => w.method), ["local_writes_clear"]);
  assert.deepStrictEqual(app.errors, []);
});

test("New install ID sends telemetry_new_install_id, says so and shows the new ID; usage data stays off", { skip }, async () => {
  await openSettings();
  const from = app.calls.length;
  const usage = () => page().locator(".page.settings section", { has: page().locator("h2", { hasText: "Usage data" }) });
  const before = (await usage().locator("dd.mono").innerText()).split(" ")[0];
  await usage().getByRole("button", { name: "New install ID" }).click();
  assert.strictEqual(await toastText(), "New install ID");
  const after = await page().waitForFunction((old) => {
    const dd = [...document.querySelectorAll(".page.settings dd.mono")].find((d) => /random, not tied to you/.test(d.textContent));
    const id = dd?.textContent.split(" ")[0];
    return id && id !== old && id;
  }, before);
  assert.match(await after.jsonValue(), /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/);
  assert.deepStrictEqual(writes(from), [{ method: "telemetry_new_install_id", params: {} }]);
  assert.ok(!(await usage().getByRole("checkbox").isChecked()), "usage data is still off");
  const status = await page().evaluate(() => rpc("telemetry_status"));
  assert.strictEqual(status.on, false);
  assert.strictEqual(status.install_id, await after.jsonValue());
  assert.deepStrictEqual(app.errors, []);
});

test("changing a setting sends exactly that change to save_app_settings", { skip }, async () => {
  await openSettings();
  let from = app.calls.length;
  const compression = page().getByRole("combobox", { name: "Compression" });
  assert.strictEqual(await compression.inputValue(), "zstd");
  await compression.selectOption("snappy");
  await page().waitForFunction(() => document.querySelector('.page.settings select[aria-label="Compression"]')?.value === "snappy"
    && state.settings.onelake_compression.value === "snappy");
  assert.deepStrictEqual(writes(from), [{ method: "save_app_settings", params: { changes: { onelake_compression: "snappy" } } }]);

  from = app.calls.length;
  await page().locator(".page.settings label.choice-row", { hasText: "Preview" }).locator("input[name=channel]").check();
  assert.strictEqual(await toastText(), "Preview: takes effect the next time KratosForge starts");
  assert.deepStrictEqual(writes(from), [{ method: "save_app_settings", params: { changes: { channel: "preview" } } }]);
  const saved = await page().evaluate(() => rpc("app_settings"));
  assert.deepStrictEqual([saved.onelake_compression.value, saved.channel.value, saved.telemetry.value], ["snappy", "preview", false]);

  // Back as they were, through the page.
  await page().getByRole("combobox", { name: "Compression" }).selectOption("zstd");
  await page().waitForFunction(() => state.settings.onelake_compression.value === "zstd");
  await page().locator(".page.settings label.choice-row", { hasText: "Stable" }).locator("input[name=channel]").check();
  await page().waitForFunction(() => state.settings.channel.value === "stable");
  assert.deepStrictEqual(app.errors, []);
});

test("a setting the engine refuses says why plainly and isn't saved", { skip }, async () => {
  await openSettings();
  const from = app.calls.length;
  const group = page().locator(".advanced-group", { has: page().locator("h3", { hasText: "Sign-in" }) });
  await group.getByRole("textbox", { name: "App (client) ID" }).fill("not-an-app-id");
  await group.getByRole("button", { name: "Save" }).click();
  const text = await toastText();
  assert.match(text, /^Not saved: That isn't an app \(client\) ID/, text);
  assertPlain(text);
  assert.deepStrictEqual(writes(from), [{ method: "save_app_settings", params: { changes: { client_id: "not-an-app-id" } } }]);
  assert.strictEqual((await page().evaluate(() => rpc("app_settings"))).client_id.source, "default");
  assert.deepStrictEqual(app.errors, []);
});

test("axe finds nothing serious on Settings and its Delete left-overs confirm", { skip: skip || (!axeSource && "axe-core is not installed") }, async () => {
  await openSettings();
  assert.deepStrictEqual(await axeFindings(page(), ".page.settings"), []);
  await page().locator(".setting", { hasText: "Files on this PC" }).getByRole("button", { name: "Delete left-overs" }).click();
  await page().getByRole("dialog", { name: "Delete left-overs?" }).waitFor();
  assert.deepStrictEqual(await axeFindings(page(), "#dialog-root .dialog"), []);
  const from = app.calls.length;
  await page().getByRole("dialog", { name: "Delete left-overs?" }).getByRole("button", { name: "Cancel" }).click();
  assert.deepStrictEqual(writes(from), []);
  assert.deepStrictEqual(app.errors, []);
});
