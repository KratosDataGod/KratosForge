// Source control (git.js), the parts git-coverage and the other git-*.test.cjs leave out: the top bar's words for a
// branch, a detached commit or none, the plan and drift helpers' wording for every kind of value, the three Fabric
// workspace boxes in each state, Git not installed, a repository that can't be read and taking it off the list, the
// Repository settings (name and email, a remote of each provider, Add as origin), the command bar's Git commands, Start
// a new repository and New repository from their dialogs, and a notebook's branch chip.
//
// The engine is mocked in the shapes sidecar/fabric_desktop/git_service.py and git/repo.py return, as
// git-coverage.test.cjs does. Nothing reaches a Git remote or Fabric.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
const REPO = "C:/src/sales";
const OTHER = "C:/src/finance";
const SHA = (c) => c.repeat(40);
const copy = (x) => JSON.parse(JSON.stringify(x));
const KINDS = {
  notebooks: { label: "Notebooks", holds: "Notebooks and their Spark job definitions.", for: "For one team's notebooks." },
  project: { label: "Project", holds: "A metadata project.", for: "For one data product." },
};

let db;
const WS = "11111111-2222-3333-4444-555555555555";
const STATUS = { workspace_head: SHA("7"), remote_commit: SHA("9"), remote_tracking: SHA("8"), remote_fetched_matches: false, fingerprint: "fp1", aligned: false,
  counts: { conflict: 1, both_changed: 1, incoming: 0, outgoing: 0, same_change: 0 },
  items: [{ name: "NB_Orders", label: "Notebook", workspace_change: "modified", remote_change: "modified", state: "conflict", git_support: "yes", limits: [] },
    { name: "LH_Silver", label: "Lakehouse", workspace_change: "deleted", remote_change: "added", state: "both_changed", git_support: "preview",
      limits: ["Tables and files never go through Git."] },
    { name: "Odd", label: "Thing", workspace_change: "renamed", remote_change: "", state: "moved", git_support: "no", limits: ["never go"] }] };
const UPDATE = { env: "dev", confirm_text: "Sales Dev", linked_name: "Sales Dev", name_differs: false, fingerprint: "fp2",
  plan: { from: null, to: SHA("9"), changes: [], overwrites: [{ name: "PL_Load", label: "Data pipeline", state: "outgoing" }],
    kept: [{ name: "SM_Sales", label: "Semantic model", state: "unchanged", why: "Same in Git" }, { name: "NB_Orders", label: "Notebook", state: "conflict", why: "conflict" }] } };
const fresh = () => ({ signedIn: true, overviews: [], overviewFails: false, statusError: false, updates: [], checks: {},
  workspaces: [{ id: WS, displayName: "Sales Dev" }, { id: "ws-off", displayName: "Scratch" }, { id: "ws-err", displayName: "Broken" }, { id: "ws-other", displayName: "Finance Dev" },
    { id: "ws-bad", displayName: "Read-only" }],
  overview: { linked: false, problem: "fabric-desktop.toml can't be read: line 3 isn't TOML.", link: null },
  current: REPO, installed: true, statusFails: false, forgotten: null, branch: "main", ops: 0,
  settings: { root: REPO, identity: { name: "Chris", email: "chris@contoso.com", local_name: "Chris", local_email: null },
    remotes: [{ name: "upstream", url: "https://git.contoso.com/sales.git", provider: "git" }, { name: "mirror", url: "https://dev.azure.com/contoso/sales/_git/sales", provider: "azure_devops" }],
    credential_helpers: ["manager"], ssh_command: false, git_version: "2.47.1", notes: [] } });
function status(root = db.current) {
  return copy({ commit: SHA("a"), branch: db.branch, upstream: "origin/main", ahead: 0, behind: 0, root, name: root.split("/").pop(),
    staged: [], unstaged: [], untracked: [], conflicts: [], changed: 0, dirty: false, merging: false, stashes: 0,
    remotes: [{ name: "origin", url: "https://github.com/contoso/sales.git", provider: "github" }], fetched_at: null, links: null,
    summary: `${db.branch}, 0 uncommitted changes.` });
}

let app;
const page = () => app.page;
const calls = (method) => app.calls.filter((c) => c.method === method).map((c) => c.params);
const toastText = () => page().evaluate(() => document.getElementById("toast").textContent);
const frames = (n = 3) => page().evaluate((n) => new Promise((r) => { let i = 0; const f = () => (++i >= n ? r() : requestAnimationFrame(f)); requestAnimationFrame(f); }), n);
async function until(check, what) {
  for (let i = 0; i < 300; i++) { if (await check()) return; await frames(1); }
  assert.fail(`timed out waiting for ${what}`);
}
const toastSays = (re) => until(async () => re.test(await toastText()), String(re));

before(async () => {
  if (skip) return;
  app = await openApp({
    mocks: {
      readiness_check: () => [],
      app_settings: () => ({ show_preview: { value: true, source: "default" } }),
      git_info: () => ({ installed: db.installed, version: db.installed ? "2.47.1" : null, current: db.forgotten === db.current ? OTHER : db.current, kinds: KINDS,
        repos: [REPO, OTHER].filter((p) => p !== db.forgotten).map((p) => ({ path: p, name: p.split("/").pop(), exists: true, kind: "notebooks", suggested: null })),
        notebooks_folder: "C:/Users/chris/KratosForge/notebooks", clone_folder: "C:\\Users\\chris\\source\\repos" }),
      git_status: ({ repo }) => { if (db.statusFails && repo === REPO) throw new Error(`GitError: ${repo} isn't a Git repository.`); return status(repo); },
      git_open: ({ path }) => { db.current = path; return status(path); },
      git_forget: ({ repo }) => { db.forgotten = repo; return { forgotten: true }; },
      git_settings: () => copy(db.settings),
      git_set_identity: ({ name, email }) => { Object.assign(db.settings.identity, { name, email, local_name: name, local_email: email }); return { ok: true }; },
      git_add_remote: ({ url }) => { db.settings.remotes.push({ name: "origin", url, provider: "github" }); return status(); },
      git_init: ({ path, branch }) => { if (path.includes("bad")) throw new Error(`GitError: ${path} can't be made a repository.`); db.branch = branch; db.current = path; return status(path); },
      git_create: ({ path }) => { if (path.endsWith("taken")) throw new Error(`GitError: ${path} already exists.`); db.current = path; return status(path); },
      git_fetch: () => ({ id: `op-${++db.ops}`, kind: "fetch", repo: db.current, status: "running", percent: null, phase: "Starting" }),
      git_pull: () => ({ id: `op-${++db.ops}`, kind: "pull", repo: db.current, status: "running", percent: null, phase: "Starting" }),
      git_push: () => { throw new Error("GitError: There's no remote to push to."); },
      git_op_status: ({ id }) => ({ id, kind: "fetch", repo: db.current, status: "ok", percent: null, message: "Fetched from origin." }),
      git_branches: () => ({ local: [{ name: db.branch, current: true, upstream: "origin/main", ahead: 0, behind: 0, subject: "x", date: "2026-11-19T10:00:00+00:00" }], remote: [], stashes: [] }),
      git_notebook_info: ({ name }) => ({
        "Wrong place": { repo: OTHER, name: "finance", branch: null, state: null, path: "x.ipynb", wrong_place: "finance holds Project definitions, not notebooks." },
        "In conflict": { repo: OTHER, name: "finance", branch: "dev", state: "conflict", path: "y.ipynb" },
      }[name] || null),
      git_diff: ({ path }) => ({ path, view: "text", binary: false, hunks: [], added: 0, removed: 0 }),
      environment: () => ({ python: "Python 3 (test)", user: "chris@contoso.com" }),
      list_workspaces: () => copy(db.workspaces),
      fabric_git_overview: ({ env }) => { db.overviews.push(env); if (db.overviewFails) { db.overviewFails = false; throw new Error("FabricError: Fabric didn't answer in time."); } return { env, ...copy(db.overview) }; },
      fabric_git_status: () => { if (db.statusError) { db.statusError = false; throw new Error("The workspace's Git connection needs to be signed in again."); } return copy(STATUS); },
      fabric_git_update: (p) => { const next = db.updates.shift(); if (next instanceof Error) throw next; return { ...UPDATE, ...next(p) }; },
      fabric_git_check: ({ workspace_id }) => { const c = db.checks[workspace_id]; if (c instanceof Error) throw c; return copy(c); },
      fabric_git_link: ({ workspace_id, confirm }) => {
        if (workspace_id === "ws-bad") throw new Error("ValueError: fabric-desktop.toml can't be written.");
        return workspace_id === "ws-other" && !confirm ? { needs_confirm: true, message: "The workspace follows contoso/finance. Link it anyway?" } : { envs: {}, status: status() };
      },
    },
  });
});
after(async () => { await app?.close(); });

beforeEach(async () => {
  if (skip) return;
  db = fresh();
  await page().evaluate(() => {
    closeDialog(); closeMenu?.();
    clearTimeout(gitPage.opTimer);
    Object.assign(gitPage, { info: null, repo: null, status: null, tab: "changes", selected: null, diff: null, message: "", branches: null,
      log: null, detail: null, compare: [], compared: null, settings: null, op: null });
    state.settings = { ...(state.settings || {}), show_preview: { value: true } };
    document.getElementById("toast").textContent = "";
    go("home");
  });
  app.calls.length = 0;
  app.errors.length = 0;
});

test("the top bar's words and the commit button say the branch, a detached commit or no commits, and how far apart", { skip }, async () => {
  const out = await page().evaluate(() => {
    const st = (o) => ({ staged: [], unstaged: [], untracked: [], conflicts: [], merging: false, ...o });
    return {
      pills: [gitPillText(null), gitPillText({ branch: null, commit: "1111111222" }), gitPillText({ branch: null, commit: null }),
        gitPillText({ branch: "main", ahead: 2, behind: 1, changed: 1 }), gitPillText({ branch: "dev", changed: 3 })],
      labels: [gitCommitLabel(null), gitCommitLabel(st({ branch: "main", conflicts: [{}] })), gitCommitLabel(st({ branch: "main", merging: true })),
        gitCommitLabel(st({ branch: null, staged: [{}, {}] })), gitCommitLabel(st({ branch: "main", untracked: [{}] })), gitCommitLabel(st({}))],
      side: gitSideBySide(null).length,
      groups: gitGroupRepos([{ path: "a", kind: "project" }, { path: "b", kind: null }], null).map((g) => g.label),
    };
  });
  assert.deepStrictEqual(out.pills, ["", "1111111", "No commits", "main ↑2 ↓1 · 1 unsaved change", "dev · 3 unsaved changes"]);
  assert.deepStrictEqual(out.labels, ["Commit", "Fix the conflicts to commit", "Commit the merge to main", "Commit 2 staged files",
    "Stage all and commit 1 file to main", "Nothing to commit"]);
  assert.strictEqual(out.side, 0);
  assert.deepStrictEqual(out.groups, ["Not yet sorted"]);
  assert.deepStrictEqual(app.errors, []);
});

test("the platform and capacity helpers say every kind of value, change and workspace in words", { skip }, async () => {
  const out = await page().evaluate(() => ({
    values: [platformValue([]), platformValue(["a", "b"]), platformValue({ x: 1 }), platformValue(""), platformValue(null), platformValue(undefined),
      platformValue("text"), platformValue(3), platformValue(false)],
    admins: [platformChangeLines({ kind: "admins", changes: [{ git: ["ana"], deployed: ["bo", "cy"] }] }),
      platformChangeLines({ kind: "admins", changes: [] })],
    fields: platformChangeLines({ kind: "domains", changes: [{ git: "x", deployed: null }, { field: "owner", git: ["a"], deployed: [] }] }),
    steps: [platformStepText({ kind: "domains", id: "sales", action: "rebuild" }), platformStepText({ kind: "domains", id: "s", action: "update", what: "rename it" }, { domains: "Domain" })],
    capacity: capacityLines({ state: "not_in_git", fields: [{ field: "region", git: "westeurope", fabric: "northeurope" }],
      workspaces: { same: 1, elsewhere: [{ id: "w1" }], not_in_git: [{ name: "Finance" }], not_visible: [{ id: "w7" }] } }),
    capacity2: capacityLines({ state: "differs", fields: [], workspaces: { same: 2, elsewhere: [], not_in_git: [{ id: "w2" }], not_visible: [] } }),
  }));
  assert.deepStrictEqual(out.values, ["none", "a, b", '{"x":1}', "empty", "empty", "empty", '"text"', "3", "false"]);
  assert.deepStrictEqual(out.admins, [["Git adds ana", "Git removes bo, cy"], []]);
  assert.deepStrictEqual(out.fields, ['value: Git "x", deployed empty', "owner: Git a, deployed none"]);
  assert.deepStrictEqual(out.steps, ["domains sales: rebuild", "Domain s: rename it"]);
  assert.deepStrictEqual(out.capacity, ["Region: Git westeurope, Fabric northeurope", "w1: Git expects it here; Fabric has it on a capacity you can't see",
    "Finance: on this capacity", "w7: you can't see this workspace, so it isn't compared", "1 workspace is where Git expects"]);
  assert.deepStrictEqual(out.capacity2, ["w2: on this capacity in Fabric, not listed in capacity.toml", "2 workspaces are where Git expects"]);
  assert.deepStrictEqual(app.errors, []);
});

test("the Fabric workspace boxes say how far this PC, the remote and the workspace are apart, in each state", { skip }, async () => {
  const out = await page().evaluate(() => {
    const lines = (a) => fabricGitBoxes(a, (t) => `at ${t}`).map((b) => `${b.label} | ${b.title} | ${b.commit} | ${b.line}`);
    return {
      none: fabricGitBoxes(null),
      a: lines({ local: {}, remote: { name: null, branch: "main", commit: null }, workspace: { commit: null, folder: null, last_sync: null } }),
      b: lines({ local: { branch: "dev", commit: "1234567890", ahead_of_remote: 1, behind_remote: 2, uncommitted: 3 },
        remote: { name: "origin", branch: "dev", commit: "2222222333", ahead_of_workspace: null }, workspace: { commit: "3333333444", folder: "fabric", last_sync: "noon" } }),
      c: lines({ local: { behind_remote: 1, uncommitted: 1 }, remote: { name: "origin", branch: "main", commit: "a1", ahead_of_workspace: 2, behind_workspace: 1 } }),
      d: lines({ local: { ahead_of_remote: 2 }, remote: { name: "origin", branch: "main", commit: "a1", ahead_of_workspace: 1, behind_workspace: 0 } }),
      e: lines({ remote: { name: "origin", branch: "main", commit: "a1", ahead_of_workspace: 0, behind_workspace: 3 } }),
      f: lines({ remote: { name: "origin", branch: "main", commit: "a1", ahead_of_workspace: 0, behind_workspace: 0 } }),
      sums: [fabricGitSummary(null), fabricGitSummary({ aligned: true }), fabricGitSummary({ counts: { conflict: 1, both_changed: 2, incoming: 3, outgoing: 4, same_change: 5 } }),
        fabricGitSummary({ counts: { conflict: 2 } }), fabricGitSummary({})],
      support: [fgSupportText({ git_support: "yes", limits: ["Its data and settings never go through Git."] }), fgSupportText({ git_support: "no", limits: ["never go"] }),
        fgSupportText({ git_support: "odd" })],
    };
  });
  assert.deepStrictEqual(out.none, []);
  assert.deepStrictEqual(out.a, ["This PC (local) | No branch |  | Up to date with the remote", "Git remote | ?/main |  | Not fetched on this PC yet",
    "Fabric workspace (cloud) | Not synced yet |  | Repository root"]);
  assert.deepStrictEqual(out.b, ["This PC (local) | dev | 1234567 | 1 commit not pushed · 2 commits to pull · 3 files not committed",
    "Git remote | origin/dev | 2222222 | Can't compare with the workspace yet", "Fabric workspace (cloud) | Synced to 3333333 |  | Folder fabric · last sync at noon"]);
  assert.deepStrictEqual(out.c, ["This PC (local) | No branch |  | 1 commit to pull · 1 file not committed",
    "Git remote | origin/main | a1 | 2 commits the workspace doesn't have · behind the workspace's commit by 1"]);
  assert.deepStrictEqual(out.d.slice(1), ["Git remote | origin/main | a1 | 1 commit the workspace doesn't have"]);
  assert.match(out.d[0], /2 commits not pushed/);
  assert.deepStrictEqual(out.e.slice(1), ["Git remote | origin/main | a1 | behind the workspace's commit by 3"]);
  assert.deepStrictEqual(out.f.slice(1), ["Git remote | origin/main | a1 | Same commit as the workspace"]);
  assert.deepStrictEqual(out.sums, ["", "The workspace and Git match: nothing to update or commit.",
    "1 conflict · 2 changed on both sides · 3 newer in Git · 4 newer in the workspace · 5 same on both sides", "2 conflicts", ""]);
  assert.deepStrictEqual(out.support, ["Full, definition only", "Not supported by Fabric Git", ""]);
  assert.deepStrictEqual(app.errors, []);
});

test("Git not installed says where to get it, and Check again reads Git again", { skip }, async () => {
  db.installed = false;
  await page().evaluate(() => go("source"));
  await page().getByText("Git isn't installed on this PC").waitFor();
  await page().getByRole("button", { name: "Get Git for Windows" }).click();
  await until(async () => (await page().evaluate(() => window.__fdShellCalls.filter((c) => c.cmd === "open_link").map((c) => c.args.url))).includes("https://git-scm.com/download/win"), "the link");
  db.installed = true;
  await page().getByRole("button", { name: "Check again" }).click();
  await page().locator(".git-page").waitFor();
  assert.strictEqual(calls("git_info").length, 2);
  assert.deepStrictEqual(app.errors, []);
});

test("a repository that can't be read says so, and Take it off the list opens the next one", { skip }, async () => {
  db.statusFails = true;
  await page().evaluate(() => go("source"));
  await page().getByText("This repository can't be read").waitFor();
  assert.match(await page().locator("#view").innerText(), /C:\/src\/sales may have moved or been deleted\./);
  await page().getByRole("button", { name: "Take it off the list" }).click();
  await page().locator(".git-page").waitFor();
  assert.deepStrictEqual(calls("git_forget"), [{ repo: REPO }]);
  assert.strictEqual(await page().evaluate(() => gitPage.status.root), OTHER);
  assert.deepStrictEqual(app.errors, []);
});

test("Repository settings save the name and email, name each provider, and add an origin", { skip }, async () => {
  await page().evaluate(() => { gitPage.tab = "settings"; go("source"); });
  const settings = page().locator(".git-settings");
  await settings.waitFor();
  assert.match(await settings.innerText(), /Set for this repository only\./);
  assert.deepStrictEqual(await settings.locator("table.mini tr").allInnerTexts(),
    ["upstream\thttps://git.contoso.com/sales.git\tGit", "mirror\thttps://dev.azure.com/contoso/sales/_git/sales\tAzure DevOps"]);
  assert.match(await settings.innerText(), /Git 2\.47\.1 · credential helper: manager\n/);
  await settings.getByLabel("Name", { exact: true }).fill("Chris Lee");
  await settings.getByLabel("Email").fill("chris.lee@contoso.com");
  await settings.getByRole("button", { name: "Save for this repository" }).click();
  await toastSays(/^Saved\. New commits use this name and email\.$/);
  assert.deepStrictEqual(calls("git_set_identity"), [{ repo: REPO, name: "Chris Lee", email: "chris.lee@contoso.com" }]);
  await settings.getByLabel("Remote address").fill("https://github.com/contoso/sales.git");
  await settings.getByRole("button", { name: "Add as origin" }).click();
  await until(async () => (await page().locator(".git-settings table.mini tr").count()) === 3, "the new origin");
  assert.strictEqual(await page().locator(".git-settings").getByRole("button", { name: "Add as origin" }).count(), 0);
  await page().locator(".git-settings").getByRole("button", { name: "Take it off the list" }).click();
  await page().getByRole("dialog").getByRole("button", { name: "Cancel" }).click();
  assert.deepStrictEqual(calls("git_forget"), []);
  assert.deepStrictEqual(app.errors, []);
});

test("the command bar's Git commands open the right tab, fetch and pull, and say when a push can't start", { skip }, async () => {
  await page().evaluate(() => go("source"));
  await page().locator(".git-page").waitFor();
  const run = (label) => page().evaluate((label) => features.commands.find((c) => c.label === label).run(), label);
  const when = await page().evaluate(() => features.commands.filter((c) => c.feature === "sourceControl" && c.when).map((c) => [c.label, !!c.when()]));
  assert.deepStrictEqual(when, [["Commit staged changes", true], ["Fetch", true], ["Pull", true], ["Push", true], ["Create a branch", true], ["Switch branch", true]]);
  await run("Create a branch");
  await until(async () => (await page().evaluate(() => gitPage.tab)) === "branches", "Branches");
  await run("Switch branch");
  await run("Commit staged changes");
  await until(async () => (await page().evaluate(() => document.activeElement?.id)) === "git-message", "the message box");
  await run("Fetch");
  await toastSays(/^Fetched from origin\.$/);
  await run("Pull");
  await until(async () => calls("git_pull").length === 1, "the pull");
  assert.deepStrictEqual(calls("git_pull"), [{ repo: REPO, mode: "ff-only" }]);
  await run("Push");
  await toastSays(/^Couldn't start: /);
  await run("Open Source control");
  await run("Open a repository");
  await page().getByRole("dialog", { name: "Open a repository" }).waitFor();
  await page().evaluate(() => closeDialog());
  assert.deepStrictEqual(app.errors, []);
});

test("Start a new repository shows Git's refusal in the dialog, then starts it on the branch typed", { skip }, async () => {
  await page().evaluate(() => features.commands.find((c) => c.label === "Make a folder a repository").run());
  const d = page().getByRole("dialog", { name: "Start a new repository" });
  await d.getByRole("textbox", { name: "Folder", exact: true }).fill("C:\\src\\bad");
  await d.getByRole("button", { name: "Start repository" }).click();
  await d.getByText(/C:\\src\\bad can't be made a repository\./).waitFor();
  await d.getByRole("textbox", { name: "Folder", exact: true }).fill("C:/src/new-project");
  await d.getByLabel("First branch").fill("");
  await d.getByLabel("First branch").press("Enter");
  await toastSays(/^Repository started\. Stage and commit your files to begin its history\.$/);
  assert.deepStrictEqual(calls("git_init").at(-1), { path: "C:/src/new-project", branch: "main", kind: null });
  assert.strictEqual(await page().evaluate(() => state.view), "source");
  assert.deepStrictEqual(app.errors, []);
});

test("New repository needs a name, shows a refusal, and makes the repository of the kind picked", { skip }, async () => {
  await page().evaluate(async () => { await gitLoadInfo(); features.commands.find((c) => c.label === "New repository").run(); });
  const d = page().getByRole("dialog", { name: "New repository" });
  await d.waitFor();
  assert.match(await d.innerText(), /Notebooks and their Spark job definitions\. For one team's notebooks\./);
  await d.getByRole("button", { name: "Create repository" }).click();
  await d.getByText("Give the repository a name.").waitFor();
  await d.getByLabel("Project").check();
  assert.match(await d.innerText(), /A metadata project\. For one data product\./);
  await d.getByLabel("Repository name").fill("taken");
  await d.getByRole("button", { name: "Create repository" }).click();
  await d.getByText(/C:\\Users\\chris\\source\\repos\\taken already exists\./).waitFor();
  await d.getByLabel("Repository name").fill("sales-project");
  await d.getByRole("button", { name: "Create repository" }).click();
  await toastSays(/^Repository created with its starting files\./);
  assert.deepStrictEqual(calls("git_create").at(-1), { path: "C:\\Users\\chris\\source\\repos\\sales-project", kind: "project" });
  assert.deepStrictEqual(app.errors, []);
});

test("the top bar's pill names a repository with conflicts and no branch; a notebook's chip opens its repository", { skip }, async () => {
  await page().evaluate((sha) => {
    gitPage.status = { branch: null, commit: sha, name: "sales", changed: 1, conflicts: [{}], summary: "detached" };
    renderGitPill();
  }, SHA("d"));
  const pill = page().locator("#git-slot .git-pill");
  assert.strictEqual(await pill.getAttribute("aria-label"), "Branch none in sales · 1 unsaved change");
  assert.match(await pill.getAttribute("title"), /on the version "no branch"\. 1 file changed since your last commit\./);
  assert.match(await pill.getAttribute("class"), /bad/);

  await page().evaluate(async () => {
    await gitLoadInfo();
    gitPage.repo = "C:/src/sales";
    for (const name of ["Wrong place", "In conflict"]) document.body.append(h("div", { class: "cov-chip", "data-name": name }, gitNotebookChip({ name })));
  });
  const wrong = page().locator(".cov-chip[data-name='Wrong place'] button");
  await wrong.waitFor();
  assert.strictEqual(await wrong.innerText(), "no branch · wrong repository");
  await wrong.click();
  await until(async () => (await page().evaluate(() => [gitPage.repo, state.view].join(" "))) === `${OTHER} source`, "the other repository");
  await page().evaluate(() => { gitPage.repo = "C:/src/sales"; });
  const conflict = page().locator(".cov-chip[data-name='In conflict'] button");
  assert.strictEqual(await conflict.innerText(), "dev · conflict");
  await conflict.click();
  await until(async () => (await page().evaluate(() => gitPage.selected?.path)) === "y.ipynb", "the conflicted file");
  assert.strictEqual(await page().evaluate(() => gitPage.selected.group), "conflicts");
  await page().evaluate(() => document.querySelectorAll(".cov-chip").forEach((e) => e.remove()));
  assert.deepStrictEqual(app.errors, []);
});

async function fabricTab() {
  await page().evaluate(() => { gitPage.fabric.key = null; gitPage.fabric.env = "dev"; gitPage.tab = "fabric"; go("source"); });
  await page().locator(".git-platform").waitFor();
}
const LINKED = (o = {}) => ({ linked: true, problem: "The link names a workspace you can't open.", changed_in_fabric: ["branch", "folder"],
  link: { workspace_id: WS, workspace_name: "", owner: "contoso", project: "Data", repository: null, branch: null, folder: null },
  connection: { connected: true }, alignment: { local: { branch: "main" }, notes: [] }, ...o });

test("the Fabric workspace tab says why the link can't be read, retries a failed read, and reads each environment", { skip }, async () => {
  db.overviewFails = true;
  await fabricTab();
  await page().getByText("Couldn't read the workspace's Git connection").waitFor();
  await page().getByRole("button", { name: /Retry|Try again/ }).first().click();
  await page().getByText("No Dev workspace linked").waitFor();
  assert.match(await page().locator(".git-platform").innerText(), /fabric-desktop\.toml can't be read: line 3 isn't TOML\./);
  await page().getByLabel("Environment").selectOption("test");
  await page().getByText("No Test workspace linked").waitFor();
  assert.deepStrictEqual(db.overviews, ["dev", "dev", "test"]);
  assert.deepStrictEqual(app.errors, []);
});

test("a linked workspace names what Fabric changed; Check status says when it fails and when this PC fetched another commit", { skip }, async () => {
  db.overview = LINKED();
  db.statusError = true;
  await fabricTab();
  const tab = page().locator(".git-platform");
  await tab.getByText(`Linked workspace: ${WS}`).waitFor();
  const text = await tab.innerText();
  assert.match(text, /Fabric didn't say which repository it follows\./);
  assert.match(text, /The link names a workspace you can't open\./);
  assert.match(text, /Fabric now shows a different branch and folder than this link\. Link the workspace again to update fabric-desktop\.toml\./);
  await tab.getByRole("button", { name: "Check status in Fabric" }).click();
  await tab.getByText("The workspace's Git connection needs to be signed in again.").waitFor();
  await tab.getByRole("button", { name: "Check status in Fabric" }).click();
  await tab.locator("table.list").waitFor();
  assert.match(await tab.innerText(), new RegExp(`Fabric compared Git commit 9999999; this PC last fetched 8888888\\. Fetch to see the same commits here\\.`));
  assert.match(await tab.innerText(), /Items \(3 differ\)\s*1 conflict · 1 changed on both sides/);
  const rows = await tab.locator("tbody tr").allInnerTexts();
  assert.match(rows[1], /LH_Silver\s*Lakehouse\s*Deleted\s*Added\s*Both changed\s*Preview in Fabric, definition only\s*Tables and files never go through Git\./);
  assert.match(rows[2], /Odd\s*Thing\s*No change\s*moved\s*Not supported by Fabric Git/);
  await tab.getByRole("button", { name: "Commit in Fabric" }).click();
  await until(async () => (await page().evaluate(() => window.__fdShellCalls.filter((c) => c.cmd === "open_link").map((c) => c.args.url)))
    .includes(`https://app.fabric.microsoft.com/groups/${WS}/list?experience=fabric-developer`), "Fabric's link");
  assert.deepStrictEqual(app.errors, []);
});

test("Update from Git says when Fabric changed or there's nothing to bring in, and says what each refusal needs", { skip }, async () => {
  db.overview = LINKED({ link: { workspace_id: WS, workspace_name: "Sales Dev", owner: "contoso", repository: "sales", branch: "main", folder: "/fabric" } });
  await fabricTab();
  const tab = page().locator(".git-platform");
  await tab.getByRole("button", { name: "Check status in Fabric" }).click();
  await tab.locator("table.list").waitFor();
  const open = () => tab.getByRole("button", { name: "Update from Git…" }).click();
  db.updates.push(() => ({ stale: true, status: copy(STATUS) }));
  await open();
  await toastSays(/Fabric changed since you checked\. Here is the new list: check it, then update\./);
  db.updates.push(() => ({ nothing: true }));
  await open();
  await toastSays(/^Nothing to bring in from Git\.$/);
  db.updates.push(() => ({ name_differs: true, confirm_text: "Sales Dev (new)", linked_name: "Sales Dev" }));
  await open();
  const d = page().getByRole("dialog", { name: "Update Sales Dev (new) from Git" });
  await d.waitFor();
  const text = await d.innerText();
  assert.match(text, /Fabric calls this workspace Sales Dev \(new\), but fabric-desktop\.toml says Sales Dev\./);
  assert.match(text, /brings the workspace from its first sync to 9999999 on main/);
  assert.match(text, /Only the conflicts below\./);
  assert.match(text, /2 conflicts: NB_Orders, LH_Silver/);
  assert.match(text, /May be lost[\s\S]*PL_Load/);
  assert.match(text, /Not touched[\s\S]*SM_Sales/);
  assert.match(text, /This changes 0 items in the workspace\. 1 uncommitted workspace change may be lost\. Type Sales Dev \(new\) to confirm\./);
  await d.getByLabel(/Take the Git version/).check();
  assert.match(await d.innerText(), /This changes 2 items in the workspace, and 2 lose the workspace's own change\./);
  await d.locator("#fg-override").check();
  await d.locator("#fg-confirm").fill("Sales Dev (new)");
  const go = d.getByRole("button", { name: "Update workspace" });
  const answers = [[() => ({ needs_choice: true }), "Choose what happens to the conflicts."],
    [() => ({ needs_override: true }), "Tick the box to let Fabric replace these workspace items."],
    [() => ({ needs_confirm: true, confirm_text: "Sales Dev (new)" }), "Type Sales Dev (new) exactly to confirm."],
    [() => ({}), "Nothing to bring in from Git."],
    [new Error("The update was refused."), "The update was refused."]];
  for (const [answer, says] of answers) {
    db.updates.push(answer);
    assert.ok(await go.isEnabled(), `before: ${says}`);
    await go.evaluate((b) => b.click());  // Playwright never finds this button stable in the tall dialog, so the click is sent to it directly
    await d.locator(".field-error").getByText(says).waitFor();
    assert.ok(await go.isEnabled(), says);
  }
  assert.deepStrictEqual(calls("fabric_git_update").at(-1), { repo: REPO, env: "dev", fingerprint: "fp2", policy: "PreferRemote", confirm_name: "Sales Dev (new)", allow_override: true });
  db.updates.push(() => ({ stale: true, status: copy(STATUS) }));
  await go.evaluate((b) => b.click());
  await toastSays(/Fabric changed since the preview, so nothing was updated\./);
  assert.strictEqual(await page().getByRole("dialog").count(), 0);
  assert.deepStrictEqual(app.errors, []);
});

test("Link a workspace asks Fabric what each follows, says when it can't, links after asking, and shows a refusal", { skip }, async () => {
  db.workspaces = [];
  await fabricTab();
  await page().evaluate(() => { state.workspaces = null; });
  await page().getByRole("button", { name: "Link a workspace…" }).click();
  await toastSays(/^Sign in to pick a workspace\.$/);
  db.workspaces = fresh().workspaces;
  await page().evaluate(() => { state.workspaces = null; });
  db.checks = { "ws-off": { connection: { connected: false }, remote: null },
    "ws-err": new Error("You can't see that workspace's Git connection."),
    "ws-other": { connection: { connected: true, provider_label: "Azure DevOps", owner: "contoso", project: "Data", repository: "finance", branch: "main", folder: "", synced_head: SHA("5") }, remote: null },
    "ws-bad": { connection: { connected: true, provider_label: "GitHub", owner: "contoso", project: "", repository: "sales", branch: "main", folder: "/f", synced_head: null }, remote: "origin" } };
  await page().getByRole("button", { name: "Link a workspace…" }).click();
  const d = page().getByRole("dialog", { name: "Link sales to a Dev workspace" });
  await d.waitFor();
  const ws = d.getByLabel("Workspace");
  await ws.selectOption("ws-off");
  await d.getByText(/This workspace isn't connected to Git in Fabric\./).waitFor();
  await ws.selectOption("ws-err");
  await d.getByText("You can't see that workspace's Git connection.").waitFor();
  await ws.selectOption("");
  assert.strictEqual((await d.locator(".fg-said").innerText()).trim(), "");
  await ws.selectOption("ws-bad");
  await d.getByText("Matches this repository's remote origin").waitFor();
  assert.match(await d.locator(".fg-said").innerText(), /Connected to GitHub: contoso\/sales, branch main, folder \/f/);
  await d.getByRole("button", { name: "Link", exact: true }).click();
  await d.getByText("fabric-desktop.toml can't be written.").waitFor();
  await ws.selectOption("ws-other");
  await d.getByText("None of this repository's remotes points there").waitFor();
  assert.match(await d.locator(".fg-said").innerText(), /contoso\/Data\/finance, branch main[\s\S]*Last synced to commit 5555555/);
  await d.getByRole("button", { name: "Link", exact: true }).click();
  const anyway = page().getByRole("dialog", { name: "Link it anyway?" });
  await anyway.getByRole("button", { name: "Link anyway" }).click();
  await until(async () => calls("fabric_git_link").length === 3, "the confirmed link");
  assert.deepStrictEqual(calls("fabric_git_link").at(-1), { repo: REPO, env: "dev", workspace_id: "ws-other", workspace_name: "Finance Dev", confirm: true });
  assert.deepStrictEqual(app.errors, []);
});
