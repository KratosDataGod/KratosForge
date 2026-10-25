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
  ];
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
