// Try the fix beside a SQL best-practice finding on the Warehouse page (learn.js, GD-02, from the
// approved mockup): the engine's safe rewrite as a preview, Compare (both versions run read-only in
// Fabric after a confirm, here mocked) and Apply, one edit that Ctrl+Z undoes. The rewrites and the
// row comparison are tested in test_learn_fixes.py.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { playwright, openApp, sqlRunMocks, axeSource, axeFindings } = require("./harness.cjs");

const WH = { kind: "warehouse", workspace_id: "ws1", workspace_name: "Sales Dev", item_id: "wh1", name: "WH_Gold" };
const SQL = "SELECT region, SUM(amount) AS revenue\nFROM dbo.sales\nWHERE YEAR(order_date) = 2026\nGROUP BY region;";
const FIXED = "SELECT region, SUM(amount) AS revenue\nFROM dbo.sales\nWHERE (order_date >= '2026-01-01' AND order_date < '2027-01-01')\nGROUP BY region;";
const SHOTS = process.env.SCREENSHOT_DIR;
const skip = !playwright && "Playwright is not installed";

let app;
let hold = false;  // the next runs stay running until stopped
let compared = { result: "match", level: "complete", rows: { before: 4, after: 4 }, seconds: { before: 1.84, after: 0.21 }, limitations: [],
  runs: { before: "run-a", after: "run-b" } };
const page = () => app.page;
const panel = () => page().locator(".sql-compat");
const sq002 = () => panel().locator("[data-rule=SQ002]");
const starts = () => app.calls.filter((c) => c.method === "sql_start").map((c) => c.params);

before(async () => {
  if (skip) return;
  const runs = sqlRunMocks(() => ({ columns: [{ name: "region", type: "string" }], rows: [["n"]] }));
  app = await openApp({
    engine: ["app_settings", "save_app_settings", "sql_format", "sql_analyze", "sql_practices", "sql_queries_list",
      "governance_practice_settings", "learn_catalog", "learn_rule_guides", "learn_dismissals", "learn_fixes"],
    mocks: { ...runs, sql_start: (p) => ({ ...runs.sql_start(p), target: "warehouse", ...(hold ? { state: "running" } : {}) }),
      sql_status: (p) => ({ ...runs.sql_status(p), target: "warehouse", ...(hold ? { state: "running" } : {}) }),
      sql_cancel: ({ execution }) => ({ id: execution, state: "cancelling" }),
      learn_fix_compare: () => compared, learn_fix_checked: () => ({ recorded: true }) },
  });
  await page().waitForFunction(() => state.settings && "learn_mode" in state.settings);
  await page().evaluate((wh) => openWarehouseQuery(wh), WH);
  await page().waitForSelector(".warehouse-page .sql-edit .cm-editor");
  await page().evaluate((wh) => {
    // The explorer's catalog, as if loaded: Try the fix needs order_date's type.
    sqlPage.catalog = { key: sqlRefKey(wh), state: "done", objects: [{ schema: "dbo", name: "sales", type: "U" }],
      cols: { "dbo.sales": [{ name: "region", type: "varchar(20)" }, { name: "amount", type: "decimal(18,2)" }, { name: "order_date", type: "date" }] },
      at: new Date(), error: null };
  }, WH);
  await setDoc(SQL);
  await page().locator("button[data-practices]").click();
  await sq002().waitFor();
});

after(async () => { await app?.close(); });

async function setDoc(text) {
  await page().evaluate((text) => {
    const v = sqlPage.editor.view;
    v.dispatch({ changes: { from: 0, to: v.state.doc.length, insert: text }, selection: { anchor: 0 } });
  }, text);
  await page().waitForFunction((text) => sqlPage.tabs[sqlPage.active].practices && sqlPage.tabs[sqlPage.active].compatText === text, text, { timeout: 10000 });
}

test("Try the fix shows the rewrite as a preview and changes nothing", { skip }, async () => {
  await sq002().getByRole("button", { name: "Try the fix" }).click();
  const preview = sq002().locator(".learn-fix");
  await preview.waitFor();
  assert.strictEqual(await preview.locator("pre.before").innerText(), "YEAR(order_date) = 2026");
  assert.strictEqual(await preview.locator("pre.after").innerText(), "(order_date >= '2026-01-01' AND order_date < '2027-01-01')");
  assert.match(await preview.innerText(), /Preview · nothing has changed yet[\s\S]*order_date is a date column, so the range returns exactly the same rows[\s\S]*The principle/);
  assert.strictEqual(await page().evaluate(() => sqlPage.editor.text()), SQL);
  assert.strictEqual(await sq002().getByRole("button", { name: "Try the fix" }).getAttribute("aria-expanded"), "true");
  assert.strictEqual(await panel().locator("[data-rule=SQ001] button", { hasText: "Try the fix" }).count(), 0, "a rule without a safe fix has no button");
});

test("saying no to the confirm runs nothing", { skip }, async () => {
  const before = starts().length;
  await sq002().getByRole("button", { name: "Compare before applying" }).click();
  const dialog = page().getByRole("dialog");
  assert.match(await dialog.innerText(), /Run both versions in Fabric on WH_Gold\?[\s\S]*Both only read, and both use capacity/);
  await dialog.getByRole("button", { name: "Cancel" }).click();
  await sq002().getByRole("button", { name: "Compare before applying" }).waitFor();
  assert.strictEqual(starts().length, before);
});

test("Compare runs both versions read-only and shows same rows and measured times", { skip }, async () => {
  await sq002().getByRole("button", { name: "Compare before applying" }).click();
  await page().getByRole("dialog").getByRole("button", { name: "Run both" }).click();
  const result = sq002().locator(".learn-fix-result");
  await result.waitFor();
  const sent = starts().slice(-2);
  assert.deepStrictEqual(sent.map((p) => p.query), [SQL.replace(/;$/, ""), FIXED.replace(/;$/, "")]);
  assert.ok(sent.every((p) => p.target === "warehouse" && !p.allow && !p.confirm), "both only read");
  const said = await result.innerText();
  assert.match(said, /Compared in Fabric · read-only[\s\S]*Same rows[\s\S]*4 rows, every row compared/);
  assert.match(said, /Time · measured\s+1\.84 s\s+0\.21 s/);
  assert.match(said, /One run each, the fixed one second/);
  assert.strictEqual(await page().evaluate(() => sqlPage.editor.text()), SQL, "Compare changes nothing");
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "try-the-fix-compare.png") });
});

test("the preview has no serious axe findings", { skip: skip || (!axeSource && "axe-core is not installed") }, async () => {
  const found = await axeFindings(page(), ".sql-compat");
  assert.deepStrictEqual(found, []);
});

test("Apply is one edit: the finding goes, and Ctrl+Z brings the SQL back", { skip }, async () => {
  await sq002().getByRole("button", { name: "Apply", exact: true }).click();
  assert.strictEqual(await page().evaluate(() => sqlPage.editor.text()), FIXED);
  // GD-06: after a Compare that matched, Profile records the fix (the engine compares the two runs again).
  await page().getByText("Profile records it as fixed and checked.", { exact: false }).waitFor();
  assert.deepStrictEqual(app.calls.filter((c) => c.method === "learn_fix_checked").map((c) => c.params),
    [{ before: "run-a", after: "run-b", rule_id: "SQ002" }]);
  await page().waitForFunction((t) => sqlPage.tabs[sqlPage.active].compatText === t && sqlPage.tabs[sqlPage.active].practices, FIXED, { timeout: 10000 });
  await sq002().waitFor({ state: "detached" });
  await page().keyboard.press("Control+z");
  assert.strictEqual(await page().evaluate(() => sqlPage.editor.text()), SQL);
  await sq002().waitFor();
});

test("different rows say so, and say not to apply it yet", { skip }, async () => {
  compared = { ...compared, result: "differs", rows: { before: 4, after: 3 } };
  await sq002().getByRole("button", { name: "Try the fix" }).click();
  await sq002().getByRole("button", { name: "Compare before applying" }).click();
  await page().getByRole("dialog").getByRole("button", { name: "Run both" }).click();
  await sq002().locator(".learn-fix-result").waitFor();
  assert.match(await sq002().locator(".learn-fix-result").innerText(), /Different rows[\s\S]*4 rows now, 3 rows with the fix\. Don't apply it before finding out why/);
  await sq002().getByRole("button", { name: "Cancel" }).click();
  await sq002().locator(".learn-fix").waitFor({ state: "detached" });
});

test("Apply without a matching Compare records nothing in Profile (GD-06)", { skip }, async () => {
  const before = app.calls.filter((c) => c.method === "learn_fix_checked").length;
  await sq002().getByRole("button", { name: "Try the fix" }).click();
  await sq002().getByRole("button", { name: "Apply", exact: true }).click();   // no Compare this time
  assert.strictEqual(await page().evaluate(() => sqlPage.editor.text()), FIXED);
  await page().keyboard.press("Control+z");
  await page().waitForFunction((t) => sqlPage.tabs[sqlPage.active].compatText === t && sqlPage.tabs[sqlPage.active].practices, SQL, { timeout: 10000 });
  await sq002().getByRole("button", { name: "Try the fix" }).waitFor();
  await page().waitForTimeout(300);  // the fixes arrive after the findings: let that redraw finish
  assert.strictEqual(app.calls.filter((c) => c.method === "learn_fix_checked").length, before);
});

test("SQL changed since the check: Apply changes nothing", { skip }, async () => {
  await sq002().getByRole("button", { name: "Try the fix" }).click();
  await page().evaluate(() => { sqlPage.tabs[sqlPage.active].compatText = "stale"; });
  await sq002().getByRole("button", { name: "Apply", exact: true }).click();
  assert.strictEqual(await page().evaluate(() => sqlPage.editor.text()), SQL);
});

test("Stop ends a Compare that is still running, in Fabric too, and nothing is applied", { skip }, async () => {
  await page().evaluate(() => { sqlPage.tabs[sqlPage.active].compatText = sqlPage.editor.text(); });  // as checked
  hold = true;
  const before = starts().length;
  await sq002().getByRole("button", { name: "Compare before applying" }).click();
  await page().getByRole("dialog").getByRole("button", { name: "Run both" }).click();
  await sq002().getByRole("button", { name: "Stop" }).click();
  await sq002().getByRole("button", { name: "Compare before applying" }).waitFor();
  hold = false;
  assert.strictEqual(starts().length, before + 1, "the fixed version never started");
  const cancels = app.calls.filter((c) => c.method === "sql_cancel").map((c) => c.params.execution);
  assert.ok(cancels.length && cancels.every((id) => id === cancels[0]), "the running query was stopped");
  assert.strictEqual(await page().evaluate(() => sqlPage.editor.text()), SQL);
});
