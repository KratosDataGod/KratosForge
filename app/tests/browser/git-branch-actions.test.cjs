// #1422: Source control's actions that throw work away, and the branch actions next to them (see
// harness.cjs for how to run it). Discard changes, Delete file (a file Git doesn't track yet),
// Discard block, Delete branch, Delete anyway (a branch with commits nowhere else) and Undo the pull
// each ask first in a danger confirm: nothing is sent while it is open, Cancel sends nothing, and
// confirming sends exactly that repository and those paths, that block or that branch. Unstage sends
// those paths. Create branch and Switch send the name (and, with uncommitted changes, the choice made
// in the dialog), and the page then shows the new branch. Bring back sends that stash. Take it off
// the list says the folder stays and sends nothing on Cancel. An engine refusal shows a plain message
// and the page stays as it was. Opening the tabs sends no write, and axe finds nothing serious on
// each confirm.
//
// The engine is mocked: each mock answers in the shape sidecar/fabric_desktop/git_service.py and
// git/repo.py return (status, branches, stashes, diff hunks, switch's needs_choice), and a refusal is
// the text the engine sends ("GitError: <plain words>", git/cli.py explain()).
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp, startupReads, axeSource, axeFindings } = require("./harness.cjs");

let app;
const skip = !playwright && "Playwright is not installed";
const page = () => app.page;
const REPO = "C:/src/sales";
const OTHER = "C:/src/finance";  // a second repository on the list: nothing may be sent for it
const READS = ["git_info", "git_status", "git_branches", "git_diff", "git_settings"];
const WRITES = ["git_discard", "git_apply_hunk", "git_stage", "git_unstage", "git_stage_all", "git_commit", "git_create_branch",
  "git_switch", "git_delete_branch", "git_restore_stash", "git_abort_merge", "git_forget", "git_open", "git_set_kind",
  "git_set_identity", "git_add_remote", "git_fetch", "git_pull", "git_push"];
const SHA = (c) => c.repeat(40);
const copy = (x) => JSON.parse(JSON.stringify(x));

// git/repo.py parse_unified + hunk_id: two blocks of changed lines in load.py.
const HUNKS = [
  { id: "a1b2c3d4e5f60718", header: "@@ -1,4 +1,4 @@", old_start: 1, new_start: 1, context: "", lines: [
    { kind: " ", old: 1, new: 1, text: "import pandas as pd" }, { kind: "-", old: 2, new: null, text: "rows = 10" },
    { kind: "+", old: null, new: 2, text: "rows = 100" }, { kind: " ", old: 3, new: 3, text: "" }] },
  { id: "f0e1d2c3b4a59687", header: "@@ -20,3 +20,4 @@ def load():", old_start: 20, new_start: 20, context: "def load():", lines: [
    { kind: " ", old: 20, new: 20, text: "    df = read()" }, { kind: "+", old: null, new: 21, text: "    df = df.sample(5)  # experiment" },
    { kind: "+", old: null, new: 22, text: "    print(df)" }, { kind: " ", old: 21, new: 23, text: "    return df" }] },
];

function fresh() {
  return {
    branch: "main", merging: false,
    staged: [{ path: "notes.md", state: "modified", code: "M" }],
    unstaged: [{ path: "load.py", state: "modified", code: "M" }, { path: "orders.sql", state: "modified", code: "M" }],
    untracked: [{ path: "scratch.py", state: "untracked", code: "U" }],
    conflicts: [],
    local: [
      { name: "main", commit: SHA("a"), upstream: "origin/main", subject: "Load the orders table", ahead: 0, behind: 0, gone: false },
      { name: "report/q3-totals", commit: SHA("b"), upstream: "origin/report/q3-totals", subject: "Q3 totals", ahead: 0, behind: 0, gone: true },
      { name: "spike/forecast", commit: SHA("c"), upstream: null, subject: "Try a forecast", ahead: 0, behind: 0, gone: false },
    ],
    remote: [{ name: "origin/main", commit: SHA("a") }, { name: "origin/fix/typo", commit: SHA("d") }],
    stashes: [
      { ref: "stash@{0}", date: "2026-10-30T09:00:00+00:00", message: "On spike/forecast: KratosForge: changes on spike/forecast before switching to main" },
      { ref: "stash@{1}", date: "2026-10-28T09:00:00+00:00", message: "On main: KratosForge: changes on main before switching to report/q3-totals" },
    ],
    repos: [REPO, OTHER],
  };
}
let db = fresh();
const fail = {};  // method -> the engine's error text, for the next calls to it

// git/repo.py status(), as git_status and every change return it.
function status() {
  const changed = db.staged.length + db.unstaged.length + db.untracked.length + db.conflicts.length;
  return copy({ commit: SHA("a"), branch: db.branch, upstream: db.branch === "main" ? "origin/main" : null, ahead: 0, behind: 0,
    root: REPO, name: "sales", staged: db.staged, unstaged: db.unstaged, untracked: db.untracked, conflicts: db.conflicts,
    changed, dirty: changed > 0, merging: db.merging, stashes: db.stashes.length,
    remotes: [{ name: "origin", url: "https://github.com/contoso/sales.git", provider: "github", web: "https://github.com/contoso/sales" }],
    fetched_at: "2026-10-31T08:00:00+00:00", links: null,
    summary: `${db.branch}, ${changed ? `${changed} uncommitted changes` : "no uncommitted changes"}.` });
}
// git_service.py git_branches: repo.py branches() plus stashes().
function branches() {
  const local = db.local.map((b) => ({ ...b, date: "2026-10-29T10:00:00+00:00", current: b.name === db.branch }));
  const tracked = new Set(local.map((b) => b.upstream).filter(Boolean));
  return copy({ current: db.branch, detached: false, local,
    remote: db.remote.map((r) => ({ ...r, remote: "origin", date: "2026-10-29T10:00:00+00:00", subject: "Fix a typo", tracked: tracked.has(r.name) })),
    stashes: db.stashes });
}
const repo = (name) => ({ path: name, name: name.split("/").pop(), exists: true, kind: "notebooks", suggested: null });
const guard = (method, fn) => (p) => {
  if (fail[method]) throw new Error(fail[method]);
  if (p.repo !== REPO) throw new Error("GitError: Open this repository in Source control first.");
  return fn(p);
};
const mocks = {
  readiness_check: () => [],
  // git_service.py git_info: git_repos() with whether Git is installed.
  git_info: () => ({ installed: true, version: "2.47.1", repos: db.repos.map(repo), current: db.repos.includes(REPO) ? REPO : db.repos[0] || null,
    kinds: { notebooks: { label: "Notebooks", holds: "Notebooks and their Spark job definitions.", for: "For one team's notebooks." } },
    notebooks_folder: "C:/Users/chris/KratosForge/notebooks", clone_folder: "C:/Users/chris/source/repos" }),
  git_status: guard("git_status", () => status()),
  git_branches: guard("git_branches", () => branches()),
  git_settings: guard("git_settings", () => ({ root: REPO, identity: { name: "Chris", email: "chris@contoso.com", local_name: null, local_email: null },
    remotes: status().remotes, credential_helpers: ["manager"], ssh_command: false, git_version: "2.47.1", notes: [] })),
  git_diff: guard("git_diff", ({ path, staged }) => ({ path, staged: !!staged, base: null, target: null, untracked: false, view: "text",
    binary: false, hunks: path === "load.py" && !staged ? HUNKS : [], added: 3, removed: 1, truncated: false })),
  git_discard: guard("git_discard", ({ paths, confirm }) => {
    if (!confirm) throw new Error("GitError: This throws away your changes to 1 file and can't be undone. Confirm to go ahead.");
    db.unstaged = db.unstaged.filter((e) => !paths.includes(e.path));
    db.untracked = db.untracked.filter((e) => !paths.includes(e.path));
    return status();
  }),
  git_apply_hunk: guard("git_apply_hunk", ({ action, confirm }) => {
    if (action === "discard" && !confirm) throw new Error("GitError: This throws away 2 changed lines in load.py. Confirm to go ahead.");
    return status();
  }),
  git_unstage: guard("git_unstage", ({ paths }) => {
    const out = db.staged.filter((e) => !paths || paths.includes(e.path));
    db.staged = db.staged.filter((e) => !out.includes(e));
    db.unstaged.push(...out);
    return status();
  }),
  git_create_branch: guard("git_create_branch", ({ name }) => {
    if (db.local.some((b) => b.name === name)) throw new Error(`GitError: A branch called ${name} already exists.`);
    db.local.push({ name, commit: SHA("a"), upstream: null, subject: "Load the orders table", ahead: 0, behind: 0, gone: false });
    db.branch = name;
    const { stashes, ...b } = branches();
    return b;
  }),
  // git_service.py git_switch: with uncommitted changes and no mode, needs_choice instead of switching.
  git_switch: guard("git_switch", ({ name, mode }) => {
    const changed = db.staged.length + db.unstaged.length + db.untracked.length;
    if (changed && !["stash", "carry"].includes(mode)) {
      return { needs_choice: true, message: `You have ${changed} uncommitted changes. Choose whether to put them aside or bring them with you.`,
        changes: copy([...db.staged, ...db.unstaged, ...db.untracked]) };
    }
    let local = name;
    if (name.startsWith("origin/")) {
      local = name.slice("origin/".length);
      db.local.push({ name: local, commit: SHA("d"), upstream: name, subject: "Fix a typo", ahead: 0, behind: 0, gone: false });
    } else if (!db.local.some((b) => b.name === name)) throw new Error(`GitError: There's no branch called ${name}.`);
    const stashed = changed > 0 && mode === "stash";
    if (stashed) {
      db.stashes.unshift({ ref: "stash@{0}", date: "2026-11-01T09:00:00+00:00", message: `On ${db.branch}: KratosForge: changes on ${db.branch} before switching to ${local}` });
      db.stashes.forEach((s, i) => { s.ref = `stash@{${i}}`; });
      db.staged = []; db.unstaged = []; db.untracked = [];
    }
    db.branch = local;
    return { branch: local, stashed, status: status() };
  }),
  git_delete_branch: guard("git_delete_branch", ({ name, confirm, force }) => {
    if (!confirm) throw new Error(`GitError: This deletes the branch ${name} on this PC (the remote's copy stays). Confirm to go ahead.`);
    if (name === "spike/forecast" && !force) throw new Error("GitError: That branch has commits no other branch holds. Deleting it would lose them.");
    db.local = db.local.filter((b) => b.name !== name);
    const { stashes, ...b } = branches();
    return b;
  }),
  git_restore_stash: guard("git_restore_stash", ({ ref }) => {
    db.stashes = db.stashes.filter((s) => s.ref !== ref);
    db.stashes.forEach((s, i) => { s.ref = `stash@{${i}}`; });
    db.unstaged.push({ path: "forecast.py", state: "modified", code: "M" });
    return status();
  }),
  git_abort_merge: guard("git_abort_merge", ({ confirm }) => {
    if (!confirm) throw new Error("GitError: This undoes the merge and puts every file back as it was before the pull. Confirm to go ahead.");
    db.merging = false; db.conflicts = [];
    return status();
  }),
  // git_service.py git_forget: the list without it; the folder is left as it is.
  git_forget: ({ repo: r }) => {
    if (fail.git_forget) throw new Error(fail.git_forget);
    db.repos = db.repos.filter((p) => p !== r);
    return { repos: db.repos.map(repo), current: db.repos[0] || null };
  },
};

before(async () => {
  if (skip) return;
  app = await openApp({ mocks });
  await startupReads(app);
  for (const end = Date.now() + 10000; !app.calls.some((c) => c.method === "git_status"); await new Promise((r) => setTimeout(r, 25))) {
    if (Date.now() > end) throw new Error("the app's startup read of the repository (git_status) never came");
  }
});
after(async () => { if (app) await app.close(); });

// Each test starts from the same repository, on the Changes tab, with no dialog or message showing.
beforeEach(async () => {
  if (skip) return;
  db = fresh();
  for (const k of Object.keys(fail)) delete fail[k];
  await page().evaluate(() => {
    closeMenu(); closeDialog();
    const t = document.getElementById("toast"); t.hidden = true; t.textContent = "";
    Object.assign(gitPage, { info: null, repo: null, status: null, tab: "changes", selected: null, diff: null, branches: null,
      log: null, settings: null, message: "" });
    go("home");
  });
});

const writes = (from) => app.calls.slice(from).filter((c) => WRITES.includes(c.method)).map((c) => ({ method: c.method, params: c.params }));
const pause = (ms) => new Promise((r) => setTimeout(r, ms));
const toastText = async () => (await page().waitForFunction(() => { const t = document.getElementById("toast"); return !t.hidden && t.textContent; })).jsonValue();

// Source control open on a tab, once that tab has drawn.
async function open(tab) {
  await page().evaluate((tab) => { gitPage.tab = tab; go("source"); }, tab);
  const ready = { changes: ".git-page .git-changes", branches: ".git-page .git-branches table", settings: ".git-page .git-settings" }[tab];
  await page().locator(ready).first().waitFor();
}
const fileRow = (path) => page().locator(".git-changes .git-file", { has: page().locator(".git-name", { hasText: new RegExp(`^${path}`) }) });
const branchRow = (name) => page().locator(".git-branches tr", { has: page().getByText(name, { exact: true }) });
const stashRow = (text) => page().locator(".git-branches tr", { hasText: text });

// Waits for the confirm, checks it is a danger confirm with nothing sent yet, and returns it.
async function confirmShown(title, from, text) {
  const dialog = page().getByRole("dialog", { name: title });
  await dialog.waitFor();
  if (text) assert.match(await dialog.innerText(), text);
  assert.match(await dialog.locator(".dialog-actions button").last().getAttribute("class"), /danger-solid/, `${title} isn't a danger confirm`);
  assert.deepStrictEqual(writes(from), [], `${title}: something was sent before the confirm was answered`);
  return dialog;
}

// The destructive actions on the Changes tab: where each starts, its confirm, and what it sends.
const CHANGES = [
  { name: "Discard changes", start: () => fileRow("load.py").getByRole("button", { name: "Discard changes to load.py" }).click(),
    title: "Discard changes?", ok: "Discard changes", text: /Your changes to load\.py since it was last staged or committed are thrown away/,
    sent: [{ method: "git_discard", params: { repo: REPO, paths: ["load.py"], confirm: true } }],
    gone: () => fileRow("load.py") },
  { name: "Delete file", start: () => fileRow("scratch.py").getByRole("button", { name: "Discard changes to scratch.py" }).click(),
    title: "Delete this file?", ok: "Delete file", text: /scratch\.py isn't in Git yet, so discarding deletes it from this PC/,
    sent: [{ method: "git_discard", params: { repo: REPO, paths: ["scratch.py"], confirm: true } }],
    gone: () => fileRow("scratch.py") },
  { name: "Discard block", start: async () => {
    await fileRow("load.py").locator(".git-name").click();
    await page().locator("#git-diff tr.hunk").nth(1).getByRole("button", { name: "Discard block…" }).click();
  }, title: "Discard this block?", ok: "Discard block", text: /Throws away these 2 changed lines in load\.py \(lines 20 to 23\)/,
  sent: [{ method: "git_apply_hunk", params: { repo: REPO, path: "load.py", hunk: HUNKS[1].id, action: "discard", confirm: true } }] },
];

for (const a of CHANGES) {
  test(`${a.name} asks first; Cancel sends nothing, and confirming sends exactly that repository and path`, { skip }, async () => {
    await open("changes");
    let from = app.calls.length;
    await a.start();
    let dialog = await confirmShown(a.title, from, a.text);
    await dialog.getByRole("button", { name: "Cancel" }).click();
    await dialog.waitFor({ state: "detached" });
    await pause(200);
    assert.deepStrictEqual(writes(from), [], "Cancel sent something");
    assert.strictEqual(await fileRow(a.sent[0].params.paths?.[0] || "load.py").count(), 1, "the file left the list on Cancel");

    from = app.calls.length;
    await a.start();
    dialog = await confirmShown(a.title, from, a.text);
    await dialog.getByRole("button", { name: a.ok, exact: true }).click();
    await dialog.waitFor({ state: "detached" });
    for (const end = Date.now() + 5000; writes(from).length < a.sent.length && Date.now() < end;) await pause(25);
    await pause(200);
    assert.deepStrictEqual(writes(from), a.sent);
    if (a.gone) await a.gone().waitFor({ state: "detached" });
    assert.deepStrictEqual(app.errors, []);
  });
}

test("Unstage sends that path only, Unstage all sends the repository, and neither needs a confirm", { skip }, async () => {
  db.staged.push({ path: "README.md", state: "modified", code: "M" });
  await open("changes");
  let from = app.calls.length;
  await fileRow("notes.md").getByRole("button", { name: "Unstage notes.md" }).click();
  await page().locator(".git-changes .git-group", { hasText: "Staged changes 1" }).waitFor();
  assert.deepStrictEqual(writes(from), [{ method: "git_unstage", params: { repo: REPO, paths: ["notes.md"] } }]);
  assert.strictEqual(await fileRow("README.md").getByRole("button", { name: "Unstage README.md" }).count(), 1);
  from = app.calls.length;
  await page().getByRole("button", { name: "Unstage all" }).click();
  await page().locator(".git-changes .git-group", { hasText: "Staged changes" }).waitFor({ state: "detached" });
  assert.deepStrictEqual(writes(from), [{ method: "git_unstage", params: { repo: REPO } }]);
  assert.strictEqual(await page().getByRole("dialog").count(), 0);
});

test("Undo the pull asks first; Cancel sends nothing, and confirming sends that repository and the banner goes", { skip }, async () => {
  db.merging = true;
  db.conflicts = [{ path: "load.py", state: "conflict", code: "C", how: "changed on both sides" }];
  db.unstaged = [];
  await open("changes");
  const undo = page().locator(".git-page .git-warn").getByRole("button", { name: "Undo the pull" });
  let from = app.calls.length;
  await undo.click();
  let dialog = await confirmShown("Undo the pull?", from, /Every file goes back to how it was before the pull, including the conflicts you fixed/);
  await dialog.getByRole("button", { name: "Cancel" }).click();
  await dialog.waitFor({ state: "detached" });
  await pause(200);
  assert.deepStrictEqual(writes(from), []);
  assert.strictEqual(await undo.count(), 1);
  from = app.calls.length;
  await undo.click();
  dialog = await confirmShown("Undo the pull?", from);
  await dialog.getByRole("button", { name: "Undo the pull", exact: true }).click();
  await page().locator(".git-page .git-warn").waitFor({ state: "detached" });
  assert.deepStrictEqual(writes(from), [{ method: "git_abort_merge", params: { repo: REPO, confirm: true } }]);
  assert.strictEqual((await toastText()).split("\n")[0], "The pull was undone.");
});

test("Delete branch asks first; Cancel sends nothing, and confirming sends that branch and it leaves the list", { skip }, async () => {
  await open("branches");
  const del = () => branchRow("report/q3-totals").getByRole("button", { name: "Delete" });
  let from = app.calls.length;
  await del().click();
  let dialog = await confirmShown("Delete report/q3-totals?", from, /The branch is deleted on this PC\. A copy on the remote stays/);
  await dialog.getByRole("button", { name: "Cancel" }).click();
  await dialog.waitFor({ state: "detached" });
  await pause(200);
  assert.deepStrictEqual(writes(from), []);
  from = app.calls.length;
  await del().click();
  dialog = await confirmShown("Delete report/q3-totals?", from);
  await dialog.getByRole("button", { name: "Delete branch", exact: true }).click();
  await branchRow("report/q3-totals").waitFor({ state: "detached" });
  assert.deepStrictEqual(writes(from), [{ method: "git_delete_branch", params: { repo: REPO, name: "report/q3-totals", confirm: true } }]);
  assert.strictEqual((await toastText()).split("\n")[0], "Deleted report/q3-totals.");
  assert.strictEqual(await branchRow("spike/forecast").count(), 1);
});

test("A branch with commits nowhere else asks again before Delete anyway, which sends force for that branch only", { skip }, async () => {
  await open("branches");
  const del = () => branchRow("spike/forecast").getByRole("button", { name: "Delete" });
  const first = { method: "git_delete_branch", params: { repo: REPO, name: "spike/forecast", confirm: true } };
  // Cancel on the second confirm: only Git's own refusal was asked for, nothing is deleted.
  let from = app.calls.length;
  await del().click();
  let dialog = await confirmShown("Delete spike/forecast?", from);
  await dialog.getByRole("button", { name: "Delete branch", exact: true }).click();
  const again = "spike/forecast has commits that aren't anywhere else";
  await page().getByRole("dialog", { name: again }).waitFor();
  dialog = page().getByRole("dialog", { name: again });
  assert.match(await dialog.innerText(), /Deleting it loses those commits unless they were pushed/);
  assert.match(await dialog.locator(".dialog-actions button").last().getAttribute("class"), /danger-solid/);
  assert.deepStrictEqual(writes(from), [first], "force was sent before Delete anyway");
  await dialog.getByRole("button", { name: "Cancel" }).click();
  await dialog.waitFor({ state: "detached" });
  await pause(200);
  assert.deepStrictEqual(writes(from), [first]);
  assert.strictEqual(await branchRow("spike/forecast").count(), 1);
  // Delete anyway.
  from = app.calls.length;
  await del().click();
  await (await confirmShown("Delete spike/forecast?", from)).getByRole("button", { name: "Delete branch", exact: true }).click();
  await page().getByRole("dialog", { name: again }).getByRole("button", { name: "Delete anyway" }).click();
  await branchRow("spike/forecast").waitFor({ state: "detached" });
  assert.deepStrictEqual(writes(from), [first, { method: "git_delete_branch", params: { ...first.params, force: true } }]);
  assert.strictEqual(await branchRow("report/q3-totals").count(), 1);
});

test("Create branch sends the name typed, and the page and the top bar show the new branch", { skip }, async () => {
  db.staged = []; db.unstaged = []; db.untracked = [];
  await open("branches");
  await page().getByRole("textbox", { name: "New branch name" }).fill("feature/q3-forecast");
  const from = app.calls.length;
  await page().getByRole("button", { name: "Create branch" }).click();
  await branchRow("feature/q3-forecast").locator(".pill-s.info").waitFor();
  assert.deepStrictEqual(writes(from), [{ method: "git_create_branch", params: { repo: REPO, name: "feature/q3-forecast" } }]);
  assert.match(await branchRow("feature/q3-forecast").innerText(), /current/);
  assert.strictEqual(await branchRow("main").getByRole("button", { name: "Switch" }).count(), 1);
  await page().locator("#git-slot .git-pill[aria-label^='Branch feature/q3-forecast in sales']").waitFor();
});

test("Switch on a clean repository sends that branch and no mode, and the page shows it", { skip }, async () => {
  db.staged = []; db.unstaged = []; db.untracked = [];
  await open("branches");
  const from = app.calls.length;
  await branchRow("report/q3-totals").getByRole("button", { name: "Switch" }).click();
  await branchRow("report/q3-totals").locator(".pill-s.info").waitFor();
  assert.deepStrictEqual(writes(from), [{ method: "git_switch", params: { repo: REPO, name: "report/q3-totals", mode: null } }]);
  assert.strictEqual((await toastText()).split("\n")[0], "Now on report/q3-totals.");
  await page().locator("#git-slot .git-pill[aria-label^='Branch report/q3-totals in sales']").waitFor();
});

test("Switch with uncommitted changes asks what to do with them and sends the choice; Cancel sends nothing more", { skip }, async () => {
  await open("branches");
  const asked = { method: "git_switch", params: { repo: REPO, name: "spike/forecast", mode: null } };
  let from = app.calls.length;
  await branchRow("spike/forecast").getByRole("button", { name: "Switch" }).click();
  let dialog = page().getByRole("dialog", { name: "Switch to spike/forecast?" });
  await dialog.waitFor();
  const said = await dialog.innerText();
  assert.match(said, /You have 4 uncommitted changes\. .* Nothing is thrown away\./);
  assert.match(said, /M notes\.md\nM load\.py\nM orders\.sql\nU scratch\.py/);
  assert.ok(await dialog.getByRole("radio", { name: /Put them aside/ }).isChecked(), "putting them aside is the default");
  await dialog.getByRole("button", { name: "Cancel" }).click();
  await dialog.waitFor({ state: "detached" });
  await pause(200);
  assert.deepStrictEqual(writes(from), [asked]);
  assert.strictEqual(await branchRow("main").locator(".pill-s.info").count(), 1);
  // Bring them with me.
  from = app.calls.length;
  await branchRow("spike/forecast").getByRole("button", { name: "Switch" }).click();
  dialog = page().getByRole("dialog", { name: "Switch to spike/forecast?" });
  await dialog.getByRole("radio", { name: /Bring them with me/ }).check();
  await dialog.getByRole("button", { name: "Switch", exact: true }).click();
  await branchRow("spike/forecast").locator(".pill-s.info").waitFor();
  assert.deepStrictEqual(writes(from), [asked, { method: "git_switch", params: { ...asked.params, mode: "carry" } }]);
  assert.strictEqual((await toastText()).split("\n")[0], "Now on spike/forecast.");
  await page().locator("#git-slot .git-pill[aria-label^='Branch spike/forecast in sales']").waitFor();
});

test("Switch to put changes aside sends stash, and the page shows the new branch and the changes put aside", { skip }, async () => {
  await open("branches");
  const from = app.calls.length;
  await branchRow("origin/fix/typo").getByRole("button", { name: "Check out" }).click();
  const dialog = page().getByRole("dialog", { name: "Switch to origin/fix/typo?" });
  await dialog.getByRole("button", { name: "Switch", exact: true }).click();
  await branchRow("fix/typo").locator(".pill-s.info").waitFor();
  assert.deepStrictEqual(writes(from), [{ method: "git_switch", params: { repo: REPO, name: "origin/fix/typo", mode: null } },
    { method: "git_switch", params: { repo: REPO, name: "origin/fix/typo", mode: "stash" } }]);
  assert.strictEqual((await toastText()).split("\n")[0], "Now on fix/typo. Your changes are put aside under Branches.");
  await stashRow("changes on main before switching to fix/typo").waitFor();
});

test("Bring back sends that stash, and it leaves the list", { skip }, async () => {
  await open("branches");
  const from = app.calls.length;
  await stashRow("before switching to report/q3-totals").getByRole("button", { name: "Bring back" }).click();
  await stashRow("before switching to report/q3-totals").waitFor({ state: "detached" });
  assert.deepStrictEqual(writes(from), [{ method: "git_restore_stash", params: { repo: REPO, ref: "stash@{1}" } }]);
  assert.strictEqual((await toastText()).split("\n")[0], "Your changes are back.");
  assert.strictEqual(await stashRow("before switching to main").count(), 1);
});

test("Take it off the list says the folder stays; Cancel sends nothing, and confirming sends that repository", { skip }, async () => {
  await open("settings");
  const off = page().locator(".git-settings").getByRole("button", { name: "Take it off the list" });
  let from = app.calls.length;
  await off.click();
  let dialog = page().getByRole("dialog", { name: "Take this repository off the list?" });
  await dialog.waitFor();
  assert.match(await dialog.innerText(), /The folder and its files stay exactly as they are; open it again any time\./);
  assert.deepStrictEqual(writes(from), []);
  await dialog.getByRole("button", { name: "Cancel" }).click();
  await dialog.waitFor({ state: "detached" });
  await pause(200);
  assert.deepStrictEqual(writes(from), []);
  from = app.calls.length;
  await off.click();
  dialog = page().getByRole("dialog", { name: "Take this repository off the list?" });
  await dialog.getByRole("button", { name: "Take off the list" }).click();
  // The other repository on the list opens in its place.
  await page().waitForFunction((other) => gitPage.repo === other, OTHER);
  assert.deepStrictEqual(writes(from), [{ method: "git_forget", params: { repo: REPO } }]);
  assert.deepStrictEqual(db.repos, [OTHER]);
});

// Refusals as the engine sends them: the plain words, without "GitError", and the page as it was.
const REFUSALS = [
  { name: "Create branch with a name already taken", method: "git_create_branch", tab: "branches",
    run: async () => {
      await page().getByRole("textbox", { name: "New branch name" }).fill("spike/forecast");
      await page().getByRole("button", { name: "Create branch" }).click();
    }, said: "No branch created: A branch called spike/forecast already exists." },
  { name: "Bring back over changes it would overwrite", method: "git_restore_stash", tab: "branches",
    error: "GitError: Git stopped so your uncommitted changes aren't overwritten. Commit them, or put them aside, and try again.",
    run: () => stashRow("before switching to main").getByRole("button", { name: "Bring back" }).click(),
    said: "Couldn't bring them back: Git stopped so your uncommitted changes aren't overwritten. Commit them, or put them aside, and try again." },
  { name: "Discard on a file Git can't change", method: "git_discard", tab: "changes",
    error: "GitError: Unable to unlink 'load.py': Permission denied",
    run: async () => {
      await fileRow("load.py").getByRole("button", { name: "Discard changes to load.py" }).click();
      await page().getByRole("dialog", { name: "Discard changes?" }).getByRole("button", { name: "Discard changes" }).click();
    }, said: "Not discarded: Unable to unlink 'load.py': Permission denied" },
  { name: "Delete anyway that Git refuses", method: "git_delete_branch", tab: "branches", afterFirst: true,
    error: "GitError: Couldn't lock ref 'refs/heads/spike/forecast': it is in use",
    run: async () => {
      await branchRow("spike/forecast").getByRole("button", { name: "Delete" }).click();
      await page().getByRole("dialog", { name: "Delete spike/forecast?" }).getByRole("button", { name: "Delete branch" }).click();
      const again = page().getByRole("dialog", { name: "spike/forecast has commits that aren't anywhere else" });
      await again.waitFor();
      fail.git_delete_branch = "GitError: Couldn't lock ref 'refs/heads/spike/forecast': it is in use";
      await again.getByRole("button", { name: "Delete anyway" }).click();
    }, said: "Not deleted: Couldn't lock ref 'refs/heads/spike/forecast': it is in use" },
];

for (const r of REFUSALS) {
  test(`${r.name}: a plain message, and the page stays as it was`, { skip }, async () => {
    if (r.error && !r.afterFirst) fail[r.method] = r.error;
    await open(r.tab);
    const was = JSON.stringify(db);
    const shown = () => page().locator(r.tab === "branches" ? ".git-branches" : ".git-changes").innerText();
    const before = await shown();
    await r.run();
    const said = (await toastText()).split("\n")[0];
    assert.strictEqual(said, r.said);
    assert.doesNotMatch(said, /GitError|Error:/);
    assert.strictEqual(JSON.stringify(db), was);
    await page().waitForFunction(() => !document.getElementById("dialog-root").childElementCount);
    await pause(200);
    assert.strictEqual(await shown(), before);
    assert.strictEqual(await page().evaluate(() => gitPage.status.branch), "main");
  });
}

test("opening Changes, a diff, Branches and Repository settings sends no write", { skip }, async () => {
  const from = app.calls.length;
  await open("changes");
  await fileRow("load.py").locator(".git-name").click();
  await page().locator("#git-diff tr.hunk").nth(1).waitFor();
  await page().getByRole("tab", { name: "Branches" }).click();
  await page().locator(".git-branches table").first().waitFor();
  await page().getByRole("tab", { name: "Repository settings" }).click();
  await page().locator(".git-settings").waitFor();
  await pause(300);
  const made = app.calls.slice(from).map((c) => c.method);
  for (const m of READS) assert.ok(made.includes(m), `${m} was read`);
  assert.deepStrictEqual(made.filter((m) => WRITES.includes(m)), []);
  assert.ok(app.calls.slice(from).every((c) => !c.params?.repo || c.params.repo === REPO), "a call named another repository");
});

test("axe finds nothing serious on each confirm", { skip: skip || (!axeSource && "axe-core is not installed") }, async () => {
  const check = async (where) => {
    await page().locator("#dialog-root .dialog").waitFor();
    assert.deepStrictEqual(await axeFindings(page(), "#dialog-root .dialog"), [], where);
    await page().evaluate(() => closeDialog());
  };
  await open("changes");
  for (const a of CHANGES) { await a.start(); await check(a.name); }
  await open("branches");
  await branchRow("report/q3-totals").getByRole("button", { name: "Delete" }).click();
  await check("Delete branch");
  await branchRow("spike/forecast").getByRole("button", { name: "Delete" }).click();
  await page().getByRole("dialog", { name: "Delete spike/forecast?" }).getByRole("button", { name: "Delete branch" }).click();
  await page().getByRole("dialog", { name: "spike/forecast has commits that aren't anywhere else" }).waitFor();
  await check("Delete anyway");
  await branchRow("spike/forecast").getByRole("button", { name: "Switch" }).click();
  await page().getByRole("dialog", { name: "Switch to spike/forecast?" }).waitFor();
  await check("Switch with uncommitted changes");
  await open("settings");
  await page().locator(".git-settings").getByRole("button", { name: "Take it off the list" }).click();
  await check("Take it off the list");
  db.merging = true; db.conflicts = [{ path: "load.py", state: "conflict", code: "C", how: "changed on both sides" }];
  await page().evaluate(() => { gitPage.status = null; gitPage.tab = "changes"; go("home"); });
  await open("changes");
  await page().locator(".git-page .git-warn").getByRole("button", { name: "Undo the pull" }).click();
  await check("Undo the pull");
});

test("no page errors", { skip }, () => assert.deepStrictEqual(app.errors, []));
