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
// axe covers dialogs: appstudio.js:appStudioImport, appstudio.js:appStudioPublishDialog, fabricstep.js:stepConfirm, git.js:gitSecretsDialog
// axe covers dialogs: meta.js:dictionaryImportDialog, estate.js:restoreDialog, integrations.js:editTool, platform.js:confirmShortcutEnvs
// axe covers dialogs: sqlchanges.js:whChangeConfirm, meta.js:createEnvWorkspace, whproject.js:whProjectNewObject
// axe covers dialogs: meta.js:pickKeysWorkspace, mesh.js:pickDesignationWorkspace, mesh.js:keysSetupDialog
// axe covers dialogs: dataops.js:columnLineage, df-dataflows.js:dfRunInFabric, meta.js:metaRiskGate, meta.js:readSourceDialog
// axe covers dialogs: governance.js:policyDialog, prove.js:showScorecard, xray.js:vacuumAll, optimize.js:optimizeMove
// axe covers dialogs: platform.js:landingSetupDialog, meta.js:ossieImportDialog, platform.js:confirmApproval, git.js:gitPlatformDeploy
// axe covers dialogs: optimize.js:optimizeSyncDetails, meta.js:profileEntity, platform.js:confirmCopyJob, product-items.js:productItemDialog
// axe covers dialogs: whmonitor.js:whMonitorBuild, xray.js:stagingDialog, optimize.js:optimizeCreateFlow, meta.js:moveSourcesToPlatform
// axe covers dialogs: dataops.js:quarantinePanel, fabricstep.js:insertVOrderCell, governance.js:governancePolicy, mesh.js:keysPlatformCard
// axe covers dialogs: platform.js:platformGlossaryTab, platform.js:requestTable, shortcuts.js:renderShortcuts
// axe covers dialogs: whproject.js:whProjectDeploy, whproject.js:whProjectImport

// "dialog: mode: finding" at 200% or in a contrast theme that needs more than a small fix (docs/ux/accessibility-zoom-contrast.md).
const KNOWN_ZOOM_CONTRAST = new Set([]);

let app;
const skip = (!playwright && "Playwright is not installed") || (!axeSource && "axe-core is not installed");
const page = () => app.page;

const AT = "2026-11-04T09:30:00Z";
const PLACES = { places: [{ root: null, label: "This PC" }, { root: "C:/repos/sales", label: "sales" }] };
const CELLS = [{ type: "code", cell_id: "c1", source: "df = spark.read.table('orders')", meta: {} },
  { type: "markdown", cell_id: "c2", source: "# Orders", meta: {} }];
const POLICY_DEF = { description: "Tables use snake_case", severity: "medium", enforcement: "warn", mandatory: false, scopes: ["enterprise", "domain"], params: {} };
const POLICY = { id: "pol1", name: "Table names", status: "published", category: "naming", owner: "Data office", current_version: 1, draft: null, history: [],
  versions: [{ version: 1, status: "published", published_at: AT, published_by: "ana@contoso.com", intact: true, hash: "abc" }],
  current: { version: 1, status: "published", published_at: AT, published_by: "ana@contoso.com", definition: POLICY_DEF } };
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
      xray_staging_preview: () => ({ ok: true, lakehouse_name: "LH_Staging", kept: "LH_Staging_2", quiet_days: 30, created: "2026-09-01T00:00:00Z",
        last_write: "2026-10-01T00:00:00Z", files: 12, bytes: 40960, dataflows: [{ name: "Orders flow", ok: true, last_refresh: "2026-11-04T00:00:00Z" }] }),
      meta_profile: () => ({ rows: 1200, seconds: 0.4, suggestions: [{ id: "s1", kind: "not_null", rule: "order_id is never empty", toml: "" }],
        columns: [{ name: "order_id", type: "BIGINT", nulls: 0, null_pct: 0, distinct: 1200, min: 1, max: 1200, issues: [], top: [] }] }),
      list_items: () => [{ id: "wh1", displayName: "WH_Sales", type: "Warehouse" }, { id: "sm1", displayName: "Sales model", type: "SemanticModel" }],
      platform_status: () => ({ landing: { central: false } }),
      optimize_status: () => ({ default_name: "FabricDesktop_Central" }),
      meta_sources_move: () => ({ moved: ["sap"], conflicts: [{ code: "crm", id: 4, why: "the platform uses id 4 for another source" }] }),
      git_platform_deploy: () => ({ commit: "0123456789abcdef", catalog_env: "prod", labels: {}, plan_id: "p1", steps: [], stops: [],
        removable: [], blocked: [], notes: ["Platform admins only."] }),
      meta_lineage: () => ({ columns: { order_id: { how: "copied", label: "direct" }, total: { how: "derived", label: "computed" } } }),
      workload_preflight: () => ({ code: "not_in_inventory" }),
      meta_risk_gate: () => ({ checks: [{ entity: "silver.orders", result: "warning", lines: ["The capacity is 80% used this hour."], needs_acknowledgement: true }],
        run: ["silver.orders"], held: {} }),
      list_capacities: () => [{ id: "c1", display: "F64 Sales", sku: "F64", state: "Active" }],
      git_vscode_plan: () => ({ files: [{ path: ".vscode/settings.json", state: "new", label: "Settings", why: "Points VS Code at KratosForge" },
        { path: "AGENTS.md", state: "exists", label: "Agent notes", why: "Tells AI tools about the repo", text: "Use KratosForge." }] }),
      platform_domains: () => [{ id: "d1", name: "Sales", parent_id: null }, { id: "d2", name: "EMEA", parent_id: "d1" }],
      environment: () => ({ python: "Python 3 (test)", user: "ana@contoso.com", tenant: "t1" }),
      appstudio_publish_check: () => ({ ready: true, problems: [], warnings: ["Visual titles aren't compiled yet."], name: "Sales overview",
        workspace_id: "w1", workspace_name: "Sales Dev", confirm_text: "Sales Dev", model: { id: "m1", name: "Sales model" },
        conflict: { id: "r1", ours: false }, unfinished: { at: AT, workspace_id: "w1", name: "Sales overview" }, fingerprint: "f1", parts: [] }),
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
      notebook_vorder_cell: () => ({ candidates: ["dbo.orders"] }),
      meta_replay: () => ({ status: "ok", rows_written: 2, held: 0, downstream: ["gold.sales"] }),
      governance_policy: () => POLICY,
      governance_overview: () => ({ me: { admin: true, domains_owned: [] }, options: { categories: { naming: "Naming" } } }),
      meta_keys_to_platform: () => ({ ok: false, tables: [{ table: "keys.customer", match: false, differ: ["42", "43"] }] }),
      platform_glossary: () => [{ term: "Active customer", definition: "Bought something in the last 12 months", domain_id: null, owner: "Data office" }],
      sql_start: () => ({ state: "completed", id: "e1" }),
      sql_page: () => ({ columns: ["name"], rows: [], has_next: false }),
      wh_project_write: () => ({ counts: { added: 1, changed: 0, same: 0, skipped: 0 }, skipped: [], changed: [] }),
      wh_project_deploy_plan: () => ({ environment: "dev", branch: "main", commit: "abc1234def", gated: false,
        changes: [{ action: "create", kind: "table", schema: "dbo", name: "orders", offered: true, ticked: true, why: "new in the project",
          sql: "CREATE TABLE dbo.orders (id int);" }] }),
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
    // Batch 8: a host for a card or tab drawn outside its page, a press on a button by its words, and a wait.
    window.__host = () => {
      document.getElementById("a11y-host")?.remove();
      return document.body.appendChild(Object.assign(document.createElement("div"), { id: "a11y-host" }));
    };
    window.__press = (words, root = document) => {
      const b = [...root.querySelectorAll("button")].find((x) => x.textContent.trim().startsWith(words));
      if (!b) throw new Error(`No button "${words}"`);
      b.click();
    };
    window.__until = async (fn) => {
      for (let i = 0; i < 50 && !fn(); i++) await new Promise((r) => setTimeout(r, 100));
    };
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
  ["settings.js:showMotionGallery", "Living Experience", () => { showMotionGallery(); }],
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
  // Batch 5: App Studio, Fabric step, secrets, dictionary, restore, tools, approvals and warehouse changes.
  ["appstudio.js:appStudioImport", "Import an app", () => { appStudioImport(); }],
  ["appstudio.js:appStudioPublishDialog", "Publish to Power BI", () => {
    appStudioPublishDialog({ valid: true, spec: { name: "Sales overview", pages: [{ components: [{}, {}] }], sources: [{ kind: "sample" }] } }); },
    "#dialog-root .dialog input[aria-label='Type Sales Dev to publish']"],
  ["fabricstep.js:stepConfirm", "Run the last cell of Daily sales on Fabric?", () => {
    stepConfirm({ name: "Daily sales" }, { tables: ["silver.orders"], workspace_name: "Sales Dev", workspace_id: "w1", lakehouse: "LH_Silver", item_name: "Daily sales (step)" }); }],
  ["git.js:gitSecretsDialog", "These files look like they hold a secret", () => {
    gitSecretsDialog([{ path: "config/settings.json", line: 4, kind: "Storage key", match: "AccountKey=abc***" }]); }],
  ["meta.js:dictionaryImportDialog", "Import descriptions", () => { dictionaryImportDialog({ layer: "silver" }, () => {}); }],
  ["estate.js:restoreDialog", "Restore silver.orders to version 3?", () => {
    restoreDialog("silver.orders", "dev", { version: 3, at: "2026-11-04T08:00:00Z" }, { incremental: true, downstream: ["gold.sales"] }); }],
  ["integrations.js:editTool", "Edit Copilot", () => {
    editTool({ permissions: [{ id: "READ_METADATA", offered: true, allowed: true }, { id: "READ_DATA", offered: true, allowed: true }] },
      { id: "copilot", name: "Copilot", grants: ["READ_METADATA"], auto: [], workspaces: [], config: {}, kind: "vscode", transport: "stdio" }, () => {}); }],
  ["platform.js:confirmShortcutEnvs", "Approve shortcut sales_orders?", () => {
    confirmShortcutEnvs({ id: "r1", target: "sales_orders", detail: { name: "sales_orders", workspace_name: "Sales Dev", lakehouse_name: "LH_Bronze" } }); }],
  ["sqlchanges.js:whChangeConfirm", "Change WH_Sales?", () => {
    whChangeConfirm({ name: "WH_Sales", kind: "warehouse", workspace_id: "w1" },
      { statements: [{ statement: "UPDATE dbo.orders SET x = 1", permission: "warehouse_write", reason: "Changes rows" }, { statement: "SELECT 1", permission: "warehouse_read" }] }); }],
  ["meta.js:createEnvWorkspace", "New workspace for Dev", () => { createEnvWorkspace("dev", state.meta.project); }],
  ["whproject.js:whProjectNewObject", "New object", () => {
    whProjectNewObject({ name: "WH_Sales", kind: "warehouse" }, { repo: "C:/repos/wh", folder: "WH_Sales" }, {}, () => {}); }],
  ["meta.js:pickKeysWorkspace", "Keys workspace for Dev", () => { pickKeysWorkspace("dev"); }, "#dialog-root .dialog .choice"],
  ["mesh.js:pickDesignationWorkspace", "Landing workspace for Dev", () => {
    pickDesignationWorkspace("dev", { id: "landing", label: "Landing" }, () => {}); }, "#dialog-root .dialog .choice"],
  ["mesh.js:keysSetupDialog", "Create the Keys workspace", () => { keysSetupDialog({ keys: { default_name: "KratosForge Keys" } }); }],
  // Batch 6: lineage, Fabric runs, risk gate, sources, governance policy, scorecard, vacuum and landing.
  ["dataops.js:columnLineage", "Column lineage: silver.orders", () => { columnLineage("silver.orders"); }],
  ["df-dataflows.js:dfRunInFabric", "Run Orders flow?", () => {
    dfRunInFabric({ id: "w9", displayName: "Other" }, { item_id: "df1", name: "Orders flow" }, "Run", () => {}); }],
  ["meta.js:metaRiskGate", "Run Daily load in Dev?", () => { metaRiskGate(state.meta, ["silver.orders"], "dev", "Daily load"); }],
  ["meta.js:readSourceDialog", "Add a source this project reads", () => {
    readSourceDialog([{ code: "sap", title: "SAP ERP", system_type: "SAP", owner: "ERP team", id: 3, read: false }], "dev", () => {}); }],
  ["governance.js:policyDialog", "New policy", () => {
    policyDialog({ categories: { naming: "Naming", quality: "Quality" }, severities: ["low", "medium", "high"], enforcements: ["advisory", "warn", "block"],
      scopes: ["enterprise", "domain", "project", "entity"] }, null, () => {}); }],
  ["prove.js:showScorecard", "Scorecard", () => { showScorecard({ scorecard: {} }); }],
  ["xray.js:vacuumAll", "Vacuum 2 tables", () => { vacuumAll([{ table: "orders" }, { table: "customers" }], null, null); }],
  ["optimize.js:optimizeMove", "Dismiss this improvement?", () => { optimizeMove({ id: "rec1" }, "dismissed"); }],
  ["platform.js:landingSetupDialog", "Move to one landing workspace", () => { landingSetupDialog({ default_landing_name: "KratosForge Landing" }, () => {}); }],
  ["meta.js:ossieImportDialog", "Import from Ossie", () => { ossieImportDialog({ name: "Sales model", existing: true, workspace_name: "Sales Dev" }, null); }],
  ["platform.js:confirmApproval", "Pick a capacity for Sales Dev", () => { confirmApproval({ kind: "workspace", target: "Sales Dev", detail: {} }); }],
  ["git.js:gitPlatformDeploy", "Deploy the platform repository", () => { gitPlatformDeploy("C:/repos/platform"); }],
  // Batch 7: the last dialogs, several opened from a step before them (next() presses that step's main button).
  ["optimize.js:optimizeSyncDetails", "Sync with the lakehouse", () => {
    optimizeSyncDetails({ pending: 3, pending_rows: 120, oldest: "2026-11-05T09:00:00Z", last_sync: "2026-11-05T08:00:00Z" }); }],
  ["meta.js:profileEntity", "Profile silver.orders", () => { profileEntity("silver.orders"); }, "#dialog-root .dialog.wide .scroll"],
  ["platform.js:confirmCopyJob", "Approve source erp?", () => {
    confirmCopyJob({ target: "erp", detail: { owner: "erp-team@contoso.com",
      copy: { connection_name: "ERP SQL", times: ["02:00"], tables: [{ table: "dbo.orders", folder: "orders", mode: "incremental", column: "modified_at" }] } } }); }],
  ["product-items.js:productItemDialog", "Add a semantic model", () => {
    productItemDialog({ id: "p1" }, { type: "SemanticModel", title: "Semantic models", dialog: "Add a semantic model", word: "semantic model" }, () => {}); }],
  ["whmonitor.js:whMonitorBuild", "Build monitoring views in Sales Dev?", () => {
    sqlPage.whMonitor = { item: "wh1", status: { database: "monitoring", querysets: {} } };
    whMonitorBuild({ workspace_id: "w1", workspace_name: "Sales Dev" }); }],
  ["xray.js:stagingDialog", "Remove LH_Staging", () => {
    stagingDialog({ id: "f1", workspace_id: "w1", lakehouse_id: "l1", lakehouse_name: "LH_Staging", workspace_name: "Sales Dev" }, null, null); }, "#dialog-root .dialog table"],
  ["optimize.js:optimizeCreateFlow", "Create an optimization lakehouse", async () => {
    optimizeCreateFlow();
    await new Promise((r) => setTimeout(r, 300));
    document.querySelector("#dialog-root .dialog .tool.primary").click(); }, "#dialog-root .dialog input"],
  ["meta.js:moveSourcesToPlatform", "Some sources weren't moved", async () => {
    moveSourcesToPlatform([{ code: "sap", id: 3 }, { code: "crm", id: 4 }], "dev", () => {});
    await new Promise((r) => setTimeout(r, 300));
    document.querySelector("#dialog-root .dialog .tool.primary").click(); }],
  // Batch 8: dialogs opened from inside a page.
  ["dataops.js:quarantinePanel", "Replayed", async () => {
    const host = __host();
    host.append(quarantinePanel({ name: "silver.orders" }, { env: "dev", total: 1, columns: ["order_id"], editable: [], checks: { order_id: "not null" },
      rows: [{ _qid: "q1", _check: "order_id not null", _loaded_at: "2026-11-04", order_id: null }] }));
    host.querySelector('input[aria-label="Select row q1"]').click();
    __press("Replay selected", host); }],
  ["fabricstep.js:insertVOrderCell", "Insert V-Order cell", () => {
    const nb = state.notebook;
    state.nbTables.l1 = ["dbo.orders", "dbo.customers"];
    state.notebook = { ...nb, lakehouse: { workspace_id: "w1", lakehouse_id: "l1", name: "LH_Silver" } };
    insertVOrderCell(nb.cells[0]);
    state.notebook = nb; }],
  ["governance.js:governancePolicy", "Deprecate Table names?", async () => {
    const was = [state.view, platformPage.tab];
    state.view = "platform"; platformPage.tab = "governance";
    Object.assign(govPage, { open: "pol1", sub: "definition", version: null });
    const host = __host();
    await governancePolicy(host, "pol1");
    [state.view, platformPage.tab] = was;
    govPage.open = null;
    __press("Deprecate", host); }],
  ["mesh.js:keysPlatformCard", "Keys not switched", async () => {
    __host().append(keysPlatformCard({ platform: false, platform_available: true, env: "dev" }, () => {}));
    __press("Move keys", document.getElementById("a11y-host"));
    await __until(() => document.querySelector("#dialog-root .dialog .tool.primary"));
    document.querySelector("#dialog-root .dialog .tool.primary").click();
    await __until(() => document.querySelector('#dialog-root .dialog[aria-label="Keys not switched"]')); }],
  ["platform.js:platformGlossaryTab", "New glossary term", async () => {
    const was = [state.view, platformPage.tab, platformPage.me];
    state.view = "platform"; platformPage.tab = "glossary"; platformPage.me = { admin: true, domains_owned: [] };
    const host = __host();
    await platformGlossaryTab(host);
    __press("New term", host);
    [state.view, platformPage.tab, platformPage.me] = was; }],
  ["platform.js:requestTable", "Decline key table supplier?", () => {
    const host = __host();
    host.append(requestTable([{ id: "r1", kind: "key_table", target: "supplier", status: "waiting", can_decide: true,
      requested_by: "ben@contoso.com", requested_at: "2026-11-04T09:30:00Z", reason: "For the supplier dimension" }], () => {}));
    __press("Decline", host); }],
  ["shortcuts.js:renderShortcuts", "Repoint 1 shortcut", async () => {
    const was = [state.view, catalogPage.tab, catalogPage.data];
    state.view = "onelake"; catalogPage.tab = "shortcuts";
    catalogPage.data = { workspaces: [{ id: "w1", displayName: "Sales Dev" }],
      items: [{ type: "Lakehouse", item_id: "l1", workspace_id: "w1", name: "LH_Silver", workspace_name: "Sales Dev" }] };
    Object.assign(scPage, { view: "health", selected: null, checkedOnce: true, loadedOnce: true, data: { admin: false, checked_at: "2026-11-05T08:00:00Z",
      counts: { total: 1, broken: 1, unreachable: 0, not_checked: 0 }, needs: [], requests: [], problems: [], errors: {},
      rows: [{ id: "s1", name: "orders", path: "Tables/orders", full_path: "Tables/orders", kind: "table", health: "broken", health_detail: "The target lakehouse is gone",
        target_type: "OneLake", target: "LH_Old", target_path: "Tables/orders", item_id: "gone1", workspace_id: "w9", host_workspace_id: "w1", lakehouse_id: "l1",
        workspace_name: "Sales Dev", lakehouse_name: "LH_Silver", target_workspace_name: "Old workspace", used_by: [], risks: [] }] } });
    await renderShortcuts();
    __press("Repoint all to another lakehouse");
    [state.view, catalogPage.tab, catalogPage.data] = was;
    scPage.data = null; }],
  ["whproject.js:whProjectDeploy", "Deploy WH_Sales", () => {
    const ref = { kind: "warehouse", workspace_id: "w1", workspace_name: "Sales Dev", item_id: "wh1", name: "WH_Sales" };
    sqlWarehouseOk.add(sqlRefKey(ref));
    sqlPage.catalog = { key: sqlRefKey(ref), state: "done", objects: [], cols: {} };
    whProjectDeploy(ref, { repo: "C:/repos/wh", repo_label: "wh", folder: "WH_Sales" }); }],
  ["whproject.js:whProjectImport", "Import WH_Sales into WH_Sales", () => {
    const ref = { kind: "warehouse", workspace_id: "w1", workspace_name: "Sales Dev", item_id: "wh1", name: "WH_Sales" };
    sqlWarehouseOk.add(sqlRefKey(ref));
    sqlPage.catalog = { key: sqlRefKey(ref), state: "done", objects: [], cols: {} };
    whProjectImport(ref, { repo: "C:/repos/wh", folder: "WH_Sales" }, () => {}); }],
  ["profile.js:editProfileIdentity", "Edit profile", async () => { profilePage.data = await rpc("profile_get", {}); editProfileIdentity(); }],
];

// Runs in the page after a Tab: where focus is, whether it's still in the dialog, whether it shows a ring
// (an outline on it or a close parent: a contrast theme removes box shadows, so a ring drawn as one is gone),
// and whether it's on screen and not under something, inside what clips it.
function focusInDialog() {
  const el = document.activeElement;
  if (!el || el === document.body) return null;
  const dialog = el.closest("#dialog-root .dialog");
  let ring = false;
  for (let n = el, d = 0; n && d < 4 && !ring && n !== dialog; n = n.parentElement, d++) {
    const cs = getComputedStyle(n);
    ring = (cs.outlineStyle !== "none" && parseFloat(cs.outlineWidth) > 0) || cs.boxShadow !== "none";
  }
  const rect = el.getBoundingClientRect();
  let [l, t, r, b] = [Math.max(rect.left, 0), Math.max(rect.top, 0), Math.min(rect.right, innerWidth), Math.min(rect.bottom, innerHeight)];
  for (let n = el.parentElement; n; n = n.parentElement) {
    if (getComputedStyle(n).overflow === "visible") continue;
    const c = n.getBoundingClientRect();
    [l, t, r, b] = [Math.max(l, c.left), Math.max(t, c.top), Math.min(r, c.right), Math.min(b, c.bottom)];
  }
  const top = r > l && b > t ? document.elementFromPoint((l + r) / 2, (t + b) / 2) : null;
  const label = (el.getAttribute("aria-label") || el.textContent || el.value || "").trim().replace(/\s+/g, " ").slice(0, 30);
  el.dataset.a11yStop ||= String(Math.random());
  return { key: `${el.tagName.toLowerCase()}${label ? ` "${label}"` : ""}`, stop: el.dataset.a11yStop, inside: !!dialog, ring,
    visible: !!top && (top === el || el.contains(top) || top.contains(el) || !!top.closest("label")?.contains(el) || !!top.closest("#toast")) };
}

// The open dialog at 200% (the window's 1440x940 laid out as 720x470) and in a Windows contrast theme (forced
// colors): axe again (contrast is the theme's there, so not measured), the dialog inside the window, and a Tab
// walk that stays in the dialog, keeps each Tab stop on screen at 200% and shows a ring in the contrast theme.
async function zoomAndContrast(p) {
  const found = [];
  for (const mode of [{ id: "200%", viewport: { width: 720, height: 470 } }, { id: "contrast theme", forced: true }]) {
    await p.setViewportSize(mode.viewport || { width: 1440, height: 940 });
    await p.emulateMedia({ forcedColors: mode.forced ? "active" : "none" });
    found.push(...(await axeFindings(p, "#dialog-root", { off: mode.forced ? ["color-contrast"] : [] })).map((f) => `${mode.id}: ${f}`));
    // Never wider than the window; taller only where the backdrop scrolls to the rest. And nothing it holds
    // runs past its own edges (a dialog's content can't be scrolled to there).
    const spill = await p.evaluate(() => {
      const d = document.querySelector("#dialog-root .dialog");
      const r = d.getBoundingClientRect();
      const scrolls = ["auto", "scroll"].includes(getComputedStyle(d.parentElement).overflowY);
      if (r.left < -1 || r.right > innerWidth + 1 || ((r.top < -1 || r.bottom > innerHeight + 1) && !scrolls)) {
        return `the dialog is ${Math.round(r.width)}x${Math.round(r.height)} in a ${innerWidth}x${innerHeight} window`;
      }
      const out = [...d.children].find((c) => c.getBoundingClientRect().bottom > r.bottom + 1 && getComputedStyle(d).overflowY === "visible");
      return out ? `what it holds runs ${Math.round(out.getBoundingClientRect().bottom - r.bottom)}px past its bottom` : null;
    });
    if (spill) found.push(`${mode.id}: ${spill}`);
    // A contrast theme drops the dialog's shadow and paints it like the page: only a border shows where it ends.
    if (mode.forced && await p.evaluate(() => getComputedStyle(document.querySelector("#dialog-root .dialog")).borderTopStyle === "none")) {
      found.push(`${mode.id}: the dialog has no edge`);
    }
    const seen = new Set();
    for (let i = 0; i < 80; i++) {
      await p.keyboard.press("Tab");
      const at = await p.evaluate(focusInDialog);
      if (!at || seen.has(at.stop)) break;
      seen.add(at.stop);
      if (!at.inside) { found.push(`${mode.id}: Tab left the dialog for ${at.key}`); break; }
      if (mode.viewport && !at.visible) found.push(`${mode.id}: ${at.key} is off screen or covered when it has focus`);
      if (mode.forced && !at.ring) found.push(`${mode.id}: ${at.key} shows no focus ring`);
    }
  }
  await p.setViewportSize({ width: 1440, height: 940 });
  await p.emulateMedia({ forcedColors: "none" });
  return found;
}

for (const [key, title, open, ready = "#dialog-root .dialog"] of CASES) {
  test(`axe finds nothing serious in ${key}`, { skip }, async () => {
    await page().evaluate(() => closeDialog());
    await page().evaluate(open);
    await page().locator(ready).first().waitFor({ timeout: 5000 }).catch(() => assert.fail(`${key} didn't open`));
    const opened = await page().locator("#dialog-root .dialog").first().getAttribute("aria-label");
    assert.ok(title instanceof RegExp ? title.test(opened) : opened === title, `${key} opened "${opened}"`);
    assert.deepStrictEqual(await axeFindings(page(), "#dialog-root"), [], key);
    assert.deepStrictEqual((await zoomAndContrast(page())).filter((f) => !KNOWN_ZOOM_CONTRAST.has(`${key}: ${f}`)), [], `${key} at 200% and in a contrast theme`);
    await page().evaluate(() => closeDialog());
  });
}

test("the 200% and contrast theme checks catch a dialog that breaks", { skip }, async () => {
  await page().evaluate(() => {
    closeDialog();
    const ringByShadow = h("button", { class: "a11y-shadow-ring", style: "outline:none" }, "Shadow ring");
    ringByShadow.addEventListener("focus", () => { ringByShadow.style.boxShadow = "0 0 0 3px #1b3664"; });
    ringByShadow.addEventListener("blur", () => { ringByShadow.style.boxShadow = ""; });
    // A wide dialog (fixed height) whose content has no scrolling pane of its own.
    const overlay = showDialog("Planted", h("div", {}, ringByShadow, h("div", { style: "height:900px" }, "Tall")), [btn("Close", null, closeDialog, "tool")]);
    overlay.querySelector(".dialog").classList.add("wide");
    overlay.querySelector(".dialog").style.border = "none";
  });
  const found = await zoomAndContrast(page());
  await page().evaluate(() => closeDialog());
  assert.ok(found.some((f) => /^200%: what it holds runs \d+px past its bottom$/.test(f)), found.join("; "));
  assert.ok(found.includes('contrast theme: button "Shadow ring" shows no focus ring'), found.join("; "));
  assert.ok(found.includes("contrast theme: the dialog has no edge"), found.join("; "));
  assert.ok(!found.some((f) => f.startsWith("200%: button \"Shadow ring\" shows")), "only the contrast theme loses a shadow ring");
});

test("the dialogs drew without a script error", { skip }, () => {
  assert.deepStrictEqual(app.errors, []);
});
