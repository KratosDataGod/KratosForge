// Several charts per result (charts.js): + Chart adds a second chart of the same result, charted
// over every row in the real engine, with its own settings saved in the cell. No lakehouse is
// attached, so nothing reaches OneLake.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { playwright, openApp } = require("./harness.cjs");

const SHOTS = process.env.SCREENSHOT_DIR;
const skip = !playwright && "Playwright is not installed";

const CELLS = [{ type: "code", cell_id: "c1", meta: {},
  source: "import polars as pl\npl.DataFrame({'region': ['East', 'West'] * 1500, 'month': ['Jan', 'Feb', 'Mar'] * 1000, 'amount': range(3000)})" }];

let app;
const page = () => app.page;

before(async () => {
  if (skip) return;
  app = await openApp({
    viewport: { width: 1440, height: 900 },
    engine: ["start_cell", "poll_cell", "interrupt_cell", "kernel_variables", "notebook_frame_page", "notebook_frame_chart"],
    mocks: { save_notebook: () => ({ saved: true }), notebook_dependencies: () => ({ cells: [] }) },
  });
  await page().evaluate((cells) => { loadIntoState({ name: "Charts test", cells }); go("notebook"); }, CELLS);
  await page().evaluate(() => runCell(state.notebook.cells[0]));
  await page().locator(".result-range").waitFor({ timeout: 60000 });
});

after(async () => { if (app) await app.close(); });

test("+ Chart adds a second chart with its own settings, and Remove chart takes it away", { skip }, async () => {
  const out = page().locator(".table-output");
  const views = out.getByRole("group", { name: "View" });
  await views.getByRole("button", { name: "Add a chart of this result" }).click();
  const second = views.getByRole("button", { name: "Chart 2", exact: true });
  assert.strictEqual(await second.getAttribute("aria-pressed"), "true");
  await out.locator(".chart-area svg").waitFor({ timeout: 60000 });
  await out.getByRole("combobox", { name: "X axis" }).selectOption("month");
  await out.locator(".chart-area svg").waitFor();
  assert.match(await out.locator(".chart-area").innerText(), /Mar/);
  await out.getByRole("textbox", { name: "Chart title" }).fill("By month");
  await out.getByRole("textbox", { name: "Chart title" }).press("Enter");
  await views.getByRole("button", { name: "By month", exact: true }).waitFor();
  const meta = await page().evaluate(() => JSON.parse(JSON.stringify(state.notebook.cells[0].meta)));
  assert.strictEqual(meta.chart.x, "region", "the first chart keeps its own X axis");
  assert.deepStrictEqual([meta.charts.length, meta.charts[0].x, meta.charts[0].title], [1, "month", "By month"]);
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "several-charts.png") });
  // The first chart still shows its own settings.
  await views.getByRole("button", { name: "Chart", exact: true }).click();
  assert.strictEqual(await out.getByRole("combobox", { name: "X axis" }).inputValue(), "region");
  assert.strictEqual(await out.getByRole("button", { name: "Remove chart" }).count(), 0);
  await views.getByRole("button", { name: "By month", exact: true }).click();
  await out.getByRole("button", { name: "Remove chart" }).click();
  assert.strictEqual(await views.getByRole("button", { name: "Chart", exact: true }).getAttribute("aria-pressed"), "true");
  assert.strictEqual(await views.getByRole("button", { name: "By month", exact: true }).count(), 0);
  assert.strictEqual(await page().evaluate(() => state.notebook.cells[0].meta.charts), undefined);
  assert.deepStrictEqual(app.errors, []);
});
