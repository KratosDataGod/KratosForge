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
