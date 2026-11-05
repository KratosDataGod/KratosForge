// Git-native Phase 2: Source control > Fabric workspace (see harness.cjs for how to run it).
// No link: the tab says so and offers Link a workspace; the link dialog asks Fabric what the workspace
// follows, says whether a remote here points there, and links. A workspace on another repository asks
// first, and Cancel sends no link. Linked: this PC, the Git remote and the workspace side by side, and
// Check status in Fabric lists the items that differ, conflicts first. Unlink asks first. Nothing on
// this tab writes to Fabric: the only writes are fabric_git_link and fabric_git_unlink, which change
// fabric-desktop.toml in the repository. axe finds nothing serious on the tab or the dialog.
//
// The engine is mocked in the shapes sidecar/fabric_desktop/fabric_git.py and fabric_git_service.py
// return. FD_SHOTS=<folder> also saves screenshots of each state.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { playwright, openApp, startupReads, axeSource, axeFindings, animationsDone } = require("./harness.cjs");
// axe covers dialogs (#1673, ui.test.cjs): the cases below run axe while these are open.
// axe covers dialogs: git.js:gitFabricLinkDialog, git.js:gitFabricUpdateDialog

let app;
const skip = !playwright && "Playwright is not installed";
const page = () => app.page;
const REPO = "C:/src/sales";
const WS = "11111111-2222-3333-4444-555555555555";
const OTHER_WS = "99999999-2222-3333-4444-555555555555";
const SHA = (c) => c.repeat(40);
const copy = (x) => JSON.parse(JSON.stringify(x));
const WRITES = ["fabric_git_link", "fabric_git_unlink"];
const updates = [];  // fabric_git_update calls that carried a confirm_name: the only ones that may write

const CONN = { state: "ConnectedAndInitialized", connected: true, initialized: true, selective: false, provider: "github",
  provider_label: "GitHub", owner: "contoso", project: "", repository: "sales", branch: "main", folder: "/fabric", host: "",
  synced_head: SHA("7"), last_sync: "2026-11-03T09:00:00Z" };
const AZDO = { ...CONN, provider: "azure_devops", provider_label: "Azure DevOps", project: "Data", repository: "finance" };
const LINK = { workspace_id: WS, workspace_name: "Sales Dev", provider: "github", owner: "contoso", repository: "sales",
  branch: "main", folder: "/fabric" };
const ALIGN = {
  local: { branch: "feature/orders-silver", commit: SHA("4"), uncommitted: 3, ahead_of_remote: 2, behind_remote: 0 },
  remote: { name: "origin", branch: "main", commit: SHA("9"), ahead_of_workspace: 1, behind_workspace: 0 },
  workspace: { commit: SHA("7"), last_sync: "2026-11-03T09:00:00Z", known_here: true, folder: "/fabric" },
  notes: ["You're on feature/orders-silver; the workspace follows main. Your commits reach the workspace only after they're on main and someone updates it from Git.",
    "Pushing to Git doesn't update the workspace: it changes only when someone chooses Update from Git."],
};
const row = (name, label, ws, remote, conflict, state, support = "yes", limits = []) => ({ item_type: label, label, name, logical_id: "", object_id: "",
  workspace_change: ws, remote_change: remote, conflict, state, git_support: support, round_trip: support, limits });
const DATA_STAYS = "Tables and files never go through Git or deployment pipelines; only the definition does";
const STATUS = { workspace_head: SHA("7"), remote_commit: SHA("9"), fingerprint: "abc", aligned: false,
  remote_tracking: SHA("9"), remote_fetched_matches: true,
  counts: { conflict: 1, both_changed: 0, incoming: 1, outgoing: 2, same_change: 0, unchanged: 0 },
  items: [row("NB_Orders_Silver", "Notebook", "modified", "modified", "conflict", "conflict"),
    row("PL_Daily_Load", "Data pipeline", "", "modified", "", "incoming"),
    row("LH_Silver", "Lakehouse", "modified", "", "", "outgoing", "yes", [DATA_STAYS]),
    row("SM_Sales", "Semantic model", "added", "", "", "outgoing", "preview")] };

let db;
function fresh() { return { link: null }; }

function status() {
  return copy({ commit: SHA("4"), branch: "feature/orders-silver", upstream: null, ahead: 0, behind: 0, root: REPO, name: "sales",
    staged: [], unstaged: [], untracked: [], conflicts: [], changed: 0, dirty: false, merging: false, stashes: 0,
    remotes: [{ name: "origin", url: "https://github.com/contoso/sales.git", provider: "github", web: "https://github.com/contoso/sales" }],
    fetched_at: "2026-11-03T08:00:00+00:00", links: null, summary: "feature/orders-silver, no uncommitted changes." });
}
const guard = (fn) => (p) => {
  if (p.repo !== REPO) throw new Error("GitError: Open this repository in Source control first.");
  return fn(p);
};

const mocks = {
  readiness_check: () => [],
  environment: () => ({ python: "Python 3 (test)", user: "chris@contoso.com" }),
  list_workspaces: () => [{ id: WS, displayName: "Sales Dev" }, { id: OTHER_WS, displayName: "Finance Dev" }],
  git_info: () => ({ installed: true, version: "2.47.1", repos: [{ path: REPO, name: "sales", exists: true, kind: null, suggested: null }], current: REPO,
    kinds: {}, notebooks_folder: "C:/Users/chris/KratosForge/notebooks", clone_folder: "C:/Users/chris/source/repos" }),
  git_status: guard(() => status()),
  fabric_git_overview: guard(({ env }) => {
    if (db.overviewFails) { db.overviewFails -= 1; throw new Error("FabricError: Fabric didn't answer in time."); }
    return db.link && env === "dev"
    ? { env, linked: true, link: db.link, problem: null, connection: CONN, changed_in_fabric: [], alignment: ALIGN }
    : { env, linked: false, problem: null, link: null };
  }),
  fabric_git_check: guard(({ workspace_id }) => ({ connection: workspace_id === WS ? CONN : AZDO, remote: workspace_id === WS ? "origin" : null })),
  fabric_git_link: guard(({ workspace_id, confirm }) => {
    if (workspace_id !== WS && !confirm) {
      return { needs_confirm: true, message: "The workspace follows contoso/finance, which none of this repository's remotes points to. Link it anyway?", connection: AZDO };
    }
    db.link = { ...LINK, workspace_id };
    return { envs: { dev: db.link, test: null, prod: null }, problem: null, exists: true, status: status() };
  }),
  fabric_git_unlink: guard(() => { db.link = null; return { envs: { dev: null, test: null, prod: null }, problem: null, exists: true, status: status() }; }),
  fabric_git_status: guard(() => copy(db.status || STATUS)),
  // fabric_git_service.py fabric_git_update: the plan, and a write only with every gate.
  fabric_git_update: guard(({ fingerprint, policy, confirm_name, allow_override }) => {
    const st = db.status || STATUS;
    const plan = { from: SHA("7"), to: SHA("9"), policy: policy || "", conflicts: 1, needs_policy: !policy, nothing: false,
      changes: [{ ...st.items[1], why: "Replaced with the version in Git" }],
      overwrites: [...(policy === "PreferRemote" ? [{ ...st.items[0], why: "lost" }] : []), { ...st.items[2], why: "at risk" }, { ...st.items[3], why: "at risk" }],
      kept: [] };
    const name = db.fabricName || "Sales Dev";
    const base = { env: "dev", plan, confirm_text: name, linked_name: "Sales Dev", name_differs: name !== "Sales Dev",
      allow_override: !!allow_override, fingerprint: st.fingerprint };
    if (fingerprint !== st.fingerprint) return { ...base, stale: true, status: copy(st), done: false };
    if (!policy) return { ...base, needs_choice: true, done: false };
    if (!allow_override) return { ...base, needs_override: true, done: false };
    if (confirm_name !== name) return { ...base, needs_confirm: true, done: false };
    updates.push({ policy, confirm_name, allow_override });
    return { ...base, done: true };
  }),
};

before(async () => {
  if (skip) return;
  app = await openApp({ mocks });
  await startupReads(app);
});
after(async () => { if (app) await app.close(); });

beforeEach(async () => {
  if (skip) return;
  db = fresh();
  await page().evaluate(() => {
    closeMenu(); closeDialog();
    Object.assign(gitPage, { info: null, repo: null, status: null, tab: "changes" });
    Object.assign(gitPage.fabric, { key: null, env: "dev", overview: null, error: null, status: null, statusError: null, checking: false });
    go("home");
  });
});

const writes = (from) => app.calls.slice(from).filter((c) => WRITES.includes(c.method)).map((c) => ({ method: c.method, params: c.params }));
const shot = async (name) => {
  if (!process.env.FD_SHOTS) return;
  await animationsDone(page());
  await page().evaluate(() => { const t = document.getElementById("toast"); t.hidden = true; });
  await page().screenshot({ path: path.join(process.env.FD_SHOTS, `${name}.png`), fullPage: false });
};
async function openTab() {
  await page().evaluate(() => { gitPage.tab = "fabric"; go("source"); });
  await page().locator(".git-page .git-platform").first().waitFor();
}
// axe-core's serious findings in one part of the page ([] when axe-core isn't installed).
const serious = (scope) => (axeSource ? axeFindings(page(), scope) : []);

test("no link yet: the tab says so, and linking asks Fabric first then writes only the link", { skip }, async () => {
  await openTab();
  await page().getByText("No Dev workspace linked").waitFor();
  await shot("1-not-linked");
  const from = app.calls.length;
  await page().getByRole("button", { name: "Link a workspace…" }).click();
  const dialog = page().getByRole("dialog", { name: /Link sales to a Dev workspace/ });
  await dialog.waitFor();
  await dialog.getByLabel("Workspace").selectOption(WS);
  await dialog.getByText("Matches this repository's remote origin").waitFor();
  assert.match(await dialog.innerText(), /Connected to GitHub: contoso\/sales, branch main, folder \/fabric/);
  assert.deepStrictEqual(await serious("#dialog-root .dialog"), []);
  await shot("2-link-dialog");
  assert.deepStrictEqual(writes(from), [], "choosing a workspace sent a write");
  await dialog.getByRole("button", { name: "Link", exact: true }).click();
  await page().locator(".fg-boxes").waitFor();
  assert.deepStrictEqual(writes(from), [{ method: "fabric_git_link", params: { repo: REPO, env: "dev", workspace_id: WS, workspace_name: "Sales Dev", confirm: false } }]);
});

test("a workspace on another repository asks first, and Cancel links nothing", { skip }, async () => {
  await openTab();
  await page().getByRole("button", { name: "Link a workspace…" }).click();
  const dialog = page().getByRole("dialog", { name: /Link sales/ });
  await dialog.getByLabel("Workspace").selectOption(OTHER_WS);
  await dialog.getByText("None of this repository's remotes points there").waitFor();
  const from = app.calls.length;
  await dialog.getByRole("button", { name: "Link", exact: true }).click();
  const ask = page().getByRole("dialog", { name: "Link it anyway?" });
  await ask.waitFor();
  await ask.getByRole("button", { name: "Cancel" }).click();
  await ask.waitFor({ state: "detached" });
  assert.deepStrictEqual(writes(from).map((w) => w.params.confirm), [false], "Cancel still linked");
  assert.strictEqual(db.link, null);
});

test("linked: three commits side by side, then items that differ, conflicts first", { skip }, async () => {
  db.link = copy(LINK);
  await openTab();
  const boxes = page().locator(".fg-box");
  await boxes.first().waitFor();
  assert.deepStrictEqual(await boxes.locator("b").allInnerTexts(), ["feature/orders-silver", "origin/main", "Synced to 7777777"]);
  assert.match(await boxes.nth(0).innerText(), /2 commits not pushed · 3 files not committed/);
  assert.match(await boxes.nth(1).innerText(), /1 commit the workspace doesn't have/);
  assert.match(await page().locator(".fg-notes").innerText(), /Pushing to Git doesn't update the workspace/);
  await shot("3-linked");
  const from = app.calls.length;
  await page().getByRole("button", { name: "Check status in Fabric" }).click();
  await page().locator("tr.fg-conflict").waitFor();
  const names = await page().locator(".git-platform table tbody tr td:first-child").allInnerTexts();
  assert.deepStrictEqual(names, ["NB_Orders_Silver", "PL_Daily_Load", "LH_Silver", "SM_Sales"]);
  assert.match(await page().locator(".git-platform").innerText(), /1 conflict · 1 newer in Git · 2 newer in the workspace/);
  assert.match(await page().locator("tr", { hasText: "SM_Sales" }).innerText(), /Preview in Fabric/);
  assert.match(await page().locator("tr", { hasText: "LH_Silver" }).innerText(), /Full, definition only[\s\S]*Tables and files never go through Git/);
  assert.match(await page().locator("tr", { hasText: "PL_Daily_Load" }).innerText(), /Full$/);
  assert.deepStrictEqual(writes(from), [], "checking status sent a write");
  assert.deepStrictEqual(await serious(".git-page"), []);
  await shot("4-items");
});

test("when the workspace's connection can't be read, the tab says so and Retry reads it again", { skip }, async () => {
  db.link = copy(LINK);
  db.overviewFails = 1;
  try {
    await openTab();
    const panel = page().locator(".git-platform .error-panel");
    await panel.waitFor();
    assert.match(await panel.innerText(), /Couldn't read the workspace's Git connection/);
    await panel.getByRole("button", { name: "Retry" }).click();
    await page().locator(".fg-box").first().waitFor();
  } finally { db.overviewFails = 0; }
});

test("unlink asks first, and only then writes", { skip }, async () => {
  db.link = copy(LINK);
  await openTab();
  await page().locator(".fg-boxes").waitFor();
  const from = app.calls.length;
  await page().getByRole("button", { name: "More" }).click();
  await page().getByRole("menuitem", { name: "Unlink" }).click();
  const ask = page().getByRole("dialog", { name: "Unlink this workspace?" });
  await ask.waitFor();
  assert.deepStrictEqual(writes(from), []);
  await ask.getByRole("button", { name: "Unlink" }).click();
  await page().getByText("No Dev workspace linked").waitFor();
  assert.deepStrictEqual(writes(from), [{ method: "fabric_git_unlink", params: { repo: REPO, env: "dev" } }]);
});

test("Update from Git: a preview, a conflict choice and the workspace name, before the one write", { skip }, async () => {
  db.link = copy(LINK);
  updates.length = 0;
  await openTab();
  await page().getByRole("button", { name: "Check status in Fabric" }).click();
  await page().locator("tr.fg-conflict").waitFor();
  await page().getByRole("button", { name: "Update from Git…" }).click();
  const dialog = page().getByRole("dialog", { name: "Update Sales Dev from Git" });
  await dialog.waitFor();
  const text = await dialog.innerText();
  assert.match(text, /PL_Daily_Load[\s\S]*Replaced with the version in Git/);
  assert.match(text, /1 conflict: NB_Orders_Silver/);
  assert.match(text, /May be lost[\s\S]*LH_Silver[\s\S]*SM_Sales/);
  assert.doesNotMatch(text, /fabric-desktop.toml says/);
  const go = dialog.getByRole("button", { name: "Update workspace" });
  assert.strictEqual(await go.isDisabled(), true, "the update can start before the name is typed");
  assert.match(await go.getAttribute("class"), /danger-solid/);
  await dialog.getByLabel("Take the Git version (the workspace's change is lost)").check();
  assert.match(await dialog.innerText(), /1 loses the workspace's own change\. 2 uncommitted workspace changes may be lost/);
  await dialog.getByRole("textbox").fill("sales dev");
  assert.strictEqual(await go.isDisabled(), true, "a name in the wrong case starts it");
  await dialog.getByRole("textbox").fill("Sales Dev");
  assert.strictEqual(await go.isDisabled(), true, "it starts without consent to replace items");
  await dialog.getByLabel("Let Fabric replace the workspace items listed above with the Git versions").check();
  assert.deepStrictEqual(updates, []);
  assert.deepStrictEqual(await serious("#dialog-root .dialog"), []);
  await shot("5-update-from-git");
  await go.click();
  await dialog.waitFor({ state: "detached" });
  assert.deepStrictEqual(updates, [{ policy: "PreferRemote", confirm_name: "Sales Dev", allow_override: true }]);
});

test("Update from Git asks for Fabric's own workspace name and warns when the link file disagrees", { skip }, async () => {
  db.link = copy(LINK);
  db.fabricName = "Sales Prod";
  updates.length = 0;
  try {
    await openTab();
    await page().getByRole("button", { name: "Check status in Fabric" }).click();
    await page().locator("tr.fg-conflict").waitFor();
    await page().getByRole("button", { name: "Update from Git…" }).click();
    const dialog = page().getByRole("dialog", { name: "Update Sales Prod from Git" });
    await dialog.waitFor();
    assert.match(await dialog.getByRole("alert").innerText(), /Fabric calls this workspace Sales Prod, but fabric-desktop.toml says Sales Dev/);
    const go = dialog.getByRole("button", { name: "Update workspace" });
    await dialog.getByLabel("Let Fabric replace the workspace items listed above with the Git versions").check();
    await dialog.getByRole("textbox").fill("Sales Dev");
    assert.strictEqual(await go.isDisabled(), true, "the link file's name starts it");
    await dialog.getByRole("textbox").fill("Sales Prod");
    await go.click();
    await dialog.waitFor({ state: "detached" });
    assert.deepStrictEqual(updates, [{ policy: "PreferWorkspace", confirm_name: "Sales Prod", allow_override: true }]);
  } finally { db.fabricName = null; }
});

test("Update from Git stops when Fabric changed since the check, and shows the new list", { skip }, async () => {
  db.link = copy(LINK);
  updates.length = 0;
  await openTab();
  await page().getByRole("button", { name: "Check status in Fabric" }).click();
  await page().locator("tr.fg-conflict").waitFor();
  db.status = { ...copy(STATUS), fingerprint: "changed", items: STATUS.items.slice(1),
    counts: { ...STATUS.counts, conflict: 0 } };
  await page().getByRole("button", { name: "Update from Git…" }).click();
  await page().locator("tr.fg-conflict").waitFor({ state: "detached" });
  assert.strictEqual(await page().getByRole("dialog").count(), 0, "a dialog opened on an out-of-date preview");
  assert.deepStrictEqual(updates, []);
});
