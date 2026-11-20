// Notebook result viewer (frames.py): a cell's table pages, searches and sorts the whole result in
// the real engine, and Download writes the whole result to a new file. No lakehouse is attached,
// so nothing reaches OneLake.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const { playwright, openApp } = require("./harness.cjs");

const SHOTS = process.env.SCREENSHOT_DIR;
const skip = !playwright && "Playwright is not installed";

const CELLS = [{ type: "code", cell_id: "c1", meta: {},
  source: "import polars as pl\npl.DataFrame({'n': range(2500), 'name': [f'row {i}' for i in range(2500)]})" }];

let app;
const page = () => app.page;

before(async () => {
  if (skip) return;
  app = await openApp({
    viewport: { width: 1440, height: 900 },
    engine: ["start_cell", "poll_cell", "interrupt_cell", "kernel_variables", "notebook_frame_page", "notebook_frame_download", "notebook_frame_wrangle"],
    mocks: { save_notebook: () => ({ saved: true }), notebook_dependencies: () => ({ cells: [] }) },
  });
  await page().evaluate((cells) => { loadIntoState({ name: "Viewer test", cells }); go("notebook"); }, CELLS);
  await page().evaluate(() => runCell(state.notebook.cells[0]));
  await page().locator(".result-range").waitFor({ timeout: 60000 });
});

after(async () => { if (app) await app.close(); });

test("the table pages through all 2,500 rows and sorts and searches every row", { skip }, async () => {
  const out = page().locator(".table-output");
  const range = out.locator(".result-range");
  assert.strictEqual(await range.innerText(), "Rows 1 to 100 of 2,500");
  assert.match(await out.locator("thead").innerText(), /n\s*integer[\s\S]*name\s*string/);
  await out.getByRole("button", { name: "Next" }).click();
  await out.getByText("Rows 101 to 200 of 2,500").waitFor();
  await out.locator("th .th-sort").first().click();
  await out.locator("th .th-sort").first().click();
  // Wait for the descending page itself: the range line reads the same after the first sort.
  await out.locator("tbody tr").first().getByText("row 2499", { exact: true }).waitFor();
  assert.strictEqual(await range.innerText(), "Rows 1 to 100 of 2,500");
  assert.match(await out.locator("tbody tr").first().innerText(), /^2499\s+row 2499$/);
  await out.getByRole("searchbox", { name: "Search all rows" }).fill("row 1999");
  await out.getByText("Rows 1 to 1 of 1 matching").waitFor();
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "result-viewer.png") });
});

test("Filter shows a box under each header that filters every row, with the search, and says what to write", { skip }, async () => {
  const out = page().locator(".table-output");
  const range = out.locator(".result-range");
  await out.getByRole("searchbox", { name: "Search all rows" }).fill("");
  await out.getByText("Rows 1 to 100 of 2,500").waitFor();
  await out.getByRole("button", { name: "Filter", exact: true }).click();
  assert.strictEqual(await out.getByRole("button", { name: "Filter", exact: true }).getAttribute("aria-pressed"), "true");
  const n = out.getByRole("textbox", { name: "Filter n", exact: true });
  assert.ok(await n.evaluate((el) => el === document.activeElement), "the first box takes focus");
  await n.fill("< 100");
  await n.press("Enter");
  await out.getByText("Rows 1 to 100 of 100 matching").waitFor();
  await out.getByRole("textbox", { name: "Filter name" }).fill("9");  // applies as you stop typing
  await out.getByText("Rows 1 to 19 of 19 matching").waitFor();
  assert.ok(await out.getByRole("textbox", { name: "Filter name" }).evaluate((el) => el === document.activeElement), "focus stays in the box");
  await out.getByRole("searchbox", { name: "Search all rows" }).fill("row 9");
  await out.getByText("Rows 1 to 11 of 11 matching").waitFor();  // 9, 90 to 99
  await n.fill("> lots");
  await n.press("Enter");
  await out.locator(".sql-grid-error").getByText(/Write a number after > in the filter on n/).waitFor();
  assert.strictEqual(await n.getAttribute("aria-invalid"), "true");
  assert.strictEqual(await range.innerText(), "Rows 1 to 11 of 11 matching", "the rows on screen stay");
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "result-viewer-filter.png") });
  await out.getByRole("button", { name: "Filter", exact: true }).click();  // off: the boxes and their filters go
  await out.getByRole("searchbox", { name: "Search all rows" }).fill("");
  await out.getByText("Rows 1 to 100 of 2,500").waitFor();
  assert.strictEqual(await out.locator(".sql-filter").count(), 0);
  assert.ok(await out.locator(".sql-grid-error").isHidden());
  const last = app.calls.filter((c) => c.method === "notebook_frame_page").at(-1).params;
  assert.strictEqual(last.filters, undefined);
});

test("the table is the SQL page's grid: select and copy cells, open a value, resize and hide columns, and Column statistics opens Inspect", { skip }, async () => {
  const out = page().locator(".table-output");
  await page().evaluate(() => { window.__copied = []; Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText: async (t) => { window.__copied.push(t); } } }); });
  const cell = (r, c) => out.locator(`tbody tr[data-r="${r}"] td[data-c="${c}"]`);
  await cell(0, 0).click();
  await cell(1, 1).click({ modifiers: ["Shift"] });
  await out.getByText(/Rows 1 to 100 of 2,500 · 2 × 2 cells selected/).waitFor();
  await page().keyboard.press("Control+c");
  await page().waitForFunction(() => window.__copied.length === 1);
  assert.strictEqual(await page().evaluate(() => window.__copied[0]), "2499\trow 2499\n2498\trow 2498", "the table is still sorted by n, descending");
  await cell(1, 1).click({ button: "right" });
  const items = await page().locator("#menu [role=menuitem]").evaluateAll((els) => els.map((e) => e.firstChild.textContent));
  assert.deepStrictEqual(items, ["Copy", "Copy with headers", "Copy as CSV", "Copy as JSON", "Copy as INSERT statements", "Open value", "Column statistics"]);
  await page().getByRole("menuitem", { name: "Copy as CSV" }).click();
  await page().waitForFunction(() => window.__copied.length === 2);
  assert.strictEqual(await page().evaluate(() => window.__copied[1]), "n,name\r\n2499,row 2499\r\n2498,row 2498");
  await page().keyboard.press("ArrowDown");
  await page().keyboard.press("Enter");
  const side = out.locator(".sql-side");
  await side.waitFor();
  assert.strictEqual(await side.locator("h3").textContent(), "name, row 3");
  assert.strictEqual(await side.locator("pre").textContent(), "row 2497");
  await page().keyboard.press("Escape");
  await side.waitFor({ state: "detached" });
  // Columns... hides a column; a header's edge resizes one.
  const heads = async () => (await out.locator("thead th .th-sort").allTextContents()).map((t) => t.replace(/ [▲▼]$/, ""));
  await out.getByRole("button", { name: "Columns...", exact: true }).click();
  const dialog = page().getByRole("dialog", { name: "Columns" });
  await dialog.getByRole("checkbox", { name: /^n\b/ }).uncheck();
  await dialog.getByRole("button", { name: "Done" }).click();
  assert.deepStrictEqual(await heads(), ["name"]);
  await out.getByText("1 column is hidden. Columns... shows it.").waitFor();
  const th = out.locator('th[data-col="1"]');
  const w0 = (await th.boundingBox()).width;
  await th.locator(".sql-col-resize").focus();
  for (let k = 0; k < 2; k++) await page().keyboard.press("ArrowLeft");
  await page().waitForFunction((w) => Math.abs(document.querySelector('.table-output th[data-col="1"]').getBoundingClientRect().width - w) <= 2, w0 - 32);
  await out.getByRole("button", { name: "Columns...", exact: true }).click();
  await dialog.getByRole("button", { name: "Show all" }).click();
  await dialog.getByRole("button", { name: "Done" }).click();
  assert.deepStrictEqual(await heads(), ["n", "name"]);
  // Column statistics: the notebook's Inspect, which profiles every column over every row.
  await cell(0, 0).click({ button: "right" });
  await page().getByRole("menuitem", { name: "Column statistics" }).click();
  await out.getByRole("button", { name: "Inspect" }).and(page().locator("[aria-pressed=true]")).waitFor();
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "result-viewer-inspect.png") });
  await out.getByRole("button", { name: "Table", exact: true }).click();
  await out.locator(".result-range").waitFor();
  assert.deepStrictEqual(app.errors, []);
});

test("Download saves the whole result as a new file each time", { skip }, async () => {
  const out = page().locator(".table-output");
  const paths = [];
  for (let i = 0; i < 2; i++) {
    await out.locator(".result-download summary").click();
    await out.getByRole("menuitem", { name: "CSV", exact: true }).click();
    let toast = page().getByText(/^Saved 2,500 rows to /);
    if (paths.length) toast = toast.filter({ hasNotText: paths[0] });
    await toast.last().waitFor();
    paths.push((await toast.last().innerText()).replace(/^Saved 2,500 rows to /, ""));
  }
  // The name carries the time to the second, so the second file is either a later second or "(2)".
  assert.notStrictEqual(paths[0], paths[1]);
  assert.match(paths[0], /Viewer test result .*\.csv$/);
  assert.ok(paths[1].endsWith(" (2).csv") || paths[1] > paths[0], paths[1]);
  assert.ok(fs.existsSync(paths[0]) && fs.existsSync(paths[1]), "both files are there");
  assert.deepStrictEqual(app.errors, []);
});

test("Clean filters every row in the engine and adds the Polars code as a new cell", { skip }, async () => {
  const out = page().locator(".table-output").first();
  await out.getByRole("button", { name: "Clean" }).click();
  await out.getByText("2,500 rows before, 2,500 rows after.").waitFor({ timeout: 60000 });
  await out.getByRole("combobox", { name: "Add a step" }).selectOption("filter");
  const form = out.locator(".wrangle-add");
  await form.getByRole("combobox", { name: "Column" }).selectOption("n");
  await form.getByRole("combobox", { name: "Keep rows where it" }).selectOption("ge");
  await form.getByRole("textbox", { name: "Value" }).fill("2490");
  await form.getByRole("button", { name: "Add step" }).click();
  await out.getByText("2,500 rows before, 10 rows after.").waitFor();
  assert.match(await out.locator(".wrangle-code").innerText(), /df = df\.filter\(pl\.col\('n'\) >= 2490\)/);
  await out.getByRole("button", { name: "Add code to notebook" }).click();
  const cells = await page().evaluate(() => state.notebook.cells.map((c) => c.source));
  assert.strictEqual(cells.length, 2);
  assert.match(cells[1], /^import polars as pl[\s\S]*df_clean = clean_data\(df\)\ndisplay\(df_clean\)\n$/);
  assert.deepStrictEqual(app.errors, []);
});

test("resizing a column in one cell's table leaves another cell's table as it was", { skip }, async () => {
  const first = page().locator(".table-output").first();
  await first.getByRole("button", { name: "Table", exact: true }).click();
  await first.locator(".result-range").waitFor();
  await page().evaluate(() => {
    state.selected = state.notebook.cells[0].id;
    insertCell("import polars as pl\npl.DataFrame({'n': [1, 2], 'name': ['a', 'b']})");
    return runCell(state.notebook.cells[1]);
  });
  const second = page().locator(".table-output").nth(1);
  await second.locator(".result-range").waitFor({ timeout: 60000 });
  const width = (out) => out.locator('th[data-col="1"]').evaluate((el) => Math.round(el.getBoundingClientRect().width));
  const before = await width(second);
  const w0 = await width(first);
  await first.locator('th[data-col="1"] .sql-col-resize').focus();
  for (let k = 0; k < 3; k++) await page().keyboard.press("ArrowRight");
  await page().waitForFunction((w) => Math.abs(document.querySelectorAll('.table-output th[data-col="1"]')[0].getBoundingClientRect().width - w) <= 2, w0 + 48);
  assert.strictEqual(await width(second), before, "the other table keeps its width");
  assert.deepStrictEqual(app.errors, []);
});
