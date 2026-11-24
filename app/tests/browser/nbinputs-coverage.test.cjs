// The Parameters and Widgets bar (nbinputs.js) beyond notebook-inputs.test.cjs, against the real
// engine: a notebook with widgets and no parameter cell, a combobox widget that offers its choices and
// keeps a value typed in, and hiding and showing the bar.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
const CELLS = [
  { type: "code", cell_id: "w1", source: 'dbutils.widgets.combobox("table", "orders", ["orders", "customers"], "Table")', meta: {} },
  { type: "code", cell_id: "u1", source: 'print(dbutils.widgets.get("table"))', meta: {} },
];

let app;
const page = () => app.page;
const bar = () => page().locator(".nb-inputs");

before(async () => {
  if (skip) return;
  app = await openApp({ engine: ["save_notebook", "notebook_inputs", "notebook_inputs_set"] });
});
after(async () => { await app?.close(); });

beforeEach(async () => {
  if (skip) return;
  await page().evaluate(async (cells) => {
    await rpc("save_notebook", { name: "Widgets only", cells });
    await rpc("notebook_inputs_set", { name: "Widgets only", cells, values: { parameters: {}, widgets: {} } });
    state.nbInputsCollapsed = false;
    loadIntoState({ name: "Widgets only", cells });
    go("notebook");
  }, CELLS);
  await bar().waitFor();
});

test("widgets with no parameter cell are headed Widgets and say dbutils.widgets made them", { skip }, async () => {
  assert.strictEqual(await bar().locator(".nb-inputs-head b").first().evaluate((b) => b.firstChild.textContent), "Widgets");
  assert.match(await bar().innerText(), /Created by dbutils\.widgets\. Runs on this PC, in Fabric and side by side use these values/);
});

test("a combobox offers its choices and keeps a value typed in", { skip }, async () => {
  const box = bar().getByLabel(/^Table/);
  assert.strictEqual(await box.inputValue(), "orders");
  assert.deepStrictEqual(await bar().locator("datalist#nbin-widgets-table-list option").evaluateAll((os) => os.map((o) => o.value)),
    ["orders", "customers"]);
  assert.strictEqual(await box.getAttribute("list"), "nbin-widgets-table-list");
  await box.fill("returns");
  await box.press("Enter");
  await bar().getByText("Set here: table = returns. Run the notebook to use them.").waitFor();
  assert.deepStrictEqual(app.calls.filter((c) => c.method === "notebook_inputs_set").at(-1).params.values,
    { parameters: {}, widgets: { table: "returns" } });
});

test("Hide parameters folds the bar to its head, and Show parameters opens it again", { skip }, async () => {
  await bar().getByRole("button", { name: "Hide parameters" }).click();
  await bar().getByRole("button", { name: "Show parameters" }).waitFor();
  assert.strictEqual(await bar().getByRole("button", { name: "Show parameters" }).getAttribute("aria-expanded"), "false");
  assert.strictEqual(await bar().locator(".nb-inputs-fields").count(), 0);
  assert.strictEqual(await bar().locator(".nb-inputs-head b").first().evaluate((b) => b.firstChild.textContent), "Widgets", "the head stays");
  await bar().getByRole("button", { name: "Show parameters" }).click();
  await bar().getByLabel(/^Table/).waitFor();
  assert.strictEqual(await page().evaluate(() => state.nbInputsCollapsed), false);
});
