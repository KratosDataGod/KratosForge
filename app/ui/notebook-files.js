// Notebook files (NB-001, NB-021): All notebooks (reached from Home), search inside notebooks,
// open a notebook file from anywhere, save a copy, delete to Recently deleted and restore, Edit on
// this PC for a Fabric notebook, and the check before a Publish that would replace changes made in
// Fabric. The engine side is notebook_files_service.py; nothing here replaces a notebook.

const nbFiles = { query: "", savedIn: null, namesOnly: false, seq: 0, timer: null, collapsed: new Set() };
const NB_FILE_MAX_BYTES = 20 * 1024 * 1024;

// ---- pure helpers (ui.test.cjs)

// The "Saved in" chips: All, This PC, then each repository, with how many notebooks each holds.
function nbFilesPlaces(list) {
  const repos = new Map();
  for (const n of list) if (n.place) repos.set(n.place.root, { key: n.place.root, label: n.place.label, count: (repos.get(n.place.root)?.count || 0) + 1 });
  return [{ key: null, label: "All", count: list.length }, { key: "pc", label: "This PC", count: list.filter((n) => !n.place).length }, ...repos.values()];
}

function nbFilesShown(list, savedIn) {
  if (!savedIn) return list;
  return list.filter((n) => (savedIn === "pc" ? !n.place : n.place?.root === savedIn));
}

// A search hit's line as [before, match, after], so the match can be marked.
function nbHitParts(hit) {
  return [hit.text.slice(0, hit.start), hit.text.slice(hit.start, hit.end), hit.text.slice(hit.end)];
}

function nbHitCount(result) {
  return (result?.notebooks || []).reduce((n, nb) => n + nb.hits.length + (nb.name_match && !nb.hits.length ? 1 : 0), 0);
}

// One line for each change between Fabric's copy and the last publish (meta/diff.py notebook_changes).
function nbChangeTitle(c) {
  if (c.kind === "reordered") return "Cells were reordered";
  if (c.kind === "other item") return "A different notebook in Fabric now has this name (the one published from here was renamed or deleted)";
  const where = c.position ? `Cell ${c.position}` : "A cell";
  return `${where}: ${{ added: "added", removed: "removed", changed: "changed" }[c.kind] || c.kind}`;
}

function nbPlaceLabel(place) {
  return place ? place.label : "This PC";
}

// ---- Folders for notebooks (notebook_folders_service.py): "Sales/Daily load" is Daily load in Sales.

const NB_FOLDER_MAX_DEPTH = 3;

function nbLeaf(name) {
  return name.slice(name.lastIndexOf("/") + 1);
}

function nbFolderOf(name) {
  const i = name.lastIndexOf("/");
  return i < 0 ? "" : name.slice(0, i);
}

// The notebooks and folders (notebook_folders rows, empty ones too) as a tree, per place:
// {folders: [{key, path, name, place, folders, notebooks, count}], loose: [notebooks in no folder]}.
// count is every notebook in the folder and its subfolders.
function nbFolderTree(list, folders) {
  const roots = [];
  const loose = [];
  const nodes = new Map();
  const node = (place, path) => {
    const key = `${place?.root || ""}|${path}`;
    if (!nodes.has(key)) {
      const n = { key, path, name: nbLeaf(path), place: place || null, folders: [], notebooks: [], count: 0 };
      nodes.set(key, n);
      const parent = nbFolderOf(path);
      (parent ? node(place, parent).folders : roots).push(n);
    }
    return nodes.get(key);
  };
  for (const f of folders || []) node(f.place, f.path);
  for (const n of list) {
    if (n.name.includes("/")) node(n.place, nbFolderOf(n.name)).notebooks.push(n);
    else loose.push(n);
  }
  const byName = (a, b) => a.name.localeCompare(b.name, undefined, { sensitivity: "base" }) || (a.place?.label || "").localeCompare(b.place?.label || "");
  const finish = (f) => {
    f.folders.sort(byName);
    f.count = f.notebooks.length + f.folders.reduce((sum, c) => sum + finish(c), 0);
    return f.count;
  };
  roots.sort(byName);
  roots.forEach(finish);
  return { folders: roots, loose };
}

// The folder paths a notebook can move to: those in its own place, with "" (no folder) first.
function nbFolderChoices(folders, place) {
  const root = place?.root || null;
  return ["", ...(folders || []).filter((f) => (f.place?.root || null) === root).map((f) => f.path).sort((a, b) => a.localeCompare(b))];
}

// The name a notebook gets in a folder ("" for none).
function nbNameInFolder(name, folder) {
  return folder ? `${folder}/${nbLeaf(name)}` : nbLeaf(name);
}

function nbFolderDepth(path) {
  return path ? path.split("/").length : 0;
}

// ---- All notebooks

function renderAllNotebooks() {
  const results = h("div", { class: "nbf-results" }, h("p", { class: "muted-text" }, "Loading notebooks..."));
  const chips = h("div", { class: "row wrap nbf-chips", role: "group", "aria-label": "Saved in" });
  const deletedBox = h("div", {}, h("p", { class: "muted-text small" }, "Loading..."));
  const input = h("input", { type: "search", value: nbFiles.query, placeholder: "Search names and code in cells",
    "aria-label": "Search notebook names and code",
    oninput: (e) => { nbFiles.query = e.target.value; clearTimeout(nbFiles.timer); nbFiles.timer = setTimeout(() => nbFilesDraw(results, chips), 250); } });
  setView(h("div", { class: "page nbf-page" },
    h("h1", {}, "All notebooks"),
    pageIntro("Every notebook on this PC and in your notebooks repositories. Fabric notebooks are in Workspaces: select one there and use Edit on this PC. ", learnMore("all-notebooks")),
    h("div", { class: "row wrap nbf-bar" },
      btn("New notebook", "notebook", () => newNotebook(), "tool primary"),
      btn("New folder", "folder", () => nbNewFolderDialog(null, () => nbFilesDraw(results, chips)), "tool outline"),
      btn("Open a notebook file…", "folderOpen", nbOpenFile, "tool outline"),
      h("div", { class: "spacer" }),
      h("label", { class: "nbf-search" }, icon("search", 16), input)),
    chips, results,
    foldable("nbfiles.deleted", h("h2", {}, "Recently deleted"), deletedBox),
    nbFoldersHowItWorks()));
  nbFilesDraw(results, chips);
  nbFilesDrawDeleted(deletedBox);
  if (nbFiles.query) input.focus();
}

async function nbFilesDraw(results, chips) {
  const seq = ++nbFiles.seq;
  const [list, folderOut] = await Promise.all([rpc("notebook_listing").catch(() => null), rpc("notebook_folders").catch(() => null)]);
  if (seq !== nbFiles.seq || !results.isConnected) return;
  if (!list) { results.replaceChildren(h("p", { class: "error-text" }, "Couldn't list notebooks.")); return; }
  const places = nbFilesPlaces(list);
  if (nbFiles.savedIn && !places.some((p) => p.key === nbFiles.savedIn)) nbFiles.savedIn = null;
  const chip = (on, label, onclick) => h("button", { class: `chip${on ? " on" : ""}`, "aria-pressed": String(on), onclick }, label);
  const redraw = () => nbFilesDraw(results, chips);
  chips.replaceChildren(...[  // replaceChildren writes a null as the text "null": leave them out
    nbFiles.query ? h("span", { class: "muted-text small" }, "Search in") : null,
    nbFiles.query ? chip(!nbFiles.namesOnly, "Names and code", () => { nbFiles.namesOnly = false; redraw(); }) : null,
    nbFiles.query ? chip(nbFiles.namesOnly, "Names only", () => { nbFiles.namesOnly = true; redraw(); }) : null,
    places.length > 2 ? h("span", { class: "muted-text small" }, "Saved in") : null,
    ...(places.length > 2 ? places.map((p) => chip(nbFiles.savedIn === p.key, `${p.label} ${p.count}`, () => { nbFiles.savedIn = p.key; redraw(); })) : [])].filter(Boolean));
  const folders = (folderOut?.folders || []).filter((f) => !nbFiles.savedIn || (nbFiles.savedIn === "pc" ? !f.place : f.place?.root === nbFiles.savedIn));
  if (!nbFiles.query.trim()) { results.replaceChildren(nbFilesTable(nbFilesShown(list, nbFiles.savedIn), redraw, folders, folderOut?.folders || [])); return; }
  const open = state.notebook?.dirty ? { name: state.notebook.name, cells: state.notebook.cells.map((c) => ({ type: c.type, source: c.source, cell_id: c.cell_id })) } : null;
  results.replaceChildren(h("p", { class: "muted-text" }, "Searching..."));
  const found = await rpc("notebook_search", { query: nbFiles.query, names_only: nbFiles.namesOnly, saved_in: nbFiles.savedIn, open_notebook: open })
    .catch((err) => ({ error: friendlyError(err).text }));
  if (seq !== nbFiles.seq || !results.isConnected) return;
  results.replaceChildren(nbFilesFound(found));
}

// The list, grouped by folder (collapsible, with counts) when there are any folders.
function nbFilesTable(list, redraw, folders = [], allFolders = folders) {
  if (!list.length && !folders.length) return h("p", { class: "muted-text" }, "No notebooks here yet. Create one, or open a notebook file.");
  const row = (n, depth = 0) => h("tr", { class: depth ? "nbf-in-folder" : null },
    h("td", { style: depth ? `padding-left:${12 + depth * 20}px` : null },
      h("button", { class: "link-btn", title: n.name, onclick: () => openNotebook(n.name) }, icon("notebook", 16), depth ? nbLeaf(n.name) : n.name)),
    h("td", {}, nbPlaceLabel(n.place)),
    h("td", { class: n.published_to ? "" : "muted-text" }, n.published_to ? n.published_to.workspace_name || "Fabric" : "Not published"),
    h("td", { class: "muted-text" }, when(new Date(n.modified * 1000).toISOString())),
    h("td", { class: "nbf-actions" }, h("button", { class: "tool small", "aria-label": `More actions for ${n.name}`, "aria-haspopup": "menu",
      onclick: (e) => showMenu(e.currentTarget, nbFilesMenu(n, redraw, allFolders)) }, "⋯")));
  const head = h("thead", {}, h("tr", {}, h("th", {}, "Name"), h("th", {}, "Saved in"), h("th", {}, "Published to"), h("th", {}, "Modified"),
    h("th", {}, h("span", { class: "sr-only" }, "Actions"))));
  const body = h("tbody");
  const table = h("div", { class: "card" }, h("table", { class: "list nbf-table" }, head, body));
  const paint = () => {
    if (!folders.length) { body.replaceChildren(...list.map((n) => row(n))); return; }
    const tree = nbFolderTree(list, folders);
    const rows = [];
    const group = (key, label, count, depth, menu, place) => {
      const open = !nbFiles.collapsed.has(key);
      rows.push(h("tr", { class: "nbf-group" },
        h("td", { colspan: 4, style: `padding-left:${8 + depth * 20}px` },
          h("button", { class: "nbf-fold", "aria-expanded": String(open), onclick: () => {
            if (open) nbFiles.collapsed.add(key); else nbFiles.collapsed.delete(key);
            paint();
          } }, chevron(open), menu ? icon("folder", 16) : null, h("span", { class: "nbf-group-name" }, label),
          place ? h("span", { class: "muted-text small" }, place.label) : null,
          h("span", { class: "nbf-count", title: `${count} notebook${count === 1 ? "" : "s"}` }, String(count)))),
        h("td", { class: "nbf-actions" }, menu ? h("button", { class: "tool small", "aria-label": `Folder actions for ${label}`, "aria-haspopup": "menu",
          onclick: (e) => showMenu(e.currentTarget, menu) }, "⋯") : null)));
      return open;
    };
    const showPlace = new Set(folders.map((f) => f.place?.root || "")).size > 1;
    const draw = (f, depth) => {
      if (!group(f.key, f.name, f.count, depth, nbFolderMenu(f, redraw), showPlace ? f.place || { label: "This PC" } : null)) return;
      f.folders.forEach((c) => draw(c, depth + 1));
      f.notebooks.forEach((n) => rows.push(row(n, depth + 1)));
    };
    tree.folders.forEach((f) => draw(f, 0));
    if (tree.loose.length && group("|", "Not in a folder", tree.loose.length, 0, null, null)) tree.loose.forEach((n) => rows.push(row(n, 1)));
    body.replaceChildren(...rows);
  };
  paint();
  return table;
}

function nbFilesMenu(n, redraw, folders = []) {
  return [
    { label: "Open", icon: "open", action: () => openNotebook(n.name) },
    { label: "Save a copy…", icon: "add", action: () => nbSaveCopyDialog(n.name, false).then((ok) => ok && redraw()) },
    { label: "Rename…", icon: "code", action: () => nbRenameFromList(n.name).then((ok) => ok && redraw()) },
    { label: "Move to folder…", icon: "folder", action: () => nbMoveToFolderDialog(n, folders).then((ok) => ok && redraw()) },
    { label: "Move to another place…", icon: "folder", action: async () => {
      const nb = await attempt(() => rpc("open_notebook", { name: n.name }), "Could not open the notebook");
      if (nb) { await moveNotebookDialog(nb); redraw(); }
    } },
    { label: "Delete…", icon: "delete", action: () => nbDelete(n.name).then((ok) => ok && render()) },
  ];
}

function nbFilesFound(found) {
  if (found.error) return h("p", { class: "error-text" }, `Couldn't search: ${found.error}`);
  const books = found.notebooks || [];
  const head = h("p", { class: "muted-text small" }, books.length
    ? `${nbHitCount(found)} match${nbHitCount(found) === 1 ? "" : "es"} in ${books.length} of ${found.searched} notebooks${found.more ? " (showing the first ones)" : ""}`
    : `No notebook matches "${found.query}".`);
  const goTo = async (name, hit) => {
    await openNotebook(name);
    const nb = state.notebook;
    if (!nb || nb.name !== name) return;
    const n = nb.cells.findIndex((c) => c.cell_id === hit.cell_id) + 1 || hit.cell;
    depsGo(nb, n);
  };
  return h("div", { class: "nbf-found" }, head,
    books.map((b) => h("section", { class: "card pad nbf-book" },
      h("div", { class: "row" }, h("b", {}, b.name),
        h("span", { class: "muted-text small" }, [nbPlaceLabel(b.place), b.name_match ? "name matches" : "",
          b.hits.length ? `${b.hits.length}${b.more ? "+" : ""} in cells` : ""].filter(Boolean).join(" · ")),
        h("div", { class: "spacer" }), btn("Open", null, () => openNotebook(b.name), "tool outline small")),
      b.hits.map((hit) => {
        const [pre, mid, post] = nbHitParts(hit);
        return h("div", { class: "nbf-hit" },
          h("span", { class: "muted-text small" }, `Cell ${hit.cell}${hit.type === "markdown" ? " (Markdown)" : ""}`),
          h("code", { class: "nbf-line" }, pre, h("mark", {}, mid), post),
          h("button", { class: "link-btn", onclick: () => goTo(b.name, hit) }, `Go to cell ${hit.cell}`));
      }))),
    (found.problems || []).map((p) => h("p", { class: "muted-text small" }, `${p.name}: ${p.text}`)),
    h("p", { class: "muted-text small" }, "Searches the saved notebook files on this PC: cell code and Markdown, not outputs. The open notebook's unsaved edits are included. Fabric notebooks aren't searched."));
}

async function nbFilesDrawDeleted(box) {
  const rows = await rpc("notebook_deleted").catch(() => null);
  if (!box.isConnected) return;
  if (!rows) { box.replaceChildren(h("p", { class: "error-text" }, "Couldn't list deleted notebooks.")); return; }
  if (!rows.length) { box.replaceChildren(h("p", { class: "muted-text small" }, "Nothing deleted. A deleted notebook is kept here for 30 days.")); return; }
  box.replaceChildren(h("table", { class: "list" },
    h("thead", {}, h("tr", {}, h("th", {}, "Name"), h("th", {}, "Was in"), h("th", {}, "Deleted"), h("th", {}, "Kept until"), h("th", {}, h("span", { class: "sr-only" }, "Actions")))),
    h("tbody", {}, rows.map((r) => h("tr", {}, h("td", {}, r.name), h("td", {}, nbPlaceLabel(r.place)),
      h("td", { class: "muted-text" }, when(r.deleted_at)), h("td", { class: "muted-text" }, dateText(r.kept_until)),
      h("td", { class: "nbf-actions" }, btn("Restore", "undo", () => nbRestore(r).then((ok) => ok && render()), "tool outline small", { "aria-label": `Restore ${r.name}` })))))),
  h("p", { class: "muted-text small" }, "Kept on this PC for 30 days, then removed for good."));
}

// ---- Save a copy, rename, delete, restore

async function nbSaveCopyDialog(name, open = true) {
  const nb = state.notebook?.name === name ? state.notebook : null;
  if (nb?.dirty && !(await saveNotebook())) return false;
  const [list, placesOut] = await Promise.all([rpc("list_notebooks").catch(() => []), rpc("notebook_places").catch(() => null)]);
  const taken = new Set((list || []).map((n) => n.name));
  let suggestion = `${name} 2`;
  for (let i = 3; taken.has(suggestion); i++) suggestion = `${name} ${i}`;
  const places = placesOut?.places || [];
  const current = (list || []).find((n) => n.name === name)?.place?.root || "";
  const nameInput = h("input", { value: suggestion, "aria-label": "Name" });
  const select = h("select", { "aria-label": "Save in" }, places.map((p) => h("option", { value: p.root || "", selected: (p.root || "") === current ? true : null },
    p.root ? `${p.label} repository` : "This PC (the app's notebooks folder)")));
  const openBox = h("input", { type: "checkbox", checked: open ? true : null });
  const problem = h("p", { class: "error-text small", hidden: true });
  return new Promise((resolve) => {
    let settled = false;
    const done = (v) => { if (settled) return; settled = true; closeDialog(); resolve(v); };
    const submit = async () => {
      const newName = nameInput.value.trim();
      if (!newName) return;
      if (taken.has(newName)) { problem.textContent = `A notebook named ${newName} already exists. Pick another name.`; problem.hidden = false; return; }
      const out = await attempt(() => rpc("notebook_save_copy", { name, new_name: newName, place: select.value || null }), "Couldn't save a copy");
      if (!out) return;
      done(true);
      toast(`Saved a copy as ${newName}. ${name} is unchanged.`);
      if (openBox.checked) openNotebook(newName);
    };
    nameInput.addEventListener("input", () => { problem.hidden = true; });
    nameInput.addEventListener("keydown", (e) => { if (e.key === "Enter") submit(); });
    showDialog(`Save a copy of ${name}`, h("div", { class: "nbf-form" },
      h("p", { class: "muted-text" }, `The copy has the same cells, lakehouse and parameters. ${name} stays as it is.`),
      h("label", { class: "field" }, "Name", nameInput), problem,
      places.length > 1 ? h("label", { class: "field" }, "Save in", select) : null,
      h("label", { class: "check" }, openBox, "Open the copy"),
      h("ul", { class: "nbf-facts" },
        h("li", {}, "The copy isn't published. Its first Publish makes a new Fabric item under the copy's name."),
        h("li", {}, `${name} keeps its Fabric item, its schedules and its Prove history.`))),
    [btn("Cancel", null, () => done(false), "tool outline"), btn("Save copy", null, submit, "tool primary")], () => done(false));
    nameInput.select();
  });
}

async function nbRenameFromList(name) {
  const open = state.notebook?.name === name ? state.notebook : null;
  const newName = await askText("Rename notebook", "Name", name, "Rename");
  if (!newName || newName === name) return false;
  if (open && !(await saveNotebook())) return false;
  const ok = await attempt(() => rpc("rename_notebook", { name, new_name: newName }), "Could not rename");
  if (ok) { nbRenamedHere(name, newName); nbRenameWarning(ok.problems); }
  return !!ok;
}

// A notebook renamed or moved into a folder: the copy kept in memory (app.js) and the open notebook
// follow its new name.
function nbRenamedHere(name, newName) {
  const kept = openNotebooks.get(name);
  if (kept) { openNotebooks.delete(name); kept.name = newName; openNotebooks.set(newName, kept); }
  if (state.notebook?.name === name) { state.notebook.name = newName; renderChrome(); }
}

async function nbDelete(name) {
  const check = await attempt(() => rpc("notebook_delete_check", { name }), "Couldn't check the notebook");
  if (!check) return false;
  const facts = [
    check.fabric_item ? `Its Fabric item in ${check.fabric_item.workspace_name || "its workspace"} isn't deleted. Delete it in Fabric if you want it gone too.` : null,
    check.schedules.length ? `It has ${check.schedules.length} schedule${check.schedules.length > 1 ? "s" : ""} on this PC (${check.schedules.map((s) => s.text).filter(Boolean).join("; ") || "on this PC"}). ${check.schedules.length > 1 ? "They are" : "It is"} turned off, not deleted, so Restore brings ${check.schedules.length > 1 ? "them" : "it"} back.` : null,
    check.place ? `It's removed from the ${check.place.label} repository's folder, which shows as a change to commit there.` : null,
  ].filter(Boolean);
  const text = `${name} goes to Recently deleted on this PC. You can restore it for ${check.keep_days} days; after that it's gone.` +
    (facts.length ? `\n\n${facts.map((f) => `• ${f}`).join("\n")}` : "");
  if (!(await confirmDialog(`Delete ${name}?`, h("span", { style: "white-space:pre-line" }, text), "Delete", { danger: true }))) return false;
  const open = state.notebook?.name === name ? state.notebook : null;
  if (open?.dirty && !(await saveNotebook())) return false;  // what goes to Recently deleted is the latest
  const out = await attempt(() => rpc("notebook_delete", { name }), "Couldn't delete the notebook");
  if (!out) return false;
  openNotebooks.delete(name);
  if (state.notebook?.name === name) { disposeEditors(); state.notebook = null; if (state.view === "notebook") { go("notebooks"); } }
  renderChrome();
  toast(`Deleted ${name}. It's in Recently deleted on All notebooks until ${dateText(out.kept_until)}.`, false, { log: true });
  return true;
}

async function nbRestore(row) {
  let out = await rpc("notebook_restore", { id: row.id }).catch((err) => ({ error: friendlyError(err).text }));
  if (out.error && /already exists/.test(out.error)) {
    const name = await askText(`Restore ${row.name}`, "A notebook with that name exists now. Restore it as", `${row.name} (restored)`, "Restore");
    if (!name) return false;
    out = await attempt(() => rpc("notebook_restore", { id: row.id, name }), "Couldn't restore the notebook");
    if (!out) return false;
  } else if (out.error) {
    toast(`Couldn't restore the notebook: ${out.error}`, true);
    return false;
  }
  const notes = [out.moved_to_pc ? "Its repository isn't open any more, so it's back in the app's own folder." : "",
    out.schedules_on?.length ? "Its schedule on this PC is on again." : "",
    out.schedules_left_off?.length ? "Its schedule on this PC stays off: it runs the notebook by its old name." : ""].filter(Boolean);
  toast(`Restored ${out.name}. ${notes.join(" ")}`.trim(), false, { log: true });
  return true;
}

// ---- Folders: new, move a notebook into one, rename, delete (empty only)

function nbFolderMenu(f, redraw) {
  return [
    { label: "New notebook here", icon: "notebook", action: () => newNotebook(null, { folder: f.path, place: f.place?.root || null }) },
    nbFolderDepth(f.path) < NB_FOLDER_MAX_DEPTH ? { label: "New folder inside…", icon: "folder", action: () => nbNewFolderDialog(f, redraw) } : null,
    { label: "Rename folder…", icon: "code", action: () => nbRenameFolderDialog(f, redraw) },
    { label: "Delete folder…", icon: "delete", action: () => nbDeleteFolder(f, redraw) },
  ].filter(Boolean);
}

// The dialog's error line: what the engine said, in place, so the person can fix it and try again.
function nbFolderProblem() {
  return h("p", { class: "error-text small", role: "alert", hidden: true });
}

// What to do about each part kept by name that didn't follow a moved notebook (the file moved).
const NB_RENAME_FIX = {
  session: "Close and reopen the notebook to start its session again.",
  environment: "Pick its environment again on the notebook's status bar.",
  "Fabric link": "Publish it once to link it to its Fabric item again.",
  versions: "Its earlier versions are still kept under the old name in the app's notebook-versions folder.",
  schedules: "Open Monitor > KratosForge > Schedules and point its schedule on this PC at the new name.",
};

// After a move or folder rename: a warning naming what didn't follow, and what to do. Pure (ui.test.cjs).
function nbRenameProblemLines(problems) {
  return (problems || []).map((p) => `${p.notebook} (was ${p.was}): its ${p.step} didn't follow (${p.error}). ${NB_RENAME_FIX[p.step] || ""}`.trim());
}

function nbRenameWarning(problems) {
  const lines = nbRenameProblemLines(problems);
  if (!lines.length) return;
  showDialog("Moved, but some things didn't follow", h("div", { class: "nbf-form" },
    h("div", { class: "nbf-warn", role: "alert" }, h("p", {}, "The notebook files moved. These still use the old names:"),
      h("ul", { class: "nbf-facts" }, lines.map((l) => h("li", {}, l))))),
  [btn("OK", null, closeDialog, "tool primary")]);
}

function nbShowProblem(el, err) {
  el.textContent = friendlyError(err).text;
  el.hidden = false;
}

async function nbNewFolderDialog(parent, redraw) {
  const places = parent ? [] : ((await rpc("notebook_places").catch(() => null))?.places || []);
  const nameInput = h("input", { "aria-label": "Folder name", placeholder: "Sales" });
  const select = h("select", { "aria-label": "Make it in" }, places.map((p) => h("option", { value: p.root || "" }, p.root ? `${p.label} repository` : "This PC (the app's notebooks folder)")));
  const problem = nbFolderProblem();
  const where = parent ? `inside ${parent.path}` : "";
  const make = async () => {
    const name = nameInput.value.trim();
    if (!name) return;
    const path = parent ? `${parent.path}/${name}` : name;
    const place = parent ? parent.place?.root || null : select.value || null;
    const out = await rpc("notebook_folder_create", { path, place }).catch((err) => { nbShowProblem(problem, err); return null; });
    if (!out) return;
    closeDialog();
    toast(`Made the folder ${out.path}. Use Move to folder on a notebook's menu to put notebooks in it.`);
    redraw?.();
  };
  nameInput.addEventListener("input", () => { problem.hidden = true; });
  nameInput.addEventListener("keydown", (e) => { if (e.key === "Enter") make(); });
  showDialog(parent ? `New folder in ${parent.name}` : "New folder", h("div", { class: "nbf-form" },
    h("label", { class: "field" }, "Name", nameInput), problem,
    places.length > 1 ? h("label", { class: "field" }, "Make it in", select) : null,
    h("ul", { class: "nbf-facts" },
      h("li", {}, `Makes an empty folder ${where || "in the notebooks folder"} on this PC. Nothing in Fabric changes.`),
      h("li", {}, `Folders go ${NB_FOLDER_MAX_DEPTH} levels deep. Type Sales/Archive to make one inside another.`))),
  [btn("Cancel", null, closeDialog, "tool outline"), btn("Make folder", null, make, "tool primary")]);
  nameInput.focus();
}

async function nbMoveToFolderDialog(n, folders) {
  if (!folders?.length) folders = (await rpc("notebook_folders").catch(() => null))?.folders || [];
  const choices = nbFolderChoices(folders, n.place);
  if (choices.length < 2 && !n.name.includes("/")) {
    toast(`There are no folders in ${nbPlaceLabel(n.place)} yet. Use New folder on All notebooks first.`);
    return false;
  }
  const current = nbFolderOf(n.name);
  const select = h("select", { "aria-label": "Folder" }, choices.map((p) => h("option", { value: p, selected: p === current ? true : null }, p || "Not in a folder")));
  const becomes = h("b");
  const problem = nbFolderProblem();
  const update = () => { becomes.textContent = nbNameInFolder(n.name, select.value); problem.hidden = true; };
  select.addEventListener("change", update);
  update();
  return new Promise((resolve) => {
    let settled = false;
    const done = (v) => { if (settled) return; settled = true; closeDialog(); resolve(v); };
    const move = async () => {
      if (select.value === current) { done(false); return; }
      if (state.notebook?.name === n.name && state.notebook.dirty && !(await saveNotebook())) return;
      const out = await rpc("notebook_move_to_folder", { name: n.name, folder: select.value }).catch((err) => { nbShowProblem(problem, err); return null; });
      if (!out) return;
      nbRenamedHere(n.name, out.name);
      done(true);
      toast(`${nbLeaf(n.name)} is now ${out.name}.${out.schedules_moved?.length ? " Its schedules on this PC follow it." : ""}`);
      nbRenameWarning(out.problems);
    };
    showDialog(`Move ${nbLeaf(n.name)}`, h("div", { class: "nbf-form" },
      h("label", { class: "field" }, "Folder", select),
      h("p", { class: "muted-text small" }, "It becomes ", becomes, `, in ${nbPlaceLabel(n.place)}.`), problem,
      h("ul", { class: "nbf-facts" },
        h("li", {}, "Its resources folder, versions, comments, session and this PC's schedules go with it."),
        previewMarked("notebookFolderPublish", h("li", {}, `Publish still names its Fabric item ${nbLeaf(n.name)}, so it keeps the item it has. Fabric folders aren't used. `)),
        h("li", {}, "If that folder already has a notebook with this name, nothing moves."))),
    [btn("Cancel", null, () => done(false), "tool outline"), btn("Move", null, move, "tool primary")], () => done(false));
    select.focus();
  });
}

async function nbRenameFolderDialog(f, redraw) {
  const nameInput = h("input", { value: f.name, "aria-label": "Name" });
  const problem = nbFolderProblem();
  const rename = async () => {
    const newName = nameInput.value.trim();
    if (!newName || newName === f.name) { closeDialog(); return; }
    const open = state.notebook;
    if (open?.dirty && open.name.startsWith(`${f.path}/`) && !(await saveNotebook())) return;
    const out = await rpc("notebook_folder_rename", { path: f.path, new_name: newName, place: f.place?.root || null })
      .catch((err) => { nbShowProblem(problem, err); return null; });
    if (!out) return;
    for (const m of out.moved) nbRenamedHere(m.from, m.to);
    closeDialog();
    toast(`Renamed ${f.path} to ${out.new_path}. ${out.moved.length} notebook${out.moved.length === 1 ? "" : "s"} moved with it.`);
    nbRenameWarning(out.problems);
    redraw?.();
  };
  nameInput.addEventListener("input", () => { problem.hidden = true; });
  nameInput.addEventListener("keydown", (e) => { if (e.key === "Enter") rename(); });
  showDialog(`Rename folder ${f.name}`, h("div", { class: "nbf-form" },
    h("label", { class: "field" }, "Name", nameInput), problem,
    h("ul", { class: "nbf-facts" },
      h("li", {}, f.count ? `${f.count} notebook${f.count === 1 ? "" : "s"} move with it, and ${f.count === 1 ? "its" : "their"} names change to the new folder.` : "It has no notebooks in it."),
      h("li", {}, "Their resources, versions and this PC's schedules follow the new names."),
      h("li", {}, "Fabric items keep their names, so Publish updates the same items."),
      h("li", {}, "If a folder with the new name is already there, nothing is renamed."))),
  [btn("Cancel", null, closeDialog, "tool outline"), btn("Rename", null, rename, "tool primary")]);
  nameInput.select();
}

function nbFolderRefusal(f, text) {
  showDialog(`Can't delete ${f.name}`, h("div", { class: "nbf-form" }, h("div", { class: "nbf-warn" }, h("p", {}, text))),
    [btn("OK", null, closeDialog, "tool primary")]);
}

async function nbDeleteFolder(f, redraw) {
  if (f.count) {
    nbFolderRefusal(f, `${f.path} has ${f.count} notebook${f.count === 1 ? "" : "s"} in it. Move or delete ${f.count === 1 ? "it" : "them"} first: a folder is deleted only when it's empty, so no notebook is ever deleted with it.`);
    return false;
  }
  if (!(await confirmDialog(`Delete folder ${f.path}?`, "The empty folder is removed from this PC. No notebook is in it.", "Delete", { danger: true }))) return false;
  const out = await rpc("notebook_folder_delete", { path: f.path, place: f.place?.root || null }).catch((err) => ({ error: friendlyError(err).text }));
  if (out.error) { nbFolderRefusal(f, out.error); return false; }
  toast(`Deleted the folder ${f.path}.`);
  redraw?.();
  return true;
}

// "How this works": every write the folder actions make, named once on the page.
function nbFoldersHowItWorks() {
  const rows = [
    ["New folder", "Makes an empty folder in the place's notebooks folder (the app's own, or a notebooks repository's).", "It's already there, its name has a character a file name can't hold, ends in .resources, or is more than 3 levels deep."],
    ["New notebook here", "Saves Sales/Notebook 1.ipynb in that folder.", "Never: it takes the next free number."],
    ["Move to folder", "Renames the notebook's file and its resources folder into the folder, then moves its versions and points this PC's schedules, its session, environment and Fabric link at the new name.", "That folder already has a notebook with its name."],
    ["Rename folder", "Renames the folder on disk with everything in it, then moves each notebook's versions, schedules, session, environment and Fabric link to its new name.", "A folder or notebook with the new name is already there."],
    ["Delete folder", "Removes the folder.", "Anything is in it: notebooks, other folders or files."],
    ["Publish", "Names the Fabric item after the notebook's own name (Daily load), with no Fabric folder.", "Another notebook here already publishes to that item."],
  ];
  return foldable("nbfiles.folders-how", h("h2", {}, "How folders work"), h("div", {},
    h("p", { class: "muted-text small" }, "A folder is part of a notebook's name: Sales/Daily load is Daily load in the Sales folder. Everything below changes files on this PC; in a notebooks repository it shows as a change to commit. Nothing in Fabric changes. ", learnMore("notebook-folders")),
    h("table", { class: "list" }, h("thead", {}, h("tr", {}, h("th", {}, "Action"), h("th", {}, "What it writes"), h("th", {}, "Refuses when"))),
      h("tbody", {}, rows.map(([a, w, r]) => h("tr", {}, h("td", {}, h("b", {}, a)), h("td", {}, w), h("td", { class: "muted-text" }, r)))))));
}

// The notebook explorer's Notebooks tab (notebook.js): every notebook grouped by folder, to open one.
function nbExplorerNotebooks() {
  const box = h("div", { class: "nb-explorer-books" }, h("p", { class: "muted-text small" }, "Loading notebooks..."));
  Promise.all([rpc("list_notebooks").catch(() => null), rpc("notebook_folders").catch(() => null)]).then(([list, out]) => {
    if (!box.isConnected) return;
    if (!list) { box.replaceChildren(h("p", { class: "error-text small" }, "Couldn't list notebooks.")); return; }
    const tree = nbFolderTree(list, out?.folders || []);
    const paint = () => {
      const items = [];
      const book = (n, depth) => items.push(h("button", { class: `nb-explorer-book${state.notebook?.name === n.name ? " active" : ""}`, title: n.name,
        style: `padding-left:${6 + depth * 14}px`, onclick: () => openNotebook(n.name) }, icon("notebook", 14), nbLeaf(n.name)));
      const folder = (f, depth) => {
        const key = `x${f.key}`;
        const open = !nbFiles.collapsed.has(key);
        items.push(h("button", { class: "nb-explorer-folder", "aria-expanded": String(open), style: `padding-left:${2 + depth * 14}px`,
          onclick: () => { if (open) nbFiles.collapsed.add(key); else nbFiles.collapsed.delete(key); paint(); } },
          chevron(open), icon("folder", 14), h("span", {}, f.name), h("span", { class: "nbf-count" }, String(f.count))));
        if (!open) return;
        f.folders.forEach((c) => folder(c, depth + 1));
        f.notebooks.forEach((n) => book(n, depth + 1));
      };
      tree.folders.forEach((f) => folder(f, 0));
      tree.loose.forEach((n) => book(n, 0));
      box.replaceChildren(...(items.length ? items : [h("p", { class: "muted-text small" }, "No notebooks yet.")]),
        btn("All notebooks", null, () => go("notebooks"), "tool link small"));
    };
    paint();
  });
  return box;
}

// ---- Open a notebook file from anywhere on this PC

function nbOpenFile() {
  const input = h("input", { type: "file", accept: ".ipynb,application/x-ipynb+json", hidden: true });
  input.addEventListener("change", async () => {
    const file = input.files?.[0];
    input.remove();
    if (!file) return;
    if (file.size > NB_FILE_MAX_BYTES) { toast(`${file.name} is over 20 MB, so it isn't opened.`, true); return; }
    const content = await file.text();
    const check = await attempt(() => rpc("notebook_import_check", { file_name: file.name, content }), `Couldn't open ${file.name}`);
    if (check) nbOpenFileDialog(file, content, check);
  });
  document.body.append(input);
  input.click();
}

async function nbOpenFileDialog(file, content, check) {
  const places = (await rpc("notebook_places").catch(() => null))?.places || [];
  const nameInput = h("input", { value: check.name, "aria-label": "Name" });
  const select = h("select", { "aria-label": "Save in" }, places.map((p) => h("option", { value: p.root || "" }, p.root ? `${p.label} repository` : "This PC (the app's notebooks folder)")));
  const parts = [`${check.cells} cell${check.cells === 1 ? "" : "s"}: ${check.code} code, ${check.markdown} Markdown.`,
    check.outputs_dropped ? "Outputs in the file are left out; run it to see them here." : ""].filter(Boolean).join(" ");
  const add = async () => {
    const name = nameInput.value.trim();
    if (!name) return;
    const out = await attempt(() => rpc("notebook_import", { content, name, place: select.value || null }), `Couldn't add ${file.name}`);
    if (!out) return;
    closeDialog();
    toast(`Added ${name}. ${file.name} wasn't changed.`);
    openNotebook(name);
  };
  showDialog("Open a notebook file", h("div", { class: "nbf-form" },
    h("p", { class: "muted-text" }, "KratosForge adds a copy to your notebooks and opens it. The file you picked isn't changed or moved."),
    h("div", { class: "nbf-file" }, icon("document", 20), h("div", {}, h("b", {}, file.name), h("div", { class: "muted-text small" }, sizeText(file.size)))),
    h("label", { class: "field" }, "Name", nameInput),
    check.taken ? h("p", { class: "muted-text small" }, `You already have a notebook named ${check.file_name}, so this name is suggested.`) : null,
    places.length > 1 ? h("label", { class: "field" }, "Save in", select) : null,
    h("ul", { class: "nbf-facts" }, h("li", {}, parts),
      check.lakehouse ? h("li", {}, `Its default lakehouse, ${check.lakehouse.name}, is attached. Running it here reads that lakehouse's tables from OneLake, which uses capacity.`) : null,
      check.workspace?.workspace_name ? h("li", {}, `It publishes to ${check.workspace.workspace_name}, as saved in the file.`) : null),
    check.notes.length ? h("div", { class: "nbf-warn" }, check.notes.map((n) => h("p", {}, n))) : null),
  [btn("Cancel", null, closeDialog, "tool outline"), btn("Add and open", null, add, "tool primary")]);
  nameInput.select();
}

// ---- A Fabric notebook on this PC (Workspaces > a notebook item's preview)

async function nbEditFromFabric(item) {
  if (!(await ensureSignedIn())) return;
  toast(`Reading ${item.name} from Fabric...`);
  const check = await attempt(() => rpc("notebook_fabric_check", { workspace_id: item.workspace_id, item_id: item.item_id, name: item.name }),
    `Couldn't read ${item.name} from Fabric`);
  if (!check) return;
  if (check.here) { toast(`${check.here} is already on this PC; opening it.`); openNotebook(check.here); return; }
  if (check.taken) {
    toast(`You already have a notebook named ${item.name} that isn't this Fabric item. Rename it first, since Publish finds the Fabric item by its name.`, true);
    return;
  }
  const places = (await rpc("notebook_places").catch(() => null))?.places || [];
  const select = h("select", { "aria-label": "Save in" }, places.map((p) => h("option", { value: p.root || "" }, p.root ? `${p.label} repository` : "This PC (the app's notebooks folder)")));
  const copy = async () => {
    const out = await attempt(() => rpc("notebook_from_fabric", { workspace_id: item.workspace_id, item_id: item.item_id, name: item.name,
      workspace_name: item.workspace_name || "", place: select.value || null }), `Couldn't copy ${item.name} to this PC`);
    if (!out) return;
    closeDialog();
    toast(`${out.name} is on this PC. Publish updates the same Fabric item.`);
    openNotebook(out.name);
  };
  showDialog(`Edit ${item.name} on this PC`, h("div", { class: "nbf-form" },
    h("p", { class: "muted-text" }, "KratosForge reads the notebook's cells from Fabric and saves them as a notebook on this PC. Nothing in Fabric changes, and no Fabric job runs."),
    places.length > 1 ? h("label", { class: "field" }, "Save in", select) : null,
    h("ul", { class: "nbf-facts" },
      h("li", {}, `${check.cells} cells. It keeps the name ${item.name} and stays linked to this Fabric item, so Publish updates it instead of making a new one.`),
      check.lakehouse ? h("li", {}, `Its default lakehouse, ${check.lakehouse.name}, is attached. Running it here reads those tables from OneLake, which uses capacity.`) : null,
      h("li", {}, "If someone changes it in Fabric after this copy, Publish shows what changed and asks before replacing it.")),
    check.notes.length ? h("div", { class: "nbf-warn" }, check.notes.map((n) => h("p", {}, n))) : null,
    learnMore("notebook-files")),
  [btn("Cancel", null, closeDialog, "tool outline"), btn("Copy to this PC", null, copy, "tool primary")]);
}

// ---- Before Publish or Run in Fabric: did someone change the item in Fabric?

// Resolves to {replace} to go on (replace: true when the person chose to replace Fabric's
// changes), or null to stop.
async function nbFabricChangeGate(nb) {
  if (!(await saveNotebook())) return null;  // saved first, so a file changed outside the app is never published over
  const payload = publishPayload();
  const check = await attempt(() => rpc("notebook_publish_check", { name: payload.name, cells: payload.cells, lakehouse_ref: payload.lakehouse_ref,
    workspace: payload.workspace, location_ref: payload.location_ref }), `Couldn't check whether ${nb.name} changed in Fabric`);
  if (!check) return null;
  if (!check.changed) return { replace: false };
  const ws = publishWorkspaceName(nb);
  return new Promise((resolve) => {
    let settled = false;
    const done = (v) => { if (settled) return; settled = true; closeDialog(); resolve(v); };
    const keepTheirs = async () => {
      const copyName = `${nb.name} (Fabric)`;
      const out = await attempt(() => rpc("notebook_from_fabric", { workspace_id: publishWorkspace(nb)?.workspace_id,
        item_id: check.item_id, name: nb.name, workspace_name: ws, copy_name: copyName }), "Couldn't copy Fabric's version");
      if (!out) return;
      done(null);
      toast(`Saved Fabric's version as ${copyName}. Nothing was published; compare them, then publish again.`, false, { log: true });
    };
    // The confirm replaces this dialog, which would run its close (Cancel): settle first, so Cancel
    // there publishes nothing too.
    const replace = async () => {
      settled = true;
      const ok = await confirmDialog(`Replace the changes in Fabric?`, `Publishing replaces ${nb.name} in ${ws} with the notebook here. The changes made in Fabric are lost unless you keep a copy first.`, "Replace with mine", { danger: true });
      resolve(ok ? { replace: true } : null);
    };
    showDialog(`${nb.name} changed in Fabric`, h("div", { class: "nbf-form" },
      previewMarked("fabricEdit", h("p", { class: "muted-text" }, `Someone changed it in ${ws} after it was last published from here${check.published_at ? ` (${when(check.published_at)})` : ""}. Publishing now would replace those changes with yours.`)),
      h("h3", { class: "nbf-h" }, "Changed in Fabric"),
      check.fabric.map((c) => h("div", { class: "nbf-change" }, h("b", {}, nbChangeTitle(c)),
        c.diff?.length ? h("pre", { class: "nbf-diff" }, c.diff.slice(0, 12).map((l) => h("span", { class: l.startsWith("+") ? "add" : l.startsWith("-") ? "del" : "" }, l))) : null)),
      check.mine?.length ? h("p", { class: "muted-text small" }, `Here, ${check.mine.filter((c) => c.position).map((c) => `cell ${c.position}`).join(", ") || "the notebook"} changed since then. Nothing has been published yet.`) : null),
    [btn("Replace with mine", null, replace, "tool danger"), h("div", { class: "spacer" }),
      btn("Cancel", null, () => done(null), "tool outline"), btn("Copy Fabric's version here first", null, keepTheirs, "tool primary")], () => done(null));
  });
}

registerFeature({
  id: "notebookFiles", views: { notebooks: renderAllNotebooks },
  commands: [
    { label: "All notebooks", icon: "notebook", keywords: "list files find delete restore recently deleted", run: () => go("notebooks") },
    { label: "Search inside notebooks", icon: "search", keywords: "find code cells text grep", run: () => { go("notebooks"); setTimeout(() => document.querySelector(".nbf-search input")?.focus(), 0); } },
    { label: "Open a notebook file", icon: "folderOpen", keywords: "import ipynb jupyter upload", run: nbOpenFile },
    { label: "Save a copy of this notebook", icon: "add", keywords: "save as duplicate", run: () => state.notebook ? nbSaveCopyDialog(state.notebook.name) : toast("Open a notebook first.") },
    { label: "New notebook folder", icon: "folder", keywords: "folder group organize directory", run: () => nbNewFolderDialog(null, () => state.view === "notebooks" && render()) },
    { label: "Move this notebook to a folder", icon: "folder", keywords: "folder group organize move", run: () => state.notebook ? nbMoveToFolderDialog({ name: state.notebook.name, place: state.notebook.place || null }, null) : toast("Open a notebook first.") },
  ],
});
