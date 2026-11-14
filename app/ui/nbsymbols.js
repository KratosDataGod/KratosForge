// Go to definition (F12), find references (Shift+F12) and rename (F2) for a notebook's Python names,
// across every cell, as an IDE does (editor parity N10). The engine reads the cells with Python's own
// parser (notebook_symbol, nbsymbols.py) and runs nothing, so it works with Runs on: Fabric too. Scopes
// are followed: a function's own argument or local of the same name isn't the notebook's name.
// Attributes, keyword argument names, strings and comments are never changed by a rename.

let symPop = null;

const SYM_KEYWORDS = new Set(["False", "None", "True", "and", "as", "assert", "async", "await", "break", "class",
  "continue", "def", "del", "elif", "else", "except", "finally", "for", "from", "global", "if", "import", "in", "is",
  "lambda", "nonlocal", "not", "or", "pass", "raise", "return", "try", "while", "with", "yield"]);

// Why `name` can't be a Python name, or "" (nbsymbols.rename_check says the same in the engine).
function symNameProblem(name) {
  if (!/^[\p{L}_][\p{L}\p{N}_]*$/u.test(name)) return "That isn't a Python name: use letters, digits and underscores, not starting with a digit.";
  if (SYM_KEYWORDS.has(name)) return `${name} is a Python keyword.`;
  return "";
}

// The notebook's cells as the engine reads them: each editor's own text when it has one.
function symCells() {
  return state.notebook.cells.map((c) => ({ type: c.type, source: editors.get(c.id)?.state.doc.toString() ?? c.source }));
}

function symCellOf(view) {
  for (const [id, v] of editors) if (v === view) return state.notebook.cells.find((c) => c.id === id);
  return null;
}

async function symLookup(view, name = null) {
  const cell = symCellOf(view);
  if (!cell) return null;
  const index = state.notebook.cells.indexOf(cell);
  try {
    return await rpc("notebook_symbol", { cells: symCells(), cell: index, offset: view.state.selection.main.head, name });
  } catch (e) {
    toast(friendlyError(e).text, true);
    return null;
  }
}

function symHide() {
  if (symPop) symPop.hidden = true;
}

function symFloat(label) {
  if (!symPop) {
    symPop = h("div", { class: "nb-intel nb-sym", role: "dialog", hidden: true });
    symPop.addEventListener("keydown", (e) => { if (e.key === "Escape") { e.stopPropagation(); symClose(); } });
    document.body.append(symPop);
    document.addEventListener("mousedown", (e) => { if (symPop && !symPop.hidden && !symPop.contains(e.target)) symHide(); }, true);
  }
  symPop.setAttribute("aria-label", label);
  symPop.replaceChildren();
  return symPop;
}

// Closes the box and goes back to the editor it came from.
let symFrom = null;
function symClose() {
  symHide();
  symFrom?.focus();
}

// Selects the cell, opens what hides it, and selects the name in its editor.
function symGo(place) {
  const cell = state.notebook.cells[place.cell];
  if (!cell) return;
  symHide();
  revealCell(cell);  // nbcells.js: open a folded section
  selectCell(cell);
  if (cell.meta?.source_hidden) setMeta(cell, "source_hidden", false);
  const view = editors.get(cell.id);
  if (!view || place.to > view.state.doc.length) {
    document.querySelector(`[data-cell="${cell.id}"]`)?.scrollIntoView({ block: "center" });
    return;
  }
  view.focus();
  view.dispatch({ selection: { anchor: place.from, head: place.to }, effects: CM.EditorView.scrollIntoView(place.from, { y: "center" }) });
}

const symCellLabel = (i) => `Cell ${i + 1}`;

function symSkippedText(skipped) {
  if (!skipped?.length) return "";
  const list = skipped.map((s) => s.cell + 1).join(", ");
  return ` Cell${skipped.length === 1 ? "" : "s"} ${list} ${skipped.length === 1 ? "has" : "have"} Python the editor can't read, so ${skipped.length === 1 ? "it was" : "they were"} left out.`;
}

// F12: the definition in effect at the cursor, as the cells run top to bottom: the last one before it,
// else the first one after it.
async function symDefinition(view) {
  const found = await symLookup(view);
  if (!found) return;
  if (!found.name) { toast("Put the cursor on a name to go to its definition."); return; }
  const defs = found.places.filter((p) => p.kind === "definition");
  if (!defs.length) {
    toast(`${found.name} isn't defined in this notebook's Python cells: it's built in, or comes from the session.` + symSkippedText(found.skipped));
    return;
  }
  const index = state.notebook.cells.indexOf(symCellOf(view));
  const head = view.state.selection.main.head;
  const before = defs.filter((p) => p.cell < index || (p.cell === index && p.from <= head));
  const here = before.find((p) => p.cell === index && p.from <= head && head <= p.to);
  symGo(here || before.at(-1) || defs[0]);
}

// Shift+F12: every place the name is defined or used, in a list to go to.
async function symReferences(view) {
  const found = await symLookup(view);
  if (!found) return;
  if (!found.name) { toast("Put the cursor on a name to find where it's used."); return; }
  symFrom = view;
  const cells = new Set(found.places.map((p) => p.cell));
  const pop = symFloat(`Where ${found.name} is used`);
  const head = h("div", { class: "nb-intel-head" }, h("code", {}, h("b", {}, found.name)),
    h("span", { class: "nb-intel-kind" }, `${found.places.length} place${found.places.length === 1 ? "" : "s"} in ${cells.size} cell${cells.size === 1 ? "" : "s"}`));
  const list = h("div", { class: "nb-sym-list", role: "list" }, ...found.places.map((p) => h("button", {
    class: "nb-sym-item", role: "listitem", onclick: () => symGo(p),
  }, h("span", { class: "nb-sym-where" }, `${symCellLabel(p.cell)}, line ${p.line}`),
  h("code", {}, p.text), p.kind === "definition" ? h("span", { class: "nb-intel-kind" }, "defined") : null)));
  const note = symSkippedText(found.skipped);
  pop.append(head, list);
  if (note) pop.append(h("div", { class: "nb-sym-note" }, note.trim()));
  intelPlace(pop, view.coordsAtPos(view.state.selection.main.head) || view.dom.getBoundingClientRect());
  pop.querySelector(".nb-sym-item")?.focus();
}

// F2: a box for the new name; Enter renames every place in every Python cell the editor can read.
async function symRename(view) {
  const found = await symLookup(view);
  if (!found) return;
  if (!found.name) { toast("Put the cursor on a name to rename it."); return; }
  if (!found.places.some((p) => p.kind === "definition")) {
    toast(`${found.name} isn't defined in this notebook's Python cells, so it isn't renamed here.`);
    return;
  }
  symFrom = view;
  const pop = symFloat(`Rename ${found.name}`);
  const input = h("input", { type: "text", class: "nb-sym-input", value: found.name, "aria-label": `New name for ${found.name}`, spellcheck: "false" });
  const why = h("div", { class: "nb-sym-note", role: "status" }, `Renames ${found.places.length} place${found.places.length === 1 ? "" : "s"}. Enter to rename, Esc to cancel.`);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); symApplyRename(view, found.name, input.value.trim(), why); }
  });
  pop.append(h("label", { class: "nb-sym-label" }, `Rename ${found.name} to`), input, why);
  intelPlace(pop, view.coordsAtPos(view.state.selection.main.head) || view.dom.getBoundingClientRect());
  input.focus();
  input.select();
}

async function symApplyRename(view, old, name, why) {
  if (name === old) { symClose(); return; }
  const problem = symNameProblem(name);
  if (problem) { why.textContent = problem; return; }
  const taken = await symLookup(view, name);
  if (!taken) return;
  const used = Math.max(taken.places.length, taken.anywhere || 0);
  if (used) {
    // Anywhere, a function's own argument or a loop variable included: there it would take over the renamed name.
    why.textContent = `${name} is already used in this notebook (${used} place${used === 1 ? "" : "s"}), so the two would become one. Pick another name.`;
    return;
  }
  const found = await symLookup(view, old);  // again: the cells may have changed while the box was open
  if (!found) return;
  const byCell = new Map();
  let frozen = 0;
  for (const p of found.places) {
    const cell = state.notebook.cells[p.cell];
    if (!cell) continue;
    if (cell.meta?.frozen) { frozen += 1; continue; }
    if (!byCell.has(cell)) byCell.set(cell, []);
    byCell.get(cell).push({ from: p.from, to: p.to, insert: name });
  }
  let count = 0;
  byCell.forEach((edits, cell) => { findWrite(cell, edits); count += edits.length; findAfterEdit(cell); });
  if (count) markDirty();
  symClose();
  toast(`Renamed ${old} to ${name} in ${count} place${count === 1 ? "" : "s"} across ${byCell.size} cell${byCell.size === 1 ? "" : "s"}.` +
    (frozen ? ` ${frozen} in frozen cells ${frozen === 1 ? "was" : "were"} left as ${frozen === 1 ? "it is" : "they are"}.` : "") +
    symSkippedText(found.skipped));
}

// What intelExtensions (nbintel.js) adds to a Python cell's editor.
function symKeys() {
  const run = (f) => (view) => { f(view); return true; };
  return CM.Prec.high(CM.keymap.of([
    { key: "F12", run: run(symDefinition) },
    { key: "Shift-F12", run: run(symReferences) },
    { key: "F2", run: run(symRename) },
  ]));
}

{
  const inNotebook = () => state.view === "notebook";
  registerShortcuts("Notebook: names in Python cells", [
    { keys: ["F12"], label: "Go to where the name under the cursor is defined, in any cell", when: inNotebook },
    { keys: ["Shift+F12"], label: "List every place the name is used, across the cells", when: inNotebook },
    { keys: ["F2"], label: "Rename the name everywhere it's used (not in strings, comments or attributes)", when: inNotebook },
  ]);
}
