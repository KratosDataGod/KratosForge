// #1375: three views of a result, in a real browser (see harness.cjs for how to run it).
// Notebook Clean view (wrangler.js, over notebook_frame_wrangle): adding, editing and removing steps
// sends exactly those steps and the displayed variable's name, the preview shows the engine's rows, a
// result the kernel no longer holds says to run the cell again, and Add code to notebook puts the code
// in a new cell below without running it. Notebook Query profile (qprofile.js, over
// notebook_frame_query_profile): nothing is sent until it is asked for, the capacity note shows, and the
// steps show rows, time and share; an engine error shows a plain message. Warehouse Plan view
// (sqlplan.js, sql_start estimated_plan): showplan XML draws the statement and its operators with
// estimated rows and cost, a warning shows, Copy XML gives the XML as it came, and XML that isn't a
// showplan shows a plain message. Opening the views sends no write, and axe finds nothing serious on each.
// The engine is mocked for the frames and Fabric; shapes follow the engine source cited by each mock.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp, sqlRunMocks, axeSource, axeFindings, settleForAxe } = require("./harness.cjs");

let app;
const skip = !playwright && "Playwright is not installed";
const page = () => app.page;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const sent = (method) => app.calls.filter((c) => c.method === method).map((c) => c.params);
const READS = ["notebook_frame_wrangle", "notebook_frame_query_profile", "sql_start", "sql_page"];
// Running a cell is the only way these views could change anything; a warehouse run that isn't an
// estimated plan is checked on its own. (Add code to notebook edits the notebook, so it may save it.)
const WRITES = ["start_cell", "notebook_frame_download"];

// ---- the notebook: a spark result shown with display(sales), and a cell under it

const CELLS = [
  { type: "code", cell_id: "c1", meta: {}, source: "sales = spark.sql(\"SELECT region, amount FROM dbo.sales\")\ndisplay(sales)" },
  { type: "code", cell_id: "c2", meta: {}, source: "print('after')" },
];
const COLUMNS = ["region", "amount"];
const TYPES = ["string", "integer"];
// The whole result the kernel holds (frames.py); the cell's own output shows only the first three.
const ALL = [["East", 120], ["West", 340], ["East", 95], ["West", 410], ["North", 60], ["West", 275]];
// notebook.py display() + Kernel._show(): a table output with its frame id, and spark for a query to profile.
const output = () => ({ type: "table", columns: COLUMNS, types: TYPES, rows: ALL.slice(0, 3), total_rows: ALL.length, frame: "f1", spark: true });

// wrangler.py OPS and describe_step: the step's line in the Steps list.
const OPS = { filter: "Filter rows", rename: "Rename column", sort: "Sort rows" };
const describe = (s) => { const cols = s.columns || (s.column ? [s.column] : []); return cols.length ? `${OPS[s.op]}: ${cols.map((c) => `'${c}'`).join(", ")}` : OPS[s.op]; };

let gone = false;
let profileError = null;
let wrangleError = null;
// frames.py Frames.wrangle -> wrangler.run: the code, the preview rows, rows before and after, the steps' lines.
function wrangle({ frame, steps, name }) {
  if (wrangleError) throw new Error(wrangleError);
  if (gone) return { frame, gone: true, message: "This result is no longer in the kernel." };  // service.py notebook_frame_wrangle
  let cols = [...COLUMNS];
  let rows = ALL.map((r) => [...r]);
  const changed = [];
  for (const s of steps) {
    const i = cols.indexOf(s.column);
    if (s.op === "filter" && s.test === "eq") rows = rows.filter((r) => String(r[i]) === s.value);
    if (s.op === "rename") { cols[i] = s.to; changed.push(s.to); }
    if (s.op === "sort") rows.sort((a, b) => (a[i] < b[i] ? 1 : -1) * (s.desc ? 1 : -1));
  }
  const body = steps.map((s) => `    # ${describe(s)}\n    df = df.step(${JSON.stringify(s)})`).join("\n") || "    pass";
  return { frame, code: `import polars as pl\n\n\ndef clean_data(df):\n${body}\n    return df\n\n\n${name}_clean = clean_data(${name})\ndisplay(${name}_clean)\n`,
    columns: cols, types: TYPES, rows, rows_before: ALL.length, rows_after: rows.length, changed, sampled: false, error: null,
    steps: steps.map(describe) };
}

// spark_local.query_profile -> _read_profile: totals and one entry per DuckDB step.
const PROFILE = { seconds: 0.0425, rows: 2, rows_scanned: 6, peak_memory: 262144, more_steps: 0, steps: [
  { depth: 0, name: "HASH_GROUP_BY", rows: 2, seconds: 0.0125, share: 0.3, details: { Groups: "#0" } },
  { depth: 1, name: "FILTER", rows: 3, seconds: 0.005, share: 0.12, details: { Filters: "region='West'" } },
  { depth: 2, name: "ARROW_SCAN", rows: 6, seconds: 0.025, share: 0.58, details: { Function: "ARROW_SCAN" } },
] };
// spark_local.query_profile raises this for a result that has no query behind it.
const NO_QUERY = "ValueError: A query profile is for a result of spark or %%sql. Show one as the cell's result, then ask for its profile.";

// ---- the warehouse: Fabric's showplan XML (Microsoft's showplan schema, showplanxml.xsd)

const WH = { kind: "warehouse", workspace_id: "ws1", workspace_name: "Sales Dev", item_id: "wh1", name: "WH_Gold" };
const NS = "http://schemas.microsoft.com/sqlserver/2004/07/showplan";
const PLAN = `<?xml version="1.0" encoding="utf-16"?>
<ShowPlanXML xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:xsd="http://www.w3.org/2001/XMLSchema" Version="1.564" Build="12.0.2000.8" xmlns="${NS}">
  <BatchSequence>
    <Batch>
      <Statements>
        <StmtSimple StatementText="SELECT TOP 10 c.Segment, SUM(f.Amount) AS Revenue FROM dbo.FactSales f JOIN dbo.DimCustomer c ON c.CustomerKey = f.CustomerKey GROUP BY c.Segment ORDER BY Revenue DESC" StatementId="1" StatementCompId="1" StatementType="SELECT" StatementSubTreeCost="20" StatementEstRows="10" StatementOptmLevel="FULL">
          <QueryPlan DegreeOfParallelism="1" CachedPlanSize="48" CompileTime="3" CompileCPU="3" CompileMemory="512">
            <RelOp NodeId="0" PhysicalOp="Sort" LogicalOp="TopN Sort" EstimateRows="10" EstimateIO="0.01" EstimateCPU="0.1" AvgRowSize="31" EstimatedTotalSubtreeCost="20" Parallel="0" EstimateRebinds="0" EstimateRewinds="0">
              <OutputList><ColumnReference Column="Segment"/><ColumnReference Column="Revenue"/></OutputList>
              <TopSort Distinct="0" Rows="10">
                <RelOp NodeId="1" PhysicalOp="Hash Match" LogicalOp="Inner Join" EstimateRows="250000" EstimateIO="0" EstimateCPU="3" AvgRowSize="23" EstimatedTotalSubtreeCost="18" Parallel="0" EstimateRebinds="0" EstimateRewinds="0">
                  <Warnings><PlanAffectingConvert ConvertIssue="Cardinality Estimate" Expression="CONVERT(int,[f].[CustomerKey],0)"/></Warnings>
                  <Hash>
                    <RelOp NodeId="2" PhysicalOp="Table Scan" LogicalOp="Table Scan" EstimateRows="18484" EstimateIO="0.4" EstimateCPU="0.1" AvgRowSize="15" EstimatedTotalSubtreeCost="1" Parallel="0" EstimateRebinds="0" EstimateRewinds="0">
                      <TableScan Ordered="0" ForcedIndex="0" NoExpandHint="0"><Object Database="[WH_Gold]" Schema="[dbo]" Table="[DimCustomer]" Alias="[c]"/></TableScan>
                    </RelOp>
                    <RelOp NodeId="3" PhysicalOp="Table Scan" LogicalOp="Table Scan" EstimateRows="250000" EstimateIO="10" EstimateCPU="2" AvgRowSize="12" EstimatedTotalSubtreeCost="12" Parallel="0" EstimateRebinds="0" EstimateRewinds="0">
                      <TableScan Ordered="0" ForcedIndex="0" NoExpandHint="0"><Object Database="[WH_Gold]" Schema="[dbo]" Table="[FactSales]" Alias="[f]"/></TableScan>
                    </RelOp>
                  </Hash>
                </RelOp>
              </TopSort>
            </RelOp>
          </QueryPlan>
        </StmtSimple>
      </Statements>
    </Batch>
  </BatchSequence>
</ShowPlanXML>`;
let planAnswer = PLAN;

const result = () => page().locator(".table-output").first();

// A fresh result under the first cell, as the kernel answers a run (no state left from another test).
async function showResult(view) {
  await page().evaluate((out) => {
    const c = state.notebook.cells[0];
    c.outputs = [out]; c.status = "ok"; c.count = 1;
    renderNotebook();
  }, output());
  await result().waitFor();
  if (view) await result().getByRole("button", { name: view, exact: true }).click();
}

before(async () => {
  if (skip) return;
  const runs = sqlRunMocks((p) => (p.estimated_plan
    ? { columns: [{ name: "Microsoft SQL Server 2005 XML Showplan", type: "string" }], rows: [[planAnswer]] }
    : { columns: [{ name: "x", type: "int" }], rows: [[1]] }));
  app = await openApp({
    engine: ["sql_format", "sql_analyze", "sql_queries_list"],
    mocks: {
      ...runs,
      sql_start: (p) => ({ ...runs.sql_start(p), target: "warehouse" }),
      sql_status: (p) => ({ ...runs.sql_status(p), target: "warehouse" }),
      // frames.py Frames.page: one page of the whole result.
      notebook_frame_page: ({ frame, offset = 0, limit = 100 }) => ({ frame, columns: COLUMNS, types: TYPES,
        rows: ALL.slice(offset, offset + limit), offset, total: ALL.length, all_rows: ALL.length }),
      notebook_frame_wrangle: wrangle,
      notebook_frame_query_profile: ({ frame }) => { if (profileError) throw new Error(profileError); return { frame, ...PROFILE }; },
      notebook_dependencies: () => ({ cells: [] }),
      save_notebook: () => ({ saved: true }),
      kernel_variables: () => ({ variables: [] }),
    },
  });
  await page().evaluate((cells) => { loadIntoState({ name: "Output views", cells }); go("notebook"); }, CELLS);
  // Copy writes here, so the test reads exactly what reached the clipboard.
  await page().evaluate(() => {
    window.__copied = [];
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText: async (t) => { window.__copied.push(t); } } });
  });
  app.errors.length = 0;
});

after(async () => { await app?.close(); });

// ---- Clean

test("Clean sends exactly the steps shown and the displayed variable's name, and previews the engine's rows", { skip }, async () => {
  const from = app.calls.length;
  await showResult("Clean");
  await result().getByText("6 rows before, 6 rows after.").waitFor();
  const wr = () => app.calls.slice(from).filter((c) => c.method === "notebook_frame_wrangle").map((c) => c.params);
  assert.deepStrictEqual(wr(), [{ frame: "f1", steps: [], name: "sales" }], "opening asks for no steps, on the variable display() showed");

  // Add: Filter rows where region equals West.
  await result().getByRole("combobox", { name: "Add a step" }).selectOption("filter");
  const form = result().locator(".wrangle-add");
  await form.getByRole("combobox", { name: "Column" }).selectOption("region");
  await form.getByRole("combobox", { name: "Keep rows where it" }).selectOption("eq");
  await form.getByRole("textbox", { name: "Value" }).fill("West");
  await form.getByRole("button", { name: "Add step" }).click();
  await result().getByText("6 rows before, 3 rows after.").waitFor();
  const FILTER = { op: "filter", column: "region", test: "eq", value: "West" };
  assert.deepStrictEqual(wr().at(-1), { frame: "f1", steps: [FILTER], name: "sales" });
  // The preview is the engine's answer: row 410 was never in the cell's own output.
  assert.deepStrictEqual(await result().locator(".wrangle-preview tbody tr").allInnerTexts().then((r) => r.map((t) => t.split(/\s+/).join(" "))),
    ["West 340", "West 410", "West 275"]);

  // Add: Rename amount to revenue.
  await result().getByRole("combobox", { name: "Add a step" }).selectOption("rename");
  await form.getByRole("combobox", { name: "Column" }).selectOption("amount");
  await form.getByRole("textbox", { name: "New name" }).fill("revenue");
  await form.getByRole("button", { name: "Add step" }).click();
  await result().getByText("Changed columns are marked.").waitFor();
  const RENAME = { op: "rename", column: "amount", to: "revenue" };
  assert.deepStrictEqual(wr().at(-1), { frame: "f1", steps: [FILTER, RENAME], name: "sales" });
  const shown = () => result().locator(".wrangle-step > span:first-child").allInnerTexts();
  assert.deepStrictEqual(await shown(), ["Filter rows: 'region'", "Rename column: 'amount'"]);
  assert.match(await result().locator(".wrangle-preview thead").innerText(), /revenue/);

  // Edit: the data frame the code reads.
  const n = wr().length;
  await result().getByRole("textbox", { name: "Data frame variable" }).fill("west_sales");
  await result().getByRole("textbox", { name: "Data frame variable" }).press("Enter");
  await result().locator(".wrangle-code").getByText(/west_sales_clean = clean_data\(west_sales\)/).waitFor();
  assert.deepStrictEqual(wr().slice(n), [{ frame: "f1", steps: [FILTER, RENAME], name: "west_sales" }], "one request, same steps, the new name");

  // Remove the first step: only the rename is left, and only it is sent.
  await result().getByRole("button", { name: "Remove step 1" }).click();
  await result().getByText("6 rows before, 6 rows after.").waitFor();
  assert.deepStrictEqual(wr().at(-1), { frame: "f1", steps: [RENAME], name: "west_sales" });
  assert.deepStrictEqual(await shown(), ["Rename column: 'amount'"]);
  for (const params of wr()) assert.strictEqual(params.frame, "f1");
  assert.deepStrictEqual(sent("start_cell"), [], "the notebook didn't run anything");
});

test("Add code to notebook puts the code in a new cell below the result's cell and doesn't run it", { skip }, async () => {
  const from = app.calls.length;
  await showResult("Clean");
  await result().getByRole("combobox", { name: "Add a step" }).selectOption("sort");
  await result().locator(".wrangle-add").getByRole("combobox", { name: "Column" }).selectOption("amount");
  await result().locator(".wrangle-add").getByRole("combobox", { name: "Order" }).selectOption("1");
  await result().locator(".wrangle-add").getByRole("button", { name: "Add step" }).click();
  await result().locator(".wrangle-step").getByText("Sort rows: 'amount'").waitFor();
  const code = await result().locator(".wrangle-code").innerText();
  await result().getByRole("button", { name: "Add code to notebook" }).click();
  const cells = await page().evaluate(() => state.notebook.cells.map((c) => ({ source: c.source, outputs: (c.outputs || []).length, status: c.status || null })));
  assert.strictEqual(cells.length, 3);
  assert.strictEqual(cells[0].source, CELLS[0].source);
  assert.strictEqual(cells[1].source, code, "the generated code, as previewed, right under the result's cell");
  assert.match(cells[1].source, /sales_clean = clean_data\(sales\)\ndisplay\(sales_clean\)\n$/);
  assert.strictEqual(cells[2].source, CELLS[1].source);
  assert.strictEqual(cells[1].outputs, 0);
  await sleep(300);
  assert.deepStrictEqual(app.calls.slice(from).filter((c) => c.method === "start_cell"), [], "nothing ran");
  assert.match(await page().evaluate(() => document.getElementById("toast").textContent), /Run it to make the cleaned data frame/);
});

test("a result the kernel no longer holds says to run the cell again", { skip }, async () => {
  gone = true;
  try {
    await showResult("Clean");
    await result().getByText("Run the cell again to clean this result: the kernel no longer holds it.").waitFor();
    assert.strictEqual(await result().locator(".wrangle-preview table").count(), 0);
  } finally { gone = false; }
});

test("a preview the engine refuses shows its plain message", { skip }, async () => {
  wrangleError = "ValueError: 'sales!' isn't a Python variable name.";  // wrangler.valid_name
  try {
    await showResult("Clean");
    await result().locator(".wrangle-preview").getByText(/^Could not preview: /).waitFor();
    assert.strictEqual(await result().locator(".wrangle-preview").innerText(), "Could not preview: 'sales!' isn't a Python variable name.");
  } finally { wrangleError = null; }
});

// ---- Query profile

test("Query profile sends nothing until asked, shows the capacity note, then each step's rows, time and share", { skip }, async () => {
  const from = app.calls.length;
  await showResult("Query profile");
  const qp = result().locator(".qprofile");
  await qp.getByText(/a lakehouse table is read from OneLake again, which uses capacity/).waitFor();
  await sleep(400);
  assert.deepStrictEqual(app.calls.slice(from).filter((c) => c.method === "notebook_frame_query_profile"), [], "not run before it is asked for");
  await qp.getByRole("button", { name: "Profile the query" }).click();
  await qp.locator("tbody tr").first().waitFor();
  assert.deepStrictEqual(app.calls.slice(from).filter((c) => c.method === "notebook_frame_query_profile").map((c) => c.params), [{ frame: "f1" }]);
  const rows = (await qp.locator("tbody tr").allInnerTexts()).map((t) => t.replace(/\s+/g, " ").trim());
  assert.deepStrictEqual(rows, [
    "Group by · #0 2 0.01 s 30%",
    "Filter · region='West' 3 0.005 s 12%",
    "Read result · ARROW_SCANSlowest step 6 0.03 s 58%",
  ]);
  assert.match(await qp.locator(".qprofile-stats").innerText(), /Time\s+0\.04 s[\s\S]*Rows read\s+6[\s\S]*Rows returned\s+2/);
  assert.deepStrictEqual(sent("start_cell"), []);
});

test("a query profile the engine can't make shows its plain message", { skip }, async () => {
  profileError = NO_QUERY;
  try {
    await showResult("Query profile");
    await result().getByRole("button", { name: "Profile the query" }).click();
    const alert = result().locator(".qprofile").getByRole("alert");
    await alert.waitFor();
    assert.strictEqual(await alert.innerText(), "A query profile is for a result of spark or %%sql. Show one as the cell's result, then ask for its profile.");
    assert.strictEqual(await result().getByRole("button", { name: "Profile the query" }).isEnabled(), true, "it can be asked again");
  } finally { profileError = null; }
});

test("axe finds nothing serious on the Clean and Query profile views", { skip: skip || (!axeSource && "axe-core is not installed") }, async () => {
  await showResult("Clean");
  await result().getByText("6 rows before, 6 rows after.").waitFor();
  await result().getByRole("combobox", { name: "Add a step" }).selectOption("filter");
  await result().locator(".wrangle-add").getByRole("button", { name: "Add step" }).waitFor();
  await settleForAxe(page());
  assert.deepStrictEqual(await axeFindings(page(), ".table-output"), [], "Clean");
  await showResult("Query profile");
  await result().getByRole("button", { name: "Profile the query" }).click();
  await result().locator(".qprofile tbody tr").first().waitFor();
  await settleForAxe(page());
  assert.deepStrictEqual(await axeFindings(page(), ".table-output"), [], "Query profile");
});

// ---- the warehouse Plan view

async function openWarehouse() {
  await page().evaluate((wh) => openWarehouseQuery(wh), WH);
  await page().waitForSelector(".warehouse-page .sql-edit .cm-editor");
  await page().evaluate(() => {
    const v = sqlPage.editor.view;
    v.dispatch({ changes: { from: 0, to: v.state.doc.length, insert: "SELECT TOP 10 c.Segment, SUM(f.Amount) AS Revenue\nFROM dbo.FactSales f JOIN dbo.DimCustomer c ON c.CustomerKey = f.CustomerKey\nGROUP BY c.Segment ORDER BY Revenue DESC;" } });
  });
}

async function askPlan() {
  await page().locator(".warehouse-page").getByRole("button", { name: "Estimated plan" }).click();
  const confirm = page().getByRole("dialog").getByRole("button", { name: "Run in Fabric" });
  if (await confirm.isVisible().catch(() => false)) await confirm.click();
}

test("the Plan view draws the statement and its operators with estimated rows and cost, and the warning", { skip }, async () => {
  await openWarehouse();
  const from = app.calls.length;
  await page().locator(".warehouse-page").getByRole("button", { name: "Estimated plan" }).click();
  await page().getByRole("dialog").getByRole("button", { name: "Run in Fabric" }).click();
  const tree = page().getByRole("list", { name: "Plan for statement 1" });
  await tree.waitFor();
  const starts = app.calls.slice(from).filter((c) => c.method === "sql_start").map((c) => c.params);
  assert.strictEqual(starts.length, 1);
  assert.ok(starts[0].estimated_plan && !starts[0].allow && !starts[0].confirm, "a read, asked for its plan");
  assert.match(await page().locator(".sql-plan-stmt-head").innerText(), /^Statement 1 · 10 rows · cost 20$/);
  const ops = (await tree.getByRole("listitem").allTextContents());
  assert.strictEqual(ops.length, 4);
  // Own cost = subtree cost less the children's: Sort 20-18 = 2 (10%), Hash 18-13 = 5 (25%),
  // DimCustomer 1 (5%), FactSales 12 (60%).
  assert.match(ops[0], /^Sort \(TopN Sort\)10 rows10%$/);
  assert.match(ops[1], /^Level 2: Hash Match \(Inner Join\)warning250,000 rows25%$/);
  assert.match(ops[2], /^Level 3: Table Scandbo\.DimCustomer18,484 rows5%$/);
  assert.match(ops[3], /^Level 3: Table Scandbo\.FactSales250,000 rows60%$/);
  assert.strictEqual(await tree.locator(".sql-plan-warn").filter({ hasText: "warning" }).count(), 1, "the warning is on the join only");
  assert.match(await page().locator(".sql-plan-op.heavy").innerText(), /FactSales/, "the heaviest operator stands out");
});

test("Copy XML gives the showplan exactly as it came", { skip }, async () => {
  await page().evaluate(() => { window.__copied = []; });
  await page().locator(".sql-plan").getByRole("button", { name: "Copy XML" }).click();
  await page().waitForFunction(() => window.__copied.length === 1);
  assert.strictEqual(await page().evaluate(() => window.__copied[0]), PLAN);
});

test("axe finds nothing serious on the Plan view", { skip: skip || (!axeSource && "axe-core is not installed") }, async () => {
  await page().waitForFunction(() => !document.querySelector(".dialog"));
  await settleForAxe(page());
  assert.deepStrictEqual(await axeFindings(page(), ".warehouse-page"), []);
});

test("XML that isn't a showplan shows a plain message and the XML as it came", { skip }, async () => {
  for (const answer of ['<?xml version="1.0"?><Report xmlns="urn:example"><Row id="1"/></Report>', "<ShowPlanXML><BatchSequence>"]) {
    planAnswer = answer;
    const before = app.calls.length;
    await askPlan();
    await page().waitForFunction(() => /isn't a showplan/.test(document.querySelector(".sql-plan")?.textContent || ""));
    assert.ok(app.calls.slice(before).some((c) => c.method === "sql_start" && c.params.estimated_plan));
    assert.match(await page().locator(".sql-plan").innerText(), /^Fabric's answer isn't a showplan\. It came back as:/);
    assert.strictEqual(await page().locator(".sql-plan-xml").textContent(), answer);
    assert.strictEqual(await page().getByRole("list", { name: "Plan for statement 1" }).count(), 0);
  }
  planAnswer = PLAN;
});

// ---- read-only, and no page errors

test("opening Clean, Query profile and the Plan view sent no write", { skip }, async () => {
  const methods = new Set(app.calls.map((c) => c.method));
  for (const w of WRITES) assert.ok(!methods.has(w), `${w} was sent`);
  for (const p of sent("sql_start")) assert.ok(p.estimated_plan, "every warehouse run was an estimated plan");
  assert.deepStrictEqual(READS.filter((r) => !methods.has(r)), [], "every read was exercised");
});

test("nothing on the page threw", { skip }, async () => {
  assert.deepStrictEqual(app.errors, []);
});
