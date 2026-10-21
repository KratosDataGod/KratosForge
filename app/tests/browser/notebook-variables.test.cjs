// The Variables pane (nbvars.js), against the real engine: filter, sort by memory, and open a data
// frame or a dict without running code. Secrets stay hidden.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { playwright, openApp } = require("./harness.cjs");

const SHOTS = process.env.SCREENSHOT_DIR;
const skip = !playwright && "Playwright is not installed";

const SETUP = [
  "import polars as pl",
  "sales = pl.DataFrame({'order_id': list(range(5000)), 'region': ['west'] * 5000})",
  "config = {'region': 'west', 'api_token': 'abc123', 'retries': 3}",
  "names = ['ann', 'bo']",
  "count = 7",
].join("\n");

let app;
const page = () => app.page;
const pane = () => page().locator(".varpane");
const names = () => pane().locator("table.vars:not(.inner) > tbody > tr.click td.name").allInnerTexts();

before(async () => {
  if (skip) return;
  app = await openApp({ engine: ["start_cell", "poll_cell", "kernel_variables", "kernel_variable_detail", "kernel_complete", "interrupt_cell"] });
  await page().evaluate((source) => {
    loadIntoState({ name: "Vars", cells: [{ type: "code", cell_id: "c1", source, meta: {} }] });
    state.showVariables = true;
    go("notebook");
  }, SETUP);
  await page().evaluate(() => runCell(state.notebook.cells[0]));
  await pane().locator("td.name", { hasText: "sales" }).waitFor();
});

after(async () => { if (app) await app.close(); });

test("variables are listed by name with a total, and the filter matches names and types", { skip }, async () => {
  assert.deepStrictEqual((await names()).map((n) => n.trim()), ["config", "count", "names", "sales"]);
  await pane().locator(".vars-foot").getByText(/^4 variables, about .* in memory$/).waitFor();
  await pane().getByLabel("Filter variables").fill("dict");
  assert.deepStrictEqual((await names()).map((n) => n.trim()), ["config"]);
  await pane().locator(".vars-foot").getByText(/^1 of 4 variables/).waitFor();
  await pane().getByLabel("Filter variables").fill("zzz");
  await pane().getByText("No variable matches the filter.").waitFor();
  await pane().getByLabel("Filter variables").fill("");
});

test("sorting by size puts the biggest first; again reverses it", { skip }, async () => {
  await pane().getByRole("button", { name: /^Size/ }).click();
  assert.strictEqual((await names())[0].trim(), "sales");
  await pane().getByRole("button", { name: /^Size/ }).click();
  assert.strictEqual((await names()).at(-1).trim(), "sales");
  await pane().getByRole("button", { name: /^Name/ }).click();
  assert.strictEqual((await names())[0].trim(), "config");
});

test("opening a data frame shows its columns and types; a dict shows its items with secrets hidden", { skip }, async () => {
  await pane().getByRole("button", { name: "Open sales" }).click();
  const detail = pane().locator("tr.vars-detail");
  await detail.getByText("2 columns").waitFor();
  assert.deepStrictEqual(await detail.locator("td.val").allInnerTexts(), ["Int64", "String"]);
  await pane().getByRole("button", { name: "Open config" }).click();
  const cfg = pane().locator("tr.vars-detail").first();  // config sorts first
  assert.deepStrictEqual(await cfg.locator("td.val").allInnerTexts(), ["'west'", "hidden", "3"]);
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "notebook-variables.png") });
  // Opening a variable doesn't add a cell; clicking its row still does.
  assert.strictEqual(await page().evaluate(() => state.notebook.cells.length), 1);
  await pane().getByRole("button", { name: "Close sales" }).click();
  assert.strictEqual(await pane().locator("tr.vars-detail").count(), 1);
  assert.deepStrictEqual(app.errors, []);
});
