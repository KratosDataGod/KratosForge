// Tables and files from the notebook's Explorer (nbdrag.js): drag one into a cell and the name or
// the load code lands where it's dropped; right-click shows the ways to load it. The lakehouse
// listing is mocked; nothing is read.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { playwright, openApp } = require("./harness.cjs");

const SHOTS = process.env.SCREENSHOT_DIR;
const skip = !playwright && "Playwright is not installed";

const LH = { workspace_id: "w1", lakehouse_id: "l1", name: "LH_Bronze", workspace_name: "Dev" };
const CELLS = [
  { type: "code", cell_id: "c1", source: "import polars as pl\n\nprint()", meta: {} },
  { type: "code", cell_id: "c2", source: "%%sql\nSELECT * FROM ", meta: {} },
  { type: "code", cell_id: "c3", source: "x = 1", meta: { frozen: true } },
];

let app;
const page = () => app.page;
const sources = () => page().evaluate(() => state.notebook.cells.map((c) => c.source));

before(async () => {
  if (skip) return;
  app = await openApp({
    mocks: {
      save_notebook: () => ({ saved: true }),
      list_tables: () => ({ dbo: ["sales"] }),
      list_files: () => [{ name: "orders.csv", path: "Files/raw/orders.csv", is_dir: false, size: 2048 }],
    },
  });
  await page().evaluate(([cells, lh]) => {
    loadIntoState({ name: "Loads", cells, lakehouse: lh });
    state.showExplorer = true;
    state.explorerTab = "data";
    go("notebook");
  }, [CELLS, LH]);
  await page().locator(".explorer").getByRole("button", { name: "Tables" }).click();
  await page().locator(".explorer").getByRole("button", { name: "Files" }).click();
  await page().locator(".explorer").getByRole("button", { name: "sales" }).waitFor();
});

after(async () => { if (app) await app.close(); });

async function dropOn(source, cellIndex, lineText, where = "start") {
  const target = await page().evaluate(([i, text, where]) => {
    const view = editors.get(state.notebook.cells[i].id);
    const doc = view.state.doc.toString();
    const at = text === null ? doc.length : doc.indexOf(text) + (where === "end" ? text.length : 0);
    const c = view.coordsAtPos(at);
    return { x: c.left + 1, y: (c.top + c.bottom) / 2 };
  }, [cellIndex, lineText, where]);
  await source.dragTo(page().locator(".cell").nth(cellIndex).locator(".cm-content"), { targetPosition: await relative(cellIndex, target) });
}

async function relative(i, abs) {
  const box = await page().locator(".cell").nth(i).locator(".cm-content").boundingBox();
  return { x: abs.x - box.x, y: abs.y - box.y };
}

test("a table dropped on an empty Python line becomes its load code", { skip }, async () => {
  const sales = page().locator(".explorer").getByRole("button", { name: "sales" });
  await dropOn(sales, 0, "\nprint", "start");  // the empty second line
  assert.strictEqual((await sources())[0], 'import polars as pl\ndf = lakehouse.read_table("dbo.sales")\ndf\nprint()');
});

test("dropped inside code it's the quoted name; in SQL the bare name", { skip }, async () => {
  const sales = page().locator(".explorer").getByRole("button", { name: "sales" });
  await dropOn(sales, 0, "print()", "end");
  assert.match((await sources())[0], /\nprint\(\)?"dbo\.sales"/);
  await dropOn(sales, 1, null);
  assert.strictEqual((await sources())[1], "%%sql\nSELECT * FROM dbo.sales");
});

test("a frozen cell refuses the drop", { skip }, async () => {
  const sales = page().locator(".explorer").getByRole("button", { name: "sales" });
  await dropOn(sales, 2, null);
  assert.strictEqual((await sources())[2], "x = 1");
  await page().locator("#toast").getByText(/frozen/).waitFor();
});

test("right-click a file lists the ways to load it, and one adds a cell", { skip }, async () => {
  const file = page().locator(".explorer").getByRole("button", { name: /orders\.csv/ });
  await file.click({ button: "right" });
  const menu = page().locator("#menu");
  await menu.waitFor();
  const items = await menu.getByRole("menuitem").allInnerTexts();
  assert.deepStrictEqual(items.map((t) => t.trim()), ["Load with Polars", "Load with Spark", "Read the file's text", "Copy path"]);
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "notebook-load-menu.png") });
  await menu.getByRole("menuitem", { name: "Load with Spark" }).click();
  const all = await sources();
  assert.ok(all.includes('df = spark.read.option("header", True).csv("Files/raw/orders.csv")\ndisplay(df)'));
  assert.deepStrictEqual(app.errors, []);
});
