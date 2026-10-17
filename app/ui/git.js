// Source control: Git for a folder on this PC, in the app. Changes (stage, commit, diffs),
// Branches, History and Repository settings, plus the branch pill in the top bar and a branch
// chip on notebooks kept in a repository. Git itself does the work (the engine runs the
// person's own git); fetch, pull, push and clone talk to the remote and run in the background.
// Pushing to Git never updates a Fabric workspace; that is Fabric's own Git integration.

// ---------- pure helpers (app/tests/ui.test.cjs runs these) ----------

// One-letter marks, as VS Code shows them.
const GIT_MARK = { modified: "M", added: "A", deleted: "D", renamed: "R", copied: "C", "type changed": "T",
  untracked: "U", conflict: "!" };

function gitSplitPath(path) {
  const i = String(path).lastIndexOf("/");
  return i < 0 ? { name: path, dir: "" } : { name: path.slice(i + 1), dir: path.slice(0, i) };
}

// "main ↑2 ↓1" for the top bar.
function gitPillText(st) {
  if (!st) return "";
  const where = st.branch || (st.commit ? st.commit.slice(0, 7) : "No commits");
  const parts = [where];
  if (st.ahead) parts.push(`↑${st.ahead}`);
  if (st.behind) parts.push(`↓${st.behind}`);
  return parts.join(" ");
}

// Diff lines laid out side by side: removed lines on the left, added on the right, paired in order.
function gitSideBySide(hunks) {
  const rows = [];
  for (const hunk of hunks || []) {
    rows.push({ header: hunk.header });
    let del = [], add = [];
    const flush = () => {
      for (let i = 0; i < Math.max(del.length, add.length); i++) rows.push({ left: del[i] || null, right: add[i] || null });
      del = []; add = [];
    };
    for (const l of hunk.lines) {
      if (l.kind === "-") del.push(l);
      else if (l.kind === "+") add.push(l);
      else { flush(); rows.push({ left: l, right: l }); }
    }
    flush();
  }
  return rows;
}

// What the commit button says.
function gitCommitLabel(st) {
  if (!st) return "Commit";
  const n = st.staged.length;
  const where = st.branch ? ` to ${st.branch}` : "";
  if (st.conflicts.length) return "Fix the conflicts to commit";
  if (st.merging) return `Commit the merge${where}`;
  if (n) return `Commit ${n} staged file${n === 1 ? "" : "s"}${where}`;
  const all = st.unstaged.length + st.untracked.length;
  return all ? `Stage all and commit ${all} file${all === 1 ? "" : "s"}${where}` : "Nothing to commit";
}

// ---------- state ----------

const gitPage = {
  info: null, repo: null, status: null, tab: "changes",
  selected: null,          // {path, staged, untracked, conflict}
  diff: null, diffMode: "inline", notebookView: true,
  message: "", branches: null, log: null, logRef: null, detail: null, compare: [], compared: null,
  settings: null, op: null, opTimer: null, refreshTimer: null, loading: false,
};

function gitCurrent() {
  return gitPage.repo || gitPage.info?.current || null;
}

async function gitLoadInfo() {
  gitPage.info = await rpc("git_info");
  const known = gitPage.info.repos.filter((r) => r.exists);
  if (!gitPage.repo || !known.some((r) => r.path === gitPage.repo)) gitPage.repo = gitPage.info.current && known.some((r) => r.path === gitPage.info.current) ? gitPage.info.current : known[0]?.path || null;
  return gitPage.info;
}

async function gitRefresh({ quiet = false } = {}) {
  const repo = gitCurrent();
  if (!repo) { gitPage.status = null; renderGitPill(); return null; }
  const st = await rpc("git_status", { repo }).catch((err) => { if (!quiet) toast(friendlyError(err).text, true); return null; });
  gitPage.status = st;
  if (st?.running && !gitPage.op) gitWatch(st.running);
  renderGitPill();
  return st;
}

function gitRepoName(path) {
  return gitPage.info?.repos.find((r) => r.path === path)?.name || gitSplitPath(String(path).replace(/\\/g, "/")).name;
}

async function gitSwitchRepo(path) {
  Object.assign(gitPage, { repo: path, selected: null, diff: null, branches: null, log: null, detail: null, compare: [],
    compared: null, settings: null, message: "" });
  await attempt(() => rpc("git_open", { path }), "Couldn't open the repository");
  await gitLoadInfo().catch(() => {});
  render();
}

// ---------- the page ----------

async function renderSourceControl() {
  if (!gitPage.info) {
    setView(h("div", { class: "page" }, h("h1", {}, "Source control"), h("p", { class: "muted-text" }, "Loading…")));
    const ok = await attempt(gitLoadInfo, "Couldn't read Git on this PC");
    if (!ok || state.view !== "source") return;
  }
  const info = gitPage.info;
  const head = h("div", { class: "row" }, h("h1", {}, "Source control"), preview("sourceControl", h("span")), learnMore("source-control"));
  gitPage.title = () => [h("h1", {}, "Source control"), preview("sourceControl", h("span")), learnMore("source-control")];
  if (!info.installed) {
    setView(h("div", { class: "page narrow" }, head, h("div", { class: "empty" }, icon("branch", 32),
      h("div", { class: "title" }, "Git isn't installed on this PC"),
      h("div", { class: "text" }, "Source control uses Git for Windows. Install it from git-scm.com with Git Credential Manager (the default), then open this page again."),
      btn("Get Git for Windows", "open", () => openLink("https://git-scm.com/download/win"), "tool primary"),
      btn("Check again", "sync", async () => { gitPage.info = null; render(); }, "tool outline"))));
    return;
  }
  if (!gitCurrent()) {
    setView(h("div", { class: "page narrow" }, head,
      h("p", { class: "sub" }, "Keep notebooks, SQL and metadata projects in Git: see every change, work on branches, and push to GitHub, Azure DevOps or any Git server. Everything here runs with your own Git on this PC."),
      h("div", { class: "tiles" },
        tile("arrowDownload", "Clone a repository", "Copy a repository from GitHub, Azure DevOps or another Git server to this PC.", gitCloneDialog),
        tile("folderOpen", "Open a folder", "Use a repository that is already on this PC.", gitOpenDialog),
        tile("add", "Start a new repository", "Make a folder a new Git repository.", () => gitInitDialog()),
        tile("notebook", "Version my notebooks", "Start a repository in the folder this app keeps notebooks in.", () => gitInitDialog(info.notebooks_folder)))));
    return;
  }
  if (!gitPage.status || gitPage.status.root !== gitCurrent()) {
    setView(h("div", { class: "page" }, head, h("p", { class: "muted-text" }, "Reading the repository…")));
    await gitRefresh();
    if (state.view !== "source") return;
    if (!gitPage.status) {
      setView(h("div", { class: "page narrow" }, head, h("div", { class: "empty" },
        h("div", { class: "title" }, "This repository can't be read"),
        h("div", { class: "text" }, `${gitCurrent()} may have moved or been deleted.`),
        h("div", { class: "row" }, btn("Choose another", "folderOpen", (e) => gitRepoMenu(e.currentTarget), "tool outline"),
          btn("Take it off the list", "delete", async () => {
            await attempt(() => rpc("git_forget", { repo: gitCurrent() }), "Not removed");
            gitPage.repo = null; await gitLoadInfo(); render();
          }, "tool link")))));
      return;
    }
  }
  const st = gitPage.status;
  const counts = st.staged.length + st.unstaged.length + st.untracked.length + st.conflicts.length;
  const tab = (id, label) => h("button", { class: `tab${gitPage.tab === id ? " active" : ""}`, onclick: () => { gitPage.tab = id; render(); } }, label);
  const tabs = h("div", { class: "subtabs" }, tab("changes", counts ? `Changes ${counts}` : "Changes"), tab("branches", "Branches"),
    tab("history", "History"), tab("settings", "Repository settings"));
  const bodies = { changes: gitChangesTab, branches: gitBranchesTab, history: gitHistoryTab, settings: gitSettingsTab };
  setView(h("div", { class: "page git-page" }, gitHeader(st), gitOpBand(), gitMergeBanner(st), tabs,
    h("div", { class: "git-body" }, bodies[gitPage.tab](st))));
}

function gitHeader(st) {
  const remote = st.remotes[0];
  const label = st.links?.label || "the remote";
  const busy = !!gitPage.op;
  const sub = [st.root, remote ? `${remote.name}: ${remote.url}` : "no remote", st.summary,
    st.fetched_at ? `fetched ${relTime(st.fetched_at)}` : st.remotes.length ? "not fetched yet" : null].filter(Boolean).join(" · ");
  const links = st.links || {};
  const remoteActions = st.remotes.length ? [
    h("span", { class: "muted-text small", title: "These buttons send or receive commits over the network. Everything else on this page stays on this PC." }, `Talks to ${label}:`),
    btn("Fetch", "sync", () => gitRemoteOp("git_fetch", {}), "tool outline", { disabled: busy }),
    h("div", { class: "split" },
      btn(st.behind ? `Pull ${st.behind}` : "Pull", "arrowDownload", () => gitRemoteOp("git_pull", { mode: "ff-only" }), "tool outline",
        { disabled: busy || !st.upstream ? true : null, title: st.upstream ? "Bring in the remote's new commits (fast-forward only)" : "Push this branch once to link it to the remote" }),
      h("button", { class: "tool outline split-more", "aria-label": "Pull options", disabled: busy || !st.upstream ? true : null,
        onclick: (e) => showMenu(e.currentTarget, [
          { label: "Pull (fast-forward only)", icon: "arrowDownload", action: () => gitRemoteOp("git_pull", { mode: "ff-only" }) },
          { label: "Pull and merge", icon: "branchCompare", action: () => gitRemoteOp("git_pull", { mode: "merge" }) },
        ]) }, icon("chevronDown", 14))),
    btn(st.ahead ? `Push ${st.ahead}` : st.upstream ? "Push" : "Publish branch", "arrowUpload", () => gitRemoteOp("git_push", {}),
      st.ahead || (!st.upstream && st.commit) ? "tool primary" : "tool outline", { disabled: busy || !st.commit || !st.branch ? true : null,
        title: st.upstream ? `Send your commits to ${st.upstream}` : "Send this branch to the remote and link it" }),
    links.new_pull_request ? btn("Create pull request", "branchCompare", () => openLink(links.new_pull_request), "tool outline",
      { title: `Opens ${label} to create a pull request from ${st.branch}` }) : null,
    links.repository ? btn(`Open on ${label}`, "open", (e) => showMenu(e.currentTarget, [
      { label: "Repository", icon: "open", action: () => openLink(links.repository) },
      links.branch ? { label: `Branch ${st.branch}`, icon: "branch", action: () => openLink(links.branch) } : null,
      links.pull_requests ? { label: "Pull requests for this branch", icon: "branchCompare", action: () => openLink(links.pull_requests) } : null,
    ].filter(Boolean)), "tool outline") : null,
  ] : [h("span", { class: "muted-text small" }, "No remote yet."),
    btn("Add a remote", "add", () => { gitPage.tab = "settings"; render(); }, "tool outline")];
  return h("div", { class: "page-head" },
    h("div", { class: "grow" },
      h("div", { class: "row wrap" }, gitPage.title()[0], btn(gitRepoName(st.root), "chevronDown", (e) => gitRepoMenu(e.currentTarget), "tool outline", { title: "Switch repository" }),
        ...gitPage.title().slice(1)),
      h("p", { class: "sub git-sub" }, sub)),
    h("div", { class: "row wrap" }, remoteActions));
}

function gitRepoMenu(anchor) {
  const repos = (gitPage.info?.repos || []).filter((r) => r.exists);
  showMenu(anchor, [
    ...repos.map((r) => ({ label: r.path === gitCurrent() ? `${r.name} (open)` : r.name, icon: "branch", action: () => gitSwitchRepo(r.path) })),
    { label: "Clone a repository", icon: "arrowDownload", action: gitCloneDialog },
    { label: "Open a folder", icon: "folderOpen", action: gitOpenDialog },
    { label: "Start a new repository", icon: "add", action: () => gitInitDialog() },
  ]);
}

function gitMergeBanner(st) {
  if (!st.merging && !st.conflicts.length) return null;
  const n = st.conflicts.length;
  return h("div", { class: "callout git-warn" }, icon("warning", 20),
    h("span", {}, n
      ? `The merge stopped: ${n} file${n === 1 ? "" : "s"} changed both here and on the remote. Open each one, keep the lines you want (between the <<<<<<< and >>>>>>> marks), stage it, then commit. Or undo the pull.`
      : "All conflicts are fixed. Commit to finish the merge."),
    h("span", { class: "spacer" }),
    btn("Undo the pull", "undo", async () => {
      if (!(await confirmDialog("Undo the pull?", "Every file goes back to how it was before the pull, including the conflicts you fixed. Your own commits are kept.", "Undo the pull"))) return;
      if (await attempt(() => rpc("git_abort_merge", { repo: gitCurrent(), confirm: true }), "Couldn't undo the merge")) { toast("The pull was undone."); gitAfterChange(); }
    }, "tool outline"));
}

// ---------- background work: fetch, pull, push, clone ----------

const GIT_OP_LABEL = { fetch: "Fetching", pull: "Pulling", push: "Pushing", clone: "Cloning" };
const GIT_OP_DONE = { fetch: "Fetched", pull: "Pulled", push: "Pushed", clone: "Cloned" };

async function gitRemoteOp(method, params) {
  const op = await attempt(() => rpc(method, { repo: gitCurrent(), ...params }), "Couldn't start");
  if (op) gitWatch(op);
}

function gitWatch(op) {
  gitPage.op = op;
  logActivity({ id: `git-${op.id}`, status: "running", title: `${GIT_OP_LABEL[op.kind]} ${gitRepoName(op.repo)}`, text: "Talking to the remote" });
  if (state.view === "source") render();
  clearTimeout(gitPage.opTimer);
  const tick = async () => {
    const now = await rpc("git_op_status", { id: op.id }).catch(() => null);
    if (!now) { gitPage.op = null; return; }
    gitPage.op = now;
    if (now.status === "running") {
      const band = document.getElementById("git-op");
      if (band) band.replaceWith(gitOpBand());
      gitPage.opTimer = setTimeout(tick, 500);
      return;
    }
    gitPage.op = null;
    const title = `${now.status === "ok" ? GIT_OP_DONE[now.kind] : GIT_OP_LABEL[now.kind]} ${gitRepoName(now.repo)}`;
    logActivity({ id: `git-${now.id}`, status: now.status === "ok" ? "ok" : now.status === "cancelled" ? "info" : "failed",
      title, text: now.message, details: now.details });
    if (now.status === "ok") toast(now.kind === "clone" ? `Cloned into ${now.result?.root || now.repo}.` : now.message);
    else if (now.status === "failed") toast(now.message, true, { quiet: true, details: now.details });
    if (now.kind === "clone" && now.status === "ok") {
      await gitLoadInfo().catch(() => {});
      gitPage.repo = now.result?.root || gitPage.repo;
      gitPage.status = null;
    } else {
      await gitRefresh({ quiet: true });
    }
    gitPage.branches = null; gitPage.log = null;
    if (state.view === "source") render();
  };
  gitPage.opTimer = setTimeout(tick, 400);
}

function gitOpBand() {
  const op = gitPage.op;
  if (!op || op.status !== "running") return h("div", { id: "git-op" });
  const pct = op.percent;
  return h("div", { id: "git-op", class: "card pad git-op" },
    h("div", { class: "row" }, h("b", {}, `${GIT_OP_LABEL[op.kind]}…`), h("span", { class: "muted-text" }, op.phase && op.phase !== "Starting" ? `${op.phase}${pct !== null ? ` ${pct}%` : ""}` : "Talking to the remote"),
      h("span", { class: "spacer" }), btn("Cancel", "dismiss", () => attempt(() => rpc("git_op_cancel", { id: op.id }), "Couldn't cancel"), "tool link small")),
    h("div", { class: "progress" }, h("div", { class: `bar${pct === null ? " indeterminate" : ""}`, style: pct === null ? "" : `width:${pct}%` })),
    h("div", { class: "muted-text small" }, "If Git needs you to sign in, Git Credential Manager opens its own window."));
}

// ---------- Changes ----------

function gitChangesTab(st) {
  const repo = gitCurrent();
  const fileRow = (e, group) => {
    const { name, dir } = gitSplitPath(e.path);
    const sel = gitPage.selected?.path === e.path && gitPage.selected.group === group;
    const actions = [];
    if (group === "staged") actions.push(h("button", { class: "tool small", title: "Unstage", "aria-label": `Unstage ${e.path}`, onclick: (ev) => { ev.stopPropagation(); gitStage([e.path], false); } }, icon("subtract", 14)));
    else {
      if (group !== "conflicts") actions.push(h("button", { class: "tool small", title: "Discard changes", "aria-label": `Discard changes to ${e.path}`, onclick: (ev) => { ev.stopPropagation(); gitDiscard([e.path], e.state === "untracked"); } }, icon("undo", 14)));
      actions.push(h("button", { class: "tool small", title: group === "conflicts" ? "Mark as fixed (stage)" : "Stage", "aria-label": `Stage ${e.path}`, onclick: (ev) => { ev.stopPropagation(); gitStage([e.path], true); } }, icon("add", 14)));
    }
    return h("div", { class: `git-file${sel ? " on" : ""}`, role: "button", tabindex: "0", title: e.from ? `${e.from} → ${e.path}` : e.path,
      onclick: () => gitSelect(e.path, group), onkeydown: (ev) => { if (ev.key === "Enter") gitSelect(e.path, group); } },
      h("span", { class: `git-name${e.state === "deleted" ? " gone" : ""}` }, name, dir ? h("span", { class: "muted-text small" }, ` ${dir}`) : null),
      h("b", { class: `git-mark ${e.state.replace(" ", "-")}`, title: e.how || e.state }, GIT_MARK[e.state] || "M"), h("span", { class: "git-actions" }, actions));
  };
  const group = (title, entries, key, action) => entries.length ? [
    h("div", { class: "git-group" }, h("span", {}, `${title} ${entries.length}`), action),
    ...entries.map((e) => fileRow(e, key))] : [];
  const unstaged = st.unstaged.concat(st.untracked);
  const msg = h("textarea", { id: "git-message", class: "git-message", rows: "3", placeholder: "Say what changed and why",
    "aria-label": "Commit message", oninput: (e) => { gitPage.message = e.target.value; } });
  msg.value = gitPage.message;
  msg.addEventListener("keydown", (e) => { if (e.key === "Enter" && e.ctrlKey) { e.preventDefault(); gitCommit(); } });
  const nothing = !st.staged.length && !unstaged.length && !st.merging;
  const left = h("section", { class: "card pad git-changes" },
    h("label", { class: "field" }, "Commit message", msg),
    btn(gitCommitLabel(st), "ok", gitCommit, "tool primary", { disabled: nothing || st.conflicts.length ? true : null }),
    h("div", { class: "muted-text small" }, "Commits stay on this PC until you push. Staged files are checked for secrets first."),
    ...group("Merge conflicts", st.conflicts, "conflicts", null),
    ...group("Staged changes", st.staged, "staged", btn("Unstage all", null, () => gitUnstageAll(), "tool link small")),
    ...group("Changes", unstaged, "unstaged", btn("Stage all", null, () => gitStageAll(), "tool link small")),
    nothing ? h("p", { class: "muted-text" }, "No changes. Edit files in this folder and they show up here.") : null,
    h("div", { class: "spacer" }),
    h("div", { class: "muted-text small" }, "M changed · A added · D deleted · U new, not in Git yet · ! conflict"));
  return h("div", { class: "git-split" }, left, gitDiffPanel());
}

async function gitSelect(path, group) {
  gitPage.selected = { path, group };
  gitPage.diff = null;
  render();
  await gitLoadDiff();
}

async function gitLoadDiff() {
  const sel = gitPage.selected;
  if (!sel) return;
  const params = { repo: gitCurrent(), path: sel.path, staged: sel.group === "staged", notebook: gitPage.notebookView,
    ...(sel.base ? { base: sel.base, target: sel.target } : {}) };
  const d = await attempt(() => rpc("git_diff", params), "Couldn't show the changes");
  if (gitPage.selected !== sel) return;
  gitPage.diff = d || { error: true };
  const panel = document.getElementById("git-diff");
  if (panel) panel.replaceWith(gitDiffPanel());
}

function gitDiffPanel() {
  const sel = gitPage.selected;
  if (!sel) return h("section", { id: "git-diff", class: "card git-diff empty" }, h("div", { class: "text" }, "Choose a file to see what changed, line by line."));
  const d = gitPage.diff;
  const isNb = /\.ipynb$/i.test(sel.path);
  const seg = h("div", { class: "segs", role: "group", "aria-label": "Diff layout" },
    ...[["inline", "Inline"], ["split", "Side by side"]].map(([id, label]) => h("button", { class: `seg${gitPage.diffMode === id ? " on" : ""}`,
      onclick: () => { gitPage.diffMode = id; document.getElementById("git-diff")?.replaceWith(gitDiffPanel()); } }, label)));
  const where = sel.base ? `${sel.base.slice(0, 7)} → ${sel.target.slice(0, 7)}` : sel.group === "staged" ? "Staged" : sel.group === "conflicts" ? "Conflict" : "Not staged";
  const headTools = [
    isNb ? h("label", { class: "field inline small" }, h("input", { type: "checkbox", checked: gitPage.notebookView ? true : null,
      onchange: (e) => { gitPage.notebookView = e.target.checked; gitPage.diff = null; document.getElementById("git-diff")?.replaceWith(gitDiffPanel()); gitLoadDiff(); } }),
    "Notebook view (code only)") : null,
    d?.view === "conflict" ? null : seg,
    gitPage.status?.commit && sel.group !== "untracked" ? btn("Open previous version", "history", () => gitShowVersion(sel.base ? sel.base : "HEAD", sel.path), "tool outline small") : null,
  ];
  const head = h("div", { class: "git-diff-head" }, h("b", {}, sel.path), h("span", { class: "muted-text small" },
    [where, d && !d.error && !d.binary && d.view !== "conflict" ? `+${d.added} −${d.removed}` : ""].filter(Boolean).join(" · ")), h("span", { class: "spacer" }), headTools);
  let body;
  if (!d) body = h("p", { class: "muted-text pad" }, "Loading…");
  else if (d.error) body = h("p", { class: "muted-text pad" }, "The changes couldn't be shown.");
  else if (d.binary) body = h("p", { class: "muted-text pad" }, "This file isn't text, so there are no lines to compare.");
  else if (d.view === "conflict") body = gitConflictTable(d);
  else if (!d.hunks.length) body = h("p", { class: "muted-text pad" }, d.view === "notebook" ? "No code changed. Only outputs or run counts differ; turn off Notebook view to see them." : "No line changes.");
  else body = gitPage.diffMode === "split" ? gitSplitTable(d.hunks) : gitInlineTable(d.hunks);
  return h("section", { id: "git-diff", class: "card git-diff" }, head,
    h("div", { class: "git-diff-body" }, body, d?.truncated ? h("p", { class: "muted-text pad" }, "Only the first 5,000 lines are shown.") : null));
}

// A file with conflicts as it is now: this branch's lines, the remote's, and the marks between them.
function gitConflictTable(d) {
  return h("div", {},
    h("p", { class: "muted-text small pad" }, `Edit the file in your editor: keep the lines you want from each side, delete the <<<<<<<, ======= and >>>>>>> lines, save, then press + to stage it. ${d.conflicts} conflict${d.conflicts === 1 ? "" : "s"} left.`),
    h("table", { class: "git-lines" }, h("tbody", {}, d.hunks.flatMap((hk) => hk.lines.map((l) => h("tr", { class: l.side ? `cf-${l.side}` : "" },
      h("td", { class: "ln" }, l.new), h("td", { class: "sign" }, l.side === "here" ? "here" : l.side === "remote" ? "remote" : ""),
      h("td", { class: "code", colspan: "2" }, l.text)))))));
}

function gitInlineTable(hunks) {
  return h("table", { class: "git-lines" }, h("tbody", {}, hunks.flatMap((hk) => [
    h("tr", { class: "hunk" }, h("td", { colspan: "4" }, hk.header)),
    ...hk.lines.map((l) => h("tr", { class: l.kind === "+" ? "add" : l.kind === "-" ? "del" : "" },
      h("td", { class: "ln" }, l.old ?? ""), h("td", { class: "ln" }, l.new ?? ""), h("td", { class: "sign" }, l.kind === " " ? "" : l.kind === "-" ? "−" : "+"),
      h("td", { class: "code" }, l.text)))])));
}

function gitSplitTable(hunks) {
  const cell = (l, side) => l ? [h("td", { class: "ln" }, side === "l" ? l.old ?? "" : l.new ?? ""),
    h("td", { class: `code ${l.kind === "+" ? "add" : l.kind === "-" ? "del" : ""}` }, l.text)] : [h("td", { class: "ln" }), h("td", { class: "code blank" })];
  const cols = h("colgroup", {}, h("col", { class: "ln" }), h("col"), h("col", { class: "ln" }), h("col"));
  return h("table", { class: "git-lines split" }, cols, h("tbody", {}, gitSideBySide(hunks).map((r) => r.header
    ? h("tr", { class: "hunk" }, h("td", { colspan: "4" }, r.header))
    : h("tr", {}, ...cell(r.left?.kind === "+" ? null : r.left, "l"), ...cell(r.right?.kind === "-" ? null : r.right, "r")))));
}

async function gitShowVersion(rev, path) {
  const f = await attempt(() => rpc("git_file_at", { repo: gitCurrent(), commit: rev, path }), "Couldn't open that version");
  if (!f) return;
  showDialog(`${path} at ${f.commit.slice(0, 7)} (read-only)`, h("div", { class: "git-version" },
    f.binary ? h("p", { class: "muted-text" }, "This file isn't text.") : h("pre", { class: "git-pre" }, f.text),
    f.truncated ? h("p", { class: "muted-text small" }, "Only the first 5 MB are shown.") : null),
  [btn("Copy", null, () => copyText(f.text || "", "The file"), "tool outline"), btn("Close", null, closeDialog, "tool primary")]);
}

async function gitAfterChange(st) {
  if (st && st.root) { gitPage.status = st; renderGitPill(); } else await gitRefresh();
  if (gitPage.selected) {
    const s = gitPage.status;
    const groups = { staged: s.staged, unstaged: s.unstaged.concat(s.untracked), conflicts: s.conflicts, untracked: s.untracked };
    const still = gitPage.selected.base || (groups[gitPage.selected.group] || []).some((e) => e.path === gitPage.selected.path);
    if (!still) {
      const moved = Object.entries(groups).find(([, list]) => list.some((e) => e.path === gitPage.selected.path));
      gitPage.selected = moved ? { path: gitPage.selected.path, group: moved[0] === "untracked" ? "unstaged" : moved[0] } : null;
    }
    gitPage.diff = null;
  }
  if (state.view === "source") { render(); gitLoadDiff(); }
}

async function gitStage(paths, on) {
  const st = await attempt(() => rpc(on ? "git_stage" : "git_unstage", { repo: gitCurrent(), paths }), on ? "Not staged" : "Not unstaged");
  if (st) gitAfterChange(st);
}

async function gitStageAll() {
  const st = await attempt(() => rpc("git_stage_all", { repo: gitCurrent() }), "Not staged");
  if (st) gitAfterChange(st);
}

async function gitUnstageAll() {
  const st = await attempt(() => rpc("git_unstage", { repo: gitCurrent() }), "Not unstaged");
  if (st) gitAfterChange(st);
}

async function gitDiscard(paths, untracked) {
  const what = paths.length === 1 ? paths[0] : `${paths.length} files`;
  const text = untracked
    ? `${what} isn't in Git yet, so discarding deletes it from this PC. This can't be undone.`
    : `Your changes to ${what} since it was last staged or committed are thrown away. This can't be undone.`;
  if (!(await confirmDialog(untracked ? "Delete this file?" : "Discard changes?", text, untracked ? "Delete file" : "Discard changes"))) return;
  const st = await attempt(() => rpc("git_discard", { repo: gitCurrent(), paths, confirm: true }), "Not discarded");
  if (st) gitAfterChange(st);
}

async function gitCommit(allowSecrets = false) {
  const st = gitPage.status;
  const message = (document.getElementById("git-message")?.value ?? gitPage.message).trim();
  if (!message) { toast("Write a commit message first: say what changed and why.", true, { quiet: true }); document.getElementById("git-message")?.focus(); return; }
  if (!st.staged.length && !st.merging) {
    const staged = await attempt(() => rpc("git_stage_all", { repo: gitCurrent() }), "Not staged");
    if (!staged) return;
    gitPage.status = staged;
  }
  const res = await attempt(() => rpc("git_commit", { repo: gitCurrent(), message, allow_secrets: allowSecrets === true }), "Not committed");
  if (!res) return;
  if (res.blocked === "secrets") { gitSecretsDialog(res.findings); return; }
  gitPage.message = "";
  gitPage.log = null;
  toast(`Committed ${res.commit.slice(0, 7)} to ${res.branch || "this repository"}. Push to send it to the remote.`);
  gitAfterChange(res.status);
}

function gitSecretsDialog(findings) {
  showDialog("These files look like they hold a secret", h("div", { class: "git-secrets" },
    h("p", { class: "text" }, "Once a secret is committed and pushed, everyone who can read the repository can read it, and it stays in the history. Move it to Key Vault or a variable library, then stage the file again."),
    h("table", { class: "mini" }, h("tbody", {}, findings.slice(0, 30).map((f) => h("tr", {},
      h("td", {}, h("b", {}, f.path), h("div", { class: "muted-text small" }, f.where || (f.line ? `line ${f.line}` : ""))),
      h("td", {}, f.kind, h("div", { class: "mono small" }, f.match)),
      h("td", { class: "muted-text small" }, f.remediation))))),
    findings.length > 30 ? h("p", { class: "muted-text small" }, `And ${findings.length - 30} more.`) : null),
  [btn("It isn't a secret, commit", null, () => { closeDialog(); gitCommit(true); }, "tool outline"),
    btn("Go back and fix it", null, closeDialog, "tool primary")]);
}

// ---------- Branches ----------

function gitBranchesTab(st) {
  if (!gitPage.branches) {
    rpc("git_branches", { repo: gitCurrent() }).then((b) => { gitPage.branches = b; if (state.view === "source" && gitPage.tab === "branches") render(); })
      .catch((err) => toast(friendlyError(err).text, true));
    return h("p", { class: "muted-text" }, "Reading branches…");
  }
  const b = gitPage.branches;
  const input = h("input", { placeholder: "feature/what-you-are-doing", "aria-label": "New branch name" });
  const create = async () => {
    const name = input.value.trim();
    if (!name) { input.focus(); return; }
    if (st.dirty) toast("Your uncommitted changes come with you to the new branch.", false);
    const res = await attempt(() => rpc("git_create_branch", { repo: gitCurrent(), name }), "No branch created");
    if (res) { gitPage.branches = null; toast(`Now on ${name}.`); gitAfterChange(); }
  };
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") create(); });
  const local = b.local.map((x) => h("tr", { class: x.current ? "current" : "" },
    h("td", {}, h("b", {}, x.name), x.current ? h("span", { class: "pill-s info" }, " current") : null, x.gone ? h("span", { class: "pill-s wrn", title: "Its remote branch was deleted (for example after its pull request merged)" }, "remote gone") : null),
    h("td", { class: "muted-text small" }, [x.upstream ? `${x.upstream}` : "only on this PC", x.ahead ? `↑${x.ahead}` : "", x.behind ? `↓${x.behind}` : ""].filter(Boolean).join(" ")),
    h("td", { class: "muted-text small" }, x.subject, " · ", when(x.date)),
    h("td", { class: "n" }, x.current ? null : [btn("Switch", "branch", () => gitSwitchBranch(x.name), "tool outline small"),
      btn("Delete", "delete", () => gitDeleteBranch(x.name), "tool link small danger")])));
  const remote = b.remote.filter((r) => !r.tracked).map((r) => h("tr", {},
    h("td", {}, r.name), h("td", { class: "muted-text small" }, r.subject, " · ", when(r.date)),
    h("td", { class: "n" }, btn("Check out", "arrowDownload", () => gitSwitchBranch(r.name), "tool outline small"))));
  return h("div", { class: "git-branches" },
    h("section", { class: "card pad" }, h("h2", {}, "New branch"),
      h("div", { class: "row" }, input, btn("Create branch", "branch", create, "tool primary")),
      h("p", { class: "muted-text small" }, `Starts from ${st.branch || "the current commit"} and switches to it. Work on a branch, then open a pull request to merge it.`)),
    h("section", { class: "card pad" }, h("h2", {}, "On this PC"), h("table", { class: "mini" }, h("tbody", {}, local))),
    h("section", { class: "card pad" }, h("h2", {}, "On the remote"),
      h("p", { class: "muted-text small" }, st.fetched_at ? `As of the last fetch, ${relTime(st.fetched_at)}.` : "Fetch to see the remote's branches."),
      remote.length ? h("table", { class: "mini" }, h("tbody", {}, remote)) : h("p", { class: "muted-text" }, "No other branches.")),
    b.stashes.length ? h("section", { class: "card pad" }, h("h2", {}, "Changes put aside"),
      h("table", { class: "mini" }, h("tbody", {}, b.stashes.map((s) => h("tr", {}, h("td", {}, s.message), h("td", { class: "muted-text small" }, when(s.date)),
        h("td", { class: "n" }, btn("Bring back", "undo", async () => {
          const res = await attempt(() => rpc("git_restore_stash", { repo: gitCurrent(), ref: s.ref }), "Couldn't bring them back");
          if (res) { gitPage.branches = null; toast("Your changes are back."); gitAfterChange(res); }
        }, "tool outline small"))))))) : null);
}

async function gitSwitchBranch(name, mode = null) {
  const res = await attempt(() => rpc("git_switch", { repo: gitCurrent(), name, mode }), "Couldn't switch");
  if (!res) return;
  if (res.needs_choice) {
    let choice = "stash";
    const radio = (value, title, text) => h("label", { class: "git-choice" }, h("input", { type: "radio", name: "git-mode", value, checked: value === choice ? true : null, onchange: () => { choice = value; } }),
      h("span", {}, h("b", {}, title), " ", text));
    showDialog(`Switch to ${name}?`, h("div", { class: "git-switch" },
      h("p", { class: "text" }, `${res.message} Nothing is thrown away.`),
      h("pre", { class: "git-pre small" }, res.changes.slice(0, 12).map((c) => `${GIT_MARK[c.state] || "M"} ${c.path}`).join("\n") + (res.changes.length > 12 ? `\n…and ${res.changes.length - 12} more` : "")),
      radio("stash", "Put them aside.", "They're saved on this PC; bring them back from Branches > Changes put aside."),
      radio("carry", "Bring them with me.", "Git refuses if a changed file is different on the other branch, and tells you which.")),
    [btn("Cancel", null, closeDialog, "tool outline"), btn("Switch", null, () => { closeDialog(); gitSwitchBranch(name, choice); }, "tool primary")]);
    return;
  }
  gitPage.branches = null; gitPage.log = null; gitPage.selected = null;
  toast(res.stashed ? `Now on ${res.branch}. Your changes are put aside under Branches.` : `Now on ${res.branch}.`);
  gitAfterChange(res.status);
}

async function gitDeleteBranch(name) {
  if (!(await confirmDialog(`Delete ${name}?`, "The branch is deleted on this PC. A copy on the remote stays; delete that on the provider's site.", "Delete branch"))) return;
  try {
    await rpc("git_delete_branch", { repo: gitCurrent(), name, confirm: true });
  } catch (err) {
    const f = friendlyError(err);
    if (!/commits no other branch holds/.test(f.text)) { toast(f.text, true, { details: f.details }); return; }
    if (!(await confirmDialog(`${name} has commits that aren't anywhere else`, "Deleting it loses those commits unless they were pushed. Delete it anyway?", "Delete anyway"))) return;
    if (!(await attempt(() => rpc("git_delete_branch", { repo: gitCurrent(), name, confirm: true, force: true }), "Not deleted"))) return;
  }
  gitPage.branches = null;
  toast(`Deleted ${name}.`);
  render();
}

// ---------- History ----------

function gitHistoryTab(st) {
  if (!st.commit) return h("p", { class: "muted-text" }, "Nothing is committed yet.");
  if (!gitPage.log) {
    rpc("git_log", { repo: gitCurrent(), limit: 50 }).then((l) => { gitPage.log = l; if (state.view === "source" && gitPage.tab === "history") render(); })
      .catch((err) => toast(friendlyError(err).text, true));
    return h("p", { class: "muted-text" }, "Reading history…");
  }
  const log = gitPage.log;
  const pick = (c, on) => {
    gitPage.compare = on ? [...gitPage.compare.filter((x) => x !== c.commit), c.commit].slice(-2) : gitPage.compare.filter((x) => x !== c.commit);
    render();
  };
  const rows = log.commits.map((c) => h("tr", { class: `click${gitPage.detail?.commit === c.commit ? " on" : ""}`, onclick: () => gitOpenCommit(c.commit) },
    h("td", { onclick: (e) => e.stopPropagation() }, h("input", { type: "checkbox", "aria-label": `Compare ${c.short}`, checked: gitPage.compare.includes(c.commit) ? true : null,
      onchange: (e) => pick(c, e.target.checked) })),
    h("td", {}, h("b", {}, c.subject), c.refs.length ? h("div", {}, c.refs.map((r) => h("span", { class: "pill-s neu" }, r.replace("HEAD -> ", "")))) : null),
    h("td", { class: "muted-text small" }, c.author), h("td", { class: "muted-text small nowrap" }, when(c.date)), h("td", { class: "mono small" }, c.short)));
  const compareBar = gitPage.compare.length === 2 ? h("div", { class: "row" },
    btn("Compare the two commits", "branchCompare", gitCompare, "tool primary small"), btn("Clear", null, () => { gitPage.compare = []; gitPage.compared = null; render(); }, "tool link small"))
    : h("span", { class: "muted-text small" }, "Tick two commits to compare them.");
  const more = log.more ? btn("Show more", "down", async () => {
    const next = await attempt(() => rpc("git_log", { repo: gitCurrent(), limit: 50, skip: log.commits.length }), "Couldn't read more history");
    if (next) { gitPage.log = { commits: log.commits.concat(next.commits), more: next.more }; render(); }
  }, "tool link") : null;
  return h("div", { class: "git-split" },
    h("section", { class: "card pad git-history" }, compareBar, h("table", { class: "mini" }, h("tbody", {}, rows)), more),
    gitPage.compared ? gitCompareView() : gitPage.detail ? gitCommitView() : h("section", { class: "card git-diff empty" }, h("div", { class: "text" }, "Choose a commit to see what it changed.")));
}

async function gitOpenCommit(commit) {
  const d = await attempt(() => rpc("git_commit_detail", { repo: gitCurrent(), commit }), "Couldn't open the commit");
  if (!d) return;
  gitPage.detail = d; gitPage.compared = null; gitPage.selected = null;
  render();
}

function gitFileList(files, base, target) {
  return h("div", {}, files.map((f) => h("div", { class: `git-file${gitPage.selected?.path === f.path && gitPage.selected.target === target ? " on" : ""}`, role: "button", tabindex: "0",
    onclick: () => { gitPage.selected = { path: f.path, group: "commit", base, target }; gitPage.diff = null; render(); gitLoadDiff(); } },
    h("span", { class: "git-name" }, f.path), h("b", { class: `git-mark ${f.state.replace(" ", "-")}` }, GIT_MARK[f.state] || "M"),
    h("span", { class: "git-actions" }, f.state !== "deleted" ? h("button", { class: "tool small", title: "Open this version (read-only)", "aria-label": `Open ${f.path} at this commit`,
      onclick: (e) => { e.stopPropagation(); gitShowVersion(target, f.path); } }, icon("open", 14)) : null))));
}

function gitCommitView() {
  const d = gitPage.detail;
  const links = gitPage.status?.links;
  return h("section", { class: "card pad git-commit" },
    h("div", { class: "row" }, h("h2", {}, d.subject), h("span", { class: "spacer" }),
      links?.repository ? btn(`Open on ${links.label}`, "open", () => openLink(`${links.repository}${links.label === "GitLab" ? "/-" : ""}/commit${links.label === "Bitbucket" ? "s" : ""}/${d.commit}`), "tool link small") : null),
    h("div", { class: "muted-text small" }, `${d.author} <${d.email}> · `, when(d.date), ` · ${d.commit}`),
    d.body ? h("pre", { class: "git-pre small" }, d.body) : null,
    h("div", { class: "git-group" }, `${d.files.length} file${d.files.length === 1 ? "" : "s"} changed`),
    gitFileList(d.files, d.base, d.commit),
    gitPage.selected?.target === d.commit ? gitDiffPanel() : null);
}

async function gitCompare() {
  const [a, b] = gitPage.compare;
  const order = gitPage.log.commits.map((c) => c.commit);
  const [base, target] = order.indexOf(a) > order.indexOf(b) ? [a, b] : [b, a];  // older first
  const res = await attempt(() => rpc("git_compare", { repo: gitCurrent(), base, target }), "Couldn't compare");
  if (res) { gitPage.compared = res; gitPage.detail = null; gitPage.selected = null; render(); }
}

function gitCompareView() {
  const c = gitPage.compared;
  return h("section", { class: "card pad git-commit" },
    h("h2", {}, `${c.base.slice(0, 7)} → ${c.target.slice(0, 7)}`),
    h("div", { class: "muted-text small" }, `${c.files.length} file${c.files.length === 1 ? "" : "s"} differ · ${c.only_in_target} commit${c.only_in_target === 1 ? "" : "s"} in between`),
    gitFileList(c.files, c.base, c.target),
    gitPage.selected?.target === c.target ? gitDiffPanel() : null);
}

// ---------- Repository settings ----------

function gitSettingsTab(st) {
  if (!gitPage.settings) {
    rpc("git_settings", { repo: gitCurrent() }).then((s) => { gitPage.settings = s; if (state.view === "source" && gitPage.tab === "settings") render(); })
      .catch((err) => toast(friendlyError(err).text, true));
    return h("p", { class: "muted-text" }, "Reading settings…");
  }
  const s = gitPage.settings;
  const name = h("input", { value: s.identity.name || "", "aria-label": "Name" });
  const email = h("input", { value: s.identity.email || "", type: "email", "aria-label": "Email" });
  const url = h("input", { placeholder: "https://github.com/org/repo.git or git@github.com:org/repo.git", "aria-label": "Remote address" });
  return h("div", { class: "git-settings" },
    h("section", { class: "card pad" }, h("h2", {}, "Your name on commits"),
      h("p", { class: "muted-text small" }, s.identity.local_name ? "Set for this repository only." : "Comes from your Git settings for every repository. Saving here sets it for this repository only."),
      h("div", { class: "row wrap" }, h("label", { class: "field" }, "Name", name), h("label", { class: "field" }, "Email", email)),
      h("div", { class: "row" }, btn("Save for this repository", "save", async () => {
        const res = await attempt(() => rpc("git_set_identity", { repo: gitCurrent(), name: name.value, email: email.value }), "Not saved");
        if (res) { gitPage.settings = null; toast("Saved. New commits use this name and email."); render(); }
      }, "tool primary"))),
    h("section", { class: "card pad" }, h("h2", {}, "Remotes"),
      s.remotes.length ? h("table", { class: "mini" }, h("tbody", {}, s.remotes.map((r) => h("tr", {}, h("td", {}, h("b", {}, r.name)), h("td", { class: "mono small" }, r.url),
        h("td", {}, r.provider === "git" ? "Git" : { github: "GitHub", azure_devops: "Azure DevOps", gitlab: "GitLab", bitbucket: "Bitbucket" }[r.provider]))))) : h("p", { class: "muted-text" }, "None yet. Add one to push this repository."),
      s.remotes.some((r) => r.name === "origin") ? null : h("div", { class: "row" }, url, btn("Add as origin", "add", async () => {
        const res = await attempt(() => rpc("git_add_remote", { repo: gitCurrent(), url: url.value }), "Not added");
        if (res) { gitPage.settings = null; await gitRefresh(); render(); }
      }, "tool primary")),
      h("p", { class: "muted-text small" }, "Use the address your provider shows under Clone. Don't put a password or token in it: Git asks through Git Credential Manager, which keeps your sign-in in Windows Credential Manager. For SSH addresses, your key must be loaded in the SSH agent.")),
    h("section", { class: "card pad" }, h("h2", {}, "Sign-in and Git"),
      h("div", { class: "muted-text small" }, `Git ${s.git_version || "(version unknown)"} · credential helper: ${s.credential_helpers.join(", ") || "none"}${s.ssh_command ? " · custom SSH command" : ""}`),
      s.notes.map((n) => h("div", { class: "callout git-warn small" }, icon("warning", 16), n))),
    h("section", { class: "card pad" }, h("h2", {}, "This repository on this PC"),
      h("div", { class: "mono small" }, s.root),
      h("div", { class: "row" }, btn("Take it off the list", "dismiss", async () => {
        if (!(await confirmDialog("Take this repository off the list?", "The folder and its files stay exactly as they are; open it again any time.", "Take off the list"))) return;
        await attempt(() => rpc("git_forget", { repo: gitCurrent() }), "Not removed");
        gitPage.repo = null; gitPage.status = null; await gitLoadInfo(); render();
      }, "tool outline"))));
}

// ---------- open, clone, init ----------

function gitPathDialog(title, intro, fields, okLabel, run) {
  const inputs = fields.map((f) => h("input", { value: f.value || "", placeholder: f.placeholder || "" }));
  const err = h("p", { class: "field-error" });
  const ok = async () => {
    err.textContent = "";
    try { await run(...inputs.map((i) => i.value.trim())); closeDialog(); } catch (e) { err.textContent = friendlyError(e).text; }
  };
  inputs.forEach((i) => i.addEventListener("keydown", (e) => { if (e.key === "Enter") ok(); }));
  showDialog(title, h("div", { class: "git-form" }, h("p", { class: "text" }, intro),
    fields.map((f, i) => h("label", { class: "field" }, f.label, inputs[i])), err),
  [btn("Cancel", null, closeDialog, "tool outline"), btn(okLabel, null, ok, "tool primary")]);
}

function gitOpenDialog() {
  gitPathDialog("Open a repository", "The folder of a Git repository on this PC (any folder inside it works).",
    [{ label: "Folder", placeholder: "C:\\Users\\you\\source\\repos\\project" }], "Open", async (path) => {
      const st = await rpc("git_open", { path });
      await gitLoadInfo(); gitPage.repo = st.root; gitPage.status = st; gitPage.tab = "changes"; go("source");
    });
}

function gitInitDialog(path = "") {
  gitPathDialog("Start a new repository", "Makes this folder a Git repository on this PC. Nothing is sent anywhere until you add a remote and push.",
    [{ label: "Folder", value: path, placeholder: "C:\\Users\\you\\source\\repos\\new-project" }, { label: "First branch", value: "main" }], "Start repository",
    async (folder, branch) => {
      const st = await rpc("git_init", { path: folder, branch: branch || "main" });
      await gitLoadInfo(); gitPage.repo = st.root; gitPage.status = st; gitPage.tab = "changes"; go("source");
      toast("Repository started. Stage and commit your files to begin its history.");
    });
}

function gitCloneDialog() {
  gitPathDialog("Clone a repository", "Copies a repository to this PC. If it needs a sign-in, Git Credential Manager opens its own window; the app never sees your password.",
    [{ label: "Repository address", placeholder: "https://github.com/org/repo.git" },
      { label: "Into this folder", value: gitPage.info?.clone_folder || "", placeholder: "C:\\Users\\you\\source\\repos" },
      { label: "Folder name (optional)", placeholder: "Named after the repository" }], "Clone",
    async (url, parent, name) => {
      const op = await rpc("git_clone_start", { url, parent, name: name || null });
      go("source");
      gitWatch(op);
    });
}

// ---------- top bar and notebooks ----------

function renderGitPill() {
  const slot = document.getElementById("git-slot");
  if (!slot) return;
  const st = gitPage.status;
  if (!st || !previewShown()) { slot.replaceChildren(); return; }
  const changed = st.changed ? ` · ${st.changed} changed` : "";
  slot.replaceChildren(h("button", { class: `pill git-pill${st.conflicts.length ? " bad" : ""}`, title: `${st.name}: ${st.summary} Open Source control.`,
    "aria-label": `Branch ${st.branch || "none"} in ${st.name}${changed}`, onclick: () => go("source") },
  icon("branch", 14), gitPillText(st), st.changed ? h("span", { class: "git-dot", "aria-hidden": "true" }) : null));
}

const gitNotebookCache = {};

// A chip on the notebook ribbon when the notebook's file is in an opened repository.
function gitNotebookChip(nb) {
  const slot = h("span", { class: "git-nb" });
  if (!previewShown() || !nb?.name) return slot;
  const fill = (info) => {
    if (!info) { slot.replaceChildren(); return; }
    slot.replaceChildren(btn(`${info.branch || "no branch"}${info.state ? ` · ${info.state}` : ""}`, "branch", async () => {
      if (gitPage.repo !== info.repo) await gitSwitchRepo(info.repo);
      gitPage.tab = "changes";
      go("source");
      if (info.state) gitSelect(info.path, info.state === "conflict" ? "conflicts" : "unstaged");
    }, "tool outline", { title: `In the repository ${info.name}. Open its changes in Source control.` }));
  };
  const cached = gitNotebookCache[nb.name];
  if (cached && Date.now() - cached.at < 15000) fill(cached.info);
  else rpc("git_notebook_info", { name: nb.name }).then((info) => { gitNotebookCache[nb.name] = { info, at: Date.now() }; fill(info); }).catch(() => {});
  return slot;
}

// Keep the pill current: when the window comes back, and every minute (status is local and quick).
window.addEventListener("focus", () => { if (gitCurrent() && !gitPage.loading) gitRefresh({ quiet: true }).then(() => { if (state.view === "source") render(); }); });
setInterval(() => { if (gitCurrent() && document.hasFocus()) gitRefresh({ quiet: true }); }, 60000);
setTimeout(() => { gitLoadInfo().then(() => gitRefresh({ quiet: true })).catch(() => {}); }, 1500);

registerFeature({
  id: "sourceControl",
  preview: true,
  rail: { label: "Source control", icon: "branch", order: 90, bottom: true },
  views: { source: renderSourceControl },
  commands: [
    { label: "Open Source control", icon: "branch", keywords: "git changes commit diff", run: () => go("source") },
    { label: "Commit staged changes", icon: "ok", keywords: "git commit", run: () => { gitPage.tab = "changes"; go("source"); setTimeout(() => document.getElementById("git-message")?.focus(), 300); }, when: () => !!gitCurrent() },
    { label: "Fetch", icon: "sync", keywords: "git fetch remote", run: () => gitRemoteOp("git_fetch", {}), when: () => !!gitPage.status?.remotes.length },
    { label: "Pull", icon: "arrowDownload", keywords: "git pull remote", run: () => gitRemoteOp("git_pull", { mode: "ff-only" }), when: () => !!gitPage.status?.upstream },
    { label: "Push", icon: "arrowUpload", keywords: "git push remote publish", run: () => gitRemoteOp("git_push", {}), when: () => !!gitPage.status?.remotes.length },
    { label: "Create a branch", icon: "branch", keywords: "git branch new", run: () => { gitPage.tab = "branches"; go("source"); }, when: () => !!gitCurrent() },
    { label: "Switch branch", icon: "branch", keywords: "git checkout switch", run: () => { gitPage.tab = "branches"; go("source"); }, when: () => !!gitCurrent() },
    { label: "Clone a repository", icon: "arrowDownload", keywords: "git clone github azure devops", run: gitCloneDialog },
    { label: "Open a repository", icon: "folderOpen", keywords: "git open folder", run: gitOpenDialog },
    { label: "Start a new repository", icon: "add", keywords: "git init new", run: () => gitInitDialog() },
  ],
});
