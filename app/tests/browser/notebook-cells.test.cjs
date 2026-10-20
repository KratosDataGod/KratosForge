// Organising cells (nbcells.js): collapse a heading's section, cell titles and tags, duplicate,
// and drag a cell by its grip to move it.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { playwright, openApp } = require("./harness.cjs");

const SHOTS = process.env.SCREENSHOT_DIR;
const skip = !playwright && "Playwright is not installed";

const CELLS = [
  { type: "markdown", cell_id: "m1", source: "# Load", meta: {} },
  { type: "code", cell_id: "c1", source: "a = 1", meta: {} },
  { type: "markdown", cell_id: "m2", source: "## Detail", meta: {} },
  { type: "code", cell_id: "c2", source: "b = 2", meta: {} },
  { type: "markdown", cell_id: "m3", source: "# Report", meta: {} },
  { type: "code", cell_id: "c3", source: "c = 3", meta: {} },
];

let app;
let saved = null;
const page = () => app.page;
const cell = (id) => page().locator(`[data-cell="${id}"]`);
const idOf = (cellId) => page().evaluate((cid) => state.notebook.cells.find((c) => c.cell_id === cid).id, cellId);
const order = () => page().evaluate(() => state.notebook.cells.map((c) => c.cell_id));

before(async () => {
  if (skip) return;
  app = await openApp({ mocks: { save_notebook: (p) => { saved = p; return { saved: true }; } } });
  await page().evaluate((cells) => {
    loadIntoState({ name: "Organise", cells });
    state.ribbonTab = "view";
    go("notebook");
  }, CELLS);
});

after(async () => { if (app) await app.close(); });

test("collapsing a heading hides its section up to the next heading of the same level", { skip }, async () => {
  const m1 = await idOf("m1");
  await cell(m1).locator(".section-toggle").click();
  assert.strictEqual(await page().locator(".cell").count(), 3);  // # Load, # Report, c = 3
  await cell(m1).getByText("3 cells hidden. Click to show.").waitFor();
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "notebook-cells-collapsed.png") });
  await page().evaluate(() => saveNotebook());
  assert.strictEqual(saved.cells[0].meta.heading_collapsed, true);
  await cell(m1).locator(".section-hidden").click();
  assert.strictEqual(await page().locator(".cell").count(), 6);
});

test("Collapse all sections nests: an open parent with a collapsed child hides only the child's cells", { skip }, async () => {
  await page().getByRole("button", { name: "Collapse all sections" }).click();
  assert.strictEqual(await page().locator(".cell").count(), 2);
  const m1 = await idOf("m1");
  await cell(m1).locator(".section-toggle").click();  // open # Load; ## Detail stays collapsed
  assert.deepStrictEqual(await page().evaluate(() => [...document.querySelectorAll(".cell")].map((e) => state.notebook.cells.find((c) => String(c.id) === e.dataset.cell).cell_id)),
    ["m1", "c1", "m2", "m3"]);
  await page().getByRole("button", { name: "Expand all sections" }).click();
  assert.strictEqual(await page().locator(".cell").count(), 6);
});

test("the outline opens a collapsed section to show a cell", { skip }, async () => {
  await page().evaluate(() => { state.showExplorer = true; state.explorerTab = "outline"; renderNotebook(); });
  await cell(await idOf("m1")).locator(".section-toggle").click();
  await page().locator(".nb-outline").getByRole("button", { name: "Detail" }).click();
  assert.strictEqual(await page().locator(".cell").count(), 6);
  assert.strictEqual(await page().evaluate(() => state.notebook.cells.find((c) => c.id === state.selected).cell_id), "m2");
});

test("a title and tags show in the cell head, the title in the outline, and both save", { skip }, async () => {
  const c1 = await idOf("c1");
  await cell(c1).locator(".gutter").click({ position: { x: 10, y: 60 } });
  await cell(c1).getByRole("button", { name: "More cell actions" }).click();
  await page().getByRole("menuitem", { name: "Add title" }).click();
  await page().locator(".dialog input").fill("Load the orders");
  await page().keyboard.press("Enter");
  await cell(c1).locator(".cell-title").getByText("Load the orders").waitFor();
  await page().locator(".nb-outline").getByRole("button", { name: "Load the orders" }).waitFor();

  await cell(c1).getByRole("button", { name: "More cell actions" }).click();
  await page().getByRole("menuitem", { name: "Add tag" }).click();
  await page().locator(".dialog input").fill("has space");
  await page().keyboard.press("Enter");
  await page().locator(".dialog").getByText("Use letters, numbers").waitFor();
  await page().locator(".dialog input").fill("nightly");
  await page().keyboard.press("Enter");
  await cell(c1).locator(".user-tag").getByText("nightly").waitFor();
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "notebook-cells-title.png") });

  await page().evaluate(() => saveNotebook());
  const meta = saved.cells.find((c) => c.cell_id === "c1").meta;
  assert.strictEqual(meta.title, "Load the orders");
  assert.deepStrictEqual([...meta.tags], ["nightly"]);

  await cell(c1).getByRole("button", { name: "Remove tag nightly" }).click();
  assert.strictEqual(await cell(c1).locator(".user-tag").count(), 0);
  assert.strictEqual(await page().evaluate((id) => state.notebook.cells.find((c) => c.id === id).meta.tags, c1), undefined);
});

test("Duplicate cell copies the source and title below, without the parameters tag", { skip }, async () => {
  const c1 = await idOf("c1");
  await page().evaluate((id) => { const c = state.notebook.cells.find((x) => x.id === id); c.meta.tags = ["parameters"]; updateCell(c); }, c1);
  await cell(c1).getByRole("button", { name: "More cell actions" }).click();
  await page().getByRole("menuitem", { name: "Duplicate cell" }).click();
  const cells = await page().evaluate(() => state.notebook.cells.map((c) => ({ id: c.cell_id, source: c.source, meta: c.meta })));
  assert.strictEqual(cells.length, 7);
  assert.strictEqual(cells[2].source, "a = 1");
  assert.strictEqual(cells[2].meta.title, "Load the orders");
  assert.strictEqual(cells[2].meta.tags, undefined);
  assert.notStrictEqual(cells[2].id, "c1");
  await page().evaluate(() => { state.notebook.cells.splice(2, 1); state.notebook.cells[1].meta.tags = []; delete state.notebook.cells[1].meta.tags; renderNotebook(); });
});

// Playwright's dragTo scrolls mid-drag here, so the drag is done in steps from a scrolled-to-top list.
async function dragCell(id, targetId, after) {
  await page().evaluate(() => { document.querySelector(".cells").scrollTop = 0; });
  await cell(id).hover();
  const g = await cell(id).locator(".cell-grip").boundingBox();
  const t = await cell(targetId).boundingBox();
  await page().mouse.move(g.x + 4, g.y + 4);
  await page().mouse.down();
  await page().mouse.move(g.x + 10, g.y - 10, { steps: 3 });
  await page().mouse.move(t.x + 200, after ? t.y + t.height - 4 : t.y + 4, { steps: 5 });
  await page().mouse.up();
}

test("dragging a cell by its grip moves it above or below the cell it's dropped on", { skip }, async () => {
  await dragCell(await idOf("c3"), await idOf("m1"), false);
  assert.deepStrictEqual(await order(), ["c3", "m1", "c1", "m2", "c2", "m3"]);
  await dragCell(await idOf("c3"), await idOf("m3"), true);
  assert.deepStrictEqual(await order(), ["m1", "c1", "m2", "c2", "m3", "c3"]);
  assert.strictEqual(await page().locator(".drop-above, .drop-below, .dragging").count(), 0);
});
