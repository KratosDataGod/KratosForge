// WH-11: the warehouse as files in Git, on the Warehouse page's Project tab, against the real engine
// and a real repository (see harness.cjs for how to run it). The warehouse's catalog queries are mocked.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const { playwright, openApp, sqlRunMocks } = require("./harness.cjs");

const WH = { kind: "warehouse", workspace_id: "ws1", workspace_name: "Sales Dev", item_id: "wh1", name: "WH_Gold" };
const SHOTS = process.env.SCREENSHOT_DIR;
const PROJECT_METHODS = ["git_init", "wh_projects_list", "wh_project_files", "wh_project_read", "wh_project_save", "wh_project_write"];

let app;
let root;
const skip = !playwright && "Playwright is not installed";
const page = () => app.page;
const file = (...p) => path.join(root, "WH_Gold.Warehouse", ...p);

// The warehouse's catalog, as the explorer's and Import's catalog queries read it.
function catalog({ query }) {
  const cols = (...names) => names.map((name) => ({ name, type: "nvarchar" }));
  if (/FROM sys\.sql_modules AS m\nJOIN/.test(query)) {
    return { columns: cols("schema_name", "object_name", "object_type", "definition"), rows: [
      ["dbo", "vw_Sales", "V", "CREATE VIEW dbo.vw_Sales AS SELECT 1 AS x"],
      ["staging", "usp_Load", "P", "CREATE PROCEDURE staging.usp_Load AS SELECT 1"]] };
  }
  if (/FROM sys\.schemas AS s WHERE/.test(query)) return { columns: cols("name"), rows: [["staging"]] };
  if (/FROM sys\.columns/.test(query)) {
    return { columns: cols("schema_name", "object_name", "column_name", "type_name", "max_length", "precision", "scale", "is_nullable"), rows: [
      ["dbo", "DimCustomer", "CustomerKey", "int", 4, 10, 0, 0], ["dbo", "DimCustomer", "Segment", "varchar", 20, 0, 0, 1]] };
  }
  if (/FROM sys\.objects/.test(query)) {
    return { columns: cols("schema_name", "object_name", "object_type"), rows: [
      ["dbo", "DimCustomer", "U"], ["dbo", "vw_Sales", "V"], ["staging", "usp_Load", "P"]] };
  }
  return { columns: cols("x"), rows: [[1]] };
}

before(async () => {
  if (skip) return;
  const runs = sqlRunMocks(catalog);
  app = await openApp({
    engine: ["sql_format", "sql_dialects", "sql_analyze", "sql_queries_list", ...PROJECT_METHODS],
    mocks: { ...runs, sql_start: (p) => ({ ...runs.sql_start(p), target: "warehouse" }),
      sql_status: (p) => ({ ...runs.sql_status(p), target: "warehouse" }) },
  });
  root = path.join(app.engineHome, "sales-platform");
  fs.mkdirSync(root);
  await page().evaluate((p) => rpc("git_init", { path: p }), root);
  await page().evaluate((wh) => openWarehouseQuery(wh), WH);
  await page().waitForSelector(".warehouse-page .sql-edit .cm-editor");
  app.errors.length = 0;
});

after(async () => { await app?.close(); });

const projectTab = () => page().getByRole("tab", { name: "Project" });
const panel = () => page().locator(".wh-project");

test("with no project yet, Import makes WH_Gold.Warehouse in Fabric's layout", { skip }, async () => {
  await projectTab().click();
  await panel().getByText("No WH_Gold.Warehouse folder").waitFor();
  await panel().getByRole("button", { name: "Import from WH_Gold" }).click();
  await page().getByRole("dialog").getByRole("button", { name: "Run in Fabric" }).click();  // catalog queries use capacity
  const dialog = page().getByRole("dialog", { name: /Import WH_Gold into a new project/ });
  await dialog.waitFor();
  assert.match(await dialog.textContent(), /4 objects scripted.*Make WH_Gold\.Warehouse in.*4 new files.*0 files that differ/s);
  await dialog.getByRole("button", { name: "Import" }).click();
  await panel().getByText("DimCustomer.sql").waitFor();
  assert.match(fs.readFileSync(file("dbo", "Tables", "DimCustomer.sql"), "utf8"), /CREATE TABLE \[dbo\]\.\[DimCustomer\]\n\(\n {4}\[CustomerKey\] int NOT NULL,\n {4}\[Segment\] varchar\(20\) NULL\n\);\nGO\n/);
  assert.ok(fs.existsSync(file("dbo", "Views", "vw_Sales.sql")));
  assert.ok(fs.existsSync(file("staging", "StoredProcedures", "usp_Load.sql")));
  assert.ok(fs.existsSync(file("Security", "staging.sql")));
  assert.ok(fs.existsSync(file("WH_Gold.sqlproj")) && fs.existsSync(file(".platform")));
  assert.match(await panel().textContent(), /WH_Gold\.Warehouse · sales-platform · main/);
  assert.strictEqual(await panel().locator(".wh-p-git.added").count(), 4, "each file is new to Git");
});

test("a file opens in its own tab and Save writes it back, never over a newer one", { skip }, async () => {
  await panel().getByRole("button", { name: /DimCustomer\.sql/ }).click();
  await page().getByRole("tab", { name: "DimCustomer" }).waitFor();
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "wh-11-project.png") });
  await page().evaluate(() => {
    const v = sqlPage.editor.view;
    v.dispatch({ changes: { from: v.state.doc.length, insert: "-- reviewed\n" } });
  });
  await page().getByRole("button", { name: "Save file" }).click();
  await page().waitForFunction(() => /Saved dbo\/Tables\/DimCustomer\.sql/.test(document.querySelector(".toast")?.textContent || ""));
  assert.match(fs.readFileSync(file("dbo", "Tables", "DimCustomer.sql"), "utf8"), /-- reviewed\n$/);
  fs.writeFileSync(file("dbo", "Tables", "DimCustomer.sql"), "CREATE TABLE dbo.DimCustomer (a int);\n");
  await page().locator(".sql-edit .cm-content").click();
  await page().keyboard.press("Control+s");
  await page().waitForFunction(() => /changed since you opened it/.test(document.querySelector(".toast")?.textContent || ""));
  assert.strictEqual(fs.readFileSync(file("dbo", "Tables", "DimCustomer.sql"), "utf8"), "CREATE TABLE dbo.DimCustomer (a int);\n");
});

test("Import again keeps a file that differs unless you tick replace", { skip }, async () => {
  await panel().getByRole("button", { name: "Import from WH_Gold" }).click();
  const dialog = page().getByRole("dialog", { name: /Import WH_Gold into WH_Gold\.Warehouse/ });
  await dialog.waitFor();
  assert.match(await dialog.textContent(), /0 new files.*1 file that differ.*3 already the same.*Differ: dbo\.DimCustomer/s);
  await dialog.getByRole("button", { name: "Import" }).click();
  await page().waitForFunction(() => /0 new, 0 replaced, 3 the same/.test(document.querySelector(".toast")?.textContent || ""));
  assert.strictEqual(fs.readFileSync(file("dbo", "Tables", "DimCustomer.sql"), "utf8"), "CREATE TABLE dbo.DimCustomer (a int);\n");
  await panel().getByRole("button", { name: "Import from WH_Gold" }).click();
  await dialog.waitFor();
  await dialog.getByRole("checkbox").check();
  await dialog.getByRole("button", { name: "Import" }).click();
  await page().waitForFunction(() => /1 replaced/.test(document.querySelector(".toast")?.textContent || ""));
  assert.match(fs.readFileSync(file("dbo", "Tables", "DimCustomer.sql"), "utf8"), /\[Segment\] varchar\(20\) NULL/);
});

test("New object writes a starting script where the layout puts it and opens it", { skip }, async () => {
  await panel().getByRole("button", { name: "New object" }).click();
  const dialog = page().getByRole("dialog", { name: "New object" });
  await dialog.getByRole("combobox", { name: "Kind" }).selectOption("view");
  await dialog.getByRole("textbox", { name: "Name" }).fill("vw_Targets");
  await dialog.getByRole("button", { name: "Create file" }).click();
  await page().getByRole("tab", { name: "vw_Targets" }).waitFor();
  assert.match(fs.readFileSync(file("dbo", "Views", "vw_Targets.sql"), "utf8"), /^CREATE VIEW \[dbo\]\.\[vw_Targets\]/);
  await panel().getByRole("button", { name: "New object" }).click();
  await dialog.getByRole("combobox", { name: "Kind" }).selectOption("view");
  await dialog.getByRole("textbox", { name: "Name" }).fill("vw_Sales");
  await dialog.getByRole("button", { name: "Create file" }).click();
  await dialog.getByText("dbo.vw_Sales is already in the project, in dbo/Views/vw_Sales.sql.").waitFor();
  await dialog.getByRole("button", { name: "Cancel" }).click();
});

test("a SQL analytics endpoint has no Project tab", { skip }, async () => {
  await page().evaluate(() => openWarehouseQuery({ kind: "endpoint", workspace_id: "ws1", workspace_name: "Sales Dev", item_id: "lh1", name: "LH_Gold" }));
  await page().waitForSelector(".warehouse-page .sql-edit .cm-editor");
  assert.strictEqual(await projectTab().count(), 0);
});

test("nothing on the page threw", { skip }, async () => {
  assert.deepStrictEqual(app.errors, []);
});
