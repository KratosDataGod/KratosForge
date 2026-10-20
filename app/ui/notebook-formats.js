// Notebook export and import in the formats Fabric, Databricks and Jupyter use
// (notebook_formats.py). Export saves a new file in Downloads and never replaces one; the notebook
// isn't saved or changed. Import reads a file the person picks, shows what it would create, then
// writes each notebook in it as a new notebook under a free name: no notebook is replaced and the
// picked file is only read.

const NBF_EXPORTS = [
  { format: "ipynb", label: "Jupyter notebook (.ipynb)" },
  { format: "ipynb_outputs", label: "Jupyter notebook with outputs (.ipynb)" },
  { format: "fabric_py", label: "Fabric source (.py)" },
  { format: "databricks_py", label: "Databricks source (.py)" },
  { format: "html", label: "Web page (.html)" },
];

// A cell's language by the first line the engine reports (its Fabric cell magic, or python).
const NBF_LANGUAGES = { python: "Python", "%%sql": "SQL", "%%spark": "Scala", "%%sparkr": "R", "%%sh": "Shell", "%%html": "HTML" };

const NBF_ACCEPT = ".ipynb,.py,.sql,.scala,.dbc";
const NBF_MAX_BYTES = 64 * 1024 * 1024;

// What the export sends: the cells as on screen, with each code cell's outputs and run count.
function nbfExportPayload(nb, format) {
  return { name: nb.name, format, lakehouse_ref: nb.lakehouse || null,
    cells: nb.cells.map((c) => ({ type: c.type, source: c.source, meta: c.meta, cell_id: c.cell_id,
      outputs: c.type === "code" ? c.outputs || [] : [], count: typeof c.count === "number" ? c.count : null })) };
}

function nbfExportMenu() {
  return [{ info: "Saves a new file in your Downloads folder. A file already there is never replaced, and the notebook isn't saved or changed." },
    ...NBF_EXPORTS.map((x) => ({ label: x.label, icon: "arrowDownload", action: () => nbfExport(x.format) }))];
}

function nbfExportButton() {
  return previewMarked("nbFormats", btn("Export", "arrowDownload", (e) => showMenu(e.currentTarget, nbfExportMenu()), "tool",
    { title: "Save this notebook as .ipynb, Fabric or Databricks source, or a web page" }));
}

async function nbfExport(format) {
  const nb = state.notebook;
  if (!nb) return;
  const out = await attempt(() => rpc("notebook_export", nbfExportPayload(nb, format)), "Could not export the notebook");
  if (out) toast(`Saved ${nb.name} to ${out.path}`);
}

// An ArrayBuffer as base64, a chunk at a time so a large archive doesn't overflow the call stack.
function nbfBase64(buffer) {
  const bytes = new Uint8Array(buffer);
  let text = "";
  for (let i = 0; i < bytes.length; i += 0x8000) text += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  return btoa(text);
}

function nbfPlural(n, word) {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}

// One line per notebook the import would create.
function nbfCheckRows(check) {
  return check.notebooks.map((n) => h("tr", {},
    h("td", {}, n.name, n.name !== n.source_name ? h("div", { class: "muted-text" }, `${n.source_name} is taken, so it gets the next free name.`) : null),
    h("td", { class: "muted-text" }, n.folder || ""),
    h("td", {}, `${nbfPlural(n.cells, "cell")}${n.markdown ? `, ${n.markdown} markdown` : ""}`),
    h("td", { class: "muted-text" }, (n.languages || []).map((l) => NBF_LANGUAGES[l] || l).join(", "))));
}

function nbfImportDialog() {
  const picked = { name: "", content: "", encoding: "text" };
  const out = h("div", { class: "nbf-preview" });
  const input = h("input", { type: "file", accept: NBF_ACCEPT, "aria-label": "Notebook file" });
  const go = btn("Import", "arrowUpload", async () => {
    go.disabled = true;
    const res = await attempt(() => rpc("notebook_import", { file_name: picked.name, content: picked.content, encoding: picked.encoding }), "Not imported");
    if (!res) { go.disabled = false; return; }
    closeDialog();
    toast(res.created.length === 1 ? `Imported ${res.created[0]}` : `Imported ${res.created.length} notebooks: ${res.created.join(", ")}`);
    if (res.created.length) openNotebook(res.created[0]);
  }, "tool primary");
  go.disabled = true;
  input.onchange = async () => {
    go.disabled = true;
    const f = input.files[0];
    if (!f) return;
    if (f.size > NBF_MAX_BYTES) { fill(out, h("p", { class: "error-text" }, "The file is larger than 64 MB, so it can't be imported.")); return; }
    const dbc = /\.dbc$/i.test(f.name);
    Object.assign(picked, { name: f.name, encoding: dbc ? "base64" : "text", content: dbc ? nbfBase64(await f.arrayBuffer()) : await f.text() });
    fill(out, h("p", { class: "muted-text" }, "Reading the file..."));
    const check = await attempt(() => rpc("notebook_import_check", { file_name: picked.name, content: picked.content, encoding: picked.encoding }), "Not read");
    if (!check) { fill(out); return; }
    fill(out, h("p", { class: "text" }, `${check.format}: ${nbfPlural(check.notebooks.length, "new notebook")} will be created.`),
      h("table", { class: "list" }, h("thead", {}, h("tr", {}, h("th", {}, "New notebook"), h("th", {}, "Folder in the file"),
        h("th", {}, "Cells"), h("th", {}, "Languages"))), h("tbody", {}, nbfCheckRows(check))));
    go.disabled = !check.notebooks.length;
  };
  const overlay = showDialog("Import a notebook", h("div", { class: "scroll" },
    h("p", { class: "text" }, "A Jupyter notebook (.ipynb), Fabric or Databricks notebook source (.py, .sql, .scala), a percent-format script (# %%), or a Databricks archive (.dbc) with any number of notebooks."),
    h("label", { class: "field" }, "File", input),
    out,
    howItWorks("nbFormatsImport", {
      reads: "The file you pick, on this PC. It is only read, never changed.",
      changes: "Writes one new notebook file (.ipynb) for each notebook in the file, in the app's notebooks folder. A notebook that already has the name keeps it; the new one gets the next free name, such as Sales (2). Nothing is sent to Fabric.",
      results: "The first new notebook opens. All of them are listed under Recent notebooks.",
      anchor: "import-and-export-notebooks-preview" })),
  [btn("Cancel", null, closeDialog, "tool outline"), go]);
  const head = overlay.querySelector(".dialog-head");
  head.insertBefore(previewTag(), head.lastChild);
  return overlay;
}

registerFeature({
  id: "nbFormats",
  commands: [
    { label: "Import a notebook file", icon: "arrowUpload", keywords: "import ipynb jupyter fabric databricks dbc py source percent open file", run: nbfImportDialog },
    ...NBF_EXPORTS.map((x) => ({ label: `Export notebook as ${x.label}`, icon: "arrowDownload", keywords: "export save download ipynb fabric databricks html",
      when: () => !!state.notebook && state.view === "notebook", run: () => nbfExport(x.format) })),
  ],
});
