// Several cells selected (nbselect.js), the paths notebook-select and notebook-merge leave out:
// Ctrl+click adding and taking out cells, freezing and unfreezing a selection (frozen cells refuse
// delete and merge), deleting with a frozen cell kept and deleting every cell, copy and cut by key,
// moving cells that aren't next to each other, a Fabric notebook that can't run selected cells,
// plain keys that end the selection, and Ctrl+Shift+Enter on an empty line. Saving is mocked;
// what the person sees and what the notebook holds is checked.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
const CELLS = [
  { type: "code", cell_id: "c1", source: "a = 1", meta: {} },
  { type: "code", cell_id: "c2", source: "b = 2", meta: {} },
  { type: "markdown", cell_id: "c3", source: "# Notes", meta: {} },
  { type: "code", cell_id: "c4", source: "d = 4\n", meta: {} },
];

let app;
const page = () => app.page;
const cell = (i) => page().locator(".cell").nth(i);
const bar = () => page().locator(".nb-sel-bar");
const gutter = (i, modifiers) => cell(i).locator(".gutter").click({ position: { x: 10, y: 40 }, modifiers });
const sources = () => page().evaluate(() => state.notebook.cells.map((c) => c.source));
const toastSays = (re) => page().waitForFunction((src) => new RegExp(src).test(document.getElementById("toast")?.textContent || ""), re.source);

before(async () => {
  if (skip) return;
  app = await openApp({ mocks: { save_notebook: () => ({ saved: true }) } });
  app.errors.length = 0;
});
after(async () => { if (app) await app.close(); });

beforeEach(async () => {
  if (skip) return;
  await page().evaluate((cells) => {
    nbSel.ids.clear(); nbSel.anchor = null; nbSel.lastD = 0;
    loadIntoState({ name: "Picks", cells: structuredClone(cells) });
    state.ribbonTab = "run";
    go("notebook");
    const t = document.getElementById("toast"); t.hidden = true; t.textContent = "";
  }, CELLS);
  await cell(3).waitFor();
  // Command mode on the first cell.
  await gutter(0);
  await page().keyboard.press("Escape");
  app.errors.length = 0;
});

test("Ctrl+click adds a cell and takes it out again; with one left the bar goes", { skip }, async () => {
  await gutter(2, ["Control"]);
  await bar().getByText("2 cells selected").waitFor();
  assert.deepStrictEqual(await page().evaluate(() => [...nbSel.ids].map((id) => state.notebook.cells.findIndex((c) => c.id === id))), [0, 2]);
  await gutter(3, ["Control"]);
  await bar().getByText("3 cells selected").waitFor();
  await gutter(2, ["Control"]);
  await bar().getByText("2 cells selected").waitFor();
  assert.strictEqual(await page().locator(".cell.multi").count(), 2);
  await gutter(3, ["Control"]);
  await bar().waitFor({ state: "detached" });
  assert.strictEqual(await page().locator(".cell.multi").count(), 0);
  assert.deepStrictEqual(app.errors, []);
});

test("Freeze freezes the selection, which then refuses delete and merge; Unfreeze undoes it", { skip }, async () => {
  await page().keyboard.press("Shift+ArrowDown");
  await bar().getByText("2 cells selected").waitFor();
  await bar().getByRole("button", { name: "Freeze" }).click();
  assert.deepStrictEqual(await page().evaluate(() => state.notebook.cells.map((c) => !!c.meta?.frozen)), [true, true, false, false]);
  await bar().getByRole("button", { name: "Delete" }).click();
  await toastSays(/^The selected cells are frozen\. Unfreeze them to delete them\.$/);
  await bar().getByRole("button", { name: "Merge" }).click();
  await toastSays(/^A frozen cell can't be merged\. Unfreeze it first\.$/);
  assert.strictEqual((await sources()).length, 4);
  await bar().getByRole("button", { name: "Unfreeze" }).click();
  await bar().getByRole("button", { name: "Freeze" }).waitFor();
  assert.deepStrictEqual(await page().evaluate(() => state.notebook.cells.map((c) => !!c.meta?.frozen)), [false, false, false, false]);
  assert.deepStrictEqual(app.errors, []);
});

test("Delete keeps a frozen cell and says so; deleting every cell leaves one empty code cell", { skip }, async () => {
  await page().evaluate(() => { state.notebook.cells[1].meta.frozen = true; });
  await page().keyboard.press("Shift+ArrowDown");
  await bar().getByText("2 cells selected").waitFor();
  await page().keyboard.press("Delete");
  await toastSays(/^Deleted 1 cells\. Press Z to undo\. 1 frozen cell was kept\.$/);
  assert.deepStrictEqual(await sources(), ["b = 2", "# Notes", "d = 4\n"]);
  await page().evaluate(() => { state.notebook.cells[0].meta.frozen = false; });
  await page().keyboard.press("Control+a");
  await bar().getByText("3 cells selected").waitFor();
  await page().keyboard.press("d");
  await page().keyboard.press("d");
  await toastSays(/^Deleted 3 cells\. Press Z to undo\.$/);
  assert.deepStrictEqual(await page().evaluate(() => state.notebook.cells.map((c) => [c.type, c.source])), [["code", ""]]);
  assert.deepStrictEqual(app.errors, []);
});

test("C copies and X cuts the selection; Escape ends it", { skip }, async () => {
  await page().keyboard.press("Shift+ArrowDown");
  await bar().getByText("2 cells selected").waitFor();
  await page().keyboard.press("c");
  await toastSays(/^2 cells copied$/);
  assert.deepStrictEqual(await page().evaluate(() => cellClipboard.map((c) => c.source)), ["a = 1", "b = 2"]);
  await page().keyboard.press("Escape");
  await bar().waitFor({ state: "detached" });
  await gutter(2);
  await page().keyboard.press("Escape");
  await page().keyboard.press("Shift+ArrowDown");
  await bar().getByText("2 cells selected").waitFor();
  await page().keyboard.press("x");
  await toastSays(/^Deleted 2 cells\. Press Z to undo\.$/);
  assert.deepStrictEqual(await page().evaluate(() => cellClipboard.map((c) => c.source)), ["# Notes", "d = 4\n"]);
  assert.deepStrictEqual(await sources(), ["a = 1", "b = 2"]);
  assert.deepStrictEqual(app.errors, []);
});

test("cells apart don't move together, and a code and a Markdown cell aren't merged", { skip }, async () => {
  await gutter(2, ["Control"]);
  await bar().getByText("2 cells selected").waitFor();
  await page().keyboard.press("Alt+ArrowDown");
  await toastSays(/^Select cells next to each other to move them together\.$/);
  await page().keyboard.press("Shift+M");
  await toastSays(/^Select only code cells or only Markdown cells to merge them\.$/);
  assert.deepStrictEqual(await sources(), CELLS.map((c) => c.source));
  assert.deepStrictEqual(app.errors, []);
});

test("a plain arrow or a one-cell key like B ends the selection", { skip }, async () => {
  await page().keyboard.press("Shift+ArrowDown");
  await bar().getByText("2 cells selected").waitFor();
  await page().keyboard.press("ArrowDown");
  await bar().waitFor({ state: "detached" });
  await page().keyboard.press("Shift+ArrowUp");
  await bar().getByText("2 cells selected").waitFor();
  await page().keyboard.press("b");
  await bar().waitFor({ state: "detached" });
  assert.strictEqual((await sources()).length, 5, "B still adds a cell below");
  assert.deepStrictEqual(app.errors, []);
});

test("a Fabric notebook can't run selected cells: the button says why and Ctrl+Enter tells", { skip }, async () => {
  await page().evaluate(() => { state.notebook.compute = "fabric"; });
  await page().keyboard.press("Shift+ArrowDown");
  const run = bar().getByRole("button", { name: "Run selected" });
  await run.waitFor();
  assert.ok(await run.isDisabled());
  assert.strictEqual(await run.getAttribute("title"), "Fabric runs the whole notebook. Switch Runs on to This PC to run selected cells.");
  await page().keyboard.press("Control+Enter");
  await toastSays(/^This notebook runs in Fabric, which runs the whole notebook\. Switch Runs on to This PC to run selected cells\.$/);
  assert.deepStrictEqual(app.calls.filter((c) => c.method === "start_cell"), []);
  assert.deepStrictEqual(app.errors, []);
});

test("Ctrl+Shift+Enter on an empty line says to select some code and runs nothing", { skip }, async () => {
  await cell(3).locator(".cm-content").click();
  await page().keyboard.press("Control+End");
  await page().keyboard.press("Control+Shift+Enter");
  await toastSays(/^Select some code, or put the cursor on a line with code, to run it\.$/);
  assert.deepStrictEqual(app.calls.filter((c) => c.method === "start_cell"), []);
  assert.deepStrictEqual(app.errors, []);
});
