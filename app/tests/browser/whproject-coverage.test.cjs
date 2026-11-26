// The warehouse as files in Git (whproject.js: Project tab, Import, New object, Compare and Deploy): the states and
// answers warehouse-project and warehouse-deploy don't reach. No repository yet, another project picked, a folder that
// can't be read and files that aren't objects; Import cancelled, failing at each step, skipping files, into a new
// project; New object's checks; Compare's labels, a difference that can't be shown and a warehouse version that can't
// be written; and Deploy turned off, failing, with nothing to do, gated by Git, and with a change that fails. The
// project's files and the warehouse are mocked with the engine's replies (warehouse_project_service.py); nothing
// reaches Fabric.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp, sqlRunMocks } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
const DEV = { kind: "warehouse", workspace_id: "ws1", workspace_name: "Sales Dev", item_id: "wh1", name: "WH_Gold" };
const NOENV = { kind: "warehouse", workspace_id: "ws5", item_id: "wh5", name: "WH_Gold" };
const PROD = { kind: "warehouse", workspace_id: "ws9", workspace_name: "Sales Prod", item_id: "wh9", name: "WH_Gold" };
const REPO = "C:\\src\\sales-platform";
const MINE = { repo: REPO, repo_label: "sales-platform", folder: "WH_Gold.Warehouse", name: "WH_Gold" };
const OTHER = { repo: "C:\\src\\finance", repo_label: "finance", folder: "Finance.Warehouse", name: "Finance" };
const file = (path, kind, schema, name, extra = {}) => ({ path, git: "", version: `v-${path}`, kind, schema, name, problem: "", ...extra });
const FILES = [file("dbo/Tables/DimCustomer.sql", "table", "dbo", "DimCustomer")];

const world = {};
function reset() {
  Object.assign(world, {
    fail: {}, catalogFail: null,
    projects: { repos: [{ repo: REPO, label: "sales-platform" }], projects: [MINE] },
    files: { [MINE.folder]: { branch: "main", files: FILES, sqlproj: true, platform: true, note: "" },
      [OTHER.folder]: { branch: "dev", files: [], sqlproj: true, platform: true, note: "" } },
    write: null, compare: null, deploy: null, changes: [], failRun: false,
  });
}
reset();

const counts = (o) => ({ added: o.added.length, changed: o.changed.length, same: o.same.length, skipped: o.skipped.length });
const writeReply = (p, o) => ({ repo: p.repo, folder: p.folder || `${p.create}.Warehouse`, dry_run: !!p.dry_run, created: !p.folder && !p.dry_run,
  added: [], changed: [], same: [], skipped: [], ...o, counts: counts({ added: [], changed: [], same: [], skipped: [], ...o }) });

function catalog({ query }) {
  const cols = (...names) => names.map((name) => ({ name, type: "nvarchar" }));
  if (/FROM sys\.sql_modules AS m\nJOIN/.test(query)) {
    return { columns: cols("schema_name", "object_name", "object_type", "definition"), rows: [
      ["dbo", "vw_Sales", "V", "CREATE VIEW dbo.vw_Sales AS SELECT 1 AS x"], ["dbo", "fn_Odd", "XX", "CREATE FUNCTION dbo.fn_Odd() RETURNS int AS BEGIN RETURN 1 END"],
      ["dbo", "vw_Empty", "V", "  "]] };
  }
  if (/FROM sys\.schemas AS s WHERE/.test(query)) return { columns: cols("name"), rows: [["staging"]] };
  if (/FROM sys\.columns/.test(query)) {
    return { columns: cols("schema_name", "object_name", "column_name", "type_name", "max_length", "precision", "scale", "is_nullable"),
      rows: [["dbo", "DimCustomer", "CustomerKey", "int", 4, 10, 0, 0]] };
  }
  if (/FROM sys\.objects/.test(query)) {
    if (world.catalogFail) throw new Error(world.catalogFail);
    return { columns: cols("schema_name", "object_name", "object_type"), rows: [["dbo", "DimCustomer", "U"], ["dbo", "NoColumns", "U"], ["dbo", "vw_Sales", "V"]] };
  }
  return { columns: cols("x"), rows: [[1]] };
}

function failing(method) {
  const why = world.fail[method];
  if (why) { delete world.fail[method]; throw new Error(why); }
}

let app;
const page = () => app.page;
const calls = (m) => app.calls.filter((c) => c.method === m).map((c) => c.params);
const panel = () => page().locator(".wh-project");
const dialogNamed = (name) => page().getByRole("dialog", { name });
const toastText = () => page().evaluate(() => document.getElementById("toast").textContent);
const frames = (n = 3) => page().evaluate((n) => new Promise((r) => { let i = 0; const f = () => (++i >= n ? r() : requestAnimationFrame(f)); requestAnimationFrame(f); }), n);
async function until(check, what) {
  for (let i = 0; i < 300; i++) { if (await check()) return; await frames(1); }
  assert.fail(`timed out waiting for ${what}`);
}
const toastSays = (re) => until(async () => re.test(await toastText()), String(re));

before(async () => {
  if (skip) return;
  const runs = sqlRunMocks(catalog);
  const states = new Map();
  app = await openApp({
    engine: ["sql_format", "sql_analyze", "sql_queries_list"],
    mocks: {
      ...runs,
      meta_project: () => ({ environments: { dev: { workspace_id: "ws1" }, prod: { workspace_id: "ws9" } } }),
      sql_start: (p) => {
        if (p.dry_run) return { dry_run: true, permission: "warehouse_write", refused: null, statements: [] };
        if (p.confirm) {
          if (world.failRun) throw new Error("The connection to Fabric was reset.");
          const id = `chg${world.changes.length + 1}`;
          world.changes.push(p);
          states.set(id, { id, state: "completed", change: true, affected: 0, committed: true, target: "warehouse" });
          return { id, state: "running", target: "warehouse" };
        }
        return { ...runs.sql_start(p), target: "warehouse" };
      },
      sql_status: (p) => states.get(p.execution) || { ...runs.sql_status(p), target: "warehouse" },
      wh_projects_list: () => { failing("wh_projects_list"); return world.projects; },
      wh_project_files: ({ repo, folder }) => { failing("wh_project_files"); return { repo, folder, ...world.files[folder] }; },
      wh_project_read: ({ path }) => ({ path, text: "CREATE SCHEMA [staging];\nGO\n", version: "v1" }),
      wh_project_write: (p) => { failing("wh_project_write"); return writeReply(p, world.write ? world.write(p) : {}); },
      wh_project_compare: ({ repo, folder }) => ({ repo, folder, ...world.compare }),
      wh_project_diff: () => { failing("wh_project_diff"); return { rows: [{ op: "changed", left: "SELECT 1 AS x", right: "SELECT 2 AS x" }] }; },
      wh_project_deploy_plan: ({ repo, folder }) => { failing("wh_project_deploy_plan"); return { repo, folder, unread: [], ...world.deploy() }; },
    },
  });
  await page().evaluate((wh) => openWarehouseQuery(wh), DEV);
  await page().waitForSelector(".warehouse-page .sql-edit .cm-editor");
  app.errors.length = 0;
});
after(async () => { await app?.close(); });

// The Project tab, read again from the engine, for ref.
async function showProject(ref = DEV) {
  await page().evaluate((ref) => {
    closeDialog();
    whProjects.state = "idle";
    sqlPage.whProjectFiles = {};
    sqlPage.whProjectPick = {};
    sqlPage.whCompare = null;
    sqlPage.whCompareShown = false;
    if (sqlPage.ref?.workspace_id !== ref.workspace_id) openWarehouseQuery(ref); else renderQuery();
  }, ref);
  await page().waitForSelector(".warehouse-page .sql-edit .cm-editor");
  await page().getByRole("tab", { name: "Project" }).click();
  await panel().waitFor();
  app.calls.length = 0;
}

// Answers the once-per-warehouse "Run in Fabric" question when it's asked.
async function runInFabric(ok = true) {
  const d = dialogNamed(/Run this query in Fabric on WH_Gold\?/);
  if (await d.isVisible().catch(() => false)) await d.getByRole("button", { name: ok ? "Run in Fabric" : "Cancel" }).click();
}

test("with no repository the tab says to open one; with only another project it can be picked; projects can be switched", { skip }, async () => {
  reset();
  world.projects = { repos: [], projects: [] };
  await showProject();
  await panel().getByText("Open or clone a Git repository in Source control first: the project's files go there.").waitFor();
  assert.strictEqual(await panel().getByRole("button", { name: "Import from WH_Gold" }).count(), 0);

  world.projects = { repos: [{ repo: OTHER.repo, label: "finance" }], projects: [OTHER] };
  await showProject();
  await panel().getByText(/No WH_Gold\.Warehouse folder in the repositories opened in Source control/).waitFor();
  await panel().getByLabel("Project").selectOption("");  // Choose...: nothing to read
  assert.deepStrictEqual(calls("wh_project_files"), []);
  await panel().getByLabel("Project").selectOption(`${OTHER.repo}|${OTHER.folder}`);
  await panel().getByText("No .sql files yet. Import from the warehouse, or add a New object.").waitFor();
  assert.deepStrictEqual(calls("wh_project_files"), [{ repo: OTHER.repo, folder: OTHER.folder }]);
  assert.match(await panel().locator(".wh-p-where").innerText(), /Finance\.Warehouse · finance · dev/);

  world.projects = { repos: [{ repo: REPO, label: "sales-platform" }, { repo: OTHER.repo, label: "finance" }], projects: [OTHER, MINE] };
  await showProject();
  const pick = panel().locator("select.wh-p-pick");
  await pick.waitFor();
  assert.deepStrictEqual(await pick.locator("option").allInnerTexts(), ["WH_Gold.Warehouse · sales-platform", "Finance.Warehouse · finance"]);
  await pick.selectOption(`${OTHER.repo}|${OTHER.folder}`);
  await until(async () => /Finance\.Warehouse · finance/.test(await panel().locator(".wh-p-where").innerText()), "the other project");
  assert.deepStrictEqual(app.errors, []);
});

test("a project's files: a folder that can't be read, and files that aren't objects, changed in Git or of no known kind", { skip }, async () => {
  reset();
  world.fail.wh_project_files = "The folder WH_Gold.Warehouse isn't in the repository any more.";
  await showProject();
  await panel().getByText("Could not read the project: The folder WH_Gold.Warehouse isn't in the repository any more.").waitFor();
  world.files[MINE.folder] = { branch: null, sqlproj: false, platform: false, note: "The project has more than 2000 .sql files; the first 2000 are listed.",
    files: [file("dbo/Tables/DimCustomer.sql", "table", "dbo", "DimCustomer", { git: "M" }),
      { path: "bad.sql", git: "", version: "", problem: "It couldn't be read as UTF-8 text." },
      { path: "notes.sql", git: "A", version: "v-n", problem: "It doesn't create a table, view, procedure, function or schema." },
      file("dbo/Synonyms/syn_Sales.sql", "synonym", "dbo", "syn_Sales")] };
  await panel().getByRole("button", { name: "Refresh" }).click();
  await panel().getByText("notes.sql").waitFor();
  const text = await panel().innerText();
  assert.match(text, /This folder has no \.sqlproj, so Fabric may not read it as a warehouse project\./);
  assert.match(text, /The project has more than 2000 \.sql files; the first 2000 are listed\./);
  assert.doesNotMatch(await panel().locator(".wh-p-where").innerText(), /·.*·/);  // no branch
  assert.strictEqual(await panel().locator(".wh-p-git.changed").getAttribute("title"), "Changed, not committed yet");
  assert.strictEqual(await panel().locator(".wh-p-git.added").getAttribute("title"), "New, not committed yet");
  assert.ok(await panel().getByRole("button", { name: /bad\.sql/ }).isDisabled());
  assert.ok(await panel().getByRole("button", { name: /notes\.sql/ }).isEnabled());
  assert.strictEqual(await panel().locator(".wh-q-from").count(), 2);
  assert.strictEqual(await panel().getByRole("button", { name: /syn_Sales\.sql/ }).getAttribute("title"), "File dbo.syn_Sales · dbo/Synonyms/syn_Sales.sql");
  assert.deepStrictEqual(app.errors, []);
});

test("Import stops when Run in Fabric is cancelled, the catalog can't be read, or the project can't be read", { skip }, async () => {
  reset();
  await showProject();
  await panel().getByRole("button", { name: "Import from WH_Gold" }).click();
  await runInFabric(false);
  await frames(5);
  assert.deepStrictEqual(calls("wh_project_write"), []);

  world.catalogFail = "Login failed for user.";
  await panel().getByRole("button", { name: "Import from WH_Gold" }).click();
  await runInFabric();
  await toastSays(/^Could not script WH_Gold: /);
  assert.deepStrictEqual(calls("wh_project_write"), []);
  world.catalogFail = null;

  world.fail.wh_project_write = "The folder WH_Gold.Warehouse isn't in the repository any more.";
  await panel().getByRole("button", { name: "Import from WH_Gold" }).click();
  await toastSays(/^Could not read the project: The folder WH_Gold\.Warehouse isn't in the repository any more\./);
  assert.strictEqual(calls("wh_project_write").length, 1);
  assert.deepStrictEqual(app.errors, []);
});

test("Import's summary names skipped files and the first six that differ; cancelled it writes nothing; a failed write says so", { skip }, async () => {
  reset();
  await showProject();
  const differ = ["A", "B", "C", "D", "E", "F", "G"].map((n) => ({ kind: "view", name: `dbo.vw_${n}`, path: `dbo/Views/vw_${n}.sql`, replaced: "no" }));
  world.write = (p) => ({ added: [{ kind: "schema", name: "staging", path: "Security/staging.sql" }], changed: differ, same: [],
    skipped: [{ kind: "view", name: "queryinsights.x", why: "Fabric keeps it" }, { kind: "table", name: "dbo.Sales", why: "case" },
      { kind: "table", name: "dbo.sales", why: "case" }, { kind: "table", name: "dbo.SALES", why: "case" }].slice(0, p.dry_run ? 4 : 1) });
  await panel().getByRole("button", { name: "Import from WH_Gold" }).click();
  await runInFabric();
  const d = dialogNamed("Import WH_Gold into WH_Gold.Warehouse");
  await d.waitFor();
  const objects = calls("wh_project_write")[0].objects;
  assert.deepStrictEqual(objects.map((o) => `${o.kind} ${o.schema}.${o.name}`), ["schema staging.staging", "table dbo.DimCustomer", "table dbo.NoColumns",
    "view dbo.vw_Sales", "function dbo.fn_Odd"]);
  assert.match(objects[2].text, /CREATE TABLE \[dbo\]\.\[NoColumns\]/);
  const text = await d.innerText();
  assert.match(text, /5 objects scripted from WH_Gold's catalog\./);
  assert.match(text, /1 new file\s*7 files that differ from the warehouse\s*0 already the same\s*4 skipped: queryinsights\.x \(Fabric keeps it\); dbo\.Sales \(case\); dbo\.sales \(case\)$/m);
  assert.match(text, /Differ: dbo\.vw_A, dbo\.vw_B, dbo\.vw_C, dbo\.vw_D, dbo\.vw_E, dbo\.vw_F, \.\.\./);
  await d.getByRole("button", { name: "Cancel" }).click();
  await frames(5);
  assert.strictEqual(calls("wh_project_write").length, 1);

  await panel().getByRole("button", { name: "Import from WH_Gold" }).click();
  await d.waitFor();
  world.fail.wh_project_write = "PermissionError: [Errno 13] Permission denied: 'Security'";
  await d.getByRole("button", { name: "Import" }).click();
  await toastSays(/^Could not import: /);

  await panel().getByRole("button", { name: "Import from WH_Gold" }).click();
  await d.waitFor();
  await d.getByRole("button", { name: "Import" }).click();
  await toastSays(/^Imported WH_Gold: 1 new, 0 replaced, 0 the same, 1 skipped\.$/);
  assert.deepStrictEqual(app.errors, []);
});

test("Import into a new project reads the plan again for the repository picked, and the new project is then shown", { skip }, async () => {
  reset();
  world.projects = { repos: [{ repo: REPO, label: "sales-platform" }, { repo: OTHER.repo, label: "finance" }], projects: [] };
  world.write = (p) => ({ added: p.repo === REPO ? [{ kind: "schema", name: "staging" }] : [{ kind: "schema", name: "staging" }, { kind: "table", name: "dbo.DimCustomer" }] });
  await showProject();
  await panel().getByRole("button", { name: "Import from WH_Gold" }).click();
  await runInFabric();
  const d = dialogNamed("Import WH_Gold into a new project");
  await d.waitFor();
  assert.match(await d.innerText(), /1 new file\b/);
  world.fail.wh_project_write = "The repository is busy.";  // a failed read leaves the summary as it was
  await d.getByLabel("Repository").selectOption(OTHER.repo);
  await until(async () => calls("wh_project_write").length === 2, "the failed read");
  assert.match(await d.innerText(), /1 new file\b/);
  await d.getByLabel("Repository").selectOption(REPO);
  await d.getByLabel("Repository").selectOption(OTHER.repo);
  await until(async () => /2 new files/.test(await d.innerText()), "the plan for finance");
  assert.deepStrictEqual(calls("wh_project_write").at(-1), { repo: OTHER.repo, create: "WH_Gold", objects: calls("wh_project_write")[0].objects, dry_run: true });
  world.projects = { repos: world.projects.repos, projects: [{ ...MINE, repo: OTHER.repo, repo_label: "finance" }] };
  world.files[MINE.folder] = { branch: "main", files: FILES, sqlproj: true, platform: true, note: "" };
  await d.getByRole("button", { name: "Import" }).click();
  await toastSays(/^Imported WH_Gold: 2 new, 0 replaced, 0 the same\.$/);
  assert.strictEqual(await page().evaluate((k) => sqlPage.whProjectPick[k], await page().evaluate((r) => sqlRefKey(r), DEV)), `${OTHER.repo}|WH_Gold.Warehouse`);
  await until(async () => /WH_Gold\.Warehouse · finance/.test(await panel().innerText()), "the new project");
  assert.deepStrictEqual(app.errors, []);
});

test("New object: a schema needs only a name, a name is needed, and a skipped or existing object says why in the dialog", { skip }, async () => {
  reset();
  await showProject();
  await panel().getByRole("button", { name: "New object" }).click();
  const d = dialogNamed("New object");
  await d.getByRole("combobox", { name: "Kind" }).selectOption("schema");
  assert.ok(await d.getByRole("textbox", { name: "Schema" }).isDisabled());
  await d.getByRole("button", { name: "Create file" }).click();
  await d.getByText("Give it a schema and a name.").waitFor();
  assert.deepStrictEqual(calls("wh_project_write"), []);
  await d.getByRole("textbox", { name: "Name" }).fill("queryinsights");
  world.write = () => ({ skipped: [{ kind: "schema", name: "queryinsights", why: "Fabric makes and keeps this schema itself." }] });
  await d.getByRole("button", { name: "Create file" }).click();
  await d.getByText("Fabric makes and keeps this schema itself.").waitFor();
  assert.deepStrictEqual(calls("wh_project_write")[0].objects, [{ kind: "schema", schema: "queryinsights", name: "queryinsights", text: "CREATE SCHEMA [queryinsights];\nGO\n" }]);
  await d.getByRole("textbox", { name: "Name" }).fill("staging");
  world.write = () => ({ same: [{ kind: "schema", name: "staging", path: "Security/staging.sql" }] });
  await d.getByRole("button", { name: "Create file" }).click();
  await d.getByText("staging is already in the project, in Security/staging.sql.").waitFor();
  await d.getByRole("textbox", { name: "Name" }).fill("landing");
  world.write = () => ({ added: [{ kind: "schema", name: "landing", path: "Security/landing.sql" }] });
  world.files[MINE.folder] = { ...world.files[MINE.folder], files: [...FILES, file("Security/landing.sql", "schema", "landing", "landing", { git: "A" })] };
  await d.getByRole("button", { name: "Create file" }).click();
  await page().getByRole("tab", { name: "landing" }).waitFor();
  assert.deepStrictEqual(calls("wh_project_read"), [{ repo: REPO, folder: MINE.folder, path: "Security/landing.sql" }]);
  assert.deepStrictEqual(app.errors, []);
});

// The engine's compare rows (warehouse_compare.compare).
const VIEW_W = "CREATE VIEW dbo.vw_Sales AS SELECT 1 AS x\nGO\n";
function compareResult() {
  return {
    objects: [
      { kind: "view", schema: "dbo", name: "vw_Sales", state: "changed", path: "dbo/Views/vw_Sales.sql", warehouse_text: VIEW_W,
        project_text: "CREATE VIEW dbo.vw_Sales AS SELECT 2 AS x\nGO\n", detail: [] },
      { kind: "synonym", schema: "dbo", name: "syn_Old", state: "only_warehouse", warehouse_text: "CREATE SYNONYM dbo.syn_Old FOR dbo.x\nGO\n" },
      { kind: "view", schema: "dbo", name: "vw_New", state: "only_project", path: "dbo/Views/vw_New.sql", project_text: "CREATE VIEW dbo.vw_New AS SELECT 3 AS z\n\n" },
    ],
    counts: { changed: 1, only_project: 1, only_warehouse: 1, same: 0 },
    unread: [1, 2, 3, 4].map((i) => ({ path: `x${i}.sql`, why: "It doesn't create a table, view, procedure, function or schema." })),
  };
}

async function openCompare(ref = DEV) {
  await panel().getByRole("button", { name: "Compare with WH_Gold" }).click();
  await runInFabric();
  await page().locator(".wh-cmp").waitFor();
}

test("Compare labels its environment and groups, shows only-project text, and says when a difference or a group is empty", { skip }, async () => {
  reset();
  world.files[MINE.folder] = { ...world.files[MINE.folder], branch: null };
  world.compare = compareResult();
  await showProject();
  world.fail.wh_project_diff = "Each side can be up to 1024 KB.";
  await openCompare();
  const cmp = page().locator(".wh-cmp");
  assert.match(await cmp.locator("h2").innerText(), /^Compare the project with WH_Gold \(Dev\)$/);
  assert.match(await cmp.innerText(), /Not compared: x1\.sql \(.*\); x2\.sql \(.*\); x3\.sql \(.*\), and 1 more\./);
  await toastSays(/^Could not show the difference: Each side can be up to 1024 KB\./);
  assert.deepStrictEqual(await cmp.locator(".wh-cmp-cols div").allInnerTexts(), ["In WH_Gold now", "In the project"]);
  assert.strictEqual(await cmp.locator(".wh-cmp-row").count(), 0);
  await cmp.getByRole("button", { name: /dbo\.vw_New/ }).click();
  await until(async () => (await cmp.locator(".wh-cmp-line.added").count()) === 1, "the only-project lines");
  assert.match(await cmp.innerText(), /Deploy to WH_Gold creates it in the warehouse\./);
  await cmp.getByRole("button", { name: "Same 0" }).click();
  await cmp.getByText("Nothing in this group.").waitFor();
  await cmp.getByRole("button", { name: "All 3" }).click();

  world.compare = { objects: [], counts: { changed: 0, only_project: 0, only_warehouse: 0, same: 0 }, unread: [] };
  await cmp.getByRole("button", { name: "Compare again" }).click();
  await cmp.getByText("Nothing to show.").waitFor();
  assert.strictEqual(await cmp.locator(".wh-cmp-chips").innerText().then((t) => t.replace(/\s+/g, " ")), "All 0 Changed 0 Only in project 0 Only in warehouse 0 Same 0");
  assert.deepStrictEqual(app.errors, []);
});

test("Take the warehouse's version says when it was skipped or the file holds another script, and is cancelled by Replace's Cancel", { skip }, async () => {
  reset();
  world.compare = compareResult();
  await showProject();
  await openCompare();
  const cmp = page().locator(".wh-cmp");
  await cmp.getByRole("button", { name: "Take the warehouse's version" }).click();
  const replace = dialogNamed("Replace dbo/Views/vw_Sales.sql?");
  await replace.getByRole("button", { name: "Cancel" }).click();
  assert.deepStrictEqual(calls("wh_project_write"), []);
  world.write = () => ({ changed: [{ kind: "view", name: "dbo.vw_Sales", path: "dbo/Views/vw_Sales.sql", replaced: "no" }] });
  await cmp.getByRole("button", { name: "Take the warehouse's version" }).click();
  await replace.getByRole("button", { name: "Replace" }).click();
  await toastSays(/^dbo\/Views\/vw_Sales\.sql already holds another script, so dbo\.vw_Sales wasn't written\. Open the file to see what's there\.$/);
  assert.deepStrictEqual(calls("wh_project_write"), [{ repo: REPO, folder: MINE.folder, replace: true,
    objects: [{ kind: "view", schema: "dbo", name: "vw_Sales", text: VIEW_W }] }]);

  await cmp.getByRole("button", { name: /dbo\.syn_Old/ }).click();
  world.write = () => ({ skipped: [{ kind: "synonym", name: "dbo.syn_Old", why: "It isn't a table, view, procedure, function or schema with a name." }] });
  await cmp.getByRole("button", { name: "Take the warehouse's version" }).click();
  await toastSays(/^Could not write dbo\.syn_Old: It isn't a table, view, procedure, function or schema with a name\.$/);
  world.write = () => ({});
  await page().evaluate(() => toastClose());  // the error toast stays until closed
  await cmp.getByRole("button", { name: "Take the warehouse's version" }).click();
  await toastSays(/^Could not write dbo\.syn_Old: it was skipped$/);
  assert.deepStrictEqual(app.errors, []);
});

// The engine's deploy plan (wh_project_deploy_plan), for the environment env.
const change = (o) => ({ kind: "view", schema: "dbo", name: "vw_Sales", path: "dbo/Views/vw_Sales.sql", detail: [], precondition: null,
  action: "alter", sql: "CREATE OR ALTER VIEW dbo.vw_Sales AS SELECT 2 AS x", ticked: true, offered: true, why: "definition changes",
  committed: true, git: "", ...o });
const plan = (o) => ({ branch: "main", commit: "0123456789abcdef", upstream: "origin/main", ahead: 0, gated: false, environment: "dev", uncommitted: [],
  changes: [change()], ...o });

test("Deploy says when warehouse changes are off, when the plan can't be made, and when there's nothing to deploy", { skip }, async () => {
  reset();
  world.compare = compareResult();
  await showProject();
  await openCompare();
  const deploy = () => page().getByRole("button", { name: "Deploy to WH_Gold…" }).click();
  await page().evaluate(() => { state.settings = { ...state.settings, sql_warehouse_writes: { value: false, source: "policy" } }; });
  await deploy();
  await toastSays(/^Your organization has turned warehouse changes off, so this can't be deployed from here\./);
  await page().evaluate(() => { state.settings.sql_warehouse_writes = { value: false, source: "user" }; });
  await deploy();
  await toastSays(/^Warehouse changes are turned off\. Turn them on in Settings > Advanced > SQL queries to deploy\.$/);
  await page().evaluate(() => { delete state.settings.sql_warehouse_writes; });
  assert.deepStrictEqual(calls("wh_project_deploy_plan"), []);

  world.deploy = () => plan();
  world.fail.wh_project_deploy_plan = "Send up to 5000 objects at a time.";
  await deploy();
  await toastSays(/^Could not plan the deploy: Send up to 5000 objects at a time\./);
  world.deploy = () => plan({ changes: [] });
  await deploy();
  await toastSays(/^WH_Gold already matches WH_Gold\.Warehouse: nothing to deploy\.$/);
  assert.strictEqual(await page().getByRole("dialog").count(), 0);
  assert.deepStrictEqual(app.errors, []);
});

test("in Dev a ticked file that isn't committed is named, unticking it leaves nothing to deploy, and a change that can't be sent fails", { skip }, async () => {
  reset();
  world.compare = compareResult();
  world.deploy = () => plan({ changes: [change({ git: "M", committed: false })] });
  await showProject();
  await openCompare();
  await page().getByRole("button", { name: "Deploy to WH_Gold…" }).click();
  const d = dialogNamed("Deploy WH_Gold.Warehouse");
  await d.waitFor();
  assert.match(await d.innerText(), /From sales-platform, branch main at 0123456, to WH_Gold in Sales Dev \(Dev\)\./);
  assert.match(await d.innerText(), /1 of these file isn't committed: Dev deploys them as they are on this PC\./);
  const box = d.getByRole("checkbox", { name: "CREATE OR ALTER VIEW dbo.vw_Sales" });
  await box.uncheck();
  assert.ok(await d.getByRole("button", { name: "Deploy 0 changes" }).isDisabled());
  await box.check();
  world.failRun = true;
  await d.getByRole("button", { name: "Deploy 1 change" }).click();
  const done = dialogNamed("Deployed 0 of 1 changes");
  await done.waitFor();
  assert.match(await done.innerText(), /Failed: CREATE OR ALTER VIEW dbo\.vw_Sales \(The connection to Fabric was reset\.\)/);
  await done.getByRole("button", { name: "Close" }).last().click();
  await page().locator(".wh-cmp").waitFor();
  assert.deepStrictEqual(app.errors, []);
});

test("outside Dev a deploy waits for a commit, a remote branch or a push, and asks for the name", { skip }, async () => {
  reset();
  world.compare = compareResult();
  await showProject(NOENV);
  const held = (o) => change({ offered: false, ticked: false, held: true, why: "definition changes; held until it's committed and pushed", ...o });
  const gates = [
    [plan({ gated: true, environment: "none", branch: null, commit: null, changes: [held({ committed: false, git: "M" })] }),
      /No environment deploys only what's committed\. Commit dbo\/Views\/vw_Sales\.sql in Source control first\./],
    [plan({ gated: true, environment: "none", upstream: null, changes: [held({}), held({ name: "vw_Two" })] }),
      /No environment deploys only what's pushed, and main has no remote branch yet\. Push it in Source control first\./],
    [plan({ gated: true, environment: "none", upstream: null, branch: null, changes: [held({})] }),
      /this branch has no remote branch yet/],
    [plan({ gated: true, environment: "none", ahead: 1, changes: [held({})] }), /No environment deploys only what's pushed\. Push 1 commit in Source control first\./],
    [plan({ gated: true, environment: "none", ahead: 3, changes: [held({})] }), /Push 3 commits in Source control first\./],
  ];
  let first = true;
  for (const [p, says] of gates) {
    world.deploy = () => p;
    if (first) { await openCompare(NOENV); first = false; }
    await page().getByRole("button", { name: "Deploy to WH_Gold…" }).click();
    const d = dialogNamed("Deploy WH_Gold.Warehouse");
    await d.waitFor();
    assert.match(await d.locator(".wh-dep-gate").innerText(), says);
    await d.getByRole("button", { name: "Cancel" }).click();
  }
  world.deploy = () => plan({ gated: true, environment: "none", branch: null, commit: null });
  await page().getByRole("button", { name: "Deploy to WH_Gold…" }).click();
  const d = dialogNamed("Deploy WH_Gold.Warehouse");
  await d.waitFor();
  assert.match(await d.innerText(), /branch \(none\) at no commit yet, to WH_Gold in ws5 \(no environment, so it's treated like Prod\)\./);
  assert.match(await d.innerText(), /Type WH_Gold to deploy to it/);
  const go = d.getByRole("button", { name: "Deploy 1 change" });
  assert.ok(await go.isDisabled());
  await d.getByRole("textbox", { name: "Warehouse name" }).fill("WH_Gold");
  assert.ok(await go.isEnabled());
  await go.click();
  await dialogNamed("Deployed 1 change to WH_Gold").waitFor();
  assert.strictEqual(world.changes.length, 1);
  await dialogNamed("Deployed 1 change to WH_Gold").getByRole("button", { name: "Close" }).last().click();

  await showProject(PROD);
  world.deploy = () => plan({ gated: true, environment: "prod" });
  await openCompare(PROD);
  await page().getByRole("button", { name: "Deploy to WH_Gold…" }).click();
  await d.waitFor();
  assert.match(await d.innerText(), /Type WH_Gold to deploy to Prod/);
  assert.ok(await d.getByRole("button", { name: "Deploy 1 change to Prod" }).isDisabled());
  await d.getByRole("button", { name: "Cancel" }).click();
  assert.deepStrictEqual(app.errors, []);
});
