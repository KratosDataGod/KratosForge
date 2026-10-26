// VS Code alongside KratosForge (docs/vscode.md): open a notebook, or the folder it's in, in VS
// Code, and load the edits saved there when the app comes back into focus. Edits on both sides go
// through the changed-on-disk question in app.js (nbDiskConflict) instead.

function vscodeButton(nb) {
  return h("div", { class: "split" },
    btn("Open in VS Code", "code", () => openInVsCode(nb, "notebook"), "tool",
      { title: "Saves the notebook, then opens it in VS Code. Edits made there show here when you come back." }),
    h("button", { class: "tool split-more", "aria-label": "More VS Code options", title: "More VS Code options",
      onclick: (e) => showMenu(e.currentTarget, vscodeMenu(nb)) }, icon("chevronDown", 14)));
}

function vscodeMenu(nb) {
  return [
    { info: nb.place ? `${nb.name} is in the ${nb.place.label} repository.` : `${nb.name} is in the app's notebooks folder.` },
    { label: "Open this notebook in VS Code", icon: "code", action: () => openInVsCode(nb, "notebook") },
    { label: nb.place ? "Open the repository folder in VS Code" : "Open the notebooks folder in VS Code", icon: "folder",
      action: () => openInVsCode(nb, "folder") },
    { label: "Open this notebook in VS Code Insiders", icon: "code", action: () => openInVsCode(nb, "notebook", true) },
    nb.place ? { label: "Set up this repository for VS Code…", icon: "settings", action: () => vscodeSetupDialog(nb.place.root, nb.place.label) } : null,
  ].filter(Boolean);
}

// The shell opens only an existing .ipynb file or folder (open_in_vscode in main.rs).
async function openInVsCode(nb, what, insiders = false) {
  if (nb.dirty && !(await saveNotebook())) return;
  const where = await attempt(() => rpc("notebook_file_path", { name: nb.name }), "Couldn't find the notebook's file");
  if (!where) return;
  const path = what === "notebook" ? where.path : (where.repo_root || where.notebooks_folder);
  const ok = await attempt(() => invoke("open_in_vscode", { path, insiders }).then(() => true), "Couldn't open VS Code");
  if (ok) toast(what === "notebook" ? `Opened ${nb.name} in VS Code. Edits saved there show here when you come back.`
    : `Opened ${where.repo_label || "the notebooks folder"} in VS Code.`);
}

// Back in the app: when the open notebook's file changed outside it and nothing here is unsaved or
// running, show the file as it is now. Cells whose code didn't change keep their outputs.
async function nbReloadIfChangedOutside() {
  const nb = state.notebook;
  if (state.view !== "notebook" || !nb || nb.dirty || nb.diskConflict || !nb.diskStamp || nbRunning(nb)) return;
  const now = await rpc("notebook_disk_stamp", { name: nb.name }).catch(() => null);
  if (!now?.disk_stamp || now.disk_stamp === nb.diskStamp || state.notebook !== nb || nb.dirty || nbRunning(nb)) return;
  const fresh = await rpc("open_notebook", { name: nb.name }).catch(() => null);
  if (!fresh || state.notebook !== nb || nb.dirty || nbRunning(nb)) return;
  loadIntoState(fresh);
  renderNotebook();
  toast(`${fresh.name} changed outside KratosForge, for example in VS Code. Showing the file as it is now.`);
}

window.addEventListener("focus", () => { nbReloadIfChangedOutside(); });

// Set up a repository for VS Code (vscode_setup.py): adds the files a developer who opens the
// repository in VS Code needs. Files already there are never replaced; for Copilot's instructions
// the text to paste is shown instead. Nothing is committed.
async function vscodeSetupDialog(root, label, after) {
  const plan = await attempt(() => rpc("git_vscode_plan", { repo: root }), "Couldn't read the repository");
  if (!plan) return;
  const chosen = new Set(plan.files.filter((f) => f.state === "new").map((f) => f.path));
  const add = btn("", null, () => run(), "tool primary");
  const count = () => {
    add.textContent = chosen.size ? `Add ${chosen.size} file${chosen.size === 1 ? "" : "s"}` : "Nothing to add";
    add.disabled = !chosen.size;
  };
  const row = (f) => {
    const box = h("input", { type: "checkbox", checked: f.state === "new" ? true : null, disabled: f.state === "new" ? null : true,
      "aria-label": f.path, onchange: (e) => { e.target.checked ? chosen.add(f.path) : chosen.delete(f.path); count(); } });
    const state = { new: ["New", "info"], exists: ["Already there", "neu"], same: ["Already set up", "pass"] }[f.state];
    const paste = f.state === "exists" && f.path.endsWith(".md")
      ? h("details", {}, h("summary", {}, "Show the text to add"), h("pre", { class: "vscode-setup-text" }, f.text),
        btn("Copy", "document", () => attempt(() => navigator.clipboard.writeText(f.text).then(() => toast("Copied. Paste it into the file.")), "Could not copy"), "tool outline small"))
      : null;
    return h("div", { class: "vscode-setup-file" }, box,
      h("div", { class: "grow" }, h("label", {}, h("code", {}, f.path), h("div", { class: "muted-text small" }, `${f.label}. ${f.why}`
        + (f.state === "exists" ? " It's already in the repository, so it's left as it is." : ""))), paste),
      h("span", { class: `pill-s ${state[1]}` }, state[0]));
  };
  const run = async () => {
    const out = await attempt(() => rpc("git_vscode_apply", { repo: root, files: [...chosen] }), "Couldn't add the files");
    if (!out) return;
    closeDialog();
    toast(out.added.length ? `Added ${out.added.length} file${out.added.length === 1 ? "" : "s"} to ${label}. Commit them in Source control to share them.`
      : "Nothing was added: the files are already there.");
    if (out.skipped.length) toast(`Left as they are: ${out.skipped.join(", ")}.`, true);
    if (after) after(out.status);
  };
  count();
  showDialog(`Set up ${label} for VS Code`, h("div", { class: "scroll" },
    h("p", {}, "Adds files that make VS Code ready for anyone who clones this repository: a task that validates the open notebook, the KratosForge server for Copilot, and the extensions to install. Files already there are never replaced."),
    h("div", { class: "vscode-setup-files" }, plan.files.map(row)),
    h("div", { class: "callout" }, icon("info", 20), h("span", {},
      h("b", {}, "How this works. "), "Writes the ticked files in this repository's folder on this PC, and the kratosforge command the task runs. Nothing is committed or pushed; they show under Changes for you to review. ",
      "Doesn't write Fabric, OneLake or VS Code's own settings. The Validate task reads the notebook file and runs nothing; Copilot's tools still ask you here before they run code."))),
  [btn("Cancel", null, closeDialog, "tool outline"), add]);
}

// kratosforge:// links (deep_link.rs, docs/vscode.md): VS Code, a terminal or a browser asks the
// app to show a notebook or a page. A link only shows something; nothing in it is run, and unsaved
// edits here are saved first, as when you open another notebook yourself.
async function showLink(link) {
  if (link?.kind === "refused") {
    toast(`KratosForge didn't open that link.${link.reason ? ` ${link.reason}` : ""}`, true);
    return;
  }
  if (link?.kind === "page") {
    const page = railFeatures().concat(railFeatures(true)).map((f) => Object.keys(f.views || {})[0]).find((v) => v === link.page);
    if (page) go(page); else toast(`KratosForge has no page called ${link.page}.`, true);
    return;
  }
  if (link?.kind !== "notebook") return;
  const name = link.name ?? (await rpc("notebook_name_for_path", { path: link.path }).catch(() => ({}))).name;
  const known = name && (await rpc("list_notebooks").catch(() => [])).some((n) => n.name === name);
  if (!known) {
    toast(link.path ? `${link.path} isn't in the notebooks folder or a notebooks repository KratosForge knows. Import it, or open its repository in Source control.`
      : `There's no notebook named ${name}.`, true);
    return;
  }
  if (state.notebook?.name === name && state.view === "notebook") return;
  await openNotebook(name);
}

async function listenForLinks() {
  if (!window.__TAURI__?.event) return;
  await window.__TAURI__.event.listen("deep-link", ({ payload }) => {
    try { showLink(JSON.parse(payload)); } catch { /* not a link the shell made */ }
  });
  const pending = await invoke("take_pending_link").catch(() => null);
  if (pending) showLink(JSON.parse(pending));
}

