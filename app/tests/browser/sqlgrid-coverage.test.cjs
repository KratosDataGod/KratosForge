// The SQL result grid (sqlgrid.js), the paths sql-editor, sql-exec and result-viewer leave out: the
// grid's keys (arrows, Home, End, Page Up and Down, Ctrl with them, Ctrl+A, Shift+F10), a row's number
// selecting the row, a drag selecting a block, a double-click opening a value in a reordered grid,
// Escape clearing a filter box, no rows matching, filters on hidden columns, a column width set by
// dragging and reset with Enter, Copy of the whole page, moving a column down in Columns..., Columns...
// closing when a new result has other columns, a value that is a JSON object or text that only looks
// like JSON, and statistics that failed or show the most common values. The grid is drawn into a box
// with what it calls back recorded; what the person sees and what is called back is checked.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
const COLUMNS = [{ name: "id", type: "int" }, { name: "name", type: "varchar" }, { name: "doc", type: "varchar" }];
const ROWS = Array.from({ length: 30 }, (_, k) => [k + 1, `n${k + 1}`, k === 0 ? "{\"a\": [1, 2]}" : "{not json"]);
const RESULT = { columns: COLUMNS, rows: ROWS, offset: 0, total: 30, all_rows: 30, sort: [] };

let app;
const page = () => app.page;
const box = () => page().locator("#t-box");
const grid = () => box().locator("table.sql-result-table");
const cell = (r, c) => grid().locator(`tbody tr[data-r="${r}"] td[data-c="${c}"]`);
const sel = () => page().evaluate(() => window.__g.sel);
const calls = () => page().evaluate(() => window.__on);

before(async () => {
  if (skip) return;
  app = await openApp({ mocks: {} });
  app.errors.length = 0;
});
after(async () => { if (app) await app.close(); });

beforeEach(async () => {
  if (skip) return;
  await page().evaluate(() => { closeDialog(); closeDialog(); document.getElementById("t-box")?.remove(); });
  app.errors.length = 0;
});

// Draws the grid for result r with grid state g (window.__g), recording on.* calls in window.__on.
const draw = (r = RESULT, g = {}) => page().evaluate(([r, g]) => {
  let b = document.getElementById("t-box");
  if (!b) { b = document.createElement("div"); b.id = "t-box"; b.className = "sql-grid"; document.body.append(b); }
  window.__g = window.__g && g.keep ? window.__g : { id: "g1", sort: [], filters: {}, filterOn: false, filterError: null, ...g };
  window.__on = [];
  const rec = (name) => (...a) => { window.__on.push([name, ...a.map((x) => (x instanceof Element ? x.tagName : x))]); return name === "close" ? false : undefined; };
  const redraw = () => b.replaceChildren(...sqlResultGrid(r, window.__g, { sort: rec("sort"), filter: rec("filter"), select: () => {},
    copy: rec("copy"), open: rec("open"), menu: rec("menu"), close: rec("close") }));
  window.__redraw = redraw;
  redraw();
}, [r, g]);

test("the grid's keys move the focused cell, select the page, and Shift+F10 asks for the menu", { skip }, async () => {
  await draw();
  await grid().focus();
  assert.deepStrictEqual(await sel(), { a: { r: 0, c: 0 }, f: { r: 0, c: 0 } });
  const at = async (key) => { await page().keyboard.press(key); return (await sel()).f; };
  assert.deepStrictEqual(await at("End"), { r: 0, c: 2 });
  assert.deepStrictEqual(await at("ArrowLeft"), { r: 0, c: 1 });
  assert.deepStrictEqual(await at("PageDown"), { r: 20, c: 1 });
  assert.deepStrictEqual(await at("PageUp"), { r: 0, c: 1 });
  assert.deepStrictEqual(await at("Control+End"), { r: 29, c: 2 });
  assert.deepStrictEqual(await at("Control+ArrowLeft"), { r: 29, c: 0 });
  assert.deepStrictEqual(await at("ArrowRight"), { r: 29, c: 1 });
  assert.deepStrictEqual(await at("Control+ArrowRight"), { r: 29, c: 2 });
  assert.deepStrictEqual(await at("Home"), { r: 29, c: 0 });
  assert.deepStrictEqual(await at("Control+Home"), { r: 0, c: 0 });
  await page().keyboard.press("Control+a");
  assert.deepStrictEqual(await sel(), { a: { r: 0, c: 0 }, f: { r: 29, c: 2 } });
  assert.strictEqual(await grid().locator("td.sel").count(), 90);
  await page().keyboard.press("Shift+F10");
  assert.deepStrictEqual(await calls(), [["menu", "TD"]]);
  assert.deepStrictEqual(app.errors, []);
});

test("a row's number selects the row, a drag selects a block, and a double-click opens the value's own column", { skip }, async () => {
  await draw(RESULT, { names: COLUMNS.map((c) => c.name), order: [2, 0, 1], hidden: [], widths: {} });
  await grid().locator('tbody tr[data-r="3"] td.i').click();
  assert.deepStrictEqual(await sel(), { a: { r: 3, c: 0 }, f: { r: 3, c: 2 } });
  await cell(1, 0).hover();
  await page().mouse.down();
  await cell(2, 1).hover();
  await page().mouse.up();
  assert.deepStrictEqual(await sel(), { a: { r: 1, c: 0 }, f: { r: 2, c: 1 } });
  await cell(4, 0).dblclick();
  assert.deepStrictEqual((await calls()).filter(([n]) => n === "open"), [["open", { r: 4, c: 2 }]], "the first column on screen is doc");
  assert.deepStrictEqual(app.errors, []);
});

test("Escape clears a filter box; no rows matching and filters on hidden columns say so", { skip }, async () => {
  await draw({ ...RESULT, rows: [], total: 0 }, { filterOn: true, filters: { 1: "zz" } });
  await box().getByText("No rows match the filters.").waitFor();
  await box().getByRole("textbox", { name: "Filter name" }).press("Escape");
  assert.strictEqual(await box().getByRole("textbox", { name: "Filter name" }).inputValue(), "");
  assert.deepStrictEqual(await calls(), [["filter", 1, ""]]);
  await draw(RESULT, { names: COLUMNS.map((c) => c.name), hidden: [2], widths: {}, filters: { 2: "x" } });
  assert.strictEqual(await box().locator(".sql-grid-hidden").innerText(), "1 column is hidden. Columns... shows it. A filter on a hidden column still applies.");
  await draw(RESULT, { names: COLUMNS.map((c) => c.name), hidden: [1, 2], widths: {}, filters: { 1: "x", 2: "y" } });
  assert.strictEqual(await box().locator(".sql-grid-hidden").innerText(), "2 columns are hidden. Columns... shows them. Filters on 2 hidden columns still apply.");
  assert.deepStrictEqual(app.errors, []);
});

test("dragging a column's edge sets its width; Enter on it goes back to its own width", { skip }, async () => {
  await draw();
  const handle = box().getByRole("separator", { name: "Width of name" });
  const b = await handle.boundingBox();
  const before = await page().evaluate(() => document.querySelector('#t-box th[data-col="1"]').getBoundingClientRect().width);
  await page().mouse.move(b.x + b.width / 2, b.y + b.height / 2);
  await page().mouse.down();
  await page().mouse.move(b.x + b.width / 2 + 60, b.y + b.height / 2);
  await page().mouse.up();
  const w = await page().evaluate(() => window.__g.widths[1]);
  assert.ok(Math.abs(w - (before + 60)) <= 1, `${w} vs ${before + 60}`);
  assert.ok(await grid().evaluate((t) => t.classList.contains("sized")));
  await handle.focus();
  await page().keyboard.press("Enter");
  assert.deepStrictEqual(await page().evaluate(() => window.__g.widths), {});
  assert.ok(!(await grid().evaluate((t) => t.classList.contains("sized"))));
  assert.deepStrictEqual(app.errors, []);
});

test("Copy with nothing selected takes the whole page, and its note says how many rows the result has", { skip }, async () => {
  const out = await page().evaluate((r) => [sqlGridCopyText("cells", { ...r, rows: r.rows.slice(0, 2) }, null),
    sqlCopiedPageNote({ ...r, rows: r.rows.slice(0, 2), total: 500 })], RESULT);
  assert.deepStrictEqual(out, ["1\tn1\t{\"a\": [1, 2]}\n2\tn2\t{not json",
    "Copied the 2 rows on this page. The result has 500 rows. Use Export to save them all."]);
  assert.deepStrictEqual(app.errors, []);
});

test("Columns... moves a column down, and closes when the grid now holds other columns", { skip }, async () => {
  await draw();
  await page().evaluate((cols) => { sqlColumnsDialog(cols, () => window.__g, () => window.__redraw()); }, COLUMNS);
  const d = page().getByRole("dialog", { name: "Columns" });
  await d.getByRole("button", { name: "Move id down" }).click();
  assert.deepStrictEqual(await box().locator("thead th[data-col] .sql-sort").allInnerTexts(), ["name", "id", "doc"]);
  assert.ok(await d.getByRole("button", { name: "Move id down" }).evaluate((b) => b === document.activeElement));
  await page().evaluate(() => { window.__g.names = ["other", "names", "now"]; });
  await d.getByRole("button", { name: "Move id down" }).click();
  await d.waitFor({ state: "detached" });
  assert.deepStrictEqual(app.errors, []);
});

test("a JSON object shows indented, text that only looks like JSON as it is; failed and top-value statistics", { skip }, async () => {
  const out = await page().evaluate((cols) => {
    const b = document.createElement("div"); b.id = "t-box"; document.body.append(b);
    const r = { columns: cols, rows: [[1, "x", { a: 1 }], [2, "y", "{not json"]], offset: 0 };
    const panel = (at) => sqlValuePanel(r, at, { copy: () => {}, close: () => {} }).querySelector("pre").textContent;
    const stats = (s) => { const p = sqlStatsPanel(cols[1], s, { close: () => {} }); b.replaceChildren(p); return p.innerText; };
    return [panel({ r: 0, c: 2 }), panel({ r: 1, c: 2 }), stats({ error: "The result snapshot is gone." }),
      stats({ rows: 3, missing: 1, distinct: 2, top: [["x", 2], [null, 1], ["a".repeat(30), 1]] }),
      [...b.querySelectorAll(".top-values li span")].map((s) => [s.textContent, s.title])];
  }, COLUMNS);
  assert.strictEqual(out[0], "{\n  \"a\": 1\n}");
  assert.strictEqual(out[1], "{not json");
  assert.match(out[2], /The result snapshot is gone\./);
  assert.match(out[3], /Rows\s+3\s+Empty \(null\)\s+1\s+Distinct\s+≈ 2/);
  assert.deepStrictEqual(out[4], [["x", "x"], ["null", "null"], [`${"a".repeat(23)}…`, "a".repeat(30)]]);
  assert.deepStrictEqual(app.errors, []);
});
