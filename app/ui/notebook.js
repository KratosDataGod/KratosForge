// Fabric Desktop notebook screen: the cells, the editor, results and panes, and running cells.
// It works like a Fabric notebook: a code editor with autocomplete, a language per cell,
// command mode keys (A, B, DD, M, Y, Z...), collapse/freeze/parameter cells, live output with a
// Cancel that keeps variables, tables with Table/Chart/Inspect views, charts and HTML, and
// Variables and Outline panes. Opening, saving, publishing and Fabric runs stay in app.js.

state.showVariables = false;
state.lineNumbers = true;
state.mode = "command";        // command | edit, as in Fabric and Jupyter
state.nbTables = {};           // lakehouse_id -> ["dbo.sales", ...], for autocomplete
state.nbSchemas = {};          // lakehouse_id -> { tables: {schema: [names]}, cols, cached, pending }, for %%sql completion

const editors = new Map();     // cell id -> CodeMirror view
const deletedCells = [];       // for Z (undo delete)
let cellClipboard = null;
let lastDKey = 0;

const langComp = new CM.Compartment();
const gutterComp = new CM.Compartment();
const readOnlyComp = new CM.Compartment();

const LANGS = [["python", "Python (PySpark)"], ["sql", "Spark SQL"], ["html", "HTML"], ["markdown", "Markdown"]];
const MAGIC = { sql: "%%sql", html: "%%html" };

function selectedCell() {
  return state.notebook.cells.find((c) => c.id === state.selected) || state.notebook.cells[0];
}

// A code cell's language from its first line: Fabric's %%sql, or Databricks' %sql alone on the line
// (magics.py reads it the same way in the engine).
function cellLang(cell) {
  if (cell.type === "markdown") return "markdown";
  const first = cell.source.trimStart().split("\n", 1)[0].trim();
  if (/^%%sql\b/.test(first) || first === "%sql") return "sql";
  if (/^%%html\b/.test(first)) return "html";
  return "python";
}

// A code cell that shows Markdown when run: Databricks' %md, or %%markdown.
const mdMagic = (cell) => cell.type === "code" && /^\s*(%md|%%md|%%markdown)(\s|$)/.test(cell.source);

// A code cell that runs as a shell script on this PC (shell.py): %sh, %%sh, %%bash, %%cmd, %%powershell.
const shellMagic = (cell) => cell.type === "code" && /^\s*(%sh|%%sh|%%bash|%%cmd|%%powershell)(\s|$)/.test(cell.source);

const isParams = (cell) => (cell.meta?.tags || []).includes("parameters");

function disposeEditors() {
  editors.forEach((v) => v.destroy());
  editors.clear();
}

function dropEditor(cell) {
  editors.get(cell.id)?.destroy();
  editors.delete(cell.id);
}

// ---------- the screen ----------

// A Home ribbon menu (UX cleanup, Cleaner KratosForge): buttons that each stay one click away on
// the Run tab or in their own place are grouped behind one button, as they are (menuNode in app.js).
// items(menu) is called on each open, so the menu always shows the current state; the button reads as
// on while anything in it is on, and isn't shown when none of its buttons are (Preview features hidden).
function ribbonMenu(label, iconName, items, title) {
  const now = items(null).filter(Boolean);
  if (!now.length) return null;
  const on = now.some((b) => b.getAttribute("aria-pressed") === "true");
  return h("button", { class: `tool${on ? " toggled" : ""}`, "aria-haspopup": "menu", "aria-expanded": "false", title, "data-menu-key": `ribbon-${label}`,
    onclick: (e) => { const menu = e.currentTarget; showMenu(menu, items(menu).filter(Boolean)); } },
  icon(iconName, 16), label, icon("chevronDown", 14));
}

function renderNotebook() {
  const nb = state.notebook;
  if (!nb) return go("home");
  const scroller = document.querySelector(".cells");
  const scrollTop = scroller ? scroller.scrollTop : 0;
  const busy = state.kernel === "busy";
  if (nbLakehouse(nb)) loadTableNames(nbLakehouse(nb));

  const tabs = ["home", "edit", "run", "view"].map((t) => h("button", {
    class: `tab${state.ribbonTab === t ? " active" : ""}`, role: "tab", "aria-selected": String(state.ribbonTab === t),
    onclick: () => { state.ribbonTab = t; renderNotebook(); },
  }, t[0].toUpperCase() + t.slice(1)));

  const runAllBtn = btn("Run all", "play", runAll, "tool primary", { disabled: busy, "data-run": true });
  runAllBtn.querySelector("svg").classList.add("play");
  const cancelBtn = btn("Cancel", "stop", cancelRun, "tool", { disabled: !busy, "data-stop": true,
    title: "Stop the running cell. Variables from earlier cells are kept." });
  const where = nbWhere(nb);
  const fabricMode = where.fabric;
  const localOnlyAttrs = (attrs = {}) => fabricMode
    ? { ...attrs, disabled: true, "data-local-only": true, title: "Fabric runs the whole notebook. Use Run all, or switch Runs on to This PC to run single cells." }
    : attrs;
  const computeBtn = h("button", { class: `tool outline compute-btn${fabricMode ? " fabric" : ""}`,
    title: "Where Run all runs this notebook", onclick: (e) => showMenu(e.currentTarget, computeMenu(nb)) },
    icon(fabricMode ? "cloud" : "desktop", 16), `Runs on: ${where.short}`, icon("chevronDown", 14));
  // engines.js: Engine replaces Runs on while Preview features show; with them hidden, Runs on stays.
  const engineCtl = () => previewShown() ? engineToolbar(nb) : [computeBtn];
  const sel = selectedCell();
  const toggleBtn = (label, iconName, on, onClick, attrs = {}) =>
    btn(label, iconName, onClick, `tool${on ? " toggled" : ""}`, { "aria-pressed": String(!!on), ...attrs });

  const toolbars = {
    home: [runAllBtn, cancelBtn, ...engineCtl(), ribbonMenu("Check", "ok", () => [sbsToggle(nb), validateToggle(nb), proveToggle(nb)],
        "Side by side, Validate and Prove (also on the Run tab)"),
      h("div", { class: "sep" }), btn("Add data items", "lakehouse", openDataItems),
      h("div", { class: "spacer" }), notebookDataChip(nb), gitNotebookChip(nb), btn("Save", "save", () => saveExplicitly(() => document.querySelector("button[data-save]")), "tool", { "data-save": true }),
      ribbonMenu("Share", "arrowUpload", (menu) => [btn("Save a copy", "add", () => nbSaveCopyDialog(nb.name)), btn("Versions", "history", nbVersions), nbfExportButton(menu)],
        "Save a copy, Versions and Export"),
      vscodeButton(nb),
      ribbonMenu("Schedule", "history", () => [preview("schedules", btn("Schedule", "history", scheduleNotebook, "tool")), runsToggle(nb)],
        "Schedule this notebook and see its recent runs (also on the Run tab)"),
      h("div", { class: "split" }, btn("Publish to OneLake", "publish", publishNotebook, "tool outline",
        { title: nb.workspace?.workspace_id ? `Publish to ${nb.workspace.workspace_name}` : "Publish to a Fabric workspace you pick" }),
      h("button", { class: "tool outline split-more", "aria-label": "Publish options", title: "Publish options",
        onclick: (e) => showMenu(e.currentTarget, publishMenu(nb)) }, icon("chevronDown", 14)))],
    edit: [btn("Code", "add", () => addCell("code")), btn("Markdown", "add", () => addCell("markdown")),
      h("div", { class: "sep" }), btn("Move up", "up", () => moveCell(-1)), btn("Move down", "down", () => moveCell(1)),
      btn("Split cell", "code", () => splitCell(sel)), btn("Merge with below", "down", () => mergeBelow(sel)),
      btn("Delete cell", "delete", () => deleteCell(sel)), btn("Undo delete", "restart", undoDelete, "tool", { disabled: !deletedCells.length }),
      h("div", { class: "sep" }),
      toggleBtn("Parameter cell", "braces", isParams(sel), () => toggleParams(sel), { disabled: sel.type !== "code" }),
      toggleBtn("Freeze", "shield", sel.meta?.frozen, () => toggleFreeze(sel)),
      btn("Find and replace", "search", () => openFind(true), "tool", { title: "Find and replace in every cell (Ctrl+H)" }),
      btn("Format notebook", "code", formatNotebook, "tool", { title: "Format every code cell: Python in Black's style, SQL in the SQL page's style (nbformat.js)" }),
      h("div", { class: "sep" }), btn("Clear all outputs", "clear", clearOutputs)],
    run: [runAllBtn, btn("Run cell", "play", () => runCell(selectedCell()), "tool", localOnlyAttrs({ disabled: busy, "data-run": true })),
      btn("Run all above", "up", runAbove, "tool", localOnlyAttrs({ disabled: busy, "data-run": true })),
      btn("Run cell and below", "down", runBelow, "tool", localOnlyAttrs({ disabled: busy, "data-run": true })),
      btn("Run selected cells", "play", runSelectedCells, "tool", localOnlyAttrs({ disabled: busy, "data-run": true,
        title: "Run the cells you selected with Shift+click or Shift+Up/Down, in order" })),
      depsRunButton(nb, localOnlyAttrs({ disabled: busy, "data-run": true })), cancelBtn, ...engineCtl(), sbsToggle(nb), validateToggle(nb), proveToggle(nb), h("div", { class: "sep" }),
      btn("Restart kernel", "restart", restartKernel), btn("Restart and run all", "restart", async () => { await restartKernel(); runAll(); }, "tool", localOnlyAttrs({ disabled: busy, "data-run": true })),
      h("div", { class: "sep" }), preview("schedules", btn("Schedule", "history", scheduleNotebook)), runsToggle(nb)],
    view: [toggleBtn("Explorer", "panel", state.showExplorer, () => { state.showExplorer = !state.showExplorer; renderNotebook(); }),
      toggleBtn("Outline", "documentTable", state.showExplorer && state.explorerTab === "outline", () => {
        const on = state.showExplorer && state.explorerTab === "outline";
        state.showExplorer = !on;
        if (!on) state.explorerTab = "outline";
        renderNotebook();
      }),
      toggleBtn("Variables", "braces", state.showVariables, toggleVariables), depsToggle(), commentsToggle(nb), practicesToggle(nb),
      h("div", { class: "sep" }),
      btn("Collapse all inputs", "up", () => setAll("source_hidden", true)), btn("Expand all inputs", "down", () => setAll("source_hidden", false)),
      btn("Collapse all outputs", "up", () => setAll("outputs_hidden", true)), btn("Expand all outputs", "down", () => setAll("outputs_hidden", false)),
      nb.cells.some((c) => c.meta?.heading_collapsed && headingLevel(c))
        ? btn("Expand all sections", "chevronDown", () => setAllSections(false))
        : btn("Collapse all sections", "chevronRight", () => setAllSections(true)),
      h("div", { class: "sep" }), toggleBtn("Line numbers", "code", state.lineNumbers, toggleLineNumbers)],
  };

  const cells = h("section", { class: "cells", "aria-label": "Notebook cells", onmousedown: (e) => {
    if (e.target === e.currentTarget || e.target.classList.contains("cells-inner")) enterCommandMode();
  } },
  nbFindBar(), h("div", { class: "cells-inner" }, selectionBar(), replayBanner(nb), sbsPanel(nb), computeNote(nb), inputsBar(nb), visibleCells(nb.cells).map(renderCell),
    h("div", { class: "add-cell" }, btn("Code", "add", () => addCell("code", true), "tool outline"),
      btn("Markdown", "add", () => addCell("markdown", true), "tool outline"))));

  const lh = nbLakehouse(nb);
  setView(
    h("div", { class: "ribbon" }, h("div", { class: "ribbon-tabs", role: "tablist", "aria-label": "Ribbon" }, tabs),
      h("div", { class: "toolbar" }, toolbars[state.ribbonTab])),
    ...[nbNextLine(nb)].filter(Boolean),  // replaceChildren would write "null"
    h("div", { class: "nb-body" }, state.showExplorer ? renderExplorer() : null, cells,
      state.showVariables ? renderVariables() : null, commentsPanel(nb), practicesPanel(nb), runsPanel(nb), provePanel(nb), validatePanel(nb)),
    h("footer", { class: "statusbar" },
      h("span", {}, h("span", { class: `dot ${state.kernel}` }), kernelLabel()),
      sessionsButton(), environmentButton(nb),
      h("span", { class: "mode-hint" }, modeHint()),
      h("span", {}, `Attached: ${lh ? lh.name : "none"}`),
      h("span", { class: "last-run" }, nbLastRunText(nb.lastRun)),
      h("span", {}, fabricMode ? `Compute: Fabric capacity in ${publishWorkspaceName(nb)}`
        : `Compute: Python ${state.python || "3"} on this PC, no Fabric job`),
      nbFabricRunStatus(nb)));
  document.querySelector(".cells").scrollTop = scrollTop;
  editors.forEach((v) => v.requestMeasure());
  depsChanged();
}

function kernelLabel() {
  return { idle: "Kernel idle", busy: "Kernel busy", stopped: "Kernel stopped, variables cleared" }[state.kernel];
}

function modeHint() {
  return state.mode === "edit" ? "Edit mode: Esc for command mode, Shift+Enter to run"
    : "Command mode: Enter to edit, A above, B below, DD delete, M markdown, Y code, Z undo, Shift+Down select more";
}

function setMode(mode) {
  if (state.mode === mode) return;
  state.mode = mode;
  const hint = document.querySelector(".mode-hint");
  if (hint) hint.textContent = modeHint();
  document.querySelector(".cells")?.classList.toggle("editing", mode === "edit");
}

function enterCommandMode(cell = selectedCell()) {
  setMode("command");
  const el = document.querySelector(`[data-cell="${cell.id}"]`);
  if (el && !el.contains(document.activeElement) || document.activeElement?.closest?.(".cm-editor")) el?.focus({ preventScroll: true });
}

function enterEditMode(cell = selectedCell()) {
  if (cell.meta?.frozen) { toast("This cell is frozen. Unfreeze it on the Edit tab to change it."); return; }
  if (cell.meta?.source_hidden) setMeta(cell, "source_hidden", false);
  if (cell.type === "markdown" && !cell.editing) { cell.editing = true; updateCell(cell); }
  editors.get(cell.id)?.focus();
}

// ---------- Explorer: data items, outline, resources ----------

function renderExplorer() {
  const lh = nbLakehouse(state.notebook);
  const fromLocations = nbData(state.notebook).from === "locations";
  const tab = (id, label) => h("button", { class: `tab${state.explorerTab === id ? " active" : ""}`, role: "tab",
    "aria-selected": String(state.explorerTab === id), onclick: () => { state.explorerTab = id; renderNotebook(); } }, label);
  const tabs = h("div", { class: "explorer-tabs", role: "tablist", "aria-label": "Explorer" }, tab("data", "Data items"), tab("outline", "Outline"), tab("resources", "Resources"), tab("notebooks", "Notebooks"));
  const content = h("div", { class: "explorer-content" });

  if (state.explorerTab === "outline") {
    content.append(renderOutline());
  } else if (state.explorerTab === "notebooks") {
    content.append(nbExplorerNotebooks());  // grouped by folder (notebook-files.js)
  } else if (state.explorerTab === "resources") {
    content.append(nbResourcesSection(state.notebook), h("div", { class: "nb-res-head" }, h("b", {}, "Code snippets")),
      h("div", { class: "muted-text", style: "padding:4px 4px 10px" }, "Click a snippet to add it as a new cell."),
      ...SNIPPETS.map(([title, code]) => h("button", { class: "snippet", onclick: () => insertCell(code) },
        h("div", { class: "t" }, title), h("code", {}, code))));
  } else if (lh) {
    const tree = h("div");
    content.append(
      h("div", { class: "lh-head" }, icon("lakehouse", 18), h("span", { class: "name", title: `${lh.name} in ${lh.workspace_name}` }, lh.name),
        h("span", { class: "badge", title: fromLocations ? "From this repository's locations for the environment in the top bar" : null },
          fromLocations ? `${nbData(state.notebook).envLabel} location` : "Default"),
        h("button", { class: "tool small", title: "Refresh", "aria-label": "Refresh", onclick: () => {
          Object.keys(state.tree).filter((k) => k.startsWith(lh.lakehouse_id)).forEach((k) => delete state.tree[k]);
          delete state.nbTables[lh.lakehouse_id];
          delete state.nbSchemas[lh.lakehouse_id];
          renderNotebook();
        } }, icon("restart", 14)),
        fromLocations ? null : h("button", { class: "tool small", title: "Remove lakehouse", "aria-label": "Remove lakehouse", onclick: detachLakehouse }, icon("dismiss", 14))),
      tree,
      btn("Add data items", "add", openDataItems, "tool link", { style: "margin-top:12px" }));
    renderTree(tree, lh, insertCell);
  } else {
    content.append(h("div", { class: "empty" },
      h("span", { style: "color:var(--accent)" }, icon("lakehouse", 40)),
      h("span", { class: "title" }, "No data items yet"),
      h("span", { class: "text" }, "Attach a lakehouse to read its files and write Delta tables back to OneLake."),
      btn("Add data items", null, openDataItems, "tool primary")));
  }
  return h("aside", { class: "explorer", "aria-label": "Explorer" }, h("div", { class: "explorer-head" }, "Explorer"), tabs, content);
}

// Headings from Markdown cells, and the parameter cell, as Fabric's outline shows them.
function renderOutline() {
  const items = [];
  state.notebook.cells.forEach((cell) => {
    if (cell.type === "markdown") {
      for (const hd of markdownHeadings(cell.source)) {  // nbcells.js: skips fenced code blocks
        if (hd.level <= 4) items.push({ cell, level: hd.level, text: hd.text.replace(/[*_`]/g, "") });
      }
    } else if (isParams(cell)) {
      items.push({ cell, level: 1, text: "Parameters" });
    }
    if (cell.type === "code" && cell.meta?.title) items.push({ cell, level: 4, text: cell.meta.title, titled: true });
  });
  if (!items.length) {
    return h("div", { class: "muted-text", style: "padding:8px 4px" }, "Add Markdown headings (# Title) to build an outline.");
  }
  return h("nav", { class: "nb-outline", "aria-label": "Outline" }, items.map((it) => h("button", {
    class: `outline-item l${it.level}${it.titled ? " titled" : ""}${it.cell.id === state.selected ? " active" : ""}`,
    onclick: () => { revealCell(it.cell); selectCell(it.cell); document.querySelector(`[data-cell="${it.cell.id}"]`)?.scrollIntoView({ block: "start", behavior: "smooth" }); },
  }, it.text)));
}

function refreshOutline() {
  if (!state.showExplorer || state.explorerTab !== "outline") return;
  const content = document.querySelector(".explorer-content");
  content?.replaceChildren(renderOutline());
}

const SNIPPETS = [
  ["Import the lakehouse", "import polars as pl\nfrom fabric_desktop import lakehouse"],
  ["Read a table with Spark", 'df = spark.read.table("dbo.my_table")\ndisplay(df)'],
  ["Spark SQL cell", "%%sql\nSELECT * FROM dbo.my_table LIMIT 100"],
  ["Query into a DataFrame", "df = %sql SELECT * FROM dbo.my_table LIMIT 100\ndisplay(df)"],
  ["Write a table with Spark", 'df.write.mode("overwrite").saveAsTable("dbo.my_table")'],
  ["Read a CSV from Files", 'df = lakehouse.read_csv("Files/raw/data.csv")\ndf'],
  ["Read a table", 'df = lakehouse.read_table("dbo.my_table")\ndf'],
  ["Write a table", 'lakehouse.write_table(df, "dbo.my_table", mode="overwrite")'],
  ["List files", 'notebookutils.fs.ls("Files")'],
  ["Get a Key Vault secret", 'secret = notebookutils.credentials.getSecret("https://<vault>.vault.azure.net/", "<secret-name>")'],
  ["Run another notebook", 'result = notebookutils.notebook.run("Other notebook", 600, {"run_date": "2026-01-01"})'],
  ["Databricks widget", 'dbutils.widgets.text("run_date", "2026-01-01", "Run date")\nrun_date = dbutils.widgets.get("run_date")'],
  ["Databricks secret (Key Vault scope)", 'secret = dbutils.secrets.get("<vault-name>", "<secret-name>")'],
  ["Plot with matplotlib", 'import matplotlib.pyplot as plt\nplt.bar(["East", "West"], [960, 1350])\nplt.show()'],
];

// ---------- Variables pane ----------

function toggleVariables() {
  state.showVariables = !state.showVariables;
  renderNotebook();
  if (state.showVariables) refreshVariables();
}

function renderVariables() {
  return h("aside", { class: "varpane", "aria-label": "Variables" },
    h("div", { class: "explorer-head" }, "Variables",
      h("button", { class: "tool small", title: "Refresh", "aria-label": "Refresh variables", onclick: refreshVariables }, icon("restart", 14))),
    dbgOn() && state.notebook && !nbWhere(state.notebook).fabric ? dbgPane(state.notebook)  // debugging.js
      : h("div", { class: "var-list" }, state.variables ? variableTable(state.variables) : h("div", { class: "muted-text" }, "Loading...")));
}

// variableTable is in nbvars.js.

async function refreshVariables() {
  if (!state.showVariables || nbWhere(state.notebook).fabric) return;
  try {
    state.variables = await rpc("kernel_variables", { notebook: state.notebook.name });
  } catch {
    state.variables = [];
  }
  document.querySelector(".var-list")?.replaceChildren(variableTable(state.variables));
}

// ---------- cells ----------

function renderCell(cell) {
  const selected = cell.id === state.selected;
  const meta = cell.meta || (cell.meta = {});
  const node = h("div", {
    class: `cell ${cell.type}${selected ? " selected" : ""}${meta.frozen ? " frozen" : ""}${multiSelected() && nbSel.ids.has(cell.id) ? " multi" : ""}`,
    "data-cell": cell.id, tabindex: "-1",
    onmousedown: (e) => {
      if (selectionClick(cell, e)) return;  // nbselect.js: Shift or Ctrl+click
      if (state.selected !== cell.id) selectCell(cell);
      else if (multiSelected()) clearSelection();
      if (!e.target.closest(".cm-editor, input, select, textarea, button")) {
        setMode("command");
        setTimeout(() => { if (!document.activeElement?.closest?.(".cm-editor, input, select")) node.focus({ preventScroll: true }); });
      }
    },
  });
  const head = h("div", { class: "cell-head" },
    h("select", { class: "lang", "aria-label": "Cell language", disabled: meta.frozen || null,
      onchange: (e) => setLanguage(cell, e.target.value) },
    LANGS.map(([v, label]) => h("option", { value: v, selected: cellLang(cell) === v || null }, label))),
    isParams(cell) ? h("span", { class: "tag" }, "Parameters") : null,
    meta.frozen ? h("span", { class: "tag frozen-tag" }, icon("shield", 12), "Frozen") : null,
    fabricStepBadge(cell),  // fabricstep.js
    cell.type === "code" ? depsHead(cell) : null,
    ...cellHeadExtras(cell),
    commentBadge(cell),  // notebook-comments.js
    h("span", { class: "spacer" }),
    cell.type === "code" ? h("button", { class: "tool small", title: meta.source_hidden ? "Show code" : "Hide code",
      "aria-label": meta.source_hidden ? "Show code" : "Hide code", onclick: () => setMeta(cell, "source_hidden", !meta.source_hidden) },
    icon("code", 14)) : null,
    h("button", { class: "tool small", title: "Move up", "aria-label": "Move up", onclick: () => moveCell(-1, cell) }, icon("up", 14)),
    h("button", { class: "tool small", title: "Move down", "aria-label": "Move down", onclick: () => moveCell(1, cell) }, icon("down", 14)),
    h("button", { class: "tool small", title: "More cell actions", "aria-label": "More cell actions",
      onclick: (e) => showMenu(e.currentTarget, cellMenu(cell)) }, "..."),
    h("button", { class: "tool small", title: "Delete cell", "aria-label": "Delete cell", onclick: () => deleteCell(cell) }, icon("delete", 14)));

  if (cell.type === "markdown") {
    let body;
    if (cell.editing && !meta.frozen) {
      body = h("div", { class: "md-edit" }, editorFor(cell).dom);
    } else {
      body = h("div", { class: "md-view", ondblclick: () => enterEditMode(cell) }, markdown(cell.source));
    }
    node.append(h("div", { class: "gutter" }, cellGrip(cell), sectionToggle(cell)), h("div", { class: "cell-box" }, head, body, sectionHiddenBar(cell)));
    return node;
  }

  const gutter = renderGutter(cell);

  const box = h("div", { class: "cell-box" }, head);
  if (meta.source_hidden) {
    const first = cell.source.split("\n").find((l) => l.trim()) || "";
    box.append(h("button", { class: "hidden-input", onclick: () => setMeta(cell, "source_hidden", false) },
      `Code hidden (${cell.source.split("\n").length} lines): ${first.slice(0, 80)}`));
  } else {
    box.append(h("div", { class: "editor" }, editorFor(cell).dom));
  }
  box.append(depsStrip(cell), h("div", { class: "cell-results" }, cellResults(cell)));
  node.append(gutter, box);
  return node;
}

function renderGutter(cell) {
  const running = cell.status === "running" || cell.status === "queued";
  const runBtn = cell.status === "running"
    ? h("button", { class: "tool small", "aria-label": "Cancel", title: "Cancel (keeps variables)", onclick: cancelRun }, icon("stop", 14))
    : h("button", { class: "tool small", "aria-label": "Run cell", title: "Run cell (Shift+Enter)",
      disabled: state.kernel === "busy" || cell.meta?.frozen || null, "data-run": true, onclick: () => runCell(cell) }, icon("play", 14));
  runBtn.querySelector("svg")?.classList.add("play");
  return h("div", { class: "gutter" }, cellGrip(cell), runBtn, running ? "[*]" : `[${cell.count ?? " "}]`);
}

function cellResults(cell) {
  const parts = [];
  if (cell.status) parts.push(statusLine(cell));
  if (cell.engineFlag) parts.push(engineFlagPanel(cell));  // engines.js
  if (cell.paused) parts.push(dbgPausedPanel(cell));  // debugging.js
  if (!cell.outputs.length) return cell.hidden?.length ? [...parts, dbgCellWarning(cell)].filter(Boolean) : parts;
  if (cell.meta?.outputs_hidden) {
    parts.push(h("button", { class: "hidden-input", onclick: () => setMeta(cell, "outputs_hidden", false) },
      `Output hidden (${cell.outputs.length} item${cell.outputs.length === 1 ? "" : "s"}). Click to show.`));
  } else {
    cell.outputs.forEach((o) => parts.push(renderOutput(o, cell)));
  }
  const offer = replayOffer(cell);  // replay.js
  if (offer) parts.push(offer);
  const hidden = cell.status === "running" ? null : dbgCellWarning(cell);  // debugging.js
  if (hidden) parts.push(hidden);
  return parts;
}

// Redraws only a cell's status and output, so the editor keeps focus while output streams in.
function updateResults(cell) {
  const el = document.querySelector(`[data-cell="${cell.id}"]`);
  if (!el) return;
  el.querySelector(".cell-results")?.replaceChildren(...cellResults(cell));
  if (cell.type === "code") el.querySelector(".gutter")?.replaceWith(renderGutter(cell));
}

function cellMenu(cell) {
  const items = [];
  if (cell.type === "code") {
    items.push({ label: isParams(cell) ? "Remove parameter tag" : "Toggle parameter cell", icon: "braces", action: () => toggleParams(cell) });
    items.push({ label: cell.meta.outputs_hidden ? "Show output" : "Hide output", icon: "panel", action: () => setMeta(cell, "outputs_hidden", !cell.meta.outputs_hidden) });
    items.push({ label: "Clear output", icon: "clear", action: () => { cell.outputs = []; cell.status = null; updateCell(cell); } });
    items.push({ label: "Format cell", icon: "code", action: () => formatCells([cell]) });  // nbformat.js
    if (dbgOn() && !nbWhere(state.notebook).fabric) {  // debugging.js
      items.push({ label: "Debug cell (pause at the first line)", icon: "play", action: () => debugCell(cell) });
    }
  }
  items.push(...fabricStepMenuItems(cell));  // fabricstep.js
  items.push({ label: cell.meta.frozen ? "Unfreeze cell" : "Freeze cell", icon: "shield", action: () => toggleFreeze(cell) });
  items.push({ label: "Split cell at cursor", icon: "code", action: () => splitCell(cell) });
  items.push({ label: "Merge with cell below", icon: "down", action: () => mergeBelow(cell) });
  items.push({ label: "Copy cell", icon: "copyArrowRight", action: () => copyCell(cell) });
  items.push({ label: "Paste cell below", icon: "add", action: () => pasteCell(cell, 1) });
  items.push({ label: "Find in this cell", icon: "search", action: () => findInCell(cell) });
  items.push(...commentMenuItems(cell));  // notebook-comments.js
  return [...items, ...cellMenuExtras(cell)];
}

function statusLine(cell) {
  const s = cell.status;
  const label = { queued: ["busy", "Queued"], running: cell.paused ? ["busy", "Paused"] : ["busy", "Running"], ok: ["ok", "Succeeded"],
    error: ["bad", "Failed"], stopped: ["bad", "Stopped"], cancelled: ["bad", "Cancelled"] }[s];
  return h("div", { class: "cell-status", "data-s": s, "data-where": isFabricStep(cell) ? "fabric" : null }, h("span", { class: label[0] }, label[1]),
    s === "running" && cell.seconds != null ? h("span", {}, `${cell.seconds.toFixed(1)} s`) : null,
    s === "running" && cell.progress != null ? h("span", { class: "cell-progress", title: "How far the running query is" },
      h("progress", { max: 100, value: cell.progress, "aria-label": "Query progress" }), `Query ${Math.floor(cell.progress)}%`) : null,
    s === "ok" || s === "error" || s === "cancelled" ? h("span", {}, isFabricStep(cell) ? "Fabric step" : cell.partial ? "Ran the selection on this PC"
      : cell.ranOn ? cell.ranOn : "Ran on this PC") : null,
    cell.ranOn && cell.rows != null && s === "ok" ? h("span", {}, `${cell.rows} row${cell.rows === 1 ? "" : "s"}`) : null,
    cell.seconds != null && s !== "running" && s !== "queued" ? h("span", {}, `${cell.seconds.toFixed(1)} s`) : null,
    cell.finishedAt && s !== "running" ? h("span", {}, cell.finishedAt) : null);
}

function updateCell(cell, focus = false) {
  const old = document.querySelector(`[data-cell="${cell.id}"]`);
  if (!old) return;
  const fresh = renderCell(cell);
  old.replaceWith(fresh);
  editors.get(cell.id)?.requestMeasure();
  if (focus) enterEditMode(cell);
}

function selectCell(cell) {
  if (nbSel.ids.size) clearSelection();  // nbselect.js: a plain move ends a multi-cell selection
  const prev = state.selected;
  state.selected = cell.id;
  document.querySelector(`[data-cell="${prev}"]`)?.classList.remove("selected");
  document.querySelector(`[data-cell="${cell.id}"]`)?.classList.add("selected");
  if (state.ribbonTab === "edit" && prev !== cell.id) renderToolbar();
  document.querySelectorAll(".outline-item").forEach((b) => b.classList.remove("active"));
}

// The Edit tab's toggles follow the selected cell.
function renderToolbar() {
  const scroller = document.querySelector(".cells");
  const top = scroller?.scrollTop;
  const focused = document.activeElement?.closest?.(".cm-editor") ? state.selected : null;
  renderNotebook();
  if (scroller && top != null) document.querySelector(".cells").scrollTop = top;
  if (focused) editors.get(focused)?.focus();
}

function selectRelative(delta) {
  const cells = visibleCells(state.notebook.cells);
  const i = cells.indexOf(selectedCell());
  const next = cells[Math.max(0, Math.min(cells.length - 1, i + delta))];
  selectCell(next);
  const el = document.querySelector(`[data-cell="${next.id}"]`);
  el?.focus({ preventScroll: true });
  el?.scrollIntoView({ block: "nearest" });
}

function focusNext(cell, edit = true) {
  const cells = state.notebook.cells;
  const { hidden } = collapsedCells(cells);
  const i = cells.indexOf(cell);
  let next = cells.slice(i + 1).find((c) => !hidden.has(c.id));
  if (!next) {
    next = cellFrom({ type: "code" });
    cells.push(next);
    state.selected = next.id;
    renderNotebook();
  } else {
    selectCell(next);
  }
  const el = document.querySelector(`[data-cell="${next.id}"]`);
  el?.scrollIntoView({ block: "nearest" });
  if (edit && next.type === "code") enterEditMode(next);
  else el?.focus({ preventScroll: true });
}

function addCell(type, atEnd = false, offset = 1) {
  const cells = state.notebook.cells;
  const cell = cellFrom({ type });
  const i = atEnd ? cells.length - 1 : cells.indexOf(selectedCell());
  cells.splice(offset > 0 ? i + 1 : Math.max(i, 0), 0, cell);
  state.selected = cell.id;
  markDirty();
  revealCell(cell);  // nbcells.js: added inside a collapsed section (below its heading): open it
  renderNotebook();
  document.querySelector(`[data-cell="${cell.id}"]`)?.scrollIntoView({ block: "nearest" });
  enterEditMode(cell);
  return cell;
}

function insertCell(code) {
  const cell = addCell("code");
  setSource(cell, code);
  markDirty();
}

function setSource(cell, source) {
  cell.source = source;
  const view = editors.get(cell.id);
  if (view && view.state.doc.toString() !== source) {
    view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: source } });
  }
}

// Moves past the next shown cell; a collapsed heading takes its section along (nbcells.js).
function moveCell(delta, cell = selectedCell()) {
  if (moveShown([cell], delta) !== "moved") return;
  state.selected = cell.id;
  markDirty();
  renderNotebook();
  refreshOutline();
}

function deleteCell(cell) {
  if (cell.meta?.frozen) { toast("This cell is frozen. Unfreeze it to delete it."); return; }
  const cells = state.notebook.cells;
  const i = cells.indexOf(cell);
  cells.splice(i, 1);
  deletedCells.push({ cell: { type: cell.type, source: cell.source, meta: structuredClone(cell.meta || {}), cell_id: cell.cell_id }, index: i });
  dropEditor(cell);
  if (!cells.length) cells.push(cellFrom({ type: "code" }));
  state.selected = (cells[i] || cells[i - 1]).id;
  markDirty();
  renderNotebook();
  enterCommandMode();
}

function undoDelete() {
  const last = deletedCells.pop();
  if (!last) return;
  if (last.group) return undoDeleteGroup(last);  // nbselect.js
  const cell = cellFrom(last.cell);
  if (cell.type === "markdown") cell.editing = false;
  state.notebook.cells.splice(Math.min(last.index, state.notebook.cells.length), 0, cell);
  state.selected = cell.id;
  markDirty();
  renderNotebook();
  enterCommandMode(cell);
}

function copyCell(cell) {
  cellClipboard = { type: cell.type, source: cell.source, meta: structuredClone(cell.meta || {}) };
  toast("Cell copied");
}

function pasteCell(cell, offset) {
  if (!cellClipboard) return;
  const cells = state.notebook.cells;
  const copies = [cellClipboard].flat().map((c) => {  // several cells after a multi-cell copy (nbselect.js)
    const copy = cellFrom({ ...c, meta: structuredClone(c.meta) });
    if (copy.type === "markdown") copy.editing = false;
    delete copy.meta.tags;
    return copy;
  });
  cells.splice(cells.indexOf(cell) + (offset > 0 ? 1 : 0), 0, ...copies);
  const copy = copies[0];
  state.selected = copy.id;
  markDirty();
  copies.forEach(revealCell);  // pasted into a collapsed section: open it
  renderNotebook();
  enterCommandMode(copy);
}

function splitCell(cell) {
  if (cell.meta?.frozen) { toast("This cell is frozen. Unfreeze it on the Edit tab to change it."); return; }
  const view = editors.get(cell.id);
  const pos = view ? view.state.selection.main.head : cell.source.length;
  const before = cell.source.slice(0, pos).replace(/\n$/, "");
  let after = cell.source.slice(pos).replace(/^\n/, "");
  // The second half keeps the cell's magic as the cell writes it: Fabric's %%sql or Databricks' %sql.
  const magic = MAGIC[cellLang(cell)] && cell.source.trimStart().split(/\s/, 1)[0];
  if (magic && !after.trimStart().startsWith(magic)) after = `${magic}\n${after}`;
  setSource(cell, before);
  const cells = state.notebook.cells;
  const fresh = cellFrom({ type: cell.type, source: after });
  if (fresh.type === "markdown") fresh.editing = true;
  cells.splice(cells.indexOf(cell) + 1, 0, fresh);
  state.selected = fresh.id;
  markDirty();
  renderNotebook();
  enterEditMode(fresh);
}

// Same as Shift+M on one cell (nbselect.js): refuses cells in different languages, and Z undoes it.
function mergeBelow(cell) {
  if (state.selected !== cell.id) selectCell(cell);
  mergeSelected();
}

function setLanguage(cell, lang) {
  // The picker is disabled on a frozen cell; M and Y in command mode come here too.
  if (cell.meta?.frozen) { toast("This cell is frozen. Unfreeze it on the Edit tab to change it."); return; }
  // The old language's magic line goes, including Databricks' %md and %%markdown, so M on a %md cell keeps only its text.
  const body = cell.source.replace(/^\s*(%%(sql|html|pyspark|python|md|markdown)\b[^\n]*|%(sql|python|md)[ \t]*(?=\n|$))\n?/, "");
  const wasMarkdown = cell.type === "markdown";
  if (lang === "markdown") {
    cell.type = "markdown";
    cell.source = body;
    cell.editing = true;
    cell.outputs = [];
    cell.status = null;
  } else {
    cell.type = "code";
    cell.source = MAGIC[lang] ? `${MAGIC[lang]}\n${body}` : body;
  }
  if (wasMarkdown !== (cell.type === "markdown")) dropEditor(cell);
  else setSource(cell, cell.source);
  editors.get(cell.id)?.dispatch({ effects: langComp.reconfigure(languageFor(cell)) });
  markDirty();
  updateCell(cell);
  refreshOutline();
}

function setMeta(cell, key, value) {
  cell.meta = cell.meta || {};
  if (value) cell.meta[key] = true;
  else delete cell.meta[key];
  markDirty();
  updateCell(cell);
}

function setAll(key, value) {
  state.notebook.cells.filter((c) => c.type === "code").forEach((c) => {
    c.meta = c.meta || {};
    if (value) c.meta[key] = true;
    else delete c.meta[key];
  });
  markDirty();
  renderNotebook();
}

// One parameter cell per notebook, as in Fabric: pipelines pass values that replace its variables.
function toggleParams(cell) {
  if (cell.type !== "code") return;
  const on = !isParams(cell);
  state.notebook.cells.forEach((c) => { if (c.meta?.tags) c.meta.tags = c.meta.tags.filter((t) => t !== "parameters"); });
  if (on) (cell.meta.tags ||= []).push("parameters");
  state.notebook.cells.forEach((c) => { if (c.meta?.tags && !c.meta.tags.length) delete c.meta.tags; });
  markDirty();
  renderNotebook();
}

function toggleFreeze(cell) {
  setMeta(cell, "frozen", !cell.meta?.frozen);
  editors.get(cell.id)?.dispatch({ effects: readOnlyComp.reconfigure(readOnly(cell)) });
  if (state.ribbonTab === "edit") renderToolbar();
}

function findInCell(cell) {
  const view = editors.get(cell.id);
  if (!view) { enterEditMode(cell); return; }
  view.focus();
  CM.openSearchPanel(view);
}

function toggleLineNumbers() {
  state.lineNumbers = !state.lineNumbers;
  editors.forEach((v, id) => {
    const cell = state.notebook.cells.find((c) => c.id === id);
    if (cell) v.dispatch({ effects: gutterComp.reconfigure(gutters(cell)) });
  });
  renderNotebook();
}

function clearOutputs() {
  state.notebook.cells.forEach((c) => { c.outputs = []; c.status = null; c.count = null; c.seconds = null; });
  renderNotebook();
}

// ---------- the code editor ----------

const editorHighlight = CM.HighlightStyle.define([
  { tag: [CM.tags.keyword, CM.tags.controlKeyword, CM.tags.definitionKeyword, CM.tags.moduleKeyword, CM.tags.operatorKeyword], class: "kw" },
  { tag: [CM.tags.string, CM.tags.special(CM.tags.string)], class: "str" },
  { tag: [CM.tags.number, CM.tags.bool, CM.tags.null], class: "num" },
  { tag: [CM.tags.comment, CM.tags.lineComment, CM.tags.blockComment], class: "cm" },
  { tag: [CM.tags.function(CM.tags.variableName), CM.tags.function(CM.tags.propertyName), CM.tags.definition(CM.tags.function(CM.tags.variableName))], class: "fn" },
  { tag: [CM.tags.standard(CM.tags.variableName), CM.tags.className, CM.tags.typeName], class: "bi" },
  { tag: CM.tags.heading, class: "md-h" },
  { tag: [CM.tags.tagName, CM.tags.angleBracket], class: "kw" },
  { tag: CM.tags.attributeName, class: "fn" },
]);

const editorTheme = CM.EditorView.theme({
  "&": { fontSize: "13px" },
  "&.cm-focused": { outline: "none" },
  ".cm-content": { fontFamily: "var(--mono)", padding: "8px 0", lineHeight: "20px" },
  ".cm-gutters": { background: "transparent", border: "none", color: "var(--muted)", fontFamily: "var(--mono)" },  // 4.5:1 or more (#945)
  ".cm-activeLine": { background: "transparent" },
  "&.cm-focused .cm-activeLine": { background: "rgba(27, 54, 100, 0.04)" },
  ".cm-activeLineGutter": { background: "transparent" },
  ".cm-tooltip-autocomplete": { fontFamily: "var(--mono)", fontSize: "12.5px" },
  ".cm-scroller": { overflowX: "auto" },
});

function gutters(cell) {
  return state.lineNumbers && cell.type === "code"
    ? [CM.lineNumbers({ domEventHandlers: { mousedown: (view, line) => dbgGutterClick(cell, view, line) } }), CM.foldGutter()] : [];
}

function readOnly(cell) {
  return cell.meta?.frozen ? [CM.EditorState.readOnly.of(true), CM.EditorView.editable.of(false)] : [];
}

function languageFor(cell) {
  const lang = cellLang(cell);
  if (lang === "markdown" || mdMagic(cell)) return [CM.markdown(), CM.autocompletion()];
  if (shellMagic(cell)) return [];  // plain text: no Python highlighting or completions in a script
  // %%tsql (Fabric Python notebooks): T-SQL for a Warehouse; it runs in Fabric (notebook.py _tsql).
  if (/^\s*%%tsql(\s|$)/.test(cell.source)) return [CM.sql({ dialect: CM.StandardSQL, upperCaseKeywords: true })];
  if (lang === "html") return [CM.html(), CM.autocompletion()];
  // %%sql runs as Spark SQL: the SQL page's completion, so an alias offers its table's columns (PB-027).
  if (lang === "sql") {
    return [CM.sql({ dialect: sqlCmDialect("spark"), upperCaseKeywords: true }),
      CM.autocompletion({ override: [sqlCompletionSource(nbSqlProvider, () => "spark")], activateOnTypingDelay: 120 })];
  }
  return [CM.python(), CM.autocompletion({ override: [pythonCompletions], activateOnTypingDelay: 150 }),
    ...intelExtensions()];  // nbintel.js: hover, signature help, syntax errors
}

function currentTables() {
  const lh = state.notebook && nbLakehouse(state.notebook);
  return (lh && state.nbTables[lh.lakehouse_id]) || [];
}

async function loadTableNames(lh) {
  if (state.nbTables[lh.lakehouse_id]) return;
  state.nbTables[lh.lakehouse_id] = [];
  const sql = state.nbSchemas[lh.lakehouse_id] = { tables: {}, cols: {}, cached: {}, pending: {}, failed: {} };
  // The names this PC read last time (schema_cache.py): the tables while offline, a table's columns when Fabric can't be asked.
  const cached = rpc("cached_schemas", { lakehouse_id: lh.lakehouse_id }).catch(() => null);
  let bySchema = null;
  try {
    bySchema = await rpc("list_tables", { workspace_id: lh.workspace_id, lakehouse_id: lh.lakehouse_id });
  } catch {
    // Not signed in or offline: the cached names, or none.
  }
  const last = await cached;
  sql.cached = last?.columns || {};
  sql.tables = bySchema || last?.tables || {};
  state.nbTables[lh.lakehouse_id] = Object.entries(sql.tables).flatMap(([s, names]) => names.map((n) => `${s}.${n}`));
}

// The %%sql completion's names: the notebook's lakehouse, read when it's first needed. A table's columns
// come from Fabric once per table, or from the cache when Fabric can't be asked (offline, signed out);
// Fabric is asked again a minute later, so a table read while offline gets its real columns once back.
const NB_SQL_RETRY_MS = 60000;
const nbSqlProvider = {
  id: "notebook",
  label: "Notebook lakehouse",
  lakehouse: () => state.notebook && nbLakehouse(state.notebook),
  sql() { const lh = this.lakehouse(); return lh && state.nbSchemas[lh.lakehouse_id]; },
  tables() { return Object.entries(this.sql()?.tables || {}).flatMap(([schema, names]) => names.map((name) => ({ schema, name }))); },
  cachedColumns(t) {
    const sql = this.sql();
    const k = sqlTableName(t.schema, t.name);
    return sql?.cols[k] || (sql?.failed[k] ? sql.cached[k] || [] : undefined);
  },
  columns(t) {
    const sql = this.sql();
    const lh = this.lakehouse();
    if (!sql || !lh) return Promise.resolve([]);
    const k = sqlTableName(t.schema, t.name);
    if (sql.cols[k]) return Promise.resolve(sql.cols[k]);
    if (Date.now() - (sql.failed[k] || 0) < NB_SQL_RETRY_MS) return Promise.resolve(sql.cached[k] || []);
    sql.pending[k] ||= rpc("table_columns", { lakehouse_ref: lh, table: k })
      .then((cols) => { delete sql.failed[k]; return (sql.cols[k] = cols || []); })
      .catch(() => { sql.failed[k] = Date.now(); return sql.cached[k] || []; })
      .finally(() => { delete sql.pending[k]; });
    return sql.pending[k];
  },
};

const CM_TYPES = { method: "method", function: "function", property: "property", variable: "variable",
  keyword: "keyword", namespace: "namespace", class: "class" };

// Python completion: table names inside strings, otherwise names from the kernel (variables,
// attributes of live objects, builtins, keywords) plus names assigned in this cell.
async function pythonCompletions(ctx) {
  const node = CM.syntaxTree(ctx.state).resolveInner(ctx.pos, -1);
  if (node.name === "String" || node.name === "FormatString") {
    const word = ctx.matchBefore(/[\w.]*/);
    const tables = currentTables();
    if (!tables.length) return null;
    return { from: word.from, options: tables.map((t) => ({ label: t, type: "class", detail: "table" })), validFor: /^[\w.]*$/ };
  }
  if (node.name === "Comment") return null;
  const word = ctx.matchBefore(/[A-Za-z_][\w.]*/);
  if (!word && !ctx.explicit) return null;
  const line = ctx.state.doc.lineAt(ctx.pos);
  const before = line.text.slice(0, ctx.pos - line.from);
  let result = { from: ctx.pos, items: [] };
  if (!nbWhere(state.notebook).fabric) {
    try {
      result = await rpc("kernel_complete", { code: before, cursor: before.length, notebook: state.notebook.name });
    } catch {
      // The engine isn't running: fall back to names in this cell.
    }
  }
  if (ctx.aborted) return null;
  const from = line.from + result.from;
  const base = (word?.text || "").includes(".") ? word.text.slice(0, word.text.lastIndexOf(".") + 1) : "";
  const options = result.items.map((i) => ({ label: i.label, type: CM_TYPES[i.type] || "variable",
    info: intelCompletionInfo(base, i.label) }));  // nbintel.js
  if (!(word?.text || "").includes(".")) {
    const seen = new Set(options.map((o) => o.label));
    for (const m of ctx.state.doc.toString().matchAll(/(?:^|\s)(?:def\s+|class\s+)?([A-Za-z_]\w*)\s*(?==[^=]|\()/gm)) {
      if (!seen.has(m[1])) { seen.add(m[1]); options.push({ label: m[1], type: "variable" }); }
    }
  }
  return options.length ? { from, options, validFor: /^\w*$/ } : null;
}

function editorFor(cell) {
  let view = editors.get(cell.id);
  if (view) return view;
  const keys = cellKeymap(cell);
  view = new CM.EditorView({
    state: CM.EditorState.create({
      doc: cell.source,
      extensions: [
        gutterComp.of(gutters(cell)), cmStyleNonce(),
        CM.EditorView.contentAttributes.of({ "aria-label": cell.type === "markdown" ? "Markdown cell" : "Code cell" }),
        dbgEditorMarks(cell),  // debugging.js: breakpoints and the paused line
        CM.highlightSpecialChars(), CM.history(), CM.drawSelection(), CM.dropCursor(), CM.indentOnInput(),
        CM.bracketMatching(), CM.closeBrackets(), CM.highlightActiveLine(), CM.highlightSelectionMatches(),
        CM.syntaxHighlighting(editorHighlight),
        // Before langComp: its completion keymap, also at highest precedence, would otherwise take Esc
        // while suggestions are still being fetched, with nothing on screen.
        CM.Prec.highest(CM.keymap.of(keys)),
        langComp.of(languageFor(cell)),
        readOnlyComp.of(readOnly(cell)),
        findHighlighter(cell),  // nbfind.js
        dataItemDrop(cell),  // nbdrag.js: tables and files dragged from the Explorer
        CM.keymap.of([...CM.closeBracketsKeymap, ...CM.defaultKeymap, ...CM.searchKeymap, ...CM.historyKeymap,
          ...CM.foldKeymap, ...CM.completionKeymap, CM.indentWithTab]),
        CM.placeholder(cell.type === "markdown" ? "Write Markdown. Press Shift+Enter to render."
          : "Write Python or PySpark. Press Shift+Enter to run, Ctrl+Space for suggestions."),
        CM.EditorView.updateListener.of((u) => {
          if (u.docChanged) {
            const before = `${cellLang(cell)} ${mdMagic(cell)} ${shellMagic(cell)}`;
            cell.source = u.state.doc.toString();
            if (`${cellLang(cell)} ${mdMagic(cell)} ${shellMagic(cell)}` !== before && cell.type === "code") {
              u.view.dispatch({ effects: langComp.reconfigure(languageFor(cell)) });
              const select = document.querySelector(`[data-cell="${cell.id}"] select.lang`);
              if (select) select.value = cellLang(cell);
            }
            markDirty();
            findCellChanged();  // nbfind.js
            if (cell.type === "markdown") refreshOutline();
            else { depsChanged(); dbgEdited(cell, u); }
          }
          if (u.focusChanged && u.view.hasFocus) {
            if (state.selected !== cell.id) selectCell(cell);
            setMode("edit");
          }
        }),
        editorTheme,
      ],
    }),
  });
  editors.set(cell.id, view);
  return view;
}

function cellKeymap(cell) {
  const run = (next, insert) => () => {
    if (cell.type === "markdown") {
      cell.editing = false;
      updateCell(cell);
      refreshOutline();
      if (next) focusNext(cell, false);
      else enterCommandMode(cell);
      return true;
    }
    runCell(cell).then(() => {
      if (insert) addCell("code");
      else if (next) focusNext(cell);
    });
    return true;
  };
  return [
    { key: "Shift-Enter", run: run(true, false) },
    { key: "Mod-Enter", run: run(false, false) },
    { key: "Alt-Enter", run: run(false, true) },
    // A suggestion list on screen takes Esc first (it closes); otherwise Esc leaves for command mode.
    { key: "Escape", run: (view) => {
      if (view.dom.querySelector(".cm-tooltip-autocomplete")) return false;
      view.contentDOM.blur();
      enterCommandMode(cell);
      return true;
    } },
    { key: "Mod-Shift--", run: () => { splitCell(cell); return true; } },  // CodeMirror names keys by their character
    { key: "Mod-/", run: CM.toggleComment },
    { key: "F9", run: () => dbgToggleAtCursor(cell) },
    { key: "Shift-Alt-f", run: () => { formatCells([cell]); return true; } },
  ];
}

// ---------- command mode keys ----------

document.addEventListener("keydown", (e) => {
  if (state.view !== "notebook" || !state.notebook || document.getElementById("dialog-root").childElementCount) return;
  const t = e.target;
  if (t.closest?.(".cm-editor") || ["INPUT", "SELECT", "TEXTAREA"].includes(t.tagName) || t.isContentEditable) return;
  if (!t.closest?.(".cells") && t !== document.body) return;
  const cell = selectedCell();
  const key = e.key;
  const plain = !e.ctrlKey && !e.metaKey && !e.altKey;
  // Tab from a cell in command mode (as after Esc) moves on past the cell, so Tab doesn't loop back into
  // its own buttons and editor (#905). Shift+Tab goes back as usual.
  if (key === "Tab" && !e.shiftKey && plain && t.matches?.(".cell")) {
    const next = [...document.querySelectorAll(FOCUSABLE)].find((el) => el.tabIndex >= 0 && el.checkVisibility()
      && !t.contains(el) && t.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING);
    if (next) { e.preventDefault(); next.focus(); }
    return;
  }
  let handled = true;
  if (key === "Enter" && e.shiftKey) runCell(cell).then(() => focusNext(cell, false));
  else if (key === "Enter" && (e.ctrlKey || e.metaKey)) runCell(cell);
  else if (key === "Enter" && e.altKey) runCell(cell).then(() => addCell("code"));
  else if (key === "Enter" && plain) enterEditMode(cell);
  else if ((key === "ArrowUp" || key === "k") && plain) selectRelative(-1);
  else if ((key === "ArrowDown" || key === "j") && plain) selectRelative(1);
  else if (key === "a" && plain) addCell("code", false, -1);
  else if (key === "b" && plain) addCell("code");
  else if (key === "d" && plain) {
    if (Date.now() - lastDKey < 600) { deleteCell(cell); lastDKey = 0; } else lastDKey = Date.now();
  } else if (key === "z" && plain) undoDelete();
  else if (key === "m" && plain && cell.type === "code") setLanguage(cell, "markdown");
  else if (key === "y" && plain && cell.type === "markdown") setLanguage(cell, "python");
  else if (key === "c" && plain) copyCell(cell);
  else if (key === "x" && plain) { copyCell(cell); deleteCell(cell); }
  else if (key === "v" && plain) pasteCell(cell, 1);
  else if (key === "V" && e.shiftKey) pasteCell(cell, -1);
  else if (key === "L" && e.shiftKey) toggleLineNumbers();
  else if (key === "o" && plain && cell.type === "code") setMeta(cell, "outputs_hidden", !cell.meta?.outputs_hidden);
  else handled = false;
  if (handled) e.preventDefault();
});

// The keys above and the editor's own (editorKeys), listed in the Keyboard shortcuts dialog while a
// notebook is open. The select (nbselect.js) and find (nbfind.js) keys are listed beside their handlers.
{
  const inNotebook = () => state.view === "notebook";
  registerShortcuts("Notebook", [
    { keys: ["Shift+Enter"], label: "Run the cell and select the next one", when: inNotebook },
    { keys: ["Ctrl+Enter"], label: "Run the cell", when: inNotebook },
    { keys: ["Alt+Enter"], label: "Run the cell and add a code cell below", when: inNotebook },
    { keys: ["Esc"], label: "While editing: leave the cell for command mode, where the single-letter keys below work", when: inNotebook },
    { keys: ["Enter"], label: "In command mode: edit the selected cell", when: inNotebook },
    { keys: ["Tab"], label: "In command mode: move on past the cell", when: inNotebook },
    { keys: ["Up", "Down", "K", "J"], label: "In command mode: select the cell above or below", when: inNotebook },
    { keys: ["A", "B"], label: "In command mode: add a code cell above or below", when: inNotebook },
    { keys: ["DD"], label: "In command mode: delete the cell", when: inNotebook },
    { keys: ["Z"], label: "In command mode: put the last deleted cell back", when: inNotebook },
    { keys: ["M", "Y"], label: "In command mode: make the cell Markdown or code", when: inNotebook },
    { keys: ["C", "X"], label: "In command mode: copy or cut the cell", when: inNotebook },
    { keys: ["V", "Shift+V"], label: "In command mode: paste below or above", when: inNotebook },
    { keys: ["O"], label: "In command mode: show or hide the cell's output", when: inNotebook },
    { keys: ["Shift+L"], label: "In command mode: show or hide line numbers", when: inNotebook },
    { keys: ["Ctrl+Space"], label: "While editing: suggest names, attributes and table names", when: inNotebook },
    { keys: ["Ctrl+/"], label: "While editing: comment or uncomment the selected lines", when: inNotebook },
    { keys: ["Ctrl+Shift+-"], label: "While editing: split the cell at the cursor", when: inNotebook },
  ]);
}

// ---------- outputs ----------

// The image types the engine sends (notebook.py _IMAGE_MIMES, plus SVG), as base64.
const IMAGE_OUTPUT_TYPES = new Set(["image/png", "image/jpeg", "image/gif", "image/webp", "image/svg+xml"]);
function imageOutputOk(o) {
  return IMAGE_OUTPUT_TYPES.has(o.mime) && typeof o.data === "string" && /^[A-Za-z0-9+/=\s]*$/.test(o.data);
}

function renderOutput(o, cell) {
  if (o.type === "stream" || o.type === "text") return h("div", { class: "output" }, h("pre", {}, o.text));
  if (o.type === "widget") return renderWidgetOutput(o, cell);  // ipywidgets.js
  if (o.type === "fabric_step") return fabricStepOutput(o, cell);  // fabricstep.js
  if (o.type === "error" && cell && limitStop(o)) return limitOutput(o, cell);
  if (o.type === "error") {
    return h("div", { class: "output error" }, h("div", { class: "ename" }, `${o.ename}: ${o.evalue}`),
      o.traceback ? h("details", {}, h("summary", {}, "Traceback"), h("pre", {}, o.traceback)) : null);
  }
  if (o.type === "table") return renderTableOutput(o, cell);
  if (o.type === "image") {
    // Only picture types become a data: URL (HD-052): a notebook file from elsewhere can carry any type.
    if (!imageOutputOk(o)) return h("div", { class: "output" }, "This output says it is an image, but isn't one KratosForge shows.");
    return h("div", { class: "output" }, h("img", { class: "out-img", alt: "Chart from this cell", src: `data:${o.mime};base64,${o.data}` }));
  }
  if (o.type === "html") return h("div", { class: "output" }, htmlFrame(o.html));
  if (o.type === "vegalite") return renderVegaLite(o);
  if (o.type === "plotly") return renderPlotly(o);
  // Markdown from %%md, IPython.display.Markdown or _repr_markdown_: the Markdown cell's escaping renderer.
  if (o.type === "markdown") return h("div", { class: "output md-view" }, markdown(o.text));
  if (o.type === "json") return h("div", { class: "output json-output" }, jsonTree(o.data, null, 0));
  // Snowflake SQL translated to DuckDB (snowflake_sql.py): the per-statement report, as text.
  if (o.type === "translation") return h("div", { class: "output translation-output" }, h("pre", {}, String(o.text ?? "")));
  return h("div", { class: "output" }, h("pre", {}, JSON.stringify(o)));
}

// application/json output as a tree of text: objects and arrays fold, the first two levels open.
function jsonTree(value, key, depth) {
  const label = key === null ? null : h("span", { class: "json-key" }, `${key}: `);
  if (value === null || typeof value !== "object") {
    const cls = value === null ? "json-null" : typeof value === "string" ? "json-str" : "json-num";
    return h("div", { class: "json-row" }, label, h("span", { class: cls }, value === null ? "null" : typeof value === "string" ? JSON.stringify(value) : String(value)));
  }
  const entries = Array.isArray(value) ? value.map((v, i) => [i, v]) : Object.entries(value);
  const head = Array.isArray(value) ? `[${entries.length}]` : `{${entries.length}}`;
  return h("details", { class: "json-node", open: depth < 2 || null },
    h("summary", {}, label, h("span", { class: "json-count" }, head)),
    entries.slice(0, 500).map(([k, v]) => jsonTree(v, k, depth + 1)),
    entries.length > 500 ? h("div", { class: "json-row muted-text" }, `${entries.length - 500} more`) : null);
}

// A cell stopped by a limit from Settings (kernel_proc.py): "time" (interrupted, variables kept)
// or "memory" (its process ended, variables cleared).
function limitStop(o) {
  if (o.ename === "TimeLimit") return "time";
  if (o.ename !== "KernelStopped" || !/^Stopped: .*limit set in Settings/.test(o.evalue || "")) return null;
  return /the memory limit/.test(o.evalue) ? "memory" : "time";
}

function limitOutput(o, cell) {
  const kind = limitStop(o);
  const [head, ...rest] = o.evalue.split(/(?<=limit set in Settings\.) /);
  return h("div", { class: "output error limit-stop" }, h("b", {}, head), rest.length ? h("div", {}, rest.join(" ")) : null,
    h("div", { class: "row" },
      kind === "time" ? btn("Run again without the limit", "play", () => runCell(cell, { timeLimit: false }), "tool outline small") : null,
      kind === "memory" && typeof depsOn === "function" && depsOn() ? btn("Run stale cells", "restart", () => runStale(state.notebook), "tool outline small") : null,
      btn("Open Settings", "settings", () => go("settings"), "tool outline small")));
}

// HTML output is untrusted (anyone can write a notebook), so it never runs in the app's origin.
// The frame loads a small page from the shell's nbout: scheme (main.rs, OUTPUT_FRAME): another
// origin, sandboxed without allow-same-origin, with its own policy that allows no network. The
// HTML goes in by postMessage once that page loads, and again if the frame reloads; the page
// writes it into itself and reports its height for sizing.
function htmlFrame(html) {
  const frame = h("iframe", { class: "out-html", sandbox: "allow-scripts", title: "HTML output",
    src: window.__TAURI__.core.convertFileSrc("frame", "nbout") });
  frame.addEventListener("load", () => frame.contentWindow?.postMessage({ fdHtml: html }, "*"));
  return frame;
}

// Altair charts (chart_output.py): the Vega-Lite spec, drawn by the Vega libraries (vendor/vega) inside a
// sandboxed output frame, inline, since the frame can't load scripts; the app's page never runs them.
// The libraries load once, when the first chart shows.
let vegaSource = null;
function loadVega() {
  vegaSource ||= new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = "vendor/vega/vega-frame.js";
    script.onload = () => resolve(window.fdVegaSource);
    script.onerror = () => { vegaSource = null; reject(new Error("Could not load the chart libraries. Reload the app and run the cell again.")); };
    document.head.append(script);
  });
  return vegaSource;
}

function vegaPage(source, spec) {
  // JSON is a JS expression; with every < escaped, nothing in the spec can end the script tag.
  const data = JSON.stringify(spec).replace(/</g, "\\u003c");
  return `<!doctype html><meta charset="utf-8"><style>body{margin:0;font:13px "Segoe UI",system-ui,sans-serif}</style>` +
    `<div id="chart"></div><script>${source}<\/script><script>vegaEmbed("#chart", ${data}, { actions: false, renderer: "svg" })` +
    `.catch(function (e) { document.getElementById("chart").textContent = "This chart can't draw: " + (e && e.message || e); });<\/script>`;
}

function renderVegaLite(o) {
  const wrap = h("div", { class: "output" }, h("div", { class: "out-bar" }, previewMarked("altair", h("span", { class: "muted-text" }, "Altair chart"))));
  loadVega().then((source) => wrap.append(htmlFrame(vegaPage(source, o.spec))),
    (err) => wrap.append(h("div", { class: "more", role: "alert" }, friendlyError(err).text)));
  return wrap;
}

// plotly figures (chart_output.py): the figure's JSON, drawn by plotly.js inside a sandboxed output frame,
// inline, as Vega is. plotly.js (about 5 MB) comes from the engine's own plotly package, so it matches the
// figure's version; it's read once, when the first figure shows, rather than sent with every output.
let plotlySource = null;
function loadPlotly() {
  plotlySource ||= rpc("notebook_plotly_js").then((r) => r.source, (err) => { plotlySource = null; throw err; });
  return plotlySource;
}

function plotlyPage(source, figure) {
  // As in vegaPage: with every < escaped, nothing in the figure can end the script tag.
  const data = JSON.stringify(figure).replace(/</g, "\\u003c");
  return `<!doctype html><meta charset="utf-8"><style>body{margin:0}</style><div id="chart"></div>` +
    `<script>${source}<\/script><script>var f = ${data};` +
    `Plotly.newPlot("chart", f.data || [], f.layout || {}, Object.assign({ responsive: true }, f.config || {}))` +
    `.catch(function (e) { document.getElementById("chart").textContent = "This chart can't draw: " + (e && e.message || e); });<\/script>`;
}

function renderPlotly(o) {
  const wrap = h("div", { class: "output" });
  loadPlotly().then((source) => wrap.append(htmlFrame(plotlyPage(source, o.figure))),
    (err) => wrap.append(h("div", { class: "more", role: "alert" }, friendlyError(err).text)));
  return wrap;
}

window.addEventListener("message", (e) => {
  const height = e.data?.fdFrameHeight;
  if (typeof height !== "number") return;
  document.querySelectorAll("iframe.out-html").forEach((f) => {
    if (f.contentWindow === e.source) f.style.height = `${Math.min(Math.max(height + 4, 40), 1400)}px`;
  });
});

const isNumber = (v) => typeof v === "number";

function numericColumns(o) {
  return o.columns.map((_, i) => o.rows.length > 0 && o.rows.every((r) => r[i] === null || isNumber(r[i])) && o.rows.some((r) => isNumber(r[i])));
}

// A table result with Fabric's views: Table (search, sort), Chart, Inspect (a summary per column), and
// Clean (Data Wrangler, wrangler.js) when the kernel holds the whole result.
function renderTableOutput(o, cell) {
  o.view ||= o.summary ? "inspect" : o.frame && cell?.meta?.chart ? "chart" : "table";
  const wrap = h("div", { class: "output table-output" });
  const redraw = () => wrap.replaceWith(renderTableOutput(o, cell));
  const seg = (id, label) => h("button", { class: `seg${o.view === id ? " on" : ""}`, "aria-pressed": String(o.view === id),
    onclick: () => { o.view = id; redraw(); } }, label);
  // Several charts of one result (Databricks' "+ Visualization"): "chart" is the first, "chart-2" the second...
  const many = o.frame && cell && typeof chartCount === "function";
  const charts = many ? chartCount(cell) : 1;
  if (/^chart-\d+$/.test(o.view) && +o.view.slice(6) > charts) o.view = "chart";
  const chartSegs = Array.from({ length: charts }, (_, i) => seg(i ? `chart-${i + 1}` : "chart", many ? chartName(cell, i) : "Chart"));
  const add = many && charts < MAX_CHARTS ? previewMarked("severalCharts", h("button", { class: "seg seg-add", "aria-label": "Add a chart of this result",
    onclick: () => { o.view = `chart-${addChart(o, numericColumns(o), cell) + 1}`; redraw(); } }, "+ Chart")) : null;
  const bar = h("div", { class: "out-bar" }, h("div", { class: "segs", role: "group", "aria-label": "View" },
    seg("table", "Table"), chartSegs, add, seg("inspect", "Inspect"), o.frame ? seg("clean", "Clean") : null,
    o.frame && o.spark ? seg("qprofile", "Query profile") : null));
  wrap.append(bar);
  const numeric = numericColumns(o);
  const count = typeof o.total_rows === "number"
    ? (o.total_rows > o.rows.length ? `Showing the first ${o.rows.length} of ${o.total_rows} rows` : `${o.total_rows} row${o.total_rows === 1 ? "" : "s"}`)
    : `Showing the first ${o.rows.length} rows (${o.total_rows})`;

  if (o.view === "table" && o.frame) {
    renderResultViewer(o, wrap, bar, numeric, count);
  } else if (o.view === "table") {
    const search = h("input", { type: "search", class: "out-search", placeholder: "Search rows", value: o.q || "", "aria-label": "Search rows" });
    bar.append(h("span", { class: "spacer" }), search);
    const body = h("div", { class: "out-table-scroll" });
    const draw = () => {
      let rows = o.rows;
      const q = (o.q || "").toLowerCase();
      if (q) rows = rows.filter((r) => r.some((v) => v !== null && String(v).toLowerCase().includes(q)));
      if (o.sort) {
        const { i, dir } = o.sort;
        rows = [...rows].sort((a, b) => (a[i] === b[i] ? 0 : a[i] === null ? 1 : b[i] === null ? -1 : a[i] < b[i] ? -dir : dir));
      }
      body.replaceChildren(resultTable(o.columns, o.types, rows, numeric, o.sort, (i) => { o.sort = o.sort?.i === i ? { i, dir: -o.sort.dir } : { i, dir: 1 }; draw(); }));
      wrap.querySelector(".more").textContent = q ? `${rows.length} matching of ${count.toLowerCase()}` : count;
    };
    search.addEventListener("input", () => { o.q = search.value; draw(); });
    wrap.append(body, h("div", { class: "more" }, count));
    draw();
  } else if ((o.view === "chart" || /^chart-\d+$/.test(o.view)) && o.frame) {
    bar.append(h("span", { class: "spacer" }), previewMarked("chartBuilder", h("span", { class: "muted-text" }, "Chart of every row")));
    const i = o.view === "chart" ? 0 : +o.view.slice(6) - 1;
    wrap.append(renderChartBuilder(o, numeric, cell, i, (n, what) => { if (what === "removed") o.view = n > 1 ? `chart-${n}` : "chart"; redraw(); }));
  } else if (o.view === "chart") {
    wrap.append(renderChart(o, numeric), h("div", { class: "more" }, `Chart of ${count.toLowerCase()}`));
  } else if (o.view === "clean" && o.frame) {
    bar.append(h("span", { class: "spacer" }), previewMarked("dataWrangler", h("span", { class: "muted-text" }, "Data Wrangler")));
    wrap.append(renderWrangler(o, cell));
  } else if (o.view === "qprofile" && o.frame) {
    bar.append(h("span", { class: "spacer" }), previewMarked("queryProfile", h("span", { class: "muted-text" }, "Query profile")));
    wrap.append(renderQueryProfile(o));
  } else if (o.frame && !o.profileGone) {
    wrap.append(renderFrameProfile(o, numeric, count));
  } else {
    wrap.append(renderInspect(o, numeric), h("div", { class: "more" }, `Summary of ${count.toLowerCase()}`));
  }
  return wrap;
}

// The table of a result: headers sort (▲ ▼), each with its column type, as Fabric and Databricks show.
function resultTable(columns, types, rows, numeric, sort, onSort) {
  return h("table", {},
    h("thead", {}, h("tr", {}, columns.map((c, i) => h("th", { class: numeric[i] ? "n" : null },
      h("button", { class: "th-sort", onclick: () => onSort(i) }, c, sort?.i === i ? (sort.dir > 0 ? " ▲" : " ▼") : ""),
      types?.[i] ? h("span", { class: "th-type" }, types[i]) : null)))),
    h("tbody", {}, rows.map((r) => h("tr", {}, r.map((v, i) => h("td", { class: numeric[i] ? "n" : null }, v === null ? "null" : v))))));
}

const RESULT_PAGE = 100;

// The whole result behind a table output (frames.py): search and sort over every row, pages of
// 100, Copy and Download. The kernel holds the result; after a restart the cell must run again.
function renderResultViewer(o, wrap, bar, numeric, count) {
  const v = o.viewer ||= { offset: 0, q: "", sort: null, page: null, gone: false, busy: false };
  const search = h("input", { type: "search", class: "out-search", placeholder: "Search all rows", value: v.q, "aria-label": "Search all rows" });
  const status = h("span", { class: "more result-range", role: "status" });
  const prev = btn("Previous", null, () => load(v.offset - RESULT_PAGE), "tool outline small");
  const next = btn("Next", null, () => load(v.offset + RESULT_PAGE), "tool outline small");
  const body = h("div", { class: "out-table-scroll" });
  const menu = h("details", { class: "sql-export result-download" },
    h("summary", { class: "tool outline small", "aria-label": "Download the whole result" }, "Download ▾"),
    h("div", { class: "sql-export-menu", role: "menu" },
      [["csv", "CSV"], ["xlsx", "Excel"], ["parquet", "Parquet"]].map(([fmt, label]) =>
        h("button", { role: "menuitem", onclick: (e) => { e.target.closest("details").open = false; downloadResult(o, fmt); } }, label))));
  bar.append(h("span", { class: "spacer" }), search, btn("Copy", "document", () => copyResultPage(o), "tool outline small"), previewMarked("resultViewer", menu));
  wrap.append(body, h("div", { class: "row result-pager" }, prev, next, status));

  const draw = () => {
    const p = v.page;
    const rows = p ? p.rows : o.rows.slice(0, RESULT_PAGE);
    const total = p ? p.total : (typeof o.total_rows === "number" ? o.total_rows : null);
    body.replaceChildren(resultTable(o.columns, o.types, rows, numeric, v.sort, (i) => {
      v.sort = v.sort?.i === i ? { i, dir: -v.sort.dir } : { i, dir: 1 };
      return load(0);
    }));
    prev.disabled = v.busy || v.offset === 0;
    next.disabled = v.busy || (total !== null ? v.offset + rows.length >= total : !p && rows.length < RESULT_PAGE);
    status.textContent = v.gone ? "Run the cell again to page, search or download: the kernel no longer holds this result."
      : !rows.length ? (v.q ? `No rows match "${v.q}"` : "No rows")
        : `Rows ${(v.offset + 1).toLocaleString()} to ${(v.offset + rows.length).toLocaleString()} of ${total !== null ? total.toLocaleString() : count.toLowerCase()}${v.q ? " matching" : ""}`;
  };
  async function load(offset) {
    const ask = (v.asked = (v.asked || 0) + 1);
    v.busy = true;
    draw();
    try {
      const p = await rpc("notebook_frame_page", { frame: o.frame, offset: Math.max(0, offset), limit: RESULT_PAGE,
        sort: v.sort ? v.sort.i : null, desc: v.sort ? v.sort.dir < 0 : false, q: v.q });
      if (ask !== v.asked) return; // a newer search or page was asked for while this one ran
      if (p.gone) v.gone = true;
      else { v.page = p; v.offset = p.offset; }
    } catch (err) {
      if (ask !== v.asked) return;
      toast(`Could not load rows: ${friendlyError(err).text}`, true);
    }
    v.busy = false;
    draw();
  }
  let typing;
  search.addEventListener("input", () => { clearTimeout(typing); typing = setTimeout(() => { v.q = search.value; load(0); }, 250); });
  draw();
}

function copyResultPage(o) {
  const p = o.viewer?.page;
  const rows = p ? p.rows : o.rows.slice(0, RESULT_PAGE);
  const text = [o.columns, ...rows].map((r) => r.map((v) => (v === null ? "" : String(v).replace(/[\t\n]/g, " "))).join("\t")).join("\n");
  attempt(() => navigator.clipboard.writeText(text).then(() => toast(`Copied ${rows.length} row${rows.length === 1 ? "" : "s"}. Paste into Excel or a sheet.`)), "Could not copy");
}

// The whole result, written by the kernel to a new file in Downloads; a file there is never replaced.
async function downloadResult(o, format) {
  const label = { csv: "CSV", xlsx: "Excel", parquet: "Parquet" }[format];
  toast(`Downloading the whole result as ${label}...`);
  const out = await attempt(() => rpc("notebook_frame_download", { frame: o.frame, format, name: state.notebook?.name || "notebook" }), "Could not download");
  if (!out) return;
  if (out.gone) { toast(out.message, true); return; }
  toast(`Saved ${out.rows.toLocaleString()} row${out.rows === 1 ? "" : "s"} to ${out.path}`);
}

function renderChart(o, numeric) {
  const nums = o.columns.filter((_, i) => numeric[i]);
  const cats = o.columns.filter((_, i) => !numeric[i]);
  const cfg = o.chart ||= { kind: "bar", x: cats[0] || o.columns[0], y: nums.find((c) => c !== (cats[0] || o.columns[0])) || nums[0] || null, agg: "sum" };
  const box = h("div", { class: "chart-box" });
  const select = (label, key, options) => h("label", { class: "chart-opt" }, label, h("select", {
    onchange: (e) => { cfg[key] = e.target.value; box.replaceWith(renderChart(o, numeric)); } },
  options.map(([v, text]) => h("option", { value: v, selected: cfg[key] === v || null }, text))));
  box.append(h("div", { class: "chart-opts" },
    select("Chart", "kind", [["bar", "Bar"], ["line", "Line"], ["scatter", "Scatter"]]),
    select("X axis", "x", o.columns.map((c) => [c, c])),
    select("Values", "y", (nums.length ? nums : o.columns).map((c) => [c, c])),
    cfg.kind !== "scatter" ? select("Aggregate", "agg", [["sum", "Sum"], ["avg", "Average"], ["count", "Count"], ["max", "Max"], ["min", "Min"]]) : null));
  if (!cfg.y && cfg.agg !== "count") {
    box.append(h("div", { class: "muted-text", style: "padding:12px 0" }, "This result has no number columns to chart. Pick Count to count rows by the X axis."));
    return box;
  }
  const xi = o.columns.indexOf(cfg.x);
  const yi = o.columns.indexOf(cfg.y);
  let points;
  if (cfg.kind === "scatter") {
    points = o.rows.filter((r) => isNumber(r[xi]) && isNumber(r[yi])).map((r) => [r[xi], r[yi]]);
  } else {
    const groups = new Map();
    for (const r of o.rows) {
      const k = r[xi] === null ? "null" : String(r[xi]);
      const g = groups.get(k) || { n: 0, sum: 0, max: -Infinity, min: Infinity };
      const v = r[yi];
      g.n += 1;
      if (isNumber(v)) { g.sum += v; g.max = Math.max(g.max, v); g.min = Math.min(g.min, v); }
      groups.set(k, g);
    }
    const value = (g) => ({ sum: g.sum, avg: g.n ? g.sum / g.n : 0, count: g.n, max: g.max, min: g.min }[cfg.agg]);
    points = [...groups.entries()].slice(0, 60).map(([k, g]) => [k, value(g)]);
    if (cfg.kind === "line" && numeric[xi]) points.sort((a, b) => Number(a[0]) - Number(b[0]));
  }
  box.append(svgChart(points, cfg));
  return box;
}

function svgChart(points, cfg) {
  const W = 720, H = 280, L = 56, R = 16, T = 12, B = 56;
  const ns = "http://www.w3.org/2000/svg";
  const el = (tag, attrs, text) => {
    const n = document.createElementNS(ns, tag);
    Object.entries(attrs).forEach(([k, v]) => n.setAttribute(k, v));
    if (text != null) n.textContent = text;
    return n;
  };
  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, class: "chart", role: "img", "aria-label": `${cfg.kind} chart of ${cfg.y || "count"} by ${cfg.x}` });
  if (!points.length) {
    svg.append(el("text", { x: W / 2, y: H / 2, "text-anchor": "middle", class: "chart-label" }, "Nothing to chart"));
    return svg;
  }
  const ys = points.map((p) => p[1]).filter(isFinite);
  const yMax = Math.max(0, ...ys), yMin = Math.min(0, ...ys);
  const span = yMax - yMin || 1;
  const y = (v) => T + (H - T - B) * (1 - (v - yMin) / span);
  const fmt = (v) => (Math.abs(v) >= 1e6 ? `${(v / 1e6).toFixed(1)}M` : Math.abs(v) >= 1e3 ? `${(v / 1e3).toFixed(1)}K` : `${+v.toFixed(2)}`);
  for (let k = 0; k <= 4; k++) {
    const v = yMin + (span * k) / 4;
    svg.append(el("line", { x1: L, x2: W - R, y1: y(v), y2: y(v), class: "chart-grid" }),
      el("text", { x: L - 6, y: y(v) + 4, "text-anchor": "end", class: "chart-label" }, fmt(v)));
  }
  const plotW = W - L - R;
  if (cfg.kind === "scatter") {
    const xs = points.map((p) => p[0]);
    const xMin = Math.min(...xs), xSpan = Math.max(...xs) - xMin || 1;
    points.forEach(([px, py]) => svg.append(el("circle", { cx: L + (plotW * (px - xMin)) / xSpan, cy: y(py), r: 3.5, class: "chart-dot" })));
    svg.append(el("text", { x: L, y: H - 8, class: "chart-label" }, fmt(xMin)),
      el("text", { x: W - R, y: H - 8, "text-anchor": "end", class: "chart-label" }, fmt(xMin + xSpan)));
    return svg;
  }
  const step = plotW / points.length;
  const label = (i, text) => {
    const every = Math.ceil(points.length / 14);
    if (i % every) return;
    const t = String(text);
    svg.append(el("text", { x: L + step * (i + 0.5), y: H - B + 16, "text-anchor": "middle", class: "chart-label" }, t.length > 12 ? `${t.slice(0, 11)}…` : t));
  };
  if (cfg.kind === "bar") {
    points.forEach(([k, v], i) => {
      const bw = Math.max(2, step * 0.7);
      const top = y(Math.max(v, 0)), bottom = y(Math.min(v, 0));
      const rect = el("rect", { x: L + step * i + (step - bw) / 2, y: top, width: bw, height: Math.max(1, bottom - top), class: "chart-bar", rx: 2 });
      rect.append(el("title", {}, `${k}: ${fmt(v)}`));
      svg.append(rect);
      label(i, k);
    });
  } else {
    const d = points.map(([, v], i) => `${i ? "L" : "M"}${L + step * (i + 0.5)},${y(v)}`).join(" ");
    svg.append(el("path", { d, class: "chart-line" }));
    points.forEach(([k, v], i) => {
      const dot = el("circle", { cx: L + step * (i + 0.5), cy: y(v), r: 3, class: "chart-dot" });
      dot.append(el("title", {}, `${k}: ${fmt(v)}`));
      svg.append(dot);
      label(i, k);
    });
  }
  return svg;
}

// Inspect over every row (frames.py profile), as display(df, summary=True) and Databricks' data
// profile show it. Until the profile arrives, the preview's summary shows; after a kernel restart
// the preview's summary stays, with a note.
function renderFrameProfile(o, numeric, count) {
  const box = h("div", { class: "profile" });
  const show = (p) => {
    const fmt = (v) => (v === null || v === undefined ? "" : typeof v === "number" ? (Number.isInteger(v) ? v.toLocaleString() : +v.toPrecision(6)) : String(v));
    box.replaceChildren(h("div", { class: "inspect" }, p.columns.map((c) => {
      if (c.error) return h("div", { class: "inspect-col" }, h("div", { class: "inspect-name" }, c.name, h("span", {}, c.type)), h("div", { class: "muted-text" }, c.error));
      const stats = [["Missing", fmt(c.missing)], ["Distinct", `≈ ${fmt(c.distinct)}`]];
      if (c.infinite) stats.push(["Infinite", fmt(c.infinite)]);
      let picture = null;
      if (c.histogram) {
        stats.push(["Min", fmt(c.min)], ["Max", fmt(c.max)], ["Mean", fmt(c.mean)], ["Std dev", fmt(c.std)],
          ["Q1", fmt(c.q1)], ["Median", fmt(c.median)], ["Q3", fmt(c.q3)]);
        const top = Math.max(...c.histogram, 1);
        picture = h("div", { class: "hist", role: "img", "aria-label": `Distribution of ${c.name} from ${fmt(c.min)} to ${fmt(c.max)}` },
          c.histogram.map((b) => h("span", { style: `height:${Math.round((b / top) * 100)}%`, title: b.toLocaleString() })));
      } else if (c.kind === "date" && c.min !== undefined) {
        stats.push(["Earliest", c.min], ["Latest", c.max]);
      } else if (c.top) {
        picture = h("ul", { class: "top-values" }, c.top.map(([v, n]) => {
          const t = v === null ? "null" : String(v);
          return h("li", {}, h("span", { title: t }, t.length > 24 ? `${t.slice(0, 23)}…` : t), h("b", {}, n.toLocaleString()));
        }));
      }
      return h("div", { class: "inspect-col" }, h("div", { class: "inspect-name" }, c.name, h("span", {}, c.type)),
        picture, h("dl", {}, stats.map(([k, v]) => [h("dt", {}, k), h("dd", {}, v)])));
    })), h("div", { class: "more" }, (p.sampled ? `Profile of a random sample of ${p.profiled_rows.toLocaleString()} of ${p.rows.toLocaleString()} rows`
      : `Profile of all ${p.rows.toLocaleString()} row${p.rows === 1 ? "" : "s"}`) +
      (p.more_columns ? `. ${p.more_columns} more column${p.more_columns === 1 ? " isn't" : "s aren't"} profiled; select fewer columns to see them.` : "")));
  };
  if (o.profile) { show(o.profile); return box; }
  box.append(renderInspect(o, numeric), h("div", { class: "more", role: "status" }, `Summary of ${count.toLowerCase()}. Profiling every row...`));
  rpc("notebook_frame_profile", { frame: o.frame }).then((p) => {
    if (p.gone) {
      o.profileGone = true;
      box.querySelector(".more").textContent = `Summary of ${count.toLowerCase()}. Run the cell again to profile every row.`;
    } else { o.profile = p; show(p); }
  }, (err) => { box.querySelector(".more").textContent = `Summary of ${count.toLowerCase()}. Could not profile every row: ${friendlyError(err).text}`; });
  return box;
}

function renderInspect(o, numeric) {
  return h("div", { class: "inspect" }, o.columns.map((c, i) => {
    const values = o.rows.map((r) => r[i]);
    const present = values.filter((v) => v !== null && v !== "nan");
    const distinct = new Set(present.map(String)).size;
    const stats = [["Missing", values.length - present.length], ["Distinct", distinct]];
    let picture = null;
    if (numeric[i] && present.length) {
      const nums = present.filter(isNumber);
      const min = Math.min(...nums), max = Math.max(...nums);
      stats.push(["Min", +min.toFixed(4)], ["Max", +max.toFixed(4)], ["Mean", +(nums.reduce((a, b) => a + b, 0) / nums.length).toFixed(4)]);
      const bins = new Array(12).fill(0);
      nums.forEach((v) => { bins[Math.min(11, Math.floor(((v - min) / (max - min || 1)) * 12))] += 1; });
      const top = Math.max(...bins);
      picture = h("div", { class: "hist", role: "img", "aria-label": `Distribution of ${c}` }, bins.map((b) => h("span", { style: `height:${Math.round((b / top) * 100)}%` })));
    } else if (present.length) {
      const counts = new Map();
      present.forEach((v) => counts.set(String(v), (counts.get(String(v)) || 0) + 1));
      const topValues = [...counts.entries()].sort((a, b) => b[1] - a[1]).slice(0, 4);
      picture = h("ul", { class: "top-values" }, topValues.map(([v, n]) => h("li", {}, h("span", {}, v.length > 24 ? `${v.slice(0, 23)}…` : v), h("b", {}, n))));
    }
    return h("div", { class: "inspect-col" }, h("div", { class: "inspect-name" }, c, h("span", {}, numeric[i] ? "number" : "text")),
      picture, h("dl", {}, stats.map(([k, v]) => [h("dt", {}, k), h("dd", {}, v)])));
  }));
}

// ---------- running cells ----------

// `code` runs part of the cell (Ctrl+Shift+Enter, nbselect.js): its output replaces the cell's, and
// the cell isn't marked as run for dependencies, since the rest of it didn't run.
async function runCell(cell, { timeLimit = true, code = null, debug = false, nb = state.notebook } = {}) {
  if (cell.type !== "code") return true;
  // engines.js: on another engine the cell runs there; Run this cell on Python sets onKernel for one run here.
  const kernel = cell.onKernel === true;
  delete cell.onKernel;
  if (!kernel && nbOnEngine(nb) && !cell.meta?.frozen) return runEngineCell(cell, nb);
  // ``nb`` keeps running in its own session if another notebook is opened meanwhile.
  if (!kernel && nbWhere(nb).fabric) {
    toast("This notebook runs in Fabric, which runs the whole notebook. Use Run all, or switch Runs on to This PC to run one cell.");
    return false;
  }
  if (cell.meta?.frozen) {
    toast("This cell is frozen, so it doesn't run. Unfreeze it on the Edit tab.");
    return true;
  }
  if (isFabricStep(cell)) return runFabricStep(cell, nb);  // fabricstep.js: on Fabric Spark, not this PC
  if (kernelBusy(nb) && cell.status !== "queued") {
    const other = nb.kernelStatus !== "busy" && [...openNotebooks.values()].find((o) => o.kernelStatus === "busy");
    if (other) toast(`A cell is running in ${other.name}. Notebooks share one kernel unless Run notebooks in their own process is on in Settings.`);
    return false;
  }
  nb.stopRequested = false;
  setKernel("busy", nb);
  cell.status = "running";
  cell.outputs = [];
  cell.seconds = 0;
  cell.engineFlag = null;
  cell.ranOn = kernel && nbOnEngine(nb) ? "Python (this PC), this cell only" : null;
  cell.rows = null;
  updateResults(cell);
  cell.hidden = null;
  const started = performance.now();
  let ok = false;
  let last = null;
  try {
    const watches = await dbgWatches(nb);  // debugging.js
    await nbInputsSaved();  // nbinputs.js: a value just changed in the bar is the one that runs
    let snap = await rpc("start_cell", { code: cell.source + nbInputSuffix(cell), lakehouse_ref: nbLakehouse(nb), notebook: nb.name,
      ...(timeLimit ? {} : { time_limit: false }), ...(watches.length ? { watches } : {}), ...(code != null ? { code } : dbgStartArgs(cell, debug)) });
    for (;;) {
      if (snap.outputs) cell.outputs = snap.outputs;
      cell.count = snap.execution_count;
      cell.seconds = snap.seconds;
      const pauseMoved = dbgSnapshot(cell, snap);  // debugging.js
      cell.progress = snap.status === "running" ? snap.progress ?? null : null;  // a DuckDB query's %, while one runs
      if (snap.status !== "running") break;
      // While paused, redraw only when the pause changes, so the debugger's input keeps what's typed.
      if (state.notebook?.cells.includes(cell) && (!cell.paused || pauseMoved || snap.outputs)) updateResults(cell);
      snap = await rpc("poll_cell", { version: snap.version, wait: 0.4, notebook: nb.name });
    }
    cell.status = snap.status;
    last = snap;
    ok = snap.status === "ok";
    // Its process ended (a crash, Stop, the memory limit): variables are gone.
    if ((snap.outputs || cell.outputs).some((o) => o.ename === "KernelStopped") && state.notebook === nb) depsRestarted();
  } catch (err) {
    if (nb.stopRequested) {
      cell.status = "stopped";
      cell.outputs = [{ type: "text", text: "Stopped. The kernel restarted, so variables from earlier cells are gone." }];
    } else {
      cell.status = "error";
      cell.outputs = [{ type: "error", ename: "Kernel error", evalue: String(err) }];
      // The engine was ended (stuck or crashed), and the kernel with it.
      if (/^(The engine stopped answering|The Python sidecar stopped)/.test(String(err)) && state.notebook === nb) depsRestarted();
    }
    cell.seconds = (performance.now() - started) / 1000;
  }
  if (cell.paused) dbgSnapshot(cell, null);
  cell.finishedAt = timeText(new Date());
  cell.partial = code != null;
  setKernel(nb.stopRequested ? "stopped" : "idle", nb);
  if (state.notebook !== nb) return ok;  // finished in the background; reopening shows it
  if (code == null) depsRan(cell, ok);
  if (state.notebook?.cells.includes(cell)) updateResults(cell);
  // A failed cell inside a collapsed section: open the section so the error can be seen.
  if (cell.status === "error" && state.notebook?.cells.includes(cell) && revealCell(cell)) {
    document.querySelector(`[data-cell="${cell.id}"]`)?.scrollIntoView({ block: "nearest" });
  }
  if (state.notebook?.dirty) markDirty();
  if (!(last && await dbgAfterRun(cell, last))) refreshVariables();
  cellFx(cell);  // after dbgAfterRun, which draws the results again
  return ok;
}

// Forge animations (motion.js): a spark when a cell succeeds on its own (the Wolf Run in the status
// bar instead when it took LONG_RUN_SECONDS or more), an ember going out when it fails, and one
// announcement for screen readers. Run all shows its progress track instead of a spark per cell;
// Stop and Cancel show nothing.
function cellFx(cell) {
  if (cell.status !== "ok" && cell.status !== "error") return;
  const n = (state.notebook?.cells.indexOf(cell) ?? -1) + 1;
  const runningAll = state.notebook?.runningAll;
  if (!runningAll) announce(cell.status === "ok" ? `Cell ${n} succeeded` : `Cell ${n} failed`);
  if (cell.status === "ok" && runningAll) return;
  if (cell.status === "ok" && cell.seconds >= LONG_RUN_SECONDS && forgeEvent("notebook.long.succeeded", document.querySelector(".statusbar"))) return;
  forgeEvent(cell.status === "ok" ? "notebook.cell.succeeded" : "notebook.cell.failed",
    document.querySelector(`[data-cell="${cell.id}"] .cell-status > span`));
}

async function runCells(cells) {
  const nb = state.notebook;
  const code = cells.filter((c) => c.type === "code" && c.source.trim() && !c.meta?.frozen);
  if (!(await fabricStepPrecheck(nb, code))) return;  // fabricstep.js: before any cell runs
  nb.cancelRequested = false;
  const started = performance.now();
  const done = (ran) => {
    if (code.length < 2) return;
    // whole: the run covered every cell Run all would run, not a selection (nbnext.js offers Publish only then).
    const whole = nb.cells.filter((c) => c.type === "code" && c.source.trim() && !c.meta?.frozen).length === code.length;
    nb.lastRun = { ran, of: code.length, seconds: (performance.now() - started) / 1000, whole };
    const el = state.notebook === nb && document.querySelector(".statusbar .last-run");
    if (el) el.textContent = nbLastRunText(nb.lastRun);
    refreshNbNextLine(nb);  // nbnext.js: the next step after the run
  };
  code.forEach((c) => { c.status = "queued"; c.outputs = []; });
  setKernel("busy", nb);
  renderNotebook();
  // Cells done of cells to run, in the status bar, with the wolf on it (motion.js).
  const track = code.length > 1 ? wolfTrack(() => document.querySelector(".statusbar"), "Notebook run", "Cell", "on this PC") : null;
  track?.set(0, code.length);
  if (code.length > 1) forgeEvent("notebook.run.started", document.querySelector("button[data-run]"));
  nb.runningAll = true;
  try {
    for (const [i, cell] of code.entries()) {
      const ok = await runCell(cell, { nb });  // goes on in nb's session if another notebook is opened
      if (!ok || nb.cancelRequested) {
        track?.stop();
        code.slice(i + 1).forEach((c) => { c.status = null; if (state.notebook === nb) updateResults(c); });
        setKernel(nb.stopRequested ? "stopped" : "idle", nb);
        announce(cell.status === "error" ? `Run stopped: cell ${nb.cells.indexOf(cell) + 1} failed` : "Run stopped");
        done(ok ? i + 1 : i);
        return;
      }
      track?.set(i + 1, code.length);
      setKernel("busy", nb);
    }
    track?.arrive();
    if (code.length) announce(`Run finished: ${code.length} ${code.length === 1 ? "cell" : "cells"} succeeded`);
    setKernel(nb.stopRequested ? "stopped" : "idle", nb);
  } finally {
    nb.runningAll = false;
  }
  done(code.length);
}

function nbLastRunText(r) {
  return r ? `Last run: ${r.ran === r.of ? `${r.of} cells` : `${r.ran} of ${r.of} cells`} in ${r.seconds.toFixed(1)} s` : "";
}

function runAll() {
  if (sbsOn(state.notebook)) return runSideBySide();  // sidebyside.js
  if (nbOnEngine(state.notebook)) return runCells(state.notebook.cells);  // engines.js
  return nbWhere(state.notebook).fabric ? runInFabric() : runCells(state.notebook.cells);
}

function runAbove() {
  const cells = state.notebook.cells;
  return runCells(cells.slice(0, cells.indexOf(selectedCell())));
}

function runBelow() {
  const cells = state.notebook.cells;
  return runCells(cells.slice(cells.indexOf(selectedCell())));
}

// Inside the engine every notebook shares one kernel, so a cell running anywhere makes it busy.
function kernelBusy(nb) {
  if (nb?.kernelStatus === "busy") return true;
  return !kernelInOwnProcess() && [...openNotebooks.values()].some((o) => o.kernelStatus === "busy");
}

function setKernel(status, nb = state.notebook) {
  if (!nb || nb.kernelStatus === status) return;
  nb.kernelStatus = status;
  if (state.view !== "notebook" || nb !== state.notebook) return;
  const bar = document.querySelector(".statusbar span");
  if (bar) bar.replaceChildren(h("span", { class: `dot ${status}` }), kernelLabel());
  document.querySelectorAll("[data-run]").forEach((b) => { b.disabled = status === "busy" || b.hasAttribute("data-local-only"); });
  document.querySelectorAll("[data-stop]").forEach((b) => { b.disabled = status !== "busy"; });
}

// Cancel stops the running cell and keeps variables. Code stuck inside a native library can't be
// stopped that way; then restarting the kernel is the way out, and it clears variables.
async function cancelRun() {
  if (sbsRunning(state.notebook)) return cancelSideBySide(state.notebook);
  if (proveRunning(state.notebook)) return cancelProof(state.notebook);
  if (state.notebook?.engineRunning) return cancelEngineCell(state.notebook);  // engines.js
  const nb = state.notebook;
  if (nb) nb.cancelRequested = true;
  nb?.cells.forEach((c) => { if (c.status === "queued") { c.status = null; updateResults(c); } });
  const res = await attempt(() => rpc("interrupt_cell", { notebook: nb?.name }), "Could not cancel");
  if (res?.interrupted && !res.stopped) {
    const overlay = showDialog("The cell is still running", h("p", { class: "dialog-text" },
      "It is inside a library call that can't be interrupted. Restarting the kernel stops it, but clears your variables."), [
      btn("Keep waiting", null, closeDialog, "tool outline"),
      btn("Restart kernel", null, () => { closeDialog(); stopKernel(); }, "tool primary"),
    ]);
    overlay.querySelector(".tool.primary")?.focus();
  }
}

// With notebooks in their own process (Settings), Stop ends only that process. Otherwise the
// kernel is inside the engine, and the shell ends the engine.
function kernelInOwnProcess() {
  return state.settings?.kernel_process?.value === true;
}

async function stopKernel() {
  const nb = state.notebook;
  if (nb) Object.assign(nb, { stopRequested: true, cancelRequested: true });
  depsRestarted();
  if (kernelInOwnProcess()) {
    const out = await attempt(() => rpc("stop_kernel_process", { notebook: nb?.name }), "Could not stop the kernel");
    if (out && !out.in_engine) {  // stopped, or nothing was running
      state.notebook?.cells.forEach((c) => { if (c.status === "queued") c.status = null; });
      return;
    }
  }
  await attempt(() => invoke("stop_kernel"), "Could not stop the kernel");
  state.notebook?.cells.forEach((c) => { if (c.status === "queued") c.status = null; });
}

async function restartKernel() {
  if (proveRunning(state.notebook)) {
    toast("A proof is using the kernel. Cancel the proof first.", true);
    return;
  }
  if (state.kernel === "busy") {
    await stopKernel();
  } else if (await attempt(() => rpc("restart_kernel", { notebook: state.notebook?.name }), "Could not restart")) {
    state.notebook?.cells.forEach((c) => { c.count = null; });
    depsRestarted();
    state.kernel = "idle";
    state.variables = [];
    toast("Kernel restarted. Variables were cleared.");
    if (state.view === "notebook") renderNotebook();
  }
}

registerFeature({ id: "notebook", views: { notebook: renderNotebook } });
