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

// Repositories grouped by what they hold, in the order of the kinds; ones that don't say come last.
function gitGroupRepos(repos, kinds) {
  const order = Object.keys(kinds || {});
  const groups = order.map((k) => ({ kind: k, label: kinds[k].label, repos: [] }));
  const other = { kind: null, label: "Not yet sorted", repos: [] };
  for (const r of repos || []) (groups.find((g) => g.kind === r.kind) || other).repos.push(r);
  return [...groups, other].filter((g) => g.repos.length);
}

// Where a notebook's data is (item 37, stage 2): a lakehouse attached to the notebook itself,
// else its notebooks repository's location for the environment in the top bar, else nothing.
function nbData(nb, env = (typeof state !== "undefined" && state.meta?.env) || "dev") {
  const envLabel = env[0].toUpperCase() + env.slice(1);
  if (nb?.lakehouse) return { ref: nb.lakehouse, from: "notebook", env, envLabel };
  const loc = nb?.locations?.envs?.[env] || null;
  if (loc?.lakehouse) return { ref: loc.lakehouse, from: "locations", env, envLabel, loc };
  return { ref: null, from: loc ? "this_pc" : nb?.place ? "unset" : null, env, envLabel, loc };
}

function nbLakehouse(nb) {
  return nbData(nb).ref;
}

// The chip's words for where the data is.
function nbDataText(data) {
  if (data.from === "notebook") return `Data: ${data.ref.name} (this notebook)`;
  if (data.from === "locations") return `Data: ${data.ref.name}`;
  if (data.from === "this_pc") return `Data: nothing attached in ${data.envLabel}`;
  if (data.from === "unset") return `No location for ${data.envLabel}`;
  return "";
}

// A platform repository compared with what is deployed (item 37, stage 3).
const PLATFORM_STATES = { same: "same", changed: "changed", only_git: "only in Git", only_deployed: "removed in Git" };
const PLATFORM_GROUPS = { admins: "Admins", domains: "Domains", landing_zones: "Landing zones", sources: "Source systems",
  key_tables: "Key tables", glossary: "Glossary", shortcut_rules: "Shortcut rules", policy_templates: "Policy templates",
  policies: "Policies" };

function platformValue(v) {
  if (Array.isArray(v)) return v.length ? v.join(", ") : "none";
  if (v && typeof v === "object") return JSON.stringify(v);
  if (v === "" || v === null || v === undefined) return "empty";
  return typeof v === "string" ? `"${v}"` : String(v);
}

// What changed in one object, a line per field.
function platformChangeLines(item) {
  if (item.kind === "admins") {
    const [adds, removes] = [item.changes[0]?.git || [], item.changes[0]?.deployed || []];
    return [adds.length ? `Git adds ${adds.join(", ")}` : null, removes.length ? `Git removes ${removes.join(", ")}` : null].filter(Boolean);
  }
  return item.changes.map((c) => `${c.field || "value"}: Git ${platformValue(c.git)}, deployed ${platformValue(c.deployed)}`);
}

function platformItemName(item, labels = {}) {
  return `${labels[item.kind] || item.kind} ${item.id}`;
}

// One line per deploy step, for the plan.
function platformStepText(step, labels = {}) {
  const what = step.what || { create: "create", update: "save the changes", remove: "remove" }[step.action] || step.action;
  return `${platformItemName(step, labels)}: ${what}`;
}

// A capacity repository's drift report (item 37, stage 4): capacity.toml compared with Fabric.
const CAPACITY_STATES = { same: "same", differs: "differs", not_visible: "not visible to you", not_in_git: "not in Git" };

// The lines under one capacity: settings that differ, then where the file's workspaces really are.
function capacityLines(item) {
  const w = item.workspaces;
  const name = (x) => x.name || x.id;
  return [
    ...item.fields.map((f) => `${f.field === "sku" ? "Size" : f.field[0].toUpperCase() + f.field.slice(1)}: Git ${f.git}, Fabric ${f.fabric}`),
    ...w.elsewhere.map((x) => `${name(x)}: Git expects it here; Fabric has it on ${x.on || "a capacity you can't see"}`),
    ...w.not_in_git.map((x) => `${name(x)}: ${item.state === "not_in_git" ? "on this capacity" : "on this capacity in Fabric, not listed in capacity.toml"}`),
    ...w.not_visible.map((x) => `${name(x)}: you can't see this workspace, so it isn't compared`),
    w.same ? `${w.same} workspace${w.same === 1 ? " is" : "s are"} where Git expects` : null,
  ].filter(Boolean);
}

// ---------- state ----------

const gitPage = {
  info: null, repo: null, status: null, tab: "changes",
  selected: null,          // {path, staged, untracked, conflict}
  diff: null, diffMode: "inline", notebookView: true,
  message: "", branches: null, log: null, logRef: null, detail: null, compare: [], compared: null,
  settings: null, op: null, opTimer: null, refreshTimer: null, loading: false,
  platform: { env: "prod", key: null, diff: null, error: null },  // the Platform tab of a platform repository
  capacity: { key: null, diff: null, error: null },  // the Capacity tab of a capacity repository
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
    setView(h("div", { class: "page" }, h("h1", {}, "Source control"), loading("Loading…")));
    const ok = await attempt(gitLoadInfo, "Couldn't read Git on this PC");
    if (!ok || state.view !== "source") return;
  }
  const info = gitPage.info;
  const head = h("div", { class: "row" }, h("h1", {}, "Source control"), preview("sourceControl", h("span")), learnMore("source-control-preview"));
  gitPage.title = () => [h("h1", {}, "Source control"), preview("sourceControl", h("span")), learnMore("source-control-preview")];
  if (!info.installed) {
    setView(h("div", { class: "page narrow" }, head, emptyState({ icon: "branch", title: "Git isn't installed on this PC",
      text: "Source control uses Git for Windows. Install it from git-scm.com with Git Credential Manager (the default), then open this page again.",
      action: [btn("Get Git for Windows", "open", () => openLink("https://git-scm.com/download/win"), "tool primary"),
        btn("Check again", "sync", async () => { gitPage.info = null; render(); }, "tool outline")] })));
    return;
  }
  if (!gitCurrent()) {
    setView(h("div", { class: "page narrow" }, head,
      pageIntro("Keep notebooks, SQL and metadata projects in Git: see every change, work on branches, and push to GitHub, Azure DevOps or any Git server. Everything here runs with your own Git on this PC."),
      h("div", { class: "tiles" },
        tile("arrowDownload", "Clone a repository", "Copy a repository from GitHub, Azure DevOps or another Git server to this PC.", gitCloneDialog),
        tile("folderOpen", "Open a folder", "Use a repository that is already on this PC.", gitOpenDialog),
        tile("notebook", "Version my notebooks", "Start a notebooks repository in the folder this app keeps notebooks in.", () => gitInitDialog(info.notebooks_folder, "notebooks"))),
      h("h2", {}, "Start a repository for one kind of definition"),
      h("p", { class: "muted-text" }, "Each kind lives in its own repository, so a notebook change never rides along with a platform change."),
      gitKindTiles(info)));
    return;
  }
  if (!gitPage.status || gitPage.status.root !== gitCurrent()) {
    setView(h("div", { class: "page" }, head, h("p", { class: "muted-text" }, "Reading the repository…")));
    await gitRefresh();
    if (state.view !== "source") return;
    if (!gitPage.status) {
      setView(h("div", { class: "page narrow" }, head, emptyState({ title: "This repository can't be read",
        text: `${gitCurrent()} may have moved or been deleted.`,
        action: h("div", { class: "row" }, btn("Choose another", "folderOpen", (e) => gitRepoMenu(e.currentTarget), "tool outline"),
          btn("Take it off the list", "delete", async () => {
            await attempt(() => rpc("git_forget", { repo: gitCurrent() }), "Not removed");
            gitPage.repo = null; await gitLoadInfo(); render();
          }, "tool link")) })));
      return;
    }
  }
  const st = gitPage.status;
  const counts = st.staged.length + st.unstaged.length + st.untracked.length + st.conflicts.length;
  const tab = (id, label) => h("button", { class: `tab${gitPage.tab === id ? " active" : ""}`, role: "tab", "aria-selected": String(gitPage.tab === id),
    onclick: () => { gitPage.tab = id; render(); } }, label);
  const isPlatform = gitKindOf(st.root).kind === "platform";
  const isCapacity = gitKindOf(st.root).kind === "capacity";
  if ((gitPage.tab === "platform" && !isPlatform) || (gitPage.tab === "capacity" && !isCapacity)) gitPage.tab = "changes";
  const tabs = h("div", { class: "subtabs", role: "tablist", "aria-label": "Repository" }, tab("changes", counts ? `Changes ${counts}` : "Changes"),
    isPlatform ? tab("platform", "Platform") : null, isCapacity ? tab("capacity", "Capacity") : null, tab("branches", "Branches"),
    tab("history", "History"), tab("settings", "Repository settings"));
  const bodies = { changes: gitChangesTab, platform: gitPlatformTab, capacity: gitCapacityTab, branches: gitBranchesTab, history: gitHistoryTab, settings: gitSettingsTab };
  setView(h("div", { class: "page git-page" }, gitHeader(st), gitOpBand(), gitMergeBanner(st), gitKindBanner(st), tabs,
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
        gitKindOf(st.root).kind ? h("span", { class: "pill" }, gitPage.info.kinds[gitKindOf(st.root).kind].label) : null,
        gitKindOf(st.root).kind === "notebooks" ? btn("Data locations", "lakehouse",
          () => gitLocationsDialog(st.root, gitRepoName(st.root), gitAfterChange), "tool outline small",
          { title: "Where this repository's notebooks find their data in each environment" }) : null,
        gitKindOf(st.root).kind === "notebooks" ? btn("Set up for VS Code", "code",
          () => vscodeSetupDialog(st.root, gitRepoName(st.root), gitAfterChange), "tool outline small",
          { title: "Add a validate task, the Copilot server and recommended extensions to this repository" }) : null,
        ...gitPage.title().slice(1)),
      h("p", { class: "sub git-sub" }, sub)),
    h("div", { class: "row wrap" }, remoteActions));
}

function gitRepoMenu(anchor) {
  const repos = (gitPage.info?.repos || []).filter((r) => r.exists);
  showMenu(anchor, [
    ...gitGroupRepos(repos, gitPage.info?.kinds).flatMap((g) => [{ info: g.label },
      ...g.repos.map((r) => ({ label: r.path === gitCurrent() ? `${r.name} (open)` : r.name, icon: "branch", action: () => gitSwitchRepo(r.path) }))]),
    { info: "More" },
    { label: "New repository", icon: "add", action: () => gitNewDialog() },
    { label: "Clone a repository", icon: "arrowDownload", action: gitCloneDialog },
    { label: "Open a folder", icon: "folderOpen", action: gitOpenDialog },
  ]);
}

function gitKindTiles(info) {
  const icons = { notebooks: "notebook", project: "database", platform: "building", capacity: "gauge" };
  return h("div", { class: "tiles" }, Object.entries(info?.kinds || {}).map(([kind, k]) =>
    tile(icons[kind] || "add", `New ${k.label.toLowerCase()} repository`, `${k.holds} ${k.for}`, () => gitNewDialog(kind))));
}

function gitKindOf(root) {
  return gitPage.info?.repos.find((r) => r.path === root) || {};
}

// Under the header when the repository doesn't say what it holds: one click writes the marker.
function gitKindBanner(st) {
  const r = gitKindOf(st.root);
  const kinds = gitPage.info?.kinds || {};
  if (r.kind || !Object.keys(kinds).length) return null;
  const order = Object.keys(kinds).sort((a, b) => (b === r.suggested) - (a === r.suggested));
  return h("div", { class: "callout git-kind" }, icon("info", 20),
    h("span", {}, "What does this repository hold? KratosForge keeps each kind in its own repository. Choosing adds ",
      h("code", {}, "fabric-desktop.repo.toml"), " as a change you commit; nothing else changes."),
    h("span", { class: "spacer" }),
    h("div", { class: "row wrap" }, order.map((k) => btn(kinds[k].label, null, () => gitSetKind(st.root, k),
      k === r.suggested ? "tool primary small" : "tool outline small", k === r.suggested ? { title: "Suggested from the files in it" } : {}))));
}

async function gitSetKind(root, kind, confirm = false) {
  const res = await attempt(() => rpc("git_set_kind", { repo: root, kind, confirm }), "Couldn't save what it holds");
  if (!res) return;
  await gitLoadInfo().catch(() => {});
  gitAfterChange(res.status);
  toast(`Marked as a ${gitPage.info?.kinds?.[kind]?.label.toLowerCase() || kind} repository. Commit fabric-desktop.repo.toml to keep it.`);
}

function gitNewDialog(kind = "notebooks") {
  const kinds = gitPage.info?.kinds || {};
  let chosen = kind in kinds ? kind : Object.keys(kinds)[0];
  const name = h("input", { value: "", placeholder: "sales-notebooks", "aria-label": "Repository name" });
  const parent = h("input", { value: gitPage.info?.clone_folder || "", "aria-label": "Into this folder" });
  const holds = h("p", { class: "muted-text small" });
  const err = h("p", { class: "field-error" });
  const radios = h("div", { class: "git-kinds" }, Object.entries(kinds).map(([k, v]) =>
    h("label", { class: "git-choice" }, h("input", { type: "radio", name: "git-kind", value: k, checked: k === chosen ? true : null,
      onchange: () => { chosen = k; holds.textContent = `${v.holds} ${v.for}`; } }), h("span", {}, h("b", {}, v.label)))));
  holds.textContent = chosen ? `${kinds[chosen].holds} ${kinds[chosen].for}` : "";
  const ok = async () => {
    err.textContent = "";
    const folder = `${parent.value.trim().replace(/[\\/]+$/, "")}${parent.value.includes("\\") ? "\\" : "/"}${name.value.trim()}`;
    if (!name.value.trim()) { err.textContent = "Give the repository a name."; return; }
    try {
      const st = await rpc("git_create", { path: folder, kind: chosen });
      closeDialog();
      await gitLoadInfo(); gitPage.repo = st.root; gitPage.status = st; gitPage.tab = "changes"; go("source");
      toast("Repository created with its starting files. Review them and commit to begin its history.");
    } catch (e) { err.textContent = friendlyError(e).text; }
  };
  showDialog("New repository", h("div", { class: "git-form" },
    h("p", { class: "text" }, "A new Git repository on this PC for one kind of definition, with that kind's starting files. Nothing is sent anywhere until you add a remote and push."),
    h("div", { class: "field" }, "It holds", radios), holds,
    h("label", { class: "field" }, "Name", name), h("label", { class: "field" }, "Into this folder", folderField(parent, "the folder to create it in")), err),
  [btn("Cancel", null, closeDialog, "tool outline"), btn("Create repository", null, ok, "tool primary")]);
  setTimeout(() => name.focus(), 50);
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
      if (!(await confirmDialog("Undo the pull?", "Every file goes back to how it was before the pull, including the conflicts you fixed. Your own commits are kept.", "Undo the pull", { danger: true }))) return;
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
      // While Git still gives no measure, only the words change, so the wolf keeps its stride.
      const where = band?.dataset.measured === "no" && now.percent === null ? band.querySelector(".remote-where") : null;
      if (where) {
        where.textContent = gitOpSaid(now);
        band.querySelector("[role=progressbar]").setAttribute("aria-valuetext", where.textContent);
      } else if (band) band.replaceWith(gitOpBand());
      gitPage.opTimer = setTimeout(tick, 500);
      return;
    }
    gitPage.op = null;
    const title = `${now.status === "ok" ? GIT_OP_DONE[now.kind] : GIT_OP_LABEL[now.kind]} ${gitRepoName(now.repo)}`;
    logActivity({ id: `git-${now.id}`, status: now.status === "ok" ? "ok" : now.status === "cancelled" ? "info" : "failed",
      title, text: now.message, details: now.details });
    if (now.status === "ok") toast(now.kind === "clone" ? `Cloned into ${now.result?.root || now.repo}.` : now.message);
    else if (now.status === "failed") toast(now.message, true, { quiet: true, details: now.details });
    // Forge Spark on the message when a push, pull or clone is done (a fetch only looks), the ember
    // when one fails; cancelling plays nothing (motion.js).
    if (now.status === "ok" && now.kind !== "fetch") forgeEvent("git.op.succeeded", document.getElementById("toast"));
    else if (now.status === "failed") forgeEvent("git.op.failed", document.getElementById("toast"));
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

const gitOpSaid = (op) => (op.phase && op.phase !== "Starting" ? op.phase : "Talking to the remote");

// While Git gives no measure (talking to the remote, signing in) the navy wolf trots in place on an
// empty track, with no fill or percentage (motion.js remoteProgress); once Git counts objects the bar
// shows its percentage.
function gitOpBand() {
  const op = gitPage.op;
  if (!op || op.status !== "running") return h("div", { id: "git-op" });
  const pct = op.percent;
  const label = `${GIT_OP_LABEL[op.kind]} ${gitRepoName(op.repo)}`;
  return h("div", { id: "git-op", class: "card pad git-op", "data-measured": pct === null ? "no" : "yes" },
    h("div", { class: "row" }, h("b", {}, `${GIT_OP_LABEL[op.kind]}…`), pct !== null ? h("span", { class: "muted-text" }, `${gitOpSaid(op)} ${pct}%`) : null,
      h("span", { class: "spacer" }), btn("Cancel", "dismiss", () => attempt(() => rpc("git_op_cancel", { id: op.id }), "Couldn't cancel"), "tool link small")),
    pct === null ? remoteProgress(label, gitOpSaid(op))
      : h("div", { class: "progress", role: "progressbar", "aria-label": label, "aria-valuemin": "0", "aria-valuemax": "100", "aria-valuenow": String(pct) },
        h("div", { class: "bar", style: `width:${pct}%` })),
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
  else {
    const tools = gitHunkActions(sel, d);
    body = gitPage.diffMode === "split" ? gitSplitTable(d.hunks, tools) : gitInlineTable(d.hunks, tools);
  }
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

// The header row of one block of changed lines, with its Stage, Unstage and Discard buttons when the
// file's changes can be taken a block at a time.
function gitHunkRow(hk, tools) {
  const actions = tools && hk.id ? tools(hk) : null;
  return h("tr", { class: "hunk" }, h("td", { colspan: "4" }, actions
    ? h("div", { class: "row git-hunk-head" }, h("span", { class: "grow" }, hk.header), actions) : hk.header));
}

function gitInlineTable(hunks, tools) {
  return h("table", { class: "git-lines" }, h("tbody", {}, hunks.flatMap((hk) => [
    gitHunkRow(hk, tools),
    ...hk.lines.map((l) => h("tr", { class: l.kind === "+" ? "add" : l.kind === "-" ? "del" : "" },
      h("td", { class: "ln" }, l.old ?? ""), h("td", { class: "ln" }, l.new ?? ""), h("td", { class: "sign" }, l.kind === " " ? "" : l.kind === "-" ? "−" : "+"),
      h("td", { class: "code" }, l.text)))])));
}

function gitSplitTable(hunks, tools) {
  const cell = (l, side) => l ? [h("td", { class: "ln" }, side === "l" ? l.old ?? "" : l.new ?? ""),
    h("td", { class: `code ${l.kind === "+" ? "add" : l.kind === "-" ? "del" : ""}` }, l.text)] : [h("td", { class: "ln" }), h("td", { class: "code blank" })];
  const cols = h("colgroup", {}, h("col", { class: "ln" }), h("col"), h("col", { class: "ln" }), h("col"));
  let at = 0;  // header rows come in the hunks' order
  return h("table", { class: "git-lines split" }, cols, h("tbody", {}, gitSideBySide(hunks).map((r) => r.header
    ? gitHunkRow(hunks[at++], tools)
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
  if (!(await confirmDialog(untracked ? "Delete this file?" : "Discard changes?", text, untracked ? "Delete file" : "Discard changes", { danger: true }))) return;
  const st = await attempt(() => rpc("git_discard", { repo: gitCurrent(), paths, confirm: true }), "Not discarded");
  if (st) gitAfterChange(st);
}

// Stage, Unstage and Discard for one block, offered only where Git can take a file a block at a time:
// a text diff of a tracked file, staged or not (not a notebook's cell view, a new file or a conflict).
function gitHunkActions(sel, d) {
  if (sel.base || d.view !== "text" || d.untracked || !["staged", "unstaged"].includes(sel.group)) return null;
  const go = (hk, action) => () => gitApplyHunk(sel.path, hk, action);
  return sel.group === "staged"
    ? (hk) => [btn("Unstage block", null, go(hk, "unstage"), "tool outline small")]
    : (hk) => [btn("Discard block…", null, go(hk, "discard"), "tool outline small"), btn("Stage block", null, go(hk, "stage"), "tool primary small")];
}

async function gitApplyHunk(path, hk, action) {
  const n = hk.lines.filter((l) => l.kind !== " ").length;
  if (action === "discard") {
    const lines = hk.lines.filter((l) => l.new != null).map((l) => l.new);
    const where = lines.length ? ` (lines ${lines[0]} to ${lines[lines.length - 1]})` : "";
    if (!(await confirmDialog("Discard this block?", `Throws away these ${n} changed line${n === 1 ? "" : "s"} in ${path}${where}. The rest of the file stays as it is. This can't be undone.`,
      "Discard block", { danger: true }))) return;
  }
  const st = await attempt(() => rpc("git_apply_hunk", { repo: gitCurrent(), path, hunk: hk.id, action, confirm: action === "discard" }),
    { stage: "Not staged", unstage: "Not unstaged", discard: "Not discarded" }[action]);
  await gitAfterChange(st || null);
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
  if (!(await confirmDialog(`Delete ${name}?`, "The branch is deleted on this PC. A copy on the remote stays; delete that on the provider's site.", "Delete branch", { danger: true }))) return;
  try {
    await rpc("git_delete_branch", { repo: gitCurrent(), name, confirm: true });
  } catch (err) {
    const f = friendlyError(err);
    if (!/commits no other branch holds/.test(f.text)) { toast(f.text, true, { details: f.details }); return; }
    if (!(await confirmDialog(`${name} has commits that aren't anywhere else`, "Deleting it loses those commits unless they were pushed. Delete it anyway?", "Delete anyway", { danger: true }))) return;
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
  const inputs = fields.map((f) => h("input", { value: f.value || "", placeholder: f.placeholder || "", "aria-label": f.label }));
  const err = h("p", { class: "field-error" });
  const ok = async () => {
    err.textContent = "";
    try { await run(...inputs.map((i) => i.value.trim())); closeDialog(); } catch (e) { err.textContent = friendlyError(e).text; }
  };
  inputs.forEach((i) => i.addEventListener("keydown", (e) => { if (e.key === "Enter") ok(); }));
  showDialog(title, h("div", { class: "git-form" }, h("p", { class: "text" }, intro),
    fields.map((f, i) => h("label", { class: "field" }, f.label, f.folder ? folderField(inputs[i], f.folder) : inputs[i])), err),
  [btn("Cancel", null, closeDialog, "tool outline"), btn(okLabel, null, ok, "tool primary")]);
}

function gitOpenDialog() {
  gitPathDialog("Open a repository", "The folder of a Git repository on this PC (any folder inside it works).",
    [{ label: "Folder", folder: "the repository's folder", placeholder: "C:\\Users\\you\\source\\repos\\project" }], "Open", async (path) => {
      const st = await rpc("git_open", { path });
      await gitLoadInfo(); gitPage.repo = st.root; gitPage.status = st; gitPage.tab = "changes"; go("source");
    });
}

function gitInitDialog(path = "", kind = null) {
  gitPathDialog("Start a new repository", "Makes this folder a Git repository on this PC. Nothing is sent anywhere until you add a remote and push.",
    [{ label: "Folder", folder: "the folder", value: path, placeholder: "C:\\Users\\you\\source\\repos\\new-project" }, { label: "First branch", value: "main" }], "Start repository",
    async (folder, branch) => {
      const st = await rpc("git_init", { path: folder, branch: branch || "main", kind });
      await gitLoadInfo(); gitPage.repo = st.root; gitPage.status = st; gitPage.tab = "changes"; go("source");
      toast("Repository started. Stage and commit your files to begin its history.");
    });
}

function gitCloneDialog() {
  gitPathDialog("Clone a repository", "Copies a repository to this PC. If it needs a sign-in, Git Credential Manager opens its own window; the app never sees your password.",
    [{ label: "Repository address", placeholder: "https://github.com/org/repo.git" },
      { label: "Into this folder", folder: "the folder to clone into", value: gitPage.info?.clone_folder || "", placeholder: "C:\\Users\\you\\source\\repos" },
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
    if (info.wrong_place) {
      slot.replaceChildren(btn(`${info.branch || "no branch"} · wrong repository`, "warning", async () => {
        if (gitPage.repo !== info.repo) await gitSwitchRepo(info.repo);
        go("source");
      }, "tool outline warn", { title: `${info.wrong_place} Open ${info.name} in Source control to change what it holds.` }));
      return;
    }
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

// Listed in the Keyboard shortcuts dialog on Source control (handled on the commit message box).
registerShortcuts("Source control", [
  { keys: ["Ctrl+Enter"], label: "In the commit message: commit", when: () => state.view === "source" },
]);

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
    { label: "New repository", icon: "add", keywords: "git init new notebooks project platform capacity", run: () => gitNewDialog() },
    { label: "Make a folder a repository", icon: "add", keywords: "git init existing folder", run: () => gitInitDialog() },
  ],
});

// ---------- notebooks in notebooks repositories (item 37, stage 2) ----------

// Shown on notebooks saved in a notebooks repository: where the data is for the environment in
// the top bar, with each environment's location and a way to edit them.
function notebookDataChip(nb) {
  if (!nb?.place) return null;
  const data = nbData(nb);
  return btn(nbDataText(data), data.from === "unset" ? "warning" : "lakehouse",
    (e) => showMenu(e.currentTarget, notebookDataMenu(nb)), `tool outline${data.from === "unset" ? " warn" : ""}`,
    { title: `${data.ref?.workspace_name ? `${data.ref.name} in ${data.ref.workspace_name}. ` : ""}Where ${nb.name} reads and writes data. It follows the environment in the top bar.` });
}

function notebookDataMenu(nb) {
  const data = nbData(nb);
  const envs = nb.locations?.envs || {};
  const where = (loc) => !loc ? "not set" : loc.lakehouse
    ? `${loc.workspace_name || "Workspace"} / ${loc.lakehouse.name}${loc.sql_name ? `, ${loc.sql_name}` : ""}` : "nothing attached (this PC)";
  return [
    { info: `From ${nb.place.label} / locations.toml` },
    ...["dev", "test", "prod"].map((env) => ({ info: `${env[0].toUpperCase() + env.slice(1)}: ${where(envs[env])}${env === data.env ? " (now)" : ""}` })),
    nb.locations?.problem ? { info: nb.locations.problem } : null,
    nb.lakehouse
      ? { label: `Use the repository's locations again (stop using ${nb.lakehouse.name})`, icon: "sync", action: detachLakehouse }
      : { label: "Attach a different lakehouse to this notebook only…", icon: "add", action: openDataItems },
    { label: "Edit locations for this repository…", icon: "layers", action: () => gitLocationsDialog(nb.place.root, nb.place.label, () => nbReloadLocations(nb)) },
    { label: "Move this notebook…", icon: "folder", action: () => moveNotebookDialog(nb) },
  ].filter(Boolean);
}

async function nbReloadLocations(nb) {
  const fresh = await rpc("open_notebook", { name: nb.name }).catch(() => null);
  if (!fresh || state.notebook !== nb) return;
  nb.place = fresh.place;
  nb.locations = fresh.locations;
  if (state.view === "notebook") renderNotebook();
}

// Asks where a new notebook goes when there are notebooks repositories; the last choice comes first.
// Resolves to the place's root (null for the app's own folder), or undefined when cancelled.
async function nbChoosePlace() {
  const places = (await rpc("notebook_places").catch(() => null))?.places || [];
  if (places.length < 2) return null;
  let last = null;
  try { last = localStorage.getItem("fd.notebookPlace"); } catch { /* storage can be off */ }
  if (last === null || !places.some((p) => (p.root || "") === last)) last = places[1].root;  // first repository
  const select = h("select", { "aria-label": "Save in" }, places.map((p) =>
    h("option", { value: p.root || "", selected: (p.root || "") === (last || "") ? true : null }, p.root ? `${p.label} (repository)` : "This PC (the app's notebooks folder)")));
  return new Promise((resolve) => {
    const done = (value) => { closeDialog(); resolve(value); };
    showDialog("New notebook", h("div", { class: "git-form" },
      h("label", { class: "field" }, "Save in", select),
      h("p", { class: "muted-text small" }, "In a repository it's saved under notebooks/ as a change to commit, and finds its data from that repository's locations.")),
    [btn("Cancel", null, () => done(undefined), "tool outline"), btn("Create", null, () => {
      try { localStorage.setItem("fd.notebookPlace", select.value); } catch { /* storage can be off */ }
      done(select.value || null);
    }, "tool primary")]);
  });
}

async function moveNotebookDialog(nb) {
  if (nb.dirty && !(await saveNotebook())) return;
  const places = ((await attempt(() => rpc("notebook_places"), "Couldn't list where notebooks can go"))?.places || [])
    .filter((p) => (p.root || null) !== (nb.place?.root || null));
  if (!places.length) {
    toast("There's nowhere else to move it. Make a notebooks repository in Source control first.");
    return;
  }
  const select = h("select", { "aria-label": "Move to" }, places.map((p) => h("option", { value: p.root || "" }, p.root ? p.label : "This PC")));
  showDialog(`Move ${nb.name}`, h("div", { class: "git-form" },
    h("label", { class: "field" }, "Move to", select),
    h("p", { class: "muted-text small" }, "The file and its resources folder move; nothing is committed. If a notebook or folder with this name is already there, nothing moves.")),
  [btn("Cancel", null, closeDialog, "tool outline"), btn("Move", null, async () => {
    const out = await attempt(() => rpc("move_notebook", { name: nb.name, place: select.value || null }), "Couldn't move the notebook");
    if (!out) return;
    closeDialog();
    delete gitNotebookCache[nb.name];
    await nbReloadLocations(nb);
    toast(out.place ? `${nb.name} is now in ${out.place.label}. Commit it there to keep it.` : `${nb.name} is now in the app's notebooks folder.`);
    if (out.note) toast(out.note, true);
  }, "tool primary")]);
}

// Edits where a notebooks repository's notebooks find their data, per environment. Saved to its
// locations.toml as ids and names, as a change to commit. Nothing in Fabric changes.
async function gitLocationsDialog(root, label, after) {
  const now = await attempt(() => rpc("git_locations", { repo: root }), "Couldn't read the locations");
  if (!now) return;
  const workspaces = await loadWorkspaces();
  if (!workspaces.length) { toast("Sign in to pick workspaces and lakehouses.", true); return; }
  const envs = ["dev", "test", "prod"];
  const rows = {};
  const opt = (value, text, on) => h("option", { value, selected: on ? true : null }, text);
  const row = (env) => {
    const cur = now.envs[env] || null;
    const ws = h("select", { "aria-label": `${env} workspace` },
      opt("", "Not set", !cur), opt("this_pc", "This PC", cur && !cur.workspace_id),
      workspaces.map((w) => opt(w.id, w.displayName, cur?.workspace_id === w.id)));
    const lh = h("select", { "aria-label": `${env} lakehouse` });
    const sql = h("select", { "aria-label": `${env} SQL database` });
    // The saved lakehouse and SQL database stay picked while Fabric's lists load, and when a list
    // doesn't show them, so saving never drops a location nobody changed.
    const saved = (key, list = []) => cur && ws.value === cur.workspace_id && cur[`${key}_id`] && !list.some((x) => x.id === cur[`${key}_id`])
      ? [opt(cur[`${key}_id`], cur[`${key}_name`] || cur[`${key}_id`], true)] : [];
    const fill = async () => {
      const [keepLh, keepSql] = [saved("lakehouse"), saved("sql")];
      lh.replaceChildren(opt("", "—", !keepLh.length), ...keepLh);
      sql.replaceChildren(opt("", "none", !keepSql.length), ...keepSql);
      lh.disabled = sql.disabled = !ws.value || ws.value === "this_pc";
      if (lh.disabled) return;
      const id = ws.value;
      const [lakes, items] = await Promise.all([rpc("list_lakehouses", { workspace_id: id }).catch(() => []),
        rpc("list_items", { workspace_id: id }).catch(() => [])]);
      if (ws.value !== id) return;
      const dbs = (items || []).filter((i) => i.type === "SQLDatabase");
      lh.replaceChildren(opt("", "—", !cur?.lakehouse_id), ...saved("lakehouse", lakes), ...lakes.map((l) => opt(l.id, l.displayName, cur?.lakehouse_id === l.id)));
      sql.replaceChildren(opt("", "none", !cur?.sql_id), ...saved("sql", dbs), ...dbs.map((i) => opt(i.id, i.displayName, cur?.sql_id === i.id)));
    };
    ws.onchange = fill;
    fill();
    rows[env] = { ws, lh, sql };
    return h("tr", {}, h("td", {}, env[0].toUpperCase() + env.slice(1)), h("td", {}, ws), h("td", {}, lh), h("td", {}, sql));
  };
  const err = h("p", { class: "field-error" });
  const value = (env) => {
    const r = rows[env];
    if (!r.ws.value) return null;
    if (r.ws.value === "this_pc") return { this_pc: true };
    const name = (sel) => sel.selectedOptions[0]?.textContent || "";
    const w = workspaces.find((x) => x.id === r.ws.value);
    return { workspace_id: r.ws.value, workspace_name: w?.displayName || "",
      ...(r.lh.value ? { lakehouse_id: r.lh.value, lakehouse_name: name(r.lh) } : {}),
      ...(r.sql.value ? { sql_id: r.sql.value, sql_name: name(r.sql) } : {}) };
  };
  showDialog(`Where ${label} find their data`, h("div", { class: "git-form" },
    h("table", { class: "list git-locations" }, h("thead", {}, h("tr", {}, h("th", {}, "Env"), h("th", {}, "Workspace"), h("th", {}, "Lakehouse"), h("th", {}, "SQL database"))),
      h("tbody", {}, envs.map(row))),
    h("p", { class: "muted-text small" }, "Saved to locations.toml in the repository by workspace and item id, with the names beside them, as a change to commit. No passwords or keys. Nothing in Fabric changes."),
    now.problem ? h("p", { class: "field-error" }, `${now.problem} Saving writes a new one.`) : null, err),
  [btn("Cancel", null, closeDialog, "tool outline"), btn("Save locations", null, async () => {
    err.textContent = "";
    try {
      await rpc("git_set_locations", { repo: root, envs: Object.fromEntries(envs.map((e) => [e, value(e)])) });
      closeDialog();
      toast("Locations saved. Commit locations.toml to share them.");
      if (after) after();
    } catch (e) { err.textContent = friendlyError(e).text; }
  }, "tool primary")]);
}

// ---------- Platform (a platform repository: compare, export, deploy) ----------

function gitPlatformReload() {
  gitPage.platform.key = null;
  if (state.view === "source" && gitPage.tab === "platform") render();
}

function gitPlatformTab(st) {
  const p = gitPage.platform;
  const key = `${st.root}|${p.env}`;
  if (p.key !== key) {
    Object.assign(p, { key, diff: null, error: null });
    rpc("git_platform_diff", { repo: st.root, env: p.env })
      .then((d) => { if (p.key === key) p.diff = d; })
      .catch((err) => { if (p.key === key) p.error = friendlyError(err).text; })
      .finally(() => { if (state.view === "source" && gitPage.tab === "platform") render(); });
  }
  const envSelect = h("select", { "aria-label": "Compare with", onchange: (e) => { p.env = e.target.value; render(); } },
    ["dev", "test", "prod"].map((e) => h("option", { value: e, selected: p.env === e ? true : null }, e[0].toUpperCase() + e.slice(1))));
  const d = p.diff;
  const c = d?.counts;
  const bar = h("section", { class: "card pad" }, h("div", { class: "row wrap" },
    h("b", {}, "Compare with"), envSelect,
    c ? h("span", { class: "muted-text small" }, `${c.same} same · ${c.changed} changed · ${c.only_git} only in Git · ${c.only_deployed} removed in Git`) : null,
    h("span", { class: "spacer" }),
    btn("Refresh", "sync", gitPlatformReload, "tool outline small"),
    btn("Export from platform…", "arrowDownload", () => gitPlatformExport(st.root), "tool outline"),
    btn("Deploy…", "arrowUpload", () => gitPlatformDeploy(st.root), "tool primary")),
    h("p", { class: "muted-text small" }, "Git is the source of truth for platform definitions; the platform holds what is deployed. Comparing only reads: nothing changes in Git or in Fabric."));
  if (p.error) return h("div", { class: "git-platform" }, bar, h("div", { class: "callout git-warn" }, icon("warning", 16), p.error));
  if (!d) return h("div", { class: "git-platform" }, bar, h("p", { class: "muted-text" }, "Comparing with the platform…"));
  const problems = d.problems.length ? h("div", { class: "callout git-warn" }, icon("warning", 16),
    h("div", {}, h("b", {}, "These files can't be used, so a deploy won't start:"), d.problems.map((x) => h("div", { class: "small" }, x)))) : null;
  const kinds = [...new Set(d.items.map((i) => i.kind))];
  const groups = kinds.map((k) => {
    const items = d.items.filter((i) => i.kind === k);
    const shown = items.filter((i) => i.state !== "same");
    const same = items.length - shown.length;
    return h("section", { class: "card pad" }, h("h2", {}, PLATFORM_GROUPS[k] || k),
      shown.map((i) => h("div", { class: "git-platform-item" },
        h("div", { class: "row" }, h("span", { class: "grow" }, i.id), h("span", { class: `pill-s ${i.state === "changed" ? "warn" : i.state === "only_deployed" ? "fail" : "info"}` }, PLATFORM_STATES[i.state])),
        i.state === "changed" ? h("div", { class: "mono small git-platform-changes" }, platformChangeLines(i).map((l) => h("div", {}, l))) : null)),
      same ? h("p", { class: "muted-text small" }, shown.length ? `${same} more ${same === 1 ? "is" : "are"} the same.`
        : same === 1 ? "Same as deployed." : `All ${same} are the same as deployed.`) : null);
  });
  return h("div", { class: "git-platform" }, bar, problems,
    d.policies_set_up ? null : h("p", { class: "muted-text small" }, "Policies aren't set up on this platform yet."),
    d.items.length ? groups : h("p", { class: "muted-text" }, "Nothing to compare yet. Export from the platform to start the files."));
}

// ---------- Capacity (a capacity repository: drift report, write Fabric's state to the file) ----------

function gitCapacityReload() {
  gitPage.capacity.key = null;
  if (state.view === "source" && gitPage.tab === "capacity") render();
}

function gitCapacityTab(st) {
  const p = gitPage.capacity;
  if (p.key !== st.root) {
    const key = st.root;
    Object.assign(p, { key, diff: null, error: null });
    rpc("git_capacity_diff", { repo: key })
      .then((d) => { if (p.key === key) p.diff = d; })
      .catch((err) => { if (p.key === key) p.error = friendlyError(err).text; })
      .finally(() => { if (state.view === "source" && gitPage.tab === "capacity") render(); });
  }
  const d = p.diff;
  const c = d?.counts;
  const read = d ? timeText(d.read_at) : null;
  const bar = h("section", { class: "card pad" }, h("div", { class: "row wrap" },
    h("b", {}, "Drift report"),
    c ? h("span", { class: "muted-text small" }, `Read from Fabric at ${read} · ${c.same} same · ${c.differs} differ · ${c.not_in_git} not in Git · ${c.not_visible} not visible to you`) : null,
    h("span", { class: "spacer" }),
    btn("Refresh", "sync", gitCapacityReload, "tool outline small"),
    d ? btn("Copy report", "copyArrowRight", () => gitCapacityCopy(d), "tool outline small") : null,
    btn("Write Fabric's state to capacity.toml…", "arrowDownload", () => gitCapacityWrite(st.root), "tool primary")),
    h("p", { class: "muted-text small" }, "It only reads: no capacity is changed and no workspace is moved. Fix drift in the Fabric admin portal, or edit capacity.toml and commit if Git is the one that's wrong."));
  const how = howItWorks("source.capacity", {
    reads: "capacity.toml in this repository, and the capacities and workspaces Fabric shows you (GET /v1/capacities, GET /v1/workspaces). It starts no Fabric job and needs no admin role.",
    changes: "Nothing in Fabric: it never resizes, pauses, resumes or assigns a capacity. Write Fabric's state to capacity.toml replaces that one file, after you confirm, as a change to review and commit.",
    results: "On this tab. Copy report puts it on the clipboard as text.",
    more: [["Not visible", "People see different capacities and workspaces. One Fabric doesn't show you isn't compared, and writing keeps it in the file as it is."]],
    anchor: "capacity-repository" });
  const wrap = (...kids) => withHowItWorks(h("div", { class: "git-platform" }, ...kids), how);
  if (p.error) return wrap(bar, h("div", { class: "callout git-warn" }, icon("warning", 16), p.error));
  if (!d) return wrap(bar, h("p", { class: "muted-text" }, "Reading capacities from Fabric…"));
  const problems = d.problems.length ? h("div", { class: "callout git-warn" }, icon("warning", 16),
    h("div", {}, h("b", {}, "These parts of capacity.toml can't be used:"), d.problems.map((x) => h("div", { class: "small" }, x)))) : null;
  const cards = d.items.map((i) => h("section", { class: "card pad" },
    h("div", { class: "row" }, h("h2", { class: "grow" }, i.name),
      h("span", { class: `pill-s ${i.state === "same" ? "ok" : i.state === "not_visible" ? "info" : "warn"}` }, CAPACITY_STATES[i.state])),
    i.state === "not_visible" ? h("p", { class: "muted-text small" }, "You can't see this capacity, so it isn't compared. Missing from your view doesn't mean it changed or was deleted.")
      : h("div", { class: "small" }, capacityLines(i).map((l) => h("div", { class: "git-platform-item" }, l)))));
  return wrap(bar, problems,
    d.items.length ? cards : h("p", { class: "muted-text" }, "No capacities in capacity.toml, and Fabric shows you none. Write Fabric's state to start the file."));
}

function gitCapacityCopy(d) {
  const text = d.items.map((i) => [`${i.name}: ${CAPACITY_STATES[i.state]}`, ...(i.state === "not_visible" ? [] : capacityLines(i).map((l) => `  ${l}`))].join("\n")).join("\n");
  navigator.clipboard.writeText(`Capacity drift report, read ${d.read_at}\n${text}\n`).then(() => toast("Copied the report."), () => toast("Couldn't copy the report."));
}

// Replaces capacity.toml with what Fabric shows, as a change to review. Nothing in Fabric changes.
async function gitCapacityWrite(root) {
  if (!(await confirmDialog("Write Fabric's state to capacity.toml?",
    "Replaces the file with the capacities, settings and workspaces Fabric shows you now. It's a change to review and commit. Nothing in Fabric changes. Capacities and workspaces you can't see stay in the file as they are.",
    "Write the file"))) return;
  let out = await attempt(() => rpc("git_capacity_write", { repo: root }), "Couldn't write capacity.toml");
  if (!out) return;
  if (out.needs_confirm) {
    if (!(await confirmDialog("Replace changes you haven't committed?", out.message, "Replace and write", { danger: true }))) return;
    out = await attempt(() => rpc("git_capacity_write", { repo: root, confirm: true }), "Couldn't write capacity.toml");
    if (!out) return;
  }
  const hidden = out.kept.length + out.kept_workspaces.length;
  const kept = hidden ? ` Kept ${out.kept.length ? `${out.kept.length} capacit${out.kept.length === 1 ? "y" : "ies"}` : ""}${out.kept.length && out.kept_workspaces.length ? " and " : ""}${out.kept_workspaces.length ? `${out.kept_workspaces.length} workspace${out.kept_workspaces.length === 1 ? "" : "s"}` : ""} you can't see.` : "";
  toast(out.written.length ? `Wrote ${out.capacities} capacit${out.capacities === 1 ? "y" : "ies"} and ${out.workspaces} workspace${out.workspaces === 1 ? "" : "s"}. Review the change, then commit.${kept}`
    : `capacity.toml already matches Fabric.${kept}`);
  await gitAfterChange(out.status);
  gitCapacityReload();
}

// Writes what is deployed into the repository as changes to review. Nothing is committed and
// nothing in Fabric changes. Uncommitted edits are replaced only after a second confirm.
async function gitPlatformExport(root) {
  if (!(await confirmDialog("Export from the platform?",
    "Writes what the platform holds into this repository, one file per object, as changes to review. Nothing is committed and nothing in Fabric changes. Files for objects that aren't deployed are left as they are. Source connections, keys, requests and grants are never exported.",
    "Export"))) return;
  let out = await attempt(() => rpc("git_platform_export", { repo: root }), "Couldn't export");
  if (!out) return;
  if (out.needs_confirm) {
    if (!(await confirmDialog("Replace files you haven't committed?", `${out.message}`, "Replace and export", { danger: true }))) return;
    out = await attempt(() => rpc("git_platform_export", { repo: root, confirm: true }), "Couldn't export");
    if (!out) return;
  }
  const extra = [out.skipped.length ? `${out.skipped.length} skipped` : null, out.left.length ? `${out.left.length} not deployed left as they are` : null,
    out.disagree.length ? `${out.disagree.length} key table${out.disagree.length === 1 ? " differs" : "s differ"} between environments` : null].filter(Boolean);
  toast(out.written.length ? `Exported ${out.written.length} file${out.written.length === 1 ? "" : "s"}. Review them in Changes, then commit.${extra.length ? ` ${extra.join("; ")}.` : ""}`
    : `The files already match the platform.${extra.length ? ` ${extra.join("; ")}.` : ""}`);
  await gitAfterChange(out.status);
  gitPlatformReload();
}

// Deploy: shows the plan (steps, what is skipped and why, removals to tick), then runs exactly that
// plan after one confirm. The engine refuses a plan that changed since it was shown.
async function gitPlatformDeploy(root) {
  const ticked = new Set();
  let plan = await attempt(() => rpc("git_platform_deploy", { repo: root, remove: [] }), "Can't deploy");
  if (!plan) return;
  const body = h("div", { class: "git-form git-deploy" });
  const go = btn("Deploy", null, () => run(), "tool danger-solid");
  const replan = async () => {
    const next = await attempt(() => rpc("git_platform_deploy", { repo: root, remove: [...ticked].map((k) => JSON.parse(k)) }), "Can't deploy");
    if (next) { plan = next; draw(); }
  };
  const draw = (note) => {
    const L = plan.labels;
    const n = plan.steps.length;
    body.replaceChildren(...[
      h("p", {}, `Deploys commit ${plan.commit.slice(0, 8)}: the catalog in ${plan.catalog_env[0].toUpperCase() + plan.catalog_env.slice(1)}, and admins and key tables in every environment. Platform admins only; each step is recorded in the platform's audit as you.`),
      note ? h("div", { class: "callout git-warn small" }, icon("warning", 16), note) : null,
      plan.stops.length ? h("div", { class: "callout git-warn" }, icon("warning", 16),
        h("div", {}, h("b", {}, "The deploy can't start:"), plan.stops.map((x) => h("div", { class: "small" }, x)))) : null,
      h("h3", {}, n ? `Will do (${n} step${n === 1 ? "" : "s"})` : "Nothing to deploy"),
      h("div", { class: "small" }, plan.steps.map((s) => h("div", {}, `✓ ${platformStepText(s, L)}`))),
      plan.removable.length ? h("div", {}, h("h3", {}, "Removed in Git: tick each one to remove it"),
        [...new Map(plan.removable.map((r) => [JSON.stringify({ kind: r.kind, id: r.id }), r])).entries()].map(([k, r]) =>
          h("label", { class: "row small" }, h("input", { type: "checkbox", checked: ticked.has(k) ? true : null,
            onchange: (e) => { if (e.target.checked) ticked.add(k); else ticked.delete(k); replan(); } }), platformItemName(r, L))),
        h("p", { class: "muted-text small" }, "A removed policy is retired, never deleted. A domain or source still in use is refused. Key tables are never removed: their keys stay.")) : null,
      plan.blocked.length ? h("div", {}, h("h3", {}, `Skipped (${plan.blocked.length})`),
        plan.blocked.map((b) => h("div", { class: "small" }, `${platformItemName(b, L)} ${b.why}`))) : null,
      plan.notes.length ? h("div", { class: "muted-text small" }, plan.notes.map((x) => h("div", {}, x))) : null,
      h("p", { class: "muted-text small" }, "A changed policy is published as a new version; published versions never change. Source ids are never renumbered.")].filter(Boolean));
    go.disabled = !!plan.stops.length || !n;
    go.lastChild.textContent = n ? `Deploy ${n} step${n === 1 ? "" : "s"}` : "Deploy";
  };
  const run = async () => {
    go.disabled = true;
    let failed = false;
    const out = await attempt(() => rpc("git_platform_deploy", { repo: root, remove: [...ticked].map((k) => JSON.parse(k)), confirm: plan.plan_id })
      .catch((err) => { failed = !friendlyError(err).cancelled; throw err; }), "Deploy failed");
    if (!out) {
      go.disabled = false;
      if (failed) forgeEvent("deploy.failed", document.getElementById("toast"));  // an ember beside the error (motion.js)
      return;
    }
    if (out.deployed) {
      closeDialog();
      toast(`Deployed ${out.done.length} step${out.done.length === 1 ? "" : "s"}.`);
      forgeEvent("deploy.succeeded", document.getElementById("toast"));  // Anvil Strike (motion.js)
      gitPlatformReload();
    } else if (out.failed) {
      const note = `Stopped at ${platformStepText(out.failed, out.labels)}: ${out.message} ${out.done.length} of ${out.total} steps applied; the rest didn't run.`;
      plan = (await rpc("git_platform_deploy", { repo: root, remove: [...ticked].map((k) => JSON.parse(k)) }).catch(() => null)) || { ...out, steps: [] };
      draw(`${note} What is left is below. Fix the file and commit, then deploy again.`);
      forgeEvent("deploy.failed", body.querySelector(".callout.git-warn"));  // the ember beside where it stopped
      gitPlatformReload();
    } else {
      plan = out;
      draw("The platform or the files changed since this plan was shown. Check the new plan, then deploy.");
    }
  };
  draw();
  showDialog("Deploy the platform repository", body, [btn("Cancel", null, closeDialog, "tool outline"), go]);
}
