// Notebook find and replace: one bar over the cells that searches every cell, as Fabric's and
// Databricks' notebook find does. Match case, whole word and regular expressions; the matches are
// marked in every cell's editor; Replace and Replace all change cells the way typing would (each
// editor keeps its own undo), skip frozen cells, and Replace all can be undone from the bar.
// The one-cell search (CodeMirror's own panel) stays on the cell's "..." menu.

const nbFind = { open: false, replace: false, q: "", with: "", matchCase: false, word: false, regex: false,
  markdown: true, current: -1, matches: [], error: "", undo: null, bar: null };

const findRefresh = CM.StateEffect.define();

// Every match in the cells, in notebook order: [{ cell, from, to }]. The query's flags are the bar's.
// Returns { matches, error } and never throws on a bad regular expression.
function findMatches(cells, q, opts = {}) {
  if (!q) return { matches: [], error: "" };
  let re;
  try {
    const body = opts.regex ? q : q.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    re = new RegExp(opts.word ? `\\b(?:${body})\\b` : body, opts.matchCase ? "gu" : "giu");
  } catch (err) {
    return { matches: [], error: "That isn't a valid regular expression." };
  }
  const matches = [];
  for (const cell of cells) {
    if (cell.type === "markdown" && opts.markdown === false) continue;
    re.lastIndex = 0;
    for (const m of cell.source.matchAll(re)) {
      if (!m[0].length) continue;  // an empty match (a^, x*) would loop on every position
      matches.push({ cell, from: m.index, to: m.index + m[0].length, text: m[0] });
      if (matches.length >= 5000) return { matches, error: "", capped: true };
    }
  }
  return { matches, error: "" };
}

// The replacement for one match: with a regular expression, $1 and $<name> refer to its groups.
function findReplacement(match, q, withText, opts = {}) {
  if (!opts.regex) return withText;
  const re = new RegExp(opts.word ? `\\b(?:${q})\\b` : q, opts.matchCase ? "u" : "iu");
  return match.text.replace(re, withText);
}

// Applies replacements to a cell's text from the end, so earlier positions stay right.
function findApply(source, edits) {
  let out = source;
  for (const e of [...edits].sort((a, b) => b.from - a.from)) out = out.slice(0, e.from) + e.insert + out.slice(e.to);
  return out;
}

function findOpts() {
  return { matchCase: nbFind.matchCase, word: nbFind.word, regex: nbFind.regex, markdown: nbFind.markdown };
}

function findRecompute(keepCell = true) {
  const before = nbFind.matches[nbFind.current];
  const out = findMatches(state.notebook?.cells || [], nbFind.q, findOpts());
  nbFind.matches = out.matches;
  nbFind.capped = !!out.capped;
  nbFind.error = out.error;
  if (!nbFind.matches.length) nbFind.current = -1;
  else if (keepCell && before) {
    const i = nbFind.matches.findIndex((m) => m.cell === before.cell && m.from >= before.from);
    nbFind.current = i >= 0 ? i : 0;
  } else if (nbFind.current < 0 || nbFind.current >= nbFind.matches.length) nbFind.current = 0;
  editors.forEach((v) => v.dispatch({ effects: findRefresh.of(null) }));
  findCount();
}

// Marks the bar's matches in one cell's editor; the current match gets its own color.
function findHighlighter(cell) {
  const mark = CM.Decoration.mark({ class: "nb-find-match" });
  const current = CM.Decoration.mark({ class: "nb-find-match current" });
  const build = () => {
    const b = new CM.RangeSetBuilder();
    if (nbFind.open && nbFind.q) {
      const cur = nbFind.matches[nbFind.current];
      for (const m of nbFind.matches) {
        if (m.cell === cell && m.to <= cell.source.length) b.add(m.from, m.to, m === cur ? current : mark);
      }
    }
    return b.finish();
  };
  return CM.ViewPlugin.fromClass(class {
    constructor() { this.decorations = build(); }
    update(u) {
      if (u.docChanged || u.transactions.some((t) => t.effects.some((e) => e.is(findRefresh)))) this.decorations = build();
    }
  }, { decorations: (v) => v.decorations });
}

// Called when a cell's text changes by typing: the matches move with it.
function findCellChanged() {
  if (!nbFind.open || !nbFind.q) return;
  clearTimeout(findCellChanged.t);
  findCellChanged.t = setTimeout(() => findRecompute(), 120);
}

function findCount() {
  const el = nbFind.bar?.querySelector(".nb-find-count");
  if (!el) return;
  el.classList.toggle("bad", !!nbFind.error || (!!nbFind.q && !nbFind.matches.length));
  el.textContent = nbFind.error || (!nbFind.q ? "" : !nbFind.matches.length ? "No results"
    : `${nbFind.current + 1} of ${nbFind.matches.length}${nbFind.capped ? "+" : ""}`);
  nbFind.bar.querySelectorAll("[data-needs-match]").forEach((b) => { b.disabled = !nbFind.matches.length; });
  const undo = nbFind.bar.querySelector(".nb-find-undo");
  if (undo) undo.hidden = !nbFind.undo;
}

function openFind(replace = false) {
  if (!state.notebook) return;
  nbFind.open = true;
  nbFind.nb = state.notebook;
  if (replace) nbFind.replace = true;
  // The selected text in the editor becomes the query, as in Fabric.
  const view = editors.get(state.selected);
  if (view?.hasFocus) {
    const sel = view.state.selection.main;
    const text = view.state.sliceDoc(sel.from, sel.to);
    if (text && !text.includes("\n")) nbFind.q = text;
  }
  const scroller = document.querySelector(".cells");
  const top = scroller?.scrollTop;
  renderNotebook();
  if (top != null) document.querySelector(".cells").scrollTop = top;
  findRecompute(false);
  const input = nbFind.bar.querySelector(".nb-find-q");
  input.focus();
  input.select();
}

function closeFind() {
  nbFind.open = false;
  nbFind.undo = null;
  editors.forEach((v) => v.dispatch({ effects: findRefresh.of(null) }));
  nbFind.bar?.remove();
  const cell = nbFind.matches[nbFind.current]?.cell || selectedCell();
  if (cell) enterCommandMode(cell);
}

// Shows a match: selects its cell, opens hidden code or a rendered Markdown cell, and selects the
// text in the cell's editor without taking focus from the bar.
function findShow(i) {
  const m = nbFind.matches[i];
  if (!m) return;
  nbFind.current = i;
  const cell = m.cell;
  selectCell(cell);
  if (cell.meta?.source_hidden) setMeta(cell, "source_hidden", false);
  if (cell.type === "markdown" && !cell.editing && !cell.meta?.frozen) { cell.editing = true; updateCell(cell); }
  const view = editors.get(cell.id);
  editors.forEach((v) => v.dispatch({ effects: findRefresh.of(null) }));
  if (view && m.to <= view.state.doc.length) {
    view.dispatch({ selection: { anchor: m.from, head: m.to }, effects: CM.EditorView.scrollIntoView(m.from, { y: "center" }) });
  } else {
    document.querySelector(`[data-cell="${cell.id}"]`)?.scrollIntoView({ block: "center" });
  }
  findCount();
}

function findStep(delta) {
  if (!nbFind.matches.length) return;
  const n = nbFind.matches.length;
  findShow(((nbFind.current < 0 ? (delta > 0 ? -1 : 0) : nbFind.current) + delta + n) % n);
}

// Changes a cell's text through its editor when it has one, so Ctrl+Z in the cell undoes it.
function findWrite(cell, edits) {
  const view = editors.get(cell.id);
  if (view && view.state.doc.toString() === cell.source) {
    view.dispatch({ changes: edits.map((e) => ({ from: e.from, to: e.to, insert: e.insert })) });
  } else {
    cell.source = findApply(cell.source, edits);
  }
}

function replaceOne() {
  const m = nbFind.matches[nbFind.current];
  if (!m) return;
  if (m.cell.meta?.frozen) { toast("That match is in a frozen cell, so it wasn't changed. Unfreeze the cell to change it."); findStep(1); return; }
  findWrite(m.cell, [{ from: m.from, to: m.to, insert: findReplacement(m, nbFind.q, nbFind.with, findOpts()) }]);
  nbFind.undo = null;
  markDirty();
  findAfterEdit(m.cell);
  findRecompute();
  if (nbFind.matches.length) findShow(Math.min(nbFind.current, nbFind.matches.length - 1));
}

function replaceAll() {
  const byCell = new Map();
  let frozen = 0;
  for (const m of nbFind.matches) {
    if (m.cell.meta?.frozen) { frozen += 1; continue; }
    if (!byCell.has(m.cell)) byCell.set(m.cell, []);
    byCell.get(m.cell).push({ from: m.from, to: m.to, insert: findReplacement(m, nbFind.q, nbFind.with, findOpts()) });
  }
  if (!byCell.size) {
    if (frozen) toast(`All ${frozen} match${frozen === 1 ? " is" : "es are"} in frozen cells, so nothing was changed.`);
    return;
  }
  nbFind.undo = [...byCell.keys()].map((cell) => ({ cell, source: cell.source }));
  let count = 0;
  byCell.forEach((edits, cell) => { findWrite(cell, edits); count += edits.length; findAfterEdit(cell); });
  markDirty();
  findRecompute(false);
  toast(`Replaced ${count} match${count === 1 ? "" : "es"} in ${byCell.size} cell${byCell.size === 1 ? "" : "s"}` +
    (frozen ? `. ${frozen} in frozen cells ${frozen === 1 ? "was" : "were"} left as they are.` : "."));
}

function undoReplaceAll() {
  const undo = nbFind.undo;
  if (!undo) return;
  nbFind.undo = null;
  for (const { cell, source } of undo) {
    if (!state.notebook.cells.includes(cell)) continue;
    setSource(cell, source);
    findAfterEdit(cell);
  }
  markDirty();
  findRecompute(false);
  toast("Replace all was undone.");
}

// What typing in the cell would have refreshed: a rendered Markdown cell, the outline, dependencies.
function findAfterEdit(cell) {
  if (cell.type === "markdown") {
    if (!cell.editing) updateCell(cell);
    refreshOutline();
  } else {
    depsChanged();
  }
}

// The bar is one element kept between redraws of the notebook, so typing in it isn't interrupted.
function nbFindBar() {
  if (!nbFind.open) return null;
  if (nbFind.nb !== state.notebook) {  // another notebook was opened: its cells, not the last one's
    nbFind.nb = state.notebook;
    nbFind.undo = null;
    queueMicrotask(() => findRecompute(false));
  }
  if (!nbFind.bar) {
    const toggle = (key, label, text) => h("button", { class: `tool small nb-find-opt${nbFind[key] ? " toggled" : ""}`, title: label,
      "aria-label": label, "aria-pressed": String(nbFind[key]), onclick: (e) => {
        nbFind[key] = !nbFind[key];
        e.currentTarget.classList.toggle("toggled", nbFind[key]);
        e.currentTarget.setAttribute("aria-pressed", String(nbFind[key]));
        findRecompute(false);
      } }, text);
    const q = h("input", { type: "text", class: "nb-find-q", placeholder: "Find in notebook", "aria-label": "Find in notebook",
      value: nbFind.q, spellcheck: "false" });
    q.addEventListener("input", () => { nbFind.q = q.value; nbFind.undo = null; findRecompute(false); if (nbFind.matches.length) findShow(0); });
    const w = h("input", { type: "text", class: "nb-find-with", placeholder: "Replace with", "aria-label": "Replace with",
      value: nbFind.with, spellcheck: "false" });
    w.addEventListener("input", () => { nbFind.with = w.value; });
    const expand = h("button", { class: "tool small", title: "Show replace", "aria-label": "Show replace",
      "aria-expanded": String(nbFind.replace), onclick: () => {
        nbFind.replace = !nbFind.replace;
        bar.classList.toggle("with-replace", nbFind.replace);
        expand.setAttribute("aria-expanded", String(nbFind.replace));
        if (nbFind.replace) w.focus();
      } }, icon("chevronRight", 14));
    const bar = h("div", { class: `nb-find${nbFind.replace ? " with-replace" : ""}`, role: "search", "aria-label": "Find and replace in notebook",
      onkeydown: (e) => {
        if (e.key === "Escape") { e.preventDefault(); closeFind(); }
        else if (e.key === "Enter" && e.target === q) { e.preventDefault(); findStep(e.shiftKey ? -1 : 1); }
        else if (e.key === "Enter" && e.target === w) { e.preventDefault(); if (e.ctrlKey || e.metaKey) replaceAll(); else replaceOne(); }
        else if ((e.key === "h" || e.key === "H") && (e.ctrlKey || e.metaKey)) { e.preventDefault(); expand.click(); }
      } },
    expand,
    h("div", { class: "nb-find-rows" },
      h("div", { class: "nb-find-row" }, q,
        toggle("matchCase", "Match case", "Aa"), toggle("word", "Match whole word", "ab"), toggle("regex", "Use regular expression", ".*"),
        h("span", { class: "nb-find-count", "aria-live": "polite" }),
        h("button", { class: "tool small", title: "Previous match (Shift+Enter)", "aria-label": "Previous match", "data-needs-match": true, onclick: () => findStep(-1) }, icon("up", 14)),
        h("button", { class: "tool small", title: "Next match (Enter)", "aria-label": "Next match", "data-needs-match": true, onclick: () => findStep(1) }, icon("down", 14)),
        h("label", { class: "nb-find-md", title: "Search Markdown cells too" },
          h("input", { type: "checkbox", checked: nbFind.markdown || null, onchange: (e) => { nbFind.markdown = e.target.checked; findRecompute(false); } }),
          "Markdown"),
        h("button", { class: "tool small", title: "Close (Esc)", "aria-label": "Close find", onclick: closeFind }, icon("dismiss", 14))),
      h("div", { class: "nb-find-row nb-find-replace" }, w,
        btn("Replace", null, replaceOne, "tool outline small", { "data-needs-match": true, title: "Replace this match (Enter)" }),
        btn("Replace all", null, replaceAll, "tool outline small", { "data-needs-match": true, title: "Replace every match (Ctrl+Enter)" }),
        btn("Undo replace all", "undo", undoReplaceAll, "tool small nb-find-undo", { hidden: true }))));
    nbFind.bar = bar;
  }
  if (nbFind.bar.contains(document.activeElement)) {
    const el = document.activeElement;
    queueMicrotask(() => el.focus({ preventScroll: true }));
  }
  queueMicrotask(findCount);
  return nbFind.bar;
}

// Ctrl+F in command mode and Ctrl+H anywhere in the notebook open the bar; Ctrl+F while editing a
// cell keeps the cell's own search, as in Fabric.
document.addEventListener("keydown", (e) => {
  if (state.view !== "notebook" || !state.notebook || document.getElementById("dialog-root").childElementCount) return;
  if (!(e.ctrlKey || e.metaKey) || e.altKey) return;
  const key = e.key.toLowerCase();
  const inEditor = !!e.target.closest?.(".cm-editor");
  if (e.target.closest?.(".nb-find")) return;
  if (key === "h" || (key === "f" && e.shiftKey) || (key === "f" && !inEditor && !["INPUT", "SELECT", "TEXTAREA"].includes(e.target.tagName))) {
    e.preventDefault();
    e.stopPropagation();
    openFind(key === "h");
  }
}, true);
