// S10 (Preview): SQL from a template on a lakehouse's SQL page, in a real browser (see harness.cjs for
// how to run it). The gallery offers only read-only Spark SQL and DuckDB templates, never a T-SQL one.
// Every template, filled with its defaults, is read by the real engine's rules for its own dialect
// (sql_analyze); nothing runs and no lakehouse is needed.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp, sqlRunMocks, axeFindings, axeSource } = require("./harness.cjs");

const LH = { workspace_id: "ws1", lakehouse_id: "lh1", name: "Sales" };

let app;
const skip = !playwright && "Playwright is not installed";
const page = () => app.page;

before(async () => {
  if (skip) return;
  app = await openApp({
    engine: ["sql_format", "sql_analyze", "sql_templates_list", "sql_queries_list"],
    mocks: { ...sqlRunMocks(() => ({ columns: [{ name: "x", type: "int" }], rows: [[1]] })),
      list_tables: () => ({ dbo: ["sales"] }), table_columns: () => [] },
  });
  await page().evaluate((lh) => openSqlQuery(lh), LH);
  await page().waitForSelector(".sql-page .sql-edit .cm-editor");
  app.errors.length = 0;
});

after(async () => { await app?.close(); });

const openGallery = async () => {
  await page().getByRole("button", { name: "More ways to start a query" }).click();
  await page().getByRole("menuitem", { name: "SQL from a template" }).click();
  const dialog = page().locator(".sqlt-dialog");
  await dialog.waitFor();
  return dialog;
};

test("every lakehouse template, filled with its defaults, has no errors under its own dialect's rules", { skip }, async () => {
  const out = await page().evaluate(async () => {
    const res = [];
    for (const t of SQL_LAKEHOUSE_TEMPLATES) {
      const sql = sqlTemplateFill(t.sql, {}, t.dialect);
      const r = await rpc("sql_analyze", { query: sql, target: t.dialect });
      res.push({ id: t.id, status: r.outcome.status, errors: r.diagnostics.filter((d) => d.severity === "error").map((d) => d.rule),
        left: /<\w+\s*,/.test(sql) });
    }
    return res;
  });
  assert.strictEqual(out.length, 10);
  for (const r of out) {
    assert.deepStrictEqual(r.errors, [], r.id);
    assert.strictEqual(r.left, false, `${r.id} has no placeholder left`);
  }
});

// axe covers dialogs: sqltemplates.js:openSqlTemplates
test("the gallery on a lakehouse offers Spark SQL and DuckDB templates only, starting on the tab's dialect", { skip }, async () => {
  const dialog = await openGallery();
  assert.match(await dialog.textContent(), /For Sales · Spark SQL and DuckDB, read only/);
  assert.deepStrictEqual(await dialog.locator(".sqlt-group").allInnerTexts(), ["Spark SQL", "DuckDB"]);
  assert.strictEqual(await dialog.locator(".sqlt-group.on").innerText(), "Spark SQL", "a new tab is Spark SQL");
  assert.deepStrictEqual(await dialog.locator(".sqlt-card b").allInnerTexts(), ["Top N rows (Spark SQL)", "Row count by group (Spark SQL)",
    "Find duplicates on a key (Spark SQL)", "Null counts per column (Spark SQL)", "Latest row per key (Spark SQL)"]);
  await dialog.getByText("Your own templates are T-SQL, so they're offered on a warehouse only.").waitFor();
  assert.strictEqual(await dialog.getByRole("button", { name: "New template" }).count(), 0);
  assert.strictEqual(await dialog.getByRole("button", { name: "Import" }).count(), 0);
  assert.strictEqual(await dialog.getByRole("button", { name: "Make a copy" }).count(), 0);
  await dialog.getByText("Opening a template runs nothing. These templates only read.").waitFor();
  if (axeSource) assert.deepStrictEqual(await axeFindings(page(), "#dialog-root"), []);
  await dialog.getByLabel("Search templates").fill("Create a table");
  await dialog.getByText("No template matches.").waitFor();
  await dialog.getByLabel("Search templates").fill("table");
  assert.ok((await dialog.locator(".sqlt-card b").allInnerTexts()).every((t) => /\((Spark SQL|DuckDB)\)$/.test(t)));
  assert.ok(!app.calls.some((c) => c.method === "sql_templates_list"), "your own T-SQL templates aren't read");
  await page().evaluate(() => closeDialog());
});

test("a DuckDB template opens filled, in a DuckDB tab, and runs nothing", { skip }, async () => {
  const tabs = await page().evaluate(() => sqlPage.tabs.length);
  const dialog = await openGallery();
  await dialog.getByRole("button", { name: "DuckDB" }).click();
  await dialog.getByRole("button", { name: /Find duplicates on a key \(DuckDB\)/ }).click();
  await dialog.getByLabel("key columns").fill("order_id, line");
  await dialog.getByLabel("table name").fill('bad"name');
  await dialog.getByText('Use a name of up to 128 characters without " or a line break.').waitFor();
  assert.strictEqual(await dialog.getByRole("button", { name: "Open in a new tab" }).isDisabled(), true);
  await dialog.getByLabel("table name").fill("orders");
  await dialog.getByRole("button", { name: "Open in a new tab" }).click();
  const tab = await page().evaluate(() => sqlPage.tabs[sqlPage.active]);
  assert.strictEqual(await page().evaluate(() => sqlPage.tabs.length), tabs + 1);
  assert.deepStrictEqual([tab.name, tab.dialect], ["Find duplicates on a key (DuckDB)", "duckdb"]);
  assert.strictEqual(tab.sql, 'SELECT "order_id", "line", COUNT(*) AS copies\nFROM "orders"\nGROUP BY "order_id", "line"\nHAVING COUNT(*) > 1\nORDER BY copies DESC;\n');
  assert.ok(!app.calls.some((c) => c.method === "sql_start"), "opening a template runs nothing");
  const again = await openGallery();
  assert.strictEqual(await again.locator(".sqlt-group.on").innerText(), "DuckDB", "a DuckDB tab starts on DuckDB");
  await page().evaluate(() => closeDialog());
});

test("with Preview features hidden, the lakehouse page has no SQL from a template", { skip }, async () => {
  await page().evaluate(() => { state.settings = { ...(state.settings || {}), show_preview: { value: false } }; renderQuery(); });
  await page().waitForSelector(".sql-page .sql-edit .cm-editor");
  assert.strictEqual(await page().getByRole("button", { name: "More ways to start a query" }).count(), 0);
  assert.strictEqual(await page().getByRole("button", { name: "New SQL query" }).count(), 1);
  await page().evaluate(() => { state.settings = { ...(state.settings || {}), show_preview: { value: true } }; });
  assert.deepStrictEqual(app.errors, []);
});
