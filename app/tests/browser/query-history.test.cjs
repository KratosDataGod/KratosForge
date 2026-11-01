// WH-07: query history and Fabric query insights on the Warehouse page, in a real browser (see
// harness.cjs for how to run it). Runs are mocked: no warehouse, sign-in or Fabric is needed.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { playwright, openApp, sqlRunMocks } = require("./harness.cjs");

const WH = { kind: "warehouse", workspace_id: "ws1", workspace_name: "Sales Dev", item_id: "wh1", name: "WH_Gold" };
const SHOTS = process.env.SCREENSHOT_DIR;

let app;
const skip = !playwright && "Playwright is not installed";
const page = () => app.page;
const starts = () => app.calls.filter((c) => c.method === "sql_start").map((c) => c.params);

before(async () => {
  if (skip) return;
  const runs = sqlRunMocks((p) => {
    if (/DimCustomr/.test(p.query)) throw new Error("Invalid object name 'dbo.DimCustomr'.");
    if (/queryinsights/.test(p.query)) return { columns: [{ name: "number_of_runs", type: "int" }], rows: [[42]] };
    if (/sys\./.test(p.query)) return { columns: [{ name: "x", type: "int" }], rows: [] };
    return { columns: [{ name: "CustomerName", type: "varchar" }], rows: [["Contoso"], ["Fabrikam"]] };
  });
  app = await openApp({
    engine: ["sql_format", "sql_analyze", "sql_queries_list"],
    mocks: { ...runs, sql_start: (p) => ({ ...runs.sql_start(p), target: "warehouse" }) },
  });
  await page().evaluate((wh) => openWarehouseQuery(wh), WH);
  await page().waitForSelector(".warehouse-page .sql-edit .cm-editor");
  app.errors.length = 0;
});

after(async () => { await app?.close(); });

async function runSql(text) {
  await page().evaluate((text) => {
    const v = sqlPage.editor.view;
    v.dispatch({ changes: { from: 0, to: v.state.doc.length, insert: text }, selection: { anchor: text.length } });
  }, text);
  const n = starts().length;
  await page().getByRole("button", { name: "Run", exact: true }).click();
  const confirm = page().getByRole("dialog").getByRole("button", { name: "Run in Fabric" });
  if (await confirm.isVisible().catch(() => false)) await confirm.click();
  await page().waitForFunction((n) => !sqlPage.tabs[sqlPage.active].running && sqlPage.tabs[sqlPage.active].exec?.id && document.querySelectorAll(".sql-pill").length, n);
  await page().waitForFunction(() => !sqlPage.tabs[sqlPage.active].running);
}
const aside = () => page().locator(".warehouse-page aside.explorer");
const history = () => page().evaluate(() => JSON.parse(localStorage.getItem("fabric-desktop.sqlhistory.warehouse.wh1")) || []);

test("the History tab starts empty and offers Fabric's query insights", { skip }, async () => {
  await aside().getByRole("tab", { name: "History" }).click();
  await aside().getByText("No runs yet").waitFor();
  assert.match(await aside().textContent(), /Query insights from Fabric.*Long running.*Frequently run.*Recent requests/s);
});

test("each run is kept with its SQL, state, time and rows, newest first", { skip }, async () => {
  await runSql("-- top customers\nSELECT TOP 20 CustomerName FROM dbo.DimCustomer");
  await runSql("SELECT * FROM dbo.DimCustomr");
  const h = await history();
  assert.deepStrictEqual(h.map((e) => [e.state, e.rows]), [["failed", 0], ["completed", 2]]);
  assert.strictEqual(h[0].error, "Invalid object name '…'.", "an error's quoted values aren't kept");
  assert.strictEqual(h[1].sql, "-- top customers\nSELECT TOP 20 CustomerName FROM dbo.DimCustomer");
  await aside().locator(".wh-h-row").first().waitFor();
  const rows = await aside().locator(".wh-h-row").allTextContents();
  assert.match(rows[0], /SELECT \* FROM dbo\.DimCustomr.*Failed/);
  assert.match(rows[1], /SELECT TOP 20 CustomerName.*Succeeded.*2 rows/, "the first line that isn't a comment");
  assert.ok(rows.every((r) => !/ 0\.0 s/.test(r)), `a run too short to round reads <0.1s, like Monitor: ${rows}`);
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "wh-07-history.png") });
});

test("a run opens in a new tab without running, and the filter narrows the list", { skip }, async () => {
  const before = starts().length;
  const tabs = await page().evaluate(() => sqlPage.tabs.length);
  await aside().locator(".wh-h-row", { hasText: "TOP 20" }).click();
  await page().waitForFunction((n) => sqlPage.tabs.length === n + 1, tabs);
  assert.match(await page().evaluate(() => sqlPage.editor.text()), /^-- top customers/);
  assert.strictEqual(starts().length, before, "opening a past run runs nothing");
  // Typed a letter at a time, as a person does: each letter redraws the list, and the filter keeps
  // the focus (it lost it after the first letter, and a redraw mid-fill flaked this test in CI).
  const filter = aside().getByLabel("Filter history");
  await filter.click();
  await page().keyboard.type("cust");
  await page().evaluate(() => sqlPage.whRedraw());  // a run finishing or the catalog arriving
  await page().keyboard.type("omr");
  assert.strictEqual(await filter.inputValue(), "customr");
  assert.ok(await filter.evaluate((el) => el === document.activeElement), "the filter keeps the focus");
  assert.deepStrictEqual(await aside().locator(".wh-h-row .wh-h-sql").allTextContents(), ["SELECT * FROM dbo.DimCustomr"]);
  await filter.fill("");
});

test("query insights open in a new tab and run there", { skip }, async () => {
  const before = starts().length;
  await aside().getByRole("button", { name: "Frequently run" }).click();
  await page().waitForFunction((n) => !sqlPage.tabs[sqlPage.active].running && sqlPage.tabs[sqlPage.active].result, before);
  const ran = starts().slice(before).filter((p) => !p.query.includes("sys."));
  assert.deepStrictEqual(ran.map((p) => [p.target, p.query.replace(/\s+/g, " ")]),
    [["warehouse", "SELECT TOP (50) * FROM queryinsights.frequently_run_queries ORDER BY number_of_runs DESC;"]]);
  assert.strictEqual(await page().evaluate(() => sqlPage.tabs[sqlPage.active].name), "Frequently run");
});

test("every insights query has no errors under the Fabric Warehouse rules", { skip }, async () => {
  const out = await page().evaluate(async () => {
    const res = [];
    for (const [, , sql] of SQL_INSIGHTS) for (const target of ["warehouse", "endpoint"]) {
      const r = await rpc("sql_analyze", { query: sql, target });
      res.push(r.diagnostics.filter((d) => d.severity === "error").map((d) => d.rule));
    }
    return res;
  });
  assert.deepStrictEqual(out, [[], [], [], [], [], []]);
});

test("history is capped and Clear history asks first", { skip }, async () => {
  await page().evaluate(() => { for (let i = 0; i < 205; i++) sqlHistoryAdd(sqlPage.ref, { at: Date.now(), sql: `SELECT ${i}`, state: "completed", seconds: 0.1, rows: 1 }); });
  assert.strictEqual((await history()).length, 200);
  await page().evaluate(() => sqlPage.whRedraw());
  await aside().getByRole("button", { name: "Clear history" }).click();
  const confirm = page().getByRole("dialog", { name: "Clear history" });
  assert.match(await confirm.textContent(), /Remove the 200 runs kept for WH_Gold on this PC/);
  await confirm.getByRole("button", { name: "Clear" }).click();
  // Wait on each step's state, so a failure says which step didn't happen: the dialog closing, the
  // runs leaving storage, or the History tab redrawing (it once waited 30 s for "No runs yet" in CI).
  await confirm.waitFor({ state: "hidden" });
  await page().waitForFunction(() => !localStorage.getItem("fabric-desktop.sqlhistory.warehouse.wh1"));
  // This wait timed out once in CI with the runs already gone and nothing thrown (flaky ledger):
  // a timeout now says what the explorer showed instead.
  await page().waitForFunction(() => /No runs yet/.test(document.querySelector(".warehouse-page aside.explorer")?.textContent || ""), null, { timeout: 15000 })
    .catch(async (err) => {
      const seen = await page().evaluate(() => ({
        asides: document.querySelectorAll(".warehouse-page aside.explorer").length,
        view: sqlPage.whAside, filter: sqlPage.whHistoryFilter, tabs: sqlPage.tabs.length, active: sqlPage.active,
        running: sqlPage.tabs.map((t) => !!t.running), dialogs: document.querySelectorAll("[role=dialog]").length,
        text: (document.querySelector(".warehouse-page aside.explorer")?.textContent || "").slice(0, 300),
      }));
      throw new Error(`${err.message}; the explorer showed ${JSON.stringify(seen)}`);
    });
  assert.deepStrictEqual(await history(), []);
});

test("every warehouse's history shares one budget, and a full storage drops the oldest runs, not the new one", { skip }, async () => {
  const out = await page().evaluate(() => {
    const other = (n) => `fabric-desktop.sqlhistory.other-${n}`;
    const big = "x".repeat(SQL_HISTORY_SQL);
    const old = (n) => Array.from({ length: 15 }, (_, i) => ({ at: 1000 * n + 100 - i, sql: big, state: "completed" }));
    localStorage.setItem(other(1), JSON.stringify(old(1)));
    localStorage.setItem(other(2), JSON.stringify(old(2)));
    sqlHistoryAdd(sqlPage.ref, { at: Date.now(), sql: "SELECT 'budget'", state: "completed" });
    let total = 0;
    for (let i = 0; i < localStorage.length; i++) {
      const k = localStorage.key(i);
      if (k.startsWith("fabric-desktop.sqlhistory.")) total += localStorage.getItem(k).length;
    }
    const budget = { total, newest: sqlHistory(sqlPage.ref)[0].sql, one: sqlHistoryRead(other(1)).length, two: sqlHistoryRead(other(2)).length };
    // Fill the rest of this PC's storage, then add a run: the oldest go and the new run is kept.
    const fill = [];
    try { for (let i = 0; i < 2000; i++) { localStorage.setItem(`filler.${i}`, "y".repeat(100000)); fill.push(i); } } catch { /* full */ }
    try { for (let i = 0; i < 2000; i++) { localStorage.setItem(`filler.s${i}`, "y".repeat(1000)); fill.push(`s${i}`); } } catch { /* full to the last KB */ }
    let threw = null;
    try { sqlHistoryAdd(sqlPage.ref, { at: Date.now(), sql: `SELECT '${"z".repeat(5000)}'`, state: "completed" }); } catch (err) { threw = String(err); }
    const full = { threw, newest: sqlHistory(sqlPage.ref)[0].sql.length, kept: sqlHistoryRead(other(1)).length + sqlHistoryRead(other(2)).length };
    for (const i of fill) localStorage.removeItem(`filler.${i}`);
    localStorage.removeItem(other(1));
    localStorage.removeItem(other(2));
    return { budget, full };
  });
  assert.ok(out.budget.total <= 500000, `history stays in its budget (${out.budget.total})`);
  assert.strictEqual(out.budget.newest, "SELECT 'budget'");
  assert.ok(out.budget.one < 15 && out.budget.two === 15, "the oldest runs, in the other warehouse, went first");
  assert.strictEqual(out.full.threw, null);
  assert.strictEqual(out.full.newest, 5009, "the new run was kept");
  assert.ok(out.full.kept < out.budget.one + out.budget.two, "room was made from the oldest runs");
  await page().evaluate(() => sqlHistoryClear(sqlPage.ref));
});

test("nothing on the page threw", { skip }, async () => {
  assert.deepStrictEqual(app.errors, []);
});
