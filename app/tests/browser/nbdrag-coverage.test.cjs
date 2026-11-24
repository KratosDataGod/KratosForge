// Tables and files from the notebook's Explorer (nbdrag.js), the paths notebook-drag leaves out: Copy
// name and Copy path from the right-click menu (and when the clipboard refuses), Esc giving focus back
// to the row, a table whose name needs backticks in SQL, a name dropped into Markdown, and a table
// dropped between cells, which adds a cell that loads it under the cell it was dropped on. The
// lakehouse listing is mocked; what the person sees and what lands in the notebook is checked.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
const LH = { workspace_id: "w1", lakehouse_id: "l1", name: "LH_Bronze", workspace_name: "Dev" };
const CELLS = [
  { type: "code", cell_id: "c1", source: "a = 1", meta: {} },
  { type: "code", cell_id: "c2", source: "b = 2", meta: {} },
];

let app;
const page = () => app.page;
const explorer = () => page().locator(".explorer");
const menu = () => page().locator("#menu");
const sources = () => page().evaluate(() => state.notebook.cells.map((c) => c.source));
const toastSays = (re) => page().waitForFunction((src) => new RegExp(src).test(document.getElementById("toast")?.textContent || ""), re.source);

before(async () => {
  if (skip) return;
  app = await openApp({ mocks: {
    save_notebook: () => ({ saved: true }),
    list_tables: () => ({ dbo: ["sales", "order lines"] }),
    list_files: () => [{ name: "orders.csv", path: "Files/raw/orders.csv", is_dir: false, size: 2048 }],
  } });
  await page().evaluate(([cells, lh]) => {
    loadIntoState({ name: "Loads", cells, lakehouse: lh });
    state.showExplorer = true;
    state.explorerTab = "data";
    go("notebook");
  }, [CELLS, LH]);
  await explorer().getByRole("button", { name: "Tables" }).click();
  await explorer().getByRole("button", { name: "Files" }).click();
  await explorer().getByRole("button", { name: "sales", exact: true }).waitFor();
  app.errors.length = 0;
});
after(async () => { if (app) await app.close(); });

beforeEach(async () => {
  if (skip) return;
  await page().evaluate((cells) => {
    state.notebook.cells.splice(0, state.notebook.cells.length, ...cells.map((c) => cellFrom(structuredClone(c))));
    state.selected = state.notebook.cells[0].id;
    renderNotebook();
    window.__copied = null;
    Object.defineProperty(navigator, "clipboard", { value: { writeText: async (t) => { window.__copied = t; } }, configurable: true });
    const t = document.getElementById("toast"); t.hidden = true; t.textContent = "";
  }, CELLS);
  app.errors.length = 0;
});

test("Copy name copies a table's name; when the clipboard refuses it says the name instead", { skip }, async () => {
  const sales = explorer().getByRole("button", { name: "sales", exact: true });
  await sales.click({ button: "right" });
  await menu().getByRole("menuitem", { name: "Copy name" }).click();
  await toastSays(/^Copied dbo\.sales$/);
  assert.strictEqual(await page().evaluate(() => window.__copied), "dbo.sales");
  await page().evaluate(() => {
    Object.defineProperty(navigator, "clipboard", { value: { writeText: async () => { throw new Error("denied"); } }, configurable: true });
  });
  await sales.click({ button: "right" });
  await menu().getByRole("menuitem", { name: "Copy name" }).click();
  await toastSays(/^Couldn't copy\. The name is dbo\.sales$/);
  assert.deepStrictEqual(app.errors, []);
});

test("Copy path copies a file's path; Esc closes the menu and gives the row its focus back", { skip }, async () => {
  const file = explorer().getByRole("button", { name: /orders\.csv/ });
  await file.click({ button: "right" });
  await menu().waitFor();
  await page().keyboard.press("Escape");
  await menu().waitFor({ state: "hidden" });
  assert.ok(await file.evaluate((el) => el === document.activeElement), "the row has focus again");
  await file.click({ button: "right" });
  await menu().getByRole("menuitem", { name: "Copy path" }).click();
  await toastSays(/^Copied Files\/raw\/orders\.csv$/);
  assert.strictEqual(await page().evaluate(() => window.__copied), "Files/raw/orders.csv");
  assert.deepStrictEqual(app.errors, []);
});

test("a table whose name has a space is queried in backticks", { skip }, async () => {
  await explorer().getByRole("button", { name: "order lines" }).click({ button: "right" });
  await menu().getByRole("menuitem", { name: "Query with SQL" }).click();
  assert.ok((await sources()).includes("%%sql\nSELECT * FROM dbo.`order lines` LIMIT 1000"), JSON.stringify(await sources()));
  // In Markdown a dropped name is code; a backtick inside a SQL name is doubled.
  assert.deepStrictEqual(await page().evaluate(() => [
    dataItemDropText({ kind: "table", schema: "dbo", table: "order lines" }, "markdown"),
    dataItemDropText({ kind: "table", schema: "raw data", table: "a`b" }, "sql"),
  ]), ["`dbo.order lines`", "`raw data`.`a``b`"]);
  assert.deepStrictEqual(app.errors, []);
});

// A drop the way the browser sends one, carrying what dragging the Explorer row puts in it.
const dropOnGutter = (i, item) => page().evaluate(([i, item]) => {
  const dt = new DataTransfer();
  dt.setData("application/x-kratosforge-data-item", JSON.stringify(item));
  const target = document.querySelectorAll(".cell")[i].querySelector(".gutter");
  const over = new DragEvent("dragover", { dataTransfer: dt, bubbles: true, cancelable: true });
  target.dispatchEvent(over);
  const drop = new DragEvent("drop", { dataTransfer: dt, bubbles: true, cancelable: true });
  target.dispatchEvent(drop);
  return [over.defaultPrevented, drop.defaultPrevented];
}, [i, item]);

test("a table dropped on a cell outside its editor adds a cell under it that loads the table", { skip }, async () => {
  assert.deepStrictEqual(await dropOnGutter(0, { kind: "table", schema: "dbo", table: "sales" }), [true, true]);
  assert.deepStrictEqual(await sources(), ["a = 1", 'df = lakehouse.read_table("dbo.sales")\ndf', "b = 2"]);
  assert.strictEqual(await page().locator(".cells.drop-ready").count(), 0);
  // A drop that carries no Explorer item is left alone.
  const plain = await page().evaluate(() => {
    const dt = new DataTransfer(); dt.setData("text/plain", "hello");
    const drop = new DragEvent("drop", { dataTransfer: dt, bubbles: true, cancelable: true });
    document.querySelector(".cell .gutter").dispatchEvent(drop);
    return drop.defaultPrevented;
  });
  assert.strictEqual(plain, false);
  assert.strictEqual((await sources()).length, 3);
  assert.deepStrictEqual(app.errors, []);
});
