// The SQL query page (query.js) beyond query-coverage and the other sql-* and query-* tests: the Messages view's
// lines for a refused or rolled-back change, the parameter text and .sql file line helpers, tabs read back with
// the file they came from, a DuckDB plan that waits and one that times out, a script's statement that ran with no
// rows and one that didn't run because the run was cancelled, Chart before any run, Save as table on a Prod
// workspace when the table appears while the name is typed, and on a warehouse: the run's band while Fabric finds
// the connection, Fabric's estimated plan once it waits and once it fails, Best practices that couldn't check, F12 before the
// objects are listed, and Allow changes when the engine can't read the script first.
// The engine's SQL calls are mocked with the shapes sql_exec.py answers with; nothing runs and nothing reaches Fabric.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
const LH = { workspace_id: "ws1", lakehouse_id: "lh3", name: "Sales" };
const WH = { kind: "warehouse", workspace_id: "ws1", workspace_name: "Sales Dev", item_id: "wh3", name: "WH_Gold" };
const T0 = 1795000000;  // engine times are seconds
const COLS = [{ name: "region", type: "VARCHAR" }];
const ROWS = [["East"], ["West"]];

// Runs kept by id; a query's text picks what it does. `waits` counts the status reads a run stays queued for.
const sql = { runs: new Map(), next: 1, hold: false, save: [] };
function start(params) {
  const id = `r3-${sql.next++}`;
  const q = params.query;
  const base = { id, created: T0, started: T0 + 1, ended: T0 + 2, seconds: 1, time_limit_minutes: 30,
    target: params.target === "warehouse" ? "warehouse" : "lakehouse" };
  let status, after = null, waits = 0;
  if (params.dry_run && /dry_fails/.test(q)) throw new Error("The engine couldn't read the script.");
  if (params.estimated_plan && /plan_waits/.test(q)) {
    status = { ...base, state: "queued", rows: 0, columns: [] }; waits = 1;
    after = { ...base, state: "completed", rows: 1, columns: [{ name: params.target === "warehouse" ? "Showplan" : "explain_value", type: "VARCHAR" }] };
  } else if (params.estimated_plan && /plan_times_out/.test(q)) {
    status = { ...base, state: "running", rows: 0, columns: [] }; waits = 1;
    after = { ...base, state: "timed_out", rows: 0, columns: [] };
  } else if (params.estimated_plan && /plan_fails/.test(q)) {
    status = { ...base, state: "queued", rows: 0, columns: [] }; waits = 1;
    after = { ...base, state: "failed", rows: 0, columns: [] };
  } else if (params.statements && /cancel_me/.test(q)) {
    status = { ...base, state: "cancelled", rows: 0, columns: [], parts: [{ statement: 1, columns: COLS, rows: 2, seconds: 0.01 }] };
  } else if (params.statements) {
    status = { ...base, state: "completed", rows: 2, columns: COLS, parts: [{ statement: 1, columns: COLS, rows: 2, seconds: 0.01 }] };
  } else if (/wh_waits/.test(q)) {
    status = { ...base, state: "queued", ended: null, seconds: 0, rows: 0, columns: [] }; waits = Infinity;
    after = { ...base, state: "completed", rows: 2, columns: COLS };
  } else {
    status = { ...base, state: "completed", rows: 2, columns: COLS };
  }
  sql.runs.set(id, { q, status, after, waits, params });
  return status;
}
function statusOf({ execution }) {
  const run = sql.runs.get(execution);
  if (run.after && (run.waits === Infinity ? !sql.hold : run.waits-- <= 0)) run.status = run.after;
  return run.status;
}
function pageOf({ execution, offset = 0 }) {
  const run = sql.runs.get(execution);
  if (run.status.columns?.[0]?.name === "explain_value") return { id: execution, offset, limit: 1000, rows: [[JSON.stringify({ steps: [] })]], columns: run.status.columns, total: 1, has_previous: false, has_next: false };
  if (run.status.columns?.[0]?.name === "Showplan") return { id: execution, offset, limit: 1000, rows: [["<ShowPlanXML/>"]], columns: run.status.columns, total: 1, has_previous: false, has_next: false };
  return { id: execution, offset, limit: 1000, rows: ROWS, columns: COLS, total: ROWS.length, has_previous: false, has_next: false };
}

let app;
const page = () => app.page;
const calls = (method) => app.calls.filter((c) => c.method === method).map((c) => c.params);
const band = () => page().locator(".sql-band");
const toastSays = (re) => page().waitForFunction((src) => new RegExp(src).test(document.getElementById("toast").textContent), re.source);
const pill = (text) => page().waitForFunction((t) => document.querySelector(".sql-pill")?.textContent === t
  && !sqlPage.tabs[sqlPage.active].running, text);

before(async () => {
  if (skip) return;
  app = await openApp({
    engine: ["sql_analyze", "sql_format"],
    storage: {
      // A tab kept on this PC with the workspace file it came from.
      "fabric-desktop.sql.lh3": JSON.stringify([
        { name: "From a file", sql: "SELECT 1", dialect: "duckdb", file: { name: "q.sql", place: "Queries" } },
        { name: "Bad file", sql: "SELECT 2", dialect: "duckdb", file: { name: 7 } },
      ]),
    },
    mocks: {
      readiness_check: () => [],
      list_tables: () => ({ dbo: ["orders"] }),
      table_columns: () => [{ name: "region", type: "VARCHAR" }],
      governance_practice_settings: () => ({ settings: {}, policies: [] }),
      sql_practices: () => { throw new Error("the practices reader isn't running"); },
      sql_start: start,
      sql_status: statusOf,
      sql_page: pageOf,
      sql_cancel: () => ({ cancelled: true }),
      // lakehouse.py save_sql: a Prod workspace asks for its typed name; the second answer finds the table there now.
      save_sql: (p) => { sql.save.push(p); return p.confirm_name ? { exists: true } : { prod: "Sales Prod", exists: false }; },
    },
  });
  await page().evaluate(() => {
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText: async () => {} } });
  });
});
after(async () => { sql.hold = false; if (app) await app.close(); });
beforeEach(() => { if (!skip) app.errors.length = 0; });

async function openLakehouse() {
  await page().evaluate((lh) => { closeDialog(); toastClose(); openSqlQuery(lh); }, LH);
  await page().waitForSelector(".sql-edit .cm-editor");
}
async function setDoc(text) {
  await page().evaluate((t) => {
    const v = sqlPage.editor.view;
    v.dispatch({ changes: { from: 0, to: v.state.doc.length, insert: t }, selection: { anchor: t.length } });
    v.focus();
  }, text);
}
const runIt = async () => { await page().locator(".sql-edit .cm-content").focus(); await page().keyboard.press("Control+Enter"); };

test("Messages, the parameter text and a .sql file's parameter line read what they're given", { skip }, async () => {
  const out = await page().evaluate(() => {
    const multi = { units: [{ n: 1, text: "UPDATE t SET a = 1", line: 1, known: true }, { n: 2, text: "SELECT 1", line: 2, known: false }], change: true };
    const ex = { state: "failed", rows: 0, columns: [], change: true, affected: 0, committed: false, error: "boom",
      refused: { statement: 2, label: "drops a table" }, parts: [{ statement: 1, affected: 3, seconds: 0.5 }] };
    return {
      messages: sqlMessages(ex, { multi }, "WH_Gold · in Fabric").textContent,
      none: sqlMessages(null, { error: "Error: the engine stopped" }, "x").textContent,
      text: sqlParamsText([{ name: "note", value: "x".repeat(45) }]),
      split: sqlFileSplit("-- KratosForge parameters: {not json\nSELECT 1"),
      label: sqlPartLabel(multi.units[0], ex, multi).text,
    };
  });
  assert.match(out.messages, /Change: 0 rows affected · nothing kept/);
  assert.match(out.messages, /Refused before it ran: statement 2 drops a table/);
  assert.match(out.messages, /Statement 1 \(line 1\): UPDATE · 3 rows rolled back · 0\.5 s/);
  assert.match(out.messages, /Message: boom/);
  assert.strictEqual(out.none, "the engine stopped");
  assert.strictEqual(out.text, `note = ${"x".repeat(40)}…`);
  assert.deepStrictEqual(out.split, { sql: "-- KratosForge parameters: {not json\nSELECT 1", params: null });
  assert.strictEqual(out.label, "1 UPDATE · 3 rows rolled back");
  assert.deepStrictEqual(app.errors, []);
});

test("tabs read back keep the file a tab came from, and drop one that isn't a file", { skip }, async () => {
  await openLakehouse();
  const tabs = await page().evaluate(() => sqlPage.tabs.map((t) => ({ name: t.name, file: t.file || null })));
  assert.deepStrictEqual(tabs.slice(0, 2), [{ name: "From a file", file: { name: "q.sql", place: "Queries" } }, { name: "Bad file", file: null }]);
  assert.deepStrictEqual(app.errors, []);
});

test("Chart before any run says to run a query; DuckDB's plan waits for its run, and says when it timed out", { skip }, async () => {
  await openLakehouse();
  await page().evaluate(() => { sqlPage.tabs[sqlPage.active].exec = null; sqlPage.redraw?.(); });
  await setDoc("SELECT region FROM orders");
  await band().getByRole("tab", { name: "Chart" }).click();
  await page().locator(".sql-grid", { hasText: "Run a query to chart its result. The chart reads the rows already on this PC; nothing runs again." }).waitFor();

  await setDoc("SELECT 'plan_waits' AS x");
  await band().getByRole("tab", { name: "Plan" }).click();
  await page().getByRole("button", { name: /^Show the plan/ }).click();
  await page().getByRole("button", { name: "Show the plan again" }).waitFor();
  assert.ok(calls("sql_status").some((s) => sql.runs.get(s.execution)?.params.estimated_plan));
  await setDoc("SELECT 'plan_times_out' AS x");
  await page().getByRole("button", { name: "Show the plan again" }).click();
  await page().locator(".sql-plan .output.error", { hasText: "The plan timed out." }).waitFor();
  await band().getByRole("tab", { name: "Results" }).click();
  assert.deepStrictEqual(app.errors, []);
});

test("a script's statement that gave no rows says so, and one after a cancelled run says it didn't run", { skip }, async () => {
  await openLakehouse();
  await setDoc("SELECT region FROM orders;\nSELECT 2 AS b");
  await runIt();
  await pill("Succeeded");
  await page().locator(".sql-rtabs [role=tab]").nth(1).click();
  await page().locator(".sql-grid", { hasText: "This statement ran and returned no rows." }).waitFor();

  await setDoc("SELECT 'cancel_me' AS a;\nSELECT 2 AS b");
  await runIt();
  await page().waitForFunction(() => !sqlPage.tabs[sqlPage.active].running && sqlPage.tabs[sqlPage.active].exec?.state === "cancelled");
  await page().locator(".sql-rtabs [role=tab]").nth(1).click();
  await page().locator(".sql-grid", { hasText: "This statement didn't run: the run stopped." }).waitFor();
  assert.deepStrictEqual(app.errors, []);
});

test("Save as table on a Prod workspace says when the table appeared while its name was typed, and writes nothing", { skip }, async () => {
  await openLakehouse();
  await setDoc("SELECT region FROM orders");
  sql.save = [];
  await page().getByRole("button", { name: "Save as table" }).click();
  const ask = page().getByRole("dialog", { name: "Save as table" });
  await ask.getByRole("textbox").fill("regions");
  await ask.getByRole("button", { name: "Save" }).click();
  const prod = page().getByRole("dialog", { name: "Write to Prod?" });
  await prod.waitFor();
  await prod.getByRole("textbox").fill("Sales Prod");
  await prod.getByRole("button", { name: "Save" }).click();
  await toastSays(/^Sales got a table called regions while you confirmed, so nothing was written\. Save again to replace it\.$/);
  assert.deepStrictEqual(sql.save.map((p) => [p.table, p.mode ?? null, p.confirm_name ?? null]), [["regions", null, null], ["regions", "new", "Sales Prod"]]);
  await page().evaluate(() => toastClose());
  assert.deepStrictEqual(app.errors, []);
});

test("on a warehouse: the band while Fabric finds the connection, the estimated plan, and Best practices that couldn't check", { skip }, async () => {
  await page().evaluate((wh) => { closeDialog(); toastClose(); openWarehouseQuery(wh); }, WH);
  await page().waitForSelector(".warehouse-page .sql-edit .cm-editor");
  // F12 before the warehouse's objects are listed says to list them.
  await setDoc("SELECT * FROM dbo.Sales");
  await page().keyboard.press("F12");
  await toastSays(/^List the warehouse's objects first: Explorer › List objects\.$/);
  await page().evaluate(() => toastClose());
  // With Allow changes ticked the engine reads the script first; when it can't, the tab says so and offers Retry.
  await page().evaluate((wh) => sqlChangesOn.add(sqlRefKey(wh)), WH);
  await setDoc("SELECT 'dry_fails' AS x");
  await page().getByRole("button", { name: "Run", exact: true }).click();
  await page().locator(".sql-grid", { hasText: "The engine couldn't read the script." }).first().waitFor();
  await page().getByRole("button", { name: "Retry" }).waitFor();
  assert.strictEqual(calls("sql_start").at(-1).dry_run, true);
  await page().evaluate((wh) => sqlChangesOn.delete(sqlRefKey(wh)), WH);
  sql.hold = true;
  await setDoc("SELECT 'wh_waits' AS x");
  await page().getByRole("button", { name: "Run", exact: true }).click();
  await page().getByRole("dialog").getByRole("button", { name: "Run in Fabric" }).click();
  await band().getByText("Waiting to start: finding the warehouse's SQL connection in Fabric, or another query is still running").waitFor();
  sql.hold = false;
  await pill("Succeeded");

  await setDoc("SELECT 'plan_waits' AS x");
  await page().getByRole("button", { name: "Estimated plan" }).click();
  await page().waitForFunction(() => sqlPage.tabs[sqlPage.active].plan?.xml === "<ShowPlanXML/>");
  await setDoc("SELECT 'plan_fails' AS x");
  await page().getByRole("button", { name: "Estimated plan" }).click();
  await page().waitForFunction(() => sqlPage.tabs[sqlPage.active].plan?.error === "The estimated plan failed.");

  const chip = page().locator(".sql-practices-chip");
  await page().waitForFunction(() => /check failed/.test(document.querySelector(".sql-practices-chip")?.textContent || ""));
  await chip.click();
  await page().locator("#sql-compat", { hasText: "Could not check the SQL: the practices reader isn't running" }).waitFor();
  await setDoc("");
  await page().waitForFunction(() => sqlPage.tabs[sqlPage.active].practices === null);
  // The panel closes once the findings change; what it holds says there's nothing to check.
  await page().waitForFunction(() => /Write T-SQL to check it\./.test(document.getElementById("sql-compat")?.textContent || ""));
  assert.deepStrictEqual(app.errors, []);
});
