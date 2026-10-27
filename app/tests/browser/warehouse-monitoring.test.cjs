// WH-19: the warehouse's Monitoring tab, in a real browser (see harness.cjs for how to run it).
// Monitoring reads and the build are mocked: the engine's side is tested in test_warehouse_monitoring.py.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { playwright, openApp } = require("./harness.cjs");

const WS = "11111111-2222-3333-4444-555555555555";
const WH_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee";
const WH = { kind: "warehouse", workspace_id: WS, workspace_name: "Sales Prod", item_id: WH_ID, name: "WH_Gold" };
const DB = { workspace_id: WS, item_id: "db1", name: "Sales_Prod_Monitoring_Database", cluster: "https://trd-a.z4.kusto.fabric.microsoft.com",
  last_seen: "2026-10-26T20:58:00Z", items: 3 };
const SHOTS = process.env.SCREENSHOT_DIR;
const OP = "4f1c2b8e-1d2a-4c3b-9e8f-0a1b2c3d4e5f";
const EVIL = "<img src=x onerror=\"window.monPwned=1\">";

let app;
let status = { workspace_id: WS, monitored: true, database: DB, querysets: {}, problems: [], build_allowed: true, auto: false };
let settings = { wh_monitoring_auto: { value: [], source: "default" }, wh_monitoring_views: { value: true, source: "default" } };
const skip = !playwright && "Playwright is not installed";
const page = () => app.page;
const calls = (m) => app.calls.filter((c) => c.method === m).map((c) => c.params);
const until = async (ok, ms = 5000) => {
  for (const end = Date.now() + ms; !ok(); ) {
    if (Date.now() > end) throw new Error("timed out waiting");
    await new Promise((r) => setTimeout(r, 50));
  }
};

const VIEWS = {
  summary: { columns: [{ name: "Queries", type: "long" }, { name: "Failed", type: "long" }, { name: "P50DurationMs", type: "real" },
    { name: "P95DurationMs", type: "real" }, { name: "CpuTimeMs", type: "long" }, { name: "LastSeen", type: "datetime" }],
  rows: [[1240, 31, 820, 14200, 5_400_000, "2026-10-26T20:58:00Z"]] },
  slowest: { columns: [{ name: "OperationEndTime", type: "datetime" }, { name: "OperationName", type: "string" }, { name: "Status", type: "string" },
    { name: "ResultCode", type: "string" }, { name: "DurationMs", type: "long" }, { name: "CpuTimeMs", type: "long" }, { name: "User", type: "string" },
    { name: "OperationId", type: "string" }],
  rows: [["2026-10-26T01:42:00Z", "Batch", "Succeeded", "0", 182000, 640000, EVIL, OP],
    ["2026-10-25T23:10:00Z", "Batch", "Failed", "24701", 96000, 120000, "dana@contoso.com", "not an id; DROP TABLE x"]] },
  activity: { columns: [{ name: "Hour", type: "datetime" }, { name: "Queries", type: "long" }], rows: [["2026-10-26T20:00:00Z", 52]] },
};

before(async () => {
  if (skip) return;
  app = await openApp({
    mocks: {
      app_settings: () => settings,
      save_app_settings: ({ changes }) => { for (const [k, v] of Object.entries(changes)) settings[k] = { value: v, source: "user" }; return settings; },
      sql_queries_list: () => ({ places: [], queries: [] }),
      wh_monitoring_status: () => status,
      wh_monitoring_view: (p) => ({ view: p.view, window: p.window, truncated: false, ...(VIEWS[p.view] || { columns: [], rows: [] }) }),
      list_items: () => [{ id: WH_ID, type: "Warehouse", displayName: "WH_Gold" }, { id: "bbbbbbbb-bbbb-cccc-dddd-eeeeeeeeeeee", type: "Warehouse", displayName: "WH_Silver" },
        { id: "cccccccc-bbbb-cccc-dddd-eeeeeeeeeeee", type: "SQLEndpoint", displayName: "LH_Gold" }, { id: "x", type: "Notebook", displayName: "nb" }],
      wh_monitoring_build: (p) => {
        status = { ...status, querysets: Object.fromEntries(p.warehouses.map((w) => [`${w.name}_monitoring`, `q-${w.name}`])), auto: !!p.auto };
        if (p.auto) settings.wh_monitoring_auto = { value: [WS], source: "user" };
        return { workspace_id: WS, database: DB.name, failed: 0,
          results: p.warehouses.map((w) => ({ warehouse_id: w.id, queryset: `${w.name}_monitoring`, item_id: `q-${w.name}`, state: "created" })) };
      },
    },
  });
  await page().evaluate(() => loadSettings());
  await page().evaluate((wh) => openWarehouseQuery(wh), WH);
  await page().waitForSelector(".warehouse-page");
  app.errors.length = 0;
});

after(async () => { await app?.close(); });

test("Monitoring shows the warehouse's queries from workspace monitoring", { skip }, async () => {
  await page().getByRole("button", { name: "Monitoring", exact: true }).click();
  await page().getByRole("tab", { name: "Monitoring" }).waitFor();
  await page().waitForFunction(() => /1,240/.test(document.querySelector(".wh-mon-tiles")?.textContent || ""));
  const tiles = await page().locator(".wh-mon-tiles").textContent();
  assert.match(tiles, /Queries1,240/);
  assert.match(tiles, /0\.8 s.*P95 14\.2 s/);
  assert.match(tiles, /2\.5%31 queries/);
  assert.match(await page().locator(".wh-mon-on").textContent(), /Sales_Prod_Monitoring_Database/);
  const view = calls("wh_monitoring_view").find((c) => c.view === "slowest");
  assert.deepStrictEqual({ ...view }, { database_workspace_id: WS, database_id: "db1", warehouse_id: WH_ID, view: "slowest", window: "24h" });
  assert.strictEqual(calls("wh_monitoring_build").length, 0, "nothing is built without asking");
  assert.match(await page().locator(".wh-mon-card.side").textContent(), /Not built yet.*WH_Gold_monitoring/);
});

test("a name from monitoring is text, and only an operation id gets a Query insights button", { skip }, async () => {
  assert.strictEqual(await page().locator(".wh-mon img").count(), 0);
  assert.strictEqual(await page().evaluate(() => window.monPwned), undefined);
  const rows = page().locator(".wh-mon-table tbody tr");
  assert.strictEqual(await rows.nth(0).getByRole("button", { name: "Query insights" }).count(), 1);
  assert.strictEqual(await rows.nth(1).getByRole("button", { name: "Query insights" }).count(), 0, "a value that isn't an id never goes into SQL");
});

test("Query insights opens a query tab for that operation", { skip }, async () => {
  await page().locator(".wh-mon-table tbody tr").nth(0).getByRole("button", { name: "Query insights" }).click();
  await page().waitForFunction(() => !sqlPage.whMonitorShown);
  const tab = await page().evaluate(() => sqlPage.tabs[sqlPage.active]);
  assert.strictEqual(tab.name, "Query insights");
  assert.match(tab.sql, new RegExp(`distributed_statement_id = '${OP}'`));
  const dialog = page().getByRole("dialog");
  if (await dialog.count()) await dialog.getByRole("button", { name: "Cancel" }).click();  // the run asks first, like any run in Fabric
  await page().getByRole("tab", { name: "Monitoring" }).click();
  await page().locator(".wh-mon-tiles").waitFor();
});

test("another view and window read again", { skip }, async () => {
  const before = calls("wh_monitoring_view").length;
  await page().getByRole("combobox", { name: "View" }).selectOption("activity");
  await page().waitForFunction(() => /Hour/.test(document.querySelector(".wh-mon-table thead")?.textContent || ""));
  await page().getByRole("combobox", { name: "Window" }).selectOption("7d");
  await until(() => calls("wh_monitoring_view").slice(before).filter((c) => c.window === "7d").length >= 2);
  const got = calls("wh_monitoring_view").slice(before);
  assert.ok(got.some((c) => c.view === "activity" && c.window === "24h"));
  assert.ok(got.some((c) => c.view === "activity" && c.window === "7d") && got.some((c) => c.view === "summary" && c.window === "7d"));
});

test("Build views asks first, lists the workspace's warehouses and builds the ticked ones", { skip }, async () => {
  await page().getByRole("button", { name: "Build views…" }).click();
  const dialog = page().getByRole("dialog");
  await dialog.waitFor();
  assert.match(await dialog.textContent(), /Sales_Prod_Monitoring_Database/);
  assert.match(await dialog.textContent(), /new: WH_Gold_monitoring.*new: LH_Gold_monitoring.*new: WH_Silver_monitoring/s);
  assert.doesNotMatch(await dialog.textContent(), /nb_monitoring/);
  if (SHOTS) {
    await page().waitForTimeout(400);
    await page().screenshot({ path: path.join(SHOTS, "wh-19-build.png") });
  }
  await dialog.getByRole("checkbox", { name: "WH_Silver" }).uncheck();
  const auto = dialog.getByRole("checkbox", { name: /without asking again/ });
  assert.strictEqual(await auto.isChecked(), false, "building without asking is opt-in");
  await auto.check();
  await dialog.getByRole("button", { name: "Build 2 querysets" }).click();
  await page().waitForFunction(() => /Open in Fabric/.test(document.querySelector(".wh-mon-card.side")?.textContent || ""));
  const sent = calls("wh_monitoring_build").at(-1);
  assert.strictEqual(sent.confirm, true);
  assert.strictEqual(sent.auto, true);
  assert.deepStrictEqual(sent.warehouses.map((w) => w.name), ["WH_Gold", "LH_Gold"]);
  assert.strictEqual(sent.database_id, "db1");
  assert.match(await page().locator(".wh-mon-card.side").textContent(), /without asking/);
  if (SHOTS) {
    await page().waitForFunction(() => !document.querySelector(".dialog"));
    await page().waitForTimeout(600);
    await page().screenshot({ path: path.join(SHOTS, "wh-19-monitoring.png") });
  }
});

test("a warehouse in a yes-workspace gets its views when the page opens it, without a dialog", { skip }, async () => {
  const n = calls("wh_monitoring_build").length;
  await page().evaluate((wh) => openWarehouseQuery(wh), { ...WH, item_id: "dddddddd-bbbb-cccc-dddd-eeeeeeeeeeee", name: "WH_New" });
  await until(() => calls("wh_monitoring_build").length > n);
  const built = calls("wh_monitoring_build").slice(n);
  assert.strictEqual(built.length, 1);
  assert.deepStrictEqual(built[0].warehouses, [{ id: "dddddddd-bbbb-cccc-dddd-eeeeeeeeeeee", name: "WH_New" }]);
  assert.strictEqual(built[0].confirm, true);
  assert.strictEqual(await page().getByRole("dialog").count(), 0);
});

test("Settings lists the workspace said yes to, and Remove makes the app ask again", { skip }, async () => {
  await page().evaluate(() => {  // the SQL part of Settings > Advanced, with this test's settings
    const s = { ...state.settings, sql_temp_dir: { value: "" }, sql_warehouse_writes: { value: true } };
    for (const [key] of SQL_SETTINGS) s[key] = { value: 1 };
    const box = document.createElement("div");
    box.id = "mon-settings";
    box.append(sqlSettingsSection(s, () => false));
    document.body.append(box);
  });
  const row = page().getByRole("button", { name: "Remove Sales Prod" });
  await row.waitFor();
  await row.click();
  await page().waitForFunction(() => (state.settings?.wh_monitoring_auto?.value || []).length === 0);
  assert.deepStrictEqual(calls("save_app_settings").at(-1), { changes: { wh_monitoring_auto: [] } });
  await page().evaluate(() => document.getElementById("mon-settings").remove());
});

test("with monitoring off, the tab says how to turn it on and builds nothing", { skip }, async () => {
  status = { workspace_id: WS, monitored: false, database: null, querysets: {}, problems: [], build_allowed: true, auto: false };
  settings.wh_monitoring_auto = { value: [], source: "user" };
  await page().evaluate(() => loadSettings());
  await page().evaluate((wh) => openWarehouseQuery(wh), { ...WH, item_id: "eeeeeeee-bbbb-cccc-dddd-eeeeeeeeeeee", name: "WH_Off" });
  await page().getByRole("button", { name: "Monitoring", exact: true }).click();
  await page().waitForFunction(() => /Workspace monitoring is off/.test(document.querySelector(".wh-mon")?.textContent || ""));
  assert.match(await page().locator(".wh-mon").textContent(), /Workspace settings > Monitoring/);
  assert.strictEqual(await page().getByRole("button", { name: /Build views/ }).count(), 0);
  assert.deepStrictEqual(app.errors, []);
});
