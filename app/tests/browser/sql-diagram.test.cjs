// SQL page > Explorer > Diagram (sqldiagram.js, parity item 15), read only. On a warehouse, solid lines come
// from its keys (one more catalog query) and dashed lines are guessed from names; on a lakehouse every line
// is a guess. Clicking a table opens it in the Explorer, and Export as SVG is written by the real engine.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const { playwright, openApp, sqlRunMocks, axeSource, axeFindings } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
const WH = { kind: "warehouse", workspace_id: "ws1", workspace_name: "Sales Dev", item_id: "wh1", name: "SalesWH" };
const LH = { workspace_id: "ws1", lakehouse_id: "lh1", name: "sales_lh" };
const OBJECTS = [["gold", "dim_customer", "U"], ["gold", "fact_sales", "U"], ["gold", "dim_product", "U"], ["gold", "dim_store", "U"],
  ["gold", "v_sales", "V"], ["dbo", "audit_log", "U"]];
const COLUMNS = [["gold", "dim_customer", "customer_id", "int"], ["gold", "dim_customer", "name", "varchar"],
  ["gold", "fact_sales", "order_id", "int"], ["gold", "fact_sales", "customer_id", "int"], ["gold", "fact_sales", "product_id", "int"],
  ["gold", "fact_sales", "store_id", "int"], ["gold", "fact_sales", "amount", "decimal"],
  ["gold", "dim_product", "product_id", "int"], ["gold", "dim_store", "store_id", "int"], ["dbo", "audit_log", "id", "int"]]
  .map(([s, o, c, t]) => [s, o, c, t, 4, 10, 0, 1]);
const KEYS = [["P", "gold", "dim_customer", "customer_id", null, null, null], ["P", "gold", "dim_product", "product_id", null, null, null],
  ["F", "gold", "fact_sales", "customer_id", "gold", "dim_customer", "customer_id"],
  ["F", "gold", "fact_sales", "product_id", "gold", "dim_product", "product_id"]];
let app;
let keysFail = false;
const page = () => app.page;

before(async () => {
  if (skip) return;
  app = await openApp({
    engine: ["sql_diagram_export"],
    mocks: {
      list_tables: () => ({ dbo: ["orders", "customers", "notes"] }),
      table_columns: ({ table }) => ({ "dbo.orders": [{ name: "order_id", type: "int" }, { name: "customer_id", type: "int" }],
        "dbo.customers": [{ name: "id", type: "int" }, { name: "name", type: "varchar" }], "dbo.notes": [{ name: "text", type: "varchar" }] }[table.includes(".") ? table : `dbo.${table}`] || []),
      ...sqlRunMocks(({ query }) => {
        const cols = (n) => Array.from({ length: n }, (_, i) => ({ name: `c${i}`, type: "varchar" }));
        if (query.includes("sys.foreign_key_columns")) {
          if (keysFail) throw new Error("The SELECT permission was denied on the object 'foreign_key_columns'.");
          return { columns: cols(7), rows: KEYS };
        }
        if (query.includes("FROM sys.columns")) return { columns: cols(8), rows: COLUMNS };
        return { columns: cols(3), rows: OBJECTS };
      }),
    },
  });
});

after(async () => { await app?.close(); });

test("a warehouse's diagram draws its keys solid and a guess dashed, and a click opens the table", { skip }, async () => {
  await page().evaluate((wh) => { sqlWarehouseOk.add(sqlRefKey(wh)); openWarehouseQuery(wh); }, WH);
  await page().getByRole("button", { name: "Diagram" }).click();
  const dialog = page().getByRole("dialog", { name: "Diagram: SalesWH" });
  await dialog.getByText("4 tables · 2 from keys · 1 guessed from names").waitFor();
  assert.strictEqual(await dialog.locator(".sql-diagram path:not([stroke-dasharray])").count(), 2);
  assert.strictEqual(await dialog.locator(".sql-diagram path[stroke-dasharray]").count(), 1);
  assert.match(await dialog.locator(".sql-diagram path[stroke-dasharray] title").textContent(), /gold\.fact_sales\.store_id → gold\.dim_store\.store_id \(guessed/);
  await dialog.getByLabel("Schema").selectOption("dbo");
  await dialog.getByText("1 table · 0 from keys · 0 guessed from names").waitFor();
  await dialog.getByLabel("Schema").selectOption("gold");
  await dialog.getByText("4 tables", { exact: false }).waitFor();
  if (axeSource) {
    // axe covers dialogs: sqldiagram.js:openSqlDiagram
    assert.deepStrictEqual(await axeFindings(page(), ".dialog"), []);
  }
  await dialog.getByRole("button", { name: "dim_store: open it in the Explorer" }).click();
  await page().waitForFunction(() => document.activeElement?.dataset.tid === "o:gold.dim_store");
});

test("Export as SVG writes a new file in Downloads that matches the drawing", { skip }, async () => {
  await page().getByRole("button", { name: "Diagram" }).click();
  const dialog = page().getByRole("dialog", { name: "Diagram: SalesWH" });
  await dialog.getByText("2 from keys", { exact: false }).waitFor();
  await dialog.getByRole("button", { name: "Export as SVG" }).click();
  const toast = await page().locator("#toast").filter({ hasText: /Saved to .*\.svg/ }).textContent();
  const saved = fs.readFileSync(toast.match(/Saved to (.*\.svg)/)[1], "utf8");
  assert.ok(saved.startsWith("<svg ") && saved.includes("fact_sales") && saved.includes('stroke-dasharray="5 4"'));
  await page().keyboard.press("Escape");
  await dialog.waitFor({ state: "detached" });
});

test("when the keys can't be read, every line is a guess and it says why", { skip }, async () => {
  keysFail = true;
  await page().evaluate(() => go("home"));
  await page().evaluate((wh) => { sqlWarehouseOk.add(sqlRefKey(wh)); openWarehouseQuery(wh); }, { ...WH, item_id: "wh2" });
  await page().getByRole("button", { name: "Diagram" }).click();
  const dialog = page().getByRole("dialog", { name: "Diagram: SalesWH" });
  await dialog.getByText(/Couldn't read the warehouse's keys, so every line is guessed from names/).waitFor();
  await dialog.getByText("4 tables · 0 from keys · 3 guessed from names").waitFor();
  await page().keyboard.press("Escape");
  await dialog.waitFor({ state: "detached" });
  keysFail = false;
});

test("a lakehouse's diagram guesses from names and says a lakehouse has no keys", { skip }, async () => {
  await page().evaluate((lh) => openSqlQuery(lh), LH);
  await page().getByRole("button", { name: "Diagram" }).click();
  const dialog = page().getByRole("dialog", { name: "Diagram: sales_lh" });
  await dialog.getByText("3 tables · 1 guessed from names").waitFor();
  await dialog.getByText("Guessed from column names (a lakehouse has no keys).", { exact: false }).waitFor();
  assert.strictEqual(await dialog.getByText("From the warehouse's keys").count(), 0);
  await page().keyboard.press("Escape");
  await dialog.waitFor({ state: "detached" });
});
