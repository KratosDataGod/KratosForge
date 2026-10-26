// AF-4 (docs/ux/action-first.md): in a notebook Run all is the solid button, Publish stays one click
// away, the notebook's actions are in the command bar, and after a run of several cells a line under
// the ribbon says what happened and offers the next step from what the run showed (see harness.cjs).
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
let app;
const page = () => app.page;
const code = (id, source) => ({ type: "code", cell_id: id, source, meta: {} });

before(async () => {
  if (skip) return;
  app = await openApp({ engine: ["start_cell", "poll_cell", "kernel_variables", "kernel_complete", "interrupt_cell"] });
});
after(async () => { if (app) await app.close(); });

async function open(cells) {
  await page().evaluate((cells) => { loadIntoState({ name: "Orders", cells }); state.ribbonTab = "home"; go("notebook"); }, cells);
  await page().waitForSelector(".ribbon button[data-run]");
}
async function runAllAndWait() {
  await page().evaluate(() => runAll());
  await page().waitForFunction(() => state.kernel !== "busy" && state.notebook.lastRun, null, { timeout: 60000 });
}

test("Run all is the one solid button and Publish to OneLake is still on the ribbon", { skip }, async () => {
  await open([code("c1", "a = 1"), code("c2", "b = a + 1")]);
  const solid = await page().$$eval(".ribbon .toolbar button.primary", (bs) => bs.map((b) => b.textContent.trim()));
  assert.deepStrictEqual(solid, ["Run all"]);
  assert.ok(await page().$('.ribbon .split button:has-text("Publish to OneLake")'));
  assert.ok(await page().$(".ribbon .split .split-more"));
  assert.strictEqual(await page().$(".nb-next"), null);  // nothing to say before a run
});

test("the command bar finds the notebook's actions", { skip }, async () => {
  await open([code("c1", "a = 1")]);
  const labels = await page().evaluate(() => actionsFor({ view: "notebook", nb: state.notebook, busy: false }).map((s) => s.action.label));
  for (const l of ["Run all", "Run the selected cell", "Restart the kernel and run all", "Save the notebook", "Publish to OneLake"]) {
    assert.ok(labels.includes(l), l);
  }
  const palette = await page().evaluate(() => paletteItems("run").filter((i) => i.group === "Actions").map((i) => i.label));
  assert.ok(palette.includes("Run all"), "Run all isn't in the command bar");
  assert.ok(palette.includes("Run the selected cell"), "Run the selected cell isn't in the command bar");
});

test("after a clean run the line says so and offers Publish; closing it hides it", { skip }, async () => {
  await open([code("c1", "a = 1"), code("c2", "b = a + 1")]);
  await page().evaluate(() => { state.notebook.dirty = false; });
  await runAllAndWait();
  await page().evaluate(() => { state.notebook.dirty = false; refreshNbNextLine(state.notebook); });
  const line = page().locator(".nb-next");
  assert.match(await line.textContent(), /^Ran 2 cells on this PC in \d+\.\d s\.Every cell ran\./);
  assert.ok(await line.locator('button:has-text("Publish to OneLake")').count());
  await line.locator(".nb-next-close").click();
  assert.strictEqual(await page().$(".nb-next"), null);
  await page().evaluate(() => renderNotebook());
  assert.strictEqual(await page().$(".nb-next"), null, "a closed line came back on redraw");
  assert.deepStrictEqual(app.errors, []);
});

test("after a failed run the line leads with the failed cell and Go to the failed cell selects it", { skip }, async () => {
  await open([code("c1", "a = 1"), code("c2", "raise ValueError('bad row')"), code("c3", "c = 3")]);
  await runAllAndWait();
  const line = page().locator(".nb-next");
  assert.match(await line.textContent(), /Cell 2 failed\./);
  assert.doesNotMatch(await line.textContent(), /Every cell ran/);
  const first = await line.locator("[data-recommendation]").first().getAttribute("data-recommendation");
  assert.strictEqual(first, "notebook.failed");
  await page().evaluate(() => selectCell(state.notebook.cells[0]));
  await line.locator('button:has-text("Go to the failed cell")').click();
  assert.strictEqual(await page().evaluate(() => state.notebook.cells.indexOf(selectedCell())), 1);
});

test("Validate's warnings put Review warnings before Publish, as the evidence says", { skip }, async () => {
  await open([code("c1", "a = 1"), code("c2", "b = a + 1")]);
  await runAllAndWait();
  await page().evaluate(() => {
    state.notebook.dirty = false;
    state.notebook.validate = { record: { counts: { warning: 3 } } };
    refreshNbNextLine(state.notebook);
  });
  const line = page().locator(".nb-next");
  assert.match(await line.textContent(), /Validate found 3 warnings\./);
  const buttons = await line.locator("button:not(.nb-next-close)").allTextContents();
  assert.deepStrictEqual(buttons.map((b) => b.trim()), ["Review warnings", "Publish to OneLake"]);
});
