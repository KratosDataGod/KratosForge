// Folded sections (nbcells.js) with the other editor features: a range of selected cells skips
// the cells a folded heading hides (nbselect.js), and Find opens the section a match is in (nbfind.js).
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";

const CELLS = [
  { type: "code", cell_id: "c1", source: "a = 1", meta: {} },
  { type: "markdown", cell_id: "h", source: "# Folded", meta: { heading_collapsed: true } },
  { type: "code", cell_id: "hidden", source: "needle = 2", meta: {} },
  { type: "markdown", cell_id: "h2", source: "# Open", meta: {} },
  { type: "code", cell_id: "c2", source: "b = 3", meta: {} },
];

let app;
const page = () => app.page;
const ids = (expr) => page().evaluate(expr);

before(async () => {
  if (skip) return;
  app = await openApp({ mocks: { save_notebook: () => ({ saved: true }) } });
  await page().evaluate((cells) => {
    loadIntoState({ name: "Folded", cells });
    go("notebook");
  }, CELLS);
});

after(async () => { if (app) await app.close(); });

const selected = () => ids(() => selectedCells().map((c) => c.cell_id));

test("Shift+click, Shift+Down and select all leave the folded section's cells out", { skip }, async () => {
  assert.strictEqual(await page().locator(".cell").count(), 4);
  await page().locator(".cell").first().locator(".gutter").click({ position: { x: 10, y: 40 } });
  await page().locator(".cell").last().locator(".gutter").click({ position: { x: 10, y: 40 }, modifiers: ["Shift"] });
  assert.deepStrictEqual(await selected(), ["c1", "h", "h2", "c2"]);
  await page().locator(".cell").first().locator(".gutter").click({ position: { x: 10, y: 40 } });
  await page().keyboard.press("Escape");
  await page().keyboard.press("Shift+ArrowDown");
  await page().keyboard.press("Shift+ArrowDown");
  assert.deepStrictEqual(await selected(), ["c1", "h", "h2"]);
  await page().keyboard.press("Control+a");
  assert.deepStrictEqual(await selected(), ["c1", "h", "h2", "c2"]);
  await page().keyboard.press("Escape");
});

test("Find opens the folded section that holds a match", { skip }, async () => {
  await page().locator(".cells").click({ position: { x: 5, y: 5 } });
  await page().keyboard.press("Control+h");
  const bar = page().locator(".nb-find");
  await bar.getByLabel("Find in notebook").fill("needle");
  await bar.getByText(/of 1/).waitFor();
  await bar.getByLabel("Find in notebook").press("Enter");
  await page().waitForFunction(() => document.querySelectorAll(".cell").length === 5);
  assert.strictEqual(await ids(() => state.notebook.cells.find((c) => c.id === state.selected).cell_id), "hidden");
  assert.strictEqual(await ids(() => state.notebook.cells[1].meta.heading_collapsed), undefined);
  assert.strictEqual(await bar.count(), 1);
  assert.deepStrictEqual(app.errors, []);
});

const reload = () => page().evaluate((cells) => { loadIntoState({ name: "Folded", cells: structuredClone(cells) }); go("notebook"); }, CELLS);
const order = () => ids(() => state.notebook.cells.map((c) => c.cell_id));
const shown = () => ids(() => [...document.querySelectorAll(".cell")].map((e) => state.notebook.cells.find((c) => String(c.id) === e.dataset.cell).cell_id));

test("cells selected next to each other on screen move together across a folded section, which keeps its cells", { skip }, async () => {
  await reload();
  await page().evaluate(() => { selectCell(state.notebook.cells[3]); enterCommandMode(state.notebook.cells[3]); });  // "# Open" (its gutter holds the fold chevron)
  await page().keyboard.press("Shift+ArrowUp");
  assert.deepStrictEqual(await selected(), ["h", "h2"]);
  await page().keyboard.press("Alt+ArrowUp");
  assert.deepStrictEqual(await order(), ["h", "hidden", "h2", "c1", "c2"]);
  assert.deepStrictEqual(await shown(), ["h", "h2", "c1", "c2"]);
  assert.strictEqual(await page().locator("#toast", { hasText: "next to each other" }).count(), 0);
});

test("moving a folded heading takes its hidden cells, and a cell that showed isn't hidden by the move", { skip }, async () => {
  await reload();
  await page().evaluate(() => moveCell(-1, state.notebook.cells[1]));  // the cell's Move up button
  assert.deepStrictEqual(await order(), ["h", "hidden", "c1", "h2", "c2"]);
  // c1 is now under "# Folded": the section opens rather than hide it.
  assert.deepStrictEqual(await shown(), ["h", "hidden", "c1", "h2", "c2"]);
  await reload();
  await page().evaluate(() => moveCell(1, state.notebook.cells[1]));
  assert.deepStrictEqual(await order(), ["c1", "h2", "h", "hidden", "c2"]);
  assert.deepStrictEqual(await shown(), ["c1", "h2", "h", "hidden", "c2"]);
});

test("dragging a folded heading below another cell brings its hidden cells", { skip }, async () => {
  await reload();
  await page().evaluate(() => { const [c1, h, , , c2] = state.notebook.cells; moveCellTo(h, c2, true); });
  assert.deepStrictEqual(await order(), ["c1", "h2", "c2", "h", "hidden"]);
  assert.deepStrictEqual(await shown(), ["c1", "h2", "c2", "h"]);
  assert.deepStrictEqual(app.errors, []);
});

test("a cell added or pasted below a folded heading opens the section instead of going in hidden", { skip }, async () => {
  await reload();
  await page().evaluate(() => { selectCell(state.notebook.cells[1]); enterCommandMode(state.notebook.cells[1]); });
  await page().keyboard.press("b");
  const added = await ids(() => state.notebook.cells[2]);
  assert.strictEqual(added.type, "code");
  assert.strictEqual(added.source, "");
  assert.strictEqual(await page().locator(`[data-cell="${added.id}"]`).count(), 1, "the new cell is on screen");
  assert.strictEqual(await ids(() => document.activeElement?.closest("[data-cell]")?.dataset.cell), String(added.id));
  await page().keyboard.type("typed = 1");
  assert.strictEqual(await ids(() => state.notebook.cells[2].source), "typed = 1");

  await reload();
  await page().evaluate(() => { const [c1, h] = state.notebook.cells; copyCell(c1); pasteCell(h, 1); });
  assert.deepStrictEqual(await ids(() => state.notebook.cells.map((c) => c.source)), ["a = 1", "# Folded", "a = 1", "needle = 2", "# Open", "b = 3"]);
  assert.strictEqual(await page().locator(".cell").count(), 6);

  await reload();
  await page().evaluate(() => { state.notebook.cells[3].meta.heading_collapsed = true; renderNotebook(); });  // fold "# Open" too
  await page().getByRole("button", { name: "Code" }).last().click();  // + Code at the end of the notebook
  assert.strictEqual(await page().locator(".cell").count(), 5);  // c1, h, h2, c2 and the new cell
  assert.deepStrictEqual(app.errors, []);
});
