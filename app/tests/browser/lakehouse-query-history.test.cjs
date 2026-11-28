// S36 (Preview): Query history on a lakehouse's SQL page, in a real browser (see harness.cjs for how to
// run it). The Explorer's Query history tab lists this PC's runs on the lakehouse, with a filter, and is
// apart from the result pane's History (S27), which keeps one tab's results. Runs are mocked.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp, sqlRunMocks, axeFindings, axeSource } = require("./harness.cjs");

const LH = { workspace_id: "ws1", lakehouse_id: "lh1", name: "Sales" };

let app;
const skip = !playwright && "Playwright is not installed";
const page = () => app.page;
const aside = () => page().locator("aside.explorer");

before(async () => {
  if (skip) return;
  app = await openApp({
    engine: ["sql_format", "sql_analyze", "sql_queries_list"],
    mocks: { ...sqlRunMocks((p) => {
      if (/salez/.test(p.query)) throw new Error("Catalog Error: Table with name 'salez' does not exist at line 1");
      return { columns: [{ name: "region", type: "string" }], rows: [["West"], ["East"]] };
    }), list_tables: () => ({ dbo: ["sales", "customers"] }), table_columns: () => [] },
  });
  await page().evaluate((lh) => openSqlQuery(lh), LH);
  await page().waitForSelector(".sql-page .sql-edit .cm-editor");
  app.errors.length = 0;
});

after(async () => { await app?.close(); });

async function runSql(text) {
  const before = app.calls.filter((c) => c.method === "sql_start").length;
  await page().evaluate((text) => {
    const v = sqlPage.editor.view;
    v.dispatch({ changes: { from: 0, to: v.state.doc.length, insert: text }, selection: { anchor: 0 } });
    sqlPage.runQuery();
  }, text);
  for (let i = 0; i < 100 && app.calls.filter((c) => c.method === "sql_start").length === before; i++) await page().waitForTimeout(50);
  await page().waitForFunction(() => !sqlPage.tabs[sqlPage.active].running && sqlPage.tabs[sqlPage.active].exec);
}

test("the Query history tab starts empty and has no query insights", { skip }, async () => {
  await aside().getByRole("tab", { name: "Query history" }).click();
  await aside().getByText("No runs yet").waitFor();
  assert.doesNotMatch(await aside().textContent(), /Query insights|queryinsights/);
});

test("each run on the lakehouse is kept, newest first, and the filter finds one by its table", { skip }, async () => {
  await runSql("SELECT region FROM sales");
  await runSql("-- by customer\nSELECT region FROM customers");
  await runSql("SELECT region FROM salez");
  const rows = aside().locator(".wh-h-row");
  await page().waitForFunction(() => document.querySelectorAll("aside.explorer .wh-h-row").length === 3);
  assert.deepStrictEqual(await rows.locator(".wh-h-sql").allInnerTexts(),
    ["SELECT region FROM salez", "SELECT region FROM customers", "SELECT region FROM sales"]);
  assert.match(await rows.first().locator(".wh-h-meta").innerText(), /Failed/);
  assert.match(await rows.nth(2).locator(".wh-h-meta").innerText(), /Succeeded.*2 rows/);
  assert.strictEqual(await rows.first().getAttribute("title"), "Failed: Catalog Error: Table with name '…' does not exist at line #", "the error's own words are masked");
  await aside().getByLabel("Filter history").fill("customers");
  assert.deepStrictEqual(await rows.locator(".wh-h-sql").allInnerTexts(), ["SELECT region FROM customers"]);
  await aside().getByLabel("Filter history").fill("");
  const kept = await page().evaluate(() => JSON.parse(localStorage.getItem("fabric-desktop.sqlhistory.lh1")).length);
  assert.strictEqual(kept, 3, "kept under the lakehouse's own key");
});

test("a run opens in a new tab without running, and the result pane keeps its own History", { skip }, async () => {
  const starts = app.calls.filter((c) => c.method === "sql_start").length;
  const tabs = await page().evaluate(() => sqlPage.tabs.length);
  await aside().locator(".wh-h-row").last().click();
  assert.strictEqual(await page().evaluate(() => sqlPage.tabs.length), tabs + 1);
  assert.strictEqual(await page().evaluate(() => sqlPage.tabs[sqlPage.active].sql), "SELECT region FROM sales");
  assert.strictEqual(app.calls.filter((c) => c.method === "sql_start").length, starts, "opening a run runs nothing");
  assert.strictEqual(await page().getByRole("tab", { name: "Query history" }).count(), 1);
  assert.strictEqual(await page().locator(".sql-main").getByRole("tab", { name: /^History/ }).count(), 1, "the result pane's History stays");
});

// axe covers dialogs: meta.js:confirmDialog
test("Clear history asks first and removes only this lakehouse's runs", { skip }, async () => {
  await page().evaluate(() => localStorage.setItem("fabric-desktop.sqlhistory.warehouse.wh1", JSON.stringify([{ at: 1, sql: "SELECT 1", state: "completed" }])));
  await aside().getByRole("button", { name: "Clear history" }).click();
  const dialog = page().getByRole("dialog", { name: "Clear history" });
  await dialog.getByText("Remove the 3 runs kept for Sales on this PC?").waitFor();
  if (axeSource) assert.deepStrictEqual(await axeFindings(page(), "#dialog-root"), []);
  await dialog.getByRole("button", { name: "Clear" }).click();
  await aside().getByText("No runs yet").waitFor();
  assert.strictEqual(await page().evaluate(() => localStorage.getItem("fabric-desktop.sqlhistory.lh1")), null);
  assert.notStrictEqual(await page().evaluate(() => localStorage.getItem("fabric-desktop.sqlhistory.warehouse.wh1")), null);
});

test("with Preview features hidden, the lakehouse page keeps no query history and has no Query history tab", { skip }, async () => {
  await page().evaluate((lh) => { state.settings = { ...(state.settings || {}), show_preview: { value: false } }; openSqlQuery(lh); renderQuery(); }, LH);
  await page().waitForSelector(".sql-page .sql-edit .cm-editor");
  assert.strictEqual(await aside().getByRole("tab", { name: "Query history" }).count(), 0);
  await runSql("SELECT region FROM sales");
  assert.strictEqual(await page().evaluate(() => localStorage.getItem("fabric-desktop.sqlhistory.lh1")), null);
  await page().evaluate(() => { state.settings = { ...(state.settings || {}), show_preview: { value: true } }; });
  assert.deepStrictEqual(app.errors, []);
});
