// S37 (Preview): saved queries on a lakehouse's SQL page, in a real browser (see harness.cjs for how to
// run it). The saved-query calls go to the real engine, in a data folder of its own; nothing runs a query.
// The Queries tab lists only the queries saved on this lakehouse, and the Warehouse page leaves them out.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp, sqlRunMocks, axeFindings, axeSource } = require("./harness.cjs");

const LH = { workspace_id: "ws1", lakehouse_id: "lh1", name: "Sales" };
const WH = { kind: "warehouse", workspace_id: "ws1", workspace_name: "Sales Dev", item_id: "wh1", name: "WH_Gold" };

let app;
const skip = !playwright && "Playwright is not installed";
const page = () => app.page;
const aside = () => page().locator("aside.explorer");

before(async () => {
  if (skip) return;
  app = await openApp({
    engine: ["sql_format", "sql_analyze", "sql_queries_list", "sql_query_save", "sql_query_delete"],
    mocks: { ...sqlRunMocks(() => ({ columns: [{ name: "x", type: "int" }], rows: [[1]] })),
      list_tables: () => ({ dbo: ["sales"] }), table_columns: () => [] },
  });
  // A warehouse's query and another lakehouse's, saved before the page opens.
  await page().evaluate(async () => {
    await rpc("sql_query_save", { place: "mine", title: "Warehouse revenue", sql: "SELECT TOP (5) * FROM dbo.FactSales", warehouse: "WH_Gold" });
    await rpc("sql_query_save", { place: "mine", title: "Other lakehouse", sql: "SELECT 1", lakehouse: "Finance", lakehouse_id: "lh2" });
  });
  await page().evaluate((lh) => openSqlQuery(lh), LH);
  await page().waitForSelector(".sql-page .sql-edit .cm-editor");
  app.errors.length = 0;
});

after(async () => { await app?.close(); });

const setDoc = (text) => page().evaluate((text) => {
  const v = sqlPage.editor.view;
  v.dispatch({ changes: { from: 0, to: v.state.doc.length, insert: text }, selection: { anchor: text.length } });
}, text);

test("the lakehouse page's Queries tab lists none of the warehouse's or another lakehouse's queries", { skip }, async () => {
  await aside().getByRole("tab", { name: "Queries" }).click();
  await aside().getByText("No saved queries yet").waitFor();
  assert.doesNotMatch(await aside().textContent(), /Warehouse revenue|Other lakehouse/);
});

// axe covers dialogs: sqlqueries.js:saveSqlQuery
test("Save query on the lakehouse keeps the lakehouse as the query's place", { skip }, async () => {
  await setDoc("SELECT customer, SUM(amount) AS total FROM sales GROUP BY customer");
  await page().getByRole("button", { name: "Save query" }).click();
  const dialog = page().getByRole("dialog", { name: "Save query" });
  await dialog.getByLabel("Name").fill("Top customers");
  if (axeSource) assert.deepStrictEqual(await axeFindings(page(), "#dialog-root"), []);
  await dialog.getByRole("button", { name: "Save" }).click();
  await aside().locator(".wh-q-row", { hasText: "Top customers" }).waitFor();
  const call = app.calls.filter((c) => c.method === "sql_query_save").at(-1);
  assert.deepStrictEqual([call.params.lakehouse, call.params.lakehouse_id, call.params.warehouse], ["Sales", "lh1", ""]);
  const q = (await page().evaluate(() => rpc("sql_queries_list"))).queries.find((x) => x.title === "Top customers");
  assert.deepStrictEqual([q.lakehouse, q.lakehouse_id, q.warehouse], ["Sales", "lh1", ""]);
  assert.strictEqual(await page().evaluate(() => sqlPage.tabs[sqlPage.active].file.warehouse), "");
  assert.doesNotMatch(await aside().textContent(), /Warehouse revenue|Other lakehouse/);
  assert.ok(!app.calls.some((c) => c.method === "sql_start"), "saving runs nothing");
});

test("Save writes back to the file, Save as new keeps a copy, and both stay on this lakehouse", { skip }, async () => {
  await setDoc("SELECT customer, SUM(amount) AS total FROM sales GROUP BY customer ORDER BY total DESC LIMIT 10");
  await page().getByRole("button", { name: "Save query" }).click();
  await page().waitForFunction(() => /Saved Top customers to My queries/.test(document.querySelector(".toast")?.textContent || ""));
  await page().getByRole("button", { name: "Save as new" }).click();
  const dialog = page().getByRole("dialog", { name: "Save query" });
  await dialog.getByLabel("Name").fill("Top 10 customers");
  await dialog.getByRole("button", { name: "Save" }).click();
  await aside().locator(".wh-q-row", { hasText: "Top 10 customers" }).waitFor();
  const mine = (await page().evaluate(() => rpc("sql_queries_list"))).queries.filter((x) => x.lakehouse_id === "lh1");
  assert.deepStrictEqual(mine.map((x) => x.title).sort(), ["Top 10 customers", "Top customers"]);
  assert.ok(mine.every((x) => /LIMIT 10/.test(x.sql)));
});

test("a saved query opens in its own tab, and the Warehouse page doesn't list the lakehouse's queries", { skip }, async () => {
  await page().evaluate(() => { sqlPage.active = 0; renderQuery(); });
  await aside().locator(".wh-q-row", { hasText: "Top 10 customers" }).getByRole("button", { name: "Top 10 customers", exact: true }).click();
  assert.strictEqual(await page().evaluate(() => sqlPage.tabs[sqlPage.active].name), "Top 10 customers");
  await page().evaluate((wh) => openWarehouseQuery(wh), WH);
  await page().waitForSelector(".warehouse-page .sql-edit .cm-editor");
  await page().locator(".warehouse-page aside.explorer").getByRole("tab", { name: "Queries" }).click();
  const whAside = page().locator(".warehouse-page aside.explorer");
  await whAside.locator(".wh-q-row", { hasText: "Warehouse revenue" }).waitFor();
  assert.doesNotMatch(await whAside.textContent(), /Top customers|Top 10 customers|Other lakehouse/);
});

test("with Preview features hidden, the lakehouse page has no Queries tab and no Save query", { skip }, async () => {
  await page().evaluate((lh) => { state.settings = { ...(state.settings || {}), show_preview: { value: false } }; openSqlQuery(lh); renderQuery(); }, LH);
  await page().waitForSelector(".sql-page:not(.warehouse-page) .sql-edit .cm-editor");
  assert.strictEqual(await aside().getByRole("tab", { name: "Queries" }).count(), 0);
  assert.strictEqual(await page().getByRole("button", { name: "Save query" }).count(), 0);
  await page().evaluate(() => { state.settings = { ...(state.settings || {}), show_preview: { value: true } }; });
  assert.deepStrictEqual(app.errors, []);
});
