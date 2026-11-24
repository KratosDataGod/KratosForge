// Cell dependencies (deps.js) beyond the pure checks in ui.test.cjs: the strip under each cell shows
// tables and files it reads and who wrote them, what was declared, names it reads that no cell writes,
// names nothing defines and code reading can't follow; a ref goes to its cell; a cell reading can't
// follow goes stale when any cell above ran again; Run stale cells asks before a cell with effects; and
// a graph that can't be read says so in each cell and when you ask to run. notebook_dependencies is
// mocked; a cell's finished run is stood in for with depsRan, which notebook.js calls when a run ends.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
let app;
const page = () => app.page;

const CELLS = [
  { type: "code", cell_id: "c1", source: "rows = load()\nrows.write_table('sales')", meta: {} },
  { type: "code", cell_id: "c2", source: "s = spark.table('sales')\nd = spark.table('dim_date')\nn = len(rows)", meta: {} },
  { type: "code", cell_id: "c3", source: "exec(code)\nopen('Files/b.csv', 'w')", meta: { depends_on: ["c1"] } },
  { type: "code", cell_id: "c4", source: "1 + 1", meta: {} },
];
const GRAPH = { cells: [
  { cell_id: "c1", n: 1, defines: ["rows"], writes: [{ kind: "table", name: "sales" }], downstream: [2, 3] },
  { cell_id: "c2", n: 2, upstream: [{ kind: "table", via: ["sales"], n: 1, cell_id: "c1", source: "code" }, { kind: "variable", via: ["rows"], n: 1, cell_id: "c1", source: "code" }],
    reads: [{ name: "sales" }, { name: "dim_date" }], unresolved: [{ text: "Uses spark, which no cell defines" }] },
  { cell_id: "c3", n: 3, upstream: [{ kind: "file", via: ["Files/a.csv"], n: 1, cell_id: "c1", source: "declared" }],
    dynamic: [{ line: 1, text: "exec() runs code that reading can't follow" }], effects: [{ text: "writes Files/b.csv" }] },
  { cell_id: "c4", n: 4 },
] };

let graph;
before(async () => {
  if (skip) return;
  app = await openApp({ mocks: {
    save_notebook: () => ({ saved: true }),
    notebook_dependencies: () => { if (graph instanceof Error) throw graph; return graph; },
  } });
});
after(async () => { await app?.close(); });

beforeEach(async () => {
  if (skip) return;
  graph = JSON.parse(JSON.stringify(GRAPH));
  await page().evaluate((cells) => {
    document.getElementById("dialog-root").replaceChildren();
    const t = document.getElementById("toast"); t.hidden = true; t.textContent = "";
    state.showDeps = false; state.kernel = "idle";
    loadIntoState({ name: `Deps ${Math.random().toString(36).slice(2, 7)}`, cells: JSON.parse(JSON.stringify(cells)) });
    state.ribbonTab = "view";
    go("notebook");
  }, CELLS);
  await page().getByRole("button", { name: "Dependencies" }).click();
});

const strip = (i) => page().locator(".cell").nth(i).locator(".deps-strip");
const toastSays = (re) => page().waitForFunction((src) => new RegExp(src).test(document.getElementById("toast").textContent), re.source);

test("each cell's strip says what it reads and from where, what was declared, and what reading can't follow", { skip }, async () => {
  await strip(3).getByText("Uses nothing from other cells.").waitFor();
  assert.deepStrictEqual(await strip(0).locator(":scope > span").allInnerTexts(), ["Writes table sales", "Defines rows", "Used by 2, 3"]);
  assert.deepStrictEqual(await strip(1).locator(":scope > span").allInnerTexts(), [
    "Uses rows from 1", "Reads table sales, written by 1", "Reads dim_date", "Uses spark, which no cell defines"]);
  assert.deepStrictEqual(await strip(2).locator(":scope > span").allInnerTexts(), [
    "Reads file Files/a.csv, written by 1", "Declared to depend on 1",
    "Line 1: exec() runs code that reading can't follow, so it's treated as depending on every cell above"]);
  assert.strictEqual(await strip(1).locator("span.warn").innerText(), "Uses spark, which no cell defines");
  assert.deepStrictEqual(app.calls.filter((c) => c.method === "notebook_dependencies").at(-1).params.cells[2].meta, { depends_on: ["c1"] });
  await strip(1).getByRole("button", { name: "1" }).first().click();
  assert.strictEqual(await page().evaluate(() => state.notebook.cells.indexOf(selectedCell())), 0);
});

test("a cell reading can't follow is stale when any cell above ran again, and Run stale cells asks before its effects", { skip }, async () => {
  await strip(3).getByText("Uses nothing from other cells.").waitFor();
  // Every cell ran in order, then cell 1 ran again.
  await page().evaluate(() => {
    const cells = state.notebook.cells;
    cells.forEach((c) => depsRan(c, true));
    depsRan(cells[0], true);
    depsChanged(true);
  });
  const tag = (i) => page().locator(".cell").nth(i).locator(".deps-stale");
  await tag(2).waitFor();
  assert.strictEqual(await tag(1).innerText(), "Stale: cell 1 ran again");
  assert.strictEqual(await tag(2).innerText(), "Stale: cell 1 ran again", "exec() depends on every cell above");
  assert.strictEqual(await tag(3).count(), 0);

  await page().evaluate(() => { state.ribbonTab = "run"; renderNotebook(); });
  await page().getByRole("button", { name: "Run stale cells (2)" }).click();
  const confirm = page().locator("#dialog-root .dialog");
  await confirm.getByText("Cell 3 writes Files/b.csv.").waitFor();
  assert.strictEqual(await confirm.getAttribute("aria-label"), "Run 2 stale cells?");
  await confirm.getByRole("button", { name: "Run 2 only" }).waitFor();
  await confirm.getByRole("button", { name: "Run 2 and 3" }).waitFor();
  await confirm.getByRole("button", { name: "Cancel" }).click();
  await confirm.waitFor({ state: "detached" });
});

test("a graph that can't be read says so under each cell, and Run stale cells says why it can't", { skip }, async () => {
  graph = new Error("The dependency reader stopped.");
  await page().evaluate(() => { setSource(state.notebook.cells[3], "2 + 2"); depsChanged(true); });
  await strip(0).getByText("Couldn't read dependencies: The dependency reader stopped.").waitFor();
  await page().evaluate(() => { state.ribbonTab = "run"; renderNotebook(); });
  const run = page().locator("button[data-deps-run]");
  assert.match(await run.innerText(), /^Run stale cells\s*Preview$/, "no count without a graph");
  await run.click();
  await toastSays(/^Couldn't read the notebook's dependencies: The dependency reader stopped\./);
});
