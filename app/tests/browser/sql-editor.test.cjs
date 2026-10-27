// SQL-001: the SQL query page's editor in a real browser (see harness.cjs for how to run it).
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { playwright, openApp, sqlRunMocks } = require("./harness.cjs");

const LH = { workspace_id: "ws1", lakehouse_id: "lh1", name: "Sales" };
const SHOTS = process.env.SCREENSHOT_DIR;
const COLUMNS = {
  sales: [{ name: "order_id", type: "long" }, { name: "customer_id", type: "long" }, { name: "order_date", type: "date" },
    { name: "amount", type: "decimal(18,2)" }, { name: "Region Name", type: "string" }],
  customers: [{ name: "customer_id", type: "long" }, { name: "region", type: "string" }, { name: "registered_at", type: "timestamp" }],
  "finance.budget": [{ name: "year", type: "int" }, { name: "target", type: "double" }],
};
const runs = [];

let app;
const skip = !playwright && "Playwright is not installed";

before(async () => {
  if (skip) return;
  app = await openApp({
    engine: ["sql_format", "sql_dialects"],
    // A query tab saved by the old textarea page: no dialect.
    storage: { "fabric-desktop.sql.lh1": JSON.stringify([{ name: "Monthly revenue", sql: "-- Revenue\nSELECT 1;\n" }, { name: "Query 2", sql: "SELECT 2" }]) },
    mocks: {
      list_tables: () => ({ dbo: ["sales", "customers"], finance: ["budget"] }),
      table_columns: ({ table }) => COLUMNS[table] || [],
      ...sqlRunMocks(({ query, dialect }) => {
        runs.push({ query, dialect });
        if (/boom/i.test(query)) throw new Error("Could not read the query: Invalid expression / Unexpected token. Line 1, Col: 8.");
        return { columns: [{ name: "x", type: "int64" }], rows: [[1], [null]] };
      }),
    },
  });
  await app.page.evaluate((lh) => openSqlQuery(lh), LH);
  await app.page.waitForSelector(".sql-edit .cm-editor");
  app.errors.length = 0;  // Home, behind the SQL page, has nothing to show without an engine
});

after(async () => { await app?.close(); });

const page = () => app.page;
const docText = () => page().evaluate(() => sqlPage.editor.text());
async function setDoc(text, cursor = text.length) {
  await page().evaluate(({ text, cursor }) => {
    const v = sqlPage.editor.view;
    v.dispatch({ changes: { from: 0, to: v.state.doc.length, insert: text }, selection: { anchor: cursor } });
    v.focus();
  }, { text, cursor });
}
const select = (from, to) => page().evaluate(({ from, to }) => {
  sqlPage.editor.view.dispatch({ selection: { anchor: from, head: to } });
  sqlPage.editor.view.focus();
}, { from, to });
const toastText = async () => (await page().locator(".toast").allTextContents()).join(" | ");

test("a tab saved by the old page opens in the new editor without losing its SQL", { skip }, async () => {
  assert.strictEqual(await docText(), "-- Revenue\nSELECT 1;\n");
  assert.ok(await page().locator(".sql-edit .cm-lineNumbers").count(), "line numbers");
  assert.ok(await page().locator(".sql-edit .cm-foldGutter").count(), "fold gutter");
  assert.deepStrictEqual(await page().locator(".sql-tab .name").allTextContents(), ["Monthly revenue", "Query 2"]);
  assert.strictEqual(await page().locator("select.sql-dialect").inputValue(), "spark");
  assert.deepStrictEqual(app.errors, []);
});

test("keywords, functions, strings, numbers, comments and table names are highlighted", { skip }, async () => {
  await setDoc("-- note\nSELECT COUNT(*) AS n, 'x' FROM sales WHERE amount > 10 AND customer_id = $limit");
  await page().waitForTimeout(200);
  const cls = async (word) => page().evaluate((word) => {
    const span = [...document.querySelectorAll(".sql-edit .cm-line span")].find((s) => s.textContent === word);
    return span ? span.className : null;
  }, word);
  assert.match(await cls("SELECT"), /\bkw\b/);
  assert.match(await cls("COUNT"), /\bfn\b/);
  assert.match(await cls("'x'"), /\bstr\b/);
  assert.match(await cls("10"), /\bnum\b/);
  assert.match(await cls("-- note"), /\bcm\b/);
  assert.match(await cls("sales"), /sql-ref-table/);
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "sql-001-highlighting.png") });
});

test("completion offers the lakehouse's tables after FROM", { skip }, async () => {
  await setDoc("SELECT * FROM sa");
  await page().keyboard.press("Control+Space");
  await page().waitForSelector(".cm-tooltip-autocomplete");
  const labels = await page().locator(".cm-tooltip-autocomplete li .cm-completionLabel").allTextContents();
  assert.strictEqual(labels[0], "sales");
  await page().keyboard.press("Escape");
});

test("completion offers a table's real columns through its alias, and quotes names that need it", { skip }, async () => {
  await setDoc("SELECT s. FROM sales AS s JOIN customers c ON c.customer_id = s.customer_id", "SELECT s.".length);
  await page().keyboard.press("Control+Space");
  await page().waitForSelector(".cm-tooltip-autocomplete");
  const labels = await page().locator(".cm-tooltip-autocomplete li .cm-completionLabel").allTextContents();
  assert.deepStrictEqual(labels.sort(), ["Region Name", "amount", "customer_id", "order_date", "order_id"].sort());
  await page().locator(".cm-tooltip-autocomplete li", { hasText: "Region Name" }).click();
  assert.strictEqual(await docText(), "SELECT s.`Region Name` FROM sales AS s JOIN customers c ON c.customer_id = s.customer_id");
  if (SHOTS) {
    await setDoc("SELECT c.re FROM sales AS s JOIN customers c ON c.customer_id = s.customer_id", "SELECT c.re".length);
    await page().keyboard.press("Control+Space");
    await page().waitForSelector(".cm-tooltip-autocomplete");
    await page().screenshot({ path: path.join(SHOTS, "sql-001-completion.png") });
    await page().keyboard.press("Escape");
  }
});

test("columns are fetched once per table, not on every keystroke", { skip }, async () => {
  const before = app.calls.filter((c) => c.method === "table_columns").length;
  await setDoc("SELECT  FROM sales s", "SELECT ".length);
  for (const ch of "amo") { await page().keyboard.type(ch); await page().waitForTimeout(150); }
  await page().waitForSelector(".cm-tooltip-autocomplete");
  const after = app.calls.filter((c) => c.method === "table_columns").length;
  assert.strictEqual(after - before, 0, "sales columns were already cached");
  await page().keyboard.press("Escape");
});

test("schema-qualified tables complete from the schema", { skip }, async () => {
  await setDoc("SELECT * FROM finance.");
  await page().keyboard.press("Control+Space");
  await page().waitForSelector(".cm-tooltip-autocomplete");
  assert.deepStrictEqual(await page().locator(".cm-tooltip-autocomplete li .cm-completionLabel").allTextContents(), ["budget"]);
  await page().keyboard.press("Escape");
});

test("Ctrl+Enter runs only the selected SQL", { skip }, async () => {
  await setDoc("SELECT 1 AS a;\nSELECT 2 AS b;");
  await select(15, 29);
  await page().keyboard.press("Control+Enter");
  await page().waitForSelector(".sql-pill.ok");
  assert.deepStrictEqual(runs.at(-1), { query: "SELECT 2 AS b;", dialect: "spark" });
  await setDoc("SELECT 3 AS c");
  await page().keyboard.press("Control+Enter");
  await page().waitForFunction(() => document.querySelector(".sql-pill")?.textContent === "Succeeded");
  assert.strictEqual(runs.at(-1).query, "SELECT 3 AS c");
  assert.strictEqual(await page().locator(".sql-grid td.null").first().textContent(), "NULL");
});

test("SQL errors stay visible under the editor", { skip }, async () => {
  await setDoc("SELECT boom");
  await page().keyboard.press("Control+Enter");
  await page().waitForSelector(".sql-grid .output.error");
  assert.match(await page().locator(".sql-grid .output.error").textContent(), /Could not read the query/);
  assert.strictEqual(await docText(), "SELECT boom");
});

test("inserting a column from the Explorer puts it at the cursor and keeps the cursor after it", { skip }, async () => {
  await setDoc("SELECT , amount FROM sales", "SELECT ".length);
  await page().locator(".sql-table-row", { hasText: "customers" }).locator(".chev").click();
  await page().locator(".tree-row.sql-col", { hasText: "registered_at" }).click();
  assert.strictEqual(await docText(), "SELECT registered_at, amount FROM sales");
  const head = await page().evaluate(() => sqlPage.editor.view.state.selection.main.head);
  assert.strictEqual(head, "SELECT registered_at".length);
});

test("Format SQL lays out CTEs and nested queries with the real engine, and Ctrl+Z undoes it", { skip }, async () => {
  const original = "-- top\nwith m as (select customer_id, sum(amount) total from sales where order_id in (select order_id from sales where amount > 0) group by 1) select * from m join customers c on c.customer_id = m.customer_id";
  await setDoc(original);
  await page().getByRole("button", { name: "Format SQL" }).click();
  await page().waitForFunction((o) => sqlPage.editor.text() !== o, original);
  const formatted = await docText();
  assert.match(formatted, /^-- top\nWITH m AS \(\n {2}SELECT\n/);
  assert.match(formatted, /order_id IN \(\n {6}SELECT\n/);
  assert.match(formatted, /\nJOIN customers AS c\n {2}ON c\.customer_id = m\.customer_id$/);
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "sql-001-formatted.png") });
  await page().keyboard.press("Control+z");
  assert.strictEqual(await docText(), original);
});

test("Format SQL leaves SQL it can't keep the meaning of unchanged and says why", { skip }, async () => {
  await setDoc("select ifnull(a, 1) from sales");
  await page().getByRole("button", { name: "Format SQL" }).click();
  await page().waitForFunction(() => document.querySelector(".toast")?.textContent.includes("left the query as it was"));
  assert.strictEqual(await docText(), "select ifnull(a, 1) from sales");
  assert.match(await toastText(), /remove the function 'IFNULL'/);
});

test("comment toggling, find and replace, and multiple cursors work", { skip }, async () => {
  await setDoc("SELECT a FROM t");
  await page().keyboard.press("Control+/");
  assert.strictEqual(await docText(), "-- SELECT a FROM t");
  await page().keyboard.press("Control+/");
  assert.strictEqual(await docText(), "SELECT a FROM t");
  await setDoc("SELECT x, x FROM t", "SELECT ".length);
  await select(7, 8);
  await page().keyboard.press("Control+d");
  assert.strictEqual(await page().evaluate(() => sqlPage.editor.view.state.selection.ranges.length), 2);
  await page().keyboard.type("y");
  assert.strictEqual(await docText(), "SELECT y, y FROM t");
  await page().keyboard.press("Control+f");
  await page().waitForSelector(".cm-search input[name=replace]");
  await page().keyboard.press("Escape");
});

test("Tab indents, and Ctrl+S saves the query tabs", { skip }, async () => {
  await setDoc("SELECT\na", "SELECT\n".length);
  await page().keyboard.press("Tab");
  assert.match(await docText(), /^SELECT\n\s+a$/);
  await page().keyboard.press("Control+s");
  await page().waitForFunction(() => document.querySelector(".toast")?.textContent.startsWith("Saved"));
  const saved = await page().evaluate(() => JSON.parse(localStorage.getItem("fabric-desktop.sql.lh1")));
  assert.match(saved[0].sql, /^SELECT\n\s+a$/);
  assert.strictEqual(saved[0].dialect, "spark");
});

test("T-SQL is formatted but not run on this PC, and the dialect is kept with the tab", { skip }, async () => {
  await page().locator("select.sql-dialect").selectOption("warehouse");
  assert.ok(await page().getByRole("button", { name: "Run" }).isDisabled());
  assert.match(await page().locator(".sql-actions").textContent(), /formatted here, not run/);
  await setDoc("select top 5 [Order Id], @cutoff from dbo.sales");
  await page().getByRole("button", { name: "Format SQL" }).click();
  await page().waitForFunction(() => sqlPage.editor.text().startsWith("SELECT"));
  assert.match(await docText(), /TOP 5[\s\S]*\[Order Id\][\s\S]*@cutoff/);
  const bracketed = await page().evaluate(() => [...document.querySelectorAll(".sql-edit .sql-quoted-name")].map((e) => e.textContent));
  assert.deepStrictEqual(bracketed, ["[Order Id]"], "a bracketed T-SQL name reads as a name, not a keyword");
  const count = runs.length;
  await page().keyboard.press("Control+Enter");
  assert.strictEqual(runs.length, count, "nothing ran");
  const saved = await page().evaluate(() => JSON.parse(localStorage.getItem("fabric-desktop.sql.lh1")));
  assert.strictEqual(saved[0].dialect, "warehouse");
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "sql-001-tsql.png") });
  await page().locator("select.sql-dialect").selectOption("spark");
});

test("Messages and Chart carry the Preview tag on a lakehouse page and go when preview features are hidden", { skip }, async () => {
  const views = () => page().locator(".sql-views [role=tab]").allTextContents();
  assert.deepStrictEqual(await views(), ["Results", "MessagesPreview", "ChartPreview"]);
  await page().evaluate(() => { state.settings = { ...state.settings, show_preview: { value: false } }; sqlPage.redraw(); });
  assert.deepStrictEqual(await views(), ["Results"]);
  await page().evaluate(() => { state.settings.show_preview = { value: true }; sqlPage.redraw(); });
});

test("nothing on the page threw", { skip }, async () => {
  assert.deepStrictEqual(app.errors, []);
});
