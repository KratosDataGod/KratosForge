// Settings > Connections (Preview, connections.js), the paths settings-connections.test.cjs leaves out:
// the profiles can't be read and Try again, a profile with problems or no connections, a PC with no
// Databricks CLI profile, a workspace you aren't signed in to, a workspace with no warehouses or
// clusters but catalogs to pick from, saving to a new profile, and the Snowflake side of Add a connection.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
const HOST = "https://adb-99.1.azuredatabricks.net";

const base = () => ({
  active: "default",
  profiles: [
    { id: "default", name: "This PC and Fabric", kind: "fabric", active: true, problems: [],
      connections: [{ id: "fabric", platform: "fabric", label: "", host: "", account: "", method: "app-sign-in", cli: "",
        warehouse_id: "", cluster_id: "", catalog: "", schema: "" }] },
    { id: "p-local", name: "Laptop only", kind: "local", active: false, problems: [], connections: [] },
    { id: "p-broken", name: "Broken one", kind: "", active: false,
      problems: ["The profile file isn't valid TOML: line 3", "Connection 'x' names no platform"], connections: [] },
    { id: "p-sf", name: "Warehouse team", kind: "snowflake", active: false, problems: [],
      connections: [{ id: "sf1", platform: "snowflake", label: "Finance", host: "", account: "acme-xy12", method: "cli-connection", cli: "",
        warehouse_id: "", cluster_id: "", catalog: "", schema: "" }] }],
  databricks_cli: { found: true, profiles: [{ name: "dev", host: HOST, auth_type: "databricks-cli", warning: "" }] },
  snowflake_cli: { found: false, connections: [] },
});

let profiles;
let failRead;
let found;
let app;
const page = () => app.page;
const callsOf = (m) => app.calls.filter((c) => c.method === m);
const waitCall = async (m, n = 1) => {
  for (let i = 0; i < 200 && callsOf(m).length < n; i++) await page().waitForTimeout(25);
  assert.ok(callsOf(m).length >= n, `${m} was called`);
  return callsOf(m)[n - 1];
};

before(async () => {
  if (skip) return;
  app = await openApp({
    viewport: { width: 1280, height: 1000 },
    engine: ["app_settings"],  // Settings renders once the engine's settings are in
    mocks: {
      engine_profiles: () => {
        if (failRead) { throw new Error("OSError: The profiles folder couldn't be opened"); }
        return profiles;
      },
      engine_discover: () => found,
      engine_connection_save: (p) => ({ profile: p.profile || "p-new", connection: p.snowflake_cli || p.databricks_cli }),
    },
  });
  await page().waitForFunction(() => typeof go === "function" && typeof renderConnectionsTab === "function" && !!document.querySelector(".rail-item"));
  app.errors.length = 0;
});
after(async () => { if (app) await app.close(); });

async function openTab(setup) {
  failRead = false;
  profiles = base();
  if (setup) setup(profiles);
  await page().evaluate(() => {
    Object.assign(cx, { platform: "databricks", cli: "", found: null, pick: { warehouse: "", cluster: "" }, catalog: "", schema: "", target: "", newName: "" });
    go("home"); state.settingsTab = "connections"; go("settings");
  });
  await page().locator(".cx-profiles .cx-profile").first().waitFor({ timeout: 10000 });
}

test("profiles that can't be read say so, and Try again reads them again", { skip }, async () => {
  await openTab();
  failRead = true;
  await page().locator(".cx-profiles button", { hasText: "Refresh" }).click();
  await page().getByText("The connection profiles couldn't be read.").waitFor();
  assert.match(await page().locator("#toast").textContent(), /^Couldn't read the connection profiles: /);
  assert.strictEqual(await page().locator(".cx-profile").count(), 0);
  failRead = false;
  const before = callsOf("engine_profiles").length;
  await page().locator(".connections button", { hasText: "Try again" }).click();
  await page().locator(".cx-profile").first().waitFor();
  assert.strictEqual(callsOf("engine_profiles").length, before + 1);
  assert.strictEqual(await page().locator(".cx-profile").count(), 4);
});

test("a profile's problems are listed, one with no connections says runs stay on this PC, and Refresh reads again", { skip }, async () => {
  await openTab();
  const broken = page().locator(".cx-profile", { hasText: "Broken one" });
  assert.deepStrictEqual(await broken.locator(".cx-problems li").allTextContents(),
    ["The profile file isn't valid TOML: line 3", "Connection 'x' names no platform"]);
  assert.match(await broken.textContent(), /Can't be read/);
  assert.match(await page().locator(".cx-profile", { hasText: "Laptop only" }).textContent(), /This PC only.*No platform connections: runs stay on this PC\./);
  assert.match(await page().locator(".cx-profile", { hasText: "Warehouse team" }).textContent(), /Snowflake · Finance.*acme-xy12 · cli-connection/);
  const n = callsOf("engine_profiles").length;
  profiles.profiles[1].name = "Laptop, renamed";
  await page().locator(".cx-profiles button", { hasText: "Refresh" }).click();
  await page().locator(".cx-profile", { hasText: "Laptop, renamed" }).waitFor();
  assert.strictEqual(callsOf("engine_profiles").length, n + 1);
});

test("with no Databricks CLI profile, Add a connection says how to sign in", { skip }, async () => {
  await openTab((p) => { p.databricks_cli = { found: false, profiles: [] }; });
  assert.match(await page().locator(".cx-add .cx-empty").textContent(),
    /There's no Databricks CLI profile on this PC yet\.Sign in once in a terminal, then Refresh:databricks auth login --host/);
  await openTab((p) => { p.databricks_cli = { found: true, profiles: [{ name: "nohost", host: "" }] }; });
  assert.match(await page().locator(".cx-add .cx-empty").textContent(), /The Databricks CLI's file has no profile with a workspace\./);
  await openTab((p) => { p.databricks_cli = { found: true, error: "The .databrickscfg file isn't valid: line 7", profiles: [] }; });
  assert.match(await page().locator(".cx-add .cx-empty").textContent(), /The \.databrickscfg file isn't valid: line 7/);
  assert.strictEqual(await page().locator(".cx-add button", { hasText: "Save connection" }).count(), 0);
});

test("a workspace you aren't signed in to shows the sign-in command", { skip }, async () => {
  found = { signed_in: false, parts: { identity: { ok: false, error: "401: The token expired" } } };
  await openTab();
  await page().locator('select[aria-label="Databricks CLI profile"]').selectOption("dev");
  await page().locator(".cx-form button", { hasText: "Check workspace" }).click();
  const alert = page().locator('.cx-form [role="alert"]');
  await alert.waitFor();
  assert.match(await alert.textContent(), /401: The token expired.*databricks auth login --profile dev/);
  assert.strictEqual(await page().locator(".cx-found").count(), 0);
});

test("no warehouses or clusters say so, catalogs are a list, and Save to a new profile sends its name", { skip }, async () => {
  found = { signed_in: true, suggested: {}, parts: {
    identity: { ok: true, value: { user: "ann@contoso.com" } },
    warehouses: { ok: true, value: [] }, clusters: { ok: true, value: [] },
    metastore: { ok: true, value: { default_catalog: "" } },
    catalogs: { ok: true, value: [{ name: "main" }, { name: "sales" }] } } };
  await openTab();
  await page().locator('select[aria-label="Databricks CLI profile"]').selectOption("dev");
  assert.strictEqual(await page().locator(".cx-form p.mono").textContent(), HOST);
  await page().locator(".cx-form button", { hasText: "Check workspace" }).click();
  await page().locator(".cx-found").waitFor();
  const text = await page().locator(".cx-found").textContent();
  assert.match(text, /Signed in as ann@contoso\.com\./);
  assert.match(text, /No warehouses you can see\./);
  assert.match(text, /No all-purpose clusters you can see\./);
  assert.deepStrictEqual(await page().locator('select[aria-label="Catalog"] option').allTextContents(), ["None", "main", "sales"]);
  await page().locator('select[aria-label="Catalog"]').selectOption("sales");
  await page().locator('input[aria-label="Schema"]').fill("orders");
  // A profile with problems can't take a new connection.
  assert.deepStrictEqual(await page().locator('select[aria-label="Save to profile"] option').allTextContents(),
    ["This PC and Fabric", "Laptop only", "Warehouse team", "New profile…"]);
  await page().locator('select[aria-label="Save to profile"]').selectOption("new");
  await page().locator('input[aria-label="New profile name"]').fill("  Sales lab  ");
  await page().locator(".cx-save button", { hasText: "Save connection" }).click();
  const call = await waitCall("engine_connection_save");
  assert.deepStrictEqual(call.params, { databricks_cli: "dev", warehouse_id: "", cluster_id: "", catalog: "sales", schema: "orders", profile_name: "Sales lab" });
  await page().waitForFunction(() => /Saved dev\./.test(document.getElementById("toast").textContent));
});

test("a part Databricks refused shows its error instead of the empty line, and a typed catalog is kept", { skip }, async () => {
  found = { signed_in: true, suggested: {}, parts: {
    warehouses: { ok: false, error: "403: no warehouses permission" },
    clusters: { ok: false, error: "403: no clusters permission" },
    catalogs: { ok: false, skipped: true } } };
  await openTab();
  await page().locator('select[aria-label="Databricks CLI profile"]').selectOption("dev");
  await page().locator(".cx-form button", { hasText: "Check workspace" }).click();
  await page().locator(".cx-found").waitFor();
  assert.deepStrictEqual(await page().locator(".cx-part-error").allTextContents(), ["403: no warehouses permission", "403: no clusters permission"]);
  assert.doesNotMatch(await page().locator(".cx-found").textContent(), /No warehouses you can see|Signed in as/);
  await page().locator('input[aria-label="Catalog"]').fill(" hive_metastore ");
  await page().locator('select[aria-label="Save to profile"]').selectOption("p-local");
  await page().locator(".cx-save button", { hasText: "Save connection" }).click();
  const call = await waitCall("engine_connection_save", 2);
  assert.deepStrictEqual(call.params, { databricks_cli: "dev", warehouse_id: "", cluster_id: "", catalog: "hive_metastore", schema: "", profile: "p-local" });
});

test("Snowflake with no CLI connection says how to add one", { skip }, async () => {
  await openTab();
  await page().locator('.cx-add [role="group"] button', { hasText: "Snowflake" }).click();
  assert.strictEqual(await page().locator('.cx-add [role="group"] button', { hasText: "Snowflake" }).getAttribute("aria-pressed"), "true");
  assert.match(await page().locator(".cx-add .cx-empty").textContent(), /There's no Snowflake CLI connection on this PC yet\..*snow connection add/);
  await page().locator('.cx-add [role="group"] button', { hasText: "Databricks" }).click();
  await page().locator('select[aria-label="Databricks CLI profile"]').waitFor();
});

test("a Snowflake CLI connection shows its details, warnings and notes, and Save names only the connection", { skip }, async () => {
  await openTab((p) => {
    p.snowflake_cli = { found: true, notes: ["Your config.toml is readable by other users on this PC."], connections: [
      { name: "fin", account: "acme-xy12", user: "ANN", warehouse: "WH_XS", database: "FIN", schema: "PUBLIC",
        warnings: ["Keeps a password in the file. Use key-pair or SSO instead."] },
      { name: "plain", account: "acme-zz", user: "", warehouse: "", database: "RAW", schema: "" }] };
  });
  await page().locator('.cx-add [role="group"] button', { hasText: "Snowflake" }).click();
  await page().locator('select[aria-label="Snowflake CLI connection"]').selectOption("plain");
  assert.strictEqual(await page().locator(".cx-form p.mono").textContent(), "acme-zz · RAW");
  await page().locator('select[aria-label="Snowflake CLI connection"]').selectOption("fin");
  const form = page().locator(".cx-form");
  assert.strictEqual(await form.locator("p.mono").textContent(), "acme-xy12 · ANN · WH_XS · FIN.PUBLIC");
  assert.match(await form.locator('[role="note"]').textContent(), /Keeps a password in the file/);
  assert.match(await form.textContent(), /readable by other users on this PC.*Checking a Snowflake account from here comes later/);
  assert.strictEqual(await form.locator("button", { hasText: "Check workspace" }).count(), 0);
  await page().locator('select[aria-label="Save to profile"]').selectOption("p-sf");
  const n = callsOf("engine_connection_save").length;
  await page().locator(".cx-save button", { hasText: "Save connection" }).click();
  const call = await waitCall("engine_connection_save", n + 1);
  assert.deepStrictEqual(call.params, { snowflake_cli: "fin", profile: "p-sf" });
});

test("no page errors", { skip }, () => assert.deepStrictEqual(app.errors, []));
