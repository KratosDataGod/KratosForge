// WH-06: saved and shared queries on the Warehouse page, in a real browser (see harness.cjs for how
// to run it). The saved-query calls go to the real engine, in a data folder of its own; runs are
// mocked, and nothing here runs a query.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { playwright, openApp, sqlRunMocks } = require("./harness.cjs");

const WH = { kind: "warehouse", workspace_id: "ws1", workspace_name: "Sales Dev", item_id: "wh1", name: "WH_Gold" };
const SHOTS = process.env.SCREENSHOT_DIR;

let app;
const skip = !playwright && "Playwright is not installed";
const page = () => app.page;

before(async () => {
  if (skip) return;
  app = await openApp({
    engine: ["sql_format", "sql_analyze", "sql_queries_list", "sql_query_save", "sql_query_delete"],
    mocks: { ...sqlRunMocks(() => ({ columns: [{ name: "x", type: "int" }], rows: [[1]] })) },
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
  }, text);
}
const saved = () => page().evaluate(() => rpc("sql_queries_list"));
const aside = () => page().locator(".warehouse-page aside.explorer");

test("the Queries tab starts empty and says where shared queries live", { skip }, async () => {
  await aside().getByRole("tab", { name: "Queries" }).click();
  await aside().getByText("No saved queries yet").waitFor();
  assert.match(await aside().textContent(), /My queries · on this PC.*Git repository's sql-queries folder/s);
});

test("Save query names the tab's SQL and keeps it in My queries", { skip }, async () => {
  await setDoc("SELECT Region, SUM(Amount) AS Revenue FROM dbo.FactSales GROUP BY Region");
  await page().getByRole("button", { name: "Save query" }).click();
  const dialog = page().getByRole("dialog", { name: "Save query" });
  await dialog.getByLabel("Name").fill("Revenue by region");
  await dialog.getByLabel("Description").fill("Monthly review");
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "wh-06-save-query.png") });
  await dialog.getByRole("button", { name: "Save" }).click();
  await aside().locator(".wh-q-row", { hasText: "Revenue by region" }).waitFor();
  const tab = await page().evaluate(() => sqlPage.tabs[sqlPage.active]);
  assert.strictEqual(tab.name, "Revenue by region");
  const { version, ...file } = tab.file;
  assert.deepStrictEqual(file, { place: "mine", name: "Revenue by region.sql", place_label: "My queries", title: "Revenue by region",
    description: "Monthly review", warehouse: "WH_Gold" });
  assert.match(version, /^[0-9a-f]{16}$/);
  const q = (await saved()).queries[0];
  assert.deepStrictEqual([q.title, q.warehouse, q.description, q.sql], ["Revenue by region", "WH_Gold", "Monthly review",
    "SELECT Region, SUM(Amount) AS Revenue FROM dbo.FactSales GROUP BY Region"]);
  const stored = await page().evaluate(() => JSON.parse(localStorage.getItem(queryTabsKey(sqlPage.ref))));
  assert.ok(stored.some((t) => t.file?.name === "Revenue by region.sql"), "the tab remembers its file");
  assert.ok(!app.calls.some((c) => c.method === "sql_start"), "saving runs nothing");
});

test("Save on a tab opened from a file writes back to it; Save as new makes another", { skip }, async () => {
  await setDoc("SELECT Region, SUM(Amount) AS Revenue FROM dbo.FactSales WHERE Year = 2026 GROUP BY Region");
  await page().getByRole("button", { name: "Save query" }).click();
  await page().waitForFunction(() => /Saved Revenue by region to My queries/.test(document.querySelector(".toast")?.textContent || ""));
  assert.strictEqual(await page().getByRole("dialog", { name: "Save query" }).count(), 0, "no dialog for a tab with a file");
  assert.match((await saved()).queries[0].sql, /Year = 2026/);
  await page().getByRole("button", { name: "Save as new" }).click();
  const dialog = page().getByRole("dialog", { name: "Save query" });
  await dialog.getByLabel("Name").fill("Revenue 2026");
  await dialog.getByRole("button", { name: "Save" }).click();
  await aside().locator(".wh-q-row", { hasText: "Revenue 2026" }).waitFor();
  assert.deepStrictEqual((await saved()).queries.map((q) => q.title).sort(), ["Revenue 2026", "Revenue by region"]);
  assert.strictEqual(await page().evaluate(() => sqlPage.tabs[sqlPage.active].file.name), "Revenue 2026.sql");
});

test("a saved query opens in the tab that holds it, or in a new tab", { skip }, async () => {
  const before = await page().evaluate(() => sqlPage.tabs.length);
  await aside().locator(".wh-q-row", { hasText: "Revenue 2026" }).locator(".tree-row").first().click();
  assert.strictEqual(await page().evaluate(() => sqlPage.tabs.length), before, "already open: no new tab");
  await page().evaluate(() => { sqlPage.tabs = sqlPage.tabs.filter((t) => t.file?.name !== "Revenue by region.sql"); saveQueryTabs(); renderQuery(); });
  await aside().locator(".wh-q-row", { hasText: "Revenue by region" }).locator(".tree-row").first().click();
  await page().waitForFunction(() => sqlPage.tabs[sqlPage.active].name === "Revenue by region");
  assert.match(await page().evaluate(() => sqlPage.editor.text()), /Year = 2026/);
  if (SHOTS) { await page().waitForTimeout(800); await page().screenshot({ path: path.join(SHOTS, "wh-06-queries.png") }); }
});

test("the filter keeps the focus while a person types and the list redraws", { skip }, async () => {
  const filter = aside().getByLabel("Filter queries");
  await filter.click();
  await page().keyboard.type("by reg");
  await page().evaluate(() => sqlPage.whRedraw());
  await page().keyboard.type("ion");
  assert.strictEqual(await filter.inputValue(), "by region");
  assert.ok(await filter.evaluate((el) => el === document.activeElement), "the filter keeps the focus");
  assert.deepStrictEqual(await aside().locator(".wh-q-row .label").allTextContents(), ["Revenue by region"]);
  await filter.fill("");
  await aside().locator(".wh-q-row", { hasText: "Revenue 2026" }).waitFor();
});

test("delete asks first and removes the file; the open tab keeps its SQL", { skip }, async () => {
  await aside().getByRole("button", { name: "Delete Revenue 2026" }).click();
  const confirm = page().getByRole("dialog", { name: "Delete saved query" });
  await confirm.getByRole("button", { name: "Delete" }).click();
  await page().waitForFunction(() => !document.querySelector(".wh-queries")?.textContent.includes("Revenue 2026"));
  assert.deepStrictEqual((await saved()).queries.map((q) => q.title), ["Revenue by region"]);
  assert.ok(await page().evaluate(() => sqlPage.tabs.some((t) => t.name === "Revenue 2026")));
});

test("Save keeps the query's own warehouse and never overwrites a file changed since it was opened", { skip }, async () => {
  await page().evaluate(() => rpc("sql_query_save", { place: "mine", title: "From prod", sql: "-- Warehouse: WH_Gold (prod copy)\nSELECT 1", warehouse: "WH_Prod" }));
  await page().evaluate(async () => { await sqlLoadQueries(); sqlPage.whRedraw(); });
  await aside().locator(".wh-q-row", { hasText: "From prod" }).locator("button:not(.wh-more)").click();
  assert.strictEqual(await page().evaluate(() => sqlPage.tabs[sqlPage.active].sql), "-- Warehouse: WH_Gold (prod copy)\nSELECT 1", "the SQL's own comments stay");
  await setDoc("-- Warehouse: WH_Gold (prod copy)\nSELECT 2");
  await page().getByRole("button", { name: "Save query" }).click();
  await page().waitForFunction(() => /Saved From prod/.test(document.querySelector(".toast")?.textContent || ""));
  const q = (await saved()).queries.find((q) => q.title === "From prod");
  assert.deepStrictEqual([q.warehouse, q.sql], ["WH_Prod", "-- Warehouse: WH_Gold (prod copy)\nSELECT 2"]);
  // Someone else changes the file (a Git pull): Save leaves it alone and says why.
  await page().evaluate(() => rpc("sql_query_save", { place: "mine", title: "From prod", sql: "SELECT 'pulled'", warehouse: "WH_Prod", name: "From prod.sql" }));
  await setDoc("SELECT 3");
  await page().getByRole("button", { name: "Save query" }).click();
  await page().waitForFunction(() => /changed since you opened it/.test(document.querySelector(".toast")?.textContent || ""));
  assert.strictEqual((await saved()).queries.find((q) => q.title === "From prod").sql, "SELECT 'pulled'");
  app.errors.length = 0;
});

test("nothing on the page threw", { skip }, async () => {
  assert.deepStrictEqual(app.errors, []);
});
