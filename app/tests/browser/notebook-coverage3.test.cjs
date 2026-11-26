// app/ui/notebook.js beyond notebook-coverage and the other notebook tests: a small table result's search and sort,
// a whole-result viewer's Copy, value panel, empty and failed pages and a result the kernel no longer holds, the
// Inspect profile's notes, a running query's progress, a cell stopped while it ran, Run's guards (Fabric, a frozen
// cell, a cell running in another notebook), the Outline toggle with no headings, the lakehouse's Refresh, table
// names offered inside a Python string, the run keys in command mode and in a Markdown cell, Restart and run all,
// and Run stale cells after the memory limit. The engine's calls are mocked in notebook.py's and frames.py's shapes.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp, startupReads } = require("./harness.cjs");

let app;
const skip = !playwright && "Playwright is not installed";
const page = () => app.page;
const LH = { workspace_id: "w1", lakehouse_id: "l1", name: "LH_Sales", workspace_name: "Dev" };
const MEMORY_LIMIT = "Stopped: the cell used more than the memory limit set in Settings. Variables were cleared.";

const SMALL = { type: "table", columns: ["region", "amount"], types: ["str", "int"],
  rows: [["North", 10], ["South", null], ["North", 6], ["East", 4]], total_rows: 4 };
const FRAME = { type: "table", frame: "f1", columns: ["region", "note"], types: ["str", "str"],
  rows: [["North", "a\tb"], ["South", null]], total_rows: 2 };
const PROFILE = { rows: 2000, profiled_rows: 500, sampled: true, more_columns: 1, columns: [
  { name: "region", type: "str", error: "Couldn't profile this column." },
  { name: "ratio", type: "float", missing: 0, distinct: 3, infinite: 2, histogram: [1, 2], min: 0, max: 1, mean: 0.5, std: 0.1, q1: 0.2, median: 0.5, q3: 0.7 }] };

let hold = null;  // a promise poll_cell waits on, for a cell that stays running
let release = null;
let pollAnswer = null;
let framePage = null;
const mocks = {
  save_notebook: () => ({ saved: true }),
  kernel_variables: () => [],
  start_cell: ({ code }) => {
    const key = code.trim();
    if (key === "small") return { status: "ok", outputs: [SMALL], execution_count: 1, seconds: 0.1 };
    if (key === "frame") return { status: "ok", outputs: [FRAME], execution_count: 1, seconds: 0.1 };
    if (key === "query" || key === "dies") return { status: "running", version: 1, outputs: [], execution_count: 1, seconds: 0.5, progress: 42.7 };
    if (key === "huge") return { status: "error", outputs: [{ type: "error", ename: "KernelStopped", evalue: MEMORY_LIMIT }], execution_count: 1, seconds: 0.1 };
    return { status: "ok", outputs: [{ type: "stream", text: `ran ${key}` }], execution_count: 1, seconds: 0.1 };
  },
  poll_cell: async () => { await hold; return pollAnswer(); },
  restart_kernel: () => ({ restarted: true }),
  notebook_frame_page: (p) => framePage(p),
  notebook_frame_profile: ({ frame }) => ({ frame, ...PROFILE }),
  list_tables: () => ({ dbo: ["orders"] }),  // lakehouse.py: names by schema
  cached_schemas: () => ({ tables: {}, columns: {} }),
  notebook_dependencies: () => { throw new Error("the dependency reader isn't running"); },
};

before(async () => {
  if (skip) return;
  app = await openApp({ mocks });
  await startupReads(app);
  await page().evaluate(() => {
    window.__copied = [];
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText: async (t) => { window.__copied.push(t); } } });
  });
});
after(async () => { release?.(); await app?.close(); });

beforeEach(async () => {
  if (skip) return;
  hold = null;
  pollAnswer = () => ({ status: "ok", outputs: [{ type: "stream", text: "finally" }], execution_count: 1, seconds: 9 });
  framePage = ({ frame, offset = 0 }) => ({ frame, columns: FRAME.columns, types: FRAME.types, rows: FRAME.rows, offset, total: 2, all_rows: 2 });
  await page().evaluate(() => { window.__copied = []; });
  app.errors.length = 0;
});

// Each case opens a notebook of its own cells.
async function openNotebook(sources, { tab = "home", lakehouse = null } = {}) {
  await page().evaluate(({ sources: src, tab: t, lakehouse: lh }) => {
    closeDialog(); closeMenu(); toastClose(); const toastEl = document.getElementById("toast"); toastEl.hidden = true; toastEl.textContent = "";
    state.kernel = "idle";
    state.showExplorer = false;
    loadIntoState({ name: `Cov3 ${Math.random().toString(36).slice(2, 8)}`, lakehouse: lh, cells: src.map((s, i) => (typeof s === "string"
      ? { type: "code", cell_id: `c${i}`, source: s, meta: {} } : { cell_id: `c${i}`, meta: {}, ...s })) });
    state.ribbonTab = t;
    go("notebook");
  }, { sources, tab, lakehouse });
  await page().locator(".cell").first().waitFor();
}

const cellEl = (i) => page().locator(".cell").nth(i);
const toastSays = (re) => page().waitForFunction((src) => new RegExp(src).test(document.getElementById("toast").textContent), re.source);
const waitStatus = (i, s) => page().waitForFunction(([n, want]) => document.querySelectorAll(".cell")[n]?.querySelector(".cell-status")?.dataset.s === want, [i, s]);
const runCellAt = (i) => page().evaluate((n) => { const c = state.notebook.cells[n]; selectCell(c); return runCell(c); }, i);
const callsOf = (method) => app.calls.filter((c) => c.method === method).map((c) => c.params);

test("a small table result filters by search and sorts by a column, nulls last", { skip }, async () => {
  await openNotebook(["small"]);
  await runCellAt(0);
  await cellEl(0).getByRole("searchbox", { name: "Search rows" }).fill("north");
  await page().waitForFunction(() => /^2 matching of 4 rows$/.test(document.querySelector(".cell .more")?.textContent));
  await cellEl(0).getByRole("searchbox", { name: "Search rows" }).fill("");
  await page().waitForFunction(() => document.querySelector(".cell .more")?.textContent === "4 rows");
  const amounts = () => cellEl(0).locator("tbody tr td:nth-child(2)").allInnerTexts();
  const header = cellEl(0).locator("thead th").nth(1);
  await header.locator("button").first().click();
  assert.deepStrictEqual(await amounts(), ["4", "6", "10", "null"]);
  await header.locator("button").first().click();
  assert.deepStrictEqual(await amounts(), ["10", "6", "4", "null"]);
  assert.deepStrictEqual(app.errors, []);
});

test("a whole-result viewer copies its page, opens a value, and says when a page is empty, fails or is gone", { skip }, async () => {
  await openNotebook(["frame"]);
  await runCellAt(0);
  const cell = cellEl(0);
  await cell.locator(".result-range").waitFor();
  await cell.getByRole("button", { name: "Copy", exact: true }).click();
  await toastSays(/^Copied 2 rows\. Paste into Excel or a sheet\.$/);
  assert.deepStrictEqual(await page().evaluate(() => window.__copied), ["region\tnote\nNorth\ta b\nSouth\t"]);

  await cell.locator("tbody td").first().dblclick();
  const side = cell.locator(".sql-side");
  await side.waitFor();
  await side.getByRole("button", { name: "Copy", exact: true }).click();
  await toastSays(/^Copied the value$/);
  assert.strictEqual(await page().evaluate(() => window.__copied.at(-1)), "North");
  await side.locator("pre").press("Escape");
  await side.waitFor({ state: "detached" });
  await cell.locator("tbody td").first().dblclick();
  await side.getByRole("button", { name: "Close" }).click();
  await side.waitFor({ state: "detached" });

  framePage = ({ frame }) => ({ frame, columns: FRAME.columns, types: FRAME.types, rows: [], offset: 0, total: 0, all_rows: 2 });
  await cell.getByRole("searchbox", { name: "Search all rows" }).fill("zzz");
  await cell.locator(".result-range", { hasText: 'No rows match "zzz"' }).waitFor();

  framePage = () => { throw new Error("the kernel is busy"); };
  await cell.getByRole("searchbox", { name: "Search all rows" }).fill("yyy");
  await toastSays(/^Could not load rows: /);
  await page().evaluate(() => toastClose());

  framePage = ({ frame }) => ({ frame, gone: true });
  await cell.getByRole("searchbox", { name: "Search all rows" }).fill("xxx");
  await cell.locator(".result-range", { hasText: "Run the cell again to page, search or download: the kernel no longer holds this result." }).waitFor();
  assert.deepStrictEqual(app.errors, []);
});

test("Inspect over every row notes a column it couldn't profile, infinite values, a sample and columns left out", { skip }, async () => {
  await openNotebook(["frame"]);
  await runCellAt(0);
  await cellEl(0).getByRole("button", { name: "Inspect", exact: true }).click();
  const prof = cellEl(0).locator(".profile");
  await prof.getByText("Couldn't profile this column.").waitFor();
  const text = await prof.innerText();
  assert.match(text, /Infinite\s*2/);
  assert.match(text, /Profile of a random sample of 500 of 2,000 rows\. 1 more column isn't profiled; select fewer columns to see them\./);
  assert.deepStrictEqual(app.errors, []);
});

test("a running query shows its progress, and a cell stopped while it ran says variables are gone", { skip }, async () => {
  await openNotebook(["query", "dies"]);
  hold = new Promise((r) => { release = r; });
  const run = runCellAt(0);
  await cellEl(0).locator(".cell-progress").waitFor();
  assert.match(await cellEl(0).locator(".cell-progress").innerText(), /Query 42%/);
  release();
  await run;
  await waitStatus(0, "ok");

  hold = new Promise((r) => { release = r; });
  pollAnswer = () => { throw new Error("the kernel process ended"); };
  const run2 = runCellAt(1);
  await cellEl(1).locator(".cell-progress").waitFor();
  await page().evaluate(() => { state.notebook.stopRequested = true; });
  release();
  await run2;
  await waitStatus(1, "stopped");
  await cellEl(1).getByText("Stopped. The kernel restarted, so variables from earlier cells are gone.").waitFor();
  assert.deepStrictEqual(app.errors, []);
});

test("Run says why it won't: a Fabric notebook, a frozen cell, and a cell running in another notebook", { skip }, async () => {
  await openNotebook(["a", { type: "code", source: "b", meta: { frozen: true } }]);
  const from = app.calls.length;
  const result = await page().evaluate(async () => {
    state.notebook.compute = "fabric";
    const fabric = await runCell(state.notebook.cells[0]);
    const fabricToast = document.getElementById("toast").textContent;
    state.notebook.compute = "local";
    const frozen = await runCell(state.notebook.cells[1]);
    const frozenToast = document.getElementById("toast").textContent;
    const other = { name: "Busy one", kernelStatus: "busy", cells: [] };
    openNotebooks.set("__busy", other);
    let busy;
    try { busy = await runCell(state.notebook.cells[0]); } finally { openNotebooks.delete("__busy"); }
    return { fabric, fabricToast, frozen, frozenToast, busy, busyToast: document.getElementById("toast").textContent };
  });
  assert.deepStrictEqual(result, {
    fabric: false, fabricToast: "This notebook runs in Fabric, which runs the whole notebook. Use Run all, or switch Runs on to This PC to run one cell.",
    frozen: true, frozenToast: "This cell is frozen, so it doesn't run. Unfreeze it on the Edit tab.",
    busy: false, busyToast: "A cell is running in Busy one. Notebooks share one kernel unless Run notebooks in their own process is on in Settings.",
  });
  assert.strictEqual(app.calls.slice(from).filter((c) => c.method === "start_cell").length, 0);
  assert.deepStrictEqual(app.errors, []);
});

test("Outline with no headings says how to build one, and the button closes it again", { skip }, async () => {
  await openNotebook(["x = 1"], { tab: "view" });
  const outline = page().locator(".ribbon-body, .toolbar, body").getByRole("button", { name: "Outline", exact: true }).first();
  await outline.click();
  await page().getByText("Add Markdown headings (# Title) to build an outline.").waitFor();
  await outline.click();
  await page().locator(".explorer").waitFor({ state: "detached" });
  assert.deepStrictEqual(app.errors, []);
});

test("the lakehouse's Refresh forgets its tables, and table names are offered inside a Python string", { skip }, async () => {
  await openNotebook(["spark.table('')"], { lakehouse: LH });
  await page().evaluate(() => {
    state.nbTables.l1 = ["dbo.sales", "dbo.customers"];
    state.nbSchemas.l1 = ["dbo"];
    state.tree["l1:extra"] = { open: true };
    state.showExplorer = true; state.explorerTab = "data"; renderNotebook();
  });
  await page().locator(".explorer .lh-head").waitFor();
  await page().evaluate(() => { state.showExplorer = false; renderNotebook(); });

  await cellEl(0).locator(".cm-content").click();
  await page().evaluate(() => { const v = editors.get(state.notebook.cells[0].id); v.dispatch({ selection: { anchor: 13 } }); v.focus(); });
  await page().keyboard.press("Control+Space");
  const menu = page().locator(".cm-tooltip-autocomplete");
  await menu.waitFor();
  assert.match(await menu.innerText(), /dbo\.sales[\s\S]*dbo\.customers|dbo\.customers[\s\S]*dbo\.sales/);
  await page().keyboard.press("Escape");

  await page().evaluate(() => { state.showExplorer = true; state.explorerTab = "data"; renderNotebook(); });
  await page().locator(".explorer .lh-head").getByRole("button", { name: "Refresh" }).click();
  // The tables are asked for again: the lakehouse's names now, and the opened tree is forgotten.
  await page().waitForFunction(() => JSON.stringify(state.nbTables.l1) === '["dbo.orders"]');
  assert.strictEqual(await page().evaluate(() => "l1:extra" in state.tree), false);
  assert.ok(callsOf("list_tables").some((p) => p.lakehouse_id === "l1"));
  await page().evaluate(() => { state.showExplorer = false; renderNotebook(); });
  assert.deepStrictEqual(app.errors, []);
});

test("Shift, Ctrl and Alt+Enter run a cell in command mode, and Ctrl or Shift+Enter ends a Markdown edit", { skip }, async () => {
  await openNotebook(["one", "two", { type: "markdown", source: "# Notes" }]);
  const from = app.calls.length;
  await page().evaluate(() => enterCommandMode(state.notebook.cells[0]));
  await page().keyboard.press("Control+Enter");
  await waitStatus(0, "ok");
  await page().keyboard.press("Shift+Enter");
  await page().waitForFunction(() => state.selected === state.notebook.cells[1].id);
  await page().evaluate(() => enterCommandMode(state.notebook.cells[1]));
  await page().keyboard.press("Alt+Enter");
  await page().waitForFunction(() => state.notebook.cells.length === 4);
  assert.deepStrictEqual(app.calls.slice(from).filter((c) => c.method === "start_cell").map((c) => c.params.code.trim()), ["one", "one", "two"]);

  await page().evaluate(() => enterEditMode(state.notebook.cells.find((c) => c.type === "markdown")));
  await page().waitForFunction(() => state.notebook.cells.find((c) => c.type === "markdown").editing === true);
  await page().keyboard.press("Control+Enter");
  await page().waitForFunction(() => state.notebook.cells.find((c) => c.type === "markdown").editing === false);
  await page().evaluate(() => enterEditMode(state.notebook.cells.find((c) => c.type === "markdown")));
  await page().waitForFunction(() => state.notebook.cells.find((c) => c.type === "markdown").editing === true);
  await page().keyboard.press("Shift+Enter");
  await page().waitForFunction(() => state.notebook.cells.find((c) => c.type === "markdown").editing === false);
  assert.deepStrictEqual(app.errors, []);
});

test("Restart and run all restarts the kernel and runs every cell", { skip }, async () => {
  await openNotebook(["first", "second"], { tab: "run" });
  const from = app.calls.length;
  await page().locator(".ribbon-body, .toolbar, body").getByRole("button", { name: "Restart and run all", exact: true }).first().click();
  await waitStatus(1, "ok");
  const methods = app.calls.slice(from).map((c) => c.method).filter((m) => m === "restart_kernel" || m === "start_cell");
  assert.deepStrictEqual(methods, ["restart_kernel", "start_cell", "start_cell"]);
  assert.deepStrictEqual(app.errors, []);
});

test("after the memory limit, Run stale cells is offered with dependencies on, and says when they can't be read", { skip }, async () => {
  await openNotebook(["huge"]);
  await page().evaluate(() => { state.showDeps = true; });
  await runCellAt(0);
  const stale = cellEl(0).getByRole("button", { name: "Run stale cells" });
  await stale.waitFor();
  await stale.click();
  await toastSays(/Couldn't read the notebook's dependencies/);
  await page().evaluate(() => { toastClose(); state.showDeps = false; });
  assert.ok(callsOf("notebook_dependencies").length >= 1);
  assert.deepStrictEqual(app.errors, []);
});
