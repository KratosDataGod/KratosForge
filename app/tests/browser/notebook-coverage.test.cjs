// app/ui/notebook.js beyond the other notebook tests: each kind of output a cell can give (a JSON tree, a
// picture and a file that only says it is one, Markdown, a translation report, a type the app doesn't know,
// an error with its traceback), a cell stopped by the time or memory limit, the chart of a table result,
// Run all above and Run cell and below, Cancel on a cell stuck in a library call (in the engine and in its
// own process), Restart kernel, the cell's More menu, hidden code and moving between cells in command mode.
// The engine's calls are mocked (see harness.cjs for how to run it): start_cell answers by the cell's code.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp, startupReads } = require("./harness.cjs");

let app;
const skip = !playwright && "Playwright is not installed";
const page = () => app.page;
const PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==";
const TIME_LIMIT = "Stopped: the cell ran longer than the time limit set in Settings. Variables from earlier cells are kept.";
const MEMORY_LIMIT = "Stopped: the cell used more than the memory limit set in Settings. Variables were cleared.";

// What the kernel gives for each cell's code (notebook.py's output shapes).
const OUTPUTS = {
  json: [{ type: "json", data: { name: "sales", tags: ["a", "b"], nested: { deeper: { deepest: 1 } }, big: Array.from({ length: 503 }, (_, i) => i), none: null } }],
  img: [{ type: "image", mime: "image/png", data: PNG }],
  fakeimg: [{ type: "image", mime: "text/html", data: "<script>alert(1)</script>" }],
  md: [{ type: "markdown", text: "## Totals\nAll **good**" }],
  sf: [{ type: "translation", text: "Statement 1: translated\nStatement 2: unchanged" }],
  odd: [{ type: "hologram", value: 3 }],
  table: [{ type: "table", columns: ["region", "amount", "units"], types: ["str", "int", "int"],
    rows: [["North", 10, 1], ["South", 4, 2], ["North", 6, 3]], total_rows: 3 }],
  words: [{ type: "table", columns: ["region", "note"], types: ["str", "str"], rows: [["North", "a"], ["South", "b"], ["North", "c"]], total_rows: 3 }],
};
const ERRORS = {
  boom: [{ type: "error", ename: "ZeroDivisionError", evalue: "division by zero", traceback: "Traceback (most recent call last):\n  File \"<cell>\", line 1" }],
  slow: [{ type: "error", ename: "TimeLimit", evalue: TIME_LIMIT }],
  huge: [{ type: "error", ename: "KernelStopped", evalue: MEMORY_LIMIT }],
};

let hold = null;  // a promise poll_cell waits on, for a cell that stays running
let release = null;
const mocks = {
  save_notebook: () => ({ saved: true }),
  kernel_variables: () => [],
  start_cell: ({ code }) => {
    const key = code.trim();
    if (key === "stuck") return { status: "running", version: 1, outputs: [], execution_count: 1, seconds: 0 };
    if (ERRORS[key]) return { status: "error", outputs: ERRORS[key], execution_count: 1, seconds: 0.1 };
    return { status: "ok", outputs: OUTPUTS[key] || [{ type: "stream", text: `ran ${key}` }], execution_count: 1, seconds: 0.1 };
  },
  poll_cell: async () => { await hold; return { status: "ok", outputs: [{ type: "stream", text: "finally" }], execution_count: 1, seconds: 9 }; },
  interrupt_cell: () => ({ interrupted: true, stopped: false }),
  stop_kernel_process: () => ({ stopped: true, in_engine: false }),
  restart_kernel: () => ({ restarted: true }),
};

before(async () => {
  if (skip) return;
  app = await openApp({ mocks });
  await startupReads(app);
});
after(async () => { release?.(); await app?.close(); });

// Each case opens a notebook of its own cells.
async function openNotebook(sources, tab = "home") {
  await page().evaluate(({ sources: src, tab: t }) => {
    closeDialog(); closeMenu(); const toastEl = document.getElementById("toast"); toastEl.hidden = true; toastEl.textContent = "";
    state.kernel = "idle";
    loadIntoState({ name: `Cov ${Math.random().toString(36).slice(2, 8)}`, cells: src.map((s, i) => (typeof s === "string"
      ? { type: "code", cell_id: `c${i}`, source: s, meta: {} } : { cell_id: `c${i}`, meta: {}, ...s })) });
    state.ribbonTab = t;
    go("notebook");
  }, { sources, tab });
  await page().locator(".cell").first().waitFor();
}
beforeEach(() => { hold = null; });

const cellEl = (i) => page().locator(".cell").nth(i);
const callsFrom = (from, method) => app.calls.slice(from).filter((c) => c.method === method).map((c) => c.params);
const toastSays = (re) => page().waitForFunction((src) => new RegExp(src).test(document.getElementById("toast").textContent), re.source);
const waitStatus = (i, s) => page().waitForFunction(([n, want]) => document.querySelectorAll(".cell")[n]?.querySelector(".cell-status")?.dataset.s === want, [i, s]);
async function runAll() {
  await page().locator(".ribbon-body, .toolbar, body").getByRole("button", { name: "Run all", exact: true }).first().click();
}

test("Run all shows each kind of output a cell gives, and stops at an error with its traceback", { skip }, async () => {
  await openNotebook(["json", "img", "fakeimg", "md", "sf", "odd", "boom", "after"]);
  const from = app.calls.length;
  await runAll();
  await waitStatus(6, "error");
  assert.deepStrictEqual(callsFrom(from, "start_cell").map((p) => p.code.trim()), ["json", "img", "fakeimg", "md", "sf", "odd", "boom"],
    "the cell after the error doesn't run");

  const tree = cellEl(0).locator(".json-output");
  assert.match(await tree.locator(":scope > details > summary").innerText(), /\{5\}/);
  assert.match(await tree.innerText(), /name: "sales"/);
  assert.match(await tree.innerText(), /none: null/);
  assert.strictEqual(await tree.locator(".json-row.muted-text").textContent(), "3 more", "a long array shows the first 500");
  const deepest = tree.locator("details.json-node", { hasText: "deepest" }).last();
  assert.strictEqual(await deepest.evaluate((d) => d.open), false, "the third level starts folded");

  assert.strictEqual(await cellEl(1).locator("img.out-img").getAttribute("src"), `data:image/png;base64,${PNG}`);
  assert.match(await cellEl(2).locator(".output").innerText(), /This output says it is an image, but isn't one KratosForge shows\./);
  assert.strictEqual(await cellEl(2).locator("img").count(), 0);
  assert.strictEqual(await cellEl(3).locator(".output.md-view h2").innerText(), "Totals");
  assert.strictEqual(await cellEl(3).locator(".output.md-view strong").innerText(), "good");
  assert.strictEqual(await cellEl(4).locator(".translation-output pre").innerText(), "Statement 1: translated\nStatement 2: unchanged");
  assert.deepStrictEqual(JSON.parse(await cellEl(5).locator(".output pre").innerText()), { type: "hologram", value: 3 });
  const err = cellEl(6).locator(".output.error");
  assert.strictEqual(await err.locator(".ename").innerText(), "ZeroDivisionError: division by zero");
  await err.locator("summary", { hasText: "Traceback" }).click();
  assert.match(await err.locator("pre").innerText(), /most recent call last/);
  assert.strictEqual(await cellEl(7).locator(".cell-status").count(), 0, "the cell after it has no status");
  assert.deepStrictEqual(app.errors, []);
});

test("a cell stopped by the time limit runs again without it; one stopped by the memory limit opens Settings", { skip }, async () => {
  await openNotebook(["slow", "huge"], "run");
  const from = app.calls.length;
  await cellEl(0).hover();
  await cellEl(0).getByRole("button", { name: "Run cell" }).click();
  const stop = cellEl(0).locator(".limit-stop");
  await stop.waitFor();
  assert.strictEqual(await stop.locator("b").innerText(), "Stopped: the cell ran longer than the time limit set in Settings.");
  assert.match(await stop.innerText(), /Variables from earlier cells are kept\./);
  await stop.getByRole("button", { name: "Run again without the limit" }).click();
  for (const end = Date.now() + 8000; callsFrom(from, "start_cell").length < 2; await new Promise((r) => setTimeout(r, 25))) {
    if (Date.now() > end) throw new Error("the cell didn't run again");
  }
  assert.strictEqual(callsFrom(from, "start_cell")[0].time_limit, undefined);
  assert.strictEqual(callsFrom(from, "start_cell")[1].time_limit, false);
  await waitStatus(0, "error");

  await cellEl(1).hover();
  await cellEl(1).getByRole("button", { name: "Run cell" }).click();
  const mem = cellEl(1).locator(".limit-stop");
  await mem.waitFor();
  assert.strictEqual(await mem.getByRole("button", { name: "Run again without the limit" }).count(), 0, "memory can't be lifted for one run");
  await mem.getByRole("button", { name: "Open Settings" }).click();
  await page().waitForFunction(() => state.view === "settings");
  assert.deepStrictEqual(app.errors, []);
});

test("a table result's chart sums, averages and counts by the X axis, as bars, a line or dots", { skip }, async () => {
  await openNotebook(["table", "words"]);
  await runAll();
  await waitStatus(1, "ok");
  const out = cellEl(0).locator(".table-output");
  await out.getByRole("button", { name: "Chart", exact: true }).click();
  const svg = out.locator("svg.chart");
  await svg.waitFor();
  const opt = (label) => out.locator(".chart-opt", { hasText: label }).locator("select");
  assert.strictEqual(await svg.getAttribute("aria-label"), "bar chart of amount by region");
  assert.deepStrictEqual(await svg.locator("rect.chart-bar title").allTextContents(), ["North: 16", "South: 4"]);
  assert.match(await out.locator(".more").innerText(), /Chart of 3 rows/);

  await opt("Aggregate").selectOption("avg");
  await page().waitForFunction(() => document.querySelector(".table-output svg.chart rect title")?.textContent === "North: 8");
  await opt("Aggregate").selectOption("max");
  await page().waitForFunction(() => document.querySelector(".table-output svg.chart rect title")?.textContent === "North: 10");
  await opt("Aggregate").selectOption("min");
  await page().waitForFunction(() => document.querySelector(".table-output svg.chart rect title")?.textContent === "North: 6");
  await opt("Aggregate").selectOption("count");
  await page().waitForFunction(() => document.querySelector(".table-output svg.chart rect title")?.textContent === "North: 2");

  await opt("Chart").selectOption("line");
  await out.locator("svg.chart path.chart-line").waitFor();
  assert.deepStrictEqual(await out.locator("svg.chart circle.chart-dot title").allTextContents(), ["North: 2", "South: 1"]);
  // A line over a number axis is drawn in order of it.
  await opt("X axis").selectOption("units");
  await page().waitForFunction(() => [...document.querySelectorAll(".table-output svg.chart circle title")].map((t) => t.textContent).join() === "1: 1,2: 1,3: 1");

  await opt("Chart").selectOption("scatter");
  await opt("Values").selectOption("amount");
  await page().waitForFunction(() => document.querySelectorAll(".table-output svg.chart circle.chart-dot").length === 3);
  assert.strictEqual(await out.locator(".chart-opt", { hasText: "Aggregate" }).count(), 0, "dots aren't aggregated");
  // Dots need a number on both axes.
  await opt("X axis").selectOption("region");
  await out.locator("svg.chart text", { hasText: "Nothing to chart" }).waitFor();

  // A result with no number columns says so, until Count is picked.
  const words = cellEl(1).locator(".table-output");
  await words.getByRole("button", { name: "Chart", exact: true }).click();
  await words.getByText("This result has no number columns to chart. Pick Count to count rows by the X axis.").waitFor();
  await words.locator(".chart-opt", { hasText: "Aggregate" }).locator("select").selectOption("count");
  await page().waitForFunction(() => document.querySelectorAll(".cell")[1].querySelectorAll("svg.chart rect.chart-bar").length === 2);
  assert.deepStrictEqual(app.errors, []);
});

test("Run all above and Run cell and below run the cells on either side of the selected one", { skip }, async () => {
  await openNotebook(["x = 1", "x = 2", "x = 3"], "run");
  await cellEl(1).click();
  await page().waitForFunction(() => state.selected === state.notebook.cells[1].id);
  let from = app.calls.length;
  await page().getByRole("button", { name: "Run all above" }).click();
  await waitStatus(0, "ok");
  assert.deepStrictEqual(callsFrom(from, "start_cell").map((p) => p.code.trim()), ["x = 1"]);
  from = app.calls.length;
  await page().getByRole("button", { name: "Run cell and below" }).click();
  await waitStatus(2, "ok");
  assert.deepStrictEqual(callsFrom(from, "start_cell").map((p) => p.code.trim()), ["x = 2", "x = 3"]);
});

test("Cancel on a cell stuck in a library call offers Restart kernel, which stops the engine's kernel or the notebook's own process", { skip }, async () => {
  for (const ownProcess of [false, true]) {
    hold = new Promise((r) => { release = r; });
    await openNotebook(["stuck"], "run");
    await page().evaluate((own) => { state.settings ||= {}; state.settings.kernel_process = { value: own }; window.__fdShellCalls.length = 0; }, ownProcess);
    const from = app.calls.length;
    await cellEl(0).hover();
    await cellEl(0).getByRole("button", { name: "Run cell" }).click();
    await waitStatus(0, "running");
    const cancel = page().locator("[data-stop]").first();
    await page().waitForFunction(() => !document.querySelector("[data-stop]").disabled);
    await cancel.click();
    const d = page().locator("#dialog-root .dialog");
    await d.getByText("The cell is still running").waitFor();
    assert.match(await d.innerText(), /inside a library call that can't be interrupted/);
    assert.deepStrictEqual(callsFrom(from, "interrupt_cell").length, 1);
    await d.getByRole("button", { name: "Restart kernel" }).click();
    if (ownProcess) {
      for (const end = Date.now() + 8000; !callsFrom(from, "stop_kernel_process").length; await new Promise((r) => setTimeout(r, 25))) {
        if (Date.now() > end) throw new Error("stop_kernel_process was never called");
      }
      assert.strictEqual(await page().evaluate(() => window.__fdShellCalls.some((c) => c.cmd === "stop_kernel")), false,
        "the notebook's own process stops, not the engine");
    } else {
      await page().waitForFunction(() => window.__fdShellCalls.some((c) => c.cmd === "stop_kernel"));
      assert.deepStrictEqual(callsFrom(from, "stop_kernel_process"), []);
    }
    release();
    await waitStatus(0, "ok");
  }
  await page().evaluate(() => { state.settings.kernel_process = { value: false }; });
});

test("Restart kernel clears variables; while a proof uses the kernel it says to cancel the proof first", { skip }, async () => {
  await openNotebook(["x = 1"], "run");
  const from = app.calls.length;
  await page().getByRole("button", { name: "Restart kernel", exact: true }).click();
  await toastSays(/^Kernel restarted\. Variables were cleared\./);
  assert.strictEqual(callsFrom(from, "restart_kernel").length, 1);

  await page().evaluate(() => { state.notebook.prove = { record: { id: "p1", state: "pc" } }; });
  await page().getByRole("button", { name: "Restart kernel", exact: true }).click();
  await toastSays(/^A proof is using the kernel\. Cancel the proof first\./);
  assert.strictEqual(callsFrom(from, "restart_kernel").length, 1, "nothing restarts");
  await page().evaluate(() => { state.notebook.prove = null; });
});

test("the cell's More menu tags parameters, hides and clears output, copies, pastes, merges, freezes and finds", { skip }, async () => {
  await openNotebook(["a = 1\nb = 2", "c = 3"]);
  await runAll();
  await waitStatus(1, "ok");
  const more = async (i, item) => {
    await cellEl(i).hover();
    await cellEl(i).getByRole("button", { name: "More cell actions" }).click();
    await page().getByRole("menuitem", { name: item }).click();
  };

  await more(0, "Toggle parameter cell");
  await page().waitForFunction(() => isParams(state.notebook.cells[0]));
  await more(0, "Hide output");
  await page().waitForFunction(() => state.notebook.cells[0].meta.outputs_hidden === true);
  await more(0, "Show output");
  await page().waitForFunction(() => !state.notebook.cells[0].meta.outputs_hidden);
  await more(1, "Clear output");
  await page().waitForFunction(() => !document.querySelectorAll(".cell")[1].querySelector(".cell-status"));

  await more(1, "Copy cell");
  await more(1, "Paste cell below");
  await page().waitForFunction(() => state.notebook.cells.length === 3 && state.notebook.cells[2].source === "c = 3");
  await more(1, "Merge with cell below");
  await page().waitForFunction(() => state.notebook.cells.length === 2 && state.notebook.cells[1].source === "c = 3\nc = 3");

  await more(1, "Find in this cell");
  await cellEl(1).locator(".cm-search").waitFor();
  await page().keyboard.press("Escape");

  await more(0, "Freeze cell");
  await page().waitForFunction(() => state.notebook.cells[0].meta.frozen === true);
  await page().evaluate(() => enterEditMode(state.notebook.cells[0]));
  await toastSays(/^This cell is frozen\. Unfreeze it on the Edit tab to change it\./);
  await more(0, "Unfreeze cell");
  await page().waitForFunction(() => !state.notebook.cells[0].meta.frozen);
  assert.deepStrictEqual(app.errors, []);
});

test("hidden code shows its first line and opens on a click; in command mode the arrow keys and j move between cells", { skip }, async () => {
  await openNotebook(["\nfirst = 1\nsecond = 2", "x = 2", { type: "markdown", source: "Some *notes*" }]);
  await cellEl(0).hover();
  await cellEl(0).getByRole("button", { name: "Hide code" }).click();
  const hidden = cellEl(0).locator(".hidden-input");
  assert.strictEqual(await hidden.innerText(), "Code hidden (3 lines): first = 1");
  await hidden.click();
  await cellEl(0).locator(".cm-editor").waitFor();

  await page().evaluate(() => enterCommandMode(state.notebook.cells[0]));
  const selected = () => page().evaluate(() => state.notebook.cells.findIndex((c) => c.id === state.selected));
  await page().keyboard.press("ArrowDown");
  assert.strictEqual(await selected(), 1);
  await page().keyboard.press("j");
  assert.strictEqual(await selected(), 2);
  await page().keyboard.press("j");
  assert.strictEqual(await selected(), 2, "the last cell stays selected");
  await page().keyboard.press("ArrowUp");
  assert.strictEqual(await selected(), 1);

  // A Markdown cell opens for editing on a double click.
  await cellEl(2).locator(".md-view").dblclick();
  await cellEl(2).locator(".md-edit .cm-editor").waitFor();
  assert.deepStrictEqual(app.errors, []);
});
