// S34 Compare data on the lakehouse and warehouse SQL pages (sqlcompare.js), from the approved mockup: Explorer >
// Compare data and a table's menu > Compare with. A and B each run once as reads (sql_start); a new key, tolerance,
// tile or page compares the same two results again (sql_compare) without running anything. On a warehouse they
// run in Fabric after one confirm.
// axe covers dialogs: sqlcompare.js:sqlCompareDialog
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp, sqlRunMocks, axeSource, axeFindings } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
let app;
const page = () => app.page;
const LH = { workspace_id: "ws1", lakehouse_id: "lh1", name: "Sales" };
const COMMON = [{ name: "order_id", kind: "integer", number: true }, { name: "amount", kind: "number", number: true },
  { name: "status", kind: "string", number: false }];

let answer = null;  // what sql_compare answers next, from its params
let exported = null;

before(async () => {
  if (skip) return;
  const runs = sqlRunMocks(() => ({ columns: [{ name: "order_id", type: "int64" }], rows: [[1]] }));
  app = await openApp({
    engine: ["sql_format", "sql_analyze", "sql_queries_list"],
    mocks: { ...runs,
      list_tables: () => ({ dbo: ["orders", "orders_rebuilt"] }),
      table_columns: () => [{ name: "order_id", type: "bigint" }, { name: "amount", type: "double" }, { name: "status", type: "string" }],
      sql_compare: (p) => answer(p),
      sql_compare_export: (p) => { exported = p; return { path: "C:\\Users\\x\\Downloads\\Sales compare.csv", name: "Sales compare.csv", rows: 6, notes: [] }; } },
  });
});
after(async () => { await app?.close(); });

const whole = (p) => ({ result: "differs", level: "complete", method: "whole rows", keys: [], seconds: 0.4, rows: { a: 5, b: 5 },
  common: COMMON, columns: { only_a: [], only_b: [], types: [] }, limitations: [], status: p.status, offset: p.offset,
  counts: { same: 1, only_a: 4, only_b: 4, changed: null }, listed: 8,
  examples: [{ status: "only_a", row: { order_id: 2, amount: 20, status: "shipped" }, times: 1 }] });
const keyed = (p) => ({ result: "differs", level: "complete", method: "keyed", keys: ["order_id"], seconds: 3.4, rows: { a: 48210, b: 48207 },
  common: COMMON, columns: { only_a: [], only_b: [], types: [] }, limitations: [], status: p.status, offset: p.offset,
  counts: { same: 48190, only_a: 5, only_b: 2, changed: 15 }, listed: p.status === "changed" ? 15 : 22, changed_columns: ["amount", "status"],
  examples: [
    { status: "changed", key: { order_id: 100482 }, columns: { amount: { a: 1204.5, b: 1240.5 } }, same: { status: { a: "shipped", b: "shipped" } } },
    { status: "changed", key: { order_id: 100733 }, columns: { amount: { a: 310.25, b: null } }, same: { status: { a: "shipped", b: "shipped" } } },
    ...(p.status === "changed" ? [] : [{ status: "only_b", key: { order_id: 100900 } }])] });

async function openPage() {
  await page().evaluate((lh) => openSqlQuery(lh), LH);
  await page().waitForSelector(".sql-edit .cm-editor");
  await page().getByRole("button", { name: "Compare data" }).waitFor();
}
const dialog = () => page().getByRole("dialog", { name: "Compare data" });
const starts = () => app.calls.filter((c) => c.method === "sql_start").map((c) => c.params);
const compares = () => app.calls.filter((c) => c.method === "sql_compare").map((c) => c.params);

test("Compare data runs A and B once as reads, then a key, tile and tolerance compare the same results again", { skip }, async () => {
  await openPage();
  await page().getByRole("button", { name: "Compare data" }).click();
  const d = dialog();
  await d.getByText("Pick A and B, then Compare. Both only read, so nothing is changed.").waitFor();
  assert.strictEqual(await d.getByLabel("A: table").inputValue(), "orders");
  assert.strictEqual(await d.getByLabel("B: table").inputValue(), "orders_rebuilt");

  answer = whole;
  const before = starts().length;
  await page().locator(".dialog-actions").getByRole("button", { name: "Compare" }).click();
  await d.locator(".sql-compare-verdict").getByText("Differs").waitFor();
  const ran = starts().slice(before);
  assert.deepStrictEqual(ran.map((p) => [p.query, p.dialect, p.allow]), [["SELECT * FROM orders", "spark", undefined],
    ["SELECT * FROM orders_rebuilt", "spark", undefined]], "both sides run as reads, with no allow");
  assert.match(await d.locator(".sql-compare-verdict").innerText(), /5 rows in A, 5 in B\. Compared on this PC in 0\.4 s\. Read only: nothing was changed\./);
  assert.deepStrictEqual(await d.locator(".sql-compare-tile span").allInnerTexts(), ["Same", "Only in A", "Only in B"], "no key: no Changed tile");
  await d.getByText("Showing 1–1 of 8 rows that differ.").waitFor();

  answer = keyed;
  await d.getByLabel("Add a key column").selectOption("order_id");
  await d.getByRole("button", { name: "Stop matching on order_id" }).waitFor();
  assert.strictEqual(starts().length, before + 2, "a new key runs nothing");
  assert.deepStrictEqual(compares().at(-1).keys, ["order_id"]);
  assert.deepStrictEqual(await d.locator(".sql-compare-tile b").allInnerTexts(), ["48,190", "5", "2", "15"]);
  const table = d.getByRole("table", { name: "Rows that differ, A beside B" });
  assert.deepStrictEqual(await table.locator("thead tr").nth(1).locator("th").allInnerTexts(), ["order_id", "Where", "amount", "amount"],
    "only the columns that differ");
  assert.deepStrictEqual(await table.locator("td.chg").allInnerTexts(), ["1,204.5", "1,240.5", "310.25", "NULL"]);
  await d.getByRole("button", { name: "Show all columns" }).click();
  assert.deepStrictEqual(await table.locator("thead tr").nth(1).locator("th").allInnerTexts(), ["order_id", "Where", "amount", "status", "amount", "status"]);

  await d.getByRole("button", { name: /15\s*Changed/ }).click();
  await d.getByText("Showing 1–2 of 15 changed rows.").waitFor();
  assert.strictEqual(compares().at(-1).status, "changed");
  assert.strictEqual(await d.getByRole("button", { name: /15\s*Changed/ }).getAttribute("aria-pressed"), "true");
  const asked = compares().length;
  await d.getByRole("button", { name: "Next" }).click();
  for (const end = Date.now() + 5000; compares().length === asked && Date.now() < end;) await new Promise((r) => setTimeout(r, 25));
  assert.strictEqual(compares().at(-1).offset, 50);

  await d.getByLabel("Number column").selectOption("amount");
  await d.getByLabel("Within").fill("0.01");
  await d.getByRole("button", { name: "Add", exact: true }).click();
  await d.getByRole("button", { name: "Remove the tolerance on amount" }).waitFor();
  assert.deepStrictEqual(compares().at(-1).tolerance, { amount: 0.01 });
  assert.strictEqual(starts().length, before + 2, "a tolerance runs nothing either");

  if (axeSource) assert.deepStrictEqual(await axeFindings(page(), "#dialog-root"), []);  // CI installs axe-core

  await d.getByRole("button", { name: "Export" }).click();
  await page().getByText("Saved 6 rows that differ to Sales compare.csv in Downloads.").waitFor();
  assert.deepStrictEqual([exported.keys, exported.tolerance, exported.name], [["order_id"], { amount: 0.01 }, "Sales"]);

  // Changing B runs both again on the next Compare.
  await d.getByLabel("B is a").selectOption("query");
  await d.getByLabel("B: query").fill("SELECT * FROM orders WHERE amount > 0;");
  await page().locator(".dialog-actions").getByRole("button", { name: "Compare" }).click();
  await d.locator(".sql-compare-verdict").waitFor();
  assert.deepStrictEqual(starts().slice(before + 2).map((p) => p.query), ["SELECT * FROM orders", "SELECT * FROM orders WHERE amount > 0"]);
  await page().locator(".dialog-actions").getByRole("button", { name: "Close" }).click();
});

test("a table's menu has Compare with, and a repeated key stops the compare and says so", { skip }, async () => {
  await openPage();
  await page().locator(".sql-table-row").getByRole("button", { name: "orders_rebuilt", exact: true }).click({ button: "right" });
  await page().getByRole("menuitem", { name: "Compare with…" }).click();
  const d = dialog();
  assert.strictEqual(await d.getByLabel("A: table").inputValue(), "orders_rebuilt");
  assert.strictEqual(await d.getByLabel("B: table").inputValue(), "orders");

  answer = (p) => ({ stopped: "The key (order_id) is repeated in A, so there's no single row to compare: 1 repeated in A, 0 in B. Add a column to the key, or compare without a key.",
    rows: { a: 6, b: 5 }, common: COMMON });
  await d.getByLabel("Add a key column").selectOption("order_id");  // from the table's columns, before any run
  await page().locator(".dialog-actions").getByRole("button", { name: "Compare" }).click();
  await d.getByRole("alert").getByText(/The key \(order_id\) is repeated in A/).waitFor();
  assert.strictEqual(await d.locator(".sql-compare-tile").count(), 0);
  await page().keyboard.press("Escape");
  await dialog().waitFor({ state: "detached" });
});

test("a side left empty is refused before anything runs", { skip }, async () => {
  await openPage();
  await page().getByRole("button", { name: "Compare data" }).click();
  const d = dialog();
  await d.getByLabel("A is a").selectOption("query");
  const n = compares().length;
  await page().locator(".dialog-actions").getByRole("button", { name: "Compare" }).click();
  await d.getByRole("alert").getByText("Pick a table, or a query that only reads, for A.").waitFor();
  assert.strictEqual(compares().length, n);
  await page().locator(".dialog-actions").getByRole("button", { name: "Close" }).click();
});

// The warehouse page: A and B run in Fabric as reads (target warehouse, T-SQL, no allow), after one confirm.
// A new key, a tile or a second Compare of the same A and B asks nothing and runs nothing.
const WH = { kind: "warehouse", workspace_id: "ws1", workspace_name: "Sales Dev", item_id: "wh1", name: "WH_Gold" };
async function openWarehouse() {
  await page().evaluate((wh) => openWarehouseQuery(wh), WH);
  await page().waitForSelector(".warehouse-page .sql-edit .cm-editor");
  await page().evaluate((wh) => {
    const cols = [{ name: "order_id", type: "bigint" }, { name: "amount", type: "decimal(18,2)" }, { name: "status", type: "varchar(20)" }];
    // The explorer's catalog, as if listed, with the primary key the Diagram read.
    sqlPage.catalog = { key: sqlRefKey(wh), state: "done", at: new Date(), error: null,
      objects: [{ schema: "dbo", name: "orders", type: "U" }, { schema: "dbo", name: "orders_rebuilt", type: "U" }, { schema: "dbo", name: "vOrders", type: "V" }],
      cols: { "dbo.orders": cols, "dbo.orders_rebuilt": cols, "dbo.vOrders": cols }, pks: { "dbo.orders": ["order_id"] } };
    sqlPage.whRedraw?.();
    // Count the confirms Compare shows.
    window.compareConfirms = [];
    if (!window.realConfirmDialog) window.realConfirmDialog = confirmDialog;
    window.confirmDialog = (title, text, ok, opts) => { window.compareConfirms.push({ title, text, ok }); return window.realConfirmDialog(title, text, ok, opts); };
  }, WH);
  await page().locator(".wh-explorer").getByRole("button", { name: "Compare data" }).waitFor();
}
const confirms = () => page().evaluate(() => window.compareConfirms);

test("on a warehouse, Compare data asks once, runs A and B in Fabric as reads, and a re-compare runs nothing", { skip }, async () => {
  await openWarehouse();
  await page().locator(".wh-explorer").getByRole("button", { name: "Compare data" }).click();
  const d = dialog();
  assert.strictEqual(await d.getByLabel("A: table").inputValue(), "[dbo].[orders]");
  assert.strictEqual(await d.getByLabel("B: table").inputValue(), "[dbo].[orders_rebuilt]");
  await d.getByRole("button", { name: "Stop matching on order_id" }).waitFor();  // the primary key the catalog knows

  answer = keyed;
  const before = starts().length;
  await page().locator(".dialog-actions").getByRole("button", { name: "Compare" }).click();
  const ask = page().getByRole("dialog", { name: "Run A and B in Fabric on WH_Gold?" });
  await ask.getByText("Compare runs A, then B, on the warehouse. Both only read, and both use capacity. Nothing is changed.").waitFor();
  if (axeSource) assert.deepStrictEqual(await axeFindings(page(), "#dialog-root"), []);
  assert.strictEqual(starts().length, before, "nothing runs before the confirm");
  await ask.getByRole("button", { name: "Run both" }).click();
  await d.locator(".sql-compare-verdict").getByText("Differs").waitFor();
  const ran = starts().slice(before);
  assert.deepStrictEqual(ran.map((p) => [p.query, p.target, p.dialect, p.allow, p.lakehouse_ref]), [
    ["SELECT * FROM [dbo].[orders]", "warehouse", "tsql", undefined, undefined],
    ["SELECT * FROM [dbo].[orders_rebuilt]", "warehouse", "tsql", undefined, undefined]], "both run in Fabric as reads, with no allow");
  assert.deepStrictEqual(ran[0].warehouse_ref, { workspace_id: "ws1", item_id: "wh1", kind: "warehouse" });
  assert.deepStrictEqual(compares().at(-1).keys, ["order_id"]);
  if (axeSource) assert.deepStrictEqual(await axeFindings(page(), "#dialog-root"), []);  // CI installs axe-core

  // A tile, a key and Compare again on the same A and B: no confirm, nothing runs.
  await d.getByRole("button", { name: /15\s*Changed/ }).click();
  await d.getByText("Showing 1–2 of 15 changed rows.").waitFor();
  await d.getByRole("button", { name: "Stop matching on order_id" }).click();
  await d.getByLabel("Add a key column").waitFor();
  const asked = compares().length;
  await page().locator(".dialog-actions").getByRole("button", { name: "Compare" }).click();
  for (const end = Date.now() + 5000; compares().length === asked && Date.now() < end;) await new Promise((r) => setTimeout(r, 25));
  assert.strictEqual(compares().length, asked + 1, "Compare again compares the same results");
  assert.strictEqual(starts().length, before + 2, "a new tile, key or Compare of the same A and B runs nothing");
  assert.strictEqual((await confirms()).length, 1, "the confirm shows once");

  // B changes: the confirm shows again, and Cancel brings the dialog back with nothing run.
  await d.getByLabel("B: table").selectOption("[dbo].[vOrders]");
  await page().locator(".dialog-actions").getByRole("button", { name: "Compare" }).click();
  await ask.getByRole("button", { name: "Cancel" }).click();
  await d.getByLabel("B: table").waitFor();
  assert.strictEqual(await d.getByLabel("B: table").inputValue(), "[dbo].[vOrders]");
  assert.strictEqual(starts().length, before + 2, "Cancel runs nothing");
  assert.strictEqual((await confirms()).length, 2);
  await page().locator(".dialog-actions").getByRole("button", { name: "Close" }).click();
  await dialog().waitFor({ state: "detached" });
});

test("on a warehouse, a table's menu has Compare with, and a table with no known key starts with none", { skip }, async () => {
  await openWarehouse();
  await page().locator(".wh-tree").getByRole("treeitem", { name: "orders_rebuilt", exact: true }).click({ button: "right" });
  await page().getByRole("menuitem", { name: "Compare with…" }).click();
  const d = dialog();
  assert.strictEqual(await d.getByLabel("A: table").inputValue(), "[dbo].[orders_rebuilt]");
  assert.strictEqual(await d.getByLabel("B: table").inputValue(), "[dbo].[orders]");
  assert.strictEqual(await d.getByRole("button", { name: /^Stop matching on/ }).count(), 0, "no key is guessed");
  await page().keyboard.press("Escape");
  await dialog().waitFor({ state: "detached" });
});
