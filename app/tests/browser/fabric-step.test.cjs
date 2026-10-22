// The Fabric step (fabricstep.js): the last cell, marked Run on Fabric, runs on Fabric Spark after
// the others run on this PC. Marking, the V-Order cell and the checks come from the real engine;
// the Fabric calls (publishing the step, its job and its result) are mocks, so nothing reaches Fabric.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { playwright, openApp } = require("./harness.cjs");

const SHOTS = process.env.SCREENSHOT_DIR;
const skip = !playwright && "Playwright is not installed";
const LAKEHOUSE = { workspace_id: "11111111-2222-3333-4444-555555555555", workspace_name: "Sales Dev",
  lakehouse_id: "66666666-7777-8888-9999-000000000000", name: "LH_Gold" };
const CELLS = [
  { type: "code", cell_id: "c1", source: 'rows = 3\nprint("wrote gold.fact_sales")', meta: {} },
  { type: "code", cell_id: "c2", source: "# V-Order", meta: {} },
];

let app;
const page = () => app.page;
const started = [];
let result = null;

before(async () => {
  if (skip) return;
  app = await openApp({
    viewport: { width: 1440, height: 900 },
    engine: ["save_notebook", "start_cell", "poll_cell", "kernel_variables", "notebook_fabric_step_check", "notebook_vorder_cell"],
    mocks: {
      list_tables: () => ({ gold: ["fact_sales", "dim_customer", "other"] }),
      risk_check: () => ({ result: "pass" }),
      fabric_runs: () => [],
      run_notebook_fabric_step: (p) => {
        started.push(p);
        return { kind: "Fabric step", status: "NotStarted", request_id: "fds-1", job_id: "job-1", workspace_id: LAKEHOUSE.workspace_id,
          item_id: "item-1", workspace_name: "Sales Dev", url: "https://app.fabric.microsoft.com/groups/ws/synapsenotebooks/item-1" };
      },
      notebook_fabric_step_result: () => result,
    },
  });
  await page().evaluate(({ cells, lakehouse }) => {
    state.user = "test@example.com";
    loadIntoState({ name: "Gold load", cells, lakehouse });
    go("notebook");
  }, { cells: CELLS, lakehouse: LAKEHOUSE });
});

after(async () => { if (app) await app.close(); });

const cell = (n) => page().locator(".cell").nth(n);

async function menu(n, item) {
  await cell(n).click();
  await cell(n).getByRole("button", { name: "More cell actions" }).click();
  await page().getByRole("menuitem", { name: item }).or(page().getByRole("button", { name: item })).first().click();
}

test("only the last code cell can be marked, and it shows the badge", { skip }, async () => {
  await menu(0, "Run on Fabric (Spark)");
  await page().getByText("Only the last code cell can run on Fabric").waitFor();
  assert.strictEqual(await page().evaluate(() => !!state.notebook.cells[0].meta.fabric_step), false);
  await menu(1, "Run on Fabric (Spark)");
  await cell(1).getByText("Runs on Fabric · Spark").waitFor();
  assert.match(await cell(1).locator(".fabric-step-tag").innerText(), /Preview/);
});

test("Insert V-Order cell ticks the tables the notebook names and writes OPTIMIZE ... VORDER", { skip }, async () => {
  await menu(1, "Insert V-Order cell");
  const dialog = page().locator(".dialog, [role=dialog]").last();
  await dialog.getByText("gold.fact_sales").waitFor();
  assert.strictEqual(await dialog.getByRole("checkbox", { name: "gold.fact_sales" }).isChecked(), true);
  assert.strictEqual(await dialog.getByRole("checkbox", { name: "gold.other" }).isChecked(), false);
  await dialog.getByRole("checkbox", { name: "gold.dim_customer" }).check();
  await dialog.getByRole("button", { name: "Insert" }).click();
  await page().waitForFunction(() => state.notebook.cells[1].source.includes("OPTIMIZE"));
  const source = await page().evaluate(() => state.notebook.cells[1].source);
  assert.match(source, /spark\.sql\("OPTIMIZE `gold`\.`fact_sales` VORDER"\)/);
  assert.match(source, /spark\.sql\("OPTIMIZE `gold`\.`dim_customer` VORDER"\)/);
});

test("Run all runs cell 1 here, confirms, sends only the last cell to Fabric and shows the V-Order check", { skip }, async () => {
  result = { status: "Completed", url: "https://app.fabric.microsoft.com/groups/ws/synapsenotebooks/item-1", output: { stdout: "optimized\n", error: null },
    tables: [{ table: "gold.fact_sales", v_ordered: true, files: 4 }, { table: "gold.dim_customer", v_ordered: false, files: 1 }] };
  const run = page().evaluate(() => runAll());
  await cell(0).getByText("wrote gold.fact_sales").waitFor({ timeout: 60000 });
  const dialog = page().locator(".dialog, [role=dialog]").last();
  await dialog.getByText("Run the last cell of Gold load on Fabric?").waitFor();
  const text = await dialog.innerText();
  assert.match(text, /Gold load \(Fabric step\), a Spark notebook/);
  assert.match(text, /gold\.fact_sales, gold\.dim_customer/);
  assert.match(text, /uses capacity units/);
  assert.match(text, /Files\/_fabric_desktop\/results in LH_Gold/, "the confirm names every write");
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "fabric-step-confirm.png") });
  await dialog.getByRole("button", { name: "Run on Fabric" }).click();
  await cell(1).getByText("gold.fact_sales V-Ordered").waitFor({ timeout: 20000 });
  await run;
  assert.strictEqual(started.length, 1);
  assert.strictEqual(started[0].lakehouse_ref.lakehouse_id, LAKEHOUSE.lakehouse_id);
  assert.match(await cell(1).innerText(), /Succeeded\s+Fabric step[\s\S]*Completed on Fabric[\s\S]*optimized/);
  assert.match(await cell(1).innerText(), /gold\.dim_customer not V-Ordered/);
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "fabric-step-done.png") });
  // Rewrite all files puts every file of the table that wasn't rewritten in scope.
  await cell(1).getByRole("button", { name: "Rewrite all files" }).click();
  await page().waitForFunction(() => state.notebook.cells[1].source.includes("minFileSize"));
  assert.match(await page().evaluate(() => state.notebook.cells[1].source), /OPTIMIZE `gold`\.`dim_customer` VORDER/);
  assert.deepStrictEqual(app.errors, []);
});

test("a code cell below the mark stops Run all before any cell runs", { skip }, async () => {
  const before = started.length;
  await page().evaluate(() => {
    state.notebook.cells.push(cellFrom({ type: "code", source: "late = 1" }));
    renderNotebook();
  });
  await page().evaluate(() => runAll());
  await page().getByText(/Only the last code cell can run on Fabric, and 1 code cell is below/).waitFor();
  assert.strictEqual(started.length, before);
  assert.strictEqual(await page().evaluate(() => state.notebook.cells[2].status), null);
});
