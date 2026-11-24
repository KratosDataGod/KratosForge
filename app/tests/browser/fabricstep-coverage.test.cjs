// The Fabric step (fabricstep.js) where it says no or goes wrong: only the last code cell can be marked,
// Insert V-Order cell needs a lakehouse with tables and at least one ticked, a step whose check fails,
// one without a sign-in, one Fabric refuses to start, and one that is cancelled while it runs and comes
// back failed with Fabric's error. Every engine call is mocked; the risk check and the confirm before a
// run are the app's own.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
let app;
const page = () => app.page;
const LH = { workspace_id: "w1", workspace_name: "Sales Dev", lakehouse_id: "lh1", name: "LH_Gold" };

let check, start, result, tables, signIn;
before(async () => {
  if (skip) return;
  app = await openApp({ mocks: {
    save_notebook: () => ({ saved: true }),
    cached_schemas: () => null,
    list_tables: () => tables,
    notebook_vorder_cell: (p) => (p.picked ? { source: `spark.sql("OPTIMIZE ${p.picked.join(", ")} VORDER")` } : { candidates: ["dbo.sales"] }),
    notebook_fabric_step_check: () => check,
    run_notebook_fabric_step: () => start(),
    notebook_fabric_step_result: () => result,
    cancel_fabric_run: (p) => ({ cancelled: true, ...p }),
    sign_in: () => signIn(),
    risk_check: () => ({ result: "pass" }),
    fabric_runs: () => [],
  } });
  await page().evaluate(() => { window.__stepConfirm = stepConfirm; });
});
after(async () => { await app?.close(); });

beforeEach(async () => {
  if (skip) return;
  tables = { dbo: ["sales", "customers"] };
  check = { ok: true, item_name: "Gold load (Fabric step)", workspace_name: "Sales Dev", workspace_id: "w1", lakehouse: "LH_Gold", tables: ["dbo.sales"] };
  result = { status: "InProgress" };
  signIn = () => { throw new Error("Sign-in was cancelled."); };
  await page().evaluate(() => {
    document.getElementById("dialog-root").replaceChildren();
    const t = document.getElementById("toast"); t.hidden = true; t.textContent = "";
    state.user = "chris@contoso.com"; state.nbTables = {}; state.nbSchemas = {};
    trackFabricJobs = () => {};  // Monitor's own job polling isn't part of this
    stepConfirm = window.__stepConfirm;
  });
});

const load = (cells, lakehouse = LH) => page().evaluate(([cells, lakehouse]) => {
  loadIntoState({ name: `Gold load ${Math.random()}`, cells, lakehouse });
  go("notebook");
}, [cells, lakehouse]);
const cellMenu = async (i, item) => {
  const cell = page().locator(".cell").nth(i);
  await cell.hover();
  await cell.getByRole("button", { name: "More cell actions" }).click();
  await page().getByRole("menuitem", { name: item }).click();
};
const toastSays = (text) => page().waitForFunction((t) => document.getElementById("toast").textContent === t, text);
const dialog = () => page().locator("#dialog-root .dialog");
const cellState = () => page().evaluate(() => { const c = state.notebook.cells.find((x) => x.meta.fabric_step); return { status: c.status, outputs: c.outputs }; });

test("only the last code cell can run on Fabric; marking it unmarks any other", { skip }, async () => {
  await load([
    { type: "code", cell_id: "a", source: "df = read()", meta: { fabric_step: false } },
    { type: "code", cell_id: "b", source: "write(df)", meta: {} },
  ]);
  await cellMenu(0, "Run on Fabric (Spark)");
  await toastSays("Only the last code cell can run on Fabric. Move this cell to the end, or mark the last cell.");
  assert.deepStrictEqual(await page().evaluate(() => state.notebook.cells.map((c) => !!c.meta.fabric_step)), [false, false]);
  await page().evaluate(() => { state.notebook.cells[0].meta.fabric_step = true; });
  await cellMenu(1, "Run on Fabric (Spark)");
  assert.deepStrictEqual(await page().evaluate(() => state.notebook.cells.map((c) => !!c.meta.fabric_step)), [false, true]);
  await page().locator(".cell").nth(1).locator(".fabric-step-tag").waitFor();
});

test("Insert V-Order cell needs a lakehouse with tables, and at least one table ticked", { skip }, async () => {
  const step = { type: "code", cell_id: "s", source: "spark.sql('select 1')", meta: { fabric_step: true } };
  await load([step], null);
  await cellMenu(0, "Insert V-Order cell");
  await toastSays("Attach a OneLake lakehouse first. The Fabric step V-Orders its tables.");

  tables = {};
  await load([step]);
  await cellMenu(0, "Insert V-Order cell");
  await toastSays("No tables found in LH_Gold. Sign in, or run the cells that write them first.");

  tables = { dbo: ["sales", "customers"] };
  await page().evaluate(() => { state.nbTables = {}; });
  await load([step]);
  await cellMenu(0, "Insert V-Order cell");
  await dialog().getByRole("checkbox", { name: "dbo.sales" }).waitFor();
  assert.ok(await dialog().getByRole("checkbox", { name: "dbo.sales" }).isChecked(), "a table the notebook names is ticked");
  assert.ok(!(await dialog().getByRole("checkbox", { name: "dbo.customers" }).isChecked()));
  await dialog().getByRole("checkbox", { name: "dbo.sales" }).uncheck();
  await dialog().getByRole("button", { name: "Insert" }).click();
  await toastSays("Tick at least one table.");
  assert.strictEqual(await page().evaluate(() => state.notebook.cells[0].source), "spark.sql('select 1')");

  await cellMenu(0, "Insert V-Order cell");
  await dialog().getByRole("checkbox", { name: "dbo.customers" }).check();
  await dialog().getByRole("button", { name: "Insert" }).click();
  await page().waitForFunction(() => /OPTIMIZE/.test(state.notebook.cells[0].source));
  assert.strictEqual(await page().evaluate(() => state.notebook.cells[0].source), 'spark.sql("OPTIMIZE dbo.sales, dbo.customers VORDER")');
  assert.ok(await page().evaluate(() => state.notebook.dirty));
});

test("a step that fails its check, can't sign in, or isn't started by Fabric says why in the cell", { skip }, async () => {
  const cells = [{ type: "code", cell_id: "s", source: "spark.sql('OPTIMIZE dbo.sales VORDER')", meta: { fabric_step: true } }];
  const runStep = async () => {
    await page().evaluate(() => { window.__done = runFabricStep(state.notebook.cells[0], state.notebook); });
    await page().waitForFunction(() => window.__done && state.notebook.cells[0].status === "error");
    return (await cellState()).outputs[0].evalue;
  };
  check = { ok: false, problem: "The Fabric step needs a lakehouse in a workspace on a Fabric capacity." };
  await load(cells);
  assert.strictEqual(await runStep(), "The Fabric step needs a lakehouse in a workspace on a Fabric capacity.");

  check = { ok: true, item_name: "Gold load (Fabric step)", workspace_name: "Sales Dev", lakehouse: "LH_Gold", tables: [] };
  await page().evaluate(() => { state.user = null; });
  await load(cells);
  assert.strictEqual(await runStep(), "Sign in to run this cell on Fabric. Nothing was sent.");

  await page().evaluate(() => { state.user = "chris@contoso.com"; });
  start = () => { throw new Error("Fabric refused the job: the capacity is paused."); };
  await load(cells);
  await page().evaluate(() => { window.__done = runFabricStep(state.notebook.cells[0], state.notebook); });
  // The app's own gates: the risk check passes, then the confirm names what it writes.
  const confirm = dialog().filter({ hasText: "Run the last cell of" });
  await confirm.waitFor();
  assert.match(await confirm.innerText(), /Tables\s+None named in an OPTIMIZE; the cell's own writes are V-Ordered/);
  await confirm.getByRole("button", { name: "Run on Fabric" }).click();
  await page().waitForFunction(() => state.notebook.cells[0].status === "error");
  assert.match((await cellState()).outputs[0].evalue, /Fabric refused the job: the capacity is paused\./);
});

test("Cancel while the step runs asks Fabric to cancel it, and a failed run shows Fabric's error", { skip }, async () => {
  start = () => ({ status: "NotStarted", request_id: "fds-1", job_id: "job-1", workspace_id: "w1", item_id: "item-1", workspace_name: "Sales Dev", url: "https://app.fabric.microsoft.com/run/1" });
  await load([{ type: "code", cell_id: "s", source: "spark.sql('OPTIMIZE dbo.sales VORDER')", meta: { fabric_step: true } }]);
  await page().evaluate(() => { stepConfirm = async () => true; window.__done = runFabricStep(state.notebook.cells[0], state.notebook); });
  const line = page().locator(".fabric-step-line").first();
  await line.getByRole("button", { name: "Cancel" }).click();
  const sent = () => app.calls.filter((c) => c.method === "cancel_fabric_run").map((c) => c.params);
  for (const end = Date.now() + 5000; !sent().length; await new Promise((r) => setTimeout(r, 20))) {
    if (Date.now() > end) throw new Error("Cancel never reached the engine");
  }
  assert.deepStrictEqual(sent(), [{ workspace_id: "w1", item_id: "item-1", job_id: "job-1" }]);

  result = { status: "Failed", url: "https://app.fabric.microsoft.com/run/1", tables: [],
    output: { stdout: "starting\n", error: "Traceback (most recent call last):\nAnalysisException: Table dbo.sales not found" } };
  await page().waitForFunction(() => state.notebook.cells[0].status === "error", null, { timeout: 15000 });
  const { outputs } = await cellState();
  assert.deepStrictEqual(outputs.slice(1).map((o) => o.type), ["stream", "error"]);
  assert.strictEqual(outputs[2].evalue, "AnalysisException: Table dbo.sales not found");
  assert.strictEqual(outputs[2].ename, "Error on Fabric");
  await page().locator(".fabric-step-line", { hasText: "Failed on Fabric" }).waitFor();
});
