// Several cells selected at once, and running part of a notebook, as in Fabric and Databricks:
// Shift+click or Shift+Up/Down (Shift+J/K) selects a range of cells, Ctrl+click adds or removes one.
// Run selected cells, delete (DD), copy (C), cut (X), move, freeze and merge (Shift+M) then act
// on all of them. Ctrl+Shift+Enter while editing runs only the selected text, or the line the
// cursor is on, in the cell's language.

const nbSel = { anchor: null, ids: new Set(), lastD: 0 };

// The selected cells in notebook order; one cell when nothing else is selected.
function selectedCells() {
  if (!nbSel.ids.size) return [selectedCell()];
  return state.notebook.cells.filter((c) => nbSel.ids.has(c.id));
}

const multiSelected = () => nbSel.ids.size > 1;

function selectionPaint() {
  document.querySelectorAll(".cell.multi").forEach((el) => el.classList.remove("multi"));
  if (!multiSelected()) return;
  nbSel.ids.forEach((id) => document.querySelector(`[data-cell="${id}"]`)?.classList.add("multi"));
}

// The strip over the cells while several are selected, with what can be done to all of them.
function selectionBar() {
  if (!multiSelected()) return null;
  const n = nbSel.ids.size;
  const fabric = nbWhere(state.notebook).fabric;
  return h("div", { class: "nb-sel-bar", role: "toolbar", "aria-label": "Selected cells" },
    h("b", {}, `${n} cells selected`),
    btn("Run selected", "play", runSelectedCells, "tool small", { disabled: state.kernel === "busy" || fabric || null, "data-run": true,
      title: fabric ? "Fabric runs the whole notebook. Switch Runs on to This PC to run selected cells." : "Run the selected cells in order (Ctrl+Enter)" }),
    btn("Copy", "copyArrowRight", () => copySelected(), "tool small", { title: "Copy the selected cells (C)" }),
    btn("Merge", "down", mergeSelected, "tool small", { title: "Merge the selected cells into one (Shift+M)" }),
    btn(selectedCells().every((c) => c.meta?.frozen) ? "Unfreeze" : "Freeze", "shield", freezeSelected, "tool small"),
    btn("Delete", "delete", deleteSelected, "tool small", { title: "Delete the selected cells (DD); Z undoes it" }),
    h("span", { class: "spacer" }),
    btn("Clear selection", "dismiss", clearSelection, "tool small", { title: "Esc" }));
}

function clearSelection() {
  if (!nbSel.ids.size) return;
  nbSel.ids.clear();
  nbSel.anchor = null;
  if (state.view === "notebook") renderToolbar();
}

// The range from the anchor to `cell`, which becomes the current cell.
// Ranges cover only the cells on screen: cells in a folded section aren't selected (nbcells.js).
function selectRange(cell) {
  const cells = visibleCells(state.notebook.cells);
  const anchor = cells.find((c) => c.id === nbSel.anchor) || cells.find((c) => c.id === state.selected) || cell;
  nbSel.anchor = anchor.id;
  const [a, b] = [cells.indexOf(anchor), cells.indexOf(cell)].sort((x, y) => x - y);
  nbSel.ids = new Set(cells.slice(a, b + 1).map((c) => c.id));
  moveCurrent(cell);
}

function toggleInSelection(cell) {
  if (!nbSel.ids.size) nbSel.ids.add(selectedCell().id);
  if (nbSel.ids.has(cell.id) && nbSel.ids.size > 1) nbSel.ids.delete(cell.id);
  else nbSel.ids.add(cell.id);
  nbSel.anchor = cell.id;
  moveCurrent(nbSel.ids.has(cell.id) ? cell : selectedCells()[0]);
}

// Moves the current cell without clearing the selection (selectCell clears it).
function moveCurrent(cell) {
  const keep = nbSel.ids;
  nbSel.ids = new Set();
  selectCell(cell);
  nbSel.ids = keep;
  if (nbSel.ids.size === 1) nbSel.ids.clear();
  selectionPaint();
  renderToolbar();
  const el = document.querySelector(`[data-cell="${cell.id}"]`);
  el?.focus({ preventScroll: true });
  el?.scrollIntoView({ block: "nearest" });
}

// From a cell's mousedown: true when the click changed the selection and nothing else should.
function selectionClick(cell, e) {
  if (e.target.closest(".cm-editor, input, select, textarea, button")) return false;
  if (e.shiftKey) { e.preventDefault(); selectRange(cell); return true; }
  if (e.ctrlKey || e.metaKey) { e.preventDefault(); toggleInSelection(cell); return true; }
  return false;
}

function extendSelection(delta) {
  const cells = visibleCells(state.notebook.cells);
  const i = cells.indexOf(selectedCell());
  const next = i < 0 ? null : cells[i + delta];
  if (!next) return;
  if (!nbSel.anchor) nbSel.anchor = selectedCell().id;
  selectRange(next);
}

function selectAllCells() {
  const cells = visibleCells(state.notebook.cells);
  nbSel.anchor = cells[0].id;
  selectRange(cells[cells.length - 1]);
}

// ---------- acting on the selection ----------

function runSelectedCells() {
  const cells = selectedCells();
  if (nbWhere(state.notebook).fabric) {
    toast("This notebook runs in Fabric, which runs the whole notebook. Switch Runs on to This PC to run selected cells.");
    return;
  }
  return runCells(cells);
}

function deleteSelected() {
  const cells = state.notebook.cells;
  const doomed = selectedCells().filter((c) => !c.meta?.frozen);
  const frozen = selectedCells().length - doomed.length;
  if (!doomed.length) { toast("The selected cells are frozen. Unfreeze them to delete them."); return; }
  // Undo (Z) puts them back together, at the places they came from.
  const group = doomed.map((c) => ({ cell: { type: c.type, source: c.source, meta: structuredClone(c.meta || {}), cell_id: c.cell_id }, index: cells.indexOf(c) }));
  const first = group[0].index;
  for (const c of doomed) { cells.splice(cells.indexOf(c), 1); dropEditor(c); }
  deletedCells.push({ group });
  if (!cells.length) cells.push(cellFrom({ type: "code" }));
  nbSel.ids.clear();
  nbSel.anchor = null;
  state.selected = (cells[first] || cells[first - 1] || cells[0]).id;
  markDirty();
  renderNotebook();
  enterCommandMode();
  toast(`Deleted ${doomed.length} cells. Press Z to undo.${frozen ? ` ${frozen} frozen ${frozen === 1 ? "cell was" : "cells were"} kept.` : ""}`);
}

// Undo for a deleted group: the cells go back in their old order.
function undoDeleteGroup(last) {
  const cells = state.notebook.cells;
  // Undoing a merge also puts the first cell's own text back, but only if it still holds exactly
  // the merged text: after an edit there, the cell is left as it is and only the others come back.
  let kept = false;
  if (last.merge && cells.includes(last.merge.head)) {
    if (last.merge.head.source === last.merge.after) setSource(last.merge.head, last.merge.before);
    else kept = true;
  }
  let restored = null;
  for (const { cell: saved, index } of last.group) {
    const cell = cellFrom(saved);
    if (cell.type === "markdown") cell.editing = false;
    cells.splice(Math.min(index, cells.length), 0, cell);
    restored ||= cell;
  }
  nbSel.ids = new Set(cells.filter((c, i) => last.group.some((g) => g.index === i)).map((c) => c.id));
  nbSel.anchor = restored.id;
  state.selected = restored.id;
  markDirty();
  renderNotebook();
  selectionPaint();
  enterCommandMode(restored);
  if (kept) toast("The merged cell was changed after the merge, so it was left as it is. The other cells are back below it.");
}

function copySelected(cut = false) {
  const cells = selectedCells();
  cellClipboard = cells.map((c) => ({ type: c.type, source: c.source, meta: structuredClone(c.meta || {}) }));
  if (cut) deleteSelected();
  else toast(`${cells.length} cells copied`);
}

function moveSelected(delta) {
  const cells = state.notebook.cells;
  const block = selectedCells();
  const first = cells.indexOf(block[0]);
  const last = cells.indexOf(block[block.length - 1]);
  if (last - first + 1 !== block.length) { toast("Select cells next to each other to move them together."); return; }
  if (first + delta < 0 || last + delta >= cells.length) return;
  cells.splice(first, block.length);
  cells.splice(first + delta, 0, ...block);
  markDirty();
  renderNotebook();
  selectionPaint();
  refreshOutline();
}

function freezeSelected() {
  const cells = selectedCells();
  const on = !cells.every((c) => c.meta?.frozen);
  cells.forEach((c) => {
    c.meta = c.meta || {};
    if (on) c.meta.frozen = true;
    else delete c.meta.frozen;
    editors.get(c.id)?.dispatch({ effects: readOnlyComp.reconfigure(readOnly(c)) });
  });
  markDirty();
  renderNotebook();
  selectionPaint();
}

// Shift+M: the selected cells become one, in order. With one cell selected it merges the cell
// below, as in Jupyter. Cells in different languages (Python and %%sql, say) aren't merged, since
// the second cell's magic line would end up in the middle of the first. Z undoes a merge.
function mergeSelected() {
  let cells = selectedCells();
  if (cells.length < 2) {
    const all = state.notebook.cells;
    const next = all[all.indexOf(cells[0]) + 1];
    if (!next) return;
    cells = [cells[0], next];
  }
  if (cells.some((c) => c.meta?.frozen)) { toast("A frozen cell can't be merged. Unfreeze it first."); return; }
  if (new Set(cells.map((c) => c.type)).size > 1) { toast("Select only code cells or only Markdown cells to merge them."); return; }
  const langs = new Set(cells.map(cellLang));
  if (langs.size > 1) {
    const names = Object.fromEntries(LANGS);
    toast(`These cells are in different languages (${[...langs].map((l) => names[l] || l).join(" and ")}), so they weren't merged. Change one cell's language first.`);
    return;
  }
  const [head, ...rest] = cells;
  const all = state.notebook.cells;
  const undo = { merge: { head, before: head.source }, group: rest.map((c) => ({
    cell: { type: c.type, source: c.source, meta: structuredClone(c.meta || {}), cell_id: c.cell_id }, index: all.indexOf(c) })) };
  const magic = MAGIC[cellLang(head)];
  const tails = rest.map((c) => {
    const t = c.source;
    return magic && t.trimStart().startsWith(magic) ? t.trimStart().slice(magic.length).replace(/^\n/, "") : t;
  });
  setSource(head, [head.source.replace(/\n$/, ""), ...tails.map((t) => t.replace(/\n$/, ""))].join("\n"));
  undo.merge.after = head.source;
  for (const c of rest) { all.splice(all.indexOf(c), 1); dropEditor(c); }
  deletedCells.push(undo);
  nbSel.ids.clear();
  nbSel.anchor = null;
  state.selected = head.id;
  markDirty();
  renderNotebook();
  enterCommandMode(head);
  toast(`Merged ${cells.length} cells. Press Z to undo.`);
}

// ---------- running part of a cell ----------

// The code Ctrl+Shift+Enter runs: the selected text, or the cursor's line; a SQL or HTML cell's
// magic goes in front so it runs in the cell's language.
function selectionCode(cell, source, from, to) {
  let text = source.slice(from, to);
  // A selection that starts after a line's indent keeps that indent, so the lines dedent together.
  const lead = source.slice(source.lastIndexOf("\n", from - 1) + 1, from);
  if (from !== to && !lead.trim()) text = lead + text;
  if (from === to) {
    const start = source.lastIndexOf("\n", from - 1) + 1;
    const end = source.indexOf("\n", from);
    text = source.slice(start, end < 0 ? source.length : end);
  }
  const magic = MAGIC[cellLang(cell)];
  if (!text.trim() || (magic && text.trim() === magic)) return null;
  if (!magic) {
    // Lines taken from inside a block keep their indent: dedent them so they run on their own.
    const lines = text.split("\n");
    const indent = Math.min(...lines.filter((l) => l.trim()).map((l) => l.match(/^[ \t]*/)[0].length));
    text = lines.map((l) => l.slice(Math.min(indent, l.match(/^[ \t]*/)[0].length))).join("\n");
  }
  return magic && !text.trimStart().startsWith(magic) ? `${magic}\n${text}` : text;
}

function runSelectionText(cell) {
  const view = editors.get(cell.id);
  if (!view || cell.type !== "code") return false;
  const sel = view.state.selection.main;
  const code = selectionCode(cell, view.state.doc.toString(), sel.from, sel.to);
  if (!code) { toast("Select some code, or put the cursor on a line with code, to run it."); return true; }
  runCell(cell, { code });
  return true;
}

// ---------- keys ----------

// Listed in the Keyboard shortcuts dialog while a notebook is open (handled just below).
{
  const inNotebook = () => state.view === "notebook";
  registerShortcuts("Notebook: several cells", [
    { keys: ["Shift+Click"], label: "Select every cell from the current one to the one you click", when: inNotebook },
    { keys: ["Ctrl+Click"], label: "Add a cell to the selection or take it out", when: inNotebook },
    { keys: ["Shift+Down", "Shift+Up", "Shift+J", "Shift+K"], label: "In command mode: grow the selection down or up", when: inNotebook },
    { keys: ["Ctrl+A"], label: "In command mode: select every cell (cells in folded sections are left out)", when: inNotebook },
    { keys: ["Ctrl+Enter", "Shift+Enter"], label: "With several cells selected: run them in notebook order, stopping at the first failure", when: inNotebook },
    { keys: ["C", "X"], label: "With several cells selected: copy or cut them", when: inNotebook },
    { keys: ["Shift+M"], label: "Merge the selected cells into the first one (with one cell: merge the cell below)", when: inNotebook },
    { keys: ["DD", "Delete"], label: "With several cells selected: delete them, except frozen ones (Z puts them back)", when: inNotebook },
    { keys: ["Alt+Up", "Alt+Down"], label: "With several cells selected: move them up or down", when: inNotebook },
    { keys: ["Ctrl+Shift+Enter"], label: "While editing a code cell: run only the selected text, or the line the cursor is on", when: inNotebook },
  ]);
}

document.addEventListener("keydown", (e) => {
  if (state.view !== "notebook" || !state.notebook || document.getElementById("dialog-root").childElementCount) return;
  const t = e.target;
  const inEditor = !!t.closest?.(".cm-editor");
  if (inEditor) {
    if (e.key === "Enter" && e.shiftKey && (e.ctrlKey || e.metaKey) && !e.altKey) {
      const cell = state.notebook.cells.find((c) => editors.get(c.id)?.contentDOM.contains(t));
      if (cell && runSelectionText(cell)) { e.preventDefault(); e.stopPropagation(); }
    }
    return;
  }
  if (["INPUT", "SELECT", "TEXTAREA"].includes(t.tagName) || t.isContentEditable) return;
  if (!t.closest?.(".cells") && t !== document.body) return;
  const key = e.key;
  const ctrl = e.ctrlKey || e.metaKey;
  let handled = true;
  if (e.shiftKey && !ctrl && !e.altKey && (key === "ArrowUp" || key === "K")) extendSelection(-1);
  else if (e.shiftKey && !ctrl && !e.altKey && (key === "ArrowDown" || key === "J")) extendSelection(1);
  else if (e.shiftKey && !ctrl && key === "M") mergeSelected();
  else if (ctrl && !e.shiftKey && key.toLowerCase() === "a") selectAllCells();
  else if (!multiSelected()) {
    // Plain keys with one cell: notebook.js handles them; a plain move ends a range.
    if ((key === "ArrowUp" || key === "ArrowDown" || key === "j" || key === "k") && !e.shiftKey) nbSel.ids.clear();
    handled = false;
  } else if (key === "Escape") clearSelection();
  else if (key === "Enter" && (ctrl || e.shiftKey)) runSelectedCells();
  else if (key === "d" && !ctrl && !e.shiftKey) {
    if (Date.now() - nbSel.lastD < 600) { nbSel.lastD = 0; deleteSelected(); } else nbSel.lastD = Date.now();
  } else if (key === "Delete") deleteSelected();
  else if (key === "c" && !e.shiftKey) copySelected();
  else if (key === "x" && !e.shiftKey && !ctrl) copySelected(true);
  else if (key === "ArrowUp" && e.altKey) moveSelected(-1);
  else if (key === "ArrowDown" && e.altKey) moveSelected(1);
  else if ((key === "ArrowUp" || key === "ArrowDown" || key === "j" || key === "k") && !e.shiftKey) {
    clearSelection();
    handled = false;  // and notebook.js moves the current cell
  } else if (["a", "b", "m", "y", "o", "v", "z", "Enter"].includes(key)) {
    clearSelection();  // these act on the current cell only
    handled = false;
  } else handled = false;
  if (handled) { e.preventDefault(); e.stopPropagation(); }
}, true);
