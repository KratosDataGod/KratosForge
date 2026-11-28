// Go to definition (F12), find references (Shift+F12) and rename (F2) across a notebook's Python
// cells (nbsymbols.js) against the real engine (notebook_symbol): the definition in another cell is
// selected, the references list every cell, and a rename changes the name everywhere but a function's
// own argument, attributes and strings.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { playwright, openApp } = require("./harness.cjs");

const SHOTS = process.env.SCREENSHOT_DIR;
const skip = !playwright && "Playwright is not installed";

const CELLS = [
  { type: "code", cell_id: "c1", source: "total = 0\nrows = [1, 2]", meta: {} },
  { type: "code", cell_id: "c2", source: "for r in rows:\n    total += r\nprint(total, 'total')", meta: {} },
  { type: "code", cell_id: "c3", source: "def double(total):\n    return total * 2\n\ndf.total = double(total)", meta: {} },
];

let app;
const page = () => app.page;
const sources = () => page().evaluate(() => state.notebook.cells.map((c) => editors.get(c.id)?.state.doc.toString() ?? c.source));

before(async () => {
  if (skip) return;
  app = await openApp({ engine: ["notebook_symbol", "notebook_syntax"], mocks: { save_notebook: () => ({ saved: true }) } });
  await page().evaluate((cells) => {
    loadIntoState({ name: "Symbols", cells });
    go("notebook");
  }, CELLS);
});

after(async () => { if (app) await app.close(); });

// Puts the cursor in cell i, just inside the nth `text`.
async function cursor(i, text, nth = 0) {
  await page().evaluate(([i, text, nth]) => {
    const view = editors.get(state.notebook.cells[i].id);
    let at = -1;
    for (let k = 0; k <= nth; k += 1) at = view.state.doc.toString().indexOf(text, at + 1);
    view.focus();
    view.dispatch({ selection: { anchor: at + 1 } });
  }, [i, text, nth]);
}

test("F12 selects the definition in another cell", { skip }, async () => {
  await cursor(2, "total", 3);  // double(total), the notebook's total
  await page().keyboard.press("F12");
  await page().waitForFunction(() => state.selected === state.notebook.cells[1].id);
  const sel = await page().evaluate(() => {
    const view = editors.get(state.notebook.cells[1].id);
    const { from, to } = view.state.selection.main;
    return view.state.sliceDoc(from, to) + "@" + view.state.doc.lineAt(from).number;
  });
  assert.strictEqual(sel, "total@2");  // total += r, the last definition before the cursor
});

test("Shift+F12 lists every place, and a click on one goes there", { skip }, async () => {
  await cursor(0, "total");
  await page().keyboard.press("Shift+F12");
  const pop = page().locator(".nb-sym");
  await pop.waitFor();
  assert.match(await pop.innerText(), /4 places in 3 cells/);
  assert.strictEqual(await pop.locator(".nb-sym-item").count(), 4);
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "notebook-references.png") });
  await pop.locator(".nb-sym-item").last().click();
  await pop.waitFor({ state: "hidden" });
  await page().waitForFunction(() => state.selected === state.notebook.cells[2].id);
});

test("F2 refuses a keyword or a name in use, then renames everywhere it should", { skip }, async () => {
  await cursor(0, "total");
  await page().keyboard.press("F2");
  const input = page().locator(".nb-sym-input");
  await input.waitFor();
  await input.fill("class");
  await input.press("Enter");
  await page().locator(".nb-sym").getByText("is a Python keyword").waitFor();
  await input.fill("rows");
  await input.press("Enter");
  await page().locator(".nb-sym").getByText("already used in this notebook").waitFor();
  await input.fill("grand_total");
  await input.press("Enter");
  await page().locator(".nb-sym").waitFor({ state: "hidden" });
  assert.deepStrictEqual(await sources(), [
    "grand_total = 0\nrows = [1, 2]",
    "for r in rows:\n    grand_total += r\nprint(grand_total, 'total')",
    "def double(total):\n    return total * 2\n\ndf.total = double(grand_total)",
  ]);
});

test("Esc closes the rename box and leaves the cells as they were", { skip }, async () => {
  const before = await sources();
  await cursor(0, "rows");
  await page().keyboard.press("F2");
  await page().locator(".nb-sym-input").waitFor();
  await page().keyboard.press("Escape");
  await page().locator(".nb-sym").waitFor({ state: "hidden" });
  assert.deepStrictEqual(await sources(), before);
});
