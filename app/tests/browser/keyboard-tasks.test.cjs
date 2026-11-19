// The ten tasks people do most, done with the keyboard alone (see harness.cjs for how to run it): search with
// the command bar, open Settings, open a metadata project, run a notebook cell, delete a cell and put it back,
// publish a notebook, run SQL, export its result, drill into a run on Monitor, and cancel or confirm a dialog.
// Each test only sets up its data by code; every step a person takes is a key press (Tab, Shift+Tab, Enter,
// Space, Escape, arrows, typing or a shortcut), and the test checks what changed and where focus went.
// keyboard-pass.test.cjs walks every page's Tab order; this checks that the work itself can be done.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { playwright, openApp } = require("./harness.cjs");

let app;
const skip = !playwright && "Playwright is not installed";
const page = () => app.page;
const LH = { workspace_id: "w1", lakehouse_id: "l1", name: "LH_Bronze", workspace_name: "Sales Dev" };
const PIPELINE = fs.readFileSync(path.join(__dirname, "..", "..", "..", "sidecar", "tests", "pipelines", "reference_local.json"), "utf8");
const PIPELINE_MOCKS = { "Ingest orders": { output: { rowsCopied: 120 } }, Transform: { output: { exitValue: "ok" } } };
const PROJECT = fs.mkdtempSync(path.join(os.tmpdir(), "fd-kb-project-"));
const calls = (m) => app.calls.filter((c) => c.method === m);
const notebook = (name) => ({ name, cells: [
  { type: "code", cell_id: "c1", source: "", meta: {} },
  { type: "code", cell_id: "c2", source: "total = 1", meta: {} },
  { type: "markdown", cell_id: "m1", source: "# Orders", meta: {} }], lakehouse: LH });
const { execFileSync } = require("node:child_process");

before(async () => {
  if (skip) return;
  // A metadata project on disk, made the way Create project makes one, that the engine hasn't opened.
  execFileSync(process.env.FABRIC_DESKTOP_PYTHON || "python3", ["-c",
    "import sys; from pathlib import Path; from fabric_desktop.meta.templates import init_project; init_project(Path(sys.argv[1]), '3', '', False)", PROJECT],
    { cwd: path.join(__dirname, "..", "..", "..", "sidecar") });
  const ENGINE = ["app_settings", "save_app_settings", "start_cell", "poll_cell", "interrupt_cell", "kernel_variables",
      "sql_format", "sql_analyze", "sql_start", "sql_status", "sql_page", "sql_cancel", "sql_export", "sql_export_status",
      "pipeline_run_start", "pipeline_run_status", "pipeline_runs"];
  app = await openApp({
    // The metadata project's calls go to the engine too (meta_*), except Monitor's list of runs.
    engine: { length: ENGINE.length, includes: (m) => ENGINE.includes(m) || (m.startsWith("meta_") && m !== "meta_runs_all") },
    mocks: {
      environment: () => ({ python: "Python 3 (test)", user: "ana@contoso.com", tenant: "t1" }),
      list_workspaces: () => [{ id: "w1", displayName: "Sales Dev", capacityId: "c1" }, { id: "w2", displayName: "Finance", capacityId: "c1" }],
      list_lakehouses: () => [{ id: "l1", displayName: "LH_Bronze" }],
      list_notebooks: () => [{ name: "Orders" }, { name: "Daily sales" }],
      load_notebook: ({ name }) => notebook(name),
      save_notebook: () => ({ saved: true }),
      list_tables: () => ({ dbo: ["sales"] }),
      table_columns: () => [],
      cached_schemas: () => ({ tables: {}, columns: {} }),
      notebook_publish_check: () => ({ changed: false }),
      publish_notebook: ({ name }) => ({ item_id: "n1", name, created: true }),
      history: () => [], meta_runs_all: () => ({ runs: [] }), fabric_runs: () => [], pc_schedule_runs: () => [],
    },
  });
  await page().waitForSelector("#rail-main .rail-item");
  await page().waitForFunction(() => state.settings && "show_preview" in state.settings);
  app.errors.length = 0;
});

after(async () => { await app?.close(); fs.rmSync(PROJECT, { recursive: true, force: true }); });

// What has focus, in words: its accessible name, or its text.
const focused = () => page().evaluate(() => {
  const e = document.activeElement;
  return e ? (e.getAttribute("aria-label") || e.textContent || e.value || e.id || e.tagName).trim().replace(/\s+/g, " ") : "";
});

// Presses Tab (or Shift+Tab) until focus is on what `match` names: a CSS selector that matches it, or a
// regular expression its name matches. Fails if it never gets there.
async function tabTo(match, { back = false, max = 250 } = {}) {
  for (let i = 0; i < max; i++) {
    await page().keyboard.press(back ? "Shift+Tab" : "Tab");
    const hit = await page().evaluate(([src, isRe]) => {
      const e = document.activeElement;
      if (!e || e === document.body) return false;
      if (!isRe) return e.matches(src);
      const name = (e.getAttribute("aria-label") || e.textContent || e.value || "").trim().replace(/\s+/g, " ");
      return new RegExp(src).test(name);
    }, [match instanceof RegExp ? match.source : match, match instanceof RegExp]);
    if (hit) return;
  }
  assert.fail(`Tab never reached ${match}`);
}

// Starts a task where a person would: on Home, with focus at the top of the window.
async function fromHome() {
  await page().evaluate(() => { document.getElementById("palette")?.remove(); closeDialog(); go("home"); });
  await page().waitForSelector("#view .page");
  await page().evaluate(() => {
    const start = Object.assign(document.createElement("span"), { tabIndex: -1 });
    document.body.prepend(start);
    start.focus();
    start.remove();
  });
}

async function openNotebookByCode(name) {
  await page().evaluate((nb) => { loadIntoState(nb); go("notebook"); }, notebook(name));
  await page().waitForSelector(".cell .cm-content");
}

test("1. search: Ctrl+K, type a page's name, Enter opens it", { skip }, async () => {
  await fromHome();
  await page().keyboard.press("Control+k");
  await page().waitForSelector("#palette input");
  assert.match(await page().evaluate(() => document.activeElement.closest("#palette") ? "in" : "out"), /in/, "focus is in the command bar");
  await page().keyboard.type("Monitor");
  await page().keyboard.press("Enter");
  await page().waitForFunction(() => state.view === "monitor");
  assert.strictEqual(await page().locator("#palette").count(), 0, "the command bar closed");
  // Escape from a fresh command bar closes it and puts focus back.
  await page().keyboard.press("Control+k");
  await page().waitForSelector("#palette input");
  await page().keyboard.press("Escape");
  assert.strictEqual(await page().locator("#palette").count(), 0);
});

test("2. open Settings: Tab to Settings on the rail, Enter, focus moves to the page", { skip }, async () => {
  await fromHome();
  await tabTo(/^Settings$/);
  await page().keyboard.press("Enter");
  await page().waitForFunction(() => state.view === "settings");
  await page().waitForFunction(() => document.activeElement?.closest("#view"));  // a screen reader hears the new page
  // Each settings tab is reached and chosen from the keyboard.
  await tabTo("[role=tab]");
  const first = await focused();
  await page().keyboard.press("ArrowRight");
  assert.notStrictEqual(await focused(), first, "the arrow moved to the next tab");
});

test("3. open a project: type its folder and press Open", { skip }, async () => {
  await fromHome();
  await tabTo(/^Data mesh$/);
  await page().keyboard.press("Enter");
  // With no project open, the page offers Set up a project (or is the setup page itself, with Preview off).
  await page().waitForFunction(() => state.view === "metadata" && !/Loading the metadata project/.test(document.getElementById("view").textContent));
  if (!(await page().locator("#meta-open-path").count())) {
    await tabTo(/^Set up a project$/);
    await page().keyboard.press("Enter");
  }
  await page().waitForSelector("#meta-open-path");
  await tabTo("#meta-open-path");
  await page().keyboard.type(PROJECT);
  await tabTo(/^Open$/);
  await page().keyboard.press("Enter");
  await page().waitForFunction(() => !!state.meta.project);
  assert.strictEqual(calls("meta_open").at(-1).params.path, PROJECT);
});

test("4. run a notebook cell: Tab into the cell, type, Shift+Enter", { skip }, async () => {
  await openNotebookByCode("Orders");
  await fromHomeKeepingNotebook();
  await tabTo(".cm-content");  // the first cell, which is empty
  assert.match(await page().evaluate(() => document.activeElement.textContent), /^Write Python/, "an empty cell shows its placeholder");
  await page().keyboard.type("print(6 * 7)");
  await page().keyboard.press("Shift+Enter");
  await page().waitForFunction(() => state.notebook.cells.find((c) => c.source === "print(6 * 7)")?.status === "ok", null, { timeout: 60000 });
  const out = await page().evaluate(() => JSON.stringify(state.notebook.cells.find((c) => c.source === "print(6 * 7)").outputs));
  assert.match(out, /42/);
  assert.ok(calls("start_cell").some((c) => c.params.code.startsWith("print(6 * 7)")));
});

test("5. undo: in command mode D D deletes the cell and Z puts it back", { skip }, async () => {
  await openNotebookByCode("Orders");
  const count = () => page().evaluate(() => state.notebook.cells.length);
  const before = await count();
  await tabTo(".cm-content");
  await page().keyboard.press("Escape");  // command mode: the cell is selected, not edited
  await page().keyboard.press("d");
  await page().keyboard.press("d");
  await page().waitForFunction((n) => state.notebook.cells.length === n - 1, before);
  await page().keyboard.press("z");
  await page().waitForFunction((n) => state.notebook.cells.length === n, before);
});

test("6. publish a notebook: Tab to Publish, choose the workspace, Enter", { skip }, async () => {
  await openNotebookByCode("Daily sales");
  await page().evaluate(() => { state.notebook.workspace = null; });
  await fromHomeKeepingNotebook();
  await tabTo(/^Publish to OneLake$/);
  await page().keyboard.press("Enter");
  await page().waitForSelector("#dialog-root .dialog select");
  assert.ok(await page().evaluate(() => !!document.activeElement.closest("#dialog-root")), "focus is in the dialog");
  await tabTo("#dialog-root select");
  await page().keyboard.press("ArrowDown");  // Finance
  await tabTo(/^Publish$/);
  await page().keyboard.press("Enter");
  await waitFor(() => calls("publish_notebook").length === 1);
  assert.strictEqual(calls("publish_notebook")[0].params.workspace?.workspace_id, "w2", "published to the workspace chosen with the arrow");
});

async function fromHomeKeepingNotebook() {
  await page().evaluate(() => {
    const start = Object.assign(document.createElement("span"), { tabIndex: -1 });
    document.body.prepend(start);
    start.focus();
    start.remove();
  });
}

async function waitFor(check, ms = 10000) {
  for (const end = Date.now() + ms; Date.now() < end;) {
    if (check()) return;
    await new Promise((r) => setTimeout(r, 50));
  }
  assert.fail("timed out");
}

test("7 and 8. run SQL with Ctrl+Enter, then export the result from the Export menu", { skip }, async () => {
  await page().evaluate((lh) => openSqlQuery(lh), LH);
  await page().waitForSelector(".sql-edit .cm-editor");
  await fromHomeKeepingNotebook();
  await tabTo(".sql-edit .cm-content");
  await page().keyboard.press("Control+a");
  await page().keyboard.type("SELECT range AS n FROM range(25)");
  await page().keyboard.press("Control+Enter");
  await page().waitForFunction(() => document.querySelector(".sql-pill")?.textContent === "Succeeded", null, { timeout: 60000 });
  await page().waitForFunction(() => document.querySelectorAll(".sql-grid tbody tr").length === 25);
  // The editor keeps Tab for indenting: Escape, then Tab leaves it.
  await page().keyboard.press("Escape");
  await tabTo(".sql-export > summary");
  await page().keyboard.press("Enter");
  await page().waitForSelector(".sql-export[open] [role=menuitem]");
  await tabTo(/^CSV$/);
  await page().keyboard.press("Enter");
  await page().waitForFunction(() => /Saved 25 rows to/.test(document.querySelector(".toast")?.textContent || ""), null, { timeout: 30000 });
  const saved = (await page().locator(".toast").textContent()).match(/Saved 25 rows to (.+\.csv)/)[1];
  assert.strictEqual(fs.readFileSync(saved, "utf8").trim().split("\n").length, 26);
});

test("9. Monitor drilldown: Tab to a run and press Enter to open its activities", { skip }, async () => {
  const started = await page().evaluate(([text, mocks]) => rpc("pipeline_run_start", { text, mocks, wait_scale: 0 }), [PIPELINE, PIPELINE_MOCKS]);
  await page().evaluate(async (id) => {
    while (!["succeeded", "failed"].includes((await rpc("pipeline_run_status", { run_id: id, values: false })).state)) {
      await new Promise((done) => setTimeout(done, 50));
    }
  }, started.run_id);
  await page().evaluate(() => { monitorPage.panel = "runs"; });
  await fromHome();
  await tabTo(/^Monitor$/);
  await page().keyboard.press("Enter");
  await page().waitForSelector("tr.pl-run");
  await tabTo("tr.pl-run .pl-toggle");
  await page().keyboard.press("Enter");
  await page().waitForSelector("tr.pl-child");
  assert.strictEqual(await page().locator("tr.pl-run .pl-toggle").first().getAttribute("aria-expanded"), "true");
  await tabTo(/^Details of pl_reference_local$/);
  await page().keyboard.press("Enter");
  await page().waitForFunction(() => state.view === "pipelineRun");
});

test("10. a dialog: Escape cancels and returns focus, Enter on the danger button confirms", { skip }, async () => {
  await openNotebookByCode("Orders");
  await fromHomeKeepingNotebook();
  // confirmDialog is the confirm every delete and Prod write uses (meta.js), opened here from a focused button.
  const ask = () => page().evaluate(() => {
    window.__answer = undefined;
    const opener = document.activeElement;
    confirmDialog("Delete the lakehouse LH_Bronze?", "This removes its tables and files from Fabric.", "Delete", { danger: true })
      .then((ok) => { window.__answer = ok; window.__back = document.activeElement === opener; });
  });
  await tabTo(/^Publish to OneLake$/);
  await ask();
  await page().waitForSelector("#dialog-root .dialog");
  assert.ok(await page().evaluate(() => !!document.activeElement.closest("#dialog-root")), "focus is in the dialog");
  assert.notStrictEqual(await focused(), "Delete", "a danger dialog doesn't start on its danger button");
  await page().keyboard.press("Escape");
  await page().waitForFunction(() => window.__answer === false);
  assert.ok(await page().evaluate(() => window.__back), "focus went back to the button it came from");
  await ask();
  await page().waitForSelector("#dialog-root .dialog");
  await tabTo(/^Delete$/);
  await page().keyboard.press("Enter");
  await page().waitForFunction(() => window.__answer === true);
});

test("no task left an error in the console", { skip }, async () => {
  assert.deepStrictEqual(app.errors, []);
});
