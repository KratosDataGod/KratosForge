// #1482: the Source control writes that make or fill a repository, against the real engine and a
// real Git where they stay on this PC (see harness.cjs for how to run it): Clone sends the address,
// folder and name typed and the clone shows on the page; Cancel on the band stops that clone and
// leaves no folder behind; Move this notebook sends that notebook and the place picked, and the file
// moves; Data locations sends what was picked per environment and writes locations.toml. Capacity
// (Write Fabric's state to capacity.toml) and Platform (Export from platform) read Fabric, so their
// engine calls are mocked in the shapes sidecar/fabric_desktop/git_service.py returns: each asks
// first, sends the plain write, and only when the engine says files with uncommitted changes would
// be replaced (needs_confirm) asks again in a danger confirm before sending confirm: true. Cancel at
// every dialog sends nothing, a refusal reads as a sentence, opening the pages sends no write, and
// axe finds nothing serious on each dialog. Branch, stage, discard and remote-op writes are in
// git-branch-actions.test.cjs, git-hunks.test.cjs and git-op-fx.test.cjs.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const { execFileSync } = require("node:child_process");
const { playwright, openApp, startupReads, axeSource, axeFindings } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
let app, home, salesNb, financeNb, capRepo, platRepo, upstream;
const page = () => app.page;

const ENGINE = ["git_info", "git_create", "git_status", "git_open", "git_branches", "git_log", "git_settings", "git_diff", "git_ops",
  "git_clone_start", "git_op_status", "git_op_cancel", "git_locations", "git_set_locations",
  "notebook_places", "notebook_listing", "list_notebooks", "open_notebook", "save_notebook", "move_notebook", "notebook_folders", "notebook_deleted"];
const READS = ["git_info", "git_status", "git_branches", "git_log", "git_settings", "git_diff", "git_ops", "git_op_status", "git_locations",
  "git_capacity_diff", "git_platform_diff", "notebook_places", "notebook_listing", "list_notebooks", "open_notebook", "notebook_folders", "notebook_deleted",
  "list_workspaces", "list_lakehouses", "list_items"];
const WRITES = ["git_clone_start", "git_op_cancel", "move_notebook", "git_set_locations", "git_capacity_write", "git_platform_export",
  "git_open", "git_init", "git_create", "git_forget", "git_set_kind", "git_stage", "git_stage_all", "git_commit", "git_discard",
  "git_platform_deploy", "save_notebook", "rename_notebook", "notebook_delete"];

// Fabric, as the app's own calls list it (fabric_api.py list_workspaces / list_lakehouses / list_items).
const WORKSPACES = [{ id: "ws-dev", displayName: "Sales Dev", type: "Workspace" }, { id: "ws-prod", displayName: "Sales Prod", type: "Workspace" }];
const LAKES = { "ws-dev": [{ id: "lh-bronze", displayName: "Bronze" }, { id: "lh-silver", displayName: "Silver" }],
  "ws-prod": [{ id: "lh-gold", displayName: "Gold" }] };
const ITEMS = { "ws-dev": [{ id: "nb-1", displayName: "Load", type: "Notebook" }], "ws-prod": [{ id: "sql-orders", displayName: "Orders DB", type: "SQLDatabase" }] };
let lakesHeld = null;
let errorsFrom = 0;  // a promise list_lakehouses waits on, as a slow Fabric answer would

// git_service.py git_capacity_write / git_platform_export. `plan.uncommitted`: the engine finds files
// with changes that aren't committed, so the first call (no confirm) answers needs_confirm with the text
// capacity_files.write / platform_files.export raise. `plan.refuse`: the engine's GitError text.
let plan = {};
let statusOf = {};  // repository root -> a real git_status answer, for the `status` a write returns
const capacityWrite = ({ repo, confirm }) => {
  if (plan.refuse) throw new Error(plan.refuse);
  if (plan.uncommitted && !confirm) {
    return { needs_confirm: true, message: "capacity.toml has changes that aren't committed. Confirm to replace them with what Fabric shows, or commit them first." };
  }
  return { written: ["capacity.toml"], capacities: 2, kept: ["Finance F8"], kept_workspaces: [], workspaces: 3, status: statusOf[repo] };
};
const platformExport = ({ repo, confirm }) => {
  if (plan.refuse) throw new Error(plan.refuse);
  const held = ["domains/sales.toml", "sources/workday.toml"];
  if (plan.uncommitted && !confirm) {
    return { needs_confirm: true, changes: held, message: `2 of these files have changes that aren't committed: ${held.join(", ")}. Confirm to replace them with what is deployed, or commit them first.` };
  }
  return { written: ["domains/sales.toml", "domains/hr.toml", "sources/workday.toml"], unchanged: 4, left: [{ kind: "domains", id: "old" }],
    skipped: [], replaced_uncommitted: confirm && plan.uncommitted ? held : [], disagree: [], policies_set_up: true, status: statusOf[repo] };
};

const mocks = {
  readiness_check: () => [],
  list_workspaces: () => WORKSPACES,
  list_lakehouses: async ({ workspace_id }) => { if (lakesHeld) await lakesHeld; return LAKES[workspace_id] || []; },
  list_items: ({ workspace_id }) => ITEMS[workspace_id] || [],
  // git_service.py git_capacity_diff: capacity_files.compare items, summary counts.
  git_capacity_diff: () => ({ items: [{ capacity_id: "cap-1", name: "Sales F64", fabric_name: "Sales F64", state: "same", fields: [],
    workspaces: { same: 2, elsewhere: [], not_visible: [], not_in_git: [] } }], counts: { same: 1, differs: 0, not_in_git: 0, not_visible: 0 },
  problems: [], read_at: "2026-11-01T08:00:00Z" }),
  git_capacity_write: capacityWrite,
  // git_service.py git_platform_diff: platform_files.compare items.
  git_platform_diff: ({ env }) => ({ env, items: [{ kind: "domains", id: "sales", state: "same", changes: [] }],
    counts: { same: 1, changed: 0, only_git: 0, only_deployed: 0 }, problems: [], policies_set_up: true, labels: {} }),
  git_platform_export: platformExport,
};

const git = (cwd, ...args) => execFileSync("git", ["-c", "user.name=Test", "-c", "user.email=test@example.com", ...args], { cwd, stdio: "pipe" }).toString();

before(async () => {
  if (skip) return;
  // A clone over ssh to a server that never answers, so the test can cancel one mid-way: Git runs this
  // in place of ssh (cli.environment keeps GIT_SSH_COMMAND when it is set).
  process.env.GIT_SSH_COMMAND = "sleep 60; true";
  app = await openApp({ engine: ENGINE, mocks });
  delete process.env.GIT_SSH_COMMAND;
  home = app.engineHome;
  await startupReads(app);
  // A repository to clone from, with one commit.
  upstream = path.join(home, "upstream", "orders");
  fs.mkdirSync(upstream, { recursive: true });
  git(upstream, "init", "-q", "-b", "main");
  fs.writeFileSync(path.join(upstream, "README.md"), "# Orders\n");
  git(upstream, "add", "README.md");
  git(upstream, "commit", "-q", "-m", "First");
  const made = await page().evaluate(async (base) => {
    const make = async (name, kind) => (await rpc("git_create", { path: `${base}/${name}`, kind })).root;
    return { salesNb: await make("sales-nb", "notebooks"), financeNb: await make("finance-nb", "notebooks"),
      capRepo: await make("capacity", "capacity"), platRepo: await make("platform", "platform") };
  }, (fs.mkdirSync(path.join(home, "repos")), path.join(home, "repos")));
  ({ salesNb, financeNb, capRepo, platRepo } = made);
});
after(async () => { if (app) await app.close(); });

beforeEach(async () => {
  if (skip) return;
  plan = {};
  lakesHeld = null;
  errorsFrom = app.errors.length;
  await page().evaluate(() => {
    closeMenu(); closeDialog();
    const t = document.getElementById("toast"); t.hidden = true; t.textContent = "";
    state.user = null;
    state.workspaces = null;
    Object.assign(gitPage, { info: null, repo: null, status: null, tab: "changes", selected: null, diff: null, branches: null,
      log: null, settings: null, message: "" });
    gitPage.platform.key = null; gitPage.capacity.key = null;
    go("home");
  });
});

const writes = (from) => app.calls.slice(from).filter((c) => WRITES.includes(c.method)).map((c) => ({ method: c.method, params: c.params }));
const pause = (ms) => new Promise((r) => setTimeout(r, ms));
// The message shown since the last clearToast(), so a test never reads the one before.
const clearToast = () => page().evaluate(() => { const t = document.getElementById("toast"); t.hidden = true; t.textContent = ""; });
const toastText = async () => (await page().waitForFunction(() => { const t = document.getElementById("toast"); return !t.hidden && t.textContent; })).jsonValue();
async function until(fn, what, ms = 15000) {
  for (const end = Date.now() + ms; !(await fn());) {
    if (Date.now() > end) throw new Error(`timed out waiting for ${what}`);
    await pause(50);
  }
}

// Source control open on `root` and `tab`, once that tab has drawn.
async function openSource(root, tab = "changes") {
  await page().evaluate(([root, tab]) => { gitPage.repo = root; gitPage.tab = tab; go("source"); }, [root, tab]);
  await page().locator(".git-page .subtabs").waitFor();
  await page().locator(`.git-page .subtabs [role=tab][aria-selected=true]`).waitFor();
}
const dialog = (name) => page().getByRole("dialog", { name });
async function cancel(d, from, what) {
  await d.getByRole("button", { name: "Cancel" }).click();
  await d.waitFor({ state: "detached" });
  await pause(200);
  assert.deepStrictEqual(writes(from), [], `Cancel at ${what} sent something`);
}
// A refusal reads as the engine's sentence: no Python or Git exception name, and it ends in a full stop.
function aSentence(text) {
  assert.doesNotMatch(text, /\b(GitError|ValueError|OSError|Traceback|Exception)\b/);
  assert.match(text.trim(), /[.!?]$/);
}
async function noSeriousAxe(scope) {
  if (!axeSource) return;
  assert.deepStrictEqual(await axeFindings(page(), scope), []);
}

// ---------- Clone ----------

async function openCloneDialog() {
  await clearToast();
  await openSource(salesNb);
  await page().locator(".page-head button[title='Switch repository']").click();
  await page().getByRole("menuitem", { name: "Clone a repository" }).click();
  const d = dialog("Clone a repository");
  await d.waitFor();
  return d;
}

test("Clone asks for the address and folder; Cancel sends nothing, and Clone sends exactly what was typed and opens the clone", { skip }, async () => {
  const parent = path.join(home, "clones");
  let from = app.calls.length;
  let d = await openCloneDialog();
  await d.getByRole("textbox", { name: "Repository address" }).fill(upstream);
  await d.getByRole("textbox", { name: "Into this folder" }).fill(parent);
  await noSeriousAxe(".dialog");
  await cancel(d, from, "the clone dialog");
  assert.strictEqual(fs.existsSync(parent), false, "Cancel made the folder");

  d = await openCloneDialog();
  from = app.calls.length;
  await d.getByRole("textbox", { name: "Repository address" }).fill(upstream);
  await d.getByRole("textbox", { name: "Into this folder" }).fill(parent);
  await d.getByRole("textbox", { name: "Folder name (optional)" }).fill("orders-copy");
  await d.getByRole("button", { name: "Clone", exact: true }).click();
  await d.waitFor({ state: "detached" });
  const toast = await toastText();
  const root = fs.realpathSync(path.join(parent, "orders-copy"));
  assert.strictEqual(toast.split("\n")[0], `Cloned into ${root}.`);
  assert.deepStrictEqual(writes(from), [{ method: "git_clone_start", params: { url: upstream, parent, name: "orders-copy" } }]);
  assert.strictEqual(fs.readFileSync(path.join(root, "README.md"), "utf8"), "# Orders\n");
  await page().locator(".page-head button[title='Switch repository']").filter({ hasText: "orders-copy" }).waitFor();
  assert.deepStrictEqual(app.errors.slice(errorsFrom), []);
});

test("Clone into a folder that isn't empty is refused in a sentence and nothing is overwritten; a blank name sends null", { skip }, async () => {
  const parent = path.join(home, "clones");
  fs.mkdirSync(path.join(parent, "orders-copy"), { recursive: true });
  const keep = path.join(parent, "orders-copy", "README.md");
  fs.writeFileSync(keep, "mine\n");
  let from = app.calls.length;
  const d = await openCloneDialog();
  await d.getByRole("textbox", { name: "Repository address" }).fill(upstream);
  await d.getByRole("textbox", { name: "Into this folder" }).fill(parent);
  await d.getByRole("textbox", { name: "Folder name (optional)" }).fill("orders-copy");
  await d.getByRole("button", { name: "Clone", exact: true }).click();
  const err = d.locator(".field-error");
  await until(async () => (await err.textContent()).length > 0, "the refusal");
  const said = await err.textContent();
  assert.match(said, /orders-copy already exists and isn't empty\. Choose another folder or name\./);
  aSentence(said);
  assert.strictEqual(fs.readFileSync(keep, "utf8"), "mine\n", "the folder's file was replaced");
  assert.strictEqual(await d.count(), 1, "the dialog closed on a refusal");
  assert.deepStrictEqual(writes(from), [{ method: "git_clone_start", params: { url: upstream, parent, name: "orders-copy" } }]);

  // A blank name: the engine names the folder after the repository.
  from = app.calls.length;
  await d.getByRole("textbox", { name: "Folder name (optional)" }).fill("");
  await d.getByRole("button", { name: "Clone", exact: true }).click();
  await d.waitFor({ state: "detached" });
  await toastText();
  assert.deepStrictEqual(writes(from), [{ method: "git_clone_start", params: { url: upstream, parent, name: null } }]);
  assert.ok(fs.existsSync(path.join(parent, "orders", "README.md")));
  assert.deepStrictEqual(app.errors.slice(errorsFrom), []);
});

test("Cancel on a running clone sends that clone's id only, the band goes, and the clone leaves no folder", { skip }, async () => {
  const parent = path.join(home, "clones");
  const d = await openCloneDialog();
  await d.getByRole("textbox", { name: "Repository address" }).fill("ssh://git.example.invalid/contoso/slow.git");
  await d.getByRole("textbox", { name: "Into this folder" }).fill(parent);
  const from = app.calls.length;
  await d.getByRole("button", { name: "Clone", exact: true }).click();
  await d.waitFor({ state: "detached" });
  const band = page().locator("#git-op.git-op");
  await band.waitFor();
  const started = app.calls.slice(from).find((c) => c.method === "git_clone_start");
  assert.deepStrictEqual(started.params, { url: "ssh://git.example.invalid/contoso/slow.git", parent, name: null });
  const id = await page().evaluate(() => gitPage.op?.id);
  assert.match(id, /^[0-9a-f]{12}$/);
  await noSeriousAxe("#git-op");
  const mark = app.calls.length;
  await band.getByRole("button", { name: "Cancel" }).click();
  await band.waitFor({ state: "detached" });
  assert.deepStrictEqual(writes(mark), [{ method: "git_op_cancel", params: { id } }]);
  const op = await page().evaluate((id) => rpc("git_op_status", { id }), id);
  assert.strictEqual(op.status, "cancelled");
  assert.strictEqual(fs.existsSync(path.join(parent, "slow")), false, "the cancelled clone left its folder");
  assert.deepStrictEqual(app.errors.slice(errorsFrom), []);
});

// ---------- Move a notebook ----------

const nbFile = (folder, name) => path.join(folder, `${name}.ipynb`);
async function moveFromList(name) {
  await clearToast();
  await page().evaluate(() => { nbFiles.query = ""; go("notebooks"); });
  await page().getByRole("button", { name: `More actions for ${name}` }).click();
  await page().getByRole("menuitem", { name: "Move to another place…" }).click();
  const d = dialog(`Move ${name}`);
  await d.waitFor();
  return d;
}

test("Move a notebook lists the other places; Cancel sends nothing, and Move sends that notebook and the place picked", { skip }, async () => {
  const appFolder = await page().evaluate(async () => (await rpc("git_info")).notebooks_folder);
  await page().evaluate(() => rpc("save_notebook", { name: "Daily load", cells: [{ type: "code", source: "rows = 1" }] }));
  let from = app.calls.length;
  let d = await moveFromList("Daily load");
  const options = await d.getByRole("combobox", { name: "Move to" }).locator("option").allTextContents();
  // notebook_places: the repositories most recently opened first; the place it's in now isn't offered.
  assert.deepStrictEqual(options, ["finance-nb", "sales-nb"]);
  await noSeriousAxe(".dialog");
  await cancel(d, from, "the move dialog");
  assert.ok(fs.existsSync(nbFile(appFolder, "Daily load")), "Cancel moved the file");

  // Pick the second repository, not the first offered.
  d = await moveFromList("Daily load");
  await d.getByRole("combobox", { name: "Move to" }).selectOption({ label: "sales-nb" });
  from = app.calls.length;
  await d.getByRole("button", { name: "Move", exact: true }).click();
  await d.waitFor({ state: "detached" });
  assert.strictEqual((await toastText()).split("\n")[0], "Daily load is now in sales-nb. Commit it there to keep it.");
  assert.deepStrictEqual(writes(from), [{ method: "move_notebook", params: { name: "Daily load", place: salesNb } }]);
  assert.ok(fs.existsSync(nbFile(path.join(salesNb, "notebooks"), "Daily load")));
  assert.ok(!fs.existsSync(nbFile(appFolder, "Daily load")));
  assert.ok(!fs.existsSync(nbFile(path.join(financeNb, "notebooks"), "Daily load")));

  // And back to This PC: the app's own folder is sent as null.
  d = await moveFromList("Daily load");
  await d.getByRole("combobox", { name: "Move to" }).selectOption({ label: "This PC" });
  from = app.calls.length;
  await d.getByRole("button", { name: "Move", exact: true }).click();
  await d.waitFor({ state: "detached" });
  assert.strictEqual((await toastText()).split("\n")[0], "Daily load is now in the app's notebooks folder.");
  assert.deepStrictEqual(writes(from), [{ method: "move_notebook", params: { name: "Daily load", place: null } }]);
  assert.ok(fs.existsSync(nbFile(appFolder, "Daily load")));
  assert.deepStrictEqual(app.errors.slice(errorsFrom), []);
});

test("Moving onto a notebook already there is refused in a sentence: nothing moves and nothing is overwritten", { skip }, async () => {
  const appFolder = await page().evaluate(async () => (await rpc("git_info")).notebooks_folder);
  await page().evaluate(() => rpc("save_notebook", { name: "Weekly load", cells: [{ type: "code", source: "mine = 1" }] }));
  const theirs = nbFile(path.join(salesNb, "notebooks"), "Weekly load");
  fs.mkdirSync(path.dirname(theirs), { recursive: true });
  fs.writeFileSync(theirs, "{\"theirs\": true}");
  const d = await moveFromList("Weekly load");
  await d.getByRole("combobox", { name: "Move to" }).selectOption({ label: "sales-nb" });
  const from = app.calls.length;
  await d.getByRole("button", { name: "Move", exact: true }).click();
  const toast = await toastText();
  assert.strictEqual(toast.split("\n")[0], "Couldn't move the notebook: A notebook named 'Weekly load' is already in sales-nb. Nothing was moved.");
  aSentence(toast.split("\n")[0]);
  assert.deepStrictEqual(writes(from), [{ method: "move_notebook", params: { name: "Weekly load", place: salesNb } }]);
  assert.strictEqual(fs.readFileSync(theirs, "utf8"), "{\"theirs\": true}");
  assert.match(fs.readFileSync(nbFile(appFolder, "Weekly load"), "utf8"), /mine = 1/);
  assert.strictEqual(await d.count(), 1, "the dialog closed on a refusal");
  assert.deepStrictEqual(app.errors.slice(errorsFrom), []);
});

// ---------- Data locations (locations.toml) ----------

async function openLocations() {
  await clearToast();
  await page().evaluate(() => { state.user = "chris@contoso.com"; });
  await openSource(salesNb);
  await page().getByRole("button", { name: "Data locations" }).click();
  const d = dialog("Where sales-nb find their data");
  await d.waitFor();
  return d;
}
const sel = (d, name) => d.getByRole("combobox", { name });
async function pick(d, name, label) {
  const s = sel(d, name);
  await until(async () => (await s.locator("option").allTextContents()).includes(label), `${label} in ${name}`);
  await s.selectOption({ label });
}

test("Data locations: Cancel sends nothing, and Save sends exactly the workspace, lakehouse and SQL database picked per environment", { skip }, async () => {
  const file = path.join(salesNb, "locations.toml");
  const before = fs.existsSync(file) ? fs.readFileSync(file, "utf8") : null;
  let from = app.calls.length;
  let d = await openLocations();
  await pick(d, "dev workspace", "Sales Dev");
  await noSeriousAxe(".dialog");
  await cancel(d, from, "the locations dialog");
  assert.strictEqual(fs.existsSync(file) ? fs.readFileSync(file, "utf8") : null, before, "Cancel wrote locations.toml");

  d = await openLocations();
  await pick(d, "dev workspace", "Sales Dev");
  await pick(d, "dev lakehouse", "Silver");
  await pick(d, "test workspace", "This PC");
  await pick(d, "prod workspace", "Sales Prod");
  await pick(d, "prod lakehouse", "Gold");
  await pick(d, "prod SQL database", "Orders DB");
  from = app.calls.length;
  await d.getByRole("button", { name: "Save locations" }).click();
  await d.waitFor({ state: "detached" });
  assert.strictEqual((await toastText()).split("\n")[0], "Locations saved. Commit locations.toml to share them.");
  assert.deepStrictEqual(writes(from), [{ method: "git_set_locations", params: { repo: salesNb, envs: {
    dev: { workspace_id: "ws-dev", workspace_name: "Sales Dev", lakehouse_id: "lh-silver", lakehouse_name: "Silver" },
    test: { this_pc: true },
    prod: { workspace_id: "ws-prod", workspace_name: "Sales Prod", lakehouse_id: "lh-gold", lakehouse_name: "Gold", sql_id: "sql-orders", sql_name: "Orders DB" } } } }]);
  const text = fs.readFileSync(file, "utf8");
  assert.match(text, /\[dev\]\nworkspace_id = "ws-dev"\nworkspace_name = "Sales Dev"\nlakehouse_id = "lh-silver"\nlakehouse_name = "Silver"/);
  assert.match(text, /\[test\]\nruns_on = "this_pc"/);
  assert.match(text, /sql_id = "sql-orders"/);
  // The page shows the file as a change to commit.
  await page().locator(".git-changes .git-name", { hasText: /^locations\.toml/ }).waitFor();

  // Opened again, it shows what was saved.
  d = await openLocations();
  await until(async () => (await sel(d, "prod SQL database").inputValue()) === "sql-orders", "the saved SQL database");
  assert.strictEqual(await sel(d, "dev lakehouse").inputValue(), "lh-silver");
  assert.strictEqual(await sel(d, "test workspace").inputValue(), "this_pc");
  await cancel(d, app.calls.length, "the locations dialog");
  assert.deepStrictEqual(app.errors.slice(errorsFrom), []);
});

// Fabric's lakehouse list can take seconds; until it comes, the dialog used to show "—" and Save wrote
// every environment's lakehouse and SQL database out of locations.toml (fixed in gitLocationsDialog).
test("Saving locations before Fabric's lists arrive keeps each environment's saved lakehouse and SQL database", { skip }, async () => {
  await page().evaluate((r) => rpc("git_set_locations", { repo: r, envs: {
    dev: { workspace_id: "ws-dev", workspace_name: "Sales Dev", lakehouse_id: "lh-silver", lakehouse_name: "Silver" },
    test: { this_pc: true },
    prod: { workspace_id: "ws-prod", workspace_name: "Sales Prod", lakehouse_id: "lh-gold", lakehouse_name: "Gold", sql_id: "sql-orders", sql_name: "Orders DB" } } }), salesNb);
  let release;
  lakesHeld = new Promise((r) => { release = r; });
  const d = await openLocations();
  await until(async () => (await sel(d, "prod workspace").inputValue()) === "ws-prod", "the saved prod workspace");
  const from = app.calls.length;
  await pick(d, "test workspace", "Not set");
  await d.getByRole("button", { name: "Save locations" }).click();
  await d.waitFor({ state: "detached" });
  release();
  const sent = writes(from);
  assert.strictEqual(sent.length, 1);
  assert.deepStrictEqual(sent[0].params.envs, {
    dev: { workspace_id: "ws-dev", workspace_name: "Sales Dev", lakehouse_id: "lh-silver", lakehouse_name: "Silver" },
    test: null,
    prod: { workspace_id: "ws-prod", workspace_name: "Sales Prod", lakehouse_id: "lh-gold", lakehouse_name: "Gold", sql_id: "sql-orders", sql_name: "Orders DB" } });
  assert.match(fs.readFileSync(path.join(salesNb, "locations.toml"), "utf8"), /lakehouse_id = "lh-gold"/);
  assert.deepStrictEqual(app.errors.slice(errorsFrom), []);
});

test("A location the engine refuses is said in a sentence in the dialog, which stays open, and the file is left as it was", { skip }, async () => {
  const file = path.join(salesNb, "locations.toml");
  const was = fs.readFileSync(file, "utf8");
  LAKES["ws-dev"].push({ id: "lh-bad", displayName: "Bronze\tcopy" });
  try {
    const d = await openLocations();
    await until(async () => (await sel(d, "dev lakehouse").locator("option[value=lh-bad]").count()) === 1, "the lakehouse list");
    await sel(d, "dev lakehouse").selectOption("lh-bad");
    const from = app.calls.length;
    await d.getByRole("button", { name: "Save locations" }).click();
    const err = d.locator(".field-error").last();
    await until(async () => (await err.textContent()).length > 0, "the refusal");
    const said = await err.textContent();
    assert.strictEqual(said, "The dev lakehouse name isn't valid.");
    aSentence(said);
    assert.strictEqual(writes(from).length, 1);
    assert.strictEqual(fs.readFileSync(file, "utf8"), was);
    assert.strictEqual(await d.count(), 1);
  } finally {
    LAKES["ws-dev"].pop();
  }
  assert.deepStrictEqual(app.errors.slice(errorsFrom), []);
});

// ---------- Capacity: Write Fabric's state to capacity.toml ----------

async function startCapacityWrite() {
  await clearToast();
  statusOf[capRepo] = await page().evaluate((r) => rpc("git_status", { repo: r }), capRepo);
  await openSource(capRepo, "capacity");
  await page().getByText("Sales F64").waitFor();
  await page().getByRole("button", { name: "Write Fabric's state to capacity.toml…" }).click();
  const d = dialog("Write Fabric's state to capacity.toml?");
  await d.waitFor();
  return d;
}
const capWrite = (extra = {}) => ({ method: "git_capacity_write", params: { repo: capRepo, ...extra } });

test("Write capacity.toml asks first; Cancel sends nothing, and Write the file sends the repository without confirm", { skip }, async () => {
  let from = app.calls.length;
  let d = await startCapacityWrite();
  assert.match(await d.innerText(), /Replaces the file with the capacities, settings and workspaces Fabric shows you now\. .*Nothing in Fabric changes/);
  await noSeriousAxe(".dialog");
  await cancel(d, from, "Write Fabric's state");
  d = await startCapacityWrite();
  from = app.calls.length;
  await d.getByRole("button", { name: "Write the file" }).click();
  assert.strictEqual((await toastText()).split("\n")[0],
    "Wrote 2 capacities and 3 workspaces. Review the change, then commit. Kept 1 capacity you can't see.");
  assert.deepStrictEqual(writes(from), [capWrite()]);
  assert.strictEqual(await page().getByRole("dialog").count(), 0, "it asked again with nothing uncommitted");
  assert.deepStrictEqual(app.errors.slice(errorsFrom), []);
});

test("Write capacity.toml over uncommitted changes asks again in a danger confirm, and confirm: true goes only after it", { skip }, async () => {
  plan.uncommitted = true;
  let from = app.calls.length;
  let d = await startCapacityWrite();
  await d.getByRole("button", { name: "Write the file" }).click();
  let again = dialog("Replace changes you haven't committed?");
  await again.waitFor();
  assert.match(await again.innerText(), /capacity\.toml has changes that aren't committed\. Confirm to replace them/);
  assert.match(await again.locator(".dialog-actions button").last().getAttribute("class"), /danger-solid/);
  assert.deepStrictEqual(writes(from), [capWrite()], "confirm: true went before the person agreed");
  await noSeriousAxe(".dialog");
  await again.getByRole("button", { name: "Cancel" }).click();
  await again.waitFor({ state: "detached" });
  await pause(200);
  assert.deepStrictEqual(writes(from), [capWrite()], "Cancel on the second confirm sent something");

  from = app.calls.length;
  d = await startCapacityWrite();
  await d.getByRole("button", { name: "Write the file" }).click();
  again = dialog("Replace changes you haven't committed?");
  await again.getByRole("button", { name: "Replace and write" }).click();
  assert.match((await toastText()).split("\n")[0], /^Wrote 2 capacities and 3 workspaces\./);
  assert.deepStrictEqual(writes(from), [capWrite(), capWrite({ confirm: true })]);
  assert.deepStrictEqual(app.errors.slice(errorsFrom), []);
});

test("Write capacity.toml refused by the engine says why in a sentence and sends nothing more", { skip }, async () => {
  plan.refuse = "GitError: capacity.toml line 4: sku isn't a capacity size. Fix the file first, so nothing in it is lost.";
  const d = await startCapacityWrite();
  const from = app.calls.length;
  await d.getByRole("button", { name: "Write the file" }).click();
  const toast = (await toastText()).split("\n")[0];
  assert.strictEqual(toast, "Couldn't write capacity.toml: capacity.toml line 4: sku isn't a capacity size. Fix the file first, so nothing in it is lost.");
  aSentence(toast);
  assert.deepStrictEqual(writes(from), [capWrite()]);
  assert.strictEqual(await page().getByRole("dialog").count(), 0);
  assert.deepStrictEqual(app.errors.slice(errorsFrom), []);
});

// ---------- Platform: Export from platform ----------

async function startExport() {
  await clearToast();
  statusOf[platRepo] = await page().evaluate((r) => rpc("git_status", { repo: r }), platRepo);
  await openSource(platRepo, "platform");
  await page().getByText("Same as deployed.").waitFor();
  await page().getByRole("button", { name: "Export from platform…" }).click();
  const d = dialog("Export from the platform?");
  await d.waitFor();
  return d;
}
const exportCall = (extra = {}) => ({ method: "git_platform_export", params: { repo: platRepo, ...extra } });

test("Export from platform asks first; Cancel sends nothing, and Export sends the repository without confirm", { skip }, async () => {
  let from = app.calls.length;
  let d = await startExport();
  assert.match(await d.innerText(), /Nothing is committed and nothing in Fabric changes\..*Source connections, keys, requests and grants are never exported/s);
  await noSeriousAxe(".dialog");
  await cancel(d, from, "Export from the platform");
  d = await startExport();
  from = app.calls.length;
  await d.getByRole("button", { name: "Export", exact: true }).click();
  assert.strictEqual((await toastText()).split("\n")[0], "Exported 3 files. Review them in Changes, then commit. 1 not deployed left as they are.");
  assert.deepStrictEqual(writes(from), [exportCall()]);
  assert.strictEqual(await page().getByRole("dialog").count(), 0);
  assert.deepStrictEqual(app.errors.slice(errorsFrom), []);
});

test("Export over uncommitted files asks again in a danger confirm naming them, and confirm: true goes only after it", { skip }, async () => {
  plan.uncommitted = true;
  let from = app.calls.length;
  let d = await startExport();
  await d.getByRole("button", { name: "Export", exact: true }).click();
  let again = dialog("Replace files you haven't committed?");
  await again.waitFor();
  assert.match(await again.innerText(), /2 of these files have changes that aren't committed: domains\/sales\.toml, sources\/workday\.toml\./);
  assert.match(await again.locator(".dialog-actions button").last().getAttribute("class"), /danger-solid/);
  assert.deepStrictEqual(writes(from), [exportCall()], "confirm: true went before the person agreed");
  await noSeriousAxe(".dialog");
  await again.getByRole("button", { name: "Cancel" }).click();
  await again.waitFor({ state: "detached" });
  await pause(200);
  assert.deepStrictEqual(writes(from), [exportCall()], "Cancel on the second confirm sent something");

  from = app.calls.length;
  d = await startExport();
  await d.getByRole("button", { name: "Export", exact: true }).click();
  again = dialog("Replace files you haven't committed?");
  await again.getByRole("button", { name: "Replace and export" }).click();
  assert.match((await toastText()).split("\n")[0], /^Exported 3 files\./);
  assert.deepStrictEqual(writes(from), [exportCall(), exportCall({ confirm: true })]);
  assert.deepStrictEqual(app.errors.slice(errorsFrom), []);
});

test("Export refused by the engine says why in a sentence and sends nothing more", { skip }, async () => {
  plan.refuse = "GitError: The platform's functions are older than this app. Open Platform > Settings, choose Finish or repair, then try again.";
  const d = await startExport();
  const from = app.calls.length;
  await d.getByRole("button", { name: "Export", exact: true }).click();
  const toast = (await toastText()).split("\n")[0];
  assert.strictEqual(toast, "Couldn't export: The platform's functions are older than this app. Open Platform > Settings, choose Finish or repair, then try again.");
  aSentence(toast);
  assert.deepStrictEqual(writes(from), [exportCall()]);
  assert.deepStrictEqual(app.errors.slice(errorsFrom), []);
});

// ---------- Opening sends no write ----------

test("Opening Source control on each kind of repository, the notebooks list and each write's dialog sends only reads", { skip }, async () => {
  await page().evaluate(() => rpc("save_notebook", { name: "Read only", cells: [{ type: "code", source: "" }] }));
  const from = app.calls.length;
  await (await openLocations()).getByRole("button", { name: "Cancel" }).click();
  await openSource(capRepo, "capacity");
  await page().getByText("Sales F64").waitFor();
  await openSource(platRepo, "platform");
  await page().getByText("Same as deployed.").waitFor();
  await (await openCloneDialog()).getByRole("button", { name: "Cancel" }).click();
  await (await moveFromList("Read only")).getByRole("button", { name: "Cancel" }).click();
  await pause(300);
  const sent = [...new Set(app.calls.slice(from).map((c) => c.method))];
  assert.deepStrictEqual(writes(from), []);
  assert.deepStrictEqual(sent.filter((m) => !READS.includes(m)), [], "a call that isn't a known read");
  for (const m of ["git_info", "git_status", "git_locations", "git_capacity_diff", "git_platform_diff", "notebook_places"]) {
    assert.ok(sent.includes(m), `${m} wasn't read, so this test didn't open that page`);
  }
  assert.deepStrictEqual(app.errors.slice(errorsFrom), []);
});
