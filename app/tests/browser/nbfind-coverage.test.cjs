// Notebook find and replace beyond notebook-find.test.cjs: the Show replace button and Ctrl+H in the
// bar, the Previous and Next buttons, a match in hidden code, in a Markdown cell or in a table output
// whose outputs are folded, Replace on a frozen cell, Replace all where every match is frozen or in an
// output, a Markdown cell changed by Replace all, new outputs and typing moving the matches, another
// notebook opened under the bar, and the 5,000-match cap. No engine calls.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
const CELLS = [
  { type: "markdown", cell_id: "m1", source: "# Orders\nNotes on the orders load.", meta: {} },
  { type: "code", cell_id: "c1", source: "orders = lakehouse.read_table(\"dbo.orders\")", meta: { source_hidden: true } },
  { type: "code", cell_id: "c2", source: "show(top)", meta: { outputs_hidden: true } },
  { type: "code", cell_id: "c3", source: "print(orders_total)", meta: { frozen: true } },
];
const TABLE = { type: "table", columns: ["id", "name"], rows: [[1, "orders_total"], [2, null]] };

let app;
const page = () => app.page;
const bar = () => page().locator(".nb-find");
const count = () => bar().locator(".nb-find-count");
const counted = (text) => page().waitForFunction((t) => document.querySelector(".nb-find-count")?.textContent === t, text);
const toastSays = (text) => page().waitForFunction((t) => document.getElementById("toast").textContent === t, text);
const sources = () => page().evaluate(() => state.notebook.cells.map((c) => c.source));

before(async () => {
  if (skip) return;
  app = await openApp({ mocks: { readiness_check: () => [], save_notebook: () => ({ saved: true }) } });
  await page().evaluate((cells) => {
    loadIntoState({ name: "Orders", cells });
    state.notebook.cells[2].outputs = [{ type: "image", data: "" }, null];
    go("notebook");
  }, CELLS);
  await page().locator(".cells").waitFor();
  app.errors.length = 0;
});
after(async () => { if (app) await app.close(); });

test("Show replace and Ctrl+H in the bar fold the replace row in and out", { skip }, async () => {
  await page().locator(".cells").click({ position: { x: 5, y: 5 } });
  await page().keyboard.press("Control+Shift+f");
  await bar().waitFor();
  const expand = bar().getByRole("button", { name: "Show replace" });
  assert.strictEqual(await expand.getAttribute("aria-expanded"), "false");
  await expand.click();
  assert.strictEqual(await expand.getAttribute("aria-expanded"), "true");
  assert.ok(await bar().getByLabel("Replace with").evaluate((el) => el === document.activeElement), "the replace box gets the focus");
  await bar().getByLabel("Find in notebook").press("Control+h");
  assert.strictEqual(await expand.getAttribute("aria-expanded"), "false");
  await bar().getByLabel("Find in notebook").press("Control+h");
  assert.strictEqual(await expand.getAttribute("aria-expanded"), "true");
});

test("Next and Previous step to hidden code, which opens, and to a Markdown cell, which opens for editing", { skip }, async () => {
  await bar().getByLabel("Find in notebook").fill("orders");
  await counted("1 of 5");
  assert.ok(await page().evaluate(() => state.notebook.cells[0].editing), "the first match is in the Markdown cell, opened to edit");
  await bar().getByRole("button", { name: "Next match" }).click();
  await counted("2 of 5");  // "Orders" and "orders" in the Markdown cell, then twice in the hidden code
  assert.strictEqual(await page().evaluate(() => !!state.notebook.cells[1].meta.source_hidden), true);
  await bar().getByRole("button", { name: "Next match" }).click();
  await counted("3 of 5");
  assert.deepStrictEqual(await page().evaluate(() => [state.selected === state.notebook.cells[1].id, !!state.notebook.cells[1].meta.source_hidden]),
    [true, false], "the hidden code shows");
  await bar().getByRole("button", { name: "Previous match" }).click();
  await counted("2 of 5");
  await bar().getByRole("button", { name: "Previous match" }).click();
  await counted("1 of 5");
  await bar().getByRole("button", { name: "Previous match" }).click();
  await counted("5 of 5");
});

test("Replace on a frozen cell's match says so and steps on", { skip }, async () => {
  const before = await sources();
  await bar().getByLabel("Replace with").fill("sales");
  await bar().getByRole("button", { name: "Replace", exact: true }).click();  // match 5 is in the frozen cell
  await toastSays("That match is in a frozen cell, so it wasn't changed. Unfreeze the cell to change it.");
  await counted("1 of 5");
  assert.deepStrictEqual(await sources(), before);
});

test("a table output is searched; stepping to it unfolds the outputs; Replace all with every match left alone changes nothing", { skip }, async () => {
  await page().evaluate((table) => {
    const cell = state.notebook.cells[2];
    cell.outputs = [table];
    updateResults(cell);
  }, TABLE);
  await bar().getByLabel("Find in notebook").fill("orders_total");
  await counted("1 of 1");
  await bar().locator("label.nb-find-out input").check();
  await counted("1 of 2");
  assert.strictEqual(await page().evaluate(() => !!state.notebook.cells[2].meta.outputs_hidden), true);
  await bar().getByRole("button", { name: "Next match" }).click();
  await counted("2 of 2");  // the frozen cell's code
  await bar().getByRole("button", { name: "Next match" }).click();
  await counted("1 of 2");  // the table output, before it in the notebook
  assert.strictEqual(await page().evaluate(() => !!state.notebook.cells[2].meta.outputs_hidden), false, "the folded outputs open");
  const before = await sources();
  await bar().getByRole("button", { name: "Replace all" }).click();
  await toastSays("All 2 matches are in frozen cells (1) or outputs (1), which Replace leaves as they are, so nothing was changed.");
  assert.deepStrictEqual(await sources(), before);
  // New outputs: the matches follow them.
  await page().evaluate(() => {
    const cell = state.notebook.cells[2];
    cell.outputs = [{ type: "stream", text: "orders_total = 3\norders_total ok\n" }];
    updateResults(cell);
  });
  await counted("2 of 3");  // the current match stays in the cell, at or after where it was
  await bar().getByLabel("Find in notebook").fill("in frozen only");
  await counted("No results");
  assert.ok(await bar().getByRole("button", { name: "Replace all" }).isDisabled());
  await bar().locator("label.nb-find-out input").uncheck();
});

test("Replace all in a Markdown cell that isn't open redraws it; typing in a cell moves the matches", { skip }, async () => {
  await page().evaluate(() => { const md = state.notebook.cells[0]; md.editing = false; updateCell(md); });
  await bar().getByLabel("Find in notebook").fill("Notes");
  await counted("1 of 1");
  await page().evaluate(() => { const md = state.notebook.cells[0]; md.editing = false; updateCell(md); });
  await bar().getByLabel("Replace with").fill("Checks");
  await bar().getByRole("button", { name: "Replace all" }).click();
  await toastSays("Replaced 1 match in 1 cell.");
  assert.strictEqual((await sources())[0], "# Orders\nChecks on the orders load.");
  await page().waitForFunction(() => {  // the rendered Markdown shows the new text
    const el = document.querySelector(`[data-cell="${state.notebook.cells[0].id}"]`);
    return !state.notebook.cells[0].editing && /Checks on the orders load\./.test(el?.textContent || "");
  });
  // Typing in a cell the last Replace all changed moves the matches, and Undo replace all goes away.
  await bar().getByLabel("Find in notebook").fill("lakehouse");
  await counted("1 of 1");
  await bar().getByLabel("Replace with").fill("lake");
  await bar().getByRole("button", { name: "Replace all" }).click();
  await toastSays("Replaced 1 match in 1 cell.");
  await bar().locator(".nb-find-undo").waitFor();
  await page().evaluate(() => {
    const view = editors.get(state.notebook.cells[1].id);
    view.dispatch({ changes: { from: 0, insert: "lakehouse_name = 'x'\n" } });
  });
  await counted("1 of 1");
  await bar().locator(".nb-find-undo").waitFor({ state: "hidden" });
  assert.strictEqual((await sources())[1], "lakehouse_name = 'x'\norders = lake.read_table(\"dbo.orders\")");
});

test("another notebook opened under the bar is searched instead", { skip }, async () => {
  await bar().getByLabel("Find in notebook").fill("orders");
  await counted("1 of 5");
  await page().evaluate(() => { loadIntoState({ name: "Other", cells: [{ type: "code", cell_id: "o1", source: "orders, orders", meta: {} }] }); go("notebook"); });
  await counted("1 of 2");
});

test("more than 5,000 matches count as 5000+", { skip }, async () => {
  await page().evaluate(() => { loadIntoState({ name: "Big", cells: [{ type: "code", cell_id: "b1", source: "x ".repeat(6000), meta: {} }] }); go("notebook"); });
  await bar().getByLabel("Find in notebook").fill("x");
  await counted("1 of 5000+");
  await bar().getByLabel("Find in notebook").press("Escape");
  await bar().waitFor({ state: "detached" });
  assert.strictEqual(await count().count(), 0);
  assert.deepStrictEqual(app.errors, []);
});
