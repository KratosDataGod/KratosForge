// Source control (git.js), the parts the other git-*.test.cjs leave out: Fetch, Pull and merge and Push
// from the header, the provider's links, a diff side by side, a notebook's code-only diff, a binary file
// and a diff that can't be read, a merge that stopped on conflicts, commit (with Stage all first, the
// secrets check and Ctrl+Enter), switching repository, marking what a repository holds, New repository,
// comparing two commits in History, a notebook's data chip and its menu, where a new notebook goes,
// Copy report on Capacity, and a platform deploy that changes when a removal is ticked.
//
// The engine is mocked in the shapes sidecar/fabric_desktop/git_service.py and git/repo.py return
// (status, diff hunks, log, compare, ops), as git-branch-actions.test.cjs does (see harness.cjs).
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
const REPO = "C:/src/sales";
const OTHER = "C:/src/finance";
const CAP = "C:/src/capacity";
const SHA = (c) => c.repeat(40);
const copy = (x) => JSON.parse(JSON.stringify(x));

const HUNKS = [
  { id: "h1", header: "@@ -1,4 +1,4 @@", lines: [
    { kind: " ", old: 1, new: 1, text: "import pandas as pd" }, { kind: "-", old: 2, new: null, text: "rows = 10" },
    { kind: "+", old: null, new: 2, text: "rows = 100" }, { kind: " ", old: 3, new: 3, text: "" }] },
  { id: "h2", header: "@@ -20,3 +20,4 @@ def load():", lines: [
    { kind: " ", old: 20, new: 20, text: "    df = read()" }, { kind: "+", old: null, new: 21, text: "    df = df.sample(5)" },
    { kind: "+", old: null, new: 22, text: "    print(df)" }, { kind: " ", old: 21, new: 23, text: "    return df" }] },
];
const KINDS = {
  notebooks: { label: "Notebooks", holds: "Notebooks and their Spark job definitions.", for: "For one team's notebooks." },
  project: { label: "Project", holds: "A metadata project.", for: "For one data product." },
  capacity: { label: "Capacity", holds: "capacity.toml.", for: "For the capacity admins." },
};

let db;
const fresh = () => ({
  current: REPO, branch: "main", merging: false, links: null, kinds: { [REPO]: "notebooks", [OTHER]: "notebooks", [CAP]: "capacity" }, suggested: null,
  staged: [{ path: "notes.md", state: "modified" }],
  unstaged: [{ path: "load.py", state: "modified" }, { path: "daily.ipynb", state: "modified" }, { path: "logo.png", state: "modified" }, { path: "broken.py", state: "modified" }],
  untracked: [], conflicts: [], commitAnswers: [],
});
function status(root = db.current) {
  const changed = db.staged.length + db.unstaged.length + db.untracked.length + db.conflicts.length;
  return copy({ commit: SHA("a"), branch: db.branch, upstream: "origin/main", ahead: 0, behind: 0, root, name: root.split("/").pop(),
    staged: db.staged, unstaged: db.unstaged, untracked: db.untracked, conflicts: db.conflicts, changed, dirty: changed > 0,
    merging: db.merging, stashes: 0, remotes: [{ name: "origin", url: "https://github.com/contoso/sales.git", provider: "github" }],
    fetched_at: "2026-11-19T08:00:00+00:00", links: db.links, summary: `${db.branch}, ${changed} uncommitted changes.` });
}
const op = (kind, id) => ({ id, kind, repo: db.current, status: "running", percent: null, phase: "Starting" });
const COMMITS = [
  { commit: SHA("c"), short: "ccccccc", subject: "Newest change", author: "Chris", date: "2026-11-19T10:00:00+00:00", refs: ["HEAD -> main"] },
  { commit: SHA("b"), short: "bbbbbbb", subject: "Older change", author: "Ana", date: "2026-11-18T10:00:00+00:00", refs: [] },
];

// git_service.py git_notebook_info: the repository a notebook's file is in, its branch and the file's state.
const NB_INFO = {
  "Orders load": { repo: OTHER, name: "finance", branch: "main", state: "modified", path: "load.py" },
  "Misplaced": { repo: OTHER, name: "finance", branch: "dev", state: null, path: "nb.ipynb", wrong_place: "finance holds Project definitions, not notebooks." },
};

let app;
const page = () => app.page;
const calls = (method) => app.calls.filter((c) => c.method === method).map((c) => c.params);
const shell = () => page().evaluate(() => window.__fdShellCalls.filter((c) => c.cmd === "open_link").map((c) => c.args.url));

before(async () => {
  if (skip) return;
  app = await openApp({
    mocks: {
      readiness_check: () => [],
      git_info: () => ({ installed: true, version: "2.47.1", current: db.forgotten === db.current ? OTHER : db.current, kinds: KINDS,
        repos: [REPO, OTHER, CAP].filter((p) => p !== db.forgotten).map((p) => ({ path: p, name: p.split("/").pop(), exists: true, kind: db.kinds[p] || null, suggested: p === REPO ? db.suggested : null })),
        notebooks_folder: "C:/Users/chris/KratosForge/notebooks", clone_folder: "C:/Users/chris/source/repos" }),
      git_status: ({ repo }) => status(repo),
      git_open: ({ path }) => { db.current = path; return status(path); },
      git_diff: ({ path, staged, notebook, base }) => {
        if (path === "broken.py") throw new Error("GitError: Git couldn't read broken.py.");
        if (path === "logo.png") return { path, staged, binary: true, hunks: [], added: 0, removed: 0, view: "text" };
        if (path === "daily.ipynb") return { path, staged, binary: false, view: notebook ? "notebook" : "text", hunks: notebook ? [] : copy(HUNKS.slice(0, 1)), added: 1, removed: 1 };
        if (path === "orders.sql") return { path, view: "conflict", conflicts: 1, hunks: [{ header: "", lines: [
          { new: 1, text: "<<<<<<< HEAD", side: null }, { new: 2, text: "SELECT 1", side: "here" }, { new: 3, text: "=======", side: null },
          { new: 4, text: "SELECT 2", side: "remote" }, { new: 5, text: ">>>>>>> origin/main", side: null }] }] };
        return { path, staged: !!staged, base: base || null, view: "text", binary: false, untracked: false, hunks: copy(HUNKS), added: 3, removed: 1, truncated: !!base };
      },
      git_fetch: () => op("fetch", "op-f"),
      git_pull: ({ mode }) => ({ ...op("pull", `op-p-${mode}`) }),
      git_push: () => op("push", "op-u"),
      git_op_status: ({ id }) => ({ id, kind: { f: "fetch", p: "pull", u: "push" }[id[3]], repo: db.current, status: "ok", percent: null,
        message: { f: "Fetched from origin.", p: "Pulled 2 commits.", u: "Pushed main to origin." }[id[3]] }),
      git_stage_all: () => { db.staged.push(...db.unstaged); db.unstaged = []; return status(); },
      git_commit: ({ allow_secrets }) => {
        if (!allow_secrets && db.commitAnswers.shift() === "secrets") {
          return { blocked: "secrets", findings: [{ path: "load.py", line: 4, kind: "Storage account key", match: "AccountKey=abc…", remediation: "Move it to Key Vault." },
            { path: "conf.json", where: "connection.password", kind: "Password", match: "pass…", remediation: "Use a variable library." }] };
        }
        db.staged = [];
        return { commit: SHA("e"), branch: db.branch, status: status() };
      },
      git_set_kind: ({ repo, kind }) => { db.kinds[repo] = kind; return { status: status(repo) }; },
      git_create: ({ path, kind }) => { db.kinds[path] = kind; db.current = path; return status(path); },
      git_log: ({ skip: from = 0 }) => (from ? { commits: [{ ...COMMITS[1], commit: SHA("9"), short: "9999999", subject: "First commit" }], more: false }
        : { commits: copy(COMMITS), more: true }),
      git_compare: ({ base, target }) => ({ base, target, only_in_target: 1, files: [{ path: "load.py", state: "modified" }, { path: "old.py", state: "deleted" }] }),
      git_commit_detail: ({ commit }) => ({ commit, subject: "Newest change", author: "Chris", email: "chris@contoso.com", date: "2026-11-19T10:00:00+00:00",
        body: "Why: the orders grew.", base: SHA("b"), files: [{ path: "load.py", state: "modified" }] }),
      git_notebook_info: ({ name }) => NB_INFO[name] || null,
      git_settings: () => ({ root: db.current, identity: { name: "Chris", email: "chris@contoso.com", local_name: null, local_email: null },
        remotes: [], credential_helpers: [], ssh_command: true, git_version: null, notes: ["Git Credential Manager isn't set up."] }),
      git_add_remote: ({ url }) => ({ ...status(), remotes: [{ name: "origin", url, provider: "github" }] }),
      git_forget: ({ repo }) => { db.forgotten = repo; return { forgotten: true }; },
      notebook_places: () => ({ places: [{ root: null, label: "This PC" }, { root: REPO, label: "sales" }, { root: OTHER, label: "finance" }] }),
      new_notebook: ({ place }) => ({ name: "Notebook 1", cells: [], place: place ? { root: place, label: place.split("/").pop() } : null }),
      git_capacity_diff: () => ({ read_at: "2026-11-19T08:00:00Z", problems: [], counts: { same: 0, differs: 1, not_in_git: 0, not_visible: 1 },
        items: [{ name: "Sales F64", state: "differs", fields: [{ field: "sku", git: "F64", fabric: "F128" }, { field: "region", git: "westeurope", fabric: "northeurope" }],
          workspaces: { same: 1, elsewhere: [{ name: "Finance", on: "Shared F2" }], not_in_git: [{ id: "w9" }], not_visible: [] } },
        { name: "Hidden F2", state: "not_visible", fields: [], workspaces: { same: 0, elsewhere: [], not_in_git: [], not_visible: [] } }] }),
      git_platform_deploy: ({ remove, confirm }) => {
        const steps = [{ kind: "domains", id: "sales", action: "update" }].concat(remove.map((r) => ({ ...r, action: "remove" })));
        const plan = { commit: "0123456789abcdef", catalog_env: "prod", labels: { domains: "Domain" }, plan_id: `p${remove.length}`, steps, stops: [],
          removable: [{ kind: "domains", id: "old" }, { kind: "domains", id: "old" }], blocked: [{ kind: "domains", id: "busy", why: "is still used by 2 products." }],
          notes: ["Key tables are compared in every environment."] };
        if (confirm) return { ...plan, plan_id: "p-new", deployed: false };  // the files changed since: a new plan
        return plan;
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
    Object.assign(gitPage, { info: null, repo: null, status: null, tab: "changes", selected: null, diff: null, diffMode: "inline", notebookView: true,
      message: "", branches: null, log: null, detail: null, compare: [], compared: null, settings: null, op: null });
    gitPage.capacity.key = null;
    window.__fdShellCalls.length = 0;
    document.getElementById("toast").textContent = "";
    go("home");
  });
  app.calls.length = 0;
  app.errors.length = 0;
});

async function openSource(tab = null) {
  await page().evaluate((t) => { if (t) gitPage.tab = t; go("source"); }, tab);
  await page().locator(".git-page .subtabs").waitFor();
}
async function toast(text) {
  try {
    await page().waitForFunction((t) => document.getElementById("toast").textContent === t, text, { timeout: 10000 });
  } catch {
    throw new Error(`The toast never said ${JSON.stringify(text)}; it says ${JSON.stringify(await page().locator("#toast").textContent())}`);
  }
}
const selectFile = async (path) => {
  await page().locator(".git-changes .git-file", { hasText: path.split("/").pop() }).first().click();
  await page().waitForFunction((p) => gitPage.diff && gitPage.selected?.path === p, path);
};

test("Fetch, Pull and merge, and Push send this repository and say when they're done", { skip }, async () => {
  await openSource();
  await page().getByRole("button", { name: "Fetch" }).click();
  await toast("Fetched from origin.");
  await page().waitForFunction(() => gitPage.op === null && !document.querySelector(".git-op"));
  await page().getByRole("button", { name: "Pull options" }).click();
  await page().getByRole("menuitem", { name: "Pull and merge" }).click();
  await toast("Pulled 2 commits.");
  await page().waitForFunction(() => gitPage.op === null && !document.querySelector("button[aria-label='Pull options']")?.disabled);
  await page().getByRole("button", { name: "Push", exact: true }).click();
  await toast("Pushed main to origin.");
  assert.deepStrictEqual(calls("git_fetch"), [{ repo: REPO }]);
  assert.deepStrictEqual(calls("git_pull"), [{ repo: REPO, mode: "merge" }]);
  assert.deepStrictEqual(calls("git_push"), [{ repo: REPO }]);
  assert.deepStrictEqual(app.errors, []);
});

test("the provider's links open the pull request page, the repository, the branch and its pull requests", { skip }, async () => {
  db.links = { label: "GitHub", repository: "https://github.com/contoso/sales", branch: "https://github.com/contoso/sales/tree/main",
    pull_requests: "https://github.com/contoso/sales/pulls?q=head:main", new_pull_request: "https://github.com/contoso/sales/compare/main?expand=1" };
  await openSource();
  await page().getByRole("button", { name: "Create pull request" }).click();
  await page().getByRole("button", { name: "Open on GitHub" }).click();
  await page().getByRole("menuitem", { name: "Branch main" }).click();
  await page().getByRole("button", { name: "Open on GitHub" }).click();
  await page().getByRole("menuitem", { name: "Pull requests for this branch" }).click();
  await page().getByRole("button", { name: "Open on GitHub" }).click();
  await page().getByRole("menuitem", { name: "Repository" }).click();
  await page().waitForFunction(() => window.__fdShellCalls.filter((c) => c.cmd === "open_link").length === 4);
  assert.deepStrictEqual(await shell(), [db.links.new_pull_request, db.links.branch, db.links.pull_requests, db.links.repository]);
});

test("Side by side pairs removed and added lines, and keeps each block's Stage button", { skip }, async () => {
  await openSource();
  await selectFile("load.py");
  await page().locator("#git-diff").getByRole("button", { name: "Side by side" }).click();
  const table = page().locator("#git-diff table.git-lines.split");
  await table.waitFor();
  const rows = await table.locator("tbody tr:not(.hunk)").evaluateAll((trs) => trs.map((tr) => [...tr.children].map((td) => td.textContent)));
  assert.deepStrictEqual(rows, [
    ["1", "import pandas as pd", "1", "import pandas as pd"],
    ["2", "rows = 10", "2", "rows = 100"],
    ["3", "", "3", ""],
    ["20", "    df = read()", "20", "    df = read()"],
    ["", "", "21", "    df = df.sample(5)"],
    ["", "", "22", "    print(df)"],
    ["21", "    return df", "23", "    return df"],
  ]);
  assert.strictEqual(await table.locator("td.code.del").first().textContent(), "rows = 10");
  assert.strictEqual(await table.locator("td.code.blank").count(), 2);
  assert.strictEqual(await table.getByRole("button", { name: "Stage block" }).count(), 2);
  assert.match(await page().locator("#git-diff .git-diff-head").innerText(), /Not staged · \+3 −1/);
  // The layout is kept for the next file.
  await page().locator("#git-diff").getByRole("button", { name: "Inline" }).click();
  await page().locator("#git-diff table.git-lines:not(.split)").waitFor();
  assert.deepStrictEqual(app.errors, []);
});

test("a notebook shows code changes only until Notebook view is turned off; a binary file and an unreadable diff say so", { skip }, async () => {
  await openSource();
  await selectFile("daily.ipynb");
  await page().getByText("No code changed. Only outputs or run counts differ; turn off Notebook view to see them.").waitFor();
  await page().getByLabel("Notebook view (code only)").uncheck();
  await page().locator("#git-diff table.git-lines").waitFor();
  assert.deepStrictEqual(calls("git_diff").filter((p) => p.path === "daily.ipynb").map((p) => p.notebook), [true, false]);
  await selectFile("logo.png");
  await page().getByText("This file isn't text, so there are no lines to compare.").waitFor();
  assert.doesNotMatch(await page().locator("#git-diff .git-diff-head").innerText(), /\+0/);
  await selectFile("broken.py");
  await page().getByText("The changes couldn't be shown.").waitFor();
  await toast("Couldn't show the changes: Git couldn't read broken.py.");
});

test("a merge that stopped on a conflict shows both sides and holds the commit back", { skip }, async () => {
  db.merging = true;
  db.conflicts = [{ path: "orders.sql", state: "conflict" }];
  await openSource();
  assert.match(await page().locator(".callout.git-warn").first().innerText(), /The merge stopped: 1 file changed both here and on the remote\./);
  const commit = page().getByRole("button", { name: "Fix the conflicts to commit" });
  assert.strictEqual(await commit.isDisabled(), true);
  assert.strictEqual(await page().getByRole("button", { name: "Discard changes to orders.sql" }).count(), 0);
  assert.strictEqual(await page().getByRole("button", { name: "Stage orders.sql", exact: true }).getAttribute("title"), "Mark as fixed (stage)");
  await page().locator(".git-changes .git-file", { hasText: "orders.sql" }).press("Enter");
  await page().getByText(/1 conflict left\./).waitFor();
  assert.deepStrictEqual(await page().locator("#git-diff tr.cf-here td.code, #git-diff tr.cf-remote td.code").allTextContents(), ["SELECT 1", "SELECT 2"]);
  assert.match(await page().locator("#git-diff .git-diff-head").innerText(), /orders\.sql\s*Conflict/);
  assert.strictEqual(await page().locator("#git-diff").getByRole("button", { name: "Side by side" }).count(), 0);
  // With every conflict fixed, the merge is committed.
  db.conflicts = [];
  await page().evaluate(() => gitAfterChange());
  await page().getByText("All conflicts are fixed. Commit to finish the merge.").waitFor();
  assert.strictEqual(await page().getByRole("button", { name: "Commit 1 staged file to main" }).count(), 0);
  await page().getByRole("button", { name: "Commit the merge to main" }).waitFor();
});

test("Commit asks for a message, stages everything first, and stops on a secret until told it isn't one", { skip }, async () => {
  db.staged = [];
  db.commitAnswers = ["secrets"];
  await openSource();
  const commit = page().getByRole("button", { name: "Stage all and commit 4 files to main" });
  await commit.click();
  await toast("Write a commit message first: say what changed and why.");
  assert.strictEqual(await page().evaluate(() => document.activeElement?.id), "git-message");
  assert.deepStrictEqual(calls("git_commit"), []);
  await page().getByLabel("Commit message").fill("Load 100 rows");
  await commit.click();
  const dialog = page().getByRole("dialog", { name: "These files look like they hold a secret" });
  await dialog.waitFor();
  const text = await dialog.innerText();
  assert.match(text, /load\.py\s+line 4\s+Storage account key\s+AccountKey=abc…\s+Move it to Key Vault\./);
  assert.match(text, /conf\.json\s+connection\.password/);
  await dialog.getByRole("button", { name: "It isn't a secret, commit" }).click();
  await toast("Committed eeeeeee to main. Push to send it to the remote.");
  assert.deepStrictEqual(calls("git_stage_all"), [{ repo: REPO }]);
  assert.deepStrictEqual(calls("git_commit").map((p) => [p.message, p.allow_secrets]), [["Load 100 rows", false], ["Load 100 rows", true]]);
  assert.strictEqual(await page().getByLabel("Commit message").inputValue(), "");
  assert.deepStrictEqual(app.errors, []);
});

test("Ctrl+Enter in the message commits what's staged; Go back and fix it commits nothing", { skip }, async () => {
  db.commitAnswers = ["secrets"];
  await openSource();
  await page().getByLabel("Commit message").fill("Notes");
  await page().getByLabel("Commit message").press("Control+Enter");
  await page().getByRole("dialog", { name: "These files look like they hold a secret" }).getByRole("button", { name: "Go back and fix it" }).click();
  assert.deepStrictEqual(calls("git_stage_all"), []);
  assert.strictEqual(calls("git_commit").length, 1);
  await page().getByLabel("Commit message").press("Control+Enter");
  await toast("Committed eeeeeee to main. Push to send it to the remote.");
  assert.deepStrictEqual(calls("git_commit").map((p) => p.message), ["Notes", "Notes"]);
});

test("Stage all stages every change", { skip }, async () => {
  await openSource();
  await page().getByRole("button", { name: "Stage all", exact: true }).click();
  await page().waitForFunction(() => document.querySelector(".git-changes .git-group")?.textContent.startsWith("Staged changes 5"));
  assert.deepStrictEqual(calls("git_stage_all"), [{ repo: REPO }]);
  await page().getByRole("button", { name: "Commit 5 staged files to main" }).waitFor();
});

test("the repository menu lists repositories by kind and switches to another", { skip }, async () => {
  await openSource();
  await page().getByTitle("Switch repository").click();
  const menu = page().getByRole("menu");
  const text = await menu.innerText();
  assert.match(text, /Notebooks\s+sales \(open\)\s+finance\s+Capacity\s+capacity\s+More/);
  await menu.getByRole("menuitem", { name: "finance" }).click();
  await page().waitForFunction(() => gitPage.status?.root === "C:/src/finance");
  assert.deepStrictEqual(calls("git_open"), [{ path: OTHER }]);
  assert.match(await page().locator(".git-sub").textContent(), /^C:\/src\/finance · origin: /);
});

test("a repository that doesn't say what it holds offers the kinds, the suggested one first", { skip }, async () => {
  db.kinds[REPO] = null;
  db.suggested = "project";
  await openSource();
  const banner = page().locator(".callout.git-kind");
  await banner.waitFor();
  assert.deepStrictEqual(await banner.locator("button").allTextContents(), ["Project", "Notebooks", "Capacity"]);
  assert.match(await banner.getByRole("button", { name: "Project" }).getAttribute("class"), /primary/);
  await banner.getByRole("button", { name: "Project" }).click();
  await toast("Marked as a project repository. Commit fabric-desktop.repo.toml to keep it.");
  assert.deepStrictEqual(calls("git_set_kind"), [{ repo: REPO, kind: "project", confirm: false }]);
  await page().waitForFunction(() => !document.querySelector(".callout.git-kind"));
  assert.strictEqual(await page().locator(".git-page .pill", { hasText: "Project" }).count(), 1);
});

test("New repository asks for a name, says what the chosen kind holds and creates it there", { skip }, async () => {
  await openSource();
  await page().getByTitle("Switch repository").click();
  await page().getByRole("menuitem", { name: "New repository" }).click();
  const dialog = page().getByRole("dialog", { name: "New repository" });
  await dialog.getByText("Notebooks and their Spark job definitions. For one team's notebooks.").waitFor();
  await dialog.getByRole("radio", { name: "Project" }).check();
  await dialog.getByText("A metadata project. For one data product.").waitFor();
  await dialog.getByRole("button", { name: "Create repository" }).click();
  await dialog.getByText("Give the repository a name.").waitFor();
  assert.deepStrictEqual(calls("git_create"), []);
  await dialog.getByLabel("Repository name").fill("orders-project");
  await dialog.getByRole("button", { name: "Create repository" }).click();
  await toast("Repository created with its starting files. Review them and commit to begin its history.");
  assert.deepStrictEqual(calls("git_create"), [{ path: "C:/Users/chris/source/repos/orders-project", kind: "project" }]);
  assert.strictEqual(await dialog.count(), 0);
});

test("History compares two ticked commits, shows a file's changes between them, and reads more", { skip }, async () => {
  db.links = { label: "GitHub", repository: "https://github.com/contoso/sales" };
  await openSource("history");
  await page().getByText("Newest change").waitFor();
  await page().getByText("Tick two commits to compare them.").waitFor();
  await page().getByLabel("Compare ccccccc").check();
  await page().getByLabel("Compare bbbbbbb").check();
  await page().getByRole("button", { name: "Compare the two commits" }).click();
  await page().getByText("2 files differ · 1 commit in between").waitFor();
  assert.deepStrictEqual(calls("git_compare"), [{ repo: REPO, base: SHA("b"), target: SHA("c") }]);
  assert.match(await page().locator(".git-commit h2").textContent(), /^bbbbbbb → ccccccc$/);
  assert.strictEqual(await page().getByRole("button", { name: "Open old.py at this commit" }).count(), 0);
  await page().getByRole("button", { name: "load.py", exact: true }).click();
  await page().getByText("Only the first 5,000 lines are shown.").waitFor();
  assert.deepStrictEqual(calls("git_diff").map((p) => [p.path, p.base, p.target]), [["load.py", SHA("b"), SHA("c")]]);
  assert.match(await page().locator("#git-diff .git-diff-head").innerText(), /bbbbbbb → ccccccc/);
  // Commit-to-commit diffs have no block buttons.
  assert.strictEqual(await page().getByRole("button", { name: "Stage block" }).count(), 0);
  await page().getByRole("button", { name: "Clear" }).click();
  await page().getByText("Tick two commits to compare them.").waitFor();
  await page().getByRole("button", { name: "Show more" }).click();
  await page().getByText("First commit").waitFor();
  assert.deepStrictEqual(calls("git_log").map((p) => p.skip), [undefined, 2]);
  assert.strictEqual(await page().getByRole("button", { name: "Show more" }).count(), 0);
  // A commit opens with its body and a link to it on the provider.
  await page().getByText("Newest change").first().click();
  await page().getByText("Why: the orders grew.").waitFor();
  await page().locator(".git-commit").getByRole("button", { name: "Open on GitHub" }).click();
  await page().waitForFunction(() => window.__fdShellCalls.some((c) => c.cmd === "open_link"));
  assert.deepStrictEqual(await shell(), [`https://github.com/contoso/sales/commit/${SHA("c")}`]);
  assert.deepStrictEqual(app.errors, []);
});

test("a notebook in a repository shows where its data is and each environment's location", { skip }, async () => {
  await page().evaluate(() => {
    loadIntoState({ name: "Daily sales", cells: [{ type: "code", source: "x = 1" }], place: { root: "C:/src/sales", label: "sales" },
      locations: { problem: "locations.toml names a workspace you can't open.", envs: {
        dev: { workspace_id: "w1", workspace_name: "Sales Dev", lakehouse: { name: "LH_Dev", workspace_name: "Sales Dev" }, sql_name: "SQL_Dev" },
        prod: { workspace_id: null } } } });
    go("notebook");
  });
  const chip = page().getByRole("button", { name: "Data: LH_Dev" });
  await chip.waitFor();
  assert.match(await chip.getAttribute("title"), /^LH_Dev in Sales Dev\. Where Daily sales reads and writes data\./);
  await chip.click();
  const menu = await page().getByRole("menu").innerText();
  assert.match(menu, /From sales \/ locations\.toml/);
  assert.match(menu, /Dev: Sales Dev \/ LH_Dev, SQL_Dev \(now\)/);
  assert.match(menu, /Test: not set/);
  assert.match(menu, /Prod: nothing attached \(this PC\)/);
  assert.match(menu, /locations\.toml names a workspace you can't open\./);
  assert.match(menu, /Attach a different lakehouse to this notebook only…/);
  await page().keyboard.press("Escape");

  // A lakehouse on the notebook itself wins, and the menu offers to go back to the repository's.
  await page().evaluate(() => { state.notebook.lakehouse = { name: "LH_Mine" }; renderNotebook(); });
  await page().getByRole("button", { name: "Data: LH_Mine (this notebook)" }).click();
  assert.match(await page().getByRole("menu").innerText(), /Use the repository's locations again \(stop using LH_Mine\)/);
  await page().keyboard.press("Escape");

  // No location for this environment: a warning chip; nothing attached: says so.
  await page().evaluate(() => { state.notebook.lakehouse = null; state.notebook.locations = null; renderNotebook(); });
  const unset = page().getByRole("button", { name: "No location for Dev" });
  await unset.waitFor();
  assert.match(await unset.getAttribute("class"), /warn/);
  await page().evaluate(() => { state.notebook.locations = { envs: { dev: { workspace_id: null } } }; renderNotebook(); });
  await page().getByRole("button", { name: "Data: nothing attached in Dev" }).waitFor();
  assert.deepStrictEqual(app.errors, []);
});

test("a new notebook asks where it goes when there are repositories, and remembers the choice", { skip }, async () => {
  await page().evaluate(() => { try { localStorage.removeItem("fd.notebookPlace"); } catch { /* off */ } state.notebook = null; newNotebook(); });
  const dialog = page().getByRole("dialog", { name: "New notebook" });
  // The first repository comes first when nothing was chosen before.
  assert.strictEqual(await dialog.getByLabel("Save in").inputValue(), REPO);
  assert.deepStrictEqual(await dialog.getByLabel("Save in").locator("option").allTextContents(),
    ["This PC (the app's notebooks folder)", "sales (repository)", "finance (repository)"]);
  await dialog.getByLabel("Save in").selectOption(OTHER);
  await dialog.getByRole("button", { name: "Create" }).click();
  await page().waitForFunction(() => state.view === "notebook" && state.notebook?.name === "Notebook 1");
  assert.deepStrictEqual(calls("new_notebook"), [{ place: OTHER }]);
  // The next one starts on the last choice; Cancel makes nothing.
  await page().evaluate(() => { state.notebook = null; newNotebook(); });
  assert.strictEqual(await dialog.getByLabel("Save in").inputValue(), OTHER);
  await dialog.getByLabel("Save in").selectOption("");
  await dialog.getByRole("button", { name: "Cancel" }).click();
  assert.strictEqual(await dialog.count(), 0);
  assert.strictEqual(calls("new_notebook").length, 1);
  assert.deepStrictEqual(app.errors, []);
});

test("Capacity lists what differs and Copy report puts it on the clipboard as text", { skip }, async () => {
  db.current = CAP;
  await page().evaluate(() => {
    window.__copied = null;
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText: async (t) => { window.__copied = t; } } });
  });
  await openSource("capacity");
  await page().getByRole("heading", { name: "Sales F64" }).waitFor();
  assert.match(await page().locator(".git-platform").innerText(), /0 same · 1 differ · 0 not in Git · 1 not visible to you/);
  await page().getByRole("button", { name: "Copy report" }).click();
  await toast("Copied the report.");
  const text = await page().evaluate(() => window.__copied);
  assert.strictEqual(text, ["Capacity drift report, read 2026-11-19T08:00:00Z",
    "Sales F64: differs",
    "  Size: Git F64, Fabric F128",
    "  Region: Git westeurope, Fabric northeurope",
    "  Finance: Git expects it here; Fabric has it on Shared F2",
    "  w9: on this capacity in Fabric, not listed in capacity.toml",
    "  1 workspace is where Git expects",
    "Hidden F2: not visible to you", ""].join("\n"));
  // The copy fails: says so.
  await page().evaluate(() => { navigator.clipboard.writeText = async () => { throw new Error("denied"); }; });
  await page().getByRole("button", { name: "Copy report" }).click();
  await toast("Couldn't copy the report.");
});

test("Deploy plans again when a removal is ticked, and shows a plan that changed before it ran", { skip }, async () => {
  await page().evaluate(() => { gitPlatformDeploy("C:/src/platform"); });
  const dialog = page().getByRole("dialog", { name: "Deploy the platform repository" });
  await dialog.getByText("Will do (1 step)").waitFor();
  const text = await dialog.innerText();
  assert.match(text, /Deploys commit 01234567: the catalog in Prod/);
  assert.match(text, /✓ Domain sales: save the changes/);
  assert.match(text, /Skipped \(1\)\s+Domain busy is still used by 2 products\./);
  assert.match(text, /Key tables are compared in every environment\./);
  // The same removal listed twice is one tick.
  assert.strictEqual(await dialog.getByRole("checkbox").count(), 1);
  await dialog.getByRole("checkbox", { name: "Domain old" }).check();
  await dialog.getByText("Will do (2 steps)").waitFor();
  assert.match(await dialog.innerText(), /✓ Domain old: remove/);
  assert.deepStrictEqual(calls("git_platform_deploy").map((p) => p.remove), [[], [{ kind: "domains", id: "old" }]]);
  await dialog.getByRole("button", { name: "Deploy 2 steps" }).click();
  await dialog.getByText("The platform or the files changed since this plan was shown. Check the new plan, then deploy.").waitFor();
  assert.deepStrictEqual(calls("git_platform_deploy")[2], { repo: "C:/src/platform", remove: [{ kind: "domains", id: "old" }], confirm: "p1" });
  // Unticking plans again without it.
  await dialog.getByRole("checkbox", { name: "Domain old" }).uncheck();
  await dialog.getByText("Will do (1 step)").waitFor();
  assert.deepStrictEqual(calls("git_platform_deploy")[3].remove, []);
  assert.deepStrictEqual(app.errors, []);
});

test("a notebook in a repository has a branch chip that opens its changes in Source control", { skip }, async () => {
  await page().evaluate(() => { loadIntoState({ name: "Orders load", cells: [{ type: "code", source: "x = 1" }] }); go("notebook"); });
  const chip = page().getByRole("button", { name: "main · modified" });
  await chip.waitFor();
  assert.strictEqual(await chip.getAttribute("title"), "In the repository finance. Open its changes in Source control.");
  await chip.click();
  await page().waitForFunction(() => state.view === "source" && gitPage.selected?.path === "load.py" && gitPage.diff);
  assert.deepStrictEqual(calls("git_open"), [{ path: OTHER }]);
  assert.strictEqual(calls("git_diff")[0].repo, OTHER);
  assert.strictEqual(await page().locator(".git-changes .git-file.on").count(), 1);
});

test("a notebook in a repository for another kind says so and opens that repository", { skip }, async () => {
  await page().evaluate(() => { loadIntoState({ name: "Misplaced", cells: [{ type: "code", source: "x = 1" }] }); go("notebook"); });
  const chip = page().getByRole("button", { name: "dev · wrong repository" });
  await chip.waitFor();
  assert.match(await chip.getAttribute("title"), /^finance holds Project definitions, not notebooks\. Open finance in Source control/);
  await chip.click();
  await page().waitForFunction(() => state.view === "source" && gitPage.status?.root === "C:/src/finance");
  assert.deepStrictEqual(calls("git_open"), [{ path: OTHER }]);
});

test("Repository settings adds an origin and takes the repository off the list after asking", { skip }, async () => {
  await openSource("settings");
  await page().getByText("None yet. Add one to push this repository.").waitFor();
  const text = await page().locator(".git-settings").innerText();
  assert.match(text, /Git \(version unknown\) · credential helper: none · custom SSH command/);
  assert.match(text, /Git Credential Manager isn't set up\./);
  await page().getByLabel("Remote address").fill("https://github.com/contoso/sales.git");
  await page().getByRole("button", { name: "Add as origin" }).click();
  await page().waitForFunction(() => gitPage.settings && document.querySelector(".git-settings"));
  assert.deepStrictEqual(calls("git_add_remote"), [{ repo: REPO, url: "https://github.com/contoso/sales.git" }]);
  await page().getByRole("button", { name: "Take it off the list" }).click();
  const ask = page().getByRole("dialog", { name: "Take this repository off the list?" });
  await ask.getByRole("button", { name: "Cancel" }).click();
  assert.deepStrictEqual(calls("git_forget"), []);
  await page().getByRole("button", { name: "Take it off the list" }).click();
  await ask.getByRole("button", { name: "Take off the list" }).click();
  await page().waitForFunction(() => gitPage.status?.root === "C:/src/finance");
  assert.deepStrictEqual(calls("git_forget"), [{ repo: REPO }]);
  assert.ok(!(await page().evaluate(() => gitPage.info.repos.map((r) => r.path))).includes(REPO));
});
