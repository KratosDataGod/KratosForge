// app/ui/debugging.js beyond variable-history, debugger and debugging-watch-keys: the hidden state
// warning's Ignore for this notebook (saved, or kept when the save fails), a long source shortened,
// a watch paused for being slow and resumed, a watch's error, History's row that inserts display(),
// the first run seeding who set each name, a restart ending a paused cell, F9 at the cursor, a Fabric
// Spark step that can't pause, and the paused panel's notes (data frames, an edit on the same line).
// The engine's debug store, variables and dependency graph are mocked, with their own reply shapes.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
let app;
const page = () => app.page;
let saved, failSave, vars;

const CELLS = [
  { type: "code", cell_id: "c1", source: "rows = [1, 2]", meta: {} },
  { type: "code", cell_id: "c2", source: "total = sum(rows)\nprint(total)", meta: {} },
];

before(async () => {
  if (skip) return;
  saved = { watches: [], ignore_hidden: [] };
  vars = [];
  app = await openApp({ mocks: {
    save_notebook: () => ({ saved: true }),
    kernel_variables: () => JSON.parse(JSON.stringify(vars)),
    notebook_dependencies: () => ({}),
    notebook_debug_get: () => ({ ...saved }),
    notebook_debug_set: (p) => {
      if (failSave) throw new Error("disk full");
      saved = { watches: p.watches ?? saved.watches, ignore_hidden: p.ignore_hidden ?? saved.ignore_hidden };
      return { ...saved };
    },
  } });
});
after(async () => { await app?.close(); });

beforeEach(async () => {
  if (skip) return;
  saved = { watches: [], ignore_hidden: [] };
  failSave = false;
  vars = [];
  await page().evaluate((cells) => {
    loadIntoState({ name: "Debug cov", cells: JSON.parse(JSON.stringify(cells)) });
    state.showVariables = true;
    go("notebook");
  }, CELLS);
  await page().locator(".varpane .dbg-pane").waitFor();
  await page().evaluate(() => dbgState(state.notebook).loaded);
});

const cellLoc = (i) => page().locator(".cell").nth(i);
const toastText = () => page().locator("#toast").innerText();

test("Ignore for this notebook saves the name and takes the warning off every cell", { skip }, async () => {
  const long = `leftover = ${"1 + ".repeat(40)}1`;
  await page().evaluate((src) => {
    for (const c of state.notebook.cells) {
      c.hidden = [{ name: "leftover", text: "leftover isn't defined by any cell in this notebook", source: src }];
      updateResults(c);
    }
  }, long);
  const warning = cellLoc(1).locator(".dbg-hidden");
  await warning.waitFor();
  const shown = await warning.locator("code").innerText();
  assert.strictEqual(shown, `${long.slice(0, 117)}...`, "a source past 120 characters is shortened");
  await warning.getByRole("button", { name: "Ignore for this notebook" }).click();
  await page().locator(".dbg-hidden").first().waitFor({ state: "detached" });
  assert.strictEqual(await page().locator(".dbg-hidden").count(), 0);
  assert.deepStrictEqual(app.calls.filter((c) => c.method === "notebook_debug_set").map((c) => c.params),
    [{ name: "Debug cov", ignore_hidden: ["leftover"] }]);
  assert.deepStrictEqual(await page().evaluate(() => state.notebook.debug.ignore), ["leftover"]);
  assert.deepStrictEqual(app.errors, []);
});

test("when the save fails, Ignore keeps the warning and says it couldn't save", { skip }, async () => {
  failSave = true;
  await page().evaluate(() => {
    const c = state.notebook.cells[0];
    c.hidden = [{ name: "gone", text: "gone comes from a cell further down, which ran before this one", source: null }];
    updateResults(c);
  });
  const warning = cellLoc(0).locator(".dbg-hidden");
  const was = await page().evaluate(() => state.notebook.debug.ignore);
  await warning.getByRole("button", { name: "Ignore for this notebook" }).click();
  await page().locator("#toast", { hasText: "Could not save" }).waitFor();
  assert.strictEqual(await warning.count(), 1, "the warning stays");
  assert.strictEqual(await warning.getByRole("button", { name: "Insert that cell back above" }).count(), 0, "no source to insert");
  assert.deepStrictEqual(await page().evaluate(() => state.notebook.debug.ignore), was, "nothing more is ignored");
  assert.deepStrictEqual(app.errors, []);
});

test("a slow watch is paused, and Resume sends it again; an error shows in its place", { skip }, async () => {
  await page().evaluate(() => { state.notebook.debug.watches = ["slow()", "bad()"]; dbgPaint(); });
  vars = [{ name: "rows", type: "list", size: "2", value: "[1, 2]" }];
  await page().evaluate(() => dbgAfterRun(state.notebook.cells[0], { watches: [
    { expr: "slow()", value: "42", seconds: 3 }, { expr: "bad()", error: "NameError: bad" }, { expr: "skipped()", skipped: true }] }));
  const slow = page().locator(".dbg-watch", { hasText: "slow()" });
  await slow.getByText("Paused: took longer than 2 seconds.").waitFor();
  assert.match(await slow.getAttribute("class"), /paused/);
  assert.deepStrictEqual(await page().evaluate(() => dbgWatches(state.notebook)), ["bad()"], "a paused watch isn't sent");
  await page().locator(".dbg-watch", { hasText: "bad()" }).locator(".warn", { hasText: "NameError: bad" }).waitFor();

  await slow.getByRole("button", { name: "Resume" }).click();
  await page().locator(".dbg-watch", { hasText: "slow()" }).locator(".dbg-value", { hasText: "42" }).waitFor();
  assert.deepStrictEqual(await page().evaluate(() => dbgWatches(state.notebook)), ["slow()", "bad()"]);
  assert.deepStrictEqual(app.errors, []);
});

test("the first run records who set each name; History's row inserts display() of it", { skip }, async () => {
  vars = [{ name: "rows", type: "list", size: "2", value: "[1, 2]" }];
  // The first run: nothing known of what the kernel had before, so every name is taken as this cell's.
  await page().evaluate(() => { state.notebook.debug.vars = null; return dbgAfterRun(state.notebook.cells[0], {}); });
  assert.deepStrictEqual(await page().evaluate(() => state.notebook.debug.setBy), { rows: { cell_id: "c1", source: "rows = [1, 2]" } });
  assert.deepStrictEqual(await page().evaluate(() => state.notebook.debug.history), [], "no History entry without a before");

  vars = [...vars, { name: "total", type: "int", size: "", value: "3" }];
  await page().evaluate(() => dbgAfterRun(state.notebook.cells[1], {}));
  await page().locator(".varpane").getByRole("tab", { name: "History" }).click();
  const row = page().locator(".dbg-run .add", { hasText: "total" });
  await row.waitFor();
  const before = await page().evaluate(() => state.notebook.cells.length);
  await row.click();
  const after = await page().evaluate(() => state.notebook.cells.map((c) => c.source));
  assert.strictEqual(after.length, before + 1);
  assert.ok(after.includes("display(total)"));
  assert.deepStrictEqual(app.errors, []);
});

test("a kernel restart ends a paused cell and clears its panel", { skip }, async () => {
  await page().evaluate(() => {
    const c = state.notebook.cells[1];
    c.paused = { line: 2, reason: "breakpoint", stack: [], variables: [], watches: [], evals: [] };
    c.dbgRan = c.source;
    state.notebook.dbgRun = { mode: "next" };
    updateResults(c);
  });
  await cellLoc(1).locator(".dbg-paused").waitFor();
  await page().evaluate(() => dbgRestarted());
  await cellLoc(1).locator(".dbg-paused").waitFor({ state: "detached" });
  assert.deepStrictEqual(await page().evaluate(() => [state.notebook.cells[1].paused, state.notebook.cancelRequested]), [null, true]);
  await page().evaluate(() => { delete state.notebook.dbgRun; state.notebook.cancelRequested = false; });
  assert.deepStrictEqual(app.errors, []);
});

test("F9 in an editor sets and clears a breakpoint on the cursor's line", { skip }, async () => {
  const editor = cellLoc(1).locator(".cm-content");
  await editor.click();
  await page().keyboard.press("Control+End");
  await page().keyboard.press("F9");
  assert.deepStrictEqual(await page().evaluate(() => state.notebook.cells[1].meta.breakpoints), [2]);
  await cellLoc(1).locator(".cm-bp").waitFor();
  await page().keyboard.press("F9");
  assert.strictEqual(await page().evaluate(() => state.notebook.cells[1].meta.breakpoints), undefined);
  // No editor for a cell: nothing to toggle.
  assert.strictEqual(await page().evaluate(() => dbgToggleAtCursor({ id: "nope", type: "code", meta: {} })), false);
  assert.deepStrictEqual(app.errors, []);
});

test("a Fabric Spark step can't pause: Debug says so, and debugs only when other cells run here", { skip }, async () => {
  const [both, alone, quiet] = await page().evaluate(() => {
    const [a, b] = state.notebook.cells;
    b.meta.fabric_step = true;
    const out = [dbgRemoteNote(state.notebook, [a, b], true), dbgRemoteNote(state.notebook, [b], true),
      dbgRemoteNote(state.notebook, [a, b], false)];
    delete b.meta.fabric_step;
    return out;
  });
  assert.deepStrictEqual([both, alone, quiet], [false, true, false]);
  assert.match(await toastText(), /The last cell runs on Fabric Spark, which can't pause, so it runs to the end\./);
  assert.deepStrictEqual(app.errors, []);
});

test("the paused panel notes data frames and an edit that kept the paused line", { skip }, async () => {
  await page().evaluate(() => {
    const c = state.notebook.cells[1];
    c.dbgRan = "total = sum(rows)\nprint(total)";
    c.source = "total = sum(rows)  # edited\nprint(total)";
    c.paused = { line: 2, reason: "step", stack: [{ function: "(cell)", line: 2 }],
      variables: [{ name: "df", type: "DataFrame", size: "3 × 2", value: "shape (3, 2)" }], watches: [], evals: [] };
    updateResults(c);
  });
  const panel = cellLoc(1).locator(".dbg-paused");
  await panel.waitFor();
  assert.match(await panel.locator(".dbg-capped").innerText(), /^Data frames show their size and first columns only\./);
  assert.match(await panel.locator(".dbg-edited").innerText(), /It's paused at line 2 of the code as it ran\. Your edits/);
  await page().evaluate(() => { const c = state.notebook.cells[1]; c.paused = null; updateResults(c); });
  assert.deepStrictEqual(app.errors, []);
});

test("breakpoints past a cell's end are removed before it runs, and the message names each line", { skip }, async () => {
  const args = await page().evaluate(() => {
    const c = state.notebook.cells[1];
    c.meta.breakpoints = [1, 4, 6, 9];
    return dbgStartArgs(c, false);
  });
  assert.deepStrictEqual(args, { breakpoints: [1] });
  assert.strictEqual(await toastText(), "Removed the breakpoints on lines 4, 6 and 9, because the cell has 2 lines now.");
  assert.deepStrictEqual(app.errors, []);
});

test("the hidden state warning tells a name from a later cell, and a breakpoint takes the nearest matching line", { skip }, async () => {
  const [hidden, rematch] = await page().evaluate(() => [
    dbgHidden({ unresolved: [{ name: "x", text: "x is defined only in a later cell" }, { name: "y", text: "y isn't defined" }] },
      ["x", "y"], { x: { cell_id: "c2", source: "x = 1" }, y: { cell_id: "c1", source: "rows = [1, 2]" } },
      [{ cell_id: "c1", source: "rows = [1, 2]" }, { cell_id: "c2", source: "x = 2" }], []),
    dbgBpRematch([4], (n) => ["a", "b", "c", "print(1)"][n - 1], ["print(1)", "a", "b", "c", "print(1)"]),
  ]);
  assert.deepStrictEqual(hidden, [
    { name: "x", text: "x comes from a cell further down, which ran before this one", source: "x = 1" },
    { name: "y", text: "y isn't defined by any cell in this notebook", source: null },
  ]);
  assert.deepStrictEqual(rematch, { lines: [5], dropped: [] }, "line 4's text is at lines 1 and 5: 5 is nearer");
  assert.deepStrictEqual(app.errors, []);
});
