// Breakpoints and stepping (Prove stage 5, item 44) against the real engine: a breakpoint set from
// the line number gutter pauses the cell in a function, the panel shows that frame, Evaluate runs
// in it, the keys step, and Continue finishes the cell. Debug from here carries stepping into the
// next cell, and Continue lets the cell after that run to its breakpoints only. A notebook on
// another engine says it can't pause. No lakehouse is attached.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { playwright, openApp } = require("./harness.cjs");

const SHOTS = process.env.SCREENSHOT_DIR;
const skip = !playwright && "Playwright is not installed";

const SOURCE = 'x = 1\ndef double(n):\n    y = n * 2\n    return y\nz = double(x)\npassword = "s3cr3t-value"\nw = z + 1\nw';

let app;
const page = () => app.page;

before(async () => {
  if (skip) return;
  app = await openApp({
    engine: ["start_cell", "poll_cell", "debug_cell", "interrupt_cell", "kernel_variables", "kernel_complete",
      "notebook_debug_get", "notebook_dependencies"],
    mocks: { save_notebook: () => ({ saved: true }) },
  });
  await page().evaluate((source) => {
    loadIntoState({ name: "Debug me", cells: [{ type: "code", cell_id: "c1", source, meta: {} }] });
    go("notebook");
  }, SOURCE);
});

after(async () => { if (app) await app.close(); });

test("a breakpoint from the gutter pauses in the function, and the panel steps and evaluates", { skip }, async () => {
  const p = page();
  await p.locator(".cm-lineNumbers .cm-gutterElement", { hasText: /^3$/ }).click();
  assert.deepStrictEqual(await p.evaluate(() => state.notebook.cells[0].meta.breakpoints), [3]);
  // Saved with the notebook: the cell's settings carry it, so a save sends it.
  assert.deepStrictEqual(await p.evaluate(() => notebookPayload().cells[0].meta.breakpoints), [3]);
  await p.locator(".cm-line.cm-bp").waitFor();
  await p.evaluate(() => { runCell(state.notebook.cells[0]); });
  const panel = p.locator(".dbg-paused");
  await panel.getByText("Paused at line 3 (breakpoint)").waitFor({ timeout: 30000 });
  const text = await panel.innerText();
  assert.match(text, /Call stack: \(cell\) line 5 › double line 3/);
  assert.match(text, /Variables in double/);
  assert.match(await p.locator(".cm-line.cm-paused").innerText(), /y = n \* 2/);
  if (SHOTS) await p.screenshot({ path: path.join(SHOTS, "debugger-paused.png") });

  await panel.getByRole("textbox", { name: "Expression to evaluate" }).fill("n + 10");
  await panel.getByRole("button", { name: "Evaluate" }).click();
  await panel.locator(".dbg-eval", { hasText: "n + 10" }).getByText("11").waitFor();

  await p.keyboard.press("Shift+F11");
  await panel.getByText("Paused at line 6").waitFor();
  await p.keyboard.press("F10");
  await panel.getByText("Paused at line 7").waitFor();
  const vars = await panel.locator("table.vars").innerText();
  assert.match(vars, /password\s+str\s+12\s+hidden/);
  assert.ok(!(await panel.innerText()).includes("s3cr3t-value"));

  await panel.getByRole("button", { name: "Continue" }).click();
  await p.locator(".cell-status", { hasText: "Succeeded" }).waitFor({ timeout: 30000 });
  assert.strictEqual(await p.locator(".dbg-paused").count(), 0);
  assert.strictEqual(await p.locator(".cm-line.cm-paused").count(), 0);
  assert.deepStrictEqual(app.errors, []);
});

test("Debug cell pauses at the first line, and Stop cancels it keeping variables", { skip }, async () => {
  const p = page();
  await p.evaluate(() => { debugCell(state.notebook.cells[0]); });
  const panel = p.locator(".dbg-paused");
  await panel.getByText("Paused at line 1").waitFor({ timeout: 30000 });
  await panel.getByRole("button", { name: "Stop" }).click();
  await p.locator(".cell-status", { hasText: "Cancelled" }).waitFor({ timeout: 30000 });
  assert.match(await p.locator(".cell-results").innerText(), /Variables are kept/);
  assert.strictEqual(await p.locator(".dbg-paused").count(), 0);
  assert.deepStrictEqual(app.errors, []);
});

test("breakpoints saved in a cell's settings come back when the notebook is reopened", { skip }, async () => {
  const p = page();
  await p.evaluate(() => {
    loadIntoState({ name: "Reopened", cells: [{ type: "code", cell_id: "r1", source: "a = 1\nb = 2\nc = 3", meta: { breakpoints: [2] } }] });
    go("notebook");
  });
  await p.locator(".cm-line.cm-bp", { hasText: "b = 2" }).waitFor();
  await p.locator(".cm-lineNumbers .cm-gutterElement", { hasText: /^2$/ }).click();  // clear it
  assert.strictEqual(await p.evaluate(() => "breakpoints" in state.notebook.cells[0].meta), false);
  assert.strictEqual(await p.locator(".cm-line.cm-bp").count(), 0);
  assert.deepStrictEqual(app.errors, []);
});

test("Debug from here steps from cell to cell, and Continue runs the next to its breakpoints", { skip }, async () => {
  const p = page();
  await p.evaluate(() => {
    loadIntoState({ name: "Three cells", cells: [
      { type: "code", cell_id: "d1", source: "rows = [3, 1, 2]", meta: {} },
      { type: "code", cell_id: "d2", source: "total = 0\nfor r in rows:\n    total += r", meta: {} },
      { type: "code", cell_id: "d3", source: "half = total / 2\nprint(half)", meta: { breakpoints: [2] } },
    ] });
    go("notebook");
  });
  await p.evaluate(() => { debugFromHere(state.notebook.cells[0]); });
  const panel = (i) => p.locator(`[data-cell="${i}"] .dbg-paused`);
  const ids = await p.evaluate(() => state.notebook.cells.map((c) => c.id));
  await panel(ids[0]).getByText("Paused at line 1").waitFor({ timeout: 30000 });
  await p.keyboard.press("F10");  // past the last line: cell 2 starts paused
  await panel(ids[1]).getByText("Paused at line 1").waitFor({ timeout: 30000 });
  await p.keyboard.press("F10");
  await panel(ids[1]).getByText("Paused at line 2").waitFor();
  await panel(ids[1]).getByRole("button", { name: "Continue" }).click();
  await panel(ids[2]).getByText("Paused at line 2 (breakpoint)").waitFor({ timeout: 30000 });
  assert.match(await panel(ids[2]).locator("table.vars").innerText(), /half\s+float/);
  await p.keyboard.press("F5");
  await p.locator(`[data-cell="${ids[2]}"] .cell-status`, { hasText: "Succeeded" }).waitFor({ timeout: 30000 });
  assert.match(await p.locator(`[data-cell="${ids[2]}"] .cell-results`).innerText(), /3\.0/);
  assert.strictEqual(await p.evaluate(() => state.notebook.dbgRun), null);
  assert.strictEqual(await p.locator(".dbg-paused").count(), 0);
  assert.deepStrictEqual(app.errors, []);
});

test("a notebook on another engine says it can't pause instead of debugging", { skip }, async () => {
  const p = page();
  const said = await p.evaluate(() => {
    const shown = [];
    const realToast = toast;
    toast = (text) => { shown.push(String(text)); };
    const realOn = nbOnEngine, realEngine = nbEngine;
    nbOnEngine = () => true;
    nbEngine = () => ({ engine: "databricks_connect", label: "Databricks Connect", status: "ready", remote: true });
    try {
      const cell = { type: "code", source: "x = 1", meta: { breakpoints: [1] } };
      const nb = { name: "Remote", cells: [cell] };
      return { debug: dbgRemoteNote(nb, [cell], true), run: dbgRemoteNote(nb, [cell]),
        none: dbgRemoteNote(nb, [{ type: "code", source: "y = 2", meta: {} }]), shown };
    } finally {
      toast = realToast; nbOnEngine = realOn; nbEngine = realEngine;
    }
  });
  assert.strictEqual(said.debug, true);  // nothing starts
  assert.strictEqual(said.run, false);   // the run goes on, to the end
  assert.strictEqual(said.none, false);
  assert.deepStrictEqual(said.shown, [
    "Databricks Connect can't pause, so it can't be debugged. Switch to Python (this PC) to step through cells.",
    "Databricks Connect can't pause, so breakpoints are ignored and the cells run to the end. Run on Python (this PC) to stop at them.",
  ]);
  assert.deepStrictEqual(app.errors, []);
});
