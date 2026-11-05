// #1673: axe on the dialogs that had no accessibility case when the guard came in (ui.test.cjs, "every
// dialog has an axe case"), most-used first, in batches. Each case opens one dialog the way its button
// does, with test data, runs axe on it (WCAG 2.1 A and AA, serious and critical findings) and closes it.
// Nothing reaches Fabric or a real engine but the profile read: the engine calls are mocks.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp, axeSource, axeFindings } = require("./harness.cjs");
// axe covers dialogs (#1673, ui.test.cjs): the cases below run axe while these are open.
// axe covers dialogs: notebook-versions.js:nbVersions, meta.js:showRun, settings.js:showWhatsNew, settings.js:checkForUpdates
// axe covers dialogs: notebook-files.js:nbSaveCopyDialog, notebook-files.js:nbNewFolderDialog, notebook-files.js:nbRenameFolderDialog
// axe covers dialogs: notebook-files.js:nbMoveToFolderDialog, notebook-comments.js:commentDialog, notebook.js:cancelRun
// axe covers dialogs: app.js:openDataItems, notebook-formats.js:nbfExport, meta.js:newEntityDialog, sqltemplates.js:openSqlTemplates
// axe covers dialogs: git.js:gitNewDialog, git.js:nbChoosePlace, profile.js:editProfileIdentity
// axe covers dialogs: notebook-versions.js:nbVersionView, notebook-versions.js:nbVersionCompare, app.js:nbDiskConflict
// axe covers dialogs: notebook-files.js:nbRenameWarning, notebook-files.js:nbFolderRefusal, notebook-files.js:nbFabricChangeGate
// axe covers dialogs: notebook-files.js:nbOpenFileDialog, notebook-files.js:nbEditFromFabric, engines.js:engineSwitchDialog
// axe covers dialogs: engines.js:engineRemoteDialog, home-notebooks.js:homeRunNotebooks, sqlqueries.js:saveSqlQuery
// axe covers dialogs: schedules.js:editScheduleDialog, incidents.js:priorityIncident, meta.js:showGateError
// axe covers dialogs: workloads.js:confirmWorkloadAction, platform.js:requestDialog, settings.js:usageDialog, settings.js:writeTestDialog
// axe covers dialogs: settings.js:showMotionGallery, meta.js:newKeyTableDialog, meta.js:useKeyTableDialog, meta.js:pickEnvWorkspace
// axe covers dialogs: mesh.js:linkDialog, mesh.js:domainDialog, governance.js:assignDialog, platform.js:keyTableDialog
// axe covers dialogs: platform.js:sourceDialog, governance-checks.js:labelDialog, platform-notices.js:mailServerDialog, estate.js:measureDialog
// axe covers dialogs: vscode.js:pairingDialog, vscode.js:writeDialog, vscode.js:vscodeSetupDialog, integrations.js:capabilitiesDialog
// axe covers dialogs: integrations.js:setupDialog, entra-groups.js:entraLinkDialog, entra-groups.js:entraNamingDialog
// axe covers dialogs: governance-checks.js:useTemplateDialog, governance-checks.js:saveAsTemplateDialog, mesh.js:addMemberDialog
// axe covers dialogs: access-cache.js:protectAccessCache, platform.js:landingZoneDialog, platform.js:sourceRequestDialog
// axe covers dialogs: platform-notices.js:platformRouteDialog, estate.js:costSettingsDialog

let app;
const skip = (!playwright && "Playwright is not installed") || (!axeSource && "axe-core is not installed");
const page = () => app.page;

const AT = "2026-11-04T09:30:00Z";
const PLACES = { places: [{ root: null, label: "This PC" }, { root: "C:/repos/sales", label: "sales" }] };
const CELLS = [{ type: "code", cell_id: "c1", source: "df = spark.read.table('orders')", meta: {} },
  { type: "markdown", cell_id: "c2", source: "# Orders", meta: {} }];
const PROJECT = {
  path: "C:/mesh", name: "Mesh", environments: {}, templates: { bronze_csv: "CSV files to bronze", silver_merge: "Merge into silver" },
  layers: [{ name: "bronze", lakehouse: "LH_Bronze" }, { name: "silver", lakehouse: "LH_Silver" }],
  entities: [{ name: "bronze.orders" }, { name: "silver.orders" }],
};

before(async () => {
  if (skip) return;
  app = await openApp({
    engine: ["app_settings", "profile_get"],  // the profile as a new person's engine makes it
    mocks: {
      git_vscode_plan: () => ({ files: [{ path: ".vscode/settings.json", state: "new", label: "Settings", why: "Points VS Code at KratosForge" },
        { path: "AGENTS.md", state: "exists", label: "Agent notes", why: "Tells AI tools about the repo", text: "Use KratosForge." }] }),
      platform_domains: () => [{ id: "d1", name: "Sales", parent_id: null }, { id: "d2", name: "EMEA", parent_id: "d1" }],
      environment: () => ({ python: "Python 3 (test)", user: "ana@contoso.com", tenant: "t1" }),
      list_workspaces: () => [{ id: "w1", displayName: "Sales Dev", capacityId: "c1" }, { id: "w2", displayName: "Finance", capacityId: null }],
      list_lakehouses: () => [{ id: "l1", displayName: "LH_Bronze" }],
      list_notebooks: () => [{ name: "Daily sales" }, { name: "Orders" }, { name: "reports/Weekly" }],
      notebook_places: () => PLACES,
      notebook_folders: () => ({ folders: [{ path: "reports", name: "reports", place: null }, { path: "staging", name: "staging", place: null }] }),
      notebook_versions: () => ({ coalesce_minutes: 10, keep_days: 30, versions: [
        { id: "v2", at: AT, cells: 2, kind: "save", current: true },
        { id: "v1", at: "2026-11-03T16:00:00Z", cells: 1, kind: "checkpoint", label: "Before the join", current: false }] }),
      meta_run_detail: () => ({ entity: "silver.orders", started_at: AT, status: "failed", run_id: "r42", attempt: 2, metadata: "v3", seconds: 12,
        where: "local", message: "2 rows failed the not-null check on order_id.",
        steps: [{ name: "read", rows: 120, seconds: 3, detail: "bronze.orders" }, { name: "merge", rows: 118, seconds: 9, detail: "2 rows held back" }],
        dq: [{ check: "order_id not null", failed: 2, action: "stop" }, { check: "amount >= 0", failed: 0, action: "warn" }] }),
      interrupt_cell: () => ({ interrupted: true, stopped: false }),
      notebook_export: () => ({ path: "C:/exports/Daily sales.py", notes: [{ cell: 1, kind: "magic", what: "%%sql cell became a spark.sql call" },
        { cell: null, kind: "attachment", what: "The attached lakehouse isn't kept" }] }),
      sql_template_list: () => ({ templates: [], places: [] }),
      notebook_version: () => ({ cells: [{ type: "code", source: "df = spark.read.table('orders')" }, { type: "markdown", source: "# Orders" }] }),
      notebook_version_compare: () => ({ changes: [{ kind: "changed", position: 1, diff: ["-df = spark.read.table('order')", "+df = spark.read.table('orders')"] },
        { kind: "added", position: 2, diff: ["+# Orders"] }] }),
      save_notebook: () => ({ saved: true }),
      notebook_publish_check: () => ({ changed: true, item_id: "i1", published_at: "2026-11-03T16:00:00Z",
        fabric: [{ kind: "changed", position: 1, diff: ["-x = 1", "+x = 2"] }], mine: [{ kind: "changed", position: 2 }] }),
      notebook_fabric_check: () => ({ here: null, taken: false, cells: 4, lakehouse: { name: "LH_Bronze" }, notes: ["2 cells use %run, which runs here as a plain cell."] }),
      notebook_details: () => [{ name: "Daily sales", compute: "local", cells: 2 }, { name: "Orders", compute: "local", cells: 5 }],
    },
  });
  await page().waitForSelector("#rail-main .rail-item");
  await page().evaluate(([cells, project]) => {
    state.user = "ana@contoso.com";
    loadIntoState({ name: "Daily sales", cells });
    state.meta.project = project;
    state.release = { commit: "abc1234", notes: "Faster local runs.\nThe bell lists incidents.", history: [] };
    state.commit = "abc1234";
    gitPage.info = { installed: true, clone_folder: "C:/repos",
      kinds: { notebooks: { label: "Notebooks", holds: "Notebooks and SQL.", for: "For a team's notebooks." },
        metadata: { label: "Metadata project", holds: "Entities and layers.", for: "For Data mesh." } } };
    // The update check the shell answers (lib.rs check_for_update).
    const invoke = window.__TAURI__.core.invoke;
    window.__TAURI__.core.invoke = async (cmd, args) => (cmd === "check_for_update"
      ? { commit: "abc1234", latest: { channel: "stable", commit: "def5678", page: "https://example.test/release", note: "" } }
      : invoke(cmd, args));
  }, [CELLS, PROJECT]);
});

after(async () => { await app?.close(); });

// Each: the dialog (as the guard names it), its title (a pattern where it holds a local time) and how to open it. A dialog that waits on its caller (a
// promise) is opened without waiting.
const CASES = [
  ["notebook-versions.js:nbVersions", "Versions of Daily sales", () => { nbVersions(); }],
  ["meta.js:showRun", /^silver\.orders · /, () => { showRun("r42", "dev"); }],
  ["settings.js:showWhatsNew", "What's new in KratosForge", () => { showWhatsNew(true); }],
  ["settings.js:checkForUpdates", "Check for updates", () => { checkForUpdates(); }, "#dialog-root .dialog dl"],
  ["notebook-files.js:nbSaveCopyDialog", "Save a copy of Daily sales", () => { nbSaveCopyDialog("Daily sales"); }],
  ["notebook-files.js:nbNewFolderDialog", "New folder", () => { nbNewFolderDialog(null, () => {}); }],
  ["notebook-files.js:nbRenameFolderDialog", "Rename folder reports", () => { nbRenameFolderDialog({ name: "reports", path: "reports", place: null }, () => {}); }],
  ["notebook-files.js:nbMoveToFolderDialog", "Move Orders", () => { nbMoveToFolderDialog({ name: "Orders", place: null }); }],
  ["notebook-comments.js:commentDialog", "Comment on cell 1", () => { commentDialog("Comment on cell 1", "", "Comment"); }],
  ["notebook.js:cancelRun", "The cell is still running", () => { cancelRun(); }],
  ["app.js:openDataItems", "OneLake catalog", () => { openDataItems(); }],
  ["notebook-formats.js:nbfExport", "What Databricks source doesn't hold", () => { nbfExport("databricks"); }],
  ["meta.js:newEntityDialog", "New entity", () => { newEntityDialog(); }],
  ["sqltemplates.js:openSqlTemplates", "SQL from a template", () => { openSqlTemplates({ kind: "lakehouse", name: "LH_Bronze" }, () => {}); }],
  ["git.js:gitNewDialog", "New repository", () => { gitNewDialog("notebooks"); }],
  ["git.js:nbChoosePlace", "New notebook", () => { nbChoosePlace(); }],
  ["notebook-versions.js:nbVersionView", /^Saved, /, () => { nbVersionView({ id: "v1", at: "2026-11-03T16:00:00Z", cells: 2, kind: "auto" }); }],
  ["notebook-versions.js:nbVersionCompare", /^Now compared with /, () => { nbVersionCompare({ id: "v1", at: "2026-11-03T16:00:00Z", cells: 2, kind: "auto" }); }],
  ["app.js:nbDiskConflict", "This notebook changed outside KratosForge", () => { nbDiskConflict(state.notebook, { disk_stamp: 2, unreadable: false }); }],
  ["notebook-files.js:nbRenameWarning", "Moved, but some things didn't follow", () => {
    nbRenameWarning([{ notebook: "reports/Weekly", was: "Weekly", step: "schedule", error: "the schedule file is read-only" }]); }],
  ["notebook-files.js:nbFolderRefusal", "Can't delete reports", () => { nbFolderRefusal({ name: "reports" }, "It still holds 2 notebooks. Move or delete them first."); }],
  ["notebook-files.js:nbFabricChangeGate", "Daily sales changed in Fabric", () => { nbFabricChangeGate(state.notebook); }],
  ["notebook-files.js:nbOpenFileDialog", "Open a notebook file", () => {
    nbOpenFileDialog({ name: "orders.ipynb", path: "C:/Downloads/orders.ipynb", size: 4096 }, "{}", { name: "orders", cells: 4, code: 3, markdown: 1, outputs_dropped: true, notes: [] }); }],
  ["notebook-files.js:nbEditFromFabric", "Edit Daily load on this PC", () => {
    nbEditFromFabric({ name: "Daily load", workspace_id: "w1", item_id: "i9", workspace_name: "Sales Dev" }); }],
  ["engines.js:engineSwitchDialog", "Switch to DuckDB (this PC)?", () => {
    engineSwitchDialog(state.notebook, "Python (this PC)", "DuckDB (this PC)", { decisions: ["keep", "restart"] }); }],
  ["engines.js:engineRemoteDialog", "Use Fabric Spark?", () => {
    engineRemoteDialog(state.notebook, { id: "fabric-spark", label: "Fabric Spark" },
      { target: { name: "Sales Dev", id: "w1", state: "Active" }, risk: { outcome: "warning", lines: ["The capacity is 80% used this hour."] } }); }],
  ["home-notebooks.js:homeRunNotebooks", "Run 2 notebooks?", () => { homeRunNotebooks(["Daily sales", "Orders"]); }],
  ["sqlqueries.js:saveSqlQuery", "Save query", () => { saveSqlQuery({ kind: "lakehouse", name: "LH_Bronze" }, { name: "Query 1", sql: "SELECT 1" }, true); }],
  ["schedules.js:editScheduleDialog", "Edit the schedule for Daily sales", () => {
    editScheduleDialog({ title: "Edit the schedule for Daily sales", text: "Every day at 06:00", fabric: false, signedOut: false,
      config: { every: "day", at: "06:00" }, save: async () => ({}) }); }],
  ["incidents.js:priorityIncident", /^Change the priority of /, () => {
    priorityIncident({ id: "inc1", env: "prod", priority: "P2", kind: "late", subject: "silver.orders", title: "silver.orders is late" }, () => {}); }],
  ["meta.js:showGateError", "That was blocked", () => { showGateError("Blocked by 2 checks\n- silver.orders has no owner\n- The change window is closed"); }],
  // Batch 3: workloads, requests, settings and Data mesh.
  ["workloads.js:confirmWorkloadAction", "Run Daily sales?", () => {
    confirmWorkloadAction({ id: "nb:Daily sales", name: "Daily sales" }, { action: "run", label: "Run Daily sales", env: "dev" }, "Run", false,
      { pre: { allowed: true, workload: "Daily sales", capacity: false, writes: ["silver.orders"], reads: ["bronze.orders"], risk: null } }); }],
  ["platform.js:requestDialog", "Request a key table", () => { requestDialog("key_table", "supplier", () => {}); }],
  ["settings.js:usageDialog", "What's sent", () => { usageDialog({ today: "2026-11-05", install_id: null, waiting: [] }); }],
  ["settings.js:writeTestDialog", "Test a write to OneLake", () => { writeTestDialog(() => {}); }],
  ["settings.js:showMotionGallery", "Motion & effects", () => { showMotionGallery(); }],
  ["meta.js:newKeyTableDialog", "New key table", () => { newKeyTableDialog(() => {}); }],
  ["meta.js:useKeyTableDialog", "Use a key table", () => {
    useKeyTableDialog({ key_tables: [{ name: "customer" }], central_key_tables: [{ name: "customer" }, { name: "supplier", description: "One row per supplier" }] }, () => {}); }],
  ["meta.js:pickEnvWorkspace", "Workspace for Dev", () => { pickEnvWorkspace("dev", state.meta.project); }],
  ["mesh.js:linkDialog", "New link", () => {
    linkDialog({ project: { name: "Mesh" }, published: [{ id: "finance/customers", project: "Finance", status: "published",
      ports: [{ port: "customers_v1" }, { port: "customers_v2" }] }] }, null, () => {}); }],
  ["mesh.js:domainDialog", "New domain", () => {
    domainDialog({ domains: [{ id: "d1", name: "Sales", parent_id: null }, { id: "d2", name: "Finance", parent_id: null }] }, null, () => {}); }],
  ["governance.js:assignDialog", "Assign Table names", () => {
    assignDialog({ id: "pol1", name: "Table names", current_version: 2, versions: [{ version: 1, status: "published" }, { version: 2, status: "published" }],
      current: { definition: { scopes: ["enterprise", "domain", "project"], mandatory: false, params: { max_length: { value: 40, stricter: "lower" } } } } },
      { domains: [{ id: "d1", name: "Sales", parent_id: null }], projects: ["Mesh"], options: { enforcements: ["advisory", "warn", "block"], severities: ["low", "medium", "high"] } }, { admin: true, domains: [] }, null, () => {}); }],
  ["platform.js:keyTableDialog", "New key table", () => { keyTableDialog(() => {}, true); }],
  ["platform.js:sourceDialog", "Add a source", () => {
    sourceDialog(null, () => {}, ["SAP", "Salesforce"], [{ id: "shared", name: "Shared" }, { id: "hr", name: "HR", secure: true }]); }],
  ["governance-checks.js:labelDialog", "Label email?", () => { labelDialog({ column: "email", entity: "customer", labels: ["confidential", "restricted"] }, () => {}); }],
  ["platform-notices.js:mailServerDialog", "Company mail server", () => { mailServerDialog({ host: "smtp.contoso.com", port: 587, security: "starttls", sender: "kf@contoso.com" }, () => {}); }],
  ["estate.js:measureDialog", "Capacity use this month", () => {
    measureDialog("Capacity use this month", { classification: "measured", value: 12.5, unit: "cu_hours", metric: "cu_hours", methods: ["Sum of CU-seconds"], sources: ["Capacity metrics"] }, "USD"); }],
  // Batch 4: VS Code, tools, Entra groups, governance templates, members, landing and notices.
  ["vscode.js:pairingDialog", "Let VS Code run notebooks on this PC?", () => {
    pairingDialog({ id: "pr1", client: "KratosForge for VS Code", window: "sales-repo", code: "482 913" }); }],
  ["vscode.js:writeDialog", "VS Code wants to write to OneLake", () => {
    writeDialog({ id: "a1", notebook: "Daily sales", window: "sales-repo", target: "silver.orders" }); }],
  ["vscode.js:vscodeSetupDialog", "Set up sales for VS Code", () => { vscodeSetupDialog("C:/repos/sales", "sales", () => {}); }],
  ["integrations.js:capabilitiesDialog", "What tools can do", () => {
    capabilitiesDialog({ permissions: [{ id: "READ_METADATA", offered: true, allowed: true }, { id: "READ_DATA", offered: false, allowed: false }],
      capabilities: [{ title: "List lakehouses", description: "Names and tables", permission: "READ_METADATA", approval: "never" },
        { title: "Read rows", description: "Sample rows", permission: "READ_DATA", approval: "ask" }] }); }],
  ["integrations.js:setupDialog", "Connect Copilot", () => {
    setupDialog({ id: "copilot", name: "Copilot", grants: ["READ_METADATA"], config: { vscode: { where: "In .vscode/mcp.json", text: "{}" } } }, "tok-123", "vscode"); }],
  ["entra-groups.js:entraLinkDialog", "Link a group to Sales readers", () => {
    entraLinkDialog({ label: "Sales readers", domain: "Sales", domain_id: "d1", role: "reader", env: "dev", suggested: "SALES-DEV-READERS" }, () => {}); }],
  ["entra-groups.js:entraNamingDialog", "Group naming", () => {
    entraNamingDialog({ pattern: "{prefix}-{domain}-{env}-{role}", prefix: "KF", case: "upper", domain_part: "id", words: {}, roles_off: [] }, true, () => {}); }],
  ["governance-checks.js:useTemplateDialog", "Use Table names", () => { useTemplateDialog({ id: "table-names", name: "Table names" }, null); }],
  ["governance-checks.js:saveAsTemplateDialog", "Save Table names as a template", () => {
    saveAsTemplateDialog({ id: "table-names", name: "Table names", current_version: 2 }); }],
  ["mesh.js:addMemberDialog", "Add member", () => { addMemberDialog(() => {}); }],
  ["access-cache.js:protectAccessCache", "Who may read the access cache", () => { protectAccessCache({ workspace_id: "w1" }); }],
  ["platform.js:landingZoneDialog", "New landing zone", () => { landingZoneDialog({ where: "the Landing workspace" }, () => {}); }],
  ["platform.js:sourceRequestDialog", "Request a source", () => { sourceRequestDialog("erp", () => {}); }],
  ["platform-notices.js:platformRouteDialog", "Add a route", () => { platformRouteDialog({ mail_server: null }, null, () => {}); }],
  ["estate.js:costSettingsDialog", "Cost estimate", () => { costSettingsDialog({ price_per_cu_hour: 0.18, currency: "USD", python_cu: 2, spark_cu: 8 }); }],
  ["profile.js:editProfileIdentity", "Edit profile", async () => { profilePage.data = await rpc("profile_get", {}); editProfileIdentity(); }],
];

for (const [key, title, open, ready = "#dialog-root .dialog"] of CASES) {
  test(`axe finds nothing serious in ${key}`, { skip }, async () => {
    await page().evaluate(() => closeDialog());
    await page().evaluate(open);
    await page().locator(ready).first().waitFor({ timeout: 5000 }).catch(() => assert.fail(`${key} didn't open`));
    const opened = await page().locator("#dialog-root .dialog").first().getAttribute("aria-label");
    assert.ok(title instanceof RegExp ? title.test(opened) : opened === title, `${key} opened "${opened}"`);
    assert.deepStrictEqual(await axeFindings(page(), "#dialog-root"), [], key);
    await page().evaluate(() => closeDialog());
  });
}

test("the dialogs drew without a script error", { skip }, () => {
  assert.deepStrictEqual(app.errors, []);
});
