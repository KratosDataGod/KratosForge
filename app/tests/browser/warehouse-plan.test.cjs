// WH-09: the estimated plan on the Warehouse page, in a real browser (see harness.cjs for how to run it).
// Runs are mocked: the engine's side (SHOWPLAN_XML around the query) is tested in test_warehouse_queries.py.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { playwright, openApp, sqlRunMocks } = require("./harness.cjs");

const WH = { kind: "warehouse", workspace_id: "ws1", workspace_name: "Sales Dev", item_id: "wh1", name: "WH_Gold" };
const SHOTS = process.env.SCREENSHOT_DIR;
const NS = "http://schemas.microsoft.com/sqlserver/2004/07/showplan";
const scan = (table, rows, cost) => `<RelOp PhysicalOp="Table Scan" LogicalOp="Table Scan" EstimateRows="${rows}" EstimatedTotalSubtreeCost="${cost}">
  <TableScan><Object Database="[WH_Gold]" Schema="[dbo]" Table="[${table}]"/></TableScan></RelOp>`;
const PLAN = `<?xml version="1.0" encoding="utf-16"?><ShowPlanXML xmlns="${NS}" Version="1.564"><BatchSequence><Batch><Statements>
<StmtSimple StatementText="SELECT c.Segment, SUM(f.Amount) FROM dbo.FactSales f JOIN dbo.DimCustomer c ON ..." StatementEstRows="4" StatementSubTreeCost="10">
<QueryPlan><RelOp PhysicalOp="Hash Match" LogicalOp="Aggregate" EstimateRows="4" EstimatedTotalSubtreeCost="10"><Hash>
<RelOp PhysicalOp="Hash Match" LogicalOp="Inner Join" EstimateRows="1200000" EstimatedTotalSubtreeCost="9"><Warnings NoJoinPredicate="false"/><Hash>
${scan("FactSales", 1200000, 6.5)}${scan("DimCustomer", 18484, 0.5)}
</Hash></RelOp></Hash></RelOp></QueryPlan></StmtSimple></Statements></Batch></BatchSequence></ShowPlanXML>`;

let app;
let answer = PLAN;
const skip = !playwright && "Playwright is not installed";
const page = () => app.page;
const starts = () => app.calls.filter((c) => c.method === "sql_start").map((c) => c.params);

before(async () => {
  if (skip) return;
  const runs = sqlRunMocks((p) => (p.estimated_plan
    ? { columns: [{ name: "Microsoft SQL Server 2005 XML Showplan", type: "string" }], rows: [[answer]] }
    : { columns: [{ name: "x", type: "int" }], rows: [[1]] }));
  app = await openApp({
    engine: ["sql_format", "sql_dialects", "sql_analyze", "sql_queries_list"],
    mocks: { ...runs, sql_start: (p) => ({ ...runs.sql_start(p), target: "warehouse" }),
      sql_status: (p) => ({ ...runs.sql_status(p), target: "warehouse" }) },
  });
  await page().evaluate((wh) => openWarehouseQuery(wh), WH);
  await page().waitForSelector(".warehouse-page .sql-edit .cm-editor");
  await page().evaluate(() => {
    const v = sqlPage.editor.view;
    const text = "SELECT c.Segment, SUM(f.Amount) AS Revenue\nFROM dbo.FactSales f JOIN dbo.DimCustomer c ON c.CustomerKey = f.CustomerKey\nGROUP BY c.Segment;";
    v.dispatch({ changes: { from: 0, to: v.state.doc.length, insert: text } });
  });
  app.errors.length = 0;
});

after(async () => { await app?.close(); });

test("Estimated plan asks Fabric without running the query and draws the operators", { skip }, async () => {
  await page().getByRole("button", { name: "Estimated plan" }).click();
  await page().getByRole("dialog").getByRole("button", { name: "Run in Fabric" }).click();  // it uses a little capacity
  const tree = page().getByRole("tree", { name: "Plan for statement 1" });
  await tree.waitFor();
  const sent = starts().at(-1);
  assert.ok(sent.estimated_plan && !sent.allow && !sent.confirm, "a read, asked for its plan");
  const ops = await tree.getByRole("treeitem").allTextContents();
  assert.strictEqual(ops.length, 4);
  assert.match(ops[0], /^Hash Match \(Aggregate\).*4 rows10%$/);
  assert.match(ops[2], /^Table Scan.*dbo\.FactSales.*1,200,000 rows65%$/);
  assert.match(ops[1], /warning/);
  assert.match(await page().locator(".sql-plan-op.heavy").textContent(), /FactSales/, "the heaviest operator stands out");
  assert.match(await page().locator(".sql-plan-stmt-head").textContent(), /Statement 1 · 4 rows · cost 10/);
  assert.strictEqual(await page().getByRole("tab", { name: "Plan" }).getAttribute("aria-selected"), "true");
  if (SHOTS) {
    await page().waitForFunction(() => !document.querySelector(".dialog"));
    await page().waitForTimeout(600);
    await page().screenshot({ path: path.join(SHOTS, "wh-09-plan.png") });
  }
  await page().getByRole("tab", { name: "Results" }).click();
  assert.match(await page().locator(".warehouse-page").textContent(), /Press Run to see results|Write T-SQL/, "the results weren't touched");
});

test("an answer that isn't a showplan is shown as it came", { skip }, async () => {
  answer = "<something/>";
  await page().getByRole("button", { name: "Estimated plan" }).click();
  await page().waitForFunction(() => /isn't a showplan/.test(document.querySelector(".sql-plan")?.textContent || ""));
  assert.match(await page().locator(".sql-plan-xml").textContent(), /<something\/>/);
  answer = PLAN;
});

test("names in the plan are text, never markup", { skip }, async () => {
  const evil = "&lt;img src=x onerror=&quot;window.planPwned=1&quot;&gt;";
  answer = PLAN.replace('PhysicalOp="Table Scan" LogicalOp="Table Scan" EstimateRows="18484"', `PhysicalOp="${evil}" LogicalOp="${evil}" EstimateRows="18484"`)
    .replace('Table="[DimCustomer]"', `Table="${evil}"`);
  await page().getByRole("button", { name: "Estimated plan" }).click();
  await page().waitForFunction(() => /<img src=x/.test(document.querySelector(".sql-plan-tree")?.textContent || ""));
  await page().waitForTimeout(200);
  assert.strictEqual(await page().locator(".sql-plan img").count(), 0, "no element was made from a name");
  assert.strictEqual(await page().evaluate(() => window.planPwned), undefined);
  assert.match(await page().locator(".sql-plan-op").last().locator(".sql-plan-name").textContent(), /^<img src=x onerror="window\.planPwned=1">/);
  assert.match(await page().locator(".sql-plan-op").last().locator(".sql-plan-object").textContent(), /<img src=x/);
  answer = PLAN;
});

test("nothing on the page threw", { skip }, async () => {
  assert.deepStrictEqual(app.errors, []);
});
