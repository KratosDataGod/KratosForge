// The warehouse's Monitoring tab (whmonitor.js) beyond warehouse-monitoring.test.cjs: monitoring that
// can't be read and is then off (with what went wrong while looking), a SQL analytics endpoint read by its
// own id, a view with a note, an empty view, the alert check with nothing to alert on, a window cut at
// 500 rows, a view that fails to read, an organization that turned building off, a build where one
// warehouse fails, and the automatic build in a workspace you said yes to, when it works and when it
// doesn't. Every engine call is mocked.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
let app;
const page = () => app.page;
const calls = (m) => app.calls.filter((c) => c.method === m).map((c) => c.params);

const WS = "11111111-2222-3333-4444-555555555555";
const WH_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee";
const EP_ID = "cccccccc-bbbb-cccc-dddd-eeeeeeeeeeee";
const WH = { kind: "warehouse", workspace_id: WS, workspace_name: "Sales Prod", item_id: WH_ID, name: "WH_Gold" };
const DB = { workspace_id: WS, item_id: "db1", name: "Sales_Prod_Monitoring_Database", last_seen: null };
const ITEMS = [{ id: WH_ID, type: "Warehouse", displayName: "WH_Gold" }, { id: "bbbbbbbb-bbbb-cccc-dddd-eeeeeeeeeeee", type: "Warehouse", displayName: "WH_Silver" },
  { id: EP_ID, type: "SQLEndpoint", displayName: "LH_Gold" }, { id: "lh-item", type: "Lakehouse", displayName: "LH_Gold" }];
const SUMMARY = { columns: [{ name: "Queries", type: "long" }, { name: "Failed", type: "long" }], rows: [[0, 0]] };
const ROWS = { columns: [{ name: "OperationName", type: "string" }, { name: "DurationMs", type: "long" }], rows: [["Batch", 1200.5]] };

// Per test: the status answers in turn (an Error throws), each view's answer, and the build's.
let statuses, views, build, settings;
const on = (more = {}) => ({ workspace_id: WS, monitored: true, database: DB, querysets: {}, problems: [], build_allowed: true, auto: false, ...more });

before(async () => {
  if (skip) return;
  app = await openApp({ mocks: {
    app_settings: () => settings,
    sql_queries_list: () => ({ places: [], queries: [] }),
    list_items: () => ITEMS,
    wh_monitoring_status: () => {
      const s = statuses.length > 1 ? statuses.shift() : statuses[0];
      if (s instanceof Error) throw s;
      return s;
    },
    wh_monitoring_view: (p) => {
      const v = views[p.view] ?? { columns: [], rows: [] };
      if (v instanceof Error) throw v;
      return { view: p.view, window: p.window, truncated: false, ...v };
    },
    wh_monitoring_build: (p) => build(p),
  } });
});
after(async () => { await app?.close(); });

beforeEach(async () => {
  if (skip) return;
  statuses = [on()];
  views = { summary: SUMMARY, slowest: ROWS };
  build = (p) => ({ results: p.warehouses.map((w) => ({ warehouse_id: w.id, queryset: `${w.name}_monitoring`, state: "created" })) });
  settings = { wh_monitoring_auto: { value: [], source: "default" }, wh_monitoring_views: { value: true, source: "default" } };
  await page().evaluate(() => {
    sqlPage.whMonitor = null; sqlPage.whMonitorShown = false; whMonAuto.clear(); whMonAutoChecked.clear();
    const t = document.getElementById("toast"); t.hidden = true; t.textContent = "";
    go("home");
    return loadSettings();
  });
});

const openTab = async (ref = WH) => {
  await page().evaluate((r) => openWarehouseQuery(r), ref);
  await page().waitForSelector(".warehouse-page");
  await page().getByRole("button", { name: "Monitoring", exact: true }).click();
};
const mon = () => page().locator(".wh-mon");
const toastSays = (text) => page().waitForFunction((t) => document.getElementById("toast").textContent === t, text);

test("monitoring that can't be read says why; Refresh finds it off and says what went wrong while looking", { skip }, async () => {
  statuses = [new Error("Fabric didn't answer (503)."), on({ monitored: false, database: null, problems: ["The workspace's items couldn't be listed."] })];
  await openTab();
  await mon().getByRole("alert").getByText("Could not read monitoring: Fabric didn't answer (503).").waitFor();
  await mon().getByRole("button", { name: "Refresh" }).click();
  await mon().getByText("Workspace monitoring is off for Sales Prod").waitFor();
  assert.strictEqual(await mon().locator(".wh-mon-off .muted-text.small").innerText(), "While looking: The workspace's items couldn't be listed.");
  const n = calls("wh_monitoring_status").length;
  await mon().getByRole("button", { name: "Check again" }).click();
  await page().waitForFunction((n) => !sqlPage.whMonitor.loading && sqlPage.whMonitor.status, n);
  assert.strictEqual(calls("wh_monitoring_status").length, n + 1);
  assert.deepStrictEqual(calls("wh_monitoring_view"), [], "nothing is read from a database that isn't there");
});

test("an endpoint is read by its own id; a note, an empty window, the alert check, 500 rows and a failed read each say so", { skip }, async () => {
  views.slowest = { columns: [], rows: [], note: "This view needs the WarehouseExecutions table, which has no rows yet." };
  const from = app.calls.length;
  await openTab({ kind: "endpoint", workspace_id: WS, workspace_name: "Sales Prod", item_id: "lh-item", name: "LH_Gold" });
  await mon().getByText("This view needs the WarehouseExecutions table, which has no rows yet.").waitFor();
  const read = app.calls.slice(from).filter((c) => c.method === "wh_monitoring_view").map((c) => c.params.warehouse_id);
  assert.ok(read.length && read.every((id) => id === EP_ID), "the endpoint's id, not its lakehouse's");
  assert.strictEqual(await page().locator(".wh-mon-tiles .wh-mon-tile").nth(2).locator("b").innerText(), "–", "no queries, no failure rate");

  const choose = async (label, value) => {
    await page().getByRole("combobox", { name: label }).selectOption(value);
    await page().waitForFunction(() => sqlPage.whMonitor.result);
  };
  await choose("View", "failures");
  await mon().getByText("Nothing recorded in this window.").waitFor();
  await choose("View", "alert");
  await mon().getByText("Nothing to alert on: fewer than 10 queries or under 20% failing in the last 15 minutes.").waitFor();
  views.activity = { ...ROWS, truncated: true };
  await choose("View", "activity");
  await mon().getByText("Showing the first 500 rows of this window. Pick a shorter window to narrow it.").waitFor();
  assert.deepStrictEqual(await mon().locator(".wh-mon-table tbody td").allInnerTexts(), ["Batch", (1200.5).toLocaleString()]);
  views.activity = new Error("Error: The KQL query was throttled.");
  await choose("Window", "7d");
  await mon().getByText("Could not read it: The KQL query was throttled.").waitFor();
});

test("with building turned off by the organization, built views still open in Fabric", { skip }, async () => {
  statuses = [on({ build_allowed: false, auto: true, querysets: { WH_Gold_monitoring: "q-gold" } })];
  await openTab();
  const side = page().locator(".wh-mon-card.side");
  await side.getByText("Your organization turned off building monitoring views.").waitFor();
  assert.strictEqual(await side.getByRole("button", { name: /views…/ }).count(), 0);
  assert.match(await side.innerText(), /New warehouses in this workspace get theirs without asking/);
  await side.getByRole("button", { name: "Open in Fabric" }).click();
  await page().waitForFunction(() => state.view === "fabric");
  assert.match(await page().evaluate(() => String(fabricPage.page)), /q-gold/, "Fabric opens on the queryset");
});

test("a build where one warehouse fails says which, and how many were built", { skip }, async () => {
  build = (p) => ({ results: p.warehouses.map((w, i) => (i === 0 ? { queryset: `${w.name}_monitoring`, state: "created" }
    : { queryset: `${w.name}_monitoring`, state: "failed", error: "Forbidden: you need Contributor on the workspace." })) });
  await openTab();
  await page().getByRole("button", { name: "Build views…" }).click();
  const dialog = page().getByRole("dialog");
  await dialog.getByRole("checkbox", { name: "LH_Gold" }).uncheck();
  await dialog.getByRole("checkbox", { name: "WH_Gold" }).uncheck();
  await dialog.getByRole("checkbox", { name: "WH_Silver" }).uncheck();
  assert.ok(await dialog.getByRole("button", { name: "Build", exact: true }).isDisabled(), "nothing ticked, nothing to build");
  await dialog.getByRole("checkbox", { name: "WH_Gold" }).check();
  await dialog.getByRole("checkbox", { name: "WH_Silver" }).check();
  await dialog.getByRole("button", { name: "Build 2 querysets" }).click();
  await toastSays("Built 1 of 2. WH_Silver_monitoring: Forbidden: you need Contributor on the workspace.");
  assert.deepStrictEqual(calls("wh_monitoring_build").at(-1).warehouses.map((w) => w.name), ["WH_Gold", "WH_Silver"]);
});

test("in a workspace you said yes to, the tab builds the views itself and says so, or says why it couldn't", { skip }, async () => {
  settings.wh_monitoring_auto = { value: [WS.toUpperCase()], source: "user" };
  await page().evaluate(() => loadSettings());
  statuses = [on({ auto: true })];
  await openTab();
  await toastSays("Built monitoring views for WH_Gold in Fabric (WH_Gold_monitoring), as you asked for this workspace.");
  assert.deepStrictEqual(calls("wh_monitoring_build").at(-1).warehouses, [{ id: WH_ID, name: "WH_Gold" }]);

  build = () => { throw new Error("Capacity paused"); };
  const silver = { ...WH, item_id: ITEMS[1].id, name: "WH_Silver" };
  await openTab(silver);
  await toastSays("Could not build monitoring views for WH_Silver: Capacity paused");
});
