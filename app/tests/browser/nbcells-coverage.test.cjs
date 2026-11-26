// Organising cells (nbcells.js), the paths notebook-cells leaves out: headings underlined with === or
// ---, and "# lines" inside fenced code that aren't headings; Collapse all sections when the current
// cell would be hidden; Collapse and Expand section from a heading's cell menu; a title that's too
// long, and Remove title. Saving is mocked; what the person sees and what the notebook holds is checked.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
const CELLS = [
  { type: "markdown", cell_id: "m1", source: "Sales report\n============\n\n~~~\n# not a heading\n```\n# still code\n~~~\n\nBy region\n---------", meta: {} },
  { type: "code", cell_id: "c1", source: "a = 1", meta: {} },
  { type: "markdown", cell_id: "m2", source: "```python\n# a comment\n```\n## Detail", meta: {} },
  { type: "code", cell_id: "c2", source: "b = 2", meta: {} },
];

let app;
const page = () => app.page;
const cell = (cid) => page().locator(`[data-cell="${cid}"]`);
const idOf = (cellId) => page().evaluate((cid) => state.notebook.cells.find((c) => c.cell_id === cid).id, cellId);
const meta = (cellId) => page().evaluate((cid) => state.notebook.cells.find((c) => c.cell_id === cid).meta, cellId);

before(async () => {
  if (skip) return;
  app = await openApp({ mocks: { save_notebook: () => ({ saved: true }) } });
  app.errors.length = 0;
});
after(async () => { if (app) await app.close(); });

beforeEach(async () => {
  if (skip) return;
  await page().evaluate((cells) => {
    closeDialog(); closeDialog(); closeMenu?.();
    loadIntoState({ name: "Organise more", cells: structuredClone(cells) });
    state.ribbonTab = "view";
    state.showExplorer = true; state.explorerTab = "outline";
    go("notebook");
  }, CELLS);
  await page().locator(".cell").nth(3).waitFor();
  app.errors.length = 0;
});

test("underlined lines are headings; # lines inside fenced code aren't", { skip }, async () => {
  assert.deepStrictEqual(await page().evaluate((cells) => cells.map((c) => markdownHeadings(c.source)), [CELLS[0], CELLS[2]]),
    [[{ level: 1, text: "Sales report" }, { level: 2, text: "By region" }], [{ level: 2, text: "Detail" }]]);
  const outline = page().locator(".nb-outline");
  await outline.getByRole("button", { name: "Sales report" }).waitFor();
  assert.strictEqual(await outline.getByText(/not a heading|still code|a comment/).count(), 0);
  assert.deepStrictEqual(app.errors, []);
});

test("Collapse all sections moves the current cell to the first heading when its own would be hidden", { skip }, async () => {
  const c2 = await idOf("c2");
  await cell(c2).locator(".gutter").click({ position: { x: 10, y: 40 } });
  await page().getByRole("button", { name: "Collapse all sections" }).click();
  assert.strictEqual(await page().locator(".cell").count(), 1, "the level 1 heading hides the rest");
  assert.strictEqual(await page().evaluate(() => state.notebook.cells.find((c) => c.id === state.selected).cell_id), "m1");
  assert.deepStrictEqual(app.errors, []);
});

test("a heading's cell menu collapses its section and then expands it", { skip }, async () => {
  const m2 = await idOf("m2");
  await cell(m2).locator(".gutter").click({ position: { x: 10, y: 40 } });
  await cell(m2).getByRole("button", { name: "More cell actions" }).click();
  await page().getByRole("menuitem", { name: "Collapse section" }).click();
  await cell(m2).getByText("1 cell hidden. Click to show.").waitFor();
  assert.strictEqual((await meta("m2")).heading_collapsed, true);
  await cell(m2).getByRole("button", { name: "More cell actions" }).click();
  await page().getByRole("menuitem", { name: "Expand section" }).click();
  await cell(m2).locator(".section-hidden").waitFor({ state: "detached" });
  assert.strictEqual((await meta("m2")).heading_collapsed, undefined);
  assert.deepStrictEqual(app.errors, []);
});

test("a title over 200 characters is refused; Remove title takes it off the cell and the outline", { skip }, async () => {
  const c1 = await idOf("c1");
  await cell(c1).locator(".gutter").click({ position: { x: 10, y: 40 } });
  await cell(c1).getByRole("button", { name: "More cell actions" }).click();
  await page().getByRole("menuitem", { name: "Add title" }).click();
  await page().locator(".dialog input").fill("x".repeat(201));
  await page().keyboard.press("Enter");
  await page().locator(".dialog").getByText("Keep the title under 200 characters.").waitFor();
  await page().locator(".dialog input").fill("Load the orders");
  await page().keyboard.press("Enter");
  await cell(c1).locator(".cell-title").getByText("Load the orders").waitFor();
  await page().locator(".nb-outline").getByRole("button", { name: "Load the orders" }).waitFor();
  await cell(c1).getByRole("button", { name: "More cell actions" }).click();
  await page().getByRole("menuitem", { name: "Remove title" }).click();
  await cell(c1).locator(".cell-title").waitFor({ state: "detached" });
  await page().locator(".nb-outline").getByRole("button", { name: "Load the orders" }).waitFor({ state: "detached" });
  assert.strictEqual((await meta("c1")).title, undefined);
  assert.deepStrictEqual(app.errors, []);
});
