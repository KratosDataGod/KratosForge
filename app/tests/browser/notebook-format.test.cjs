// Format code (nbformat.js, code_format.py in the real engine): Format cell from Shift+Alt+F and the
// cell menu, Format notebook from the Edit tab, and the cells it leaves as they are.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { playwright, openApp } = require("./harness.cjs");

const SHOTS = process.env.SCREENSHOT_DIR;
const skip = !playwright && "Playwright is not installed";

const CELLS = [
  { type: "code", cell_id: "py", source: "import os,sys\nx={ 'a':1 }\n%time y = x\ndisplay( x )", meta: {} },
  { type: "code", cell_id: "sql", source: "%%sql\nselect  a,b from t where x=1", meta: {} },
  { type: "code", cell_id: "bad", source: "def f( :\n  pass", meta: {} },
  { type: "markdown", cell_id: "md", source: "# Notes  here", meta: {} },
  { type: "code", cell_id: "frozen", source: "z=1", meta: { frozen: true } },
];

let app;
const page = () => app.page;
const cell = (id) => page().locator(`[data-cell="${id}"]`);
const idOf = (cellId) => page().evaluate((cid) => state.notebook.cells.find((c) => c.cell_id === cid).id, cellId);
const source = (cellId) => page().evaluate((cid) => state.notebook.cells.find((c) => c.cell_id === cid).source, cellId);
const toastText = () => page().locator("#toast").textContent();

before(async () => {
  if (skip) return;
  app = await openApp({ engine: ["notebook_format", "notebook_syntax"] });
  await page().evaluate((cells) => { loadIntoState({ name: "Format", cells }); go("notebook"); }, CELLS);
});

after(async () => { if (app) await app.close(); });

test("Shift+Alt+F formats the cell being edited, and Ctrl+Z puts it back", { skip }, async () => {
  const id = await idOf("py");
  await cell(id).locator(".cm-content").click();
  await page().keyboard.press("Shift+Alt+F");
  await page().waitForFunction(() => state.notebook.cells[0].source.startsWith("import os, sys\n"));
  assert.strictEqual(await source("py"), 'import os, sys\n\nx = {"a": 1}\n%time y = x\ndisplay(x)');
  await cell(id).getByText('x = {"a": 1}').waitFor();
  assert.strictEqual(await page().evaluate(() => state.notebook.dirty), true);
  await page().keyboard.press("Control+z");
  await page().waitForFunction(() => state.notebook.cells[0].source.startsWith("import os,sys"));
});

test("Format cell in the cell menu formats a SQL cell and keeps its magic", { skip }, async () => {
  const id = await idOf("sql");
  await cell(id).locator(".gutter").click({ position: { x: 10, y: 30 } });
  await cell(id).getByRole("button", { name: "More cell actions" }).click();
  await page().getByRole("menuitem", { name: "Format cell" }).click();
  await page().waitForFunction(() => state.notebook.cells[1].source.startsWith("%%sql\nSELECT"));
  assert.match(await source("sql"), /^%%sql\nSELECT\n {2}a,\n {2}b\nFROM t\nWHERE\n {2}x = 1$/);
});

test("a cell Python can't read is left as written, with the reason", { skip }, async () => {
  const id = await idOf("bad");
  await cell(id).locator(".cm-content").click();
  await page().keyboard.press("Shift+Alt+F");
  await page().locator("#toast", { hasText: "Python can't read it" }).waitFor();
  assert.strictEqual(await source("bad"), "def f( :\n  pass");
});

test("Format notebook formats every code cell it can and points at the one it couldn't", { skip }, async () => {
  await page().evaluate(() => { state.notebook.cells[0].source = "import os,sys"; renderNotebook(); });
  await page().keyboard.press("Escape");
  await page().evaluate(() => { state.ribbonTab = "edit"; renderNotebook(); });
  await page().getByRole("button", { name: "Format notebook" }).click();
  await page().locator("#toast", { hasText: "Formatted 1 cell." }).waitFor();
  const text = await toastText();
  assert.match(text, /One cell was left as written: cell 3\. Not formatted, because Python can't read it \(line 1:/);
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "notebook-format.png") });
  assert.strictEqual(await source("py"), "import os, sys");
  assert.strictEqual(await source("md"), "# Notes  here");
  assert.strictEqual(await source("frozen"), "z=1");
  await page().locator("#toast").getByRole("button", { name: "Show cell" }).click();
  assert.strictEqual(await page().evaluate(() => state.notebook.cells.find((c) => c.id === state.selected).cell_id), "bad");
  assert.deepStrictEqual(app.errors, []);
});
