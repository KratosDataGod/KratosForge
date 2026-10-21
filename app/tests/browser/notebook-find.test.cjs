// Notebook find and replace (nbfind.js): one bar that searches every cell, marks the matches in the
// editors, steps through them, and replaces one or all, leaving frozen cells alone. No engine calls.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { playwright, openApp } = require("./harness.cjs");

const SHOTS = process.env.SCREENSHOT_DIR;
const skip = !playwright && "Playwright is not installed";

const CELLS = [
  { type: "markdown", cell_id: "m1", source: "# Sales by region\nLoad sales, then total sales per region.", meta: {} },
  { type: "code", cell_id: "c1", source: "sales = lakehouse.read_table(\"dbo.sales\")\nsales_count = len(sales)", meta: {} },
  { type: "code", cell_id: "c2", source: "%%sql\nSELECT region, SUM(amount) FROM dbo.sales GROUP BY region", meta: {} },
  { type: "code", cell_id: "c3", source: "print(sales)", meta: { frozen: true } },
];

let app;
const page = () => app.page;
const sources = () => page().evaluate(() => state.notebook.cells.map((c) => c.source));

before(async () => {
  if (skip) return;
  app = await openApp({ mocks: { save_notebook: () => ({ saved: true }) } });
  await page().evaluate((cells) => {
    loadIntoState({ name: "Sales", cells });
    state.ribbonTab = "edit";
    go("notebook");
  }, CELLS);
});

after(async () => { if (app) await app.close(); });

test("Ctrl+H opens the bar; it counts matches in every cell and marks them in the editors", { skip }, async () => {
  await page().locator(".cells").click({ position: { x: 5, y: 5 } });
  await page().keyboard.press("Control+h");
  const bar = page().locator(".nb-find");
  await bar.waitFor();
  await bar.getByLabel("Find in notebook").fill("sales");
  await bar.getByText("1 of 9").waitFor();
  assert.ok(await page().locator(".cm-editor .nb-find-match").count() >= 5);
  await bar.getByLabel("Match whole word").click();
  await bar.getByText("1 of 8").waitFor();  // sales_count no longer matches
  await bar.locator("label.nb-find-md input").uncheck();
  await bar.getByText("1 of 5").waitFor();  // the Markdown cell's "sales" no longer counts
  await bar.getByLabel("Match case").click();
  await bar.getByText("1 of 5").waitFor();
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "notebook-find.png") });
});

test("Enter steps to the next match and selects its cell", { skip }, async () => {
  const bar = page().locator(".nb-find");
  await bar.getByLabel("Find in notebook").press("Enter");
  await bar.getByText("2 of 5").waitFor();
  await bar.getByLabel("Find in notebook").press("Enter");
  await bar.getByText("3 of 5").waitFor();
  assert.strictEqual(await page().evaluate(() => state.selected === state.notebook.cells[1].id), true);
  await bar.getByLabel("Find in notebook").press("Shift+Enter");
  await bar.getByText("2 of 5").waitFor();
});

test("Replace all changes every unfrozen cell, and Undo replace all puts them back", { skip }, async () => {
  const bar = page().locator(".nb-find");
  await bar.getByLabel("Replace with").fill("orders");
  await bar.getByRole("button", { name: "Replace all" }).click();
  const after = await sources();
  assert.strictEqual(after[1], "orders = lakehouse.read_table(\"dbo.orders\")\nsales_count = len(orders)");
  assert.match(after[2], /FROM dbo\.orders GROUP/);
  assert.strictEqual(after[3], "print(sales)");  // frozen
  assert.match(after[0], /Load sales/);           // Markdown was left out of the search
  await page().locator("#toast").getByText(/Replaced 4 matches in 2 cells\. 1 in frozen cells was left/).waitFor();
  await bar.getByText("1 of 1").waitFor();
  await bar.getByRole("button", { name: "Undo replace all" }).click();
  assert.strictEqual((await sources())[1], CELLS[1].source);
  assert.strictEqual((await sources())[2], CELLS[2].source);
});

test("Undo replace all leaves a cell edited since as it is", { skip }, async () => {
  const bar = page().locator(".nb-find");
  await bar.getByRole("button", { name: "Replace all" }).click();  // "sales" -> "orders" again
  assert.match((await sources())[1], /^orders = /);
  await page().evaluate(() => setSource(state.notebook.cells[2], "%%sql\nSELECT 42"));  // an edit after Replace all
  await page().waitForTimeout(300);
  assert.strictEqual(await bar.getByRole("button", { name: "Undo replace all" }).isHidden(), true);
  // The button went away; undoing the snapshot directly still spares the edited cell.
  const out = await page().evaluate(() => {
    nbFind.undo = [{ cell: state.notebook.cells[2], before: "old", after: "%%sql\nSELECT 1" }];
    undoReplaceAll();
    return state.notebook.cells[2].source;
  });
  assert.strictEqual(out, "%%sql\nSELECT 42");
  await page().locator("#toast").getByText(/except in 1 cell changed since/).waitFor();
  await page().evaluate((src) => { setSource(state.notebook.cells[1], src[1]); setSource(state.notebook.cells[2], src[2]); },
    CELLS.map((c) => c.source));
});

test("a regular expression with a lookbehind replaces in context", { skip }, async () => {
  const bar = page().locator(".nb-find");
  await bar.getByLabel("Match whole word").click();
  await bar.getByLabel("Use regular expression").click();
  await bar.getByLabel("Find in notebook").fill("(?<=dbo\\.)sales");
  await bar.getByText("1 of 2").waitFor();
  await bar.getByLabel("Replace with").fill("orders_v2");
  await bar.getByRole("button", { name: "Replace all" }).click();
  assert.match((await sources())[1], /read_table\("dbo\.orders_v2"\)/);
  assert.match((await sources())[1], /len\(sales\)/);
  assert.match((await sources())[2], /FROM dbo\.orders_v2 GROUP/);
  await bar.getByRole("button", { name: "Undo replace all" }).click();
  await bar.getByLabel("Use regular expression").click();
  await bar.getByLabel("Match whole word").click();
});

test("a regular expression replaces with its groups; a bad one says so", { skip }, async () => {
  const bar = page().locator(".nb-find");
  await bar.getByLabel("Match whole word").click();
  await bar.getByLabel("Use regular expression").click();
  await bar.getByLabel("Find in notebook").fill("dbo\\.(\\w+)");
  await bar.getByText("1 of 2").waitFor();
  await bar.getByLabel("Replace with").fill("silver.$1");
  await bar.getByRole("button", { name: "Replace", exact: true }).click();
  assert.match((await sources())[1], /read_table\("silver\.sales"\)/);
  assert.match((await sources())[2], /FROM dbo\.sales/);
  await bar.getByLabel("Find in notebook").fill("(unclosed");
  await bar.getByText("That isn't a valid regular expression.").waitFor();
});

test("the bar stays open while the notebook redraws, and Esc closes it", { skip }, async () => {
  await page().evaluate(() => renderNotebook());
  const bar = page().locator(".nb-find");
  assert.strictEqual(await bar.count(), 1);
  await bar.getByLabel("Find in notebook").press("Escape");  // on the box itself, whatever else takes focus
  await bar.waitFor({ state: "detached" });
  assert.strictEqual(await page().locator(".cm-editor .nb-find-match").count(), 0);
  assert.deepStrictEqual(app.errors, []);
});
