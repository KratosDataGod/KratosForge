// SQL-002: compatibility findings in the SQL editor and the Compatibility panel, checked by the real engine.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { playwright, openApp } = require("./harness.cjs");

const LH = { workspace_id: "ws1", lakehouse_id: "lh1", name: "Sales" };
const SHOTS = process.env.SCREENSHOT_DIR;
const runs = [];
const exported = [];

let app;
const skip = !playwright && "Playwright is not installed";

before(async () => {
  if (skip) return;
  app = await openApp({
    engine: ["sql_format", "sql_dialects", "sql_analyze", "sql_compat_profiles"],
    storage: { "fabric-desktop.sql.lh1": JSON.stringify([{ name: "Board pack", sql: "SELECT 1", dialect: "warehouse" }]) },
    mocks: {
      list_tables: () => ({ dbo: ["sales", "customers"] }),
      table_columns: () => [{ name: "amount", type: "decimal(18,2)" }, { name: "name", type: "string" }],
      run_sql: ({ query, dialect }) => {
        runs.push({ query, dialect });
        return { columns: [{ name: "x", type: "int64" }], rows: [[1]], row_count: 1, seconds: 0.01 };
      },
      sql_compat_export: (params) => {
        exported.push(params);
        return { path: "C:\\Users\\me\\Downloads\\Board pack compatibility warehouse.json", outcome: "known_problems",
          counts: { error: 1, warning: 0, information: 0, unverified: 0 } };
      },
    },
  });
  await app.page.evaluate((lh) => openSqlQuery(lh), LH);
  await app.page.waitForSelector(".sql-edit .cm-editor");
  app.errors.length = 0;
});

after(async () => { await app?.close(); });

const page = () => app.page;
const analyzeCalls = () => app.calls.filter((c) => c.method === "sql_analyze");
async function setDoc(text) {
  await page().evaluate((text) => {
    const v = sqlPage.editor.view;
    v.dispatch({ changes: { from: 0, to: v.state.doc.length, insert: text }, selection: { anchor: text.length } });
    v.focus();
  }, text);
}
// Wait until the panel shows the check of exactly `text`.
const checked = (text) => page().waitForFunction((text) => {
  const t = sqlPage.tabs[sqlPage.active];
  return t.compatText === text && t.compat && !document.querySelector(".sql-compat")?.textContent.includes("Write SQL");
}, text, { timeout: 10000 });
const panel = () => page().locator(".sql-compat").textContent();
const underlined = (cls) => page().evaluate((cls) => [...document.querySelectorAll(`.sql-edit .${cls}`)].map((e) => e.textContent), cls);

test("a Warehouse problem is underlined in the editor and listed in the panel", { skip }, async () => {
  const sql = "SELECT name, amount\nFROM dbo.sales\nORDER BY amount DESC\nLIMIT 10";
  await setDoc(sql);
  await checked(sql);
  await page().waitForFunction(() => document.querySelector(".sql-edit .cm-lintRange-error"));
  assert.deepStrictEqual(await underlined("cm-lintRange-error"), ["LIMIT 10"]);
  const text = await panel();
  assert.match(text, /Fabric Warehouse \(T-SQL\) · rules 2026\.10\.1 · 1 statement/);
  assert.match(text, /1 error/);
  assert.match(text, /Known problems/);
  assert.match(text, /Error · Ln 4 · LIMIT isn't T-SQL/);
  assert.doesNotMatch(text, /%/, "no percentage score");
  assert.ok(await page().locator(".sql-edit .cm-lint-marker-error").count(), "a marker in the gutter");
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "sql-002-compatibility.png") });
});

test("picking a finding selects its SQL and shows why, what to do and the source", { skip }, async () => {
  await page().locator(".sql-finding").first().click();
  const chosen = await page().evaluate(() => {
    const { from, to } = sqlPage.editor.selection();
    return sqlPage.editor.text().slice(from, to);
  });
  assert.strictEqual(chosen, "LIMIT 10");
  const detail = await page().locator(".sql-finding-detail").textContent();
  assert.match(detail, /SYN-001/);
  assert.match(detail, /doesn't support LIMIT/);
  assert.match(detail, /SELECT TOP \(n\)/);
  assert.match(detail, /Confidence: high/);
  assert.ok(await page().locator(".sql-finding-detail a", { hasText: "Source" }).count());
});

test("hovering the underline shows the finding in the editor", { skip }, async () => {
  const box = await page().locator(".sql-edit .cm-lintRange-error").boundingBox();
  await page().mouse.move(box.x + 6, box.y + box.height / 2);
  await page().waitForSelector(".cm-tooltip-lint", { timeout: 5000 });
  const tip = await page().locator(".cm-tooltip-lint").textContent();
  assert.match(tip, /Error · LIMIT isn't T-SQL/);
  assert.match(tip, /SYN-001 · confidence high/);
  await page().mouse.move(5, 5);
});

test("Export JSON saves the report for the SQL in the editor and the chosen engine", { skip }, async () => {
  await page().getByRole("button", { name: "Export JSON" }).click();
  await page().waitForFunction(() => document.querySelector(".toast")?.textContent.includes("compatibility report"));
  assert.deepStrictEqual(exported.at(-1), { query: "SELECT name, amount\nFROM dbo.sales\nORDER BY amount DESC\nLIMIT 10",
    target: "warehouse", name: "Board pack" });
});

test("changing the engine checks the SQL again against it", { skip }, async () => {
  const sql = "SELECT TOP (5) [name] FROM dbo.sales";
  await setDoc(sql);
  await checked(sql);
  assert.match(await panel(), /No known problems found by static checks\. That isn't proof it runs in Fabric Warehouse/);
  await page().locator("select.sql-dialect").selectOption("spark");
  await page().waitForFunction(() => /Spark SQL \(Lakehouse\)/.test(document.querySelector(".sql-compat").textContent) &&
    sqlPage.tabs[sqlPage.active].compat?.target?.id === "spark");
  const text = await panel();
  assert.match(text, /TOP is T-SQL/);
  assert.match(text, /\[Square brackets\] are T-SQL names/);
  assert.deepStrictEqual((await underlined("cm-lintRange-error")).sort(), ["TOP", "[name]"]);
});

test("a run on this PC isn't blocked, and says when Fabric Spark may differ", { skip }, async () => {
  const sql = "SELECT name FROM sales WHERE name RLIKE '^A(?=b)'";
  await setDoc(sql);
  await checked(sql);
  assert.match(await panel(), /Regular expressions differ on this PC/);
  await page().getByRole("button", { name: "Run" }).click();
  await page().waitForFunction(() => document.querySelector(".sql-local-note"));
  assert.strictEqual(runs.at(-1).query, sql);
  const note = await page().locator(".sql-local-note").textContent();
  assert.match(note, /Ran on this PC with DuckDB, not in Fabric\. 1 finding say/);
  assert.match(note, /A warning doesn't mean the query is wrong/);
});

test("positions stay right after characters outside the basic plane", { skip }, async () => {
  await page().locator("select.sql-dialect").selectOption("warehouse");
  const sql = "SELECT '🙂🙂' AS face\nFROM dbo.sales LIMIT 3";
  await setDoc(sql);
  await checked(sql);
  await page().waitForFunction(() => document.querySelector(".sql-edit .cm-lintRange-error"));
  assert.deepStrictEqual(await underlined("cm-lintRange-error"), ["LIMIT 3"]);
});

test("a long query is checked only when asked, so typing never waits on the engine", { skip }, async () => {
  const sql = Array.from({ length: 4000 }, (_, i) => `SELECT ${i} AS n FROM dbo.sales`).join(";\n");
  assert.ok(sql.length > 100000);
  const before = analyzeCalls().length;
  await setDoc(sql);
  await page().waitForFunction(() => /checked when you ask/.test(document.querySelector(".sql-compat").textContent));
  await page().waitForTimeout(1200);
  assert.strictEqual(analyzeCalls().length, before, "not sent to the engine");
  await page().getByRole("button", { name: "Check now" }).click();
  await checked(sql);
  assert.match(await panel(), /4,000 statements/);
  await setDoc("SELECT 1");
});

test("nothing on the page threw", { skip }, async () => {
  assert.deepStrictEqual(app.errors, []);
});
