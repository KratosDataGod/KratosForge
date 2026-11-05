// Settings > Connections (Preview, connections.js): profiles are listed by reference, Check workspace
// runs discovery (GETs only) and shows warehouses, clusters and catalogs with the suggested ones
// picked, Save names the CLI profile and the picks (never a host or token), and Remove asks first.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { playwright, openApp, axeSource, axeFindings } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
const SHOTS = process.env.SCREENSHOT_DIR;
const HOST = "https://adb-1234567890123456.7.azuredatabricks.net";

const PROFILES = () => ({
  active: "default",
  profiles: [
    { id: "default", name: "This PC and Fabric", kind: "fabric", active: true, problems: [],
      connections: [{ id: "fabric", platform: "fabric", label: "", key: "", host: "", account: "", method: "app-sign-in", cli: "",
        warehouse_id: "", cluster_id: "", catalog: "", schema: "" }] },
    { id: "p-sales", name: "Sales dev", kind: "databricks", active: false, problems: [],
      connections: [{ id: "dev", platform: "databricks", label: "", key: "profile:p-sales:dev", host: HOST, account: "", method: "cli-profile",
        cli: "dev", warehouse_id: "wh-run", cluster_id: "", catalog: "main", schema: "sales" }] }],
  databricks_cli: { path: "C:\\Users\\me\\.databrickscfg", found: true, profiles: [
    { name: "dev", host: HOST, auth_type: "databricks-cli", warning: "" },
    { name: "old", host: HOST, auth_type: "pat", warning: "Keeps a personal access token in plain text in this file. Sign in with 'databricks auth login' instead." }] },
  snowflake_cli: { path: "", found: false, default: null, connections: [] },
});
const FOUND = {
  schema: "databricks-discovery@1", host: HOST, signed_in: true, suggested: { warehouse_id: "wh-run", cluster_id: "c-ok" },
  parts: {
    identity: { ok: true, value: { user: "dev@contoso.com", name: "Dev" } },
    warehouses: { ok: true, value: [
      { id: "wh-run", name: "Shared SQL", state: "RUNNING", size: "Small", serverless: true, auto_stop_mins: 10, notes: [] },
      { id: "wh-stop", name: "Big SQL", state: "STOPPED", size: "Large", serverless: false, auto_stop_mins: 0,
        notes: ["No auto-stop: once started, it bills DBUs until someone stops it."] }] },
    clusters: { ok: true, value: [
      { id: "c-ok", name: "dev", state: "TERMINATED", spark_version: "16.4.x-scala2.12", access_mode: "Dedicated", auto_stop_mins: 30,
        connect: { usable: true, problems: [] }, notes: [] },
      { id: "c-none", name: "no uc", state: "RUNNING", spark_version: "16.4.x-scala2.12", access_mode: "NONE", auto_stop_mins: 60,
        connect: { usable: false, problems: ["Its access mode (NONE) has no Unity Catalog."] }, notes: [] }] },
    metastore: { ok: true, value: { id: "ms1", name: "westeurope", region: "westeurope", default_catalog: "main" } },
    catalogs: { ok: false, error: "Databricks refused: 403 PERMISSION_DENIED" },
  },
};

let app;
const page = () => app.page;

before(async () => {
  if (skip) return;
  app = await openApp({
    viewport: { width: 1280, height: 1000 },
    engine: ["app_settings"],  // Settings renders once the engine's settings are in
    mocks: {
      engine_profiles: PROFILES,
      engine_discover: () => FOUND,
      engine_connection_save: () => ({ profile: "p-sales", connection: "dev-2", key: "profile:p-sales:dev-2" }),
      engine_connection_remove: (p) => ({ profile: p.profile, removed: p.connection }),
    },
  });
  await page().waitForFunction(() => typeof go === "function" && typeof renderConnectionsTab === "function" && !!document.querySelector(".rail-item"));
  app.errors.length = 0;
});
after(async () => { if (app) await app.close(); });

async function openTab() {
  await page().evaluate(() => { go("home"); state.settingsTab = "connections"; go("settings"); });
  await page().locator(".cx-profiles .cx-profile").first().waitFor({ timeout: 10000 });
}

test("the Connections tab lists each profile's connections by reference", { skip }, async () => {
  await openTab();
  assert.match(await page().locator('.page.settings [role="tab"][aria-selected="true"]').textContent(), /^Connections/);
  const profiles = await page().locator(".cx-profile").allTextContents();
  assert.strictEqual(profiles.length, 2);
  assert.match(profiles[0], /This PC and Fabric.*Fabric.*In use.*The app's own sign-in/);
  assert.match(profiles[1], /Sales dev.*Databricks.*adb-1234567890123456\.7\.azuredatabricks\.net · CLI dev · warehouse wh-run · main\.sales/);
  assert.strictEqual(await page().locator(".cx-profile").first().locator("button", { hasText: "Remove" }).count(), 0);
});

test("a CLI profile that keeps a token in plain text says so", { skip }, async () => {
  await page().locator('select[aria-label="Databricks CLI profile"]').selectOption("old");
  assert.match(await page().locator(".cx-form .callout").textContent(), /personal access token in plain text/);
});

test("Check workspace shows warehouses, clusters and catalogs, with the suggested ones picked", { skip }, async () => {
  await page().locator('select[aria-label="Databricks CLI profile"]').selectOption("dev");
  await page().locator(".cx-form button", { hasText: "Check workspace" }).click();
  await page().locator(".cx-found").waitFor();
  const call = app.calls.find((c) => c.method === "engine_discover");
  assert.deepStrictEqual(call.params, { connection: "cli:dev" });
  assert.match(await page().locator(".cx-found").textContent(), /Signed in as dev@contoso\.com\./);
  assert.ok(await page().locator('input[aria-label="Shared SQL"]').isChecked());
  assert.match(await page().locator(".cx-warehouses").textContent(), /No auto-stop: once started, it bills DBUs/);
  assert.ok(await page().locator('input[aria-label="dev"]').isChecked());
  assert.strictEqual(await page().locator('input[aria-label="no uc"]').count(), 0);  // Databricks Connect can't use it
  assert.match(await page().locator(".cx-clusters").textContent(), /has no Unity Catalog/);
  assert.match(await page().locator(".cx-found").textContent(), /403 PERMISSION_DENIED/);
  assert.strictEqual(await page().locator('input[aria-label="Catalog"]').inputValue(), "main");  // the metastore's default
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "settings-connections.png"), fullPage: true });
});

test("Save names the CLI profile and the picks, never a host", { skip }, async () => {
  await page().locator('input[aria-label="Big SQL"]').check();
  await page().locator('input[aria-label="Schema"]').fill("sales");
  await page().locator('select[aria-label="Save to profile"]').selectOption("p-sales");
  await page().locator(".cx-save button", { hasText: "Save connection" }).click();
  for (let i = 0; i < 100 && !app.calls.some((c) => c.method === "engine_connection_save"); i++) await page().waitForTimeout(50);
  const call = app.calls.find((c) => c.method === "engine_connection_save");
  assert.deepStrictEqual(call.params, { databricks_cli: "dev", warehouse_id: "wh-stop", cluster_id: "c-ok", catalog: "main",
    schema: "sales", profile: "p-sales" });
});

test("Remove asks first and only then removes", { skip }, async () => {
  await openTab();
  await page().locator(".cx-profile", { hasText: "Sales dev" }).locator("button", { hasText: "Remove" }).click();
  await page().locator("#dialog-root .dialog").waitFor();
  assert.match(await page().locator("#dialog-root .dialog").textContent(), /Remove dev from Sales dev\?.*CLI's own profile stays/);
  await page().locator("#dialog-root button", { hasText: "Cancel" }).click();
  assert.ok(!app.calls.some((c) => c.method === "engine_connection_remove"));
  await page().locator(".cx-profile", { hasText: "Sales dev" }).locator("button", { hasText: "Remove" }).click();
  await page().locator("#dialog-root .dialog button", { hasText: "Remove" }).click();
  for (let i = 0; i < 100 && !app.calls.some((c) => c.method === "engine_connection_remove"); i++) await page().waitForTimeout(50);
  assert.deepStrictEqual(app.calls.find((c) => c.method === "engine_connection_remove").params, { profile: "p-sales", connection: "dev" });
});

test("Settings > Connections has no serious axe findings", { skip: skip || (!axeSource && "axe-core is not installed") }, async () => {
  await openTab();
  await page().locator('select[aria-label="Databricks CLI profile"]').selectOption("dev");
  await page().locator(".cx-form button", { hasText: "Check workspace" }).click();
  await page().locator(".cx-found").waitFor();
  assert.deepStrictEqual(await axeFindings(page(), "#view .connections"), []);
});

test("no page errors", { skip }, () => assert.deepStrictEqual(app.errors, []));
