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

let app;
const skip = !playwright && "Playwright is not installed";
const page = () => app.page;
const REPO = "C:/src/sales";
const WS = "11111111-2222-3333-4444-555555555555";
const OTHER_WS = "99999999-2222-3333-4444-555555555555";
const SHA = (c) => c.repeat(40);
const copy = (x) => JSON.parse(JSON.stringify(x));
const WRITES = ["fabric_git_link", "fabric_git_unlink"];

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
const row = (name, label, ws, remote, conflict, state, support = "yes") => ({ item_type: label, label, name, logical_id: "", object_id: "",
  workspace_change: ws, remote_change: remote, conflict, state, git_support: support, round_trip: support });
const STATUS = { workspace_head: SHA("7"), remote_commit: SHA("9"), fingerprint: "abc", aligned: false,
  remote_tracking: SHA("9"), remote_fetched_matches: true,
  counts: { conflict: 1, both_changed: 0, incoming: 1, outgoing: 2, same_change: 0, unchanged: 0 },
  items: [row("NB_Orders_Silver", "Notebook", "modified", "modified", "conflict", "conflict"),
    row("PL_Daily_Load", "Data pipeline", "", "modified", "", "incoming"),
    row("LH_Silver", "Lakehouse", "modified", "", "", "outgoing"),
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
  fabric_git_overview: guard(({ env }) => (db.link && env === "dev"
    ? { env, linked: true, link: db.link, problem: null, connection: CONN, changed_in_fabric: [], alignment: ALIGN }
    : { env, linked: false, problem: null, link: null })),
  fabric_git_check: guard(({ workspace_id }) => ({ connection: workspace_id === WS ? CONN : AZDO, remote: workspace_id === WS ? "origin" : null })),
  fabric_git_link: guard(({ workspace_id, confirm }) => {
    if (workspace_id !== WS && !confirm) {
      return { needs_confirm: true, message: "The workspace follows contoso/finance, which none of this repository's remotes points to. Link it anyway?", connection: AZDO };
    }
    db.link = { ...LINK, workspace_id };
    return { envs: { dev: db.link, test: null, prod: null }, problem: null, exists: true, status: status() };
  }),
  fabric_git_unlink: guard(() => { db.link = null; return { envs: { dev: null, test: null, prod: null }, problem: null, exists: true, status: status() }; }),
  fabric_git_status: guard(() => copy(STATUS)),
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
  assert.deepStrictEqual(writes(from), [], "checking status sent a write");
  assert.deepStrictEqual(await serious(".git-page"), []);
  await shot("4-items");
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
