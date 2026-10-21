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
    engine: ["start_cell", "poll_cell", "interrupt_cell", "kernel_variables", "notebook_frame_page", "notebook_frame_download"],
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

test("Download saves the whole result as a new file each time", { skip }, async () => {
  const out = page().locator(".table-output");
  const paths = [];
  for (let i = 0; i < 2; i++) {
    await out.locator(".result-download summary").click();
    await out.getByRole("menuitem", { name: "CSV" }).click();
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
