// Command mode and the cell editor's own keys (notebook.js), as Jupyter, Fabric and Databricks have
// them: Esc and Enter, A/B add, M/Y switch type, C/X/V copy, cut and paste, DD and Z, O hides output,
// Shift+L line numbers, and while editing Ctrl+/ comments, Ctrl+Shift+- splits, Alt+Enter runs and adds.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";

const CELLS = [
  { type: "code", cell_id: "c1", source: "a = 1", meta: {} },
  { type: "code", cell_id: "c2", source: "b = 2", meta: {} },
  { type: "markdown", cell_id: "m1", source: "Some notes", meta: {} },
];

let app;
const page = () => app.page;
const sources = () => page().evaluate(() => state.notebook.cells.map((c) => `${c.type}:${c.source}`));
const selectedIndex = () => page().evaluate(() => state.notebook.cells.findIndex((c) => c.id === state.selected));
const editing = () => page().evaluate(() => !!document.activeElement?.closest(".cm-editor"));

// Puts the notebook back and selects cell i in command mode.
async function reset(i = 0) {
  await page().evaluate(({ cells, i }) => {
    loadIntoState({ name: "Keys", cells: structuredClone(cells) });
    go("notebook");
    selectCell(state.notebook.cells[i]);
    enterCommandMode(state.notebook.cells[i]);
  }, { cells: CELLS, i });
}

before(async () => {
  if (skip) return;
  app = await openApp({
    engine: ["start_cell", "poll_cell", "kernel_variables", "kernel_complete", "interrupt_cell", "notebook_syntax"],
    mocks: { save_notebook: () => ({ saved: true }) },
  });
});

after(async () => { if (app) await app.close(); });

test("Enter edits the selected cell and Esc goes back to command mode", { skip }, async () => {
  await reset(1);
  await page().keyboard.press("Enter");
  assert.strictEqual(await editing(), true);
  await page().keyboard.press("End");
  await page().keyboard.type(" + 1");
  await page().keyboard.press("Escape");
  assert.strictEqual(await editing(), false);
  assert.strictEqual(await selectedIndex(), 1);
  assert.deepStrictEqual((await sources())[1], "code:b = 2 + 1");
});

test("Esc with suggestions on screen closes them first, and a second Esc leaves the cell", { skip }, async () => {
  await reset(1);
  await page().keyboard.press("Enter");
  await page().keyboard.press("End");
  await page().keyboard.type("\nprin");
  await page().keyboard.press("Control+Space");
  await page().locator(".cm-tooltip-autocomplete").waitFor();
  await page().keyboard.press("Escape");
  assert.strictEqual(await page().locator(".cm-tooltip-autocomplete").count(), 0);
  assert.strictEqual(await editing(), true);
  await page().keyboard.press("Escape");
  assert.strictEqual(await editing(), false);
});

test("A and B add a code cell above or below and start editing it", { skip }, async () => {
  await reset(1);
  await page().keyboard.press("a");
  assert.deepStrictEqual(await sources(), ["code:a = 1", "code:", "code:b = 2", "markdown:Some notes"]);
  assert.strictEqual(await selectedIndex(), 1);
  assert.strictEqual(await editing(), true);
  await page().keyboard.press("Escape");
  await page().keyboard.press("b");
  assert.deepStrictEqual(await sources(), ["code:a = 1", "code:", "code:", "code:b = 2", "markdown:Some notes"]);
  assert.strictEqual(await selectedIndex(), 2);
});

test("M makes a code cell Markdown and Y makes it code again", { skip }, async () => {
  await reset(0);
  await page().keyboard.press("m");
  assert.deepStrictEqual((await sources())[0], "markdown:a = 1");
  await page().evaluate(() => enterCommandMode(state.notebook.cells[0]));
  await page().keyboard.press("y");
  assert.deepStrictEqual((await sources())[0], "code:a = 1");
});

test("C then V pastes a copy below; X then Shift+V moves a cell above", { skip }, async () => {
  await reset(0);
  await page().keyboard.press("c");
  await page().evaluate(() => { selectCell(state.notebook.cells[1]); enterCommandMode(state.notebook.cells[1]); });
  await page().keyboard.press("v");
  assert.deepStrictEqual(await sources(), ["code:a = 1", "code:b = 2", "code:a = 1", "markdown:Some notes"]);
  const ids = await page().evaluate(() => state.notebook.cells.map((c) => c.cell_id));
  assert.notStrictEqual(ids[2], "c1", "a pasted copy gets its own id");

  await reset(2);
  await page().keyboard.press("x");
  assert.deepStrictEqual(await sources(), ["code:a = 1", "code:b = 2"]);
  await page().evaluate(() => { selectCell(state.notebook.cells[0]); enterCommandMode(state.notebook.cells[0]); });
  await page().keyboard.press("Shift+V");
  assert.deepStrictEqual(await sources(), ["markdown:Some notes", "code:a = 1", "code:b = 2"]);
});

test("DD deletes the cell and Z puts it back where it was", { skip }, async () => {
  await reset(1);
  await page().keyboard.press("d");
  await page().keyboard.press("d");
  assert.deepStrictEqual(await sources(), ["code:a = 1", "markdown:Some notes"]);
  await page().keyboard.press("z");
  assert.deepStrictEqual(await sources(), ["code:a = 1", "code:b = 2", "markdown:Some notes"]);
  assert.deepStrictEqual(await page().evaluate(() => state.notebook.cells.map((c) => c.cell_id)), ["c1", "c2", "m1"]);
});

test("O hides and shows a cell's output, and Shift+L toggles line numbers", { skip }, async () => {
  await reset(0);
  await page().keyboard.press("o");
  assert.strictEqual(await page().evaluate(() => state.notebook.cells[0].meta.outputs_hidden), true);
  await page().keyboard.press("o");
  assert.ok(!(await page().evaluate(() => state.notebook.cells[0].meta.outputs_hidden)));
  const before = await page().evaluate(() => state.lineNumbers);
  await page().keyboard.press("Shift+L");
  assert.strictEqual(await page().evaluate(() => state.lineNumbers), !before);
  await page().keyboard.press("Shift+L");
  assert.strictEqual(await page().evaluate(() => state.lineNumbers), before);
});

test("while editing: Ctrl+/ comments the line and Ctrl+Shift+- splits the cell at the cursor", { skip }, async () => {
  await page().evaluate((cells) => { loadIntoState({ name: "Keys", cells }); go("notebook"); },
    [{ type: "code", cell_id: "s1", source: "x = 1\ny = 2", meta: {} }]);
  await page().locator(".cell .cm-content").first().click();
  await page().keyboard.press("Control+Home");
  await page().keyboard.press("Control+/");
  assert.deepStrictEqual(await sources(), ["code:# x = 1\ny = 2"]);
  await page().keyboard.press("Control+/");
  await page().keyboard.press("ArrowDown");
  await page().keyboard.press("Home");
  await page().keyboard.press("Control+Shift+Minus");
  assert.deepStrictEqual(await sources(), ["code:x = 1", "code:y = 2"]);
  assert.strictEqual(await selectedIndex(), 1);
});

test("splitting a Databricks %sql cell keeps %sql on the second half", { skip }, async () => {
  await page().evaluate((cells) => { loadIntoState({ name: "Keys", cells }); go("notebook"); },
    [{ type: "code", cell_id: "q1", source: "%sql\nselect 1\nselect 2", meta: {} }]);
  await page().locator(".cell .cm-content").first().click();
  await page().keyboard.press("Control+End");
  await page().keyboard.press("Home");
  await page().keyboard.press("Control+Shift+Minus");
  assert.deepStrictEqual(await sources(), ["code:%sql\nselect 1", "code:%sql\nselect 2"]);
});

test("merging Databricks %sql cells keeps one %sql line", { skip }, async () => {
  await page().evaluate((cells) => { loadIntoState({ name: "Keys", cells }); go("notebook"); mergeBelow(state.notebook.cells[0]); },
    [{ type: "code", cell_id: "q1", source: "%sql\nselect 1", meta: {} }, { type: "code", cell_id: "q2", source: "%sql\nselect 2", meta: {} }]);
  assert.deepStrictEqual(await sources(), ["code:%sql\nselect 1\nselect 2"]);
});

test("Alt+Enter runs the cell and adds a code cell below; Shift+Enter runs and moves on", { skip }, async () => {
  await page().evaluate((cells) => { loadIntoState({ name: "Keys", cells }); go("notebook"); },
    [{ type: "code", cell_id: "r1", source: "print('ran')", meta: {} }]);
  await page().locator(".cell .cm-content").first().click();
  await page().keyboard.press("Alt+Enter");
  await page().waitForFunction(() => state.notebook.cells.length === 2 && state.notebook.cells[0].status === "ok");
  await page().locator(".cell").first().getByText("ran", { exact: true }).waitFor();
  assert.strictEqual(await selectedIndex(), 1);
  assert.strictEqual(await editing(), true);
  await page().keyboard.type("z = 3");
  await page().keyboard.press("Shift+Enter");
  await page().waitForFunction(() => state.notebook.cells[1].status === "ok" && state.notebook.cells.length === 3);  // the last cell: a new one follows
  assert.deepStrictEqual(await sources(), ["code:print('ran')", "code:z = 3", "code:"]);
  assert.deepStrictEqual(app.errors, []);
});
