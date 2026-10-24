// WH-01: the Warehouse page, a SQL query tab whose target is a Fabric warehouse or SQL analytics
// endpoint, in a real browser (see harness.cjs for how to run it). The run calls are mocked: no
// warehouse, sign-in or Fabric is needed. The check and formatting use the real engine.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { playwright, openApp, sqlRunMocks } = require("./harness.cjs");

const WH = { kind: "warehouse", workspace_id: "ws1", workspace_name: "Sales Dev", item_id: "wh1", name: "WH_Gold" };
const SHOTS = process.env.SCREENSHOT_DIR;
const CATALOG_COLS = ["schema_name", "object_name", "column_name", "type_name", "max_length", "precision", "scale"].map((name) => ({ name, type: "varchar" }));

let app;
const skip = !playwright && "Playwright is not installed";
const page = () => app.page;
const starts = () => app.calls.filter((c) => c.method === "sql_start").map((c) => c.params);

before(async () => {
  if (skip) return;
  const runs = sqlRunMocks((p) => {
    if (p.warehouse_ref.item_id === "lh1" && p.query.includes("sys.objects")) throw new Error("Login failed for user");
    if (p.query.includes("sys.columns")) return { columns: CATALOG_COLS, rows: [["dbo", "Sales", "Amount", "decimal", 9, 18, 2],
      ["dbo", "Sales", "Customer Name", "nvarchar", 200, 0, 0], ["dbo", "vSales", "Amount", "decimal", 9, 18, 2]] };
    if (p.query.includes("sys.objects")) return { columns: CATALOG_COLS.slice(0, 3), rows: [["dbo", "Sales", "U"], ["dbo", "vSales", "V"],
      ["dbo", "usp_Load", "P"], ["dbo", "fn_Days", "IF"], ["staging", "Orders", "U"]] };
    return { columns: [{ name: "CustomerName", type: "varchar" }, { name: "Revenue", type: "decimal" }],
      rows: [["Contoso", 1250.5], ["Fabrikam", 980]] };
  });
  app = await openApp({
    engine: ["sql_format", "sql_dialects", "sql_analyze"],
    mocks: { ...runs, sql_start: (p) => ({ ...runs.sql_start(p), target: "warehouse" }) },
  });
  await page().evaluate((wh) => openWarehouseQuery(wh), WH);
  await page().waitForSelector(".warehouse-page .sql-edit .cm-editor");
  app.errors.length = 0;
});

after(async () => { await app?.close(); });

async function setDoc(text) {
  await page().evaluate((text) => {
    const v = sqlPage.editor.view;
    v.dispatch({ changes: { from: 0, to: v.state.doc.length, insert: text }, selection: { anchor: text.length } });
    v.focus();
  }, text);
}

test("the page says the query runs in Fabric and uses capacity, and offers no lakehouse actions", { skip }, async () => {
  const text = await page().locator(".warehouse-page").textContent();
  assert.match(text, /Runs in Fabric · uses capacity · reads only/);
  assert.match(text, /Fabric Warehouse \(T-SQL\)/);
  assert.match(text, /How this works/);
  assert.strictEqual(await page().getByRole("button", { name: "Save as table" }).count(), 0);
  assert.strictEqual(await page().locator(".warehouse-page select.sql-dialect").count(), 0, "a warehouse runs T-SQL only");
  assert.ok(!app.calls.some((c) => c.method === "list_tables"), "a warehouse isn't listed as a lakehouse");
  assert.match(await page().locator("#breadcrumb").textContent(), /Sales Dev\/WH_Gold\/SQL query/);
});

test("the explorer waits for List objects until runs on the warehouse are confirmed", { skip }, async () => {
  assert.match(await page().locator(".wh-explorer").textContent(), /List objects/);
  assert.deepStrictEqual(starts(), []);
});

test("the first run asks first; Cancel sends nothing", { skip }, async () => {
  await setDoc("SELECT TOP 20 CustomerName, Revenue FROM dbo.Sales");
  await page().getByRole("button", { name: "Run", exact: true }).click();
  await page().waitForSelector(".dialog-actions");
  assert.match(await page().locator(".dialog").textContent(), /Run this query in Fabric on WH_Gold\?[\s\S]*uses capacity/);
  await page().getByRole("button", { name: "Cancel" }).click();
  assert.deepStrictEqual(starts(), []);
});

test("a confirmed run goes to the warehouse with T-SQL and shows its rows", { skip }, async () => {
  await page().getByRole("button", { name: "Run", exact: true }).click();
  await page().getByRole("button", { name: "Run in Fabric" }).click();
  await page().waitForFunction(() => document.querySelector(".sql-pill")?.textContent === "Succeeded");
  assert.deepStrictEqual(starts().filter((p) => !p.query.includes("sys.")).at(-1), { query: "SELECT TOP 20 CustomerName, Revenue FROM dbo.Sales", target: "warehouse",
    dialect: "tsql", warehouse_ref: { workspace_id: "ws1", item_id: "wh1", kind: "warehouse" } });
  assert.match(await page().locator(".sql-band").textContent(), /2 rows · [\d.]+ s in Fabric/);
  assert.deepStrictEqual(await page().locator(".sql-grid th").allTextContents(), ["", "CustomerName", "Revenue"]);
  if (SHOTS) await page().waitForTimeout(1000).then(() => page().screenshot({ path: path.join(SHOTS, "wh-01-warehouse-page.png") }));
});

test("a second run on the same warehouse doesn't ask again", { skip }, async () => {
  await page().waitForFunction(() => sqlPage.catalog?.state === "done");
  const before = starts().length;
  await page().getByRole("button", { name: "Run", exact: true }).click();
  await page().waitForFunction((n) => !sqlPage.tabs[sqlPage.active].running && sqlPage.tabs[sqlPage.active].exec?.id === `run${n + 1}`, before);
  assert.strictEqual(await page().locator(".dialog-actions").count(), 0);
  assert.strictEqual(starts().length, before + 1);
  assert.deepStrictEqual(app.errors, []);
});

test("after the first confirmed run the explorer lists the catalog by schema and kind", { skip }, async () => {
  const sys = starts().filter((p) => p.query.includes("sys."));
  assert.strictEqual(sys.length, 2, "objects, then columns");
  assert.ok(sys.every((p) => p.target === "warehouse" && p.dialect === "tsql"));
  const text = await page().locator(".wh-explorer").textContent();
  assert.match(text, /dbo.*Tables \(1\).*Sales.*Views \(1\).*Stored procedures \(1\).*Functions \(1\).*staging/);
  assert.match(text, /Listed from the warehouse catalog at/);
  const tables = await page().evaluate(() => warehouseSqlProvider(sqlPage.ref).tables());
  assert.deepStrictEqual(tables, [{ schema: "dbo", name: "Sales" }, { schema: "dbo", name: "vSales" }, { schema: "staging", name: "Orders" }]);
});

test("a table's columns show their T-SQL types, and a click inserts a quoted name", { skip }, async () => {
  await page().getByRole("button", { name: "Show columns of Sales" }).click();
  const text = await page().locator(".wh-explorer").textContent();
  assert.match(text, /Amountdecimal\(18,2\)/);
  assert.match(text, /Customer Namenvarchar\(100\)/);
  await page().getByRole("button", { name: "Customer Name" }).click();
  assert.match(await page().evaluate(() => sqlPage.editor.text()), /\[Customer Name\]/);
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "wh-02-explorer.png") });
});

test("the filter narrows the tree, and clicking a table queries its top 100 rows", { skip }, async () => {
  await page().fill(".wh-filter", "ord");
  const text = await page().locator(".wh-explorer").textContent();
  assert.match(text, /staging.*Orders/);
  assert.doesNotMatch(text, /Sales/);
  const before = starts().length;
  await page().getByRole("button", { name: "Orders", exact: true }).click();
  await page().waitForFunction((n) => sqlPage.tabs[sqlPage.active].exec?.id === `run${n + 1}` && !sqlPage.tabs[sqlPage.active].running, before);
  assert.strictEqual(starts().at(-1).query, "SELECT TOP (100) *\nFROM [staging].[Orders];");
  assert.strictEqual(await page().locator(".dialog-actions").count(), 0, "already confirmed for this warehouse");
  await page().fill(".wh-filter", "");
});

test("Workspaces offers the page on a warehouse and on an endpoint whose lakehouse is listed", { skip }, async () => {
  const refs = await page().evaluate(() => {
    const ws = { id: "ws1", displayName: "Sales Dev" };
    const items = [{ id: "wh1", type: "Warehouse", displayName: "WH_Gold" }, { id: "lh1", type: "Lakehouse", displayName: "LH_Silver" },
      { id: "ep1", type: "SQLEndpoint", displayName: "LH_Silver" }, { id: "ep2", type: "SQLEndpoint", displayName: "Orphan" },
      { id: "nb1", type: "Notebook", displayName: "Load" }];
    return items.map((it) => warehouseRefFor(ws, it, items));
  });
  assert.deepStrictEqual(refs, [
    { workspace_id: "ws1", workspace_name: "Sales Dev", name: "WH_Gold", kind: "warehouse", item_id: "wh1" },
    null,
    { workspace_id: "ws1", workspace_name: "Sales Dev", name: "LH_Silver", kind: "endpoint", item_id: "lh1" },
    null,
    null]);
});

test("an endpoint tab keeps its own saved tabs and checks against the endpoint", { skip }, async () => {
  await page().evaluate(() => openWarehouseQuery({ kind: "endpoint", workspace_id: "ws1", workspace_name: "Sales Dev", item_id: "lh1", name: "LH_Silver" }));
  await page().waitForSelector(".warehouse-page .sql-edit .cm-editor");
  assert.match(await page().locator(".warehouse-page").textContent(), /Fabric SQL analytics endpoint \(T-SQL\)/);
  assert.strictEqual(await page().evaluate(() => sqlPage.tabs[0].dialect), "endpoint");
  assert.match(await page().evaluate(() => sqlPage.tabs[0].sql), /runs in Fabric/);
  assert.strictEqual(await page().evaluate(() => sqlPage.catalog), null, "a new target starts with no catalog");
});

test("a catalog that can't be read says why and offers Try again", { skip }, async () => {
  await page().getByRole("button", { name: "List objects" }).click();
  await page().getByRole("button", { name: "Run in Fabric" }).click();
  await page().waitForFunction(() => sqlPage.catalog?.state === "failed");
  assert.match(await page().locator(".wh-explorer").textContent(), /Could not list the objects: Login failed for user/);
  assert.ok(await page().getByRole("button", { name: "Try again" }).isVisible());
  assert.deepStrictEqual(app.errors, []);
});
