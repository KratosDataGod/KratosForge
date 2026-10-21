// IntelliSense in Python cells, beyond completion: hover a name for its signature and docs (or a
// variable's type and value), signature help while typing a call's arguments, docs for the
// highlighted completion, and a red underline for Python syntax errors as you type.
// It all comes from the local kernel and engine (kernel_inspect, notebook_syntax): no AI service,
// and nothing is run. With Runs on: Fabric there are no live variables, so only the syntax check works.

let intelTip = null;
let intelHoverTimer = 0;
let intelSigTimer = 0;
let intelSigSeq = 0;

function intelLive() {
  return state.notebook && !nbWhere(state.notebook).fabric;
}

function intelFloat(cls) {
  if (!intelTip) {
    intelTip = h("div", { class: "nb-intel", role: "tooltip", hidden: true });
    document.body.append(intelTip);
  }
  intelTip.className = `nb-intel ${cls}`;
  return intelTip;
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
      mouseleave() {
        clearTimeout(intelHoverTimer);
        intelHide("hover");
      },
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
    info = await rpc("kernel_inspect", { code: line.text, cursor: pos - line.from });
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
    help = await rpc("kernel_inspect", { code, call: true });
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
      info = await rpc("kernel_inspect", { code, cursor: code.length });
    } catch {
      return null;
    }
    if (!info || (!info.doc && !info.signature && !info.value)) return null;
    return h("div", { class: "nb-intel-info" }, ...intelCard(info).filter(Boolean));
  };
}

// ---------- syntax errors ----------

function intelLinter() {
  return CM.linter(async (view) => {
    const source = view.state.doc.toString();
    if (!source.trim()) return [];
    let problems = [];
    try {
      problems = await rpc("notebook_syntax", { source });
    } catch {
      return [];
    }
    if (view.state.doc.toString() !== source) return [];
    const doc = view.state.doc;
    const at = (line, col) => {
      const l = doc.line(Math.min(Math.max(line, 1), doc.lines));
      return Math.min(l.from + Math.max(col, 0), l.to);
    };
    return problems.map((p) => {
      const from = at(p.line, p.col);
      let to = at(p.end_line, p.end_col);
      if (to <= from) to = Math.min(from + 1, doc.length);
      return { from, to: Math.max(to, from), severity: "error", message: p.message, source: "Python" };
    });
  }, { delay: 700 });
}

// What languageFor (notebook.js) adds to a Python cell's editor.
function intelExtensions() {
  return [intelHover(), intelSignature(), intelLinter()];
}

document.addEventListener("scroll", () => { intelHide(); }, true);
