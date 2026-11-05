// The platform skins end-to-end demonstration (Chris's brief, requirement 7; docs/skins-demo.md).
//
// The demo goes through these steps:
//  1. Open a project in the Fabric skin.
//  2. Run SQL on this PC.
//  3. Switch to the Databricks skin and look at the same data, with nothing lost.
//  4. Switch to the Snowflake skin and check the SQL's compatibility.
//  5. Return to KratosForge, and review the results and the optimization information.
//
// Real and mocked parts:
//  - Real: the skins, the settings, the SQL runs and the SQL checks all go through the real engine.
//  - Mocked: everything that would come from the cloud. That covers the lakehouse the SQL page is
//    pointed at and its table list. Optimize's Cost and savings tab is the real engine's (cost_report).
//  - Every mocked name starts with "MOCK", and a "Demo: cloud parts are mocked" label stays on screen
//    for the whole recording.
//  - Nothing signs in or calls Fabric, Databricks or Snowflake.
//
//   DEMO_VIDEO_DIR=<folder> SCREENSHOT_DIR=<folder> NODE_PATH="$(npm root -g)" node --test app/tests/browser/skins-demo.test.cjs
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { playwright, openApp } = require("./harness.cjs");

const SHOTS = process.env.SCREENSHOT_DIR;
const VIDEO = process.env.DEMO_VIDEO_DIR || null;
const skip = !playwright && "Playwright is not installed";

// Mocked cloud: a lakehouse that doesn't exist. The SQL below runs on this PC either way.
const LH = { workspace_id: "mock-ws", lakehouse_id: "mock-lh", name: "MOCK Sales lakehouse" };
const ORDERS = `WITH orders(order_id, region, amount, order_date) AS (VALUES
  (1, 'North', 120.00, DATE '2026-10-01'), (2, 'South', 75.50, DATE '2026-10-01'),
  (3, 'North', 310.25, DATE '2026-10-02'), (4, 'East', 42.00, DATE '2026-10-02'),
  (5, 'West', 198.40, DATE '2026-10-03'), (6, 'South', 260.00, DATE '2026-10-03'),
  (7, 'East', 88.90, DATE '2026-10-04'), (8, 'North', 15.75, DATE '2026-10-04'),
  (9, 'West', 402.10, DATE '2026-10-05'), (10, 'East', 133.30, DATE '2026-10-05'))
SELECT region, count(*) AS orders, sum(amount) AS revenue
FROM orders
GROUP BY region
ORDER BY revenue DESC`;
// Written the Snowflake way: QUALIFY and LIMIT. The check below is against Fabric Warehouse.
const SNOWFLAKE_STYLE = `SELECT region, order_id, amount
FROM orders
QUALIFY ROW_NUMBER() OVER (PARTITION BY region ORDER BY amount DESC) = 1
LIMIT 10`;
let app;
const page = () => app.page;
const shot = async (name) => { if (SHOTS) await page().screenshot({ path: path.join(SHOTS, `demo-${name}.png`) }); };
const pause = (ms) => (VIDEO ? page().waitForTimeout(ms) : null);  // only so a person can follow the recording

before(async () => {
  if (skip) return;
  app = await openApp({
    video: VIDEO,
    engine: ["app_settings", "save_app_settings", "skins_list", "sql_format", "sql_analyze", "sql_start", "sql_status",
      "sql_page", "sql_cancel", "sql_practices", "sql_practice_rules", "cost_report", "optimize_status"],
    mocks: {
      readiness_check: () => [],
      list_tables: () => ({ dbo: ["MOCK_orders"] }),
      table_columns: () => [{ name: "region", type: "string" }, { name: "amount", type: "decimal(18,2)" }],
    },
  });
  await page().evaluate(() => loadSettings());
  await page().waitForFunction(() => (skinState.list || []).length >= 4);
  // On screen for the whole recording: which parts are mocked.
  await page().evaluate(() => {
    const tag = document.createElement("div");
    tag.id = "demo-mock-label";
    tag.textContent = "Demo: cloud parts are mocked (names start with MOCK). SQL runs on this PC.";
    Object.assign(tag.style, { position: "fixed", right: "12px", bottom: "12px", zIndex: 99999, padding: "6px 10px",
      background: "#5c3300", color: "#fff", font: "600 12px Segoe UI, sans-serif", borderRadius: "4px", pointerEvents: "none" });
    document.body.append(tag);
  });
  app.errors.length = 0;
});
after(async () => { await app?.close(); });

async function chooseSkin(name, id) {
  await page().evaluate(() => go("settings"));
  const radio = page().getByRole("group", { name: /^Skin/ }).getByRole("radio", { name });
  await radio.waitFor();
  await radio.scrollIntoViewIfNeeded();
  await pause(800);
  await radio.check();
  await page().waitForFunction((id) => document.body.dataset.skin === id, id);
  await pause(800);
}
async function setDoc(text) {
  await page().evaluate((text) => {
    const v = sqlPage.editor.view;
    v.dispatch({ changes: { from: 0, to: v.state.doc.length, insert: text }, selection: { anchor: 0 } });
    v.focus();
  }, text);
}
const grid = () => page().evaluate(() => [...document.querySelectorAll(".sql-grid tbody tr")]
  .map((tr) => [...tr.querySelectorAll("td")].slice(1).map((td) => td.textContent)));
const kept = () => page().evaluate(() => ({ notebook: state.notebook?.name, edit: state.notebook?.cells[0].source, dirty: state.dirty,
  sql: (sqlPage.tabs || []).map((t) => t.sql) }));
let firstRun;

test("1. a project opens in the Fabric skin", { skip }, async () => {
  await chooseSkin("Fabric-familiar", "fabric");
  // An open notebook with an edit not saved yet: the skin switches below must keep it.
  await page().evaluate(() => {
    loadIntoState({ name: "Orders by region", cells: [{ type: "code", cell_id: "c1", source: "orders = spark.table('MOCK_orders')", meta: {} }] });
    state.notebook.cells[0].source = "orders = spark.table('MOCK_orders')  # edited, not saved";
    state.dirty = true;
  });
  await page().evaluate((lh) => openSqlQuery(lh), LH);
  await page().waitForSelector(".sql-edit .cm-editor");
  await setDoc(ORDERS);
  const rail = await page().evaluate(() => railFeatures().map((f) => f.id));
  assert.deepStrictEqual(rail.slice(0, 5), ["home", "create", "workspaces", "onelake", "monitor"]);
  await pause(1200);
  await shot("1-fabric-project");
});

test("2. the SQL runs on this PC", { skip }, async () => {
  await page().getByRole("button", { name: "Run" }).click();
  await page().waitForFunction(() => document.querySelector(".sql-pill")?.textContent === "Succeeded"
    && !sqlPage.tabs[sqlPage.active].running, null, { timeout: 60000 });
  assert.match(await page().locator(".sql-band").textContent(), /4 rows · [\d.]+ s on this PC/);
  firstRun = await grid();
  assert.deepStrictEqual(firstRun.map((r) => r[0]), ["West", "North", "South", "East"]);
  await pause(1500);
  await shot("2-fabric-ran-locally");
});

test("3. switching to the Databricks skin keeps everything, and shows the same data", { skip }, async () => {
  const before = await kept();
  await chooseSkin("Databricks-familiar", "databricks");
  assert.deepStrictEqual(await kept(), before);
  await page().evaluate((lh) => openSqlQuery(lh), LH);
  await page().waitForSelector(".sql-grid tbody tr");
  assert.deepStrictEqual(await grid(), firstRun, "the same result, not run again");
  assert.match(await page().locator(".sql-band").textContent(), /on this PC/);
  await pause(1500);
  await shot("3-databricks-same-data");
});

test("4. in the Snowflake skin, the compatibility check finds what Fabric Warehouse doesn't support", { skip }, async () => {
  const before = await kept();
  await chooseSkin("Snowflake-familiar", "snowflake");
  assert.deepStrictEqual(await kept(), before);
  await page().evaluate((lh) => openSqlQuery(lh), LH);
  await page().waitForSelector(".sql-edit .cm-editor");
  await page().getByRole("button", { name: "New query", exact: true }).click();  // a tab of its own; the run stays in the first
  await setDoc(SNOWFLAKE_STYLE);
  await page().locator("select.sql-dialect").selectOption("warehouse");
  await page().waitForFunction((text) => {
    const t = sqlPage.tabs[sqlPage.active];
    return t.compatText === text && t.compat?.target?.id === "warehouse";
  }, SNOWFLAKE_STYLE, { timeout: 15000 });
  const panel = await page().locator(".sql-compat").textContent();
  assert.match(panel, /Fabric Warehouse \(T-SQL\)/);
  assert.match(panel, /LIMIT isn't T-SQL/);
  assert.match(panel, /Known problems/);
  await pause(2000);
  await shot("4-snowflake-compatibility");
});

test("5. back in KratosForge, the results and the optimization information are there", { skip }, async () => {
  const before = await kept();
  await chooseSkin("KratosForge", "kratosforge");
  assert.deepStrictEqual(await kept(), before);
  assert.strictEqual(await page().evaluate(() => document.documentElement.style.getPropertyValue("--accent")), "");
  await page().evaluate((lh) => openSqlQuery(lh), LH);
  await page().waitForSelector(".sql-edit .cm-editor");
  // The first tab still holds the run from step 2.
  await page().evaluate(() => { sqlPage.active = 0; render(); });
  await page().waitForSelector(".sql-grid tbody tr");
  assert.deepStrictEqual(await grid(), firstRun);
  await pause(1500);
  await shot("5a-kratosforge-results");
  await page().evaluate(() => go("optimize"));
  await page().getByRole("tab", { name: "Cost and savings" }).click();
  await page().waitForFunction(() => /Fabric use avoided on this PC/.test(document.getElementById("view")?.textContent || ""), null, { timeout: 15000 });
  await pause(2000);
  await shot("5b-kratosforge-cost-and-savings");
  // A skin change never reaches an engine, a credential or a profile: only the settings are saved.
  const sent = new Set(app.calls.map((c) => c.method));
  for (const m of ["sign_in", "engine_select", "engine_start_cell", "list_workspaces"]) assert.ok(!sent.has(m), m);
  assert.deepStrictEqual(app.errors, []);
});
