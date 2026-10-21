// Parameters and widgets bar against the real engine: the bar lists the parameter cell's values
// and the Databricks widgets, keeps a changed value with the notebook, and a run on this PC uses
// it while the cell's code stays as it was. No lakehouse is attached, so nothing reaches OneLake.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { playwright, openApp } = require("./harness.cjs");

const SHOTS = process.env.SCREENSHOT_DIR;
const skip = !playwright && "Playwright is not installed";

const CELLS = [
  { type: "code", cell_id: "p1", source: 'run_date = "2026-01-01"\nregion = "West"\nmax_rows = 100000\nfull_load = False', meta: { tags: ["parameters"] } },
  { type: "code", cell_id: "w1", source: 'dbutils.widgets.dropdown("env", "dev", ["dev", "test", "prod"], "Environment")\n'
    + 'dbutils.widgets.multiselect("regions", "East,West", ["East", "West", "North"], "Regions")', meta: {} },
  { type: "code", cell_id: "u1", source: 'print(run_date, max_rows, full_load, dbutils.widgets.get("env"), dbutils.widgets.get("regions"))', meta: {} },
];

let app;
const page = () => app.page;

before(async () => {
  if (skip) return;
  app = await openApp({
    viewport: { width: 1440, height: 900 },
    engine: ["save_notebook", "notebook_inputs", "notebook_inputs_set", "start_cell", "poll_cell", "kernel_variables"],
  });
  await page().evaluate(async (cells) => {
    await rpc("save_notebook", { name: "Daily sales", cells });
    loadIntoState({ name: "Daily sales", cells });
    go("notebook");
  }, CELLS);
});

after(async () => { if (app) await app.close(); });

test("the bar lists parameters and widgets with their values from the code", { skip }, async () => {
  const bar = page().locator(".nb-inputs");
  await bar.getByText("Parameters and widgets").waitFor();
  assert.strictEqual(await bar.getByLabel(/^run_date/).inputValue(), "2026-01-01");
  assert.strictEqual(await bar.getByLabel(/^Environment/).inputValue(), "dev");
  assert.match(await bar.innerText(), /From the parameter cell \(cell 1\)/);
  assert.match(await bar.innerText(), /Preview/);
});

test("a changed value is kept and used by the run, and the code doesn't change", { skip }, async () => {
  const bar = page().locator(".nb-inputs");
  await bar.getByLabel(/^run_date/).fill("2026-10-19");
  await bar.getByLabel(/^run_date/).press("Enter");
  await bar.getByText(/Set here: run_date = 2026-10-19/).waitFor();
  await bar.getByLabel(/^full_load/).selectOption("True");
  await bar.getByLabel(/^Environment/).selectOption("prod");
  await bar.getByRole("checkbox", { name: "East" }).uncheck();
  await bar.getByText(/regions = West/).waitFor();
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "notebook-inputs-bar.png") });
  await page().evaluate(() => runCells(state.notebook.cells));
  await page().locator(".cell").nth(2).getByText("2026-10-19 100000 True prod West").waitFor({ timeout: 60000 });
  assert.strictEqual(await page().evaluate(() => state.notebook.cells[0].source.split("\n")[0]), 'run_date = "2026-01-01"');
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "notebook-inputs-run.png") });
});

test("a bad value says what's wrong, and Reset puts back the code's values", { skip }, async () => {
  const bar = page().locator(".nb-inputs");
  await bar.getByLabel(/^max_rows/).fill("lots");
  await bar.getByLabel(/^max_rows/).press("Enter");
  await bar.getByRole("alert").getByText("Type a whole number.").waitFor();
  await bar.getByRole("button", { name: "Reset to code" }).click();
  await page().waitForFunction(() => !document.querySelector(".nb-inputs [role=alert]"));
  assert.strictEqual(await bar.getByLabel(/^run_date/).inputValue(), "2026-01-01");
  assert.strictEqual(await bar.getByLabel(/^Environment/).inputValue(), "dev");
});
