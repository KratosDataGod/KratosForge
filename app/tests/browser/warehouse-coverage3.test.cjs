// app/ui/warehouse.js beyond the Warehouse page tests: a catalog query that waits (queued, then running)
// and reads more than one page, one that fails, one whose page asks for a rerun, Script as CREATE and
// ALTER when Fabric returns no definition or the query fails (said by name, nothing opened), the
// completion provider's columns, and the explorer's Diagram button. The run calls are mocked with the
// engine's own reply shapes (sql_start, sql_status, sql_page): nothing reaches Fabric.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
let app;
const page = () => app.page;
const WH = { kind: "warehouse", workspace_id: "ws1", workspace_name: "Sales Dev", item_id: "wh1", name: "WH_Gold" };
const DEF = "CREATE PROCEDURE dbo.usp_Slow\nAS\nSELECT 1;";

// Each run by what its query names: how its status goes, and its pages of rows.
const runs = new Map();
let next = 1;
const plan = (query) => {
  if (query.includes("usp_Slow")) return { states: ["queued", "running", "completed"], pages: [[[DEF]], []] };
  if (query.includes("usp_Empty")) return { states: ["completed"], pages: [[["   "]]] };
  if (query.includes("usp_Gone")) return { states: ["running", "failed"], error: "Invalid object name 'dbo.usp_Gone'." };
  if (query.includes("usp_Rerun")) return { states: ["completed"], rerun: "The results expired. Run the query again." };
  if (query.includes("usp_Cancel")) return { states: ["cancelled"] };
  return { states: ["completed"], pages: [[]] };
};
const mocks = {
  sql_start: (p) => {
    const id = `run${next++}`;
    const r = { ...plan(p.query), step: 0 };
    runs.set(id, r);
    return { id, state: r.states[0], target: "warehouse", rows: 0, columns: [], seconds: 0 };
  },
  sql_status: ({ execution }) => {
    const r = runs.get(execution);
    r.step = Math.min(r.step + 1, r.states.length - 1);
    const state = r.states[r.step];
    return { id: execution, state, seconds: 0.1, ...(state === "failed" ? { error: r.error } : {}) };
  },
  sql_page: ({ execution, offset = 0 }) => {
    const r = runs.get(execution);
    if (r.rerun) return { id: execution, rerun_needed: true, message: r.rerun };
    r.read = (r.read || 0) + 1;  // the pages in turn, as the app asks for each from where the last ended
    const rows = r.pages[r.read - 1];
    return { id: execution, offset, rows, columns: [{ name: "definition", type: "varchar" }], has_next: r.read < r.pages.length };
  },
};

before(async () => {
  if (skip) return;
  app = await openApp({ mocks });
});
after(async () => { await app?.close(); });
beforeEach(async () => {
  if (skip) return;
  await page().evaluate(() => { const t = document.getElementById("toast"); t.hidden = true; t.textContent = ""; });
});

// Script as from the object's menu items, with open() recording what it would open.
const script = (name, label) => page().evaluate(async ([wh, name, label]) => {
  const opened = [];
  const items = whScriptItems(wh, { schema: "dbo", name, type: "P" }, [], { open: (title, text) => opened.push([title, text]), query: () => {} });
  await items.find((it) => it.label === label).action();
  return opened;
}, [WH, name, label]);
const toastText = () => page().evaluate(() => document.getElementById("toast").textContent);

test("a catalog query that waits in the queue and reads two pages gives the definition; ALTER replaces its CREATE", { skip }, async () => {
  const created = await script("usp_Slow", "Script as CREATE");
  assert.strictEqual(created.length, 1);
  assert.strictEqual(created[0][0], "usp_Slow (CREATE)");
  assert.ok(created[0][1].endsWith(`${DEF}\n`));
  const altered = await script("usp_Slow", "Script as ALTER");
  assert.match(altered[0][1], /ALTER PROCEDURE dbo\.usp_Slow\nAS\nSELECT 1;\n$/);
  const id = [...runs.keys()].at(-1);
  assert.strictEqual(runs.get(id).step, 2, "it asked for the status until it completed");
  assert.match(await toastText(), /Reading the definition of usp_Slow\.\.\./);
  assert.deepStrictEqual(app.errors, []);
});

test("no definition, a failed query, a page that needs a rerun or a cancelled run is said by name and opens nothing", { skip }, async () => {
  const cases = [
    ["usp_Empty", "Could not script usp_Empty: Fabric returned no definition for usp_Empty."],
    ["usp_Gone", "Could not script usp_Gone: Invalid object name 'dbo.usp_Gone'."],
    ["usp_Rerun", "Could not script usp_Rerun: The results expired. Run the query again."],
    ["usp_Cancel", "Could not script usp_Cancel: The catalog query cancelled."],
  ];
  for (const [name, said] of cases) {
    assert.deepStrictEqual(await script(name, "Script as CREATE"), [], name);
    assert.strictEqual(await toastText(), said);
  }
  assert.deepStrictEqual(app.errors, []);
});

test("the completion provider gives a table's columns from the loaded catalog, and none for another warehouse", { skip }, async () => {
  const got = await page().evaluate(async (wh) => {
    sqlPage.catalog = { key: sqlRefKey(wh), state: "done", objects: [{ schema: "dbo", name: "Sales", type: "U" }, { schema: "dbo", name: "usp", type: "P" }],
      cols: { "dbo.Sales": [{ name: "Amount", type: "decimal(18,2)", nullable: false }] }, at: new Date() };
    const p = warehouseSqlProvider(wh);
    const other = warehouseSqlProvider({ ...wh, item_id: "wh2" });
    return [p.id, p.tables(), p.cachedColumns({ schema: "dbo", name: "Sales" }), await p.columns({ schema: "dbo", name: "Sales" }),
      await p.columns({ schema: "dbo", name: "Nope" }), other.cachedColumns({ schema: "dbo", name: "Sales" }), other.tables()];
  }, WH);
  assert.deepStrictEqual(got, ["warehouse:wh1", [{ schema: "dbo", name: "Sales" }],
    [{ name: "Amount", type: "decimal(18,2)", nullable: false }], [{ name: "Amount", type: "decimal(18,2)", nullable: false }], [], undefined, []]);
  assert.deepStrictEqual(app.errors, []);
});

test("the explorer offers Diagram once the catalog has a table, and its Refresh lists the objects again", { skip }, async () => {
  await page().evaluate((wh) => {
    window.__wh = { diagram: 0, load: 0 };
    sqlPage.catalog = { key: sqlRefKey(wh), state: "done", objects: [{ schema: "dbo", name: "Sales", type: "U" }], cols: {}, at: new Date() };
    const ex = whExplorer(wh, { load: () => { window.__wh.load++; }, query: () => {}, insert: () => {}, open: () => {},
      diagram: () => { window.__wh.diagram++; } });
    ex.el.id = "kf-wh";
    document.body.append(ex.el);
  }, WH);
  await page().locator("#kf-wh").getByRole("button", { name: "Diagram" }).click();
  await page().locator("#kf-wh").getByRole("link", { name: "Refresh" }).click();
  assert.deepStrictEqual(await page().evaluate(() => window.__wh), { diagram: 1, load: 1 });
  await page().evaluate(() => document.getElementById("kf-wh").remove());
  assert.deepStrictEqual(app.errors, []);
});
