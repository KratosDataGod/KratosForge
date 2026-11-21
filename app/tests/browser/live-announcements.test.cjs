// Long work says when it starts and when it ends to a screen reader (see harness.cjs for how to run it): running a
// notebook cell, Run all, a SQL query, an export, a publish and a pipeline run. What counts as said is what a
// screen reader is given: new words in the window's polite region (#announce, motion.js announce), new words in
// the toast while it is already showing (a status region), or an alert as it appears. Words put into a region
// that is hidden or drawn afresh at that moment aren't read, so they don't count here.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const { playwright, openApp } = require("./harness.cjs");

let app;
const skip = !playwright && "Playwright is not installed";
const page = () => app.page;
const LH = { workspace_id: "w1", lakehouse_id: "l1", name: "LH_Bronze", workspace_name: "Sales Dev" };
const PIPELINE = fs.readFileSync(path.join(__dirname, "..", "..", "..", "sidecar", "tests", "pipelines", "reference_local.json"), "utf8");
const PIPELINE_MOCKS = { "Ingest orders": { output: { rowsCopied: 120 } }, Transform: { output: { exitValue: "ok" } } };
const notebook = (name) => ({ name, cells: [
  { type: "code", cell_id: "c1", source: "x = 6 * 7", meta: {} },
  { type: "code", cell_id: "c2", source: "print(x)", meta: {} }], lakehouse: LH, workspace: { workspace_id: "w1", workspace_name: "Sales Dev" } });

before(async () => {
  if (skip) return;
  app = await openApp({
    engine: ["app_settings", "start_cell", "poll_cell", "interrupt_cell", "kernel_variables",
      "sql_format", "sql_analyze", "sql_start", "sql_status", "sql_page", "sql_cancel", "sql_export", "sql_export_status",
      "pipeline_import", "pipeline_compatibility", "pipeline_run_check", "pipeline_run_start", "pipeline_run_status", "pipeline_runs"],
    mocks: {
      environment: () => ({ python: "Python 3 (test)", user: "ana@contoso.com", tenant: "t1" }),
      list_workspaces: () => [{ id: "w1", displayName: "Sales Dev", capacityId: "c1" }],
      list_lakehouses: () => [{ id: "l1", displayName: "LH_Bronze" }],
      list_tables: () => ({ dbo: ["sales"] }),
      table_columns: () => [],
      cached_schemas: () => ({ tables: {}, columns: {} }),
      save_notebook: () => ({ saved: true }),
      notebook_publish_check: () => ({ changed: false }),
      publish_notebook: async ({ name }) => { await new Promise((r) => setTimeout(r, 300)); return { item_id: "n1", name, created: true }; },
    },
  });
  await page().waitForSelector("#rail-main .rail-item");
  // What a screen reader is given, in order (see the top of this file).
  await page().evaluate(() => {
    window.__said = [];
    const toast = document.getElementById("toast");
    let toastShown = !toast.hidden;
    new MutationObserver((list) => {
      for (const m of list) {
        const el = m.target.nodeType === 1 ? m.target : m.target.parentElement;
        if (el?.closest("#announce")) {
          const text = document.getElementById("announce").textContent;
          if (text && window.__said.at(-1) !== text) window.__said.push(text);
        }
      }
      if (!toast.hidden && toastShown && list.some((m) => toast.contains(m.target) && m.type !== "attributes")) window.__said.push(toast.textContent);
      if (!toast.hidden && !toastShown && toast.getAttribute("role") === "alert") window.__said.push(toast.textContent);
      toastShown = !toast.hidden;
    }).observe(document.body, { subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: ["hidden"] });
  });
  app.errors.length = 0;
});

after(async () => { await app?.close(); });

const said = () => page().evaluate(() => window.__said);
const hush = () => page().evaluate(() => { window.__said = []; });
// Until something said matches each pattern, in order.
async function heard(...patterns) {
  await page().waitForFunction((srcs) => {
    let i = 0;
    for (const s of window.__said) if (i < srcs.length && new RegExp(srcs[i]).test(s)) i++;
    return i === srcs.length;
  }, patterns.map((p) => p.source), { timeout: 60000 }).catch(async () => assert.fail(`not said in order: ${patterns.join(", ")}; said: ${JSON.stringify(await said())}`));
}

async function openNotebook() {
  await page().evaluate((nb) => { loadIntoState(nb); go("notebook"); }, notebook("Orders"));
  await page().waitForSelector(".cell .cm-content");
}

test("running a cell says it started and how it ended", { skip }, async () => {
  await openNotebook();
  await hush();
  await page().evaluate(() => runCell(state.notebook.cells[0]));
  await heard(/^Running cell 1$/, /^Cell 1 succeeded$/);
});

test("Run all says how many cells it runs and how it ended", { skip }, async () => {
  await openNotebook();
  await hush();
  await page().evaluate(() => runAll());
  await heard(/^Running 2 cells$/, /^Run finished: 2 cells succeeded$/);
});

test("a SQL query says it is running and how it ended, with its rows", { skip }, async () => {
  await page().evaluate((lh) => openSqlQuery(lh), LH);
  await page().waitForSelector(".sql-edit .cm-editor");
  await hush();
  await page().evaluate(() => {
    const v = sqlPage.editor.view;
    v.dispatch({ changes: { from: 0, to: v.state.doc.length, insert: "SELECT range AS n FROM range(25)" } });
    v.focus();
  });
  await page().keyboard.press("Control+Enter");
  await heard(/^Query running$/, /^Query succeeded: 25 rows$/);
  await hush();
  await page().evaluate(() => {
    const v = sqlPage.editor.view;
    sqlPage.tabs[sqlPage.active].dialect = "duckdb";  // DuckDB's error() fails the query here
    v.dispatch({ changes: { from: 0, to: v.state.doc.length, insert: "SELECT error('boom') AS n" } });
  });
  await page().keyboard.press("Control+Enter");
  await heard(/^Query running$/, /^Query failed$/);
});

test("an export says it started and where the file went", { skip }, async () => {
  await page().evaluate(() => {
    const v = sqlPage.editor.view;
    v.dispatch({ changes: { from: 0, to: v.state.doc.length, insert: "SELECT range AS n FROM range(25)" } });
  });
  await page().keyboard.press("Control+Enter");
  await page().waitForFunction(() => document.querySelector(".sql-pill")?.textContent === "Succeeded");
  await page().waitForFunction(() => document.querySelectorAll(".sql-grid tbody tr").length === 25);
  await page().evaluate(() => { document.getElementById("toast").hidden = true; });  // a quiet window, as after a while
  await hush();
  await page().locator(".sql-export > summary").click();
  await page().getByRole("menuitem", { name: "CSV", exact: true }).click();
  await heard(/^Exporting CSV$/, /^Saved 25 rows to .+\.csv/);
});

test("a publish says it started and that it finished", { skip }, async () => {
  await openNotebook();
  await page().evaluate(() => { document.getElementById("toast").hidden = true; });
  await hush();
  await page().evaluate(() => publishNotebook());
  await heard(/^Publishing Orders to Sales Dev/, /Orders/);
  const out = await said();
  assert.ok(out.length >= 2 && !/^Publishing/.test(out.at(-1)), `the end is said too: ${JSON.stringify(out)}`);
});

test("a pipeline run says it started and how it ended", { skip }, async () => {
  await page().evaluate((text) => openPipeline({ text }, { label: "This PC" }), PIPELINE);
  await page().waitForSelector("#view .page");
  await hush();
  // Mocks for its copy and notebook steps, kept as Run keeps them for next time, so Run can start here.
  await page().evaluate((mocks) => { plSave(plPage.pipeline.imp.name, { mocks }); return pipelineRunDialog(); }, PIPELINE_MOCKS);
  await page().waitForSelector("#dialog-root .dialog");
  await page().locator("#dialog-root .dialog button.primary").last().click();
  await heard(/run started$/, /run: (Succeeded|Failed|Cancelled|Interrupted) in /);
});

test("nothing it did left an error in the console", { skip }, async () => {
  assert.deepStrictEqual(app.errors, []);
});
