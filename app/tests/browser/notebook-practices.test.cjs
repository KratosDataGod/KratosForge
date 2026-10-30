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
  app = await openApp({ engine: ["notebook_practices", "governance_practice_settings", "learn_rule_guides", "learn_dismissals",
    "learn_dismiss", "learn_undismiss"] });
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

test("About NB003 opens the rule's section of the docs (LX-01)", { skip }, async () => {
  await pane().locator("[data-rule=NB003] .learn-more").click();
  const opened = await page().evaluate(() => window.__fdShellCalls.filter((c) => c.cmd === "open_doc").map((c) => c.args.page));
  assert.deepStrictEqual(opened, ["notebook-best-practices.md#nb003"]);
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

test("Dismiss Not in this file hides a finding in this notebook only, and Show all again brings it back (LX-09)", { skip }, async () => {
  await pane().locator("[data-rule=NB003]").getByRole("button", { name: /Dismiss NB003/ }).click();
  await pane().locator("[data-rule=NB003] .learn-dismiss-item", { hasText: "Not in this file" }).click();
  await pane().locator("[data-rule=NB003]").waitFor({ state: "detached" });
  assert.match(await page().locator("button[data-practices]").innerText(), /Best practices · 1/);
  assert.deepStrictEqual(await page().evaluate(() => learnDismissed.map((d) => [d.rule_id, d.scope, d.where])), [["NB003", "file", "notebooks/Load orders"]]);
  await pane().locator(".learn-dismissed button", { hasText: "1 dismissed · Show" }).click();
  await pane().locator(".learn-dismissed button", { hasText: "Show all again" }).click();
  await pane().locator("[data-rule=NB003]").waitFor();
});

test("closing the panel stops it", { skip }, async () => {
  await pane().getByRole("button", { name: "Close best practices" }).click();
  assert.strictEqual(await pane().count(), 0);
  assert.match(await page().locator("button[data-practices]").innerText(), /^Best practices\n?Preview$/, "no count once closed");
});
