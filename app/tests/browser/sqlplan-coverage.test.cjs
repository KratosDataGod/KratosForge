// Plans on the SQL pages (sqlplan.js) beyond warehouse-plan.test.cjs and sql-editor.test.cjs: the
// Warehouse page's Plan view before a plan is asked for, a showplan statement with no operators, and one
// with no statements; the lakehouse SQL page's DuckDB plan when it can't be worked out, when it has no
// steps, and when it has more steps than it lists. sql_start, sql_status and sql_page are mocked.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp, sqlRunMocks } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
let app;
const page = () => app.page;

const WH = { kind: "warehouse", workspace_id: "ws1", workspace_name: "Sales Dev", item_id: "wh1", name: "WH_Gold" };
const LH = { workspace_id: "ws1", lakehouse_id: "lh1", name: "Sales" };
const NS = "http://schemas.microsoft.com/sqlserver/2004/07/showplan";
const showplan = (statements) => `<?xml version="1.0" encoding="utf-16"?><ShowPlanXML xmlns="${NS}" Version="1.564"><BatchSequence><Batch><Statements>${statements}</Statements></Batch></BatchSequence></ShowPlanXML>`;
// A SET statement: Fabric lists it with a cost but no QueryPlan.
const NO_OPERATORS = showplan('<StmtSimple StatementText="SET NOCOUNT ON" StatementEstRows="1" StatementSubTreeCost="0"/>');
const NO_STATEMENTS = showplan("");

let whPlan = NO_OPERATORS;
let localPlan = null;  // DuckDB's plan as the engine answers it, or an Error to fail it

before(async () => {
  if (skip) return;
  const runs = sqlRunMocks((p) => {
    if (p.target === "warehouse") return { columns: [{ name: "Microsoft SQL Server 2005 XML Showplan", type: "string" }], rows: [[whPlan]] };
    if (localPlan instanceof Error) throw localPlan;
    return { columns: [{ name: "plan", type: "string" }], rows: [[JSON.stringify(localPlan)]] };
  });
  app = await openApp({
    engine: ["sql_format", "sql_analyze", "sql_queries_list"],
    mocks: { ...runs,
      sql_start: (p) => ({ ...runs.sql_start(p), target: p.target === "warehouse" ? "warehouse" : "lakehouse" }),
      list_tables: () => ({ dbo: ["sales"] }), table_columns: () => [] },
  });
});
after(async () => { await app?.close(); });

const setDoc = (text) => page().evaluate((text) => {
  const v = sqlPage.editor.view;
  v.dispatch({ changes: { from: 0, to: v.state.doc.length, insert: text } });
}, text);

test("the Warehouse page's Plan view says what it shows before one is asked for, and says when Fabric's has nothing to draw", { skip }, async () => {
  await page().evaluate((wh) => openWarehouseQuery(wh), WH);
  await page().waitForSelector(".warehouse-page .sql-edit .cm-editor");
  await setDoc("SET NOCOUNT ON;");
  await page().getByRole("tab", { name: "Plan" }).click();
  await page().getByText("Estimated plan shows how Fabric would run the query, without running it. It uses a little capacity.").waitFor();

  await page().getByRole("button", { name: "Estimated plan" }).click();
  await page().getByRole("dialog").getByRole("button", { name: "Run in Fabric" }).click();  // it uses a little capacity
  await page().locator(".sql-plan-stmt").getByText("This statement has no operators to show.").waitFor();
  assert.strictEqual(await page().locator(".sql-plan-stmt-head").innerText(), "Statement 1 · 1 rows · cost 0");

  whPlan = NO_STATEMENTS;
  await page().getByRole("button", { name: "Estimated plan" }).click();
  await page().locator(".sql-plan").getByText("Fabric returned a plan with no statements.").waitFor();
  assert.strictEqual(await page().locator(".sql-plan-stmt").count(), 0);
});

test("the lakehouse SQL page's plan says why it couldn't be worked out, when it has no steps, and how many it left out", { skip }, async () => {
  await page().evaluate((lh) => openSqlQuery(lh), LH);
  await page().waitForSelector(".sql-edit .cm-editor");
  await setDoc("SELECT region FROM sales");
  await page().getByRole("tab", { name: /^Plan/ }).click();
  const view = page().locator(".sql-plan");

  localPlan = new Error("Catalog Error: Table with name salez does not exist!");
  await view.getByRole("button", { name: "Show the plan" }).click();
  await view.locator(".output.error").getByText("Catalog Error: Table with name salez does not exist!").waitFor();

  localPlan = { steps: [], more_steps: 0 };
  await view.getByRole("button", { name: "Show the plan" }).click();
  await view.getByText("DuckDB returned a plan with no steps.").waitFor();

  localPlan = { steps: [{ depth: 0, name: "PROJECTION", rows: null, details: { Projections: "region" } },
    { depth: 1, name: "SEQ_SCAN", rows: 4200, details: { Table: "sales" } }], more_steps: 7 };
  await view.getByRole("button", { name: "Show the plan again" }).click();
  await view.getByText("7 more steps aren't listed.").waitFor();
  assert.strictEqual(await view.getByRole("listitem").count(), 2);
  assert.strictEqual(await view.getByRole("listitem").first().locator(".sql-plan-rows").innerText(), "", "no estimate, no rows shown");
  assert.match(await view.getByRole("listitem").nth(1).innerText(), /4,200 rows/);
});
