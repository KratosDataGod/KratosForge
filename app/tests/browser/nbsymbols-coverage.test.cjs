// Go to definition, find references and rename (nbsymbols.js) where the engine's answer isn't the easy
// one: no name at the cursor, a name defined nowhere in the notebook, cells the engine couldn't read, a
// place in a hidden cell or past the end of its text, a new name that isn't a Python name, a rename that
// would touch a frozen cell, and an engine that fails. notebook_symbol is mocked per test, so each case
// says exactly what the engine answered.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
let app;
const page = () => app.page;

const CELLS = [
  { type: "code", cell_id: "c1", source: "rate = 2\nprint(rate)", meta: {} },
  { type: "code", cell_id: "c2", source: "x = rate * 3", meta: { source_hidden: true } },
  { type: "code", cell_id: "c3", source: "rate = 4", meta: { frozen: true } },
];

// What notebook_symbol answers: a function of its params, set per test.
let answer = () => ({ name: null, places: [] });
const place = (cell, from, to, line, text, kind = "use") => ({ cell, from, to, line, text, kind });

before(async () => {
  if (skip) return;
  app = await openApp({ mocks: { notebook_symbol: (p) => answer(p), save_notebook: () => ({ saved: true }) } });
});
after(async () => { await app?.close(); });

beforeEach(async () => {
  if (skip) return;
  answer = () => ({ name: null, places: [] });
  await page().evaluate((cells) => {
    if (typeof symHide === "function") symHide();
    const t = document.getElementById("toast"); t.hidden = true; t.textContent = "";
    loadIntoState({ name: `Symbols ${Math.random()}`, cells: JSON.parse(JSON.stringify(cells)) });
    go("notebook");
  }, CELLS);
  await page().waitForFunction(() => [0, 2].every((i) => editors.get(state.notebook.cells[i].id)));  // cell 2's source is hidden
});

// Puts the cursor at the start of cell i's editor.
const focusCell = (i) => page().evaluate((i) => {
  const view = editors.get(state.notebook.cells[i].id);
  view.focus();
  view.dispatch({ selection: { anchor: 0 } });
}, i);
const toastSays = (text) => page().waitForFunction((t) => document.getElementById("toast").textContent === t, text);
const sources = () => page().evaluate(() => state.notebook.cells.map((c) => editors.get(c.id)?.state.doc.toString() ?? c.source));

test("with no name at the cursor, each key says to put the cursor on one", { skip }, async () => {
  await focusCell(0);
  await page().keyboard.press("F12");
  await toastSays("Put the cursor on a name to go to its definition.");
  await page().keyboard.press("Shift+F12");
  await toastSays("Put the cursor on a name to find where it's used.");
  await page().keyboard.press("F2");
  await toastSays("Put the cursor on a name to rename it.");
  assert.ok(await page().locator(".nb-sym").isHidden());
  // The engine is sent every cell, the cell the cursor is in, and the cursor.
  const sent = app.calls.filter((c) => c.method === "notebook_symbol").at(-1).params;
  assert.deepStrictEqual({ cell: sent.cell, offset: sent.offset, name: sent.name, cells: sent.cells.map((c) => c.source) },
    { cell: 0, offset: 0, name: null, cells: CELLS.map((c) => c.source) });
});

test("a name defined nowhere in the notebook isn't gone to or renamed, and unreadable cells are named", { skip }, async () => {
  answer = () => ({ name: "spark", places: [place(0, 0, 5, 1, "spark")], skipped: [{ cell: 1 }] });
  await focusCell(0);
  await page().keyboard.press("F12");
  await toastSays("spark isn't defined in this notebook's Python cells: it's built in, or comes from the session. Cell 2 has Python the editor can't read, so it was left out.");
  await page().keyboard.press("F2");
  await toastSays("spark isn't defined in this notebook's Python cells, so it isn't renamed here.");
  assert.ok(await page().locator(".nb-sym").isHidden());
});

test("an engine that fails says why in a toast and opens nothing", { skip }, async () => {
  answer = () => { throw new Error("The engine stopped."); };
  await focusCell(0);
  await page().keyboard.press("Shift+F12");
  await toastSays("The engine stopped.");
  assert.ok(await page().locator(".nb-sym").isHidden());
});

test("references note the cells left out; going to one shows a hidden cell, and a stale place only scrolls to its cell", { skip }, async () => {
  answer = () => ({ name: "rate", skipped: [{ cell: 1 }, { cell: 2 }], places: [
    place(0, 0, 4, 1, "rate = 2", "definition"),
    place(1, 4, 8, 1, "x = rate * 3"),
    place(2, 0, 400, 1, "rate = 4", "definition"),
  ] });
  await focusCell(0);
  await page().keyboard.press("Shift+F12");
  const pop = page().locator(".nb-sym");
  await pop.waitFor();
  assert.strictEqual(await pop.getAttribute("aria-label"), "Where rate is used");
  assert.strictEqual(await pop.locator(".nb-intel-kind").first().innerText(), "3 places in 3 cells");
  assert.deepStrictEqual(await pop.locator(".nb-sym-where").allInnerTexts(), ["Cell 1, line 1", "Cell 2, line 1", "Cell 3, line 1"]);
  assert.strictEqual(await pop.locator(".nb-sym-note").innerText(), "Cells 2, 3 have Python the editor can't read, so they were left out.");
  await pop.locator(".nb-sym-item").nth(1).click();
  await pop.waitFor({ state: "hidden" });
  await page().waitForFunction(() => state.selected === state.notebook.cells[1].id && !state.notebook.cells[1].meta.source_hidden);
  const sel = await page().evaluate(() => { const v = editors.get(state.notebook.cells[1].id); const { from, to } = v.state.selection.main; return v.state.sliceDoc(from, to); });
  assert.strictEqual(sel, "rate", "the hidden cell is shown and the name selected");

  await focusCell(0);
  await page().keyboard.press("Shift+F12");
  await pop.waitFor();
  await pop.locator(".nb-sym-item").nth(2).click();
  await page().waitForFunction(() => state.selected === state.notebook.cells[2].id);
  const cell3 = await page().evaluate(() => { const v = editors.get(state.notebook.cells[2].id); return v.state.selection.main.empty; });
  assert.ok(cell3, "a place past the end of the cell's text selects nothing in it");

  // Esc closes the list and gives focus back to the editor it came from.
  await focusCell(0);
  await page().keyboard.press("Shift+F12");
  await pop.waitFor();
  await page().keyboard.press("Escape");
  await pop.waitFor({ state: "hidden" });
  assert.ok(await page().evaluate(() => editors.get(state.notebook.cells[0].id).hasFocus));
});

test("rename refuses a name Python can't use, closes on the same name, and leaves frozen cells as they are", { skip }, async () => {
  const places = [place(0, 0, 4, 1, "rate = 2", "definition"), place(0, 15, 19, 2, "print(rate)"), place(2, 0, 4, 1, "rate = 4", "definition")];
  answer = ({ name }) => (name && name !== "rate" ? { name, places: [], anywhere: 0 } : { name: "rate", places, skipped: [{ cell: 1 }] });
  await focusCell(0);
  await page().keyboard.press("F2");
  const input = page().getByLabel("New name for rate");
  await input.waitFor();
  assert.strictEqual(await page().locator(".nb-sym .nb-sym-note").innerText(), "Renames 3 places. Enter to rename, Esc to cancel.");
  await input.fill("2fast");
  await input.press("Enter");
  await page().locator(".nb-sym").getByText("That isn't a Python name: use letters, digits and underscores, not starting with a digit.").waitFor();
  await input.fill("rate");
  await input.press("Enter");
  await page().locator(".nb-sym").waitFor({ state: "hidden" });
  assert.deepStrictEqual(await sources(), CELLS.map((c) => c.source), "the same name changes nothing");

  await focusCell(0);
  await page().keyboard.press("F2");
  await input.waitFor();
  await input.fill("fee");
  await input.press("Enter");
  await toastSays("Renamed rate to fee in 2 places across 1 cell. 1 in frozen cells was left as it is. Cell 2 has Python the editor can't read, so it was left out.");
  assert.deepStrictEqual(await sources(), ["fee = 2\nprint(fee)", "x = rate * 3", "rate = 4"]);
  assert.ok(await page().evaluate(() => state.notebook.dirty));
  const asked = app.calls.filter((c) => c.method === "notebook_symbol").slice(-2).map((c) => c.params.name);
  assert.deepStrictEqual(asked, ["fee", "rate"], "it checks the new name is free, then reads the places again");
});
