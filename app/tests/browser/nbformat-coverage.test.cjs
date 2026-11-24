// Format code (nbformat.js) beyond notebook-format.test.cjs: Format cell on a frozen cell, Format
// notebook with no code cell, a cell or notebook that is already formatted, and two cells the engine
// couldn't read. notebook_format is mocked: a source with "#bad" can't be read, "#ok" is formatted.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
let app;
const page = () => app.page;

before(async () => {
  if (skip) return;
  app = await openApp({ mocks: {
    notebook_format: ({ sources }) => sources.map((s) => (s.includes("#bad")
      ? { error: "Not formatted, because Python can't read it (line 1: invalid syntax)." }
      : { changed: false, source: s })),
  } });
});
after(async () => { await app?.close(); });

const load = (cells) => page().evaluate((cells) => {
  const t = document.getElementById("toast"); t.hidden = true; t.textContent = "";
  loadIntoState({ name: "Tidy", cells }); state.ribbonTab = "edit"; go("notebook");
}, cells);
beforeEach(async () => { if (!skip) app.calls.length = 0; });

const formats = () => app.calls.filter((c) => c.method === "notebook_format");
const toastSays = (text) => page().waitForFunction((t) => document.getElementById("toast").textContent.startsWith(t), text);
const cellEl = (i) => page().locator(".cell").nth(i);

test("Format cell on a frozen cell says to unfreeze it and doesn't ask the engine", { skip }, async () => {
  await load([{ type: "code", source: "x=1", meta: { frozen: true } }]);
  await cellEl(0).locator(".gutter").click({ position: { x: 10, y: 30 } });
  await cellEl(0).getByRole("button", { name: "More cell actions" }).click();
  await page().getByRole("menuitem", { name: "Format cell" }).click();
  await toastSays("This cell is frozen. Unfreeze it on the Edit tab to change it.");
  assert.deepStrictEqual(formats(), []);
});

test("Format notebook with only text cells says there's no code to format", { skip }, async () => {
  await load([{ type: "markdown", source: "# Notes", meta: {} }, { type: "code", source: "y=2", meta: { frozen: true } }]);
  await page().getByRole("button", { name: "Format notebook" }).click();
  await toastSays("There's no code cell to format.");
  assert.deepStrictEqual(formats(), []);
});

test("Shift+Alt+F on a cell that's already formatted says so and leaves the notebook saved", { skip }, async () => {
  await load([{ type: "code", source: "x = 1  #ok", meta: {} }]);
  await cellEl(0).locator(".cm-content").click();
  await page().keyboard.press("Shift+Alt+F");
  await toastSays("The cell is already formatted.");
  assert.deepStrictEqual(formats()[0].params, { sources: ["x = 1  #ok"] });
  assert.strictEqual(await page().evaluate(() => state.notebook.dirty), false);
});

test("Format notebook with nothing to change and two cells it can't read names the first", { skip }, async () => {
  await load([{ type: "code", source: "a = 1  #ok", meta: {} }, { type: "code", source: "def f( :  #bad", meta: {} },
    { type: "code", source: "if :  #bad", meta: {} }]);
  await page().getByRole("button", { name: "Format notebook" }).click();
  await toastSays("Every code cell is already formatted. 2 cells were left as written: cell 2. Not formatted, because Python can't read it (line 1: invalid syntax).");
  await page().locator("#toast").getByRole("button", { name: "Show cell" }).click();
  assert.strictEqual(await page().evaluate(() => state.notebook.cells.findIndex((c) => c.id === state.selected)), 1);
});
