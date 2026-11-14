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
const explains = [];

let app;
const skip = !playwright && "Playwright is not installed";

before(async () => {
  if (skip) return;
  app = await openApp({
    engine: ["sql_format", "sql_function_help"],
    // A query tab saved by the old textarea page: no dialect.
    storage: { "fabric-desktop.sql.lh1": JSON.stringify([{ name: "Monthly revenue", sql: "-- Revenue\nSELECT 1;\n" }, { name: "Query 2", sql: "SELECT 2" }]) },
    mocks: {
      list_tables: () => ({ dbo: ["sales", "customers"], finance: ["budget"] }),
      table_columns: ({ table }) => COLUMNS[table] || [],
      ...sqlRunMocks((params) => {
        const { query, dialect } = params;
        if (params.estimated_plan || params.profile) {
          explains.push({ query, dialect, mode: params.estimated_plan ? "plan" : "profile" });
          const answer = params.estimated_plan
            ? { steps: [{ depth: 0, name: "PROJECTION", rows: null, details: { Projections: "x" } },
              { depth: 1, name: "SEQ_SCAN", rows: 3, details: { Table: "sales", Filters: "amount>10" } }], more_steps: 0 }
            : { seconds: 0.012, rows: 2, rows_scanned: 3, peak_memory: 1024, more_steps: 0,
              steps: [{ depth: 0, name: "HASH_GROUP_BY", rows: 2, seconds: 0.004, share: 0.33, details: {} },
                { depth: 1, name: "SEQ_SCAN", rows: 3, seconds: 0.008, share: 0.67, details: { Table: "sales" } }] };
          return { columns: [{ name: params.estimated_plan ? "plan" : "profile", type: "string" }], rows: [[JSON.stringify(answer)]] };
        }
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

test("F5 runs the selection or the whole query, as in SSMS, and never reloads the window", { skip }, async () => {
  await page().evaluate(() => { window.__notReloaded = true; });
  await setDoc("SELECT 4 AS d;\nSELECT 5 AS e;");
  await select(15, 29);
  await page().keyboard.press("F5");
  await page().waitForFunction(() => document.querySelector(".sql-pill")?.textContent === "Succeeded");
  assert.strictEqual(runs.at(-1).query, "SELECT 5 AS e;");
  await setDoc("SELECT 6 AS f");
  await page().keyboard.press("F5");
  await page().waitForFunction(() => document.querySelector(".sql-pill")?.textContent === "Succeeded");
  assert.strictEqual(runs.at(-1).query, "SELECT 6 AS f");
  // Focus outside the editor: F5 still runs this tab's query.
  const before = runs.length;
  await page().evaluate(() => { document.activeElement?.blur(); document.body.focus(); });
  await page().keyboard.press("F5");
  for (let i = 0; i < 50 && runs.length === before; i++) await page().waitForTimeout(100);
  await page().waitForFunction(() => document.querySelector(".sql-pill")?.textContent === "Succeeded");
  assert.ok(runs.length > before, "F5 outside the editor runs the query");
  assert.strictEqual(await page().evaluate(() => window.__notReloaded), true, "the window did not reload");
  const run = page().getByRole("button", { name: "Run", exact: true });
  assert.match(await run.getAttribute("title"), /F5/);
});

test("Ctrl+F2 bookmarks lines, F2 and Shift+F2 go between them, and they move with the text", { skip }, async () => {
  await setDoc("SELECT a\nFROM t\nWHERE x = 1\nORDER BY a", 0);
  const line = () => page().evaluate(() => { const v = sqlPage.editor.view; return v.state.doc.lineAt(v.state.selection.main.head).number; });
  const marked = () => page().evaluate(() => [...document.querySelectorAll(".sql-edit .cm-line")].map((l, i) => (l.classList.contains("sql-bookmarked") ? i + 1 : 0)).filter(Boolean));
  const goLine = (n) => page().evaluate((n) => { const v = sqlPage.editor.view; v.dispatch({ selection: { anchor: v.state.doc.line(n).from } }); v.focus(); }, n);
  await goLine(2);
  await page().keyboard.press("Control+F2");
  await goLine(4);
  await page().keyboard.press("Control+F2");
  assert.deepStrictEqual(await marked(), [2, 4]);
  await goLine(1);
  await page().keyboard.press("F2");
  assert.strictEqual(await line(), 2);
  await page().keyboard.press("F2");
  assert.strictEqual(await line(), 4);
  await page().keyboard.press("F2");
  assert.strictEqual(await line(), 2, "wraps around");
  await page().keyboard.press("Shift+F2");
  assert.strictEqual(await line(), 4);
  // A line typed above moves the marks down with their text.
  await goLine(1);
  await page().keyboard.press("Home");
  await page().keyboard.type("-- note\n");
  assert.deepStrictEqual(await marked(), [3, 5]);
  assert.strictEqual(await docText(), "-- note\nSELECT a\nFROM t\nWHERE x = 1\nORDER BY a");
  await goLine(3);  // Enter at the start of a marked line: the mark stays with its text
  await page().keyboard.press("Enter");
  assert.deepStrictEqual(await marked(), [4, 6]);
  await page().keyboard.press("Control+z");
  assert.deepStrictEqual(await marked(), [3, 5]);
  await goLine(3);
  await page().keyboard.press("Control+F2");
  assert.deepStrictEqual(await marked(), [5], "Ctrl+F2 on a marked line removes it");
  await page().keyboard.press("Control+Shift+F2");
  assert.deepStrictEqual(await marked(), []);
  await page().keyboard.press("F2");
  assert.strictEqual(await line(), 3, "F2 with no bookmarks stays put");
});

test("the results pane maximizes, hides with Ctrl+R wherever focus is, and comes back on a run", { skip }, async () => {
  await page().evaluate(() => { window.__notReloaded = true; });
  const card = page().locator(".sql-card");
  await setDoc("SELECT 10 AS j");
  await page().getByRole("button", { name: "Maximize", exact: true }).click();
  assert.match(await card.getAttribute("class"), /sql-pane-max/);
  assert.ok(!(await page().locator(".sql-edit").isVisible()), "the editor is hidden");
  assert.ok(await page().locator(".sql-grid").isVisible());
  await page().getByRole("button", { name: "Restore", exact: true }).click();
  assert.ok(await page().locator(".sql-edit").isVisible());
  const gridBox = await page().locator(".sql-edit").boundingBox();
  await page().evaluate(() => sqlPage.editor.focus());
  await page().keyboard.press("Control+r");
  assert.match(await card.getAttribute("class"), /sql-pane-hidden/);
  assert.ok(!(await page().locator(".sql-grid").isVisible()), "the results are hidden");
  assert.ok((await page().locator(".sql-edit").boundingBox()).height > gridBox.height, "the editor takes the room");
  assert.ok(await page().getByRole("button", { name: "Show results", exact: true }).isVisible());
  await page().evaluate(() => { document.activeElement?.blur(); document.body.focus(); });
  await page().keyboard.press("Control+r");
  assert.doesNotMatch(await card.getAttribute("class"), /sql-pane-hidden/, "Ctrl+R outside the editor shows them again");
  await page().evaluate(() => sqlPage.editor.focus());
  await page().keyboard.press("Control+r");
  await page().keyboard.press("F5");
  await page().waitForFunction(() => document.querySelector(".sql-pill")?.textContent === "Succeeded");
  assert.doesNotMatch(await card.getAttribute("class"), /sql-pane-hidden/, "a run shows the results");
  assert.ok(await page().locator(".sql-grid").isVisible());
  assert.strictEqual(await page().evaluate(() => window.__notReloaded), true, "Ctrl+R never reloads the window");
});

test("Ctrl+Shift+U and Ctrl+Shift+L change case, and Ctrl+G goes to a line", { skip }, async () => {
  await setDoc("select a\nfrom Sales\nwhere x = 1", 0);
  await select(0, 6);
  await page().keyboard.press("Control+Shift+U");
  assert.strictEqual(await docText(), "SELECT a\nfrom Sales\nwhere x = 1");
  await page().evaluate(() => { sqlPage.editor.view.dispatch({ selection: { anchor: 15 } }); sqlPage.editor.focus(); });  // in "Sales"
  await page().keyboard.press("Control+Shift+L");
  assert.strictEqual(await docText(), "SELECT a\nfrom sales\nwhere x = 1", "the word at the cursor");
  await page().keyboard.press("Control+z");
  assert.strictEqual(await docText(), "SELECT a\nfrom Sales\nwhere x = 1", "one undo step");
  // Two cursors in one word, and a selection inside it: the word changes once.
  await setDoc("select Sales", 0);
  await page().evaluate(() => {
    const v = sqlPage.editor.view;
    const ES = v.state.selection.constructor;  // EditorSelection
    v.dispatch({ selection: ES.create([ES.cursor(8), ES.cursor(10), ES.range(9, 11)]) });
    v.focus();
  });
  await page().keyboard.press("Control+Shift+U");
  assert.strictEqual(await docText(), "select SALES");
  await setDoc("SELECT a\nfrom Sales\nwhere x = 1", 0);
  await page().keyboard.press("Control+g");
  const box = page().locator(".sql-edit .cm-gotoLine input");
  await box.waitFor();
  await box.fill("3");
  await box.press("Enter");
  const line = await page().evaluate(() => { const v = sqlPage.editor.view; return v.state.doc.lineAt(v.state.selection.main.head).number; });
  assert.strictEqual(line, 3);
  assert.strictEqual(await docText(), "SELECT a\nfrom Sales\nwhere x = 1");
});

test("Ctrl+Shift+Enter runs only the statement at the cursor, or the selection", { skip }, async () => {
  const text = "SELECT 7 AS g;\n\nSELECT 8 AS h\nFROM sales;\nSELECT 9 AS i";
  await setDoc(text, text.indexOf("FROM"));
  await page().keyboard.press("Control+Shift+Enter");
  await page().waitForFunction(() => document.querySelector(".sql-pill")?.textContent === "Succeeded");
  assert.strictEqual(runs.at(-1).query, "SELECT 8 AS h\nFROM sales");
  await setDoc(text, "SELECT 7 AS g;".length);  // just after the semicolon: the statement it ends
  await page().keyboard.press("Control+Shift+Enter");
  await page().waitForFunction(() => document.querySelector(".sql-pill")?.textContent === "Succeeded");
  assert.strictEqual(runs.at(-1).query, "SELECT 7 AS g");
  await select(text.indexOf("SELECT 9"), text.indexOf("SELECT 9") + "SELECT 9".length);
  await page().keyboard.press("Control+Shift+Enter");
  await page().waitForFunction(() => document.querySelector(".sql-pill")?.textContent === "Succeeded");
  assert.strictEqual(runs.at(-1).query, "SELECT 9");
  assert.strictEqual(await docText(), text, "nothing in the editor changed");
  const run = page().getByRole("button", { name: "Run", exact: true });
  assert.match(await run.getAttribute("title"), /Ctrl\+Shift\+Enter/);
});

test("Plan shows DuckDB's plan without running the query, and Profile times each step", { skip }, async () => {
  await setDoc("SELECT x FROM sales WHERE amount > 10");
  const before = runs.length;
  await page().getByRole("tab", { name: /^Plan/ }).click();
  await page().getByRole("button", { name: "Show the plan" }).click();
  await page().waitForSelector(".sql-plan-tree");
  assert.deepStrictEqual(explains.at(-1), { query: "SELECT x FROM sales WHERE amount > 10", dialect: "spark", mode: "plan" });
  assert.deepStrictEqual(await page().locator(".sql-plan-op .sql-plan-name").allTextContents(), ["Select columns", "Level 2: Read table"]);
  assert.match(await page().locator(".sql-plan-op").nth(1).innerText(), /sales[\s\S]*3 rows/);
  await page().getByRole("tab", { name: /^Profile/ }).click();
  await page().getByRole("button", { name: "Profile the query" }).click();
  await page().waitForSelector(".qprofile table");
  assert.strictEqual(explains.at(-1).mode, "profile");
  assert.match(await page().locator(".qprofile").innerText(), /Group by[\s\S]*Read table/);
  assert.strictEqual(runs.length, before, "neither counted as a run of the query");
  await page().getByRole("tab", { name: "Results", exact: true }).click();
});

test("hovering a column, a table or a function shows what it is, and typing a call shows its arguments", { skip }, async () => {
  const text = "SELECT s.amount, date_trunc('month', s.order_date) FROM sales s";
  await setDoc(text, 0);
  await page().waitForTimeout(100);
  const hoverAt = async (pos) => {
    const xy = await page().evaluate((pos) => { const c = sqlPage.editor.view.coordsAtPos(pos); return { x: c.left + 2, y: (c.top + c.bottom) / 2 }; }, pos);
    await page().mouse.move(xy.x, xy.y);
    const card = page().locator(".nb-intel.hover:not([hidden])");
    await card.waitFor({ timeout: 15000 });  // the first answer starts the engine's DuckDB
    return card.innerText();
  };
  const away = () => page().mouse.move(5, 5);
  assert.match(await hoverAt(text.indexOf("amount") + 2), /amount[\s\S]*decimal\(18,2\)[\s\S]*Column of sales/);
  await away();
  const fn = await hoverAt(text.indexOf("date_trunc") + 2);
  assert.match(fn, /date_trunc\(fmt, ts\)/, "Spark's own usage, from the real engine");
  assert.match(fn, /truncated to the unit/);
  await away();
  assert.match(await hoverAt(text.indexOf("sales s") + 1), /sales[\s\S]*order_id[\s\S]*Region Name/);
  await away();
  // Signature help while typing: the argument the cursor is on is marked.
  await setDoc("SELECT date_trunc('month', ");
  const call = page().locator(".nb-intel.call:not([hidden])");
  await call.waitFor({ timeout: 15000 });
  assert.strictEqual(await call.locator("b").textContent(), "ts");
  await page().keyboard.type("x)");
  await page().waitForSelector(".nb-intel.call", { state: "hidden" });
  // T-SQL forms for a T-SQL dialect.
  await page().locator("select.sql-dialect").selectOption("warehouse");
  await setDoc("SELECT CHARINDEX('a', b, ");
  await call.waitFor({ timeout: 15000 });
  assert.match(await call.innerText(), /CHARINDEX\(expression_to_find, expression_to_search\[, start_location\]\)/);
  assert.strictEqual(await call.locator("b").textContent(), "start_location");
  await page().locator("select.sql-dialect").selectOption("spark");
  await setDoc("SELECT 1");
});

test("F12 shows the table under the cursor, an alias's or a column's, in the Explorer", { skip }, async () => {
  const text = "SELECT c.region, registered_at FROM customers c";
  await setDoc(text, text.indexOf("c.region"));  // on the alias
  await page().keyboard.press("F12");
  const row = page().locator(".sql-table-row .tree-row[title='Query customers']");
  await page().waitForFunction(() => document.activeElement?.title === "Query customers");
  assert.ok(await page().locator(".tree-row.sql-col", { hasText: "registered_at" }).isVisible(), "its columns are open");
  assert.match(await row.getAttribute("class"), /sql-revealed/);
  const collapse = () => page().locator(".sql-table-row", { hasText: "customers" }).locator(".chev").click();
  await collapse();
  await setDoc(text, text.indexOf("registered_at") + 3);  // on a bare column
  await page().keyboard.press("F12");
  await page().waitForFunction(() => document.activeElement?.title === "Query customers");
  await setDoc(text, text.indexOf("SELECT") + 2);  // on a keyword: nothing to go to
  await page().keyboard.press("F12");
  await page().waitForFunction(() => /F12 goes to a table or view/.test(document.querySelector(".toast")?.textContent || ""));
  assert.strictEqual(await page().evaluate(() => document.activeElement?.getAttribute("aria-label")), "SQL query", "focus stays in the editor");
  await collapse();
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

test("Messages, Chart, Plan and Profile carry the Preview tag on a lakehouse page and go when preview features are hidden", { skip }, async () => {
  const views = () => page().locator(".sql-views [role=tab]").allTextContents();
  assert.deepStrictEqual(await views(), ["Results", "MessagesPreview", "ChartPreview", "PlanPreview", "ProfilePreview"]);
  await page().evaluate(() => { state.settings = { ...state.settings, show_preview: { value: false } }; sqlPage.redraw(); });
  assert.deepStrictEqual(await views(), ["Results"]);
  await page().evaluate(() => { state.settings.show_preview = { value: true }; sqlPage.redraw(); });
});

test("the query tab list holds only tabs; Delete on a tab or its × closes it", { skip }, async () => {
  const names = () => page().locator(".sql-tabs [role=tab]").allTextContents();
  const before = await names();
  const strays = () => page().evaluate(() => [...document.querySelectorAll(".sql-tabs button")]
    .filter((b) => b.getAttribute("role") !== "tab" && !b.closest("[aria-hidden=true]")).map((b) => b.outerHTML));
  assert.deepStrictEqual(await strays(), [], "only tabs are exposed in the tablist");
  assert.strictEqual(await page().locator(".sql-tabs").getByRole("button", { name: "New query" }).count(), 0, "New query sits beside the list");
  await page().getByRole("button", { name: "New query" }).click();
  const added = (await names()).at(-1);
  await page().getByRole("tab", { name: added }).press("Delete");
  assert.deepStrictEqual(await names(), before, "a query with only the starting hint closes at once");
  assert.strictEqual(await page().evaluate(() => document.activeElement?.getAttribute("role")), "tab", "focus stays on the tabs");
  // A query with SQL of its own asks first, in red; Cancel keeps it.
  await page().getByRole("button", { name: "New query" }).click();
  const typed = (await names()).at(-1);
  await page().evaluate(() => { sqlPage.tabs[sqlPage.active].sql = "SELECT 42"; });
  await page().getByRole("tab", { name: typed }).press("Delete");
  const ask = page().getByRole("dialog", { name: `Close ${typed}?` });
  assert.match(await ask.getByRole("button", { name: "Close query" }).getAttribute("class"), /danger/);
  await ask.getByRole("button", { name: "Cancel" }).click();
  assert.deepStrictEqual(await names(), [...before, typed]);
  await page().getByRole("tab", { name: typed }).press("Delete");
  await page().getByRole("dialog", { name: `Close ${typed}?` }).getByRole("button", { name: "Close query" }).click();
  assert.deepStrictEqual(await names(), before);
  await page().getByRole("button", { name: "New query" }).click();
  await page().locator(".sql-tab", { hasText: (await names()).at(-1) }).locator(".close").click();
  assert.deepStrictEqual(await names(), before);
  // The × asks the same way.
  await page().getByRole("button", { name: "New query" }).click();
  const clicked = (await names()).at(-1);
  await page().evaluate(() => { sqlPage.tabs[sqlPage.active].sql = "SELECT 7"; });
  await page().locator(".sql-tab", { hasText: clicked }).locator(".close").click();
  await page().getByRole("dialog", { name: `Close ${clicked}?` }).getByRole("button", { name: "Cancel" }).click();
  assert.deepStrictEqual(await names(), [...before, clicked]);
  await page().locator(".sql-tab", { hasText: clicked }).locator(".close").click();
  await page().getByRole("dialog", { name: `Close ${clicked}?` }).getByRole("button", { name: "Close query" }).click();
  assert.deepStrictEqual(await names(), before);
  await page().getByRole("tab", { name: before[0] }).click();
});

test("nothing on the page threw", { skip }, async () => {
  assert.deepStrictEqual(app.errors, []);
});
