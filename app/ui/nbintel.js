// IntelliSense in Python cells, beyond completion: hover a name for its signature and docs (or a
// variable's type and value), signature help while typing a call's arguments, docs for the
// highlighted completion, and as you type a red underline for Python syntax errors and amber ones for
// lint (a name no cell defines, the Best practices rules; nblint.py). It all comes from the local kernel
// and engine (kernel_inspect, notebook_syntax): no AI service, and nothing is run. With Runs on: Fabric there are no live variables, so only the syntax check works.

let intelTip = null;
let intelHoverTimer = 0;
let intelLeaveTimer = 0;
let intelSigTimer = 0;
let intelSigSeq = 0;

function intelLive() {
  return state.notebook && !nbWhere(state.notebook).fabric;
}

function intelFloat(cls) {
  if (!intelTip) {
    // WCAG 1.4.13: the pointer can move onto a hover card (to read or scroll it), and it goes when the pointer leaves.
    intelTip = h("div", { class: "nb-intel", role: "tooltip", hidden: true,
      onmouseenter: () => clearTimeout(intelLeaveTimer),
      onmouseleave: () => intelHide("hover") });
    document.body.append(intelTip);
    // Esc closes a hover card wherever focus is, and only the card: it doesn't also leave the cell.
    document.addEventListener("keydown", (e) => {
      if (e.key !== "Escape" || !intelTip || intelTip.hidden || !intelTip.classList.contains("hover")) return;
      clearTimeout(intelHoverTimer);
      intelHide("hover");
      e.preventDefault();
      e.stopPropagation();
    }, true);
  }
  intelTip.className = `nb-intel ${cls}`;
  return intelTip;
}

// The pointer left the editor: the hover card goes, unless the pointer is on its way onto it.
function intelLeave() {
  clearTimeout(intelHoverTimer);
  clearTimeout(intelLeaveTimer);
  intelLeaveTimer = setTimeout(() => { if (!intelTip?.matches(":hover")) intelHide("hover"); }, 300);
}

function intelHide(kind = null) {
  if (intelTip && (!kind || intelTip.classList.contains(kind))) intelTip.hidden = true;
}

// Places the card below (or, when there's no room, above) a point in the editor.
function intelPlace(tip, coords, above = false) {
  tip.hidden = false;
  tip.style.left = "0px";
  tip.style.top = "0px";
  const box = tip.getBoundingClientRect();
  const left = Math.max(8, Math.min(coords.left, window.innerWidth - box.width - 8));
  const below = coords.bottom + 4;
  const top = above || below + box.height > window.innerHeight - 8 ? coords.top - box.height - 4 : below;
  tip.style.left = `${left}px`;
  tip.style.top = `${Math.max(8, top)}px`;
}

// The hover card's content for kernel_inspect's answer.
function intelCard(info) {
  const kind = { function: "function", class: "class", module: "module", variable: info.type }[info.kind] || info.kind;
  return [
    h("div", { class: "nb-intel-head" },
      h("code", {}, h("b", {}, info.name), info.signature || ""),
      h("span", { class: "nb-intel-kind" }, kind)),
    info.kind === "variable" && info.value ? h("code", { class: "nb-intel-value" }, info.value) : null,
    info.doc ? h("div", { class: "nb-intel-doc" }, info.doc) : null,
  ];
}

// ---------- hover ----------

const intelSkipNodes = new Set(["String", "FormatString", "Comment", "Number"]);

function intelHover() {
  return CM.ViewPlugin.fromClass(class {}, {
    eventHandlers: {
      mousemove(e, view) {
        clearTimeout(intelHoverTimer);
        if (!intelLive() || e.buttons) return;
        const { clientX: x, clientY: y } = e;
        intelHoverTimer = setTimeout(() => intelShowHover(view, x, y), 450);
      },
      mouseleave() { intelLeave(); },
      keydown() { intelHide("hover"); },
    },
  });
}

async function intelShowHover(view, x, y) {
  const pos = view.posAtCoords({ x, y }, false);
  if (pos == null || !view.dom.isConnected) return;
  const near = view.coordsAtPos(pos);
  if (!near || Math.abs(near.left - x) > 16 && Math.abs(near.right - x) > 16) return;  // past the end of the line
  if (intelSkipNodes.has(CM.syntaxTree(view.state).resolveInner(pos, 1).name)) return;
  const line = view.state.doc.lineAt(pos);
  let info = null;
  try {
    info = await rpc("kernel_inspect", { code: line.text, cursor: pos - line.from, notebook: state.notebook?.name });
  } catch {
    return;  // the engine is busy or stopped: no card
  }
  if (!info || !view.dom.matches(":hover")) return;
  const tip = intelFloat("hover");
  tip.replaceChildren(...intelCard(info).filter(Boolean));
  intelPlace(tip, view.coordsAtPos(line.from + info.from) || near);
}

// ---------- signature help ----------

function intelSignature() {
  return CM.ViewPlugin.fromClass(class {
    update(u) {
      if (!u.view.hasFocus) { if (u.focusChanged) intelHide("call"); return; }
      if (!(u.docChanged || u.selectionSet || u.focusChanged)) return;
      clearTimeout(intelSigTimer);
      const view = u.view;
      intelSigTimer = setTimeout(() => intelShowSignature(view), 180);
    }
    destroy() { intelHide("call"); }
  });
}

async function intelShowSignature(view) {
  if (!intelLive() || !view.hasFocus) { intelHide("call"); return; }
  const head = view.state.selection.main.head;
  const code = view.state.sliceDoc(Math.max(0, head - 4000), head);
  if (!code.includes("(")) { intelHide("call"); return; }
  const seq = ++intelSigSeq;
  let help = null;
  try {
    help = await rpc("kernel_inspect", { code, call: true, notebook: state.notebook?.name });
  } catch {
    help = null;
  }
  if (seq !== intelSigSeq || !view.hasFocus) return;
  if (!help) { intelHide("call"); return; }
  const params = help.params.flatMap((p, i) => [i ? ", " : "", i === help.active ? h("b", {}, p) : p]);
  const tip = intelFloat("call");
  tip.replaceChildren(h("code", {}, `${help.name}(`, ...params, ")", help.returns ? ` -> ${help.returns}` : ""),
    help.doc ? h("div", { class: "nb-intel-doc" }, help.doc) : null);
  const at = view.coordsAtPos(head);
  if (at) intelPlace(tip, at, true);
}

// ---------- docs for the highlighted completion ----------

// CodeMirror asks for this when a completion is highlighted: `base` is what came before the dot.
function intelCompletionInfo(base, label) {
  return async () => {
    if (!intelLive()) return null;
    const code = `${base}${label}`;
    let info = null;
    try {
      info = await rpc("kernel_inspect", { code, cursor: code.length, notebook: state.notebook?.name });
    } catch {
      return null;
    }
    if (!info || (!info.doc && !info.signature && !info.value)) return null;
    return h("div", { class: "nb-intel-info" }, ...intelCard(info).filter(Boolean));
  };
}

// ---------- syntax errors and lint warnings ----------

// The notebook's code cells as the Best practices panel sends them (practiceSources, nbpractices.js), and
// which of them this editor is, so the engine reads names from every cell (they share one namespace).
function intelLintCells(view, source) {
  const nb = state.notebook;
  const cell = nb?.cells.find((c) => editors.get(c.id) === view);
  if (!cell || cell.type !== "code") return null;
  const { sources, at } = practiceSources(nb.cells);
  const index = at.indexOf(nb.cells.indexOf(cell));
  if (index < 0) return null;
  sources[index] = source;
  return { nb, cells: sources, cell: index, n: at[index] + 1 };
}

// The governance settings the panel uses (its own copy once it has read them), read once per notebook,
// so a rule a policy turns off or changes isn't underlined differently from the panel.
const intelPracticeSettings = new WeakMap();
async function intelLintSettings(nb) {
  if (nb.practices?.settings) return nb.practices.settings;
  if (!intelPracticeSettings.has(nb)) {
    intelPracticeSettings.set(nb, rpc("governance_practice_settings", { kind: "notebook" })
      .then((out) => out?.settings || {}, () => ({})));
  }
  return intelPracticeSettings.get(nb);
}

// Best-practice findings someone dismissed (learn.js) leave the cell as they leave the panel; the rest stay.
async function intelLintShown(nb, problems, view) {
  const rules = problems.filter((p) => p.source === "Best practices");
  if (!rules.length) return problems;
  const settings = await intelLintSettings(nb);
  const doc = view.state.doc;
  await learnPrepareFindings(rules, (p) => doc.line(Math.min(Math.max(p.line, 1), doc.lines)).text);  // learn.js
  const { shown } = learnSplitFindings(rules, `${nb.place?.root || "notebooks"}/${nb.name}`, (id) => !!settings?.[id]?.enabled);
  return problems.filter((p) => p.source !== "Best practices" || shown.includes(p));
}

// Red for the first syntax error, as before; amber (warning) or blue (info) for lint: a name no cell defines,
// and the panel's best-practice rules. Nothing while Present (nbpresent.js) covers the notebook: its last
// marks stay, so closing it shows them again.
const intelLastLint = new WeakMap();
function intelLinter() {
  return CM.linter(async (view) => {
    if (nbPresent.root || nbPresent.opening) return intelLastLint.get(view) || [];  // nbpresent.js
    const source = view.state.doc.toString();
    if (!source.trim()) return [];
    const ctx = intelLintCells(view, source);
    const practicesOn = !PREVIEW_FEATURES.practices || previewShown();  // features.js: the panel is a preview
    let problems = [];
    try {
      const settings = ctx && practicesOn ? await intelLintSettings(ctx.nb) : null;
      problems = await rpc("notebook_syntax", ctx ? { source, cells: ctx.cells, cell: ctx.cell, settings } : { source });
    } catch {
      return [];
    }
    if (!practicesOn) problems = problems.filter((p) => p.source !== "Best practices");
    if (ctx) problems = await intelLintShown(ctx.nb, problems, view).catch(() => problems);
    if (view.state.doc.toString() !== source) return [];
    const doc = view.state.doc;
    const at = (line, col) => {
      const l = doc.line(Math.min(Math.max(line, 1), doc.lines));
      return Math.min(l.from + Math.max(col, 0), l.to);
    };
    const marks = problems.map((p) => {
      const from = at(p.line, p.col);
      let to = at(p.end_line, p.end_col);
      if (to <= from) to = Math.min(from + 1, doc.length);
      const severity = p.severity === "warning" || p.severity === "info" ? p.severity : "error";
      return { from, to: Math.max(to, from), severity, message: p.message, source: p.source || "Python" };
    });
    intelLastLint.set(view, marks);
    return marks;
  }, { delay: 700 });
}

// What languageFor (notebook.js) adds to a Python cell's editor.
function intelExtensions() {
  return [intelHover(), intelSignature(), intelLinter(), symKeys()];  // symKeys: nbsymbols.js
}

// A scrolled page moves the text the card points at, so the card goes; scrolling the card's own doc keeps it.
document.addEventListener("scroll", (e) => { if (!intelTip?.contains(e.target)) intelHide(); }, true);
