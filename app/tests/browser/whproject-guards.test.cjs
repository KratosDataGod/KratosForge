// The Warehouse page's Project tab when things go wrong, in a real browser (see harness.cjs for how to
// run it): projects or files that can't be read, scripting, planning or writing that fails, a file
// that already holds another script, and Deploy while changes are off or the branch isn't pushed.
// The engine's project calls and the warehouse's catalog queries are all mocked.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp, sqlRunMocks } = require("./harness.cjs");

const WH = { kind: "warehouse", workspace_id: "ws1", workspace_name: "Sales Dev", item_id: "wh1", name: "WH_Gold" };
const MINE = { repo: "C:/repos/sales", folder: "WH_Gold.Warehouse", name: "WH_Gold", repo_label: "sales" };
const OTHER = { repo: "C:/repos/sales", folder: "WH_Old.Warehouse", name: "WH_Old", repo_label: "sales" };
const REPOS = [{ repo: "C:/repos/sales", label: "sales" }, { repo: "C:/repos/finance", label: "finance" }];
const FILES = { files: [{ path: "dbo/Tables/Sales.sql", kind: "table", schema: "dbo", name: "Sales" }], branch: "main", sqlproj: "WH_Gold.sqlproj" };
const PLAN = { counts: { added: 1, changed: 0, same: 0, skipped: 0 }, added: [], changed: [], same: [], skipped: [] };

// What each mocked call does now; a test sets it before acting.
// The projects list fails at first, so the Project tab opens on that failure.
const S = { listFail: true, projects: [OTHER], filesFail: false, modulesFail: false, dryFail: false, writeFail: false,
  writeOut: null, plan: null, planFail: false };
const boom = (msg) => { throw new Error(msg); };

let app;
const skip = !playwright && "Playwright is not installed";
const page = () => app.page;
const calls = (method) => app.calls.filter((c) => c.method === method);
const panel = () => page().locator(".wh-project");
// Waits until cond() holds (an engine call this side has seen), or fails after 5 s.
async function until(cond, what) {
  for (const end = Date.now() + 5000; !cond();) {
    if (Date.now() > end) throw new Error(`timed out waiting for ${what}`);
    await new Promise((r) => setTimeout(r, 10));
  }
}
const toastSays = (re) => page().waitForFunction((src) => new RegExp(src).test([...document.querySelectorAll(".toast")].map((t) => t.textContent).join("\n")), re.source);

function catalog({ query }) {
  const cols = (...names) => names.map((name) => ({ name, type: "nvarchar" }));
  if (/FROM sys\.sql_modules AS m\nJOIN/.test(query)) {
    if (S.modulesFail) throw new Error("The catalog query timed out.");
    return { columns: cols("schema_name", "object_name", "object_type", "definition"), rows: [["dbo", "vSales", "V", "CREATE VIEW dbo.vSales AS SELECT 1 AS x"]] };
  }
  if (/FROM sys\.schemas AS s WHERE/.test(query)) return { columns: cols("name"), rows: [] };
  if (/FROM sys\.columns/.test(query)) {
    return { columns: cols("schema_name", "object_name", "column_name", "type_name", "max_length", "precision", "scale", "is_nullable"),
      rows: [["dbo", "Sales", "Amount", "int", 4, 10, 0, 0]] };
  }
  if (/FROM sys\.objects/.test(query)) return { columns: cols("schema_name", "object_name", "object_type"), rows: [["dbo", "Sales", "U"], ["dbo", "vSales", "V"]] };
  return { columns: cols("x"), rows: [[1]] };
}

before(async () => {
  if (skip) return;
  const runs = sqlRunMocks(catalog);
  app = await openApp({
    engine: ["sql_format", "sql_dialects", "sql_analyze"],
    mocks: {
      ...runs,
      sql_start: (p) => ({ ...runs.sql_start(p), target: "warehouse" }),
      meta_project: () => ({ environments: { test: { workspace_id: "ws1" } } }),
      wh_projects_list: () => (S.listFail ? boom("The repositories couldn't be read.") : { repos: REPOS, projects: S.projects }),
      wh_project_files: () => (S.filesFail ? boom("The folder is locked by another program.") : FILES),
      wh_project_write: (p) => {
        if (p.dry_run) return S.dryFail ? boom("The project's folder is gone.") : PLAN;
        if (S.writeFail) boom("The disk is full.");
        return S.writeOut || PLAN;
      },
      wh_project_compare: () => ({ objects: [{ kind: "view", schema: "dbo", name: "vSales", state: "only_warehouse",
        warehouse_text: "CREATE VIEW dbo.vSales AS SELECT 1 AS x\n", detail: [] }], counts: { same: 0, changed: 0, only_warehouse: 1, only_project: 0 }, unread: [] }),
      wh_project_deploy_plan: () => (S.planFail ? boom("The project can't be planned.") : S.plan),
    },
  });
  await page().evaluate((wh) => openWarehouseQuery(wh), WH);
  await page().waitForSelector(".warehouse-page .sql-edit .cm-editor");
  app.errors.length = 0;
});

after(async () => { await app?.close(); });

test("projects that can't be looked for say why, and Try again finds the other project", { skip }, async () => {
  await page().getByRole("tab", { name: "Project" }).click();
  await panel().getByText("Could not look for projects: ").waitFor();
  assert.match(await panel().textContent(), /Could not look for projects: The repositories couldn't be read\./);
  S.listFail = false;
  await panel().getByRole("button", { name: "Try again" }).click();
  await panel().getByText("No WH_Gold.Warehouse folder").waitFor();
  assert.strictEqual(await panel().getByRole("combobox", { name: "Project" }).count(), 1, "the other project can be picked");
});

test("Import with no project yet plans again when another repository is picked", { skip }, async () => {
  await panel().getByRole("button", { name: "Import from WH_Gold" }).click();
  await page().getByRole("dialog").getByRole("button", { name: "Run in Fabric" }).click();
  const dialog = page().getByRole("dialog", { name: /Import WH_Gold into a new project/ });
  await dialog.waitFor();
  const dry = calls("wh_project_write").length;
  await dialog.getByRole("combobox", { name: "Repository" }).selectOption("C:/repos/finance");
  await until(() => calls("wh_project_write").length > dry, "the second plan");
  const last = calls("wh_project_write").at(-1).params;
  assert.deepStrictEqual([last.repo, last.create, last.dry_run], ["C:/repos/finance", "WH_Gold", true]);
  await dialog.getByRole("button", { name: "Cancel" }).click();
  await dialog.waitFor({ state: "detached" });
  assert.strictEqual(calls("wh_project_write").filter((c) => !c.params.dry_run).length, 0, "Cancel writes nothing");
});

test("a project whose files can't be read says so, and Refresh reads them", { skip }, async () => {
  S.filesFail = true;
  await panel().getByRole("combobox", { name: "Project" }).selectOption(`${OTHER.repo}|${OTHER.folder}`);
  await panel().getByText("Could not read the project: ").waitFor();
  assert.match(await panel().textContent(), /Could not read the project: The folder is locked by another program\./);
  S.filesFail = false;
  S.projects = [MINE, OTHER];
  await panel().getByRole("button", { name: "Refresh" }).click();
  await panel().getByText("Sales.sql").waitFor();
  assert.match(await panel().textContent(), /WH_Old\.Warehouse · sales · main/);
});

test("with two projects a picker switches between them", { skip }, async () => {
  const pick = panel().locator("select.wh-p-pick");
  await pick.waitFor();
  const reads = calls("wh_project_files").length;
  await pick.selectOption(`${MINE.repo}|${MINE.folder}`);
  await panel().getByText(/WH_Gold\.Warehouse · sales · main/).waitFor();
  assert.ok(calls("wh_project_files").length > reads);
  assert.strictEqual(calls("wh_project_files").at(-1).params.folder, "WH_Gold.Warehouse");
});

test("Import that can't script the warehouse says so and writes nothing", { skip }, async () => {
  S.modulesFail = true;
  await panel().getByRole("button", { name: "Import from WH_Gold" }).click();
  await toastSays(/Could not script WH_Gold: /);
  S.modulesFail = false;
  assert.strictEqual(calls("wh_project_write").filter((c) => !c.params.dry_run).length, 0);
  assert.strictEqual(await page().getByRole("dialog", { name: /Import WH_Gold/ }).count(), 0, "no import dialog");
});

test("Import that can't read the project says so before asking anything", { skip }, async () => {
  S.dryFail = true;
  await panel().getByRole("button", { name: "Import from WH_Gold" }).click();
  await toastSays(/Could not read the project: .*The project's folder is gone/);
  S.dryFail = false;
  assert.strictEqual(await page().getByRole("dialog", { name: /Import WH_Gold/ }).count(), 0);
});

test("Import whose write fails says Could not import and reads the project again", { skip }, async () => {
  S.writeFail = true;
  const reads = calls("wh_projects_list").length;
  await panel().getByRole("button", { name: "Import from WH_Gold" }).click();
  const dialog = page().getByRole("dialog", { name: /Import WH_Gold into WH_Gold\.Warehouse/ });
  await dialog.waitFor();
  await dialog.getByRole("button", { name: "Import" }).click();
  await toastSays(/Could not import: .*The disk is full/);
  S.writeFail = false;
  await until(() => calls("wh_projects_list").length > reads, "the project to be read again after the failed write");
});

test("taking the warehouse's version into a file that holds another script writes nothing and says so", { skip }, async () => {
  S.writeOut = { counts: { added: 0, changed: 0, same: 1, skipped: 0 }, added: [], changed: [], skipped: [],
    same: [{ path: "dbo/Views/vSales.sql", replaced: "no" }] };
  await panel().getByRole("button", { name: "Compare with WH_Gold" }).click();
  await page().getByRole("heading", { name: /Compare the project with WH_Gold/ }).waitFor();
  await page().getByRole("button", { name: "Take the warehouse's version" }).click();
  await toastSays(/dbo\/Views\/vSales\.sql already holds another script, so dbo\.vSales wasn't written/);
  assert.strictEqual(await page().getByRole("button", { name: "Only in warehouse 1" }).count(), 1, "the object keeps its state");
  assert.strictEqual(await page().getByRole("button", { name: "Same 0" }).count(), 1);
  S.writeOut = null;
});

test("Deploy while warehouse changes are off says where to turn them on, or that IT turned them off", { skip }, async () => {
  await page().evaluate(() => { state.settings = { ...state.settings, sql_warehouse_writes: { value: false, source: "user" } }; });
  await page().getByRole("button", { name: "Deploy to WH_Gold…" }).click();
  await toastSays(/Warehouse changes are turned off\. Turn them on in Settings > Advanced > SQL queries to deploy\./);
  await page().evaluate(() => { state.settings.sql_warehouse_writes = { value: false, source: "policy" }; });
  await page().getByRole("button", { name: "Deploy to WH_Gold…" }).click();
  await toastSays(/Your organization has turned warehouse changes off/);
  assert.strictEqual(calls("wh_project_deploy_plan").length, 0, "nothing is planned");
  await page().evaluate(() => { state.settings.sql_warehouse_writes = { value: true, source: "user" }; });
});

test("a deploy that can't be planned says so and shows no dialog", { skip }, async () => {
  S.planFail = true;
  await page().getByRole("button", { name: "Deploy to WH_Gold…" }).click();
  await toastSays(/Could not plan the deploy: .*The project can't be planned/);
  S.planFail = false;
  assert.strictEqual(await page().getByRole("dialog", { name: /Deploy/ }).count(), 0);
});

test("a Test deploy from a branch with no remote says to push it first and keeps Deploy off", { skip }, async () => {
  S.plan = { environment: "test", branch: "feature/x", upstream: null, ahead: 1, gated: true, changes: [{ action: "create", kind: "table",
    schema: "dbo", name: "orders", offered: true, ticked: true, held: true, committed: true, why: "new in the project", sql: "CREATE TABLE dbo.orders (id int);" }] };
  await page().getByRole("button", { name: "Deploy to WH_Gold…" }).click();
  const dialog = page().getByRole("dialog");
  await dialog.getByRole("alert").filter({ hasText: /deploys only what's pushed/ }).waitFor();
  assert.match(await dialog.getByRole("alert").first().textContent(),
    /Test deploys only what's pushed, and feature\/x has no remote branch yet\. Push it in Source control first\./);
  assert.strictEqual(await dialog.getByRole("button", { name: /^Deploy 1 change/ }).isDisabled(), true);
  await dialog.getByRole("button", { name: "Cancel" }).click();
  await dialog.waitFor({ state: "detached" });
  assert.strictEqual(calls("sql_start").filter((c) => !c.params.dry_run && c.params.allow).length, 0, "nothing ran");
  assert.deepStrictEqual(app.errors, []);
});
