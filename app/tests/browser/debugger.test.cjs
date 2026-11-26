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
      "notebook_debug_get", "notebook_dependencies", "restart_kernel"],
    mocks: { save_notebook: () => ({ saved: true }), notebook_delete_check: () => ({ keep_days: 30, schedules: [] }),
      notebook_delete: () => ({ kept_until: "2030-01-01" }) },
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
    "Databricks Connect can't pause, so it can't be debugged. Switch Engine to Python (this PC) to step through cells.",
    "Databricks Connect can't pause, so breakpoints are ignored and the cells run to the end. Switch Engine to Python (this PC) to stop at them.",
  ]);
  assert.deepStrictEqual(app.errors, []);
});

// ---------- hardening ----------

async function toasts(p) {
  return p.evaluate(() => [...document.querySelectorAll(".toast")].map((t) => t.textContent));
}

test("an edited cell moves its breakpoints with their lines, and names any it removes", { skip }, async () => {
  const p = page();
  await p.evaluate(() => {
    loadIntoState({ name: "Edited", cells: [{ type: "code", cell_id: "e1", source: "a=1\nb=2\nc=3\nd=4", meta: { breakpoints: [2, 4] } }] });
    go("notebook");
  });
  await p.locator(".cm-line.cm-bp", { hasText: "d=4" }).waitFor();
  // The whole cell replaced (as Format cell does): each breakpoint finds its line again.
  await p.evaluate(() => { setSource(state.notebook.cells[0], "a = 1\n\nb = 2\nc = 3\nd = 4"); });
  assert.deepStrictEqual(await p.evaluate(() => state.notebook.cells[0].meta.breakpoints), [3, 5]);
  // A breakpoint whose line is gone is removed, and the message says which.
  await p.evaluate(() => { setSource(state.notebook.cells[0], "a = 1\nb = 2"); });
  assert.deepStrictEqual(await p.evaluate(() => state.notebook.cells[0].meta.breakpoints), [2]);
  assert.ok((await toasts(p)).some((t) => t.includes("Removed the breakpoint on line 5, because that line isn't in the cell any more.")));
  // Code changed where no editor saw it: a breakpoint past the end is named before the run.
  await p.evaluate(() => {
    const c = state.notebook.cells[0];
    c.meta.breakpoints = [2, 9];
    return dbgStartArgs(c, false);
  });
  assert.deepStrictEqual(await p.evaluate(() => state.notebook.cells[0].meta.breakpoints), [2]);
  assert.ok((await toasts(p)).some((t) => t.includes("Removed the breakpoint on line 9, because the cell has 2 lines now.")));
  assert.deepStrictEqual(app.errors, []);
});

test("the paused panel names library code it stepped over, and says when variables are capped", { skip }, async () => {
  const p = page();
  const many = Array.from({ length: 205 }, (_, i) => `v${String(i).padStart(3, "0")} = ${i}`).join("\n");
  await p.evaluate((src) => {
    loadIntoState({ name: "Library", cells: [{ type: "code", cell_id: "l1", source: src, meta: {} }] });
    go("notebook");
  }, `import json\ntext = json.dumps([1])\n${many}\nend = 1`);
  await p.evaluate(() => { debugCell(state.notebook.cells[0]); });
  const panel = p.locator(".dbg-paused");
  await panel.getByText("Paused at line 1").waitFor({ timeout: 30000 });
  await p.keyboard.press("F11");
  await panel.getByText("Paused at line 2").waitFor();
  await p.keyboard.press("F11");
  await panel.getByText("Paused at line 3").waitFor();
  assert.match(await panel.locator(".dbg-skipped").innerText(), /Stepped over, not into: json\.dumps\. Step into goes only into this cell's own code\./);
  await panel.getByRole("button", { name: "Continue" }).click();
  await p.locator(".cell-status", { hasText: "Succeeded" }).waitFor({ timeout: 30000 });
  // A breakpoint on the last line: 200 of the 200-odd variables are listed, and the panel says so.
  await p.evaluate(() => { const c = state.notebook.cells[0]; c.meta.breakpoints = [c.source.split("\n").length]; runCell(c); });
  await panel.getByText(/Paused at line \d+ \(breakpoint\)/).waitFor({ timeout: 30000 });
  assert.match(await panel.locator(".dbg-capped").innerText(), /Showing the first 200 of 2\d\d variables, by name\./);
  await panel.getByRole("button", { name: "Continue" }).click();
  await p.locator(".cell-status", { hasText: "Succeeded" }).waitFor({ timeout: 30000 });
  assert.deepStrictEqual(app.errors, []);
});

test("a kernel restart while paused unpauses, and a Debug from here run doesn't go on", { skip }, async () => {
  const p = page();
  await p.evaluate(() => {
    loadIntoState({ name: "Restart", cells: [
      { type: "code", cell_id: "k1", source: "a = 1\nb = 2", meta: {} },
      { type: "code", cell_id: "k2", source: "c = 3", meta: {} },
    ] });
    go("notebook");
  });
  await p.evaluate(() => { debugFromHere(state.notebook.cells[0]); });
  await p.locator(".dbg-paused").getByText("Paused at line 1").waitFor({ timeout: 30000 });
  await p.evaluate(async () => { await rpc("restart_kernel", { notebook: state.notebook.name }); depsRestarted(); });
  await p.waitForFunction(() => !state.notebook.runningAll, null, { timeout: 30000 });
  assert.strictEqual(await p.locator(".dbg-paused").count(), 0);
  assert.strictEqual(await p.locator(".cm-line.cm-paused").count(), 0);
  const [first, second] = await p.evaluate(() => state.notebook.cells.map((c) => c.status));
  assert.strictEqual(first, "cancelled");
  assert.strictEqual(second, null);  // the run stopped there, not on into the fresh kernel
  assert.strictEqual(await p.evaluate(() => state.notebook.dbgRun), null);
  assert.deepStrictEqual(app.errors, []);
});

test("a cell edited while paused marks the pause on its code, or nowhere, never on another line", { skip }, async () => {
  const p = page();
  await p.evaluate(() => {
    loadIntoState({ name: "Edit paused", cells: [{ type: "code", cell_id: "p1", source: "a = 1\nb = 2\nc = 3", meta: { breakpoints: [2] } }] });
    go("notebook");
  });
  await p.evaluate(() => { runCell(state.notebook.cells[0]); });
  const panel = p.locator(".dbg-paused");
  await panel.getByText("Paused at line 2 (breakpoint)").waitFor({ timeout: 30000 });
  assert.strictEqual(await panel.locator(".dbg-edited").count(), 0);
  // Two lines inserted above: the paused mark moves with line 2's code, and the panel says why.
  await p.evaluate(() => { setSource(state.notebook.cells[0], "# one\n# two\na = 1\nb = 2\nc = 3"); });
  await panel.locator(".dbg-edited").waitFor();
  assert.match(await p.locator(".cm-line.cm-paused").innerText(), /^b = 2$/);
  assert.match(await panel.locator(".dbg-edited").innerText(),
    /You changed this cell after it started\. It's paused at line 2 of the code as it ran, which is line 4 now\. Your edits and breakpoint changes take effect the next time the cell runs\./);
  // The paused line's code is gone: no mark at all, and the panel names the line as it ran.
  await p.evaluate(() => { setSource(state.notebook.cells[0], "a = 1\nc = 3"); });
  await panel.locator(".dbg-edited", { hasText: "isn't in the cell now" }).waitFor();
  assert.strictEqual(await p.locator(".cm-line.cm-paused").count(), 0);
  assert.match(await panel.locator(".dbg-edited").innerText(), /Line 2 as it ran \(b = 2\) isn't in the cell now\./);
  await panel.getByRole("button", { name: "Continue" }).click();
  await p.locator(".cell-status", { hasText: "Succeeded" }).waitFor({ timeout: 30000 });
  assert.strictEqual(await p.locator(".dbg-paused").count(), 0);
  assert.deepStrictEqual(app.errors, []);
});

test("a garbled pause from the engine draws what it can, with no errors", { skip }, async () => {
  const p = page();
  await p.evaluate(() => {
    loadIntoState({ name: "Garbled", cells: [{ type: "code", cell_id: "g1", source: "a = 1\nb = 2", meta: {} }] });
    go("notebook");
  });
  const garbled = [
    { line: 2, stack: "(cell) line 2", variables: { a: 1 }, watches: [null], evals: 7, skipped: "json.dumps" },
    { line: 2, stack: [{ function: { x: 1 }, line: "two" }, 5], variables: [{ name: null, value: { deep: [1] } }], variables_total: -3 },
    { line: 99, variables: Array.from({ length: 3000 }, (_, i) => ({ name: `v${i}`, type: "int", size: "", value: "y".repeat(5000) })) },
    { line: "2" },
    "paused",
  ];
  for (const paused of garbled) {
    const shown = await p.evaluate((paused) => {
      const cell = state.notebook.cells[0];
      cell.status = "running";
      dbgSnapshot(cell, { status: "running", paused });
      updateResults(cell);
      const panel = document.querySelector(".dbg-paused");
      const text = panel ? panel.innerText : null;
      dbgSnapshot(cell, null);
      cell.status = null;
      updateResults(cell);
      return text;
    }, paused);
    if (typeof paused === "object" && Number.isInteger(paused.line)) {
      assert.match(shown, new RegExp(`Paused at line ${paused.line}`));
      assert.ok(shown.length < 2_000_000);
    } else {
      assert.strictEqual(shown, null);  // no usable line: not shown as paused
    }
  }
  assert.strictEqual(await p.locator(".dbg-paused").count(), 0);
  assert.deepStrictEqual(app.errors, []);
});

test("Debug from here over 20 cells: Stop ends the run there, and Continue runs the rest", { skip }, async () => {
  const p = page();
  const cells = Array.from({ length: 20 }, (_, i) => ({ type: "code", cell_id: `m${i}`, source: `v${i} = ${i}\nw${i} = v${i} + 1`, meta: i === 15 ? { breakpoints: [2] } : {} }));
  await p.evaluate((cells) => { loadIntoState({ name: "Twenty", cells }); go("notebook"); }, cells);
  const panel = (i) => p.locator(`[data-cell="${i}"] .dbg-paused`);
  const ids = await p.evaluate(() => state.notebook.cells.map((c) => c.id));
  // Step through the first three cells line by line, then Stop in the fourth.
  await p.evaluate(() => { debugFromHere(state.notebook.cells[0]); });
  for (let i = 0; i < 3; i++) {
    await panel(ids[i]).getByText("Paused at line 1").waitFor({ timeout: 30000 });
    await p.keyboard.press("F10");
    await panel(ids[i]).getByText("Paused at line 2").waitFor({ timeout: 30000 });
    await p.keyboard.press("F10");
  }
  await panel(ids[3]).getByText("Paused at line 1").waitFor({ timeout: 30000 });
  await panel(ids[3]).getByRole("button", { name: "Stop" }).click();
  await p.waitForFunction(() => !state.notebook.runningAll && !state.notebook.dbgRun, null, { timeout: 30000 });
  let status = await p.evaluate(() => state.notebook.cells.map((c) => c.status));
  assert.deepStrictEqual(status.slice(0, 4), ["ok", "ok", "ok", "cancelled"]);
  assert.ok(status.slice(4).every((s) => s == null), "nothing ran after Stop");
  assert.strictEqual(await p.locator(".dbg-paused").count(), 0);
  // Again from the fifth cell: Continue at once, so the rest run to cell 16's breakpoint only, then to the end.
  await p.evaluate(() => { debugFromHere(state.notebook.cells[4]); });
  await panel(ids[4]).getByText("Paused at line 1").waitFor({ timeout: 30000 });
  await p.keyboard.press("F5");
  await panel(ids[15]).getByText("Paused at line 2 (breakpoint)").waitFor({ timeout: 30000 });
  await p.keyboard.press("F5");
  await p.waitForFunction(() => !state.notebook.runningAll && !state.notebook.dbgRun, null, { timeout: 60000 });
  status = await p.evaluate(() => state.notebook.cells.map((c) => c.status));
  assert.ok(status.slice(4).every((s) => s === "ok"), JSON.stringify(status));
  assert.strictEqual(await p.locator(".dbg-paused").count(), 0);
  assert.strictEqual(await p.locator(".cm-line.cm-paused").count(), 0);
  assert.deepStrictEqual(app.errors, []);
});

test("a cell edited while it waits its turn in Debug from here runs with its new code and breakpoints", { skip }, async () => {
  const p = page();
  await p.evaluate(() => {
    loadIntoState({ name: "Waiting", cells: [
      { type: "code", cell_id: "q1", source: "a = 1\nb = 2", meta: {} },
      { type: "code", cell_id: "q2", source: "c = a + 1\nd = c + 1", meta: { breakpoints: [2] } },
    ] });
    go("notebook");
  });
  const ids = await p.evaluate(() => state.notebook.cells.map((c) => c.id));
  const panel = (i) => p.locator(`[data-cell="${i}"] .dbg-paused`);
  await p.evaluate(() => { debugFromHere(state.notebook.cells[0]); });
  await panel(ids[0]).getByText("Paused at line 1").waitFor({ timeout: 30000 });
  // While cell 1 is paused, cell 2 is rewritten: two lines above its breakpoint, and a second breakpoint.
  await p.evaluate(() => {
    const c = state.notebook.cells[1];
    setSource(c, "# checked\n# twice\nc = a + 1\nd = c + 1\ne = d + 1");
    dbgToggleBreakpoint(c, 5);
  });
  assert.deepStrictEqual(await p.evaluate(() => state.notebook.cells[1].meta.breakpoints), [4, 5]);
  await p.keyboard.press("F5");  // Continue: cell 2 runs to its breakpoints only
  await panel(ids[1]).getByText("Paused at line 4 (breakpoint)").waitFor({ timeout: 30000 });
  assert.match(await p.locator(`[data-cell="${ids[1]}"] .cm-line.cm-paused`).innerText(), /^d = c \+ 1$/);
  assert.strictEqual(await panel(ids[1]).locator(".dbg-edited").count(), 0);  // it runs the code as edited
  await p.keyboard.press("F5");
  await panel(ids[1]).getByText("Paused at line 5 (breakpoint)").waitFor({ timeout: 30000 });
  await p.keyboard.press("F5");
  await p.waitForFunction(() => !state.notebook.runningAll && !state.notebook.dbgRun, null, { timeout: 30000 });
  assert.deepStrictEqual(await p.evaluate(() => state.notebook.cells.map((c) => c.status)), ["ok", "ok"]);
  assert.deepStrictEqual(app.errors, []);
});


// ---------- round 7: the panel under rapid keys, focus, and where a pause is ----------

// The debugger commands sent since `from` (an index into app.calls).
const sentSince = (from) => app.calls.slice(from).filter((c) => c.method === "debug_cell").map((c) => c.params.action);

test("keys pressed again while a step runs send one command, and the panel keeps focus and says each pause once", { skip }, async () => {
  const p = page();
  await p.evaluate(() => {
    loadIntoState({ name: "Rapid", cells: [{ type: "code", cell_id: "r1", source: "a = 1\nb = 2\nc = 3\nd = 4\ne = 5", meta: {} }] });
    go("notebook");
    window.__said = [];
    window.__announce = announce;
    announce = (text) => { window.__said.push(text); window.__announce(text); };
  });
  await p.evaluate(() => { debugCell(state.notebook.cells[0]); });
  const panel = p.locator(".dbg-paused");
  await panel.getByText("Paused at line 1").waitFor({ timeout: 30000 });
  await p.waitForFunction(() => window.__said.includes("Cell 1 paused at line 1"));
  // F10, F11, F5 and Shift+F5 in one go, as a held or mashed key sends them: one step, to line 2.
  const from = app.calls.length;
  await p.evaluate(() => {
    for (const [key, shiftKey] of [["F10", false], ["F10", false], ["F11", false], ["F5", false], ["F5", true], ["F11", true]]) {
      document.dispatchEvent(new KeyboardEvent("keydown", { key, shiftKey, bubbles: true, cancelable: true }));
    }
  });
  await panel.getByText("Paused at line 2").waitFor({ timeout: 30000 });
  await p.waitForTimeout(800);
  assert.deepStrictEqual(sentSince(from), ["next"]);
  assert.match(await panel.locator(".dbg-hidden-title b").innerText(), /^Paused at line 2$/);
  // The next key steps again: nothing is left stuck.
  await p.keyboard.press("F10");
  await panel.getByText("Paused at line 3").waitFor({ timeout: 30000 });
  // Enter on a focused button presses it (not the notebook's Enter, which would edit the cell), and focus stays on it.
  await panel.getByRole("button", { name: "Step over" }).focus();
  await p.keyboard.press("Enter");
  await panel.getByText("Paused at line 4").waitFor({ timeout: 30000 });
  assert.strictEqual(await p.evaluate(() => document.activeElement.textContent), "Step over");
  // Evaluate: the box keeps focus when the answer redraws the panel, and an answer isn't announced as a pause.
  const box = panel.getByRole("textbox", { name: "Expression to evaluate" });
  await box.fill("a + c");
  await box.press("Enter");
  await panel.locator(".dbg-eval", { hasText: "a + c" }).getByText("4").waitFor();
  await p.waitForTimeout(300);
  assert.strictEqual(await p.evaluate(() => document.activeElement.getAttribute("aria-label")), "Expression to evaluate");
  assert.strictEqual(await box.inputValue(), "");
  // Typing kept while the panel redraws.
  await box.fill("d * 2");
  await p.evaluate(() => updateResults(state.notebook.cells[0]));
  assert.strictEqual(await panel.getByRole("textbox", { name: "Expression to evaluate" }).inputValue(), "d * 2");
  const said = await p.evaluate(() => window.__said.filter((t) => /paused/.test(t)));
  assert.deepStrictEqual(said, ["Cell 1 paused at line 1", "Cell 1 paused at line 2", "Cell 1 paused at line 3", "Cell 1 paused at line 4"]);
  // Command mode keys don't reach the cell from the panel: D D on a button doesn't delete it.
  await panel.getByRole("button", { name: "Continue" }).focus();
  await p.keyboard.press("d");
  await p.keyboard.press("d");
  assert.strictEqual(await p.evaluate(() => state.notebook.cells.length), 1);
  await p.keyboard.press("Enter");
  await p.locator(".cell-status", { hasText: "Succeeded" }).waitFor({ timeout: 30000 });
  // The panel closed with focus in it: focus is on the cell, not lost.
  assert.strictEqual(await p.evaluate(() => document.activeElement.dataset.cell), String(await p.evaluate(() => state.notebook.cells[0].id)));
  await p.evaluate(() => { announce = window.__announce; });
  assert.deepStrictEqual(app.errors, []);
});

test("a pause in a collapsed section, or scrolled out of view, is shown", { skip }, async () => {
  const p = page();
  const cells = [{ type: "markdown", cell_id: "h0", source: "# Top", meta: {} }];
  for (let i = 0; i < 30; i++) cells.push({ type: "code", cell_id: `s${i}`, source: `v${i} = ${i}`, meta: {} });
  cells.push({ type: "markdown", cell_id: "h1", source: "# Below", meta: { heading_collapsed: true } });
  cells.push({ type: "code", cell_id: "last", source: "q = 1\nr = 2", meta: { breakpoints: [2] } });
  await p.evaluate((cells) => { loadIntoState({ name: "Long", cells }); go("notebook"); }, cells);
  const inView = () => p.evaluate(() => {
    const el = document.querySelector(".cm-line.cm-paused");
    if (!el) return false;
    const r = el.getBoundingClientRect();
    return r.top >= 0 && r.bottom <= innerHeight;
  });
  for (const collapsed of [true, false]) {
    await p.evaluate((collapsed) => {
      const nb = state.notebook;
      if (!collapsed) { delete nb.cells.at(-2).meta.heading_collapsed; renderNotebook(); }
      document.querySelector("[data-cell]").scrollIntoView();
      runCell(nb.cells.at(-1));
    }, collapsed);
    await p.locator(".dbg-paused").getByText("Paused at line 2 (breakpoint)").waitFor({ timeout: 30000 });
    await p.waitForFunction(() => {
      const el = document.querySelector(".cm-line.cm-paused");
      const r = el?.getBoundingClientRect();
      return r && r.top >= 0 && r.bottom <= innerHeight;
    }, null, { timeout: 5000 }).catch(() => {});
    assert.ok(await inView(), `paused line in view (collapsed: ${collapsed})`);
    await p.keyboard.press("F5");
    await p.locator(`[data-cell="${await p.evaluate(() => state.notebook.cells.at(-1).id)}"] .cell-status`, { hasText: "Succeeded" }).waitFor({ timeout: 30000 });
  }
  assert.deepStrictEqual(app.errors, []);
});

test("a deleted line takes its breakpoint, undo brings it back, and pasted lines move the others", { skip }, async () => {
  const p = page();
  await p.evaluate(() => {
    loadIntoState({ name: "Gutter", cells: [{ type: "code", cell_id: "u1", source: "a=1\nb=2\nc=3\nd=4", meta: { breakpoints: [2, 4] } }] });
    go("notebook");
  });
  await p.locator(".cm-line.cm-bp", { hasText: "d=4" }).waitFor();
  const bps = () => p.evaluate(() => state.notebook.cells[0].meta.breakpoints || []);
  const view = (fn, arg) => p.evaluate(([fn, arg]) => {
    const v = editors.get(state.notebook.cells[0].id);
    return new Function("v", "arg", fn)(v, arg);
  }, [fn, arg]);
  // Line 2 deleted with its line break (Ctrl+Shift+K, or selecting the line and deleting): its breakpoint goes, named.
  await view("v.dispatch({ changes: { from: 4, to: 8 }, userEvent: 'delete' });");
  assert.deepStrictEqual(await bps(), [3]);
  assert.ok((await toasts(p)).some((t) => t.includes("Removed the breakpoint on line 2, because its line was deleted.")));
  // Undo puts the line back and its breakpoint with it; redo deletes both again.
  await view("CM.undo(v);");
  assert.deepStrictEqual(await bps(), [2, 4]);
  await view("CM.redo(v);");
  assert.deepStrictEqual(await bps(), [3]);
  await view("CM.undo(v);");
  assert.deepStrictEqual(await bps(), [2, 4]);
  // Several lines pasted at the start of line 2: both breakpoints move down with their lines.
  await view("v.dispatch({ changes: { from: 4, insert: 'x=0\\ny=0\\n' }, userEvent: 'input.paste' });");
  assert.deepStrictEqual(await bps(), [4, 6]);
  assert.match(await p.locator(".cm-line.cm-bp").first().innerText(), /^b=2$/);
  // Pasted into the middle of a line: that line keeps its breakpoint.
  await view("v.dispatch({ changes: { from: 13, insert: '\\nw=0\\nv=0' }, userEvent: 'input.paste' });");
  assert.deepStrictEqual(await bps(), [4, 8]);
  // The last line deleted (with the line break before it): its breakpoint goes too.
  await view("const d = v.state.doc; v.dispatch({ changes: { from: d.line(d.lines - 1).to, to: d.length }, userEvent: 'delete' });");
  assert.deepStrictEqual(await bps(), [4]);
  assert.deepStrictEqual(app.errors, []);
});

test("a cell takes at most 100 breakpoints, and says so", { skip }, async () => {
  const p = page();
  const src = Array.from({ length: 120 }, (_, i) => `v${i} = ${i}`).join("\n");
  await p.evaluate((src) => { loadIntoState({ name: "Hundred", cells: [{ type: "code", cell_id: "h1", source: src, meta: {} }] }); go("notebook"); }, src);
  await p.evaluate(() => { const c = state.notebook.cells[0]; for (let i = 1; i <= 101; i++) dbgToggleBreakpoint(c, i); });
  assert.strictEqual(await p.evaluate(() => state.notebook.cells[0].meta.breakpoints.length), 100);
  assert.ok((await toasts(p)).some((t) => t.includes("A cell can have up to 100 breakpoints. Clear one to set another.")));
  // Clearing one still works, and then one more can be set.
  await p.evaluate(() => { const c = state.notebook.cells[0]; dbgToggleBreakpoint(c, 1); dbgToggleBreakpoint(c, 101); });
  assert.strictEqual(await p.evaluate(() => state.notebook.cells[0].meta.breakpoints.at(-1)), 101);
  assert.deepStrictEqual(app.errors, []);
});

test("a paused cell isn't deleted, moves while paused, and deleting its notebook stops it", { skip }, async () => {
  const p = page();
  await p.evaluate(() => {
    loadIntoState({ name: "Gone", cells: [
      { type: "code", cell_id: "x1", source: "a = 1\nb = 2\nc = 3", meta: { breakpoints: [3] } },
      { type: "code", cell_id: "x2", source: "z = 1", meta: {} },
    ] });
    go("notebook");
  });
  await p.evaluate(() => { debugCell(state.notebook.cells[0]); });
  const panel = p.locator(".dbg-paused");
  await panel.getByText("Paused at line 1").waitFor({ timeout: 30000 });
  await p.evaluate(() => deleteCell(state.notebook.cells[0]));
  assert.strictEqual(await p.evaluate(() => state.notebook.cells.length), 2);
  assert.ok((await toasts(p)).some((t) => t.includes("This cell is paused in the debugger. Stop it to delete it.")));
  await p.evaluate(() => { nbSel.ids = new Set(state.notebook.cells.map((c) => c.id)); deleteSelected(); nbSel.ids.clear(); });
  assert.strictEqual(await p.evaluate(() => state.notebook.cells.length), 2);
  // Moved below the other cell: still paused, its breakpoint with it, and the keys still step it.
  await p.evaluate(() => moveCell(1, state.notebook.cells[0]));
  assert.strictEqual(await p.evaluate(() => state.notebook.cells[1].cell_id), "x1");
  await panel.getByText("Paused at line 1").waitFor();
  await p.keyboard.press("F10");
  await panel.getByText("Paused at line 2").waitFor({ timeout: 30000 });
  assert.strictEqual(await p.locator(".cm-line.cm-bp", { hasText: "c = 3" }).count(), 1);
  // Deleting the notebook while it's paused stops the cell, so nothing waits on it and polling ends.
  const kept = await p.evaluate(() => { window.__gone = state.notebook; return true; });
  assert.ok(kept);
  const deleted = p.evaluate(() => nbDelete("Gone"));
  await p.locator(".dialog").getByText("A cell in it is paused in the debugger. Deleting the notebook stops that cell.").waitFor();
  await p.locator(".dialog button", { hasText: "Delete" }).click();
  assert.strictEqual(await deleted, true);
  await p.waitForFunction(() => window.__gone.cells.every((c) => c.status !== "running") && window.__gone.kernelStatus !== "busy", null, { timeout: 30000 });
  assert.strictEqual(await p.evaluate(() => window.__gone.cells[1].status), "cancelled");
  const polls = () => app.calls.filter((c) => c.method === "poll_cell" && c.params.notebook === "Gone").length;
  const n = polls();
  await p.waitForTimeout(1500);
  assert.strictEqual(polls(), n, "no more polling for the deleted notebook");
  assert.deepStrictEqual(app.errors, []);
});

test("an Evaluate that answers pending holds the step keys back until it answers, then the cell ends cleanly", { skip }, async () => {
  const p = page();
  await p.evaluate(() => {
    loadIntoState({ name: "Pending", cells: [{ type: "code", cell_id: "e1", source: "a = 1\nb = 2", meta: {} }] });
    go("notebook");
  });
  const panel = p.locator(".dbg-paused");
  await p.evaluate(() => { debugCell(state.notebook.cells[0]); });
  await panel.getByText("Paused at line 1").waitFor({ timeout: 30000 });
  const from = app.calls.length;
  await panel.getByRole("textbox", { name: "Expression to evaluate" }).fill("any(__import__('time').sleep(0.05) for _ in range(300))");
  await panel.getByRole("button", { name: "Evaluate" }).click();
  // Keys while it evaluates send nothing (the engine would run them after it, on a pause no longer shown).
  await p.keyboard.press("F10");
  await p.keyboard.press("F5");
  await p.waitForFunction(() => [...document.querySelectorAll(".toast")].some((t) => t.textContent.includes("Still evaluating.")), null, { timeout: 20000 });
  assert.deepStrictEqual(sentSince(from), ["eval"]);
  // It answered pending: the keys work again, and Stop ends the cell with nothing left paused or waiting.
  await panel.getByRole("button", { name: "Stop" }).click();
  await p.locator(".cell-status", { hasText: "Cancelled" }).waitFor({ timeout: 30000 });
  assert.strictEqual(await p.locator(".dbg-paused").count(), 0);
  assert.strictEqual(await p.evaluate(() => state.notebook.cells[0].dbgSent), null);
  await p.evaluate(() => { debugCell(state.notebook.cells[0]); });
  await panel.getByText("Paused at line 1").waitFor({ timeout: 30000 });
  await p.keyboard.press("F10");
  await panel.getByText("Paused at line 2").waitFor({ timeout: 30000 });
  await p.keyboard.press("F5");
  await p.locator(".cell-status", { hasText: "Succeeded" }).waitFor({ timeout: 30000 });
  assert.ok(!(await toasts(p)).some((t) => t.includes("Could not reach the debugger")), JSON.stringify(await toasts(p)));
  assert.deepStrictEqual(app.errors, []);
});

test("names and values with HTML in a pause show as text", { skip }, async () => {
  const p = page();
  await p.evaluate(() => {
    loadIntoState({ name: "Html", cells: [{ type: "code", cell_id: "w1", source: "a = 1", meta: {} }] });
    go("notebook");
  });
  const out = await p.evaluate(() => {
    const cell = state.notebook.cells[0];
    const evil = '<img src=x onerror="window.__pwned=1"><b>bold</b>';
    cell.status = "running";
    dbgSnapshot(cell, { status: "running", paused: { line: 1, reason: evil, stack: [{ function: evil, line: 1 }, { function: evil, line: 2 }],
      variables: [{ name: evil, type: evil, size: 7, value: true }], watches: [{ expr: evil, value: evil }], evals: [{ expr: evil, error: evil }], skipped: [evil] } });
    updateResults(cell);
    const panel = document.querySelector(".dbg-paused");
    const res = { imgs: panel.querySelectorAll("img, b b").length, text: panel.innerText };
    dbgSnapshot(cell, null);
    cell.status = null;
    updateResults(cell);
    return res;
  });
  assert.strictEqual(out.imgs, 0);
  assert.ok(out.text.includes('<img src=x onerror="window.__pwned=1"><b>bold</b>'));
  assert.match(out.text, /\btrue\b/);
  await p.waitForTimeout(100);
  assert.strictEqual(await p.evaluate(() => window.__pwned), undefined);
  assert.deepStrictEqual(app.errors, []);
});
