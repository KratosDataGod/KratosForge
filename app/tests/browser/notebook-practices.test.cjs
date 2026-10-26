// The notebook's Best practices panel (nbpractices.js) against the real engine: findings for the
// code cells with their notebook cell numbers, Go to cell puts the cursor on the line, a fix clears
// the finding as you type, and nothing is saved or run.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { playwright, openApp } = require("./harness.cjs");

const SHOTS = process.env.SCREENSHOT_DIR;
const skip = !playwright && "Playwright is not installed";

const CELLS = [
  { type: "markdown", cell_id: "m1", source: "# Load orders", meta: {} },
  { type: "code", cell_id: "c1", source: "%pip install semantic-link-labs", meta: {} },
  { type: "code", cell_id: "c2", source: 'orders = spark.read.option("header", True).csv("Files/orders/*.csv")\nrows = orders.collect()', meta: {} },
];

let app;
const page = () => app.page;
const pane = () => page().locator(".practicespane");

before(async () => {
  if (skip) return;
  app = await openApp({ engine: ["notebook_practices", "governance_practice_settings"] });
  await page().evaluate((cells) => {
    loadIntoState({ name: "Load orders", cells });
    state.ribbonTab = "view";
    go("notebook");
  }, CELLS);
});

after(async () => { if (app) await app.close(); });

test("the panel lists the findings with the notebook's cell numbers", { skip }, async () => {
  await page().locator("button[data-practices]").click();
  await pane().locator("[data-rule]").nth(2).waitFor();
  const rules = await pane().locator("[data-rule]").evaluateAll((els) => els.map((e) => e.dataset.rule));
  assert.deepStrictEqual(rules.sort(), ["NB003", "NB005", "NB009"]);
  const goTo = await pane().locator("[data-rule=NB003] .link-btn").innerText();
  assert.strictEqual(goTo, "Go to cell 3, line 2", "the markdown cell counts in the cell number");
  assert.match(await pane().innerText(), /3 warnings/);
  assert.match(await page().locator("button[data-practices]").innerText(), /Best practices · 3/);
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "notebook-practices.png") });
});

test("Go to cell selects the cell and puts the cursor on the line", { skip }, async () => {
  await pane().locator("[data-rule=NB003] .link-btn").click();
  const at = await page().evaluate(() => {
    const cell = state.notebook.cells[2];
    const view = editors.get(cell.id);
    return { selected: state.selected === cell.id, line: view.state.doc.lineAt(view.state.selection.main.head).number };
  });
  assert.deepStrictEqual(at, { selected: true, line: 2 });
});

test("fixing the code clears its finding as you type, without saving", { skip }, async () => {
  await page().evaluate(() => {
    const cell = state.notebook.cells[1];
    const view = editors.get(cell.id);
    view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: "%pip install semantic-link-labs==0.17.1" } });
  });
  await pane().locator("[data-rule=NB009]").waitFor({ state: "detached", timeout: 10000 });
  assert.strictEqual(await pane().locator("[data-rule]").count(), 2);
});

test("closing the panel stops it", { skip }, async () => {
  await pane().getByRole("button", { name: "Close best practices" }).click();
  assert.strictEqual(await pane().count(), 0);
  assert.match(await page().locator("button[data-practices]").innerText(), /^Best practices\n?Preview$/, "no count once closed");
});
