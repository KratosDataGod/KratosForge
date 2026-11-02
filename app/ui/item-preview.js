// Item preview: click a table, file, notebook, lakehouse or any other item on the Workspaces
// pages and it shows in a side panel. Tables and files are read on this PC, a notebook's cells
// come from its Fabric definition, and every other item shows Fabric's own page in the panel.

// target: {kind: "table"|"file"|"item", ref (a lakehouse ref, for tables and files), schema,
// table, path, size, item: {workspace_id, workspace_name, item_id, type, name}}
const itemPreview = { target: null, parent: null, tab: "data", cache: new Map(), host: null, onChange: null };

function previewKey(t) {
  if (t.kind === "table") return `t:${t.ref.lakehouse_id}:${t.schema}.${t.table}`;
  if (t.kind === "file") return `f:${t.ref.lakehouse_id}:${t.path}`;
  return `i:${t.item.item_id}`;
}

// Previews are a preview feature themselves: hidden with the others, clicks do what they did before.
function itemPreviewOn() {
  return !PREVIEW_FEATURES.itemPreview || previewShown();
}

// parent: the preview this one was opened from (a lakehouse's table), so Back returns to it.
function selectPreview(target, parent = null) {
  if (!itemPreviewOn()) return false;
  itemPreview.target = target;
  itemPreview.parent = parent;
  itemPreview.tab = "data";
  itemPreview.onChange?.();
  return true;
}

// Focus goes back to the row that opened the preview, or to its lakehouse's row for a table or file
// picked inside a lakehouse's preview. The page draws its rows again, so the row is found by its key.
function closePreview() {
  const back = [itemPreview.target, itemPreview.parent].filter(Boolean).map(previewKey);
  itemPreview.target = null;
  itemPreview.parent = null;
  itemPreview.onChange?.();
  placeFabricView();
  back.map((k) => document.querySelector(`[data-preview="${CSS.escape(k)}"]`)).find(Boolean)?.focus();
}

// Escape closes the panel, unless a dialog, menu, the command bar, the activity tray or the How it
// works drawer is open: that Escape is theirs. Capture phase, so it sees them before they close.
document.addEventListener("keydown", (e) => {
  if (!isShortcut(e, "close") || e.defaultPrevented || !itemPreview.target || !itemPreview.host?.isConnected) return;
  if (document.getElementById("dialog-root")?.childElementCount || document.querySelector(".overlay, #menu, #palette")
    || activity.open || howView.open || document.body.classList.contains("fabric-full")) return;
  e.preventDefault();
  closePreview();
}, true);

function isPreviewed(target) {
  return !!itemPreview.target && previewKey(itemPreview.target) === previewKey(target);
}

// The page's layout: its own content on the left, the panel on the right while something is
// selected. onChange redraws the page so the selected row is marked.
function previewLayout(main, onChange) {
  const panel = h("aside", { class: "preview-panel card" });
  const wrap = h("div", { class: "preview-layout" }, main, panel);
  itemPreview.host = panel;
  itemPreview.onChange = () => { onChange?.(); drawPreview(panel); };
  drawPreview(panel);
  return wrap;
}

async function loadPreview(t) {
  const key = previewKey(t);
  if (itemPreview.cache.has(key)) return itemPreview.cache.get(key);
  let call = null;
  if (t.kind === "table") call = rpc("preview_table", { lakehouse_ref: t.ref, table: `${t.schema}.${t.table}` });
  else if (t.kind === "file") call = rpc("preview_file", { lakehouse_ref: t.ref, path: t.path, size: t.size || 0 });
  else if (t.item.type === "Notebook") call = rpc("preview_notebook", { workspace_id: t.item.workspace_id, item_id: t.item.item_id });
  if (!call) return null;
  const result = await call.then((data) => ({ data }), (err) => ({ error: friendlyError(err).text }));
  if (result.data) itemPreview.cache.set(key, result);
  return result;
}

function previewHead(title, type, meta, actions) {
  return h("div", { class: "preview-head" },
    h("div", { class: "preview-title" }, h("span", { class: "name", title }, title), h("span", { class: "pill-s neu" }, type), previewTag(),
    ),
    h("div", { class: "preview-actions" }, ...actions.filter(Boolean),
      itemPreview.parent ? btn("Back", null, () => selectPreview(itemPreview.parent), "tool small outline") : null,
      h("button", { class: "star", title: "Close preview", "aria-label": "Close preview", onclick: closePreview }, icon("dismiss", 16))),
    h("div", { class: "preview-meta muted-text" }, meta || ""));
}

async function drawPreview(panel) {
  const t = itemPreview.target;
  panel.classList.toggle("open", !!t);
  if (!t) { panel.replaceChildren(); placeFabricView(); return; }
  const key = previewKey(t);
  const body = h("div", { class: "preview-body" }, h("p", { class: "muted-text" }, "Loading the preview..."));

  if (t.kind === "item" && t.item.type === "Lakehouse") {
    const ref = lakehouseRef({ id: t.item.workspace_id, displayName: t.item.workspace_name }, { id: t.item.item_id, displayName: t.item.name });
    const tree = h("div", { class: "explorer-content", style: "padding:0" });
    panel.replaceChildren(previewHead(t.item.name, "Lakehouse", t.item.workspace_name, [
      btn("Open", null, () => openLakehouse(ref), "tool small outline"),
      btn("SQL query", null, () => openSqlQuery(ref), "tool small outline"),
      btn("Open in Fabric", "open", () => openInFabric(fabricItemLink(t.item)), "tool small outline")]),
    h("div", { class: "preview-body" }, h("p", { class: "muted-text" }, "Click a table or file to preview it."), tree));
    renderTree(tree, ref, null, (pick) => selectPreview({ ...pick, ref }, t));
    return;
  }

  if (t.kind === "item" && t.item.type !== "Notebook") {
    // Fabric's own page for reports, semantic models, pipelines and everything else.
    const slot = h("div", { class: "fabric-slot preview-fabric" },
      h("p", { class: "muted-text" }, "Opening Fabric. Sign in once and the app keeps it between launches."));
    const link = fabricItemLink(t.item);
    panel.replaceChildren(previewHead(t.item.name, typeLabel(t.item.type), t.item.workspace_name, [
      btn("Open full page", "open", () => openInFabric(link), "tool small outline")]), slot);
    fabricView.force = true;
    dockFabricView(slot, state.view, link);
    placeFabricView();
    return;
  }

  placeFabricView();  // a Fabric page shown for the last item goes away
  const title = t.kind === "table" ? `${t.schema}.${t.table}` : t.kind === "file" ? t.path.split("/").pop() : t.item.name;
  const type = t.kind === "table" ? "Table" : t.kind === "file" ? "File" : "Notebook";
  const actions = t.kind === "table" ? [
    btn("SQL query", null, () => { openSqlQuery(t.ref); }, "tool small outline"),
    btn("Open in notebook", null, () => newNotebook(t.ref), "tool small outline")]
    : t.kind === "item" ? [preview("fabricEdit", btn("Edit on this PC", "desktop", () => nbEditFromFabric(t.item), "tool small primary")),
      btn("Open in Fabric", "open", () => openInFabric(fabricItemLink(t.item)), "tool small outline")] : [];
  panel.replaceChildren(previewHead(title, type, t.kind === "item" ? t.item.workspace_name : t.kind === "file" ? sizeText(t.size) : "", actions), body);

  const result = await loadPreview(t);
  if (!itemPreview.target || previewKey(itemPreview.target) !== key || !panel.isConnected) return;
  if (!result) { body.replaceChildren(h("p", { class: "muted-text" }, "Nothing to preview.")); return; }
  if (result.error) {
    body.replaceChildren(h("p", { class: "error-text" }, `Could not preview it: ${result.error}`),
      btn("Try again", "restart", () => { itemPreview.cache.delete(key); drawPreview(panel); }, "tool small outline"));
    return;
  }
  const d = result.data;
  if (t.kind === "item") return drawNotebookPreview(panel, body, d);
  if (d.kind === "other") { body.replaceChildren(h("p", { class: "muted-text" }, "This kind of file can't be previewed. Data files (CSV, Parquet, JSON) and text files can.")); return; }
  if (d.kind === "too_big") { body.replaceChildren(h("p", { class: "muted-text" }, `This file is ${sizeText(d.size)}, too big to download for a preview.`)); return; }
  if (d.kind === "text") {
    body.replaceChildren(h("pre", { class: "preview-text" }, d.text), d.truncated ? h("p", { class: "muted-text" }, "Showing the start of the file.") : null);
    return;
  }
  drawRowsPreview(panel, body, d);
}

function drawRowsPreview(panel, body, d) {
  const meta = panel.querySelector(".preview-meta");
  const count = d.row_count == null ? "" : `${Number(d.row_count).toLocaleString()} rows  ·  `;
  if (meta) meta.textContent = `${count}${d.columns.length} columns  ·  read on this PC`;
  const tab = (id, label) => h("button", { class: `tab${itemPreview.tab === id ? " active" : ""}`, role: "tab", "aria-selected": String(itemPreview.tab === id),
    onclick: () => { itemPreview.tab = id; drawRowsPreview(panel, body, d); } }, label);
  const tabs = h("div", { class: "subtabs", role: "tablist", "aria-label": "Preview" }, tab("data", "Data"), tab("schema", "Schema"));
  if (itemPreview.tab === "schema") {
    body.replaceChildren(tabs, h("table", { class: "list preview-grid" },
      h("thead", {}, h("tr", {}, h("th", {}, "Column"), h("th", {}, "Type"))),
      h("tbody", {}, d.columns.map((c) => h("tr", {}, h("td", {}, c.name), h("td", { class: "muted-text" }, c.type))))));
    return;
  }
  const grid = h("div", { class: "preview-scroll" }, h("table", { class: "list preview-grid" },
    h("thead", {}, h("tr", {}, d.columns.map((c) => h("th", {}, c.name, h("div", { class: "col-type" }, c.type))))),
    h("tbody", {}, d.rows.map((row) => h("tr", {}, row.map((v) => h("td", { class: v === null ? "null" : "" }, v === null ? "null" : String(v))))))));
  body.replaceChildren(tabs, grid,
    h("p", { class: "muted-text" }, d.rows.length ? `Showing the first ${d.rows.length} rows.` : "The table is empty."));
}

function drawNotebookPreview(panel, body, d) {
  const meta = panel.querySelector(".preview-meta");
  const code = d.cells.filter((c) => c.type === "code").length;
  if (meta) meta.textContent = `${d.cells.length} cells  ·  ${code} code${d.language ? `  ·  ${d.language}` : ""}`;
  let n = 0;  // code cells are numbered, as in the notebook
  body.replaceChildren(...(d.cells.length ? d.cells.map((c) => c.type === "markdown"
    ? h("div", { class: "preview-md" }, markdown(c.source))
    : h("div", { class: "preview-cell" }, h("span", { class: "n" }, `[${++n}]`), h("pre", {}, c.source)))
    : [h("p", { class: "muted-text" }, "This notebook has no cells.")]),
  h("p", { class: "muted-text" }, "Read only. Outputs aren't shown; Fabric keeps them with each run."));
}

// The Fabric view sits over the panel, so it follows the page when it scrolls.
document.addEventListener("scroll", () => { if (fabricView.slot?.classList.contains("preview-fabric")) placeFabricView(); }, true);
