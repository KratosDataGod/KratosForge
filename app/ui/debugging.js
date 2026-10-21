// Variable history, watches and hidden state (Prove stage 5), in the notebook's Variables pane.
// After each cell that runs on this PC: what it added, changed and removed (names, types and
// sizes, never values; kept for this kernel only), the watch expressions' new values (the engine
// evaluates them after the cell, notebook.Kernel._watch), and a warning on the cell when it uses a
// name that no cell in the notebook defines but the kernel still has: left over from a deleted or
// changed cell, so the notebook would fail in Fabric, in a proof or after a restart.

// ---------- pure helpers (app/tests/ui.test.cjs runs these) ----------

const DBG_WATCH_SECONDS = 2;   // a watch slower than this is paused
const DBG_HISTORY = 50;        // runs kept in History
const DBG_TRAIL = 4;           // earlier values shown under a watch

// What a cell did to the kernel's variables. `before` and `after` are kernel_variables rows
// ({name, type, size, value}); the value only decides whether a variable changed, it isn't kept.
function dbgDiff(before, after) {
  const was = new Map((before || []).map((r) => [r.name, r]));
  const now = new Map((after || []).map((r) => [r.name, r]));
  const added = [], changed = [], removed = [];
  for (const r of after || []) {
    const b = was.get(r.name);
    if (!b) added.push({ name: r.name, type: r.type, size: r.size });
    else if (b.type !== r.type || b.size !== r.size || b.value !== r.value) {
      changed.push({ name: r.name, type: r.type, size: r.size,
        was: b.type !== r.type ? b.type : b.size !== r.size ? b.size : "" });
    }
  }
  for (const r of before || []) if (!now.has(r.name)) removed.push({ name: r.name });
  return { added, changed, removed };
}

// Names a cell uses that no cell defines (or only a later cell does) but the kernel has.
// `g` is the cell's entry in notebook_dependencies; `names` the kernel's variable names; `setBy`
// maps a name to the cell that last set it in this kernel ({cell_id, source}); `cells` are the
// notebook's cells now ({cell_id, source}). A source is offered back only when that cell is gone
// or no longer has that code.
function dbgHidden(g, names, setBy, cells, ignore) {
  const have = new Set(names);
  const skip = new Set(ignore || []);
  const byId = new Map((cells || []).map((c) => [c.cell_id, c]));
  const out = [];
  for (const u of g?.unresolved || []) {
    if (!have.has(u.name) || skip.has(u.name)) continue;
    const later = /defined only in a later cell/.test(u.text);
    const from = setBy?.[u.name];
    const gone = from && (!byId.has(from.cell_id) || byId.get(from.cell_id).source !== from.source);
    out.push({
      name: u.name,
      text: later ? `${u.name} comes from a cell further down, which ran before this one`
        : `${u.name} isn't defined by any cell in this notebook`,
      source: gone ? from.source : null,
    });
  }
  return out;
}

// Records a watch's new value and keeps its last few, oldest first. A value that differs from the
// one before is marked changed.
function dbgTrail(trail, n, w) {
  const shown = w.error ? `error: ${w.error}` : w.value;
  const last = trail.length ? trail[trail.length - 1] : null;
  return [...trail, { n, value: shown, changed: !!last && last.value !== shown }].slice(-(DBG_TRAIL + 1));
}

// Breakpoint lines after an edit: `mapLine(n)` is where line n went, or null when it was deleted.
// Lines past the end are dropped, two breakpoints that land on one line become one.
function dbgBpMap(lines, mapLine, lineCount) {
  const out = new Set();
  for (const n of lines || []) {
    const m = mapLine(n);
    if (m != null && m >= 1 && m <= lineCount) out.add(m);
  }
  return [...out].sort((a, b) => a - b);
}

// The paused call stack, outermost first: "(cell) line 5 › f line 3".
function dbgStackText(stack) {
  return (stack || []).map((f) => `${f.function} line ${f.line}`).join(" › ");
}

// The debugger's keys, as in VS Code: F5 continue, F10 step over, F11 step into, Shift+F11 step out.
function dbgKeyAction(e) {
  if (e.ctrlKey || e.altKey || e.metaKey) return null;
  if (e.key === "F5" && !e.shiftKey) return "continue";
  if (e.key === "F10" && !e.shiftKey) return "next";
  if (e.key === "F11") return e.shiftKey ? "out" : "step";
  return null;
}

// ---------- state ----------

function dbgOn() {
  return previewShown() && !!PREVIEW_FEATURES.debug;
}

function dbgState(nb) {
  if (!nb.debug) {
    nb.debug = { watches: [], ignore: [], paused: new Set(), values: {}, history: [], setBy: {}, vars: null, tab: "now", loaded: null };
    nb.debug.loaded = rpc("notebook_debug_get", { name: nb.name }).then((saved) => {
      nb.debug.watches = saved.watches || [];
      nb.debug.ignore = saved.ignore_hidden || [];
    }).catch(() => {});
  }
  return nb.debug;
}

// The watches to evaluate after the next cell (runCell sends them with start_cell).
async function dbgWatches(nb) {
  if (!dbgOn() || !nb || nbWhere(nb).fabric) return [];
  const d = dbgState(nb);
  await d.loaded;
  if (d.vars === null) {  // what the kernel had before this notebook's first run, so History can say what changed
    try { d.vars = await rpc("kernel_variables", { notebook: nb.name }); } catch { /* History starts at the next run */ }
  }
  return d.watches.filter((w) => !d.paused.has(w));
}

// Called by deps.js when the kernel restarts or its process ends: the history was about it.
function dbgRestarted() {
  const d = state.notebook?.debug;
  if (!d) return;
  d.history = [];
  d.setBy = {};
  d.vars = [];
  for (const k of Object.keys(d.values)) d.values[k] = { trail: [] };
  for (const c of state.notebook.cells) c.hidden = null;
  dbgPaint();
}

// Called by runCell after a cell finished on this PC. False when it did nothing (feature hidden).
async function dbgAfterRun(cell, snap) {
  const nb = state.notebook;
  if (!dbgOn() || !nb || !nb.cells.includes(cell)) return false;
  const d = dbgState(nb);
  const n = nb.cells.indexOf(cell) + 1;
  let vars;
  try {
    vars = await rpc("kernel_variables", { notebook: nb.name });
  } catch {
    return false;
  }
  state.variables = vars;
  if (d.vars !== null) {
    const changes = dbgDiff(d.vars, vars);
    d.history.unshift({ n, cell_id: cell.cell_id, at: cell.finishedAt, seconds: cell.seconds, status: cell.status, ...changes });
    d.history.length = Math.min(d.history.length, DBG_HISTORY);
    for (const r of [...changes.added, ...changes.changed]) d.setBy[r.name] = { cell_id: cell.cell_id, source: cell.source };
  } else {
    for (const r of vars) d.setBy[r.name] ??= { cell_id: cell.cell_id, source: cell.source };
  }
  d.vars = vars;
  for (const w of snap?.watches || []) {
    if (w.skipped) continue;
    const v = d.values[w.expr] || { trail: [] };
    d.values[w.expr] = { ...w, trail: dbgTrail(v.trail, n, w) };
    if (w.seconds > DBG_WATCH_SECONDS) d.paused.add(w.expr);
  }
  const deps = await depsRefresh(nb);
  const g = deps?.byId?.get(cell.cell_id);
  cell.hidden = g ? dbgHidden(g, vars.map((r) => r.name), d.setBy,
    nb.cells.map((c) => ({ cell_id: c.cell_id, source: c.source })), d.ignore) : null;
  if (state.notebook === nb) {
    updateResults(cell);
    dbgPaint();
  }
  return true;
}

async function dbgSave(nb, change) {
  const d = dbgState(nb);
  const saved = await attempt(() => rpc("notebook_debug_set", { name: nb.name, ...change }), "Could not save");
  if (!saved) return false;
  d.watches = saved.watches;
  d.ignore = saved.ignore_hidden;
  return true;
}

// ---------- the notebook screen ----------

// The warning under a cell's output.
function dbgCellWarning(cell) {
  const nb = state.notebook;
  if (!dbgOn() || !cell.hidden?.length || !nb) return null;
  return h("div", { class: "output dbg-hidden" }, cell.hidden.map((x) => h("div", { class: "dbg-hidden-item" },
    h("div", { class: "dbg-hidden-title" }, h("b", {}, x.text), previewTag()),
    h("span", {}, "The kernel still has it from an earlier run, so this cell works now, but it will fail in Fabric, in a proof and after a restart."),
    x.source ? h("span", { class: "muted-text" }, "It was last set by a cell that read: ", h("code", {}, x.source.length > 120 ? `${x.source.slice(0, 117)}...` : x.source)) : null,
    h("span", { class: "dbg-actions" },
      x.source ? btn("Insert that cell back above", "add", () => dbgInsertAbove(cell, x.source), "tool outline small") : null,
      btn("Ignore for this notebook", null, () => dbgIgnore(nb, x.name), "tool outline small")))));
}

function dbgInsertAbove(cell, source) {
  selectCell(cell);
  const added = addCell("code", false, -1);
  setSource(added, source);
  markDirty();
}

async function dbgIgnore(nb, name) {
  const d = dbgState(nb);
  if (!(await dbgSave(nb, { ignore_hidden: [...d.ignore, name] }))) return;
  for (const c of nb.cells) {
    if (!c.hidden) continue;
    c.hidden = c.hidden.filter((x) => x.name !== name);
    updateResults(c);
  }
}

// Watches and the Now / History tabs; replaces the plain variable list when the feature is shown.
function dbgPane(nb) {
  const d = dbgState(nb);
  return h("div", { class: "dbg-pane" }, dbgWatchSection(nb, d),
    h("div", { class: "dbg-tabs", role: "tablist" },
      ["now", "history"].map((t) => h("button", { class: `dbg-tab${d.tab === t ? " on" : ""}`, role: "tab", "aria-selected": String(d.tab === t),
        onclick: () => { d.tab = t; dbgPaint(); } }, t === "now" ? "Now" : "History"))),
    d.tab === "now" ? h("div", { class: "var-list" }, state.variables ? variableTable(state.variables) : h("div", { class: "muted-text" }, "Loading..."))
      : dbgHistory(d));
}

function dbgWatchSection(nb, d) {
  const input = h("input", { class: "dbg-add", placeholder: "Add a watch, for example df.height", "aria-label": "Watch expression", maxlength: 200 });
  const add = async () => {
    const expr = input.value.trim();
    if (!expr) return;
    if (await dbgSave(nb, { watches: [...d.watches, expr] })) {
      input.value = "";
      dbgPaint();
    }
  };
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") add(); });
  return h("div", { class: "dbg-watches" },
    h("div", { class: "dbg-head" }, "Watches ", previewTag()),
    d.watches.map((expr) => dbgWatch(nb, d, expr)),
    h("div", { class: "dbg-addrow" }, input, btn("Add", null, add, "tool outline small")),
    h("div", { class: "muted-text dbg-note" }, "A watch runs as notebook code after every cell, so keep it to reads. Expressions are saved with the notebook; values aren't."));
}

function dbgWatch(nb, d, expr) {
  const v = d.values[expr];
  const paused = d.paused.has(expr);
  const drop = async () => {
    if (await dbgSave(nb, { watches: d.watches.filter((w) => w !== expr) })) {
      delete d.values[expr];
      d.paused.delete(expr);
      dbgPaint();
    }
  };
  const remove = btn("", "dismiss", drop, "tool small", { "aria-label": `Remove watch ${expr}`, title: "Remove this watch" });
  const value = paused ? h("span", { class: "muted-text" }, `Paused: took longer than ${DBG_WATCH_SECONDS} seconds. `,
    h("button", { class: "tool link small", onclick: () => { d.paused.delete(expr); dbgPaint(); } }, "Resume"))
    : !v?.trail?.length ? h("span", { class: "muted-text" }, "Shown after the next cell runs.")
      : v.error ? h("span", { class: "warn" }, v.error) : h("span", { class: "dbg-value" }, v.value);
  const earlier = (v?.trail || []).slice(0, -1);
  return h("div", { class: `dbg-watch${paused ? " paused" : ""}` },
    h("div", { class: "dbg-watch-head" }, h("code", {}, expr), remove), value,
    earlier.length || v?.trail?.[v.trail.length - 1]?.changed ? h("div", { class: "dbg-trail" },
      (v.trail || []).map((t, i) => h("span", { class: t.changed ? "changed" : null },
        `after cell ${t.n}: ${t.value}${t.changed && i === v.trail.length - 1 ? " (changed)" : ""}`))) : null);
}

function dbgHistory(d) {
  if (!d.history.length) return h("div", { class: "muted-text dbg-empty" }, "Run a cell to see what it adds, changes and removes.");
  const row = (cls, sign, r) => h("div", { class: cls, title: cls === "del" ? null : "Click to display it in a new cell",
    onclick: cls === "del" ? null : () => insertCell(`display(${r.name})`) },
  `${sign} ${r.name}  ${r.type || ""}  ${r.size || ""}${r.was ? `  (was ${r.was})` : ""}${cls === "del" ? "  removed" : ""}`);
  return h("div", { class: "dbg-history" }, d.history.map((e) => h("div", { class: "dbg-run" },
    h("b", {}, `Cell ${e.n} · ${e.at || ""} · ${typeof e.seconds === "number" ? `${e.seconds.toFixed(1)} s` : ""}${e.status && e.status !== "ok" ? ` · ${e.status}` : ""}`),
    !e.added.length && !e.changed.length && !e.removed.length ? h("span", { class: "muted-text" }, "No variables changed.") : null,
    ...e.added.map((r) => row("add", "+", r)), ...e.changed.map((r) => row("mod", "~", r)), ...e.removed.map((r) => row("del", "−", r)))),
  h("div", { class: "muted-text dbg-note" }, "Names, types and sizes only; values aren't kept. History is cleared when the kernel restarts and isn't saved."));
}

// Repaint the pane in place, so editors keep focus.
function dbgPaint() {
  const nb = state.notebook;
  if (state.view !== "notebook" || !nb || !state.showVariables) return;
  const pane = document.querySelector(".varpane .dbg-pane");
  if (pane) pane.replaceWith(dbgPane(nb));
  else document.querySelector(".var-list")?.replaceChildren(variableTable(state.variables || []));
}

// ---------- breakpoints and stepping (Prove stage 5, item 44) ----------
// Click a line number (or press F9 on a line) to set a breakpoint; a red bar marks it. A cell with
// breakpoints pauses at them whenever it runs on this PC, and "Debug cell" pauses at its first
// line. While paused, the panel under the cell shows the line, the call stack, that frame's
// variables (secrets hidden) and the watches, and evaluates expressions in that frame. The engine
// side is debugger.py. Breakpoints are kept for this session and not saved with the notebook.

const bpComp = new CM.Compartment();

function dbgBreakpoints(cell) {
  return dbgOn() && cell.type === "code" ? cell.breakpoints || [] : [];
}

// The editor extensions for one cell: its breakpoint and paused-line marks.
function dbgEditorMarks(cell) {
  return bpComp.of(dbgMarks(cell));
}

function dbgMarks(cell) {
  const lines = new Map(dbgBreakpoints(cell).map((n) => [n, "cm-bp"]));
  if (cell.paused?.line) lines.set(cell.paused.line, `${lines.has(cell.paused.line) ? "cm-bp " : ""}cm-paused`);
  if (!lines.size) return [];
  const view = editors.get(cell.id);
  const doc = view ? view.state.doc : CM.EditorState.create({ doc: cell.source }).doc;
  const b = new CM.RangeSetBuilder();
  for (const n of [...lines.keys()].sort((x, y) => x - y)) {
    if (n <= doc.lines) b.add(doc.line(n).from, doc.line(n).from, CM.Decoration.line({ class: lines.get(n) }));
  }
  return CM.EditorView.decorations.of(b.finish());
}

function dbgRepaintMarks(cell) {
  editors.get(cell.id)?.dispatch({ effects: bpComp.reconfigure(dbgMarks(cell)) });
}

function dbgToggleBreakpoint(cell, line) {
  if (!dbgOn() || cell.type !== "code") return false;
  const set = new Set(cell.breakpoints || []);
  if (set.has(line)) set.delete(line);
  else set.add(line);
  cell.breakpoints = [...set].sort((a, b) => a - b);
  dbgRepaintMarks(cell);
  return true;
}

// The line number gutter: a click sets or clears a breakpoint on that line.
function dbgGutterClick(cell, view, block) {
  return dbgToggleBreakpoint(cell, view.state.doc.lineAt(block.from).number);
}

function dbgToggleAtCursor(cell) {
  const view = editors.get(cell.id);
  if (!view) return false;
  return dbgToggleBreakpoint(cell, view.state.doc.lineAt(view.state.selection.main.head).number);
}

// Called by the editor's update listener: breakpoints follow the lines they were set on.
function dbgEdited(cell, u) {
  if (!cell.breakpoints?.length) return;
  const before = u.startState.doc;
  const moved = dbgBpMap(cell.breakpoints, (n) => {
    if (n > before.lines) return null;
    const line = before.line(n);
    return u.state.doc.lineAt(u.changes.mapPos(line.from, 1)).number;
  }, u.state.doc.lines);
  if (moved.join() !== cell.breakpoints.join()) {
    cell.breakpoints = moved;
    queueMicrotask(() => dbgRepaintMarks(cell));
  }
}

// What runCell sends with start_cell: the cell's breakpoints, and step to pause at the first line.
function dbgStartArgs(cell, debug) {
  const lines = dbgBreakpoints(cell);
  if (!dbgOn()) return {};
  return { ...(lines.length ? { breakpoints: lines } : {}), ...(debug ? { step: true } : {}) };
}

// Called by runCell with each snapshot: shows or clears the paused line and panel.
function dbgSnapshot(cell, snap) {
  const was = cell.paused?.line;
  const now = snap?.status === "running" ? snap.paused || null : null;
  const changed = JSON.stringify(cell.paused || null) !== JSON.stringify(now);
  cell.paused = now;
  if (was !== now?.line) dbgRepaintMarks(cell);
  return changed;
}

function dbgPausedCell() {
  return state.notebook?.cells.find((c) => c.paused) || null;
}

async function dbgCommand(action, expression) {
  const cell = dbgPausedCell();
  if (!cell) return null;
  return attempt(() => rpc("debug_cell", { action, notebook: state.notebook.name, ...(expression != null ? { expression } : {}) }), "Could not reach the debugger");
}

function debugCell(cell) {
  return runCell(cell, { debug: true });
}

// The panel under a paused cell.
function dbgPausedPanel(cell) {
  const p = cell.paused;
  if (!p) return null;
  const input = h("input", { class: "dbg-add", placeholder: "Evaluate in this frame, for example df.shape", "aria-label": "Expression to evaluate", maxlength: 500 });
  const evaluate = async () => {
    const expr = input.value.trim();
    if (!expr) return;
    const res = await dbgCommand("eval", expr);
    if (res) input.value = "";
    if (res?.pending) toast("Still evaluating. The result shows here when it finishes.");
  };
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") evaluate(); });
  const step = (label, action, key, ic) => btn(label, ic, () => dbgCommand(action), "tool outline small", { title: `${label} (${key})` });
  const value = (r) => r.error ? h("span", { class: "warn" }, r.error) : h("span", { class: "dbg-value" }, r.value, r.type ? h("span", { class: "muted-text" }, `  ${r.type}${r.size ? ` · ${r.size}` : ""}`) : null);
  return h("div", { class: "output dbg-paused", role: "region", "aria-label": "Debugger" },
    h("div", { class: "dbg-hidden-title" }, h("b", {}, `Paused at line ${p.line}${p.reason === "breakpoint" ? " (breakpoint)" : ""}`), previewTag()),
    h("div", { class: "dbg-actions" },
      step("Continue", "continue", "F5", "play"), step("Step over", "next", "F10", null), step("Step into", "step", "F11", null),
      step("Step out", "out", "Shift+F11", null), btn("Stop", "stop", cancelRun, "tool outline small", { title: "Cancel the cell (keeps variables)" })),
    p.stack?.length > 1 ? h("div", { class: "muted-text" }, "Call stack: ", h("code", {}, dbgStackText(p.stack))) : null,
    h("div", { class: "dbg-frame" },
      h("div", { class: "dbg-head" }, p.stack?.length > 1 ? `Variables in ${p.stack[p.stack.length - 1].function}` : "Variables"),
      p.variables?.length ? h("table", { class: "vars" }, h("tbody", {}, p.variables.map((r) => h("tr", {},
        h("td", { class: "name" }, r.name), h("td", {}, r.type), h("td", {}, r.size), h("td", { class: "val" }, r.value)))))
        : h("div", { class: "muted-text" }, "None yet."),
      p.watches?.length ? h("div", { class: "dbg-head" }, "Watches") : null,
      ...(p.watches || []).map((w) => h("div", { class: "dbg-eval" }, h("code", {}, w.expr), value(w))),
      h("div", { class: "dbg-head" }, "Evaluate"),
      ...(p.evals || []).map((w) => h("div", { class: "dbg-eval" }, h("code", {}, w.expr), value(w))),
      h("div", { class: "dbg-addrow" }, input, btn("Evaluate", null, evaluate, "tool outline small"))),
    h("div", { class: "muted-text dbg-note" }, "Evaluating runs notebook code in the paused frame, so keep it to reads. A cell under the debugger has no time limit. Only this cell's own code stops at breakpoints; libraries, %run notebooks and threads the cell starts don't."));
}

// Listed in the Keyboard shortcuts dialog while a notebook is open and breakpoints are shown (Preview).
registerShortcuts("Notebook debugger", [
  { keys: ["F9"], label: "Set or clear a breakpoint on the line", when: () => state.view === "notebook" && dbgOn() },
  { keys: ["F5"], label: "Continue to the next breakpoint (while paused)", when: () => state.view === "notebook" && dbgOn() },
  { keys: ["F10"], label: "Step over (while paused)", when: () => state.view === "notebook" && dbgOn() },
  { keys: ["F11"], label: "Step into (while paused)", when: () => state.view === "notebook" && dbgOn() },
  { keys: ["Shift+F11"], label: "Step out (while paused)", when: () => state.view === "notebook" && dbgOn() },
]);
document.addEventListener("keydown", (e) => {
  const action = dbgKeyAction(e);
  if (!action || state.view !== "notebook" || !dbgPausedCell()) return;
  e.preventDefault();
  dbgCommand(action);
});
