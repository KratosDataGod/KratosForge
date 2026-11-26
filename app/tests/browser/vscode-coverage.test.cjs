// VS Code alongside KratosForge (vscode.js): what notebook-vscode, vscode-setup, notebook-sync-merge and
// integrations-coverage don't reach. The VS Code menu's other items and its failures, coming back into focus when the
// file can't be read, the side-by-side chooser's labels, picks and answers (unresolved, changed again, saved), Set up
// for VS Code's text to paste and files left as they are, kratosforge:// links of every kind, the VS Code card in
// Settings, and a write to OneLake asked from VS Code. The notebooks are the real engine's; everything VS Code, Git and
// the editor channel would answer is mocked with the engine's replies. Nothing reaches Fabric.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
const ENGINE = ["list_notebooks", "open_notebook", "save_notebook", "notebook_places", "compute_check", "kernel_variables", "notebook_versions"];
const NAME = "Daily sales";
const WHERE = { path: "C:\\Users\\you\\notebooks\\Daily sales.ipynb", folder: "C:\\Users\\you\\notebooks", repo_root: "C:\\src\\sales-platform",
  repo_label: "sales-platform", notebooks_folder: "C:\\Users\\you\\notebooks" };
const PLAN = { name: "sales-platform", root: "C:\\src\\sales-platform", new: 1, files: [
  { path: ".vscode/tasks.json", label: "Validate task", why: "Checks the open notebook.", state: "new", text: "{}" },
  { path: ".github/copilot-instructions.md", label: "Copilot instructions", why: "Tells Copilot about KratosForge.", state: "exists",
    text: "Use the KratosForge tools to run notebooks." },
  { path: ".vscode/extensions.json", label: "Extensions", why: "The extensions to install.", state: "same", text: "{}" }] };

const world = {};
function reset() {
  Object.assign(world, { fail: {}, addAll: false, stamp: null, merge: [], pending: { enabled: false, pairing: [], writes: [] }, nameForPath: { name: null } });
}
reset();

let app;
const page = () => app.page;
const calls = (m) => app.calls.filter((c) => c.method === m).map((c) => c.params);
const dialog = () => page().locator(".dialog");
const toastText = () => page().evaluate(() => document.getElementById("toast").textContent);
const shell = (cmd) => page().evaluate((c) => window.__fdShellCalls.filter((x) => x.cmd === c).map((x) => x.args), cmd);
const frames = (n = 3) => page().evaluate((n) => new Promise((r) => { let i = 0; const f = () => (++i >= n ? r() : requestAnimationFrame(f)); requestAnimationFrame(f); }), n);
async function until(check, what) {
  for (let i = 0; i < 300; i++) { if (await check()) return; await frames(1); }
  assert.fail(`timed out waiting for ${what}`);
}
const toastSays = (re) => until(async () => re.test(await toastText()), String(re));

function failing(method) {
  const why = world.fail[method];
  if (why) { delete world.fail[method]; throw new Error(why); }
}

before(async () => {
  if (skip) return;
  app = await openApp({
    viewport: { width: 1440, height: 900 },
    engine: ENGINE,
    mocks: {
      app_settings: () => ({ show_preview: { value: true, source: "default" } }),
      notebook_file_path: () => { failing("notebook_file_path"); return WHERE; },
      notebook_disk_stamp: () => { failing("notebook_disk_stamp"); return { disk_stamp: world.stamp }; },
      notebook_sync_merge: (p) => { failing("notebook_sync_merge"); return world.merge.shift()(p); },
      git_vscode_plan: () => { failing("git_vscode_plan"); return PLAN; },
      git_vscode_apply: ({ files }) => (world.addAll ? { added: files, skipped: [], status: { branch: "main" } }
        : { added: [], skipped: files, status: { branch: "main" } }),
      notebook_name_for_path: () => world.nameForPath,
      editor_pending: () => { failing("editor_pending"); return world.pending; },
      editor_set_enabled: ({ enabled }) => ({ enabled, blocked: null, listening: enabled, port: null, pairings: [] }),
      editor_end_pairing: () => ({ ended: true }),
      editor_decide_write: ({ approve }) => ({ approved: approve }),
      editor_decide_pairing: ({ approve }) => ({ approved: approve }),
    },
  });
  await page().evaluate(async (name) => {
    state.settings = { ...(state.settings || {}), show_preview: { value: true } };
    await rpc("save_notebook", { name, cells: [{ type: "code", source: "orders = 1" }, { type: "code", source: "total = 2" }] });
    await rpc("save_notebook", { name: "Other", cells: [{ type: "code", source: "x = 1" }] });
    await openNotebook(name);
  }, NAME);
  await page().waitForFunction(() => state.notebook?.name === "Daily sales" && !!state.notebook.diskStamp);
  await page().evaluate(() => { window.__copied = []; navigator.clipboard.writeText = async (t) => { window.__copied.push(t); }; });
  app.errors.length = 0;
});
after(async () => { if (app) await app.close(); });

async function backToNotebook() {
  await page().evaluate(async (name) => {
    closeDialog();
    if (state.notebook) Object.assign(state.notebook, { dirty: false, diskConflict: false, diskConflictOpen: false });
    if (state.notebook?.name !== name || state.view !== "notebook") await openNotebook(name);
  }, NAME);
  await page().waitForFunction((name) => state.notebook?.name === name && state.view === "notebook", NAME);
  app.calls.length = 0;
}

test("the VS Code menu of a notebook in a repository opens it in Insiders, opens the repository, and sets it up", { skip }, async () => {
  reset();
  await backToNotebook();
  const menu = () => page().evaluate(() => {
    const nb = state.notebook;
    nb.place = { root: "C:\\src\\sales-platform", label: "sales-platform" };
    showMenu(document.querySelector("[aria-label='More VS Code options']"), vscodeMenu(nb));
  });
  await menu();
  assert.deepStrictEqual(await page().locator("#menu").innerText().then((t) => t.split("\n").filter(Boolean)), [
    "Daily sales is in the sales-platform repository.", "Open this notebook in VS Code", "Open the repository folder in VS Code",
    "Open this notebook in VS Code Insiders", "Set up this repository for VS Code…"]);
  await page().getByRole("menuitem", { name: "Open this notebook in VS Code Insiders" }).click();
  await toastSays(/Opened Daily sales in VS Code\. Edits saved there show here when you come back\./);
  assert.deepStrictEqual((await shell("open_in_vscode")).at(-1), { path: WHERE.path, insiders: true });
  await menu();
  await page().getByRole("menuitem", { name: "Open the repository folder in VS Code" }).click();
  await toastSays(/Opened sales-platform in VS Code\./);
  assert.deepStrictEqual((await shell("open_in_vscode")).at(-1), { path: WHERE.repo_root, insiders: false });
  await menu();
  await page().getByRole("menuitem", { name: "Open this notebook in VS Code", exact: true }).click();
  await until(async () => (await shell("open_in_vscode")).length === 3, "the third open");
  await menu();
  await page().getByRole("menuitem", { name: "Set up this repository for VS Code…" }).click();
  await page().getByRole("dialog", { name: "Set up sales-platform for VS Code" }).waitFor();
  assert.deepStrictEqual(calls("git_vscode_plan"), [{ repo: "C:\\src\\sales-platform" }]);
  await page().evaluate(() => { delete state.notebook.place; closeDialog(); });
  assert.deepStrictEqual(app.errors, []);
});

test("Open in VS Code stops when the save fails or the file can't be found", { skip }, async () => {
  reset();
  await backToNotebook();
  const before = (await shell("open_in_vscode")).length;
  await page().evaluate(async () => {
    const real = saveNotebook;
    saveNotebook = async () => false;
    try { state.notebook.dirty = true; await openInVsCode(state.notebook, "notebook"); } finally { saveNotebook = real; state.notebook.dirty = false; }
  });
  assert.deepStrictEqual(calls("notebook_file_path"), []);
  world.fail.notebook_file_path = "There's no notebook named 'Daily sales'.";
  await page().evaluate(() => openInVsCode(state.notebook, "folder"));
  await toastSays(/^Couldn't find the notebook's file: There's no notebook named 'Daily sales'\./);
  assert.strictEqual((await shell("open_in_vscode")).length, before);
  assert.deepStrictEqual(app.errors, []);
});

test("coming back into focus leaves the notebook as it is when its file can't be read, and shows it when it changed", { skip }, async () => {
  reset();
  await backToNotebook();
  const cells = () => page().evaluate(() => state.notebook.cells.map((c) => c.source));
  world.fail.notebook_disk_stamp = "PermissionError: [Errno 13] Permission denied";
  await page().evaluate(() => nbReloadIfChangedOutside());
  assert.deepStrictEqual(calls("open_notebook"), []);
  world.stamp = await page().evaluate(() => state.notebook.diskStamp);  // the same stamp: nothing changed
  await page().evaluate(() => nbReloadIfChangedOutside());
  assert.deepStrictEqual(calls("open_notebook"), []);
  world.stamp = "changed-in-vs-code";
  await page().evaluate(async () => {  // the file went away meanwhile: open_notebook fails and nothing changes
    const nb = state.notebook;
    nb.name = "Gone since";
    try { await nbReloadIfChangedOutside(); } finally { nb.name = "Daily sales"; }
  });
  assert.deepStrictEqual(calls("open_notebook"), [{ name: "Gone since" }]);
  assert.deepStrictEqual(await cells(), ["orders = 1", "total = 2"]);
  await page().evaluate(async () => { await rpc("save_notebook", { name: "Daily sales", cells: [{ type: "code", source: "orders = 3" }, { type: "code", source: "total = 2" }] }); });
  await page().evaluate(() => nbReloadIfChangedOutside());
  await toastSays(/Daily sales changed outside KratosForge, for example in VS Code\. Showing the file as it is now\./);
  assert.deepStrictEqual(await cells(), ["orders = 3", "total = 2"]);
  assert.deepStrictEqual(app.errors, []);
});

// The engine's merge (notebook_sync.merge_cells): each conflict has both versions, an order conflict both orders.
const cell = (cell_id, source, type = "code", meta = undefined) => ({ cell_id, type, source, ...(meta ? { meta } : {}) });
const LONG = "df = spark.read.table('main.sales.orders_with_a_very_long_name')";
function bigMerge() {
  return {
    resolved: false, theirs_saved_at: null,
    cells: [cell("a", "# Load\ndf = load()"), cell("b", "x = 2"), cell("b-theirs-1", "x = 3"), cell("c", "", "markdown", { title: "Notes" }),
      cell("d", LONG), cell("e", "")],
    conflicts: [
      { id: "k1", kind: "both_edited", name: "x", cell_id: "b", base: cell("b", "x = 1"), mine: cell("b", "x = 2\n"), theirs: cell("b", "x = 3") },
      { id: "k2", kind: "deleted_there", name: "d", cell_id: "d", base: cell("d", LONG), mine: cell("d", LONG), theirs: null },
      { id: "k3", kind: "deleted_here", name: "z", cell_id: "z", base: null, mine: null, theirs: cell("z", "", "markdown") },
      { id: "k4", kind: "reordered_both", name: "order", cell_id: null, base: ["a", "b"], mine: ["b", "a"], theirs: ["a", "zz"] },
    ],
  };
}

test("the side-by-side chooser labels each cell and side, picks all one way, and saves only with every pick", { skip }, async () => {
  reset();
  await backToNotebook();
  const merge = bigMerge();
  await page().evaluate((merge) => nbSyncMerge(state.notebook, { name: "Daily sales", changed_on_disk: true, disk_stamp: "s2", merge }), merge);
  const chooser = page().getByRole("dialog", { name: "This notebook changed in both places" });
  await chooser.waitFor();
  const text = await chooser.innerText();
  assert.match(text, /3 cells differ\. Pick a version for each, then save\./);
  assert.match(text, /3 other cells need no pick/);
  assert.match(text, /Cell 2, x = 2\s*Edited on both sides/);
  assert.match(text, /Cell 5, df = spark\.read\.table\('main\.sales\.order…\s*Deleted in VS Code, edited here/);
  assert.match(text, /Cell , Text\s*Deleted here, edited in VS Code/);
  assert.match(text, /This cell was deleted here\./);
  assert.match(text, /This cell was deleted in VS Code\./);
  assert.match(text, /Cell order\s*Reordered on both sides/);
  assert.strictEqual(await chooser.locator(".sync-side-head").filter({ hasText: /^In VS Code$/ }).count(), 4);
  assert.deepStrictEqual(await chooser.locator(".sync-order").nth(1).locator("li").allInnerTexts(), ["Load", "zz"]);
  await chooser.locator("details.sync-same summary").click();
  assert.deepStrictEqual(await chooser.locator("details.sync-same li").allInnerTexts(), ["Load", "Notes", "Code"]);
  assert.strictEqual(await chooser.locator(".sync-added").count() > 0, true);
  const save = chooser.getByRole("button", { name: "Save" });
  assert.match(text, /Pick 4 more to save\./);
  assert.ok(await save.isDisabled());
  await chooser.getByRole("button", { name: "Use all VS Code's" }).click();
  assert.strictEqual(await chooser.locator("input[type=radio][value=theirs]:checked").count(), 4);
  await chooser.getByRole("button", { name: "Use all mine" }).click();
  assert.strictEqual(await chooser.locator("input[type=radio][value=mine]:checked").count(), 4);
  await chooser.getByRole("radiogroup", { name: "Pick for cell 2" }).getByLabel("Keep both").check();
  await chooser.getByText("Every difference has a pick.").waitFor();

  // The engine fails, then answers that a pick is still missing, then that the file changed again: the chooser stays.
  world.fail.notebook_sync_merge = "The notebook couldn't be saved.";
  await save.click();
  await toastSays(/^Couldn't save the merged notebook: The notebook couldn't be saved\./);
  assert.ok(await save.isEnabled());
  world.merge.push(() => ({ name: NAME, resolved: false, conflicts: merge.conflicts, cells: merge.cells, disk_stamp: "s2" }));
  await save.click();
  await until(async () => calls("notebook_sync_merge").length === 2, "the second answer");
  await chooser.getByText("Every difference has a pick.").waitFor();
  world.merge.push(() => ({ name: NAME, changed_on_disk: true, disk_stamp: "s3", merge }));
  await save.click();
  await until(async () => calls("notebook_sync_merge").length === 3, "the third answer");
  assert.strictEqual(await page().getByRole("dialog").count(), 1);

  world.merge.push(({ choices }) => ({ name: NAME, resolved: true, conflicts: [], cells: merge.cells, disk_stamp: "s4", saved_choices: choices }));
  await save.click();
  await toastSays(/Saved Daily sales with your picks\. Both full versions are under Versions\./);
  assert.deepStrictEqual(calls("notebook_sync_merge").at(-1).choices, { k1: "both", k2: "mine", k3: "mine", k4: "mine" });
  assert.strictEqual(await page().getByRole("dialog").count(), 0);
  await until(async () => calls("open_notebook").length > 0, "the notebook read back");
  assert.deepStrictEqual(app.errors, []);
});

test("one cell that differs, or only the order, says so in the singular; Cancel closes the chooser", { skip }, async () => {
  reset();
  await backToNotebook();
  const one = { resolved: false, theirs_saved_at: "2026-11-26T09:30:00+00:00", cells: [cell("a", "a = 1"), cell("b", "b = 2")],
    conflicts: [{ id: "k1", kind: "both_edited", cell_id: "b", base: cell("b", "b = 1"), mine: cell("b", "b = 2"), theirs: cell("b", "b = 3") }] };
  await page().evaluate((merge) => nbSyncMerge(state.notebook, { name: "Daily sales", changed_on_disk: true, merge }), one);
  const chooser = page().getByRole("dialog", { name: "This notebook changed in both places" });
  await chooser.waitFor();
  assert.match(await chooser.innerText(), /1 cell differs\./);
  assert.match(await chooser.innerText(), /1 other cell needs no pick/);
  assert.match(await chooser.innerText(), /In VS Code, saved \d/);
  await chooser.getByRole("button", { name: "Cancel" }).click();
  assert.strictEqual(await page().evaluate(() => state.notebook.diskConflictOpen), false);

  const order = { resolved: false, cells: [cell("a", "a = 1"), cell("b", "b = 2")],
    conflicts: [{ id: "k9", kind: "reordered_both", cell_id: null, base: ["a", "b"], mine: ["b", "a"], theirs: ["b", "a"] }] };
  await page().evaluate((merge) => nbSyncMerge(state.notebook, { name: "Daily sales", changed_on_disk: true, merge }), order);
  await chooser.waitFor();
  assert.match(await chooser.innerText(), /The cell order differs\. Pick a version for each, then save\./);
  assert.match(await chooser.innerText(), /2 other cells need no pick/);
  await chooser.getByRole("button", { name: "Cancel" }).click();
  assert.deepStrictEqual(app.errors, []);
});

test("without a merge the older question asks instead, and a merge read back for another notebook isn't shown", { skip }, async () => {
  reset();
  await backToNotebook();
  await page().evaluate(() => nbSyncMerge(state.notebook, { name: "Daily sales", changed_on_disk: true, disk_stamp: "s9", merge: null }));
  await page().getByRole("dialog", { name: /This notebook changed outside KratosForge/ }).waitFor();
  await page().evaluate(() => closeDialog());
  world.merge.push(({ choices }) => ({ name: NAME, resolved: true, conflicts: [], cells: [], disk_stamp: "s5", choices }));
  await page().evaluate(() => nbSyncMerge(state.notebook, { name: "Daily sales", changed_on_disk: true, merge: { resolved: true, cells: [], conflicts: [] } }));
  await toastSays(/Merged the changes saved in VS Code with yours in Daily sales\. Both versions are under Versions\./);
  assert.deepStrictEqual(calls("notebook_sync_merge").at(-1).choices, {});
  await until(async () => calls("open_notebook").length === 1, "the merge read back");
  app.calls.length = 0;
  await page().evaluate(() => nbSyncApplied({ ...state.notebook }, {}));
  assert.strictEqual(await page().evaluate(() => state.notebook.dirty), false);
  assert.deepStrictEqual(calls("open_notebook").length, 1);
  assert.deepStrictEqual(app.errors, []);
});

test("Set up for VS Code shows the text to paste, counts the ticks, and says which files were left as they are", { skip }, async () => {
  reset();
  await backToNotebook();
  world.fail.git_vscode_plan = "C:\\src\\gone isn't a Git repository.";
  await page().evaluate(() => vscodeSetupDialog("C:\\src\\gone", "gone"));
  await toastSays(/^Couldn't read the repository: C:\\src\\gone isn't a Git repository\./);
  assert.strictEqual(await page().getByRole("dialog").count(), 0);

  await page().evaluate(() => { window.__after = []; vscodeSetupDialog("C:\\src\\sales-platform", "sales-platform", (s) => window.__after.push(s)); });
  const d = page().getByRole("dialog", { name: "Set up sales-platform for VS Code" });
  await d.waitFor();
  assert.deepStrictEqual(await d.locator(".pill-s").allInnerTexts(), ["New", "Already there", "Already set up"]);
  assert.match(await d.innerText(), /Copilot instructions\. Tells Copilot about KratosForge\. It's already in the repository, so it's left as it is\./);
  const add = d.getByRole("button", { name: /^Add|^Nothing to add/ });
  assert.strictEqual(await add.innerText(), "Add 1 file");
  await d.getByLabel(".vscode/tasks.json").uncheck();
  assert.strictEqual(await add.innerText(), "Nothing to add");
  assert.ok(await add.isDisabled());
  await d.getByLabel(".vscode/tasks.json").check();
  await d.getByText("Show the text to add").click();
  await d.getByRole("button", { name: "Copy" }).click();
  await toastSays(/Copied\. Paste it into the file\./);
  assert.deepStrictEqual(await page().evaluate(() => window.__copied.at(-1)), "Use the KratosForge tools to run notebooks.");
  await add.click();
  await toastSays(/Left as they are: \.vscode\/tasks\.json\./);
  assert.deepStrictEqual(calls("git_vscode_apply"), [{ repo: "C:\\src\\sales-platform", files: [".vscode/tasks.json"] }]);
  assert.deepStrictEqual(await page().evaluate(() => window.__after), [{ branch: "main" }]);
  world.addAll = true;
  await page().evaluate(() => vscodeSetupDialog("C:\\src\\sales-platform", "sales-platform"));
  await d.waitFor();
  await d.getByRole("button", { name: "Add 1 file" }).click();
  await toastSays(/^Added 1 file to sales-platform\. Commit them in Source control to share them\.$/);
  assert.deepStrictEqual(app.errors, []);
});

test("kratosforge:// links: refused, a page, an unknown page, and notebooks known, unknown or already open", { skip }, async () => {
  reset();
  await backToNotebook();
  await page().evaluate(() => showLink({ kind: "refused", reason: "It names a network share." }));
  await toastSays(/^KratosForge didn't open that link\. It names a network share\.$/);
  await page().evaluate(() => showLink({ kind: "refused" }));
  await toastSays(/^KratosForge didn't open that link\.$/);
  await page().evaluate(() => showLink({ kind: "page", page: "no_such_page" }));
  await toastSays(/KratosForge has no page called no_such_page\. Check the link, or open the page from the left rail\./);
  const pageView = await page().evaluate(() => Object.keys(railFeatures()[0].views)[0]);
  await page().evaluate((v) => showLink({ kind: "page", page: v }), pageView);
  await until(async () => (await page().evaluate(() => state.view)) === pageView, "the linked page");
  await page().evaluate(() => showLink({ kind: "something else" }));

  await page().evaluate(() => showLink({ kind: "notebook", name: "Nope" }));
  await toastSays(/There's no notebook named Nope\./);
  world.nameForPath = { name: null, why: "The path is longer than Windows allows." };
  await page().evaluate(() => showLink({ kind: "notebook", path: "C:\\Users\\you\\notebooks\\x.ipynb" }));
  await toastSays(/^C:\\Users\\you\\notebooks\\x\.ipynb: The path is longer than Windows allows\.$/);
  world.nameForPath = { name: null };
  await page().evaluate(() => showLink({ kind: "notebook", path: "D:\\elsewhere\\y.ipynb" }));
  await toastSays(/D:\\elsewhere\\y\.ipynb isn't in the notebooks folder or a notebooks repository KratosForge knows\./);
  assert.deepStrictEqual(calls("notebook_name_for_path"), [{ path: "C:\\Users\\you\\notebooks\\x.ipynb" }, { path: "D:\\elsewhere\\y.ipynb" }]);

  world.nameForPath = { name: "Other" };
  await page().evaluate(() => showLink({ kind: "notebook", path: "C:\\Users\\you\\notebooks\\Other.ipynb" }));
  await page().waitForFunction(() => state.notebook?.name === "Other" && state.view === "notebook");
  app.calls.length = 0;
  await page().evaluate(() => showLink({ kind: "notebook", name: "Other" }));  // already open: nothing to do
  assert.deepStrictEqual(calls("open_notebook"), []);
  assert.deepStrictEqual(app.errors, []);
});

test("a link from the shell is shown when it arrives or was waiting, and one it didn't make is ignored", { skip }, async () => {
  reset();
  await page().evaluate(() => {
    const invoke = window.__TAURI__.core.invoke;
    window.__TAURI__.core.invoke = async (cmd, args) => (cmd === "take_pending_link" ? JSON.stringify({ kind: "refused", reason: "Waiting." }) : invoke(cmd, args));
    return listenForLinks().finally(() => { window.__TAURI__.core.invoke = invoke; });
  });
  await toastSays(/KratosForge didn't open that link\. Waiting\./);
  await page().evaluate(() => window.__fdEmit("deep-link", "not json"));
  await page().evaluate(() => window.__fdEmit("deep-link", JSON.stringify({ kind: "refused", reason: "From the event." })));
  await toastSays(/KratosForge didn't open that link\. From the event\./);
  assert.deepStrictEqual(app.errors, []);
});

test("the VS Code card lists pairings, ends one, says when the organization stops it, and turns it off", { skip }, async () => {
  reset();
  const draw = (status) => page().evaluate((status) => {
    window.__refreshed = (window.__refreshed || 0);
    document.getElementById("cov-card")?.remove();
    document.body.append(h("div", { id: "cov-card" }, vscodeRunCard(status, () => { window.__refreshed += 1; })));
  }, status);
  const card = () => page().locator("#cov-card");
  const used = Math.floor(Date.now() / 1000) - 120;
  await draw({ enabled: true, blocked: null, listening: true, port: 50123, pairings: [
    { id: "p1", window: "sales-platform", client: "KratosForge for VS Code 1.4", created: used - 600, keep: "7d", expires: used + 86400 * 7, last_used: used },
    { id: "p2", window: "scratch", client: "KratosForge for VS Code 1.4", created: used - 60, keep: "session", expires: null, last_used: null }] });
  const rows = await card().locator("tbody tr").allInnerTexts();
  assert.match(rows[0], /sales-platform\s*KratosForge for VS Code 1\.4\s*Until .+\s+.*ago/);
  assert.match(rows[1], /scratch[\s\S]*Until KratosForge closes\s*Not yet/);
  assert.strictEqual(await card().locator(".switch span").innerText(), "On");
  await card().getByRole("button", { name: "End pairing with scratch" }).click();
  await toastSays(/^scratch isn't paired any more$/);
  assert.deepStrictEqual(calls("editor_end_pairing").at(-1), { pairing_id: "p2" });
  await card().getByLabel("Let VS Code run notebooks").uncheck();
  await toastSays(/VS Code can't run notebooks here now\. Every pairing ended\./);
  assert.deepStrictEqual(calls("editor_set_enabled").at(-1), { enabled: false });
  assert.strictEqual(await page().evaluate(() => window.__refreshed), 2);

  await draw({ enabled: true, blocked: null, pairings: [] });
  assert.match(await card().innerText(), /No VS Code window is paired\. In VS Code, pick KratosForge as a notebook's kernel to pair it\./);
  await card().getByLabel("Let VS Code run notebooks").uncheck();
  await card().getByLabel("Let VS Code run notebooks").check();
  await toastSays(/^VS Code windows can ask to pair$/);

  await draw({ enabled: false, blocked: "Your organization turned off running notebooks from VS Code.", pairings: [] });
  assert.match(await card().innerText(), /Your organization turned off running notebooks from VS Code\./);
  assert.ok(await card().getByLabel("Let VS Code run notebooks").isDisabled());
  assert.strictEqual(await card().locator(".switch span").innerText(), "Off");
  assert.strictEqual(await card().locator("table").count(), 0);
  await page().evaluate(() => document.getElementById("cov-card").remove());
  assert.deepStrictEqual(app.errors, []);
});

test("a write to OneLake asked from VS Code waits for an open dialog, then Allow once and Deny answer it", { skip }, async () => {
  reset();
  await page().evaluate(() => closeDialog());
  const write = (id) => ({ id, window: "sales-platform", notebook: "Daily sales", target: "Tables/orders", at: 1764150000 });
  const poll = () => page().evaluate(async () => {
    for (let i = 0; i < 100 && editorPair.watching; i++) await new Promise((r) => setTimeout(r, 20));
    await watchEditorRequests();
  });
  world.pending = { enabled: true, pairing: [], writes: [write("w1")] };
  await page().evaluate(() => showDialog("Something else", h("p", {}, "Open"), [btn("Close", null, closeDialog, "tool")]));
  await poll();
  await toastSays(/VS Code is waiting to write to OneLake\. Close this dialog to answer\./);
  world.pending = { enabled: true, pairing: [{ id: "r1", code: "4821", window: "scratch", client: "KratosForge for VS Code", at: 1 }], writes: [] };
  await poll();
  await toastSays(/A VS Code window is asking to pair\. Close this dialog to answer\./);
  await page().evaluate(() => closeDialog());

  world.pending = { enabled: true, pairing: [], writes: [write("w1")] };
  await poll();
  const ask = page().getByRole("dialog", { name: "VS Code wants to write to OneLake" });
  await ask.waitFor();
  assert.match(await ask.innerText(), /A cell in Daily sales, run from the VS Code window sales-platform, writes Tables\/orders\./);
  await ask.getByRole("button", { name: "Allow once" }).click();
  await until(async () => calls("editor_decide_write").length === 1, "the answer");
  assert.deepStrictEqual(calls("editor_decide_write"), [{ ask_id: "w1", approve: true }]);
  await poll();  // shown once: w1 isn't asked again
  assert.strictEqual(await page().getByRole("dialog").count(), 0);

  world.pending = { enabled: false, pairing: [], writes: [write("w1"), write("w2")] };
  await poll();
  await ask.waitFor();
  await ask.getByRole("button", { name: "Deny" }).click();
  await until(async () => calls("editor_decide_write").length === 2, "the denial");
  assert.deepStrictEqual(calls("editor_decide_write")[1], { ask_id: "w2", approve: false });
  world.pending = { enabled: false, pairing: [], writes: [] };
  assert.deepStrictEqual(app.errors, []);
});

test("a VS Code window asking to pair shows its code; Pair keeps it as picked and Deny says so; an engine not up says nothing", { skip }, async () => {
  reset();
  await page().evaluate(() => closeDialog());
  const poll = () => page().evaluate(async () => {
    for (let i = 0; i < 100 && editorPair.watching; i++) await new Promise((r) => setTimeout(r, 20));
    await watchEditorRequests();
  });
  world.fail.editor_pending = "The engine isn't running.";
  await poll();
  assert.strictEqual(await page().getByRole("dialog").count(), 0);
  const req = (id, window) => ({ id, code: "4821", window, client: "KratosForge for VS Code", at: 1764150000 });
  world.pending = { enabled: true, pairing: [req("r7", "sales-platform")], writes: [] };
  await poll();
  const ask = page().getByRole("dialog", { name: "Let VS Code run notebooks on this PC?" });
  await ask.waitFor();
  assert.match(await ask.innerText(), /Asked by\s*KratosForge for VS Code \(as it names itself\)\s*Window\s*sales-platform\s*Pairing code\s*4821/);
  await ask.getByLabel("Keep this pairing for").selectOption("8h");
  await ask.getByRole("button", { name: "Pair this window" }).click();
  await toastSays(/^Paired sales-platform\. Its cells run here now\.$/);
  assert.deepStrictEqual(calls("editor_decide_pairing").at(-1), { request_id: "r7", approve: true, keep: "8h" });
  world.pending = { enabled: false, pairing: [req("r8", "scratch")], writes: [] };
  await poll();
  await ask.waitFor();
  await ask.getByRole("button", { name: "Deny" }).click();
  await toastSays(/^Denied\. That VS Code window isn't paired\.$/);
  assert.deepStrictEqual(calls("editor_decide_pairing").at(-1), { request_id: "r8", approve: false, keep: "session" });
  world.pending = { enabled: false, pairing: [], writes: [] };
  assert.deepStrictEqual(app.errors, []);
});
