// PB-027: a %%sql cell completes a table alias with that table's columns, without running a cell,
// and from the names this PC cached when Fabric can't be asked. The lakehouse is mocked; nothing is read.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { playwright, openApp } = require("./harness.cjs");

const SHOTS = process.env.SCREENSHOT_DIR;
const skip = !playwright && "Playwright is not installed";

const ONLINE = { workspace_id: "w1", lakehouse_id: "l1", name: "LH_Sales", workspace_name: "Dev" };
const OFFLINE = { workspace_id: "w1", lakehouse_id: "l2", name: "LH_Away", workspace_name: "Dev" };
const SALES = [{ name: "order_id", type: "long" }, { name: "amount", type: "double" }, { name: "Region Name", type: "string" }];
const CACHED = { tables: { dbo: ["orders"] }, columns: { orders: [{ name: "order_ref", type: "string" }, { name: "placed_at", type: "timestamp" }] } };
const offline = () => { throw new Error("Couldn't reach OneLake"); };

let app;
const page = () => app.page;

before(async () => {
  if (skip) return;
  app = await openApp({
    mocks: {
      save_notebook: () => ({ saved: true }),
      list_tables: ({ lakehouse_id }) => (lakehouse_id === "l2" ? offline() : { dbo: ["sales", "customers"] }),
      table_columns: ({ lakehouse_ref, table }) => (lakehouse_ref.lakehouse_id === "l2" ? offline() : table === "sales" ? SALES : []),
      cached_schemas: ({ lakehouse_id }) => (lakehouse_id === "l2" ? CACHED : { tables: {}, columns: {} }),
    },
  });
});

after(async () => { if (app) await app.close(); });

async function openWith(lh, sql) {
  await page().evaluate(([lh, sql]) => {
    loadIntoState({ name: "Completion", cells: [{ type: "code", cell_id: "c1", source: sql, meta: {} }], lakehouse: lh });
    go("notebook");
  }, [lh, sql]);
  await page().locator(".cell .cm-content").first().waitFor();
  await page().waitForFunction((id) => state.nbSchemas[id] && Object.keys(state.nbSchemas[id].tables).length, lh.lakehouse_id);
}

// Puts the cursor just before `marker` (removed) in the first cell and types `typed` there.
async function typeAt(sql, typed) {
  await page().evaluate((at) => {
    const view = editors.get(state.notebook.cells[0].id);
    view.dispatch({ selection: { anchor: at } });
    view.focus();
  }, sql.indexOf("|"));
  await page().keyboard.type(typed, { delay: 30 });
  await page().waitForSelector(".cm-tooltip-autocomplete");
  return page().locator(".cm-tooltip-autocomplete li .cm-completionLabel").allTextContents();
}

const runs = () => app.calls.filter((c) => /cell|run_sql|sql_start/.test(c.method)).map((c) => c.method);

test("typing an alias offers its table's columns, without running a cell", { skip }, async () => {
  const sql = "%%sql\nSELECT | FROM sales AS s JOIN customers c ON c.id = s.id";
  await openWith(ONLINE, sql.replace("|", ""));
  const labels = await typeAt(sql, "s.");
  assert.deepStrictEqual(labels.sort(), ["Region Name", "amount", "order_id"].sort());
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "pb-027-alias-columns.png") });
  await page().locator(".cm-tooltip-autocomplete li", { hasText: "Region Name" }).click();
  const text = await page().evaluate(() => editors.get(state.notebook.cells[0].id).state.doc.toString());
  assert.strictEqual(text, "%%sql\nSELECT s.`Region Name` FROM sales AS s JOIN customers c ON c.id = s.id");
  assert.deepStrictEqual(runs(), [], "nothing ran");
  assert.strictEqual(app.calls.filter((c) => c.method === "table_columns").length, 1, "only the alias's table is read");
});

test("offline, the tables and columns come from the names this PC cached", { skip }, async () => {
  const sql = "%%sql\nSELECT | FROM orders o";
  await openWith(OFFLINE, sql.replace("|", ""));
  const labels = await typeAt(sql, "o.");
  assert.deepStrictEqual(labels.sort(), ["order_ref", "placed_at"]);
  await page().keyboard.press("Escape");
  await page().evaluate(() => {
    const view = editors.get(state.notebook.cells[0].id);
    view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: "%%sql\nSELECT * FROM " }, selection: { anchor: "%%sql\nSELECT * FROM ".length } });
  });
  assert.ok((await typeAt("%%sql\nSELECT * FROM |", "or")).includes("orders"), "the cached table is offered");
  await page().keyboard.press("Escape");
  assert.deepStrictEqual(runs(), [], "nothing ran");
  assert.deepStrictEqual(app.errors, []);
});
