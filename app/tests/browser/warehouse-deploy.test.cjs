// WH-13: deploy the project to the warehouse, from the Compare tab, against the real engine and a real
// repository (see harness.cjs for how to run it). The warehouse is mocked: its catalog, the engine's
// dry-run check (tested in test_warehouse_project.py) and the confirmed runs.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const { playwright, openApp, sqlRunMocks } = require("./harness.cjs");

const DEV = { kind: "warehouse", workspace_id: "ws1", workspace_name: "Sales Dev", item_id: "wh1", name: "WH_Gold" };
const PROD = { kind: "warehouse", workspace_id: "ws9", workspace_name: "Sales Prod", item_id: "wh9", name: "WH_Gold" };
const SHOTS = process.env.SCREENSHOT_DIR;
const TABLE = "CREATE TABLE [dbo].[DimCustomer]\n(\n    [CustomerKey] int NOT NULL,\n    [Segment] varchar(20) NULL\n);\nGO\n";

let app;
let root;
let failOn = null;
const changes = [];  // the confirmed runs, in order
const skip = !playwright && "Playwright is not installed";
const page = () => app.page;

function catalog({ query }) {
  const cols = (...names) => names.map((name) => ({ name, type: "nvarchar" }));
  if (/FROM sys\.sql_modules AS m\nJOIN/.test(query)) {
    return { columns: cols("schema_name", "object_name", "object_type", "definition"), rows: [
      ["dbo", "vw_Sales", "V", "CREATE VIEW dbo.vw_Sales AS SELECT 1 AS x"], ["dbo", "vw_Old", "V", "CREATE VIEW dbo.vw_Old AS SELECT 1 AS y"]] };
  }
  if (/FROM sys\.schemas AS s WHERE/.test(query)) return { columns: cols("name"), rows: [] };
  if (/FROM sys\.columns/.test(query)) {
    return { columns: cols("schema_name", "object_name", "column_name", "type_name", "max_length", "precision", "scale", "is_nullable"), rows: [
      ["dbo", "DimCustomer", "CustomerKey", "int", 4, 10, 0, 0], ["dbo", "DimCustomer", "Segment", "varchar", 20, 0, 0, 1]] };
  }
  return { columns: cols("schema_name", "object_name", "object_type"), rows: [["dbo", "DimCustomer", "U"], ["dbo", "vw_Sales", "V"], ["dbo", "vw_Old", "V"]] };
}

function write(rel, text) {
  const f = path.join(root, "WH_Gold.Warehouse", ...rel.split("/"));
  fs.mkdirSync(path.dirname(f), { recursive: true });
  fs.writeFileSync(f, text);
}

before(async () => {
  if (skip) return;
  const runs = sqlRunMocks(catalog);
  const states = new Map();
  app = await openApp({
    engine: ["sql_format", "sql_dialects", "sql_analyze", "sql_queries_list", "git_init", "wh_projects_list", "wh_project_files",
      "wh_project_read", "wh_project_compare", "wh_project_diff", "wh_project_deploy_plan"],
    mocks: { ...runs,
      sql_start: (p) => {
        if (p.dry_run) {
          return /BEGIN TRAN/i.test(p.query)
            ? { dry_run: true, permission: "fabric_mutation", statements: [], refused: { statement: 1, reason: "the run is one transaction the app commits: leave out BEGIN TRAN, COMMIT and ROLLBACK" } }
            : { dry_run: true, permission: "warehouse_write", refused: null, statements: [{ statement: 1, permission: "warehouse_write", reason: "changes the warehouse" }] };
        }
        if (p.confirm) {
          const id = `chg${changes.length + 1}`;
          changes.push(p);
          const failed = failOn && p.query.includes(failOn);
          states.set(id, failed ? { id, state: "failed", error: "Invalid object name 'dbo.Nope'.", target: "warehouse" }
            : { id, state: "completed", change: true, affected: 0, committed: true, target: "warehouse" });
          return { id, state: "running", target: "warehouse" };
        }
        return { ...runs.sql_start(p), target: "warehouse" };
      },
      sql_status: (p) => states.get(p.execution) || { ...runs.sql_status(p), target: "warehouse" } },
  });
  root = path.join(app.engineHome, "sales-platform");
  fs.mkdirSync(root);
  await page().evaluate((p) => rpc("git_init", { path: p }), root);
  write("dbo/Tables/DimCustomer.sql", TABLE.replace("[Segment] varchar(20) NULL", "[Segment] varchar(20) NULL,\n    [Region] nvarchar(50) NULL"));
  write("dbo/Tables/FactTargets.sql", "CREATE TABLE [dbo].[FactTargets]\n(\n    [Segment] varchar(20) NULL,\n    [Target] decimal(18,2) NULL\n);\nGO\n");
  write("dbo/Views/vw_Sales.sql", "CREATE VIEW dbo.vw_Sales AS SELECT 2 AS x\nGO\n");
  await page().evaluate((wh) => openWarehouseQuery(wh), DEV);
  await page().waitForSelector(".warehouse-page .sql-edit .cm-editor");
  app.errors.length = 0;
});

after(async () => { await app?.close(); });

async function openDeploy() {
  await page().getByRole("tab", { name: "Project" }).click();
  await page().locator(".wh-project").getByRole("button", { name: "Compare with WH_Gold" }).click();
  const confirm = page().getByRole("dialog").getByRole("button", { name: "Run in Fabric" });
  if (await confirm.isVisible().catch(() => false)) await confirm.click();
  await page().locator(".wh-cmp").waitFor();
  await page().getByRole("button", { name: "Deploy to WH_Gold..." }).click();
  const dialog = page().getByRole("dialog", { name: "Deploy WH_Gold.Warehouse" });
  await dialog.waitFor();
  return dialog;
}

test("a deploy previews each change in order, never ticks a drop, and runs the ticked ones confirmed", { skip }, async () => {
  const dialog = await openDeploy();
  const items = await dialog.locator(".wh-dep-verb").allTextContents();
  assert.deepStrictEqual(items, ["1. ALTER TABLE dbo.DimCustomer", "2. CREATE TABLE dbo.FactTargets", "3. CREATE OR ALTER VIEW dbo.vw_Sales", "4. DROP VIEW dbo.vw_Old"]);
  assert.ok(!(await dialog.getByRole("checkbox", { name: "DROP VIEW dbo.vw_Old" }).isChecked()), "a drop is never ticked for you");
  assert.match(await dialog.textContent(), /From sales-platform, branch main at no commit yet, to WH_Gold in Sales Dev \(Dev\)/);
  assert.strictEqual(await page().evaluate(() => document.activeElement?.textContent), "Cancel");
  assert.match(await dialog.locator(".wh-dep-sql").textContent(), /^ALTER TABLE \[dbo\]\.\[DimCustomer\] ADD \[Region\] nvarchar\(50\) NULL;$/);
  await dialog.getByRole("button", { name: /CREATE OR ALTER VIEW dbo\.vw_Sales/ }).click();
  assert.match(await dialog.locator(".wh-dep-sql").textContent(), /^CREATE OR ALTER VIEW dbo\.vw_Sales AS SELECT 2 AS x/);
  if (SHOTS) {
    await page().waitForTimeout(400);
    await page().screenshot({ path: path.join(SHOTS, "wh-13-deploy.png") });
  }
  await dialog.getByRole("button", { name: "Deploy 3 changes" }).click();
  const done = page().getByRole("dialog", { name: "Deployed 3 changes to WH_Gold" });
  await done.waitFor();
  assert.deepStrictEqual(changes.map((c) => [c.query.split(/\s+/).slice(0, 3).join(" "), c.confirm_name, c.allow[0]]), [
    ["ALTER TABLE [dbo].[DimCustomer]", "WH_Gold", "warehouse_write"], ["CREATE TABLE [dbo].[FactTargets]", "WH_Gold", "warehouse_write"],
    ["CREATE OR ALTER", "WH_Gold", "warehouse_write"]]);
  await done.getByRole("button", { name: "Close" }).last().click();
});

test("a deploy stops at the first change that fails and says which went in", { skip }, async () => {
  changes.length = 0;
  failOn = "FactTargets";
  const dialog = await openDeploy();
  await dialog.getByRole("button", { name: "Deploy 3 changes" }).click();
  const done = page().getByRole("dialog", { name: "Deployed 1 of 3 changes" });
  await done.waitFor();
  assert.match(await done.textContent(), /Done: ALTER TABLE dbo\.DimCustomer.*Failed: CREATE TABLE dbo\.FactTargets \(Invalid object name 'dbo\.Nope'\.\).*Not run: CREATE OR ALTER VIEW dbo\.vw_Sales/s);
  assert.strictEqual(changes.length, 2, "nothing after the failure was sent");
  failOn = null;
  await done.getByRole("button", { name: "Close" }).last().click();
});

test("a change the engine would refuse isn't offered", { skip }, async () => {
  write("dbo/StoredProcedures/usp_Load.sql", "CREATE PROCEDURE dbo.usp_Load AS BEGIN BEGIN TRAN; UPDATE dbo.t SET a = 1; END\nGO\n");
  const dialog = await openDeploy();
  const box = dialog.getByRole("checkbox", { name: "CREATE PROCEDURE dbo.usp_Load" });
  assert.ok(await box.isDisabled() && !(await box.isChecked()));
  assert.match(await dialog.textContent(), /the engine refuses it: the run is one transaction/);
  await dialog.getByRole("button", { name: "Cancel" }).click();
  fs.rmSync(path.join(root, "WH_Gold.Warehouse", "dbo", "StoredProcedures"), { recursive: true });
});

test("Prod deploys only what's committed and pushed, and asks for the name", { skip }, async () => {
  await page().evaluate((wh) => openWarehouseQuery(wh), PROD);
  await page().waitForSelector(".warehouse-page .sql-edit .cm-editor");
  const dialog = await openDeploy();
  assert.match(await dialog.locator(".wh-dep-gate").textContent(), /Prod deploys only what's committed\. Commit 3 files in Source control first\./);
  await dialog.getByRole("textbox", { name: "Warehouse name" }).fill("WH_Gold");
  assert.ok(await dialog.getByRole("button", { name: "Deploy 0 changes to Prod" }).isDisabled(), "the engine held every uncommitted change");
  assert.strictEqual(await dialog.getByRole("checkbox", { name: "CREATE TABLE dbo.FactTargets" }).isDisabled(), true);
  await dialog.getByRole("button", { name: "Cancel" }).click();
});

test("nothing on the page threw", { skip }, async () => {
  assert.deepStrictEqual(app.errors, []);
});
