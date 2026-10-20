// Several cells selected and running part of a notebook (nbselect.js), against the real engine:
// Shift+Down and Shift+click select a range, Run selected runs only those cells, Ctrl+Shift+Enter
// runs the selected text or the cursor's line, and DD / Z delete and restore the whole selection.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { playwright, openApp } = require("./harness.cjs");

const SHOTS = process.env.SCREENSHOT_DIR;
const skip = !playwright && "Playwright is not installed";

const CELLS = [
  { type: "code", cell_id: "c1", source: "a = 1", meta: {} },
  { type: "code", cell_id: "c2", source: "b = a + 1", meta: {} },
  { type: "code", cell_id: "c3", source: "c = b + 1", meta: {} },
  { type: "code", cell_id: "c4", source: "for i in range(3):\n    print('line', i * 10)\nprint('end')", meta: {} },
  { type: "code", cell_id: "c5", source: "%%sql\nSELECT 1 AS one;\nSELECT 2 AS two", meta: {} },
];

let app;
const page = () => app.page;
const cell = (i) => page().locator(".cell").nth(i);

before(async () => {
  if (skip) return;
  app = await openApp({
    engine: ["start_cell", "poll_cell", "kernel_variables", "kernel_complete", "interrupt_cell"],
    mocks: { save_notebook: () => ({ saved: true }) },
  });
  await page().evaluate((cells) => {
    loadIntoState({ name: "Parts", cells });
    state.ribbonTab = "run";
    go("notebook");
  }, CELLS);
});

after(async () => { if (app) await app.close(); });

const statuses = () => page().evaluate(() => state.notebook.cells.map((c) => c.status));

test("Shift+Down selects a range and Run selected runs only those cells", { skip }, async () => {
  await cell(0).locator(".gutter").click({ position: { x: 10, y: 40 } });
  await page().keyboard.press("Escape");
  await page().keyboard.press("Shift+ArrowDown");
  await page().locator(".nb-sel-bar").getByText("2 cells selected").waitFor();
  assert.strictEqual(await page().locator(".cell.multi").count(), 2);
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "notebook-select.png") });
  await page().locator(".nb-sel-bar").getByRole("button", { name: "Run selected" }).click();
  await page().waitForFunction(() => state.notebook.cells[1].status === "ok");
  assert.deepStrictEqual(await statuses(), ["ok", "ok", null, null, null]);
  await page().waitForFunction(() => /Last run: 2 cells in/.test(document.querySelector(".statusbar .last-run")?.textContent));
});

test("Shift+click extends the range; a plain click ends it", { skip }, async () => {
  await cell(0).locator(".gutter").click({ position: { x: 10, y: 40 } });
  await cell(2).locator(".gutter").click({ position: { x: 10, y: 40 }, modifiers: ["Shift"] });
  await page().locator(".nb-sel-bar").getByText("3 cells selected").waitFor();
  await cell(1).locator(".gutter").click({ position: { x: 10, y: 40 } });
  assert.strictEqual(await page().locator(".nb-sel-bar").count(), 0);
});

test("Ctrl+Shift+Enter runs the cursor's line, dedented, or the selected text", { skip }, async () => {
  const editor = cell(3).locator(".cm-content");
  await editor.click();
  await page().keyboard.press("Control+Home");
  await page().keyboard.press("ArrowDown");  // on "    print('line', i * 10)", where i isn't defined yet
  await page().evaluate(() => rpc("start_cell", { code: "i = 4" }));
  await page().waitForFunction(() => rpc("poll_cell", {}).then((s) => s.status !== "running"));
  await page().keyboard.press("Control+Shift+Enter");
  await cell(3).locator(".cell-results").getByText("line 40").waitFor();
  await cell(3).getByText("Ran the selection on this PC").waitFor();
  assert.strictEqual(await page().evaluate(() => state.notebook.cells[3].source), CELLS[3].source);
  // A SQL cell runs the selected statement as SQL.
  const sql = cell(4).locator(".cm-content");
  await sql.click();
  await page().keyboard.press("Control+End");
  await page().keyboard.press("Shift+Home");
  await page().keyboard.press("Control+Shift+Enter");
  await cell(4).locator(".cell-results").getByRole("button", { name: "two" }).waitFor();
});

test("DD deletes the selected cells and Z puts them back in place", { skip }, async () => {
  await cell(1).locator(".gutter").click({ position: { x: 10, y: 40 } });
  await page().keyboard.press("Shift+ArrowDown");
  await page().keyboard.press("d");
  await page().keyboard.press("d");
  assert.deepStrictEqual(await page().evaluate(() => state.notebook.cells.map((c) => c.cell_id)), ["c1", "c4", "c5"]);
  await page().keyboard.press("z");
  assert.deepStrictEqual(await page().evaluate(() => state.notebook.cells.map((c) => c.source)), CELLS.map((c) => c.source));
});

test("Shift+M merges the selected cells into the first", { skip }, async () => {
  await cell(0).locator(".gutter").click({ position: { x: 10, y: 40 } });
  await page().keyboard.press("Escape");
  await page().keyboard.press("Shift+ArrowDown");
  await page().keyboard.press("Shift+ArrowDown");
  await page().keyboard.press("Shift+M");
  const sources = await page().evaluate(() => state.notebook.cells.map((c) => c.source));
  assert.strictEqual(sources.length, 3);
  assert.strictEqual(sources[0], "a = 1\nb = a + 1\nc = b + 1");
  assert.deepStrictEqual(app.errors, []);
});
