// The SQL query page (query.js) beyond the sql-* and query-* tests: the Explorer when tables can't be
// listed or a table's columns can't be read, a waiting and a running run's band, an engine call that
// fails and Retry, a result that is gone, a script whose second statement fails (its tabs, the arrow
// keys, Go to line once the SQL changed, Run from statement), the Messages view, History's Compare
// with latest and a result no longer on this PC, Export's other formats, a failed and a stopped
// export and one that needs the query run again, Open in notebook, Rename, Save, Format, a dialect
// that can't run here, the parameter chip's Change, and tabs read back from this PC's storage.
// The engine's SQL calls are mocked with the shapes sql_exec.py answers with; nothing runs.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
const LH = { workspace_id: "ws1", lakehouse_id: "lh1", name: "Sales" };
const T0 = 1795000000;  // engine times are seconds

// The SQL engine: runs are kept by id; a query's text picks what it does.
const sql = { fileVersion: null, runs: new Map(), next: 1, slow: "queued", startFails: 0, gone: new Set(), tablesFail: 1, exportMode: "ok", exportState: new Map() };
const COLS = [{ name: "region", type: "VARCHAR" }, { name: "orders", type: "BIGINT" }];
const ROWS = [["East", 120], ["West", 80], ["North", 5]];
function start(params) {
  const id = `run${sql.next++}`;
  const q = params.query;
  if (/fail_start/.test(q) && sql.startFails-- > 0) throw new Error("The SQL engine stopped while starting the query.");
  const base = { id, created: T0, started: T0 + 1, ended: T0 + 2, seconds: 1, time_limit_minutes: 30, target: "lakehouse" };
  let status;
  if (params.statements) {
    const bad = params.statements.findIndex((s) => /nope/.test(s));
    status = bad < 0
      ? { ...base, state: "completed", rows: 1, columns: [{ name: "b", type: "INTEGER" }],
        parts: params.statements.map((s, i) => (/^INSERT/.test(s) ? { statement: i + 1, affected: 4, columns: [{ name: "Count", type: "BIGINT" }], rows: 1, seconds: 0.01 }
          : { statement: i + 1, columns: [{ name: "b", type: "INTEGER" }], rows: 1, seconds: 0.01 })) }
      : { ...base, state: "failed", rows: 0, columns: [], failed_statement: bad + 1,
        error: "Catalog Error: Table with name nope does not exist!\nDid you mean \"orders\"?",
        parts: params.statements.slice(0, bad).map((s, i) => ({ statement: i + 1, columns: [{ name: "a", type: "INTEGER" }], rows: 1, seconds: 0.02 })) };
  } else if (params.estimated_plan) {
    status = /plan_fails/.test(q) ? { ...base, state: "failed", error: "Parser Error: syntax error at end of input", rows: 0, columns: [] }
      : { ...base, state: "completed", rows: 1, columns: [{ name: "explain_value", type: "VARCHAR" }] };
  } else if (/no_cols/.test(q)) {
    status = { ...base, state: "completed", rows: 0, columns: [] };
  } else if (/slow_orders/.test(q)) {
    status = { ...base, state: "queued", ended: null, seconds: 0, rows: 0, columns: [] };
  } else {
    status = { ...base, state: "completed", rows: ROWS.length, columns: COLS };
  }
  sql.runs.set(id, { q, status });
  return status;
}
function statusOf({ execution }) {
  if (sql.gone.has(execution)) throw new Error(`No run ${execution}.`);
  const run = sql.runs.get(execution);
  if (/slow_orders/.test(run.q) && run.status.state !== "completed") {
    run.status = sql.slow === "completed" ? { ...run.status, state: "completed", rows: ROWS.length, columns: COLS, seconds: 4, ended: T0 + 5 }
      : { ...run.status, state: sql.slow, seconds: 3, rows: sql.slow === "running" ? 1200 : 0 };
  }
  return run.status;
}
function pageOf({ execution, offset = 0, part }) {
  const run = sql.runs.get(execution);
  if (/rerun_me/.test(run.q)) return { rerun_needed: true, message: "This result was cleaned up from this PC." };
  if (run.status.columns?.[0]?.name === "explain_value") return { id: execution, offset, limit: 1000, rows: [[JSON.stringify({ steps: [] })]], columns: run.status.columns, total: 1, has_previous: false, has_next: false };
  if (/no_cols/.test(run.q)) return { id: execution, offset, limit: 1000, rows: [], columns: [], total: 0, has_previous: false, has_next: false };
  if (/two_pages/.test(run.q)) {
    const rows = offset ? ROWS.slice(2) : ROWS.slice(0, 2);
    return { id: execution, offset, limit: 2, rows, columns: COLS, total: 3, has_previous: offset > 0, has_next: !offset };
  }
  if (part) return { id: execution, offset, limit: 1000, rows: [[part]], columns: [{ name: "a", type: "INTEGER" }], total: 1, has_previous: false, has_next: false };
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
      // Two tabs kept on this PC: one with parameter values and a .sql file it came from, one with values that aren't kept.
      "fabric-desktop.sql.lh1": JSON.stringify([
        { name: "Orders", sql: "SELECT region, count(*) AS orders FROM orders GROUP BY region", dialect: "duckdb" },
        { name: "By day", sql: "SELECT * FROM orders WHERE day = :day", dialect: "duckdb",
          params: { day: { type: "date", value: "2026-11-01" }, "bad name": { type: "text", value: "x" }, region: { type: "nope", value: "East" } },
          disk: { path: "C:/Users/chris/sql/by-day.sql", name: "by-day.sql", version: "v1", saved: null } },
      ]),
    },
    mocks: {
      readiness_check: () => [],
      list_tables: () => {
        if (sql.tablesFail-- > 0) throw new Error("ConnectionError: OneLake didn't answer");
        return { dbo: ["orders"], staging: [] };
      },
      table_columns: () => { throw new Error("403: no access to orders"); },
      governance_practice_settings: () => ({ settings: {}, policies: [] }),
      sql_file_info: ({ path }) => ({ path, exists: !!sql.fileVersion, version: sql.fileVersion, changed: T0 }),
      sql_file_write: ({ path, expect }) => {
        if (expect && expect !== sql.fileVersion) return { conflict: true, name: "by-day.sql", message: "Another program changed by-day.sql since this tab read it." };
        sql.fileVersion = "v3";
        return { path, name: "by-day.sql", version: "v3", encoding: "utf-8", newline: "\r\n" };
      },
      sql_column_stats: () => { throw new Error("The result's snapshot can't be read."); },
      sql_start: start,
      sql_status: statusOf,
      sql_page: pageOf,
      sql_cancel: () => ({ cancelled: true }),
      learn_fix_compare: ({ before: b, after: a }) => (b === a ? { result: "match", rows: { before: 3, after: 3 } }
        : { result: "differ", rows: { before: 3, after: 1 } }),
      sql_pin: () => ({ pinned: true }),
      sql_export: ({ execution, format }) => {
        if (sql.exportMode === "rerun") { sql.exportMode = "ok"; return { rerun_needed: true, message: "The run's snapshot is gone." }; }
        const id = `x-${format}-${execution}`;
        sql.exportState.set(id, format === "xlsx" ? "throw" : format === "json" ? "hold" : "done");
        return { id, state: "running", format, rows: 0, total: 3 };
      },
      sql_export_status: ({ export: id }) => {
        const how = sql.exportState.get(id);
        if (how === "throw") throw new Error("The disk is full.");
        if (how === "hold") return { id, state: "running", format: "json", rows: 1, total: 3 };
        if (how === "cancelled") return { id, state: "cancelled", format: "json", rows: 1, total: 3 };
        return { id, state: "completed", format: "csv", rows: 3, total: 3, path: "C:/Users/chris/Downloads/Orders.csv", notes: ["Raw values: formulas aren't escaped."] };
      },
      sql_export_cancel: ({ export: id }) => { sql.exportState.set(id, "cancelled"); return { cancelled: true }; },
      new_notebook: () => ({ name: "Notebook 1", cells: [{ type: "code", source: "", meta: {} }] }),
      notebook_places: () => ({ places: [{ root: null, label: "This PC" }] }),
    },
  });
  await page().evaluate((lh) => openSqlQuery(lh), LH);
  await page().waitForSelector(".sql-edit .cm-editor");
  await page().evaluate(() => {
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText: async (t) => { window.__copied = t; } } });
  });
});
after(async () => { if (app) await app.close(); });

async function setDoc(text) {
  await page().evaluate((t) => {
    const v = sqlPage.editor.view;
    v.dispatch({ changes: { from: 0, to: v.state.doc.length, insert: t }, selection: { anchor: t.length } });
    v.focus();
  }, text);
}
const runIt = async () => { await page().locator(".sql-edit .cm-content").focus(); await page().keyboard.press("Control+Enter"); };

test("tabs come back from this PC with the parameter values that can be kept", { skip }, async () => {
  const tabs = await page().evaluate(() => sqlPage.tabs.map((t) => ({ name: t.name, dialect: t.dialect, params: t.params || null, disk: t.disk?.name || null })));
  assert.deepStrictEqual(tabs, [
    { name: "Orders", dialect: "duckdb", params: null, disk: null },
    { name: "By day", dialect: "duckdb", params: { day: { type: "date", value: "2026-11-01" } }, disk: "by-day.sql" }]);
});

test("tables that can't be listed say why; Refresh lists them, an empty schema says so, and unreadable columns fold back", { skip }, async () => {
  await toastSays(/^Could not list tables: /);
  await page().getByRole("button", { name: "Refresh" }).first().click();
  await page().locator(".explorer-content .tree-row", { hasText: "orders" }).first().waitFor();
  assert.match(await page().locator(".explorer-content").first().innerText(), /staging\s+No tables/);
  await page().getByRole("button", { name: "Show columns of orders" }).click();
  await page().getByRole("button", { name: "Show columns of orders" }).waitFor();  // drawn closed again: no columns to show
  assert.deepStrictEqual(calls("table_columns").at(-1), { lakehouse_ref: LH, table: "orders" });
});

test("Ctrl+S keeps the tab on this PC; Shift+Alt+F formats it; a double-click renames it", { skip }, async () => {
  await setDoc("select region,count(*) as orders from orders group by region");
  await page().keyboard.press("Control+s");
  await toastSays(/^Saved Orders$/);
  await page().keyboard.press("Shift+Alt+F");
  await page().waitForFunction(() => /^SELECT/.test(sqlPage.editor.text()));
  assert.ok(calls("sql_format").length >= 1);
  await page().locator(".sql-tabs [role=tab]", { hasText: "Orders" }).dblclick();
  const dialog = page().getByRole("dialog", { name: "Rename query" });
  await dialog.locator("input").fill("Orders by region");
  await dialog.getByRole("button", { name: "Rename" }).click();
  await page().locator(".sql-tabs [role=tab]", { hasText: "Orders by region" }).waitFor();
  const kept = await page().evaluate(() => JSON.parse(localStorage.getItem("fabric-desktop.sql.lh1")).map((t) => t.name));
  assert.deepStrictEqual(kept, ["Orders by region", "By day"]);
});

test("a run waits, then runs with its rows so far, then succeeds", { skip }, async () => {
  sql.slow = "queued";
  await setDoc("SELECT * FROM slow_orders");
  await runIt();
  await band().getByText("Waiting for another query to finish (Settings > Advanced > SQL queries sets how many run at once)").waitFor();
  sql.slow = "running";
  await band().getByText("3 s on this PC · 1,200 rows so far").waitFor();
  sql.slow = "completed";
  await pill("Succeeded");
  assert.strictEqual(await page().locator(".sql-grid tbody tr").count(), 3);
});

test("an engine call that fails says so, and Retry runs it again", { skip }, async () => {
  sql.startFails = 1;
  await setDoc("SELECT 'fail_start' AS x");
  await runIt();
  await page().locator(".sql-grid .output.error, .sql-grid", { hasText: "The SQL engine stopped while starting the query." }).first().waitFor();
  const before = calls("sql_start").length;
  await page().getByRole("button", { name: "Retry" }).click();
  await pill("Succeeded");
  assert.strictEqual(calls("sql_start").length, before + 1);
});

test("a result that is gone from this PC says so", { skip }, async () => {
  await setDoc("SELECT 'rerun_me' AS x");
  await runIt();
  await page().locator(".sql-grid", { hasText: "This result was cleaned up from this PC." }).waitFor();
});

test("a script whose second statement fails: its tabs, the arrow keys, Go to line, Run from statement and Messages", { skip }, async () => {
  const script = "SELECT 1 AS a;\nSELECT * FROM nope;\nSELECT 2 AS b";
  await setDoc(script);
  await runIt();
  const note = page().locator(".sql-stop-note");
  await note.waitFor();
  assert.match(await note.innerText(), /Statement 2 failed, so statement 3 didn't run\. Catalog Error: Table with name nope does not exist \(line 2\)\. Statement 1 finished; its result is in its tab\./);
  assert.match(await page().locator(".sql-grid").innerText(), /Did you mean "orders"\?/, "the whole error on the failed statement's tab");
  const tabs = page().locator(".sql-rtabs [role=tab]");
  assert.strictEqual(await tabs.count(), 3);
  await tabs.nth(1).focus();
  await page().keyboard.press("ArrowRight");
  await page().locator(".sql-grid", { hasText: "This statement didn't run, because statement 2 failed first." }).waitFor();
  await page().keyboard.press("Home");
  await page().waitForFunction(() => sqlPage.tabs[sqlPage.active].multi.part === 1);
  await page().locator(".sql-grid tbody tr").first().waitFor();
  await tabs.nth(0).focus();  // the page read redrew the tabs
  await page().keyboard.press("End");
  await page().waitForFunction(() => sqlPage.tabs[sqlPage.active].multi.part === 3);
  await page().keyboard.press("ArrowLeft");
  await page().waitForFunction(() => sqlPage.tabs[sqlPage.active].multi.part === 2);
  // Messages: what the run did, statement by statement.
  await band().getByRole("tab", { name: "Messages" }).click();
  const messages = await page().locator(".sql-messages").innerText();
  assert.match(messages, /^Target: Sales · on this PC\nQueued at .+\nStarted at .+\nEnded at .+ after 1 s\nState: Failed\n/);
  assert.match(messages, /Statement 1 \(line 1\): .* · 0\.02 s\nStatement 2 \(line 2\): .*\nStatement 3 \(line 3\): .*\nMessage: Catalog Error/);
  assert.match(messages, /Time limit: 30 minutes$/);
  await band().getByRole("tab", { name: "Results" }).click();
  // Once the SQL changes, Go to line can't find the statement.
  await setDoc(`-- changed\n${script}`);
  await note.getByRole("button", { name: "Go to line 2" }).click();
  await toastSays(/^The SQL changed since this run, so the statement's place isn't known\. Run it again to find it\.$/);
  await note.getByRole("button", { name: "Run from statement 2" }).click();
  await page().waitForFunction(() => sqlPage.tabs[sqlPage.active].multi?.units.length === 2 && !sqlPage.tabs[sqlPage.active].running);
  assert.deepStrictEqual(calls("sql_start").at(-1).statements, ["SELECT * FROM nope;", "SELECT 2 AS b"]);
});

test("History compares an earlier result with the latest, and says when one is gone", { skip }, async () => {
  await setDoc("SELECT region, orders FROM orders");
  await runIt();
  await pill("Succeeded");
  await band().getByRole("tab", { name: "History" }).click();
  const rows = page().locator(".sql-history-list tbody tr");
  await rows.first().waitFor();
  const n = await rows.count();
  assert.ok(n >= 4, `${n} runs in History`);
  // The run before the latest that left a result: the failing script's first statement.
  await rows.nth(1).locator("button.link-btn").click();
  await page().locator(".sql-old-note").waitFor();
  await page().locator(".sql-old-note").getByRole("button", { name: "Compare with latest" }).click();
  await toastSays(/^Different from the latest run: 3 rows then, 1 row in the latest\. Nothing ran\.$/);
  const compared = calls("learn_fix_compare").at(-1);
  assert.notStrictEqual(compared.before, compared.after);
  await page().locator(".sql-old-note").getByRole("button", { name: "Back to the latest" }).click();
  await page().locator(".sql-old-note").waitFor({ state: "detached" });
  // A result the engine no longer has.
  await band().getByRole("tab", { name: "History" }).click();
  const last = await page().evaluate(() => sqlPage.tabs[sqlPage.active].history.at(-1).id);
  sql.gone.add(last);
  await rows.last().locator("button.link-btn").click();
  await toastSays(/^That result is no longer on this PC: the engine was restarted, or it was cleaned up\. Run the query again\.$/);
  await band().getByRole("tab", { name: "Results" }).click();
});

test("Export: raw CSV says what it kept, Excel that fails, JSON stopped, and one that needs the query run again", { skip }, async () => {
  const menu = page().locator(".sql-export > summary");
  await menu.click();
  await page().getByRole("menuitem", { name: "CSV, Keep raw values" }).click();
  await toastSays(/^Saved 3 rows to C:\/Users\/chris\/Downloads\/Orders\.csv\. Raw values: formulas aren't escaped\.$/);
  assert.strictEqual(calls("sql_export").at(-1).raw, true);
  await menu.click();
  await page().getByRole("menuitem", { name: "Excel" }).click();
  await toastSays(/^Could not export: The disk is full\.$/);
  await menu.click();
  await page().getByRole("menuitem", { name: "JSON" }).click();
  await band().locator(".sql-export-progress", { hasText: "Exporting JSON: 1 of 3 rows" }).waitFor();
  await band().getByRole("button", { name: "Cancel" }).click();
  await toastSays(/^Export stopped\. No file was left behind\.$/);
  // The snapshot is gone: asked, the query runs again, then the export runs.
  sql.exportMode = "rerun";
  const runs = calls("sql_start").length;
  await menu.click();
  await page().getByRole("menuitem", { name: "Parquet" }).click();
  const confirm = page().getByRole("dialog", { name: "Run the query again?" });
  assert.match(await confirm.innerText(), /The run's snapshot is gone\. Running it again reads the tables as they are now/);
  await confirm.getByRole("button", { name: "Run again" }).click();
  await toastSays(/^Saved 3 rows to /);
  assert.strictEqual(calls("sql_start").length, runs + 1);
  assert.strictEqual(calls("sql_export").at(-1).format, "parquet");
});

test("the parameter chip's Change asks again, and Enter in a value runs with it", { skip }, async () => {
  await page().locator(".sql-tabs [role=tab]", { hasText: "By day" }).click();
  await page().waitForFunction(() => sqlPage.tabs[sqlPage.active].name === "By day");
  const chip = page().locator(".sql-param-chip");
  await chip.waitFor();
  assert.match(await chip.innerText(), /^1 parameter: day = 2026-11-01/);
  await chip.getByRole("button", { name: "Change" }).click();
  const value = page().getByLabel("Value of day");
  await value.fill("2026-11-02");
  await value.press("Enter");
  await pill("Succeeded");
  assert.deepStrictEqual(calls("sql_start").at(-1).params, [{ name: "day", type: "date", value: "2026-11-02" }]);
  assert.match(await chip.innerText(), /day = 2026-11-02/);
});

test("a T-SQL tab on a lakehouse can't run here and says so", { skip }, async () => {
  await page().locator(".sql-tabs [role=tab]", { hasText: "Orders by region" }).click();
  await page().evaluate(() => { sqlPage.tabs[sqlPage.active].dialect = "warehouse"; renderQuery(); });
  await page().waitForSelector(".sql-edit .cm-editor");
  assert.ok(await page().getByRole("button", { name: "Run", exact: true }).isDisabled());
  const before = calls("sql_start").length;
  await runIt();
  await toastSays(/^Fabric Warehouse \(T-SQL\) can't run on this PC\. Pick Spark SQL or DuckDB to run here\.$/);
  assert.strictEqual(calls("sql_start").length, before);
});

test("Open in notebook makes a notebook that reads the query", { skip }, async () => {
  await page().evaluate(() => { sqlPage.tabs[sqlPage.active].dialect = "spark"; renderQuery(); });
  await page().waitForSelector(".sql-edit .cm-editor");
  await setDoc('SELECT """x""" AS q FROM orders');
  await page().getByRole("button", { name: "Open in notebook" }).click();
  await page().waitForFunction(() => state.view === "notebook" && state.notebook?.name === "Notebook 1");
  const source = await page().evaluate(() => state.notebook.cells[0].source);
  assert.strictEqual(source, 'import polars as pl\nfrom fabric_desktop import lakehouse\n\ndf = lakehouse.sql("""\nSELECT \\"\\"\\"x\\"\\"\\" AS q FROM orders\n""")\ndf\n');
  assert.ok(await page().evaluate(() => state.notebook.dirty));
  assert.deepStrictEqual(app.errors, []);
});

// ---------- a second round on the same page ----------

test("the Keyboard shortcuts dialog lists the SQL page's keys", { skip }, async () => {
  await page().evaluate(() => openSqlQuery({ workspace_id: "ws1", lakehouse_id: "lh1", name: "Sales" }));
  await page().waitForSelector(".sql-edit .cm-editor");
  await page().locator(".sql-band").click({ position: { x: 2, y: 2 } });
  await page().keyboard.press("?");
  const dialog = page().locator("#dialog-root .dialog[aria-label='Keyboard shortcuts']");
  await dialog.waitFor();
  const text = await dialog.innerText();
  for (const line of ["Run only the statement the cursor is in (or the selected text)", "In the editor: go to a line",
    "On a query tab: close it (asks first when it has SQL of its own)"]) assert.ok(text.includes(line), line);
  await page().keyboard.press("Escape");
  await dialog.waitFor({ state: "detached" });
});

test("a cell's menu opens its value; Column statistics that can't be read says why; Esc closes the side", { skip }, async () => {
  await page().locator(".sql-tabs [role=tab]", { hasText: "Orders by region" }).click();
  await page().waitForSelector(".sql-edit .cm-editor");
  await setDoc("SELECT region, orders FROM orders");
  await runIt();
  await pill("Succeeded");
  const cell = page().locator(".sql-grid tbody tr").first().locator("td").nth(1);
  await cell.click();
  await cell.click({ button: "right" });
  await page().getByRole("menuitem", { name: /^Open value/ }).click();
  await page().locator(".sql-side-box").getByText("East").first().waitFor();
  await cell.click({ button: "right" });
  await page().getByRole("menuitem", { name: "Column statistics" }).click();
  await page().locator(".sql-side-box", { hasText: "The result's snapshot can't be read." }).waitFor();
  await page().locator(".sql-side-box").press("Escape");
  await page().locator(".sql-side-box").waitFor({ state: "hidden" });
});

test("Chart reads every page of the result; a run with no columns says so", { skip }, async () => {
  await setDoc("SELECT region, orders FROM two_pages");
  await runIt();
  await pill("Succeeded");
  const reads = calls("sql_page").length;
  await band().getByRole("tab", { name: "Chart" }).click();
  await page().locator(".sql-grid svg, .sql-grid canvas, .sql-grid .chart").first().waitFor();
  const pages = calls("sql_page").slice(reads).map((c) => c.offset);
  assert.deepStrictEqual(pages, [0, 2], "the chart read both pages");
  await band().getByRole("tab", { name: "Results" }).click();
  await setDoc("SELECT no_cols");
  await runIt();
  await pill("Succeeded");
  await page().locator(".sql-grid", { hasText: "The query ran and returned no columns." }).waitFor();
});

test("a statement that changes rows says how many on its tab", { skip }, async () => {
  await setDoc("INSERT INTO t SELECT * FROM orders;\nSELECT 2 AS b");
  await runIt();
  await page().locator(".sql-rtabs [role=tab]", { hasText: "1 INSERT · 4 rows changed" }).waitFor();
  assert.deepStrictEqual(await page().locator(".sql-rtabs [role=tab]").allInnerTexts(), ["1 INSERT · 4 rows changed", "2 SELECT · 1 row"]);
});

test("Plan shows DuckDB's plan, and says why when it can't be worked out", { skip }, async () => {
  await setDoc("SELECT region FROM orders");
  await band().getByRole("tab", { name: "Plan" }).click();
  await page().getByRole("button", { name: "Show the plan" }).click();
  await page().getByRole("button", { name: "Show the plan again" }).waitFor();
  assert.strictEqual(calls("sql_start").at(-1).estimated_plan, true);
  await setDoc("SELECT plan_fails FROM");
  await page().getByRole("button", { name: "Show the plan again" }).click();
  await page().locator(".sql-plan .output.error", { hasText: "Parser Error: syntax error at end of input" }).waitFor();
  await band().getByRole("tab", { name: "Results" }).click();
});

test("History pins and unpins a result; a new tab has none; closing a tab lets its pinned results go", { skip }, async () => {
  await band().getByRole("tab", { name: "History" }).click();
  const first = page().locator(".sql-history-list tbody tr").first();
  await first.getByRole("button", { name: "Pin" }).click();
  await first.getByRole("button", { name: "Unpin" }).waitFor();
  assert.match(await first.innerText(), /● Pinned/);
  await first.getByRole("button", { name: "Unpin" }).click();
  await first.getByRole("button", { name: "Pin" }).waitFor();
  await first.getByRole("button", { name: "Pin" }).click();
  await first.getByRole("button", { name: "Unpin" }).waitFor();
  const pinned = await page().evaluate(() => sqlPage.tabs[sqlPage.active].history.find((e) => e.pinned).id);
  // A new tab: no results yet; clicking a table in the Explorer writes its query there and runs it.
  await page().getByRole("button", { name: "New query" }).click();
  await page().waitForSelector(".sql-edit .cm-editor");
  await band().getByRole("tab", { name: "History" }).click();
  await page().locator(".sql-grid", { hasText: "No results yet. Each run's result is kept here, on this PC, until you close this tab." }).waitFor();
  await band().getByRole("tab", { name: "Results" }).click();
  await page().locator(".explorer-content button.tree-row[title='Query orders']").click();
  await pill("Succeeded");
  assert.strictEqual(await page().evaluate(() => sqlPage.editor.text()), "SELECT *\nFROM orders\nLIMIT 100;");
  // Close the tab that pinned a result: the engine may let it go.
  await page().locator(".sql-tabs [role=tab]", { hasText: "Orders by region" }).click();
  await page().waitForSelector(".sql-edit .cm-editor");
  await page().locator(".sql-tabs [role=tab]", { hasText: "Orders by region" }).press("Delete");
  await page().getByRole("dialog", { name: "Close Orders by region?" }).getByRole("button", { name: "Close query" }).click();
  await page().locator(".sql-tabs [role=tab]", { hasText: "Orders by region" }).waitFor({ state: "detached" });
  assert.deepStrictEqual(calls("sql_pin").at(-1), { execution: pinned, pinned: false });
});

test("a .sql file another program changed: keep the tab, then Save over the file only after asking", { skip }, async () => {
  sql.fileVersion = "v2";
  await page().locator(".sql-tabs [role=tab]", { hasText: "By day" }).click();
  const note = page().locator(".sql-disk-note");
  await note.getByText("by-day.sql changed on this PC").waitFor();
  assert.match(await note.innerText(), /\(another program saved it at .+\)\. Your tab has (no )?unsaved changes/);
  await note.getByRole("button", { name: "Keep this tab as it is" }).click();
  await page().waitForFunction(() => sqlPage.tabs[sqlPage.active].disk.version === "v2" && sqlPage.tabs[sqlPage.active].disk.saved === null);
  // Someone saves it again before this tab does: Save asks before writing over it.
  sql.fileVersion = "v2b";
  await page().locator(".sql-tabs [role=tab]", { hasText: "By day" }).press("Delete");
  const ask = page().getByRole("dialog", { name: "Save changes to by-day.sql?" });
  await ask.getByRole("button", { name: "Save", exact: true }).click();
  const conflict = page().getByRole("dialog", { name: "Save by-day.sql?" });
  assert.match(await conflict.innerText(), /Another program changed by-day\.sql since this tab read it\. Saving writes this tab over the file/);
  await conflict.getByRole("button", { name: "Save anyway" }).click();
  await toastSays(/^Saved by-day\.sql$/);
  await page().locator(".sql-tabs [role=tab]", { hasText: "By day" }).waitFor({ state: "detached" });
  const writes = calls("sql_file_write");
  assert.deepStrictEqual(writes.map((w) => [w.expect, w.overwrite]), [["v2", false], ["", true]]);
  assert.strictEqual(writes[1].text, "-- KratosForge parameters: {\"day\":{\"type\":\"date\",\"value\":\"2026-11-02\"}}\nSELECT * FROM orders WHERE day = :day");
  assert.deepStrictEqual(app.errors, []);
});
