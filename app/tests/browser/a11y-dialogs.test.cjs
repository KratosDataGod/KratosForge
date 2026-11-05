// #1673: axe on the dialogs that had no accessibility case when the guard came in (ui.test.cjs, "every
// dialog has an axe case"), most-used first, in batches. Each case opens one dialog the way its button
// does, with test data, runs axe on it (WCAG 2.1 A and AA, serious and critical findings) and closes it.
// Nothing here writes: every engine call is a mock that reads.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp, axeSource, axeFindings } = require("./harness.cjs");
// axe covers dialogs (#1673, ui.test.cjs): the cases below run axe while these are open.
// axe covers dialogs: notebook-versions.js:nbVersions, meta.js:showRun, settings.js:showWhatsNew, settings.js:checkForUpdates
// axe covers dialogs: notebook-files.js:nbSaveCopyDialog, notebook-files.js:nbNewFolderDialog, notebook-files.js:nbRenameFolderDialog
// axe covers dialogs: notebook-files.js:nbMoveToFolderDialog, notebook-comments.js:commentDialog, notebook.js:cancelRun
// axe covers dialogs: app.js:openDataItems, notebook-formats.js:nbfExport, meta.js:newEntityDialog, sqltemplates.js:openSqlTemplates
// axe covers dialogs: git.js:gitNewDialog, git.js:nbChoosePlace, profile.js:editProfileIdentity

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
