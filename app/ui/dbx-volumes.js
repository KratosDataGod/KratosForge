// Workspaces > Databricks > Volumes on this PC (Preview): the Unity Catalog volumes that Databricks notebooks and
// SQL cells on this PC use (engine databricks_volume_*, databricks_volumes.py). A volume is a folder in the app's data
// folder, named in code as /Volumes/main/<schema>/<volume>; nothing here reaches Databricks. Upload and Download copy
// one file at a time through the Windows file dialog (pick_file in the shell); Delete and Drop volume ask first in red,
// because nothing on this PC keeps a copy.
// Mockup: ux/uc-volumes-local/mockup.html (approved by Chris).

const dbxVol = { volume: "", parts: [], picked: new Set(), list: null, files: null, body: null };

const dbxVolKey = (v) => `${v.database}.${v.name}`;
const dbxVolFull = (v) => `main.${v.database}.${v.name}`;
const dbxVolHere = (v) => [v.path, ...dbxVol.parts].join("/");

// Synced folders | Volumes on this PC: the views of Workspaces > Databricks, under its tab.
function dbxViews(active, extra = null) {
  const views = [["databricks_sync", "Synced folders"], ["databricks_volumes", "Volumes on this PC"]];
  return h("div", { class: "row dbx-views" },
    h("div", { class: "seg", role: "tablist", "aria-label": "Databricks views" }, views.map(([view, label]) => {
      const b = h("button", { class: active === view ? "on" : null, role: "tab", "aria-selected": String(active === view),
        onclick: () => go(view) }, label);
      return view === "databricks_volumes" ? preview("ucVolumes", b) : b;
    })),
    h("span", { class: "grow" }), extra);
}

async function renderDatabricksVolumes() {
  const body = h("div", { class: "dbx-vol" }, loading("Reading the volumes on this PC…"));
  dbxVol.body = body;
  setView(h("div", { class: "page" }, h("h1", {}, "Workspaces"), workspacesTabs("databricks_sync"),
    dbxViews("databricks_volumes", btn("New volume", null, () => dbxVolCreate(), "tool outline")),
    h("div", { class: "dbx-vol-banner", role: "note" }, h("b", {}, "This PC"),
      h("span", {}, "Unity Catalog volumes for notebooks and SQL on this PC. They're folders here, not the workspace's volumes, and nothing is uploaded to Databricks. ",
        learnMore("workspaces"))),
    body));
  const out = await attempt(() => rpc("databricks_volumes"), "Couldn't read the volumes on this PC");
  if (state.view !== "databricks_volumes" || dbxVol.body !== body) return;
  if (!out) { body.replaceChildren(h("p", { class: "muted-text" }, "The volumes on this PC couldn't be read.")); return; }
  dbxVol.list = out.volumes || [];
  const vol = dbxVol.list.find((v) => dbxVolKey(v) === dbxVol.volume) || dbxVol.list[0] || null;
  if (!vol || dbxVolKey(vol) !== dbxVol.volume) Object.assign(dbxVol, { volume: vol ? dbxVolKey(vol) : "", parts: [] });
  dbxVol.files = null;
  if (vol) {
    dbxVol.files = await attempt(() => rpc("databricks_volume_files", { path: dbxVolHere(vol) }), `Couldn't list ${dbxVolHere(vol)}`) || { error: true };
    if (state.view !== "databricks_volumes" || dbxVol.body !== body) return;
  }
  const names = new Set((dbxVol.files?.entries || []).map((e) => e.path));
  for (const p of [...dbxVol.picked]) if (!names.has(p)) dbxVol.picked.delete(p);
  dbxVolDraw();
}

// Draws the page again from what was read; ticking a box or opening a menu reads nothing.
function dbxVolDraw() {
  const vol = (dbxVol.list || []).find((v) => dbxVolKey(v) === dbxVol.volume) || null;
  dbxVol.body?.replaceChildren(h("div", { class: "dbx-vol-split" }, dbxVolList(), dbxVolDetail(vol)));
}

function dbxVolPick(v) {
  Object.assign(dbxVol, { volume: dbxVolKey(v), parts: [] });
  dbxVol.picked.clear();
  renderDatabricksVolumes();
}

function dbxVolOpen(parts) {
  dbxVol.parts = parts;
  dbxVol.picked.clear();
  renderDatabricksVolumes();
}

function dbxVolList() {
  const list = dbxVol.list || [];
  const schemas = [...new Set(list.map((v) => v.database))];
  return h("section", { class: "card pad dbx-vol-list", "aria-label": "Volumes" }, h("h2", {}, "Volumes"),
    list.length ? h("ul", { class: "dbx-vol-tree" }, schemas.map((s) => [
      h("li", { class: "dbx-vol-schema" }, `main.${s}`),
      list.filter((v) => v.database === s).map((v) => {
        const on = dbxVolKey(v) === dbxVol.volume;
        return h("li", {}, h("button", { class: `dbx-vol-item${on ? " on" : ""}`, "aria-pressed": String(on), onclick: () => dbxVolPick(v) },
          h("span", { class: "grow" }, v.name), h("span", { class: "dbx-vol-count" }, `${v.items} ${v.items === 1 ? "item" : "items"}`)));
      })]))
      : h("div", { class: "dbx-vol-empty" }, h("b", {}, "No volumes yet"),
        "Create one with ", h("code", {}, "CREATE VOLUME main.sales.landing"), " in a SQL cell, or with New volume."));
}

function dbxVolDetail(vol) {
  if (!vol) {
    return h("section", { class: "card pad dbx-vol-detail" },
      h("div", { class: "dbx-vol-empty" }, h("b", {}, "Pick a volume"), "Volumes you create on this PC show here."));
  }
  const full = dbxVolFull(vol);
  const files = dbxVol.files || {};
  const entries = files.entries || [];
  const picked = entries.filter((e) => dbxVol.picked.has(e.path));
  const here = dbxVolHere(vol);
  const crumbs = [vol.name, ...dbxVol.parts].map((name, i, all) => [
    i < all.length - 1 ? h("button", { class: "tool link dbx-vol-crumb", onclick: () => dbxVolOpen(dbxVol.parts.slice(0, i)) }, name)
      : h("span", { class: "dbx-vol-crumb here", "aria-current": "location" }, name),
    h("span", { class: "muted-text" }, " / ")]);
  return h("section", { class: "card pad dbx-vol-detail", "aria-label": full },
    h("div", { class: "row" }, h("h2", { class: "mono dbx-vol-name" }, full), h("span", { class: "grow" }),
      h("span", { class: "dbx-vol-kind" }, "Managed volume"),
      h("button", { class: "tool small", title: "More actions", "aria-label": `Actions for ${full}`, "aria-haspopup": "menu",
        onclick: (e) => showMenu(e.currentTarget, [
          { label: "Rename", action: () => dbxVolRename(vol) },
          { label: vol.comment ? "Edit comment" : "Add comment", action: () => dbxVolComment(vol) },
          vol.comment ? { label: "Remove comment", action: () => dbxVolSetComment(vol, "") } : null,
          { label: "Drop volume", action: () => dbxVolDrop(vol) },
        ].filter(Boolean)) }, "⋯")),
    h("dl", { class: "dbx-vol-where" },
      h("dt", {}, "Path"), h("dd", { class: "mono" }, vol.path), h("dd", {}, btn("Copy path", null, () => copyText(vol.path, "Path"), "tool outline")),
      h("dt", {}, "On this PC"), h("dd", { class: "mono" }, vol.folder), h("dd", {}, btn("Show on this PC", null, () => dbxVolReveal(vol.path), "tool outline")),
      vol.comment ? [h("dt", {}, "Comment"), h("dd", {}, vol.comment), h("dd", {})] : null),
    h("div", { class: "row dbx-vol-bar" }, h("nav", { class: "dbx-vol-crumbs", "aria-label": "Folder" }, crumbs), h("span", { class: "grow" }),
      btn("Upload", null, () => dbxVolUpload(vol), "tool primary"),
      btn("New folder", null, () => dbxVolNewFolder(vol), "tool outline"),
      btn("Download", null, () => dbxVolDownload(picked), "tool outline",
        { disabled: !picked.length || picked.some((e) => e.is_dir) || null, title: picked.some((e) => e.is_dir) ? "Download copies files; open a folder to download what's in it." : null }),
      btn("Delete", null, () => dbxVolDelete(vol, picked), "tool outline danger", { disabled: !picked.length || null })),
    files.error ? h("p", { class: "muted-text" }, `${here} couldn't be listed.`)
      : entries.length ? dbxVolTable(entries, files.truncated)
      : h("div", { class: "dbx-vol-empty" }, h("b", {}, dbxVol.parts.length ? "This folder is empty" : "This volume is empty"),
        "Upload files, or write to ", h("code", {}, here), " from a notebook."));
}

function dbxVolTable(entries, truncated) {
  const tick = (e) => h("input", { type: "checkbox", "aria-label": `Select ${e.name}`, checked: dbxVol.picked.has(e.path) || null,
    onchange: (ev) => { if (ev.target.checked) dbxVol.picked.add(e.path); else dbxVol.picked.delete(e.path); dbxVolDraw(); } });
  return [h("div", { class: "table-wrap" }, h("table", { class: "dbx-vol-files" },
    h("thead", {}, h("tr", {}, h("th", { class: "tick" }), h("th", {}, "Name"), h("th", { class: "num" }, "Size"), h("th", {}, "Modified"))),
    h("tbody", {}, entries.map((e) => h("tr", { class: dbxVol.picked.has(e.path) ? "sel" : null },
      h("td", { class: "tick" }, tick(e)),
      h("td", { class: "mono" }, e.is_dir
        ? h("button", { class: "tool link dbx-vol-folder", onclick: () => dbxVolOpen([...dbxVol.parts, e.name]) }, icon("folder", 16), `${e.name}/`)
        : e.name),
      h("td", { class: "num" }, e.is_dir ? "" : sizeText(e.size)),
      h("td", {}, dbxVolWhen(e.modification_time))))))),
  truncated ? h("p", { class: "muted-text small" }, `The first ${entries.length} entries are shown. LIST in a SQL cell reads them all.`) : null];
}

// "Today 05:40", "Yesterday 18:02", or the date and time.
function dbxVolWhen(ms) {
  const d = readDate(ms);
  if (!d) return "";
  const days = Math.round((new Date().setHours(0, 0, 0, 0) - new Date(d).setHours(0, 0, 0, 0)) / 86400000);
  return days === 0 ? `Today ${timeText(d)}` : days === 1 ? `Yesterday ${timeText(d)}` : dateTimeText(d);
}

function dbxVolNameProblem(name) {
  return /[\\/]/.test(name) || name === "." || name === ".." ? "A name can't hold / or \\, or be . or .." : "";
}

function dbxVolReveal(path) {
  return attempt(() => rpc("databricks_volume_reveal", { path }), "Couldn't show the folder");
}

function dbxVolCreate() {
  const schema = h("input", { value: "default", "aria-label": "Schema", spellcheck: "false", maxlength: 255 });
  const name = h("input", { "aria-label": "Volume name", placeholder: "landing", spellcheck: "false", maxlength: 255 });
  const comment = h("input", { "aria-label": "Comment", maxlength: 1000 });
  const problem = h("p", { class: "error-text", hidden: true });
  const create = btn("Create", null, async () => {
    if (!schema.value.trim() || !name.value.trim()) { problem.textContent = "A volume needs a schema and a name."; problem.hidden = false; return; }
    create.disabled = true;
    try {
      const v = await rpc("databricks_volume_create", { schema: schema.value.trim(), volume: name.value.trim(), comment: comment.value.trim() || null });
      closeDialog();
      toast(`${dbxVolFull(v)} is ready at ${v.path}`);
      dbxVolPick(v);
    } catch (e) {
      problem.textContent = friendlyError(e).text;
      problem.hidden = false;
      create.disabled = false;
    }
  }, "tool primary");
  showDialog("New volume", h("div", { class: "dbx-vol-form" },
    h("p", { class: "text" }, "A managed volume in the catalog main: a folder on this PC that notebooks and SQL cells here read as /Volumes/main/<schema>/<volume>."),
    h("label", { class: "field" }, "Schema", schema), h("label", { class: "field" }, "Name", name),
    h("label", { class: "field" }, "Comment (optional)", comment), problem),
  [btn("Cancel", null, closeDialog, "tool outline"), create]);
  name.focus();
}

async function dbxVolRename(vol) {
  const name = await askText(`Rename ${dbxVolFull(vol)}`, "New name", vol.name, "Rename", dbxVolNameProblem);
  if (!name || name === vol.name) return;
  const v = await attempt(() => rpc("databricks_volume_rename", { schema: vol.database, volume: vol.name, new_name: name }), "Couldn't rename the volume");
  if (v) { toast(`Renamed. Code now reads it as ${v.path}.`); dbxVolPick(v); }
}

// Reads the comment again first: a notebook's COMMENT ON VOLUME may have changed it since the list was read.
async function dbxVolComment(vol) {
  const now = await attempt(() => rpc("databricks_volume_describe", { schema: vol.database, volume: vol.name }), "Couldn't read the volume");
  if (!now) return;
  const text = await askText(`Comment on ${dbxVolFull(vol)}`, "Comment", now.comment || "", "Save");
  if (text !== null && text !== now.comment) dbxVolSetComment(vol, text);
}

async function dbxVolSetComment(vol, comment) {
  if (await attempt(() => rpc("databricks_volume_set_comment", { schema: vol.database, volume: vol.name, comment: comment || null }), "Couldn't save the comment")) {
    renderDatabricksVolumes();
  }
}

async function dbxVolDrop(vol) {
  const full = dbxVolFull(vol);
  const ok = await confirmDialog(`Drop ${full}?`,
    ["It's removed with every file in it, from ", h("span", { class: "mono" }, vol.path), " and from its folder on this PC:"], "Drop volume",
    { danger: true, items: [h("span", { class: "mono" }, `${vol.name} (${vol.items} ${vol.items === 1 ? "item" : "items"})`)],
      after: "This can't be undone. A notebook or query that reads the volume will fail." });
  if (!ok) return;
  if (await attempt(() => rpc("databricks_volume_drop", { schema: vol.database, volume: vol.name, confirm: true }), "Couldn't drop the volume")) {
    toast(`${full} dropped`);
    Object.assign(dbxVol, { volume: "", parts: [] });
    dbxVol.picked.clear();
    renderDatabricksVolumes();
  }
}

async function dbxVolUpload(vol) {
  const source = await attempt(() => invoke("pick_file", { start: null, saveName: null }), "Couldn't show the Open dialog");
  if (!source) return;
  const here = dbxVolHere(vol);
  let out = await attempt(() => rpc("databricks_volume_upload", { source, path: here }), "Couldn't upload the file");
  if (out?.exists) {
    const name = out.path.split("/").pop();
    if (!(await confirmDialog(`Replace ${name}?`, ["A file of that name is already in ", h("span", { class: "mono" }, here), ". Uploading replaces it."],
      "Replace", { danger: true, after: "This can't be undone. A notebook or query that reads the file will read the new one." }))) return;
    out = await attempt(() => rpc("databricks_volume_upload", { source, path: here, overwrite: true }), "Couldn't upload the file");
  }
  if (out?.path) { toast(`Uploaded to ${out.path}`); renderDatabricksVolumes(); }
}

async function dbxVolNewFolder(vol) {
  const name = await askText("New folder", "Name", "", "Create", dbxVolNameProblem);
  if (!name) return;
  const out = await attempt(() => rpc("databricks_volume_new_folder", { path: `${dbxVolHere(vol)}/${name}` }), "Couldn't make the folder");
  if (out) { toast(`Made ${out.path}`); renderDatabricksVolumes(); }
}

// One file: the Save dialog, which asks itself before saving over a file. Several: a folder, and each file keeps
// its name; files already there are listed and kept unless you replace them.
async function dbxVolDownload(picked) {
  const files = picked.filter((e) => !e.is_dir);
  if (!files.length) return;
  if (files.length === 1) {
    const target = await attempt(() => invoke("pick_file", { start: null, saveName: files[0].name }), "Couldn't show the Save dialog");
    if (!target) return;
    const out = await attempt(() => rpc("databricks_volume_download", { path: files[0].path, save_to: target, overwrite: true }), "Couldn't download the file");
    if (out?.saved_to) toast(`Saved ${out.saved_to}`);
    return;
  }
  const folder = await attempt(() => invoke("pick_folder", { start: null }), "Couldn't show the folder dialog");
  if (!folder) return;
  const to = (e) => `${folder.replace(/[\\/]+$/, "")}\\${e.name}`;
  const there = [];
  let saved = 0;
  for (const e of files) {
    const out = await attempt(() => rpc("databricks_volume_download", { path: e.path, save_to: to(e) }), `Couldn't download ${e.name}`);
    if (out?.exists) there.push(e); else if (out?.saved_to) saved++;
  }
  if (there.length && await confirmDialog(`Replace ${there.length === 1 ? there[0].name : `${there.length} files`}?`,
    `Files of these names are already in ${folder}:`, "Replace", { danger: true, items: there.map((e) => e.name) })) {
    for (const e of there) {
      if (await attempt(() => rpc("databricks_volume_download", { path: e.path, save_to: to(e), overwrite: true }), `Couldn't download ${e.name}`)) saved++;
    }
  }
  if (saved) toast(`Saved ${saved} ${saved === 1 ? "file" : "files"} to ${folder}`);
}

async function dbxVolDelete(vol, picked) {
  if (!picked.length) return;
  const here = dbxVolHere(vol);
  const one = picked.length === 1;
  const ok = await confirmDialog(`Delete ${one ? picked[0].name : `${picked.length} items`}?`,
    ["It's removed from ", h("span", { class: "mono" }, here), " and from its folder on this PC:"], "Delete",
    { danger: true, items: picked.map((e) => h("span", { class: "mono" }, e.is_dir ? `${e.name}/ (and everything in it)` : `${e.name} (${sizeText(e.size)})`)),
      after: `This can't be undone. A notebook or query that reads ${one ? "the file" : "these files"} will fail.` });
  if (!ok) return;
  if (await attempt(() => rpc("databricks_volume_delete", { paths: picked.map((e) => e.path), confirm: true }), "Couldn't delete")) {
    toast(one ? `${picked[0].name} deleted` : `${picked.length} items deleted`);
    dbxVol.picked.clear();
    renderDatabricksVolumes();
  }
}

registerFeature({
  id: "ucVolumes", preview: true, views: { databricks_volumes: renderDatabricksVolumes },
  commands: [{ label: "Volumes on this PC", icon: "folder", keywords: "databricks unity catalog uc volume volumes files dbfs upload download",
    run: () => go("databricks_volumes") }],
});
