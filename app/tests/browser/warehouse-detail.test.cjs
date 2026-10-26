// WH-16: Table detail on the Warehouse page, in a real browser (see harness.cjs for how to run it).
// Runs are mocked; the engine's check of the profile, past-time and clone scripts is tested in
// test_warehouse_queries.py.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { playwright, openApp, sqlRunMocks } = require("./harness.cjs");

const WH = { kind: "warehouse", workspace_id: "ws1", workspace_name: "Sales Dev", item_id: "wh1", name: "WH_Gold" };
const SHOTS = process.env.SCREENSHOT_DIR;
const CATALOG_COLS = ["schema_name", "object_name", "column_name", "type_name", "max_length", "precision", "scale", "is_nullable"].map((name) => ({ name, type: "varchar" }));

let app;
const skip = !playwright && "Playwright is not installed";
const page = () => app.page;
const starts = () => app.calls.filter((c) => c.method === "sql_start").map((c) => c.params);
const tabText = () => page().evaluate(() => sqlPage.tabs[sqlPage.active].sql);

before(async () => {
  if (skip) return;
  const runs = sqlRunMocks((p) => {
    if (p.query.includes("sys.columns")) return { columns: CATALOG_COLS, rows: [["dbo", "Sales", "Amount", "decimal", 9, 18, 2, false],
      ["dbo", "Sales", "Customer Name", "nvarchar", 200, 0, 0, true], ["dbo", "Sales", "Photo", "varbinary", -1, 0, 0, true]] };
    if (p.query.includes("sys.objects")) return { columns: CATALOG_COLS.slice(0, 3), rows: [["dbo", "Sales", "U"]] };
    if (p.query.includes("COUNT_BIG(*) AS n")) {
      const names = ["n", "c0_null", "c0_distinct", "c0_min", "c0_max", "c1_null", "c1_distinct", "c1_min", "c1_max", "c2_null", "c2_distinct", "c2_min", "c2_max"];
      return { columns: names.map((name) => ({ name, type: "varchar" })),
        rows: [[2000, 0, 1500, "0.50", "980.00", 500, 120, "<b>Adatum</b>", "Wingtip", 2000, null, null, null]] };
    }
    return { columns: [{ name: "x", type: "int" }], rows: [[1]] };
  });
  app = await openApp({
    engine: ["sql_format", "sql_dialects", "sql_analyze"],
    mocks: { ...runs, sql_start: (p) => ({ ...runs.sql_start(p), target: "warehouse" }) },
  });
  await page().evaluate((wh) => openWarehouseQuery(wh), WH);
  await page().waitForSelector(".warehouse-page .sql-edit .cm-editor");
  await page().getByRole("button", { name: "List objects" }).click();
  await page().getByRole("dialog").getByRole("button", { name: "Run in Fabric" }).click();
  await page().getByRole("button", { name: "Script Sales" }).waitFor();
  app.errors.length = 0;
});

after(async () => { await app?.close(); });

async function openDetail() {
  await page().getByRole("button", { name: "Script Sales" }).click();
  await page().getByRole("menuitem", { name: "Table detail" }).click();
  const dialog = page().getByRole("dialog", { name: "dbo.Sales" });
  await dialog.waitFor();
  return dialog;
}

test("Table detail lists the columns and profiles them with one query that reads", { skip }, async () => {
  const before = starts().length;
  const dialog = await openDetail();
  assert.match(await dialog.textContent(), /3 columns in WH_Gold\. Profile counts every row in Fabric/);
  assert.strictEqual(starts().length, before, "opening it runs nothing");
  await dialog.getByRole("button", { name: "Profile" }).click();
  await dialog.getByText(/2,000 rows, counted in Fabric/).waitFor();
  const q = starts().at(-1);
  assert.ok(!q.allow && !q.confirm, "a read, not a change");
  assert.match(q.query, /APPROX_COUNT_DISTINCT\(\[Amount\]\) AS c0_distinct/);
  assert.match(q.query, /SUM\(CASE WHEN \[Customer Name\] IS NULL THEN 1 ELSE 0 END\) AS c1_null/);
  assert.match(q.query, /NULL AS c2_distinct,\n  NULL AS c2_min/, "varbinary(max) gets neither distinct nor smallest and largest");
  const cells = await dialog.locator("tbody tr").evaluateAll((trs) => trs.map((tr) => [...tr.children].map((td) => td.textContent)));
  assert.deepStrictEqual(cells, [
    ["Amount", "decimal(18,2) NOT NULL", "0%", "1,500", "0.50 … 980.00"],
    ["Customer Name", "nvarchar(100)", "25%", "120", "<b>Adatum</b> … Wingtip"],
    ["Photo", "varbinary(max)", "100%", "–", "–"]]);
  assert.strictEqual(await dialog.locator("tbody b").count(), 0, "values are text");
  if (SHOTS) {
    await page().waitForTimeout(400);
    await page().screenshot({ path: path.join(SHOTS, "wh-16-detail.png") });
  }
  await dialog.getByRole("button", { name: "Close" }).last().click();
});

test("See it as it was opens a query tab that reads at a past UTC time", { skip }, async () => {
  const before = starts().length;
  const dialog = await openDetail();
  await dialog.getByRole("button", { name: "Open as it was" }).click();
  await page().getByText("Pick a past time first.").waitFor();
  await dialog.getByLabel("Past time (your time)").fill("2026-10-20T10:00:05");
  await dialog.getByRole("button", { name: "Open as it was" }).click();
  await page().waitForFunction(() => sqlPage.tabs[sqlPage.active].name === "Sales (as of)");
  const utc = await page().evaluate(() => new Date("2026-10-20T10:00:05").toISOString().replace("Z", ""));
  assert.strictEqual(await tabText(), `-- dbo.Sales as it was at ${utc} UTC. It reads only.\nSELECT TOP (100) *\nFROM [dbo].[Sales]\nOPTION (FOR TIMESTAMP AS OF '${utc}');\n`);
  assert.strictEqual(starts().length, before, "it opens, it doesn't run");
});

test("Clone writes a script into a tab and never runs it", { skip }, async () => {
  const before = starts().length;
  const dialog = await openDetail();
  await dialog.getByLabel("Clone's name").fill("Sales_before_load");
  await dialog.getByRole("button", { name: "Script the clone" }).click();
  await page().waitForFunction(() => sqlPage.tabs[sqlPage.active].name === "Sales_before_load (CLONE)");
  assert.match(await tabText(), /Tick Allow changes, then Run[\s\S]*\nCREATE TABLE \[dbo\]\.\[Sales_before_load\] AS CLONE OF \[dbo\]\.\[Sales\];\n$/);
  assert.strictEqual(starts().length, before);
});

test("nothing on the page threw", { skip }, async () => {
  assert.deepStrictEqual(app.errors, []);
});
