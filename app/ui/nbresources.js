// Notebook resources: files kept beside the notebook on this PC (<name>.resources/builtin/), as
// Fabric's built-in resources and Databricks' workspace files are. Shown on the explorer's
// Resources tab above the code snippets. Cells read them as builtin/<file>; notebookutils.nbResPath
// is the folder. Engine side: notebook_resources_service.py.

const NB_RES_MAX_FILES = 100;
const NB_RES_MAX_BYTES = 50 * 1024 * 1024;
const NB_RES_RESERVED = /^(con|prn|aux|nul|com[1-9]|lpt[1-9])$/i;

// Why a file name can't be used, or "" (the engine checks again: notebook_resources.check_name).
function nbResNameProblem(name) {
  const n = String(name || "").trim();
  if (!n || n === "." || n === "..") return "Give the file a name.";
  if (/[<>:"/\\|?*\x00-\x1f]/.test(n)) return "Avoid / \\ : * ? \" < > | in the name.";
  if (/[. ]$/.test(n)) return "A name can't end with a dot or a space on Windows.";
  if (NB_RES_RESERVED.test(n.split(".")[0].trim())) return "That name is kept by Windows for devices. Pick another.";
  if (n.length > 120) return "Keep the name to 120 characters.";
  return "";
}

function nbResView(nb) {
  if (!nb.resourcesView || nb.resourcesView.name !== nb.name) {
    nb.resourcesView = { name: nb.name, files: null, folder: "", problem: "", busy: false };
  }
  return nb.resourcesView;
}

async function nbResLoad(nb, box) {
  const v = nbResView(nb);
  try {
    const out = await rpc("notebook_resources", { name: nb.name });
    Object.assign(v, { files: out.files || [], folder: out.folder || "", problem: "" });
  } catch (err) {
    Object.assign(v, { files: [], problem: friendlyError(err).text });
  }
  if (box.isConnected) nbResFill(nb, box);
}

// Copies each chosen file in. A name already there is never replaced: the person picks another.
async function nbResAdd(nb, box, files) {
  const v = nbResView(nb);
  v.busy = true;
  nbResFill(nb, box);
  let added = 0;
  try {
    for (const f of files) {
      if (f.size > NB_RES_MAX_BYTES) { toast(`${f.name} is ${sizeText(f.size)}. Resource files can be up to 50 MB each.`, true); continue; }
      const content = replayBase64(await f.arrayBuffer());
      let name = f.name;
      for (;;) {
        const out = await attempt(() => rpc("notebook_resource_add", { name: nb.name, file_name: name, content }), `Couldn't add ${name}`);
        if (!out) break;
        if (out.added) { added += 1; break; }
        name = await askText(`${name} is already here`, "Add it as", out.suggested, "Add", nbResNameProblem);
        if (!name) break;
      }
    }
  } finally {
    v.busy = false;
  }
  if (added) toast(added === 1 ? "File added to the notebook's resources" : `${added} files added to the notebook's resources`);
  await nbResLoad(nb, box);
}

async function nbResRename(nb, box, file) {
  const name = await askText(`Rename ${file.name}`, "Name", file.name, "Rename", nbResNameProblem);
  if (!name || name === file.name) return;
  const out = await attempt(() => rpc("notebook_resource_rename", { name: nb.name, file_name: file.name, new_name: name }), "Couldn't rename the file");
  if (out) { toast(`Renamed. Cells now read it as builtin/${out.name}.`); await nbResLoad(nb, box); }
}

async function nbResDelete(nb, box, file) {
  const ok = await confirmDialog(`Delete ${file.name}?`,
    `It is deleted from this notebook's resources folder on this PC. Cells that read builtin/${file.name} won't find it.`,
    "Delete", { danger: true });
  if (!ok) return;
  if (await attempt(() => rpc("notebook_resource_delete", { name: nb.name, file_name: file.name }), "Couldn't delete the file")) {
    toast(`${file.name} deleted`);
    await nbResLoad(nb, box);
  }
}

function nbResReveal(nb, file = null) {
  return attempt(() => rpc("notebook_resource_reveal", file ? { name: nb.name, file_name: file.name } : { name: nb.name }),
    "Couldn't show the folder");
}

function nbResRow(nb, box, file) {
  return h("div", { class: "nb-res-row", role: "listitem", "data-file": file.name },
    icon("file", 16),
    h("span", { class: "nb-res-name", title: `builtin/${file.name}` }, file.name),
    h("span", { class: "muted small" }, sizeText(file.size)),
    h("button", { class: "tool small", title: "More actions", "aria-label": `Actions for ${file.name}`, "aria-haspopup": "menu",
      onclick: (e) => showMenu(e.currentTarget, [
        { label: "Copy path", action: () => copyText(`builtin/${file.name}`, "Path") },
        { label: "Show in folder", action: () => nbResReveal(nb, file) },
        { label: "Rename", action: () => nbResRename(nb, box, file) },
        { label: "Delete", action: () => nbResDelete(nb, box, file) },
      ]) }, "⋯"));
}

function nbResFill(nb, box) {
  const v = nbResView(nb);
  const input = h("input", { type: "file", multiple: true, hidden: true, "aria-label": "Choose files to add" });
  input.addEventListener("change", () => { if (input.files?.length) nbResAdd(nb, box, [...input.files]); });
  const full = (v.files || []).length >= NB_RES_MAX_FILES;
  const add = btn(v.busy ? "Adding…" : "Add files", "add", () => input.click(), "tool outline small",
    { disabled: v.busy || full || v.files === null, title: full ? "This notebook has 100 resource files, the most it can have." : null });
  const list = v.files === null ? h("div", { class: "muted-text small" }, "Loading…")
    : v.problem ? h("div", { class: "error-text small" }, v.problem)
    : v.files.length ? h("div", { class: "nb-res-list", role: "list" }, v.files.map((f) => nbResRow(nb, box, f)))
    : h("div", { class: "muted-text small" }, "No files yet. Add a CSV, JSON, a Python module or an image; cells read it as builtin/<file>.");
  box.replaceChildren(
    h("div", { class: "nb-res-head" }, h("b", {}, "Built-in files"),
      v.files?.length ? h("span", { class: "muted small" }, `${v.files.length} of ${NB_RES_MAX_FILES}`) : null),
    list,
    h("div", { class: "nb-res-actions" }, add,
      btn("Open folder", "folderOpen", () => nbResReveal(nb), "tool link small"), input),
    howItWorks("nbResources", {
      reads: "The files in this notebook's resources folder on this PC.",
      changes: "Add files copies them into <notebook>.resources/builtin beside the notebook, never over a file of the same name. " +
        "Rename and Delete change one file there; Open folder makes the folder if it isn't there yet. " +
        "Renaming or moving the notebook does the same to the folder; deleting the notebook leaves it on this PC. Cells that write to builtin/ write there too. Nothing in Fabric or OneLake changes.",
      results: "Cells read the files as builtin/<file>; notebookutils.nbResPath is the folder. They stay on this PC: publishing doesn't upload them.",
      anchor: "notebook-resources",
    }));
}

// The Resources tab's files section (renderExplorer in notebook.js puts it above the snippets).
function nbResourcesSection(nb) {
  const box = h("div", { class: "nb-res" });
  nbResFill(nb, box);
  if (nbResView(nb).files === null) nbResLoad(nb, box);
  return box;
}

registerFeature({
  id: "nbResources",
  commands: [{ label: "Show notebook resources", icon: "folder", keywords: "builtin files resources folder nbResPath",
    when: () => !!state.notebook, run: () => { state.showExplorer = true; state.explorerTab = "resources"; go("notebook"); } }],
});
