// Settings > Integrations writes, in a real browser (#1465): adding a tool sends the name and permissions
// picked and shows its token once, leaving it nowhere in the page or its storage after the dialog closes;
// Edit sends that tool's id and the permissions changed; a new token asks first, in a danger confirm,
// because the old one stops working; Revoke asks in a danger confirm and Cancel sends nothing; the local
// HTTP API asks before it goes on (it opens a port other programs on this PC can call) and sends exactly
// the new state, as the extension switch does; Test sends integrations_test and shows its answer as a
// sentence; the engine's refusals read as a sentence too. Opening the tab sends no write, and axe finds
// nothing serious. integrations-tab.test.cjs covers approvals and Recent activity.
//
// The real engine answers every call (sidecar/fabric_desktop/integrations/app_service.py) but
// integrations_set_http: the real one starts the API as a child process (_http_start) that listens on a
// port and needs the engine installed as a package, so here it is answered with what the real one returns,
// integrations_status(), after writing the same switch to the engine's store (store.set_setting), so the
// tab the engine draws next shows it.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { execFileSync } = require("node:child_process");
const { playwright, openApp, axeSource, axeFindings } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
const READS = ["integrations_status", "integrations_activity", "integrations_pending", "editor_status", "editor_pending", "app_settings"];
const WRITES = ["integrations_add_client", "integrations_update_client", "integrations_new_token", "integrations_revoke_client",
  "integrations_set_http", "integrations_set_extension", "integrations_test", "integrations_set_enabled", "integrations_decide"];
const ENGINE = [...READS, ...WRITES.filter((m) => m !== "integrations_set_http")];

// Three tools: one over MCP, one over the local API (its seeded token is thrown away), and one to revoke.
const SEED = String.raw`
import json
from fabric_desktop.service import Service
store = Service().integration_store
store.set_setting("enabled", True)
codex, _ = store.add_client("Codex", "codex", "stdio", ["READ_METADATA", "READ_DATA"])
scripts, _ = store.add_client("Scripts", "http", "http", ["READ_METADATA"])
old, _ = store.add_client("Old tool", "cursor", "stdio", ["READ_METADATA"])
print(json.dumps({"codex": codex["id"], "scripts": scripts["id"], "old": old["id"]}))
`;

let app;
let ids;
const page = () => app.page;

function py(code) {
  return execFileSync(process.env.FABRIC_DESKTOP_PYTHON || (process.platform === "win32" ? "python" : "python3"), ["-c", code], {
    cwd: path.join(__dirname, "..", "..", "..", "sidecar"),
    env: { ...process.env, LOCALAPPDATA: app.engineHome, HOME: app.engineHome, USERPROFILE: app.engineHome },
  }).toString();
}

// The engine's integrations_set_http, minus starting the API (see the top of this file).
function setHttp({ enabled }) {
  const status = JSON.parse(py(`import json
from fabric_desktop.service import Service
s = Service()
s.integration_store.set_setting("http_enabled", ${enabled ? "True" : "False"})
print(json.dumps(s.integrations_status()))`));
  return { ...status, http: { ...status.http, running: enabled, listening: enabled } };  // as when it started
}

const sent = (method) => app.calls.filter((c) => c.method === method);

async function openTab() {
  // A dialog a failed test left open would cover the tab for the tests after it.
  await page().evaluate(() => { closeDialog(); go("home"); state.settingsTab = "integrations"; go("settings"); });
  await page().waitForFunction(() => {
    const box = document.querySelector(".page.settings .integrations");
    return box && !box.querySelector(".loading") && [...box.querySelectorAll("h2")].some((h) => h.textContent === "Connected tools");
  }, null, { timeout: 15000 });
}

const card = (heading) => page().locator(".page.settings section.card").filter({ has: page().getByRole("heading", { name: heading, exact: true }) });
const toolRow = (name) => card("Connected tools").locator("tbody tr").filter({ has: page().locator("td:first-child b").getByText(name, { exact: true }) });
const dialog = (name) => page().getByRole("dialog", { name });

// The toast once it says `re`; its text.
async function toastSays(re) {
  await page().waitForFunction((src) => {
    const t = document.getElementById("toast");
    return t && !t.hidden && new RegExp(src).test(t.textContent);
  }, re.source, { timeout: 15000 });
  return page().locator("#toast").textContent();
}

// Whether `secret` is anywhere in the page: its markup, what's typed in a box, or its storage.
// Only true or false comes back, so a failing test never prints the token.
function leftInPage(secret) {
  return page().evaluate((t) => {
    const stores = [localStorage, sessionStorage].flatMap((s) => Object.keys(s).map((k) => `${k}=${s.getItem(k)}`));
    return document.documentElement.outerHTML.includes(t)
      || [...document.querySelectorAll("input, textarea")].some((i) => i.value.includes(t))
      || stores.some((v) => v.includes(t));
  }, secret);
}

// The setup dialog's token, read once and never printed; then Done.
async function takeToken(name) {
  const setup = dialog(`Connect ${name}`);
  await setup.waitFor();
  const token = await setup.locator(".warn-box .mono").textContent();
  assert.ok(/^fdt_[\w-]{20,}$/.test(token), "the setup dialog shows no token");
  await setup.getByRole("button", { name: "Done" }).click();
  await setup.waitFor({ state: "detached" });
  return token;
}

before(async () => {
  if (skip) return;
  app = await openApp({ viewport: { width: 1280, height: 1000 }, engine: ENGINE, mocks: { integrations_set_http: setHttp } });
  ids = JSON.parse(py(SEED));
});

after(async () => { if (app) await app.close(); });

test("Opening the tab sends no write, and axe finds nothing serious on it", { skip }, async () => {
  // The approval polls start 5 s after launch: wait for both, so the reads they make are counted too.
  for (const end = Date.now() + 15000; !(sent("integrations_pending").length && sent("editor_pending").length);
    await new Promise((r) => setTimeout(r, 50))) {
    if (Date.now() > end) throw new Error("the approval polls never came");
  }
  await openTab();
  await toolRow("Old tool").waitFor();
  const methods = [...new Set(app.calls.map((c) => c.method))];
  assert.deepStrictEqual(methods.filter((m) => WRITES.includes(m)), []);
  for (const want of ["integrations_status", "integrations_activity", "integrations_pending"]) assert.ok(methods.includes(want), `no ${want}`);
  if (axeSource) assert.deepStrictEqual(await axeFindings(page(), ".page.settings"), []);
  assert.deepStrictEqual(app.errors, []);
});

test("Add a tool sends the name and permissions picked, and its token is shown once and left nowhere", { skip }, async () => {
  await openTab();
  await card("Connected tools").getByRole("button", { name: "Add a tool" }).click();
  const add = dialog("Add a tool");
  await add.getByRole("button", { name: /An HTTP client/ }).click();
  await add.getByLabel("Name (so you can tell your tools apart)").fill("Build scripts");
  await add.getByRole("checkbox", { name: /^Read data/ }).check();
  if (axeSource) assert.deepStrictEqual(await axeFindings(page(), "#dialog-root"), []);
  await add.getByRole("button", { name: "Next" }).click();
  const token = await takeToken("Build scripts");
  const added = sent("integrations_add_client");
  assert.strictEqual(added.length, 1);
  assert.deepStrictEqual(added[0].params, { name: "Build scripts", kind: "http", grants: ["READ_METADATA", "READ_DATA"], auto: [], transport: "http" });
  // The engine has it: the tab shows it with the permissions picked.
  await toolRow("Build scripts").getByText("Read data").waitFor();
  assert.strictEqual(await leftInPage(token), false, "the token is still in the page after its dialog closed");
  assert.deepStrictEqual(app.errors, []);
});

test("Edit sends that tool's id and the permissions changed", { skip }, async () => {
  await openTab();
  await toolRow("Codex").getByRole("button", { name: "Edit" }).click();
  const edit = dialog("Edit Codex");
  await edit.getByRole("checkbox", { name: /^Write notebooks/ }).check();
  await edit.getByRole("button", { name: "Save" }).click();
  await toastSays(/^Saved$/);
  const updated = sent("integrations_update_client");
  assert.strictEqual(updated.length, 1);
  // The dialog sends the whole form: the name, workspaces and "don't ask" set as they were.
  assert.deepStrictEqual(updated[0].params, { client_id: ids.codex,
    changes: { name: "Codex", grants: ["READ_METADATA", "READ_DATA", "WRITE_LOCAL_FILES"], auto: [], workspaces: [] } });
  await toolRow("Codex").getByText("Write notebooks").waitFor();
  assert.deepStrictEqual(app.errors, []);
});

test("A new token asks first, in a danger confirm; Cancel sends nothing; the new token is left nowhere", { skip }, async () => {
  await openTab();
  const ask = dialog("Make a new token for Scripts?");
  await toolRow("Scripts").getByRole("button", { name: "Edit" }).click();
  await dialog("Edit Scripts").getByRole("button", { name: "New token" }).click();
  await ask.getByText(/current token stops working/).waitFor();
  assert.match(await ask.getByRole("button", { name: "Make a new token" }).getAttribute("class"), /danger/);
  await ask.getByRole("button", { name: "Cancel" }).click();
  await ask.waitFor({ state: "detached" });
  assert.strictEqual(sent("integrations_new_token").length, 0);

  await toolRow("Scripts").getByRole("button", { name: "Edit" }).click();
  await dialog("Edit Scripts").getByRole("button", { name: "New token" }).click();
  await ask.getByRole("button", { name: "Make a new token" }).click();
  const token = await takeToken("Scripts");
  assert.deepStrictEqual(sent("integrations_new_token").map((c) => c.params), [{ client_id: ids.scripts }]);
  assert.strictEqual(await leftInPage(token), false, "the new token is still in the page after its dialog closed");
  assert.deepStrictEqual(app.errors, []);
});

test("Revoke asks in a danger confirm; Cancel sends nothing; Revoke sends that tool's id", { skip }, async () => {
  await openTab();
  const ask = dialog("Revoke Old tool?");
  await toolRow("Old tool").getByRole("button", { name: "Revoke" }).click();
  assert.match(await ask.getByRole("button", { name: "Revoke" }).getAttribute("class"), /danger/);
  await ask.getByRole("button", { name: "Cancel" }).click();
  await ask.waitFor({ state: "detached" });
  assert.strictEqual(sent("integrations_revoke_client").length, 0);

  await toolRow("Old tool").getByRole("button", { name: "Revoke" }).click();
  await ask.getByRole("button", { name: "Revoke" }).click();
  await toastSays(/^Old tool revoked$/);
  assert.deepStrictEqual(sent("integrations_revoke_client").map((c) => c.params), [{ client_id: ids.old }]);
  await toolRow("Old tool").waitFor({ state: "detached" });
  assert.deepStrictEqual(app.errors, []);
});

test("The local HTTP API asks before it goes on, and each switch sends exactly the new state", { skip }, async () => {
  await openTab();
  const httpSwitch = () => card("How tools connect").locator(".setting").filter({ hasText: "Local HTTP API" }).getByRole("checkbox");
  const ask = dialog("Turn on the local HTTP API?");
  await httpSwitch().click();
  await ask.getByText(/Programs on this PC can then call KratosForge/).waitFor();
  await ask.getByRole("button", { name: "Cancel" }).click();
  await ask.waitFor({ state: "detached" });
  assert.strictEqual(sent("integrations_set_http").length, 0);
  assert.strictEqual(await httpSwitch().isChecked(), false, "the switch stays off after Cancel");

  await httpSwitch().click();
  await ask.getByRole("button", { name: "Turn on" }).click();
  await toastSays(/^The local API is on at http:\/\/127\.0\.0\.1:\d+$/);
  await openTab();
  assert.strictEqual(await httpSwitch().isChecked(), true);
  await httpSwitch().click();  // off asks nothing: it closes the port
  await toastSays(/^The local API is off$/);
  assert.deepStrictEqual(sent("integrations_set_http").map((c) => c.params), [{ enabled: true }, { enabled: false }]);

  // An extension adds capabilities to tools already connected, under the permissions each has: no question.
  const ext = () => card("Extensions").locator(".setting").filter({ hasText: "Notebook outline" }).getByRole("checkbox");
  await ext().click();
  await toastSays(/^Notebook outline is on\./);
  await openTab();
  assert.strictEqual(await ext().isChecked(), true);
  await ext().click();
  await toastSays(/^Notebook outline is off$/);
  assert.deepStrictEqual(sent("integrations_set_extension").map((c) => c.params),
    [{ extension_id: "notebook-outline", enabled: true }, { extension_id: "notebook-outline", enabled: false }]);
  assert.deepStrictEqual(app.errors, []);
});

test("Test sends integrations_test for that tool and shows the engine's answer as a sentence", { skip }, async () => {
  await openTab();
  await toolRow("Scripts").getByRole("button", { name: "Test" }).click();
  // The real engine's answer for an HTTP tool while the API isn't running.
  const said = await toastSays(/^The local HTTP API is off\. Turn it on first\.$/);
  assert.ok(!/\[object Object\]|Error:/.test(said), said);
  assert.deepStrictEqual(sent("integrations_test").map((c) => c.params), [{ client_id: ids.scripts }]);
  assert.deepStrictEqual(app.errors, []);
});

test("The engine's refusals read as a sentence, not raw text or [object Object]", { skip }, async () => {
  await openTab();
  // Add with no name: the engine's ValueError.
  await card("Connected tools").getByRole("button", { name: "Add a tool" }).click();
  const add = dialog("Add a tool");
  await add.getByLabel("Name (so you can tell your tools apart)").fill("  ");
  await add.getByRole("button", { name: "Next" }).click();
  let said = await toastSays(/^Not added: /);
  assert.match(said, /^Not added: Give the tool a name, for example Claude Code on this PC\./);
  assert.ok(!/\[object Object\]|ValueError/.test(said), said);
  await add.getByRole("button", { name: "Cancel" }).click();

  // Edit a tool revoked behind the page: the engine's LookupError.
  await toolRow("Codex").getByRole("button", { name: "Edit" }).click();
  py(`from fabric_desktop.service import Service\nService().integration_store.revoke_client(${JSON.stringify(ids.codex)})`);
  await dialog("Edit Codex").getByRole("button", { name: "Save" }).click();
  said = await toastSays(/^Not saved: /);
  assert.match(said, /^Not saved: That tool isn't connected\./);
  assert.ok(!/\[object Object\]|LookupError/.test(said), said);
  assert.deepStrictEqual(app.errors, []);
});
