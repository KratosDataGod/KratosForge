// The notebook's Edit and View tabs and each cell's language picker (notebook.js): Parameter cell,
// Freeze, Split, Merge, Delete and Undo delete, Clear all outputs, collapse inputs and outputs, line
// numbers, and switching a cell between Python, Spark SQL, HTML and Markdown.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";

const CELLS = [
  { type: "code", cell_id: "c1", source: "a = 1", meta: {} },
  { type: "code", cell_id: "c2", source: "b = 2\nc = 3", meta: {} },
  { type: "markdown", cell_id: "m1", source: "Notes", meta: {} },
];

let app;
const page = () => app.page;
const cells = () => page().evaluate(() => state.notebook.cells.map((c) => ({ type: c.type, source: c.source, meta: c.meta })));
const sources = async () => (await cells()).map((c) => `${c.type}:${c.source}`);
const cellEl = (i) => page().locator(".cell").nth(i);
const ribbon = (name) => page().locator(".toolbar, .ribbon, header").getByRole("button", { name, exact: true }).first();

async function reset(tab, select = 0) {
  await page().evaluate(({ cells, tab, select }) => {
    loadIntoState({ name: "Tabs", cells: structuredClone(cells) });
    state.ribbonTab = tab;
    go("notebook");
    selectCell(state.notebook.cells[select]);
    enterCommandMode(state.notebook.cells[select]);
    renderNotebook();
  }, { cells: CELLS, tab, select });
}

before(async () => {
  if (skip) return;
  app = await openApp({ mocks: { save_notebook: () => ({ saved: true }) } });
});

after(async () => { if (app) await app.close(); });

test("the language picker adds and removes the cell magic, and Markdown drops the code editor", { skip }, async () => {
  await reset("edit");
  const lang = cellEl(0).locator("select.lang");
  await lang.selectOption("sql");
  assert.strictEqual((await sources())[0], "code:%%sql\na = 1");
  await lang.selectOption("html");
  assert.strictEqual((await sources())[0], "code:%%html\na = 1");
  await lang.selectOption("python");
  assert.strictEqual((await sources())[0], "code:a = 1");
  await lang.selectOption("markdown");
  assert.strictEqual((await sources())[0], "markdown:a = 1");
});

test("Parameter cell tags one cell at a time, as in Fabric", { skip }, async () => {
  await reset("edit", 0);
  await ribbon("Parameter cell").click();
  await cellEl(0).getByText("Parameters", { exact: true }).waitFor();
  await page().evaluate(() => { selectCell(state.notebook.cells[1]); renderNotebook(); });
  await ribbon("Parameter cell").click();
  const tags = (await cells()).map((c) => c.meta.tags || []);
  assert.deepStrictEqual(tags, [[], ["parameters"], []]);
});

test("a frozen cell can't be edited, deleted, split or switched to another type", { skip }, async () => {
  await reset("edit", 1);
  await ribbon("Freeze").click();
  assert.strictEqual((await cells())[1].meta.frozen, true);
  await cellEl(1).getByText("Frozen", { exact: true }).waitFor();
  assert.strictEqual(await cellEl(1).locator("select.lang").isDisabled(), true);
  assert.strictEqual(await cellEl(1).locator(".cm-content").getAttribute("contenteditable"), "false");

  await page().evaluate(() => enterCommandMode(state.notebook.cells[1]));
  await page().keyboard.press("m");  // to Markdown
  await page().keyboard.press("d");
  await page().keyboard.press("d");
  await ribbon("Split cell").click();
  assert.deepStrictEqual(await sources(), ["code:a = 1", "code:b = 2\nc = 3", "markdown:Notes"]);
  await page().locator("#toast", { hasText: "frozen" }).waitFor();

  await ribbon("Freeze").click();
  assert.strictEqual((await cells())[1].meta.frozen, undefined);
});

test("Split cell, Merge with below, Delete cell and Undo delete", { skip }, async () => {
  await reset("edit", 1);
  await page().evaluate(() => {
    const view = editors.get(state.notebook.cells[1].id);
    view.dispatch({ selection: { anchor: "b = 2\n".length } });
  });
  await ribbon("Split cell").click();
  assert.deepStrictEqual(await sources(), ["code:a = 1", "code:b = 2", "code:c = 3", "markdown:Notes"]);
  await page().evaluate(() => { selectCell(state.notebook.cells[1]); renderNotebook(); });
  await ribbon("Merge with below").click();
  assert.deepStrictEqual(await sources(), ["code:a = 1", "code:b = 2\nc = 3", "markdown:Notes"]);
  await ribbon("Delete cell").click();
  assert.deepStrictEqual(await sources(), ["code:a = 1", "markdown:Notes"]);
  await ribbon("Undo delete").click();
  assert.deepStrictEqual(await sources(), ["code:a = 1", "code:b = 2\nc = 3", "markdown:Notes"]);
});

test("Clear all outputs empties every cell's output", { skip }, async () => {
  await reset("edit");
  await page().evaluate(() => {
    state.notebook.cells.forEach((c) => { if (c.type === "code") { c.outputs = [{ type: "text", text: "old output" }]; c.status = "ok"; } });
    renderNotebook();
  });
  await page().getByText("old output").first().waitFor();
  await ribbon("Clear all outputs").click();
  assert.strictEqual(await page().getByText("old output").count(), 0);
  assert.deepStrictEqual(await page().evaluate(() => state.notebook.cells.map((c) => (c.outputs || []).length)), [0, 0, 0]);
});

test("View: collapse and expand all inputs and outputs, and line numbers", { skip }, async () => {
  await reset("view");
  await ribbon("Collapse all inputs").click();
  assert.deepStrictEqual((await cells()).map((c) => !!c.meta.source_hidden), [true, true, false]);
  assert.strictEqual(await cellEl(0).locator(".cm-editor").count(), 0);
  await ribbon("Expand all inputs").click();
  assert.strictEqual(await cellEl(0).locator(".cm-editor").count(), 1);
  await ribbon("Collapse all outputs").click();
  assert.deepStrictEqual((await cells()).map((c) => !!c.meta.outputs_hidden), [true, true, false]);
  await ribbon("Expand all outputs").click();
  assert.deepStrictEqual((await cells()).map((c) => !!c.meta.outputs_hidden), [false, false, false]);
  const on = await page().evaluate(() => state.lineNumbers);
  await ribbon("Line numbers").click();
  assert.strictEqual(await page().evaluate(() => state.lineNumbers), !on);
  assert.strictEqual(await cellEl(0).locator(".cm-lineNumbers").count(), on ? 0 : 1);
  await ribbon("Line numbers").click();
  assert.deepStrictEqual(app.errors, []);
});
