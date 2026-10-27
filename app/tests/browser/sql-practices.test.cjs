// SQL best practices (query.js): the Best practices chip beside Compatibility for T-SQL, checked by
// the real engine with the compatibility check, its tab in the panel, and Line n selecting the place.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { playwright, openApp, sqlRunMocks } = require("./harness.cjs");

const LH = { workspace_id: "ws1", lakehouse_id: "lh1", name: "Sales" };
const SHOTS = process.env.SCREENSHOT_DIR;
const skip = !playwright && "Playwright is not installed";

let app;
const page = () => app.page;
const chip = () => page().locator("button[data-practices]");
const panel = () => page().locator(".sql-compat");

before(async () => {
  if (skip) return;
  app = await openApp({
    engine: ["sql_format", "sql_dialects", "sql_analyze", "sql_compat_profiles", "sql_practices", "governance_practice_settings"],
    storage: { "fabric-desktop.sql.lh1": JSON.stringify([{ name: "Views", sql: "SELECT 1", dialect: "warehouse" }]) },
    mocks: {
      list_tables: () => ({ dbo: ["sales"] }),
      table_columns: () => [{ name: "order_date", type: "date" }],
      ...sqlRunMocks(() => ({ columns: [{ name: "x", type: "int64" }], rows: [[1]] })),
    },
  });
  await page().evaluate((lh) => openSqlQuery(lh), LH);
  await page().waitForSelector(".sql-edit .cm-editor");
});

after(async () => { await app?.close(); });

async function setDoc(text) {
  await page().evaluate((text) => {
    const v = sqlPage.editor.view;
    v.dispatch({ changes: { from: 0, to: v.state.doc.length, insert: text }, selection: { anchor: text.length } });
  }, text);
  await page().waitForFunction((text) => sqlPage.tabs[sqlPage.active].compatText === text, text, { timeout: 10000 });
}

const VIEW = "CREATE VIEW dbo.v_orders AS\nSELECT *\nFROM sales\nWHERE YEAR(order_date) = 2026;";

test("T-SQL gets a Best practices chip with the count, and its tab lists the findings", { skip }, async () => {
  await setDoc(VIEW);
  await page().waitForFunction(() => /Best practices · 3/.test(document.querySelector("button[data-practices]")?.textContent));
  assert.match(await chip().getAttribute("class"), /warning/);
  await chip().click();
  await panel().locator("[data-rule]").nth(2).waitFor();
  const rules = await panel().locator("[data-rule]").evaluateAll((els) => els.map((e) => e.dataset.rule));
  assert.deepStrictEqual(rules, ["SQ001", "SQ002", "SQ005"], "worst first, then by line");
  const text = await panel().textContent();
  assert.match(text, /never changes the compatibility result/);
  assert.match(text, /No policy for this project names a SQL rule/);
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "sql-practices.png") });
});

test("Line n selects the place in the editor", { skip }, async () => {
  await panel().locator("[data-rule=SQ002] .link-btn").click();
  const sel = await page().evaluate(() => {
    const v = sqlPage.editor.view;
    const { from, to } = v.state.selection.main;
    return { line: v.state.doc.lineAt(from).number, text: v.state.sliceDoc(from, to) };
  });
  assert.deepStrictEqual(sel, { line: 4, text: "Y" }, "YEAR( on line 4");
});

test("the chip closes the panel, and Spark SQL has no chip", { skip }, async () => {
  await chip().click();
  assert.strictEqual(await panel().isHidden(), true);
  await page().locator(".sql-dialect").selectOption("spark");
  assert.strictEqual(await chip().isHidden(), true);
});
