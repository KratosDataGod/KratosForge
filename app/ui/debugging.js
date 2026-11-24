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

// Breakpoint lines after the whole cell was replaced (Format cell, a language change, Find and
// Replace without an editor): each finds its line's text in the new code, the occurrence nearest
// where it was. `oldText(n)` is line n's text before; `newLines` the lines now. A breakpoint whose
// text is gone (or was a blank line) is returned in `dropped`, so it can be named, not lost.
function dbgBpRematch(lines, oldText, newLines) {
  const at = new Map();
  newLines.forEach((t, i) => {
    const key = t.replace(/\s+/g, "");  // Format cell changes spacing, not what a line says
    if (key) at.set(key, [...(at.get(key) || []), i + 1]);
  });
  const kept = new Set(), dropped = [];
  for (const n of lines || []) {
    const options = at.get((oldText(n) ?? "").replace(/\s+/g, ""));
    if (!options) { dropped.push(n); continue; }
    kept.add(options.reduce((best, m) => (Math.abs(m - n) < Math.abs(best - n) ? m : best)));
  }
  return { lines: [...kept].sort((a, b) => a - b), dropped };
}

// Whether an edit deleted a line: one change (`ranges`, [from, to] in the old text) covers all of
// [start, end), the line and its line break. A line emptied, or joined to the next, isn't deleted.
function dbgSpanGone(start, end, ranges) {
  return end > start && ranges.some(([a, b]) => a <= start && b >= end);
}

const dbgBpLimit = 100;  // breakpoints per cell, as debugger.py MAX_BREAKPOINTS takes

// What to tell the person when breakpoints were removed: never silently.
function dbgDroppedText(dropped, why) {
  if (!dropped.length) return "";
  const list = dropped.length === 1 ? `line ${dropped[0]}`
    : `lines ${dropped.slice(0, -1).join(", ")} and ${dropped[dropped.length - 1]}`;
  return `Removed the ${dropped.length === 1 ? "breakpoint" : "breakpoints"} on ${list}, because ${why}.`;
}

// Where a pause is in the code as it is now. The kernel pauses on a line of the code as it ran, so
// after an edit mid-run that line is found again by its text (as dbgBpRematch does), and is null
// when it isn't in the cell any more: the paused mark is then never put on some other line.
function dbgPausedLine(ran, now, line) {
  if (ran == null || ran === now) return { line, edited: false };
  const before = ran.split("\n");
  const found = dbgBpRematch([line], (n) => before[n - 1] ?? null, now.split("\n")).lines;
  return { line: found.length ? found[0] : null, edited: true, text: (before[line - 1] || "").trim() };
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

// A pause as the engine sent it, in the shape the panel draws: every field checked and capped, so
// a garbled or oversized snapshot (an engine bug, a newer engine) shows less, never breaks the
// notebook. No usable line means no pause to show.
const DBG_MAX_ROWS = 500;
function dbgCleanPause(p) {
  if (!p || typeof p !== "object" || Array.isArray(p)) return null;
  const whole = (v) => (Number.isInteger(v) && v >= 1 ? v : null);
  if (whole(p.line) == null) return null;
  const text = (v, n = 2000) => (v == null ? "" : typeof v === "object" ? "" : String(v)).slice(0, n);
  const items = (v, n) => (Array.isArray(v) ? v.slice(0, n).filter((x) => x && typeof x === "object" && !Array.isArray(x)) : []);
  const value = (r) => ({ expr: text(r.expr), value: text(r.value), type: text(r.type, 200), size: text(r.size, 200),
    ...(r.error != null ? { error: text(r.error) } : {}) });
  const out = {
    line: p.line,
    reason: text(p.reason, 40),
    stack: items(p.stack, DBG_MAX_ROWS).map((f) => ({ function: text(f.function, 200), line: whole(f.line) ?? 0 })),
    variables: items(p.variables, DBG_MAX_ROWS).map((r) => ({ name: text(r.name, 200), type: text(r.type, 200), size: text(r.size, 200), value: text(r.value) })),
    watches: items(p.watches, 100).map(value),
    evals: items(p.evals, 100).map(value),
  };
  if (whole(p.variables_total) != null) out.variables_total = p.variables_total;
  const skipped = Array.isArray(p.skipped) ? p.skipped.filter((n) => typeof n === "string").slice(0, 20).map((n) => n.slice(0, 200)) : [];
  if (skipped.length) out.skipped = skipped;
  return out;
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
  const nb = state.notebook;
  // A restart ends a paused cell (the engine stops it): nothing stays paused, and a Debug from
  // here run doesn't go on into the fresh kernel.
  if (nb?.dbgRun) nb.cancelRequested = true;
  for (const c of nb?.cells || []) {
    if (!c.paused) continue;
    c.paused = null;
    dbgRepaintMarks(c);
    updateResults(c);
  }
  const d = nb?.debug;
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
      // The pane is drawn afresh: keep focus in the box, so the next watch can be typed straight away.
      document.querySelector(".varpane .dbg-add")?.focus();
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
// side is debugger.py. Breakpoints are a cell setting (cell.meta.breakpoints), saved with the
// notebook like the parameters tag (notebook._cell_metadata), so they survive save and reopen.
// "Debug from here" runs the cells from this one down under the debugger: after a step, the next
// cell starts paused at its first line; after Continue, it stops only at its breakpoints.
// Only Python on this PC can pause. A run on another engine, in Fabric or on Fabric Spark says so
// (dbgRemoteNote) instead of quietly ignoring the breakpoints.

const bpComp = new CM.Compartment();

function dbgBreakpoints(cell) {
  return dbgOn() && cell.type === "code" ? cell.meta?.breakpoints || [] : [];
}

// The editor extensions for one cell: its breakpoint and paused-line marks.
function dbgEditorMarks(cell) {
  return bpComp.of(dbgMarks(cell));
}

function dbgMarks(cell) {
  const lines = new Map(dbgBreakpoints(cell).map((n) => [n, "cm-bp"]));
  const at = cell.paused?.line ? dbgPausedLine(cell.dbgRan, cell.source, cell.paused.line).line : null;
  if (at) lines.set(at, `${lines.has(at) ? "cm-bp " : ""}cm-paused`);
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
  const set = new Set(cell.meta?.breakpoints || []);
  if (set.has(line)) set.delete(line);
  else if (set.size >= dbgBpLimit) {  // the engine would refuse to run the cell
    toast(`A cell can have up to ${dbgBpLimit} breakpoints. Clear one to set another.`);
    return true;
  } else set.add(line);
  dbgSetLines(cell, [...set].sort((a, b) => a - b));
  dbgRepaintMarks(cell);
  markDirty();  // saved with the notebook
  return true;
}

// A cell's breakpoints, kept in its settings; none removes the setting.
function dbgSetLines(cell, lines) {
  cell.meta = cell.meta || {};
  if (lines.length) cell.meta.breakpoints = lines;
  else delete cell.meta.breakpoints;
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

// Called by the editor's update listener: breakpoints follow the lines they were set on. When the
// whole cell was replaced at once (Format cell, a language change), each finds its line's text
// again instead. A breakpoint that can't follow is removed and named in a message; undoing that
// edit puts it back.
function dbgEdited(cell, u) {
  if (cell.paused) dbgPausedEdited(cell);
  const lines = cell.meta?.breakpoints || [];
  if (!lines.length && !cell.dbgDropped?.length) return;
  const before = u.startState.doc;
  let whole = false;
  const ranges = [];
  u.changes.iterChanges((fromA, toA) => {
    ranges.push([fromA, toA]);
    if (fromA === 0 && toA === before.length && before.length > 0) whole = true;
  });
  let moved, dropped, why;
  if (whole) {
    ({ lines: moved, dropped } = dbgBpRematch(lines, (n) => (n <= before.lines ? before.line(n).text : null),
      u.state.doc.toString().split("\n")));
    why = dropped.length === 1 ? "that line isn't in the cell any more" : "those lines aren't in the cell any more";
  } else {
    // A deleted line takes its breakpoint, never handing it to the line that moved up into its place.
    // The last line goes with the line break before it.
    const at = (n) => {
      if (n > before.lines) return null;
      const line = before.line(n);
      const last = n === before.lines;
      if (dbgSpanGone(last && n > 1 ? line.from - 1 : line.from, last ? line.to : line.to + 1, ranges)) return null;
      return u.state.doc.lineAt(u.changes.mapPos(line.from, 1)).number;
    };
    moved = dbgBpMap(lines, at, u.state.doc.lines);
    // Two that landed on one line are one now; one past the end went with its line.
    const kept = new Set(moved);
    const landed = lines.map(at);
    dropped = lines.filter((n, i) => landed[i] == null || !kept.has(landed[i]) || landed.indexOf(landed[i]) !== i);
    why = dropped.length === 1 ? "its line was deleted" : "their lines were deleted";
  }
  // Undo of an edit that removed breakpoints: they come back with their lines (redo removes them again).
  const undo = u.transactions.some((tr) => tr.isUserEvent("undo"));
  const doc = undo || dropped.length ? u.state.doc.toString() : null;
  if (undo) {
    const was = before.toString();
    const i = (cell.dbgDropped || []).findLastIndex((r) => r.after === was && r.before === doc);
    if (i >= 0) {
      moved = [...new Set([...moved, ...cell.dbgDropped[i].lines.filter((n) => n <= u.state.doc.lines)])].sort((a, b) => a - b);
      cell.dbgDropped.splice(i, 1);
    }
  }
  if (dropped.length) cell.dbgDropped = [...(cell.dbgDropped || []), { before: before.toString(), after: doc, lines: dropped }].slice(-20);
  if (moved.join() !== lines.join()) {
    dbgSetLines(cell, moved);  // the edit itself marks the notebook changed
    queueMicrotask(() => dbgRepaintMarks(cell));
  }
  if (dropped.length) toast(dbgDroppedText(dropped, why));
}

// An edit while the cell is paused: the paused mark follows its line's code, and the panel says the
// pause is in the code as it ran (once, when that changes, so what's typed in Evaluate is kept).
function dbgPausedEdited(cell) {
  queueMicrotask(() => dbgRepaintMarks(cell));
  const p = dbgPausedLine(cell.dbgRan, cell.source, cell.paused.line);
  const key = p.edited ? String(p.line) : "";
  if (key !== (cell.dbgEditedAt || "")) {
    cell.dbgEditedAt = key;
    updateResults(cell);  // notebook.js
  }
}

// Before a run: breakpoints past the cell's end (its code changed where no editor saw it, or in
// another program) are removed and named, never sent and quietly never hit.
function dbgCheckLines(cell) {
  const lines = cell.meta?.breakpoints || [];
  const count = cell.source.split("\n").length;
  const dropped = lines.filter((n) => n > count);
  if (!dropped.length) return;
  dbgSetLines(cell, lines.filter((n) => n <= count));
  dbgRepaintMarks(cell);
  markDirty();
  toast(dbgDroppedText(dropped, `the cell has ${count} ${count === 1 ? "line" : "lines"} now`));
}

// What runCell sends with start_cell: the cell's breakpoints, and step to pause at the first line.
function dbgStartArgs(cell, debug) {
  if (dbgOn() && cell.type === "code") dbgCheckLines(cell);
  const lines = dbgBreakpoints(cell);
  if (!dbgOn()) return {};
  cell.dbgRan = cell.source;  // what the kernel pauses in, if the cell is edited while it runs
  cell.dbgEditedAt = "";
  cell.dbgSent = null;
  return { ...(lines.length ? { breakpoints: lines } : {}), ...(debug ? { step: true } : {}) };
}

// Called by runCell with each snapshot: shows or clears the paused line and panel.
// cell.dbgSent is a command on its way: "waiting" for its answer, then "sent" until the pause it led
// to comes (the engine has left the old pause by the time it answers). dbgGap: a snapshot with no
// pause came meanwhile, so the next pause is a new one even before the answer.
function dbgSnapshot(cell, snap) {
  const was = cell.paused?.line;
  const now = snap?.status === "running" ? dbgCleanPause(snap.paused) : null;
  const changed = JSON.stringify(cell.paused || null) !== JSON.stringify(now);
  let fresh = !!now && (!cell.paused || was !== now.line);
  if (snap?.status !== "running") cell.dbgSent = null;
  else if (!now) cell.dbgGap = !!cell.dbgSent;
  else if (cell.dbgSent === "sent" || (cell.dbgSent && cell.dbgGap)) {
    cell.dbgSent = null;
    fresh = true;
  }
  cell.paused = now;
  if (was !== now?.line) dbgRepaintMarks(cell);
  if (fresh) dbgPauseShown(cell);
  return changed;
}

// A new pause in the notebook on screen: said once to screen readers, and brought into view, out
// of a collapsed section too.
function dbgPauseShown(cell) {
  const nb = state.notebook;
  if (state.view !== "notebook" || !nb?.cells.includes(cell)) return;
  announce(`Cell ${nb.cells.indexOf(cell) + 1} paused at line ${cell.paused.line}${cell.paused.reason === "breakpoint" ? " (breakpoint)" : ""}`);
  queueMicrotask(() => {  // after runCell draws the panel
    if (!cell.paused || state.notebook !== nb || !nb.cells.includes(cell)) return;
    revealCell(cell);  // nbcells.js
    const view = editors.get(cell.id);
    const at = dbgPausedLine(cell.dbgRan, cell.source, cell.paused.line).line;
    if (view && at && at <= view.state.doc.lines) {
      view.dispatch({ effects: CM.EditorView.scrollIntoView(view.state.doc.line(at).from, { y: "nearest", yMargin: 120 }) });
    }
  });
}

// A cell being deleted while paused would leave the kernel waiting on it, with nothing on screen to
// continue or stop it. True (and says so) when one of `cells` is paused.
function dbgPausedIn(cells) {
  if (!cells.some((c) => c.paused)) return false;
  toast(cells.length === 1 ? "This cell is paused in the debugger. Stop it to delete it."
    : "A selected cell is paused in the debugger. Stop it to delete it.");
  return true;
}

// updateResults (notebook.js) redraws the panel: what had focus in it gets it back in the new one,
// with the caret where it was. When the panel is gone (the cell ended), the cell takes focus.
function dbgFocusKey(el) {
  const a = document.activeElement;
  if (!a || !el.contains(a) || !a.closest(".dbg-paused")) return null;
  return { key: a.dataset.dbg, start: a.selectionStart ?? null, end: a.selectionEnd ?? null };
}

function dbgRefocus(el, keep) {
  if (!keep || el.contains(document.activeElement)) return;
  const t = keep.key && el.querySelector(`.dbg-paused [data-dbg="${keep.key}"]`);
  if (!t) { el.focus({ preventScroll: true }); return; }
  t.focus({ preventScroll: true });
  if (keep.start != null) t.setSelectionRange(keep.start, keep.end);
}

function dbgPausedCell() {
  return state.notebook?.cells.find((c) => c.paused) || null;
}

// One command at a time: a key pressed again, or held, while a step or Evaluate is on its way does
// nothing, so it can't step past lines not yet shown or hit a pause that has ended.
async function dbgCommand(action, expression) {
  const cell = dbgPausedCell();
  if (!cell || cell.dbgSent) return null;
  // In a Debug from here run, the last way out of a pause decides how the next cell starts.
  if (state.notebook.dbgRun && action !== "eval") state.notebook.dbgRun.mode = action;
  cell.dbgSent = "waiting";
  cell.dbgGap = false;
  const res = await attempt(() => rpc("debug_cell", { action, notebook: state.notebook.name, ...(expression != null ? { expression } : {}) }), "Could not reach the debugger");
  if (cell.dbgSent === "waiting") cell.dbgSent = res && action !== "eval" && cell.status === "running" ? "sent" : null;
  return res;
}

function debugCell(cell) {
  if (dbgRemoteNote(state.notebook, [cell], true)) return false;
  return runCell(cell, { debug: true });
}

// Debug from here: this cell and the ones below it, starting paused at this cell's first line.
function debugFromHere(cell) {
  const cells = state.notebook.cells;
  if (dbgRemoteNote(state.notebook, cells.slice(cells.indexOf(cell)), true)) return false;
  return runCells(cells.slice(cells.indexOf(cell)), { debug: true });
}

// How runCells starts each cell of a Debug from here run: paused at its first line unless the
// last command was Continue. False outside such a run.
function dbgStepNext(nb) {
  return !!nb?.dbgRun && nb.dbgRun.mode !== "continue";
}

// Where cells of `nb` run when not in Python on this PC, or null when they run here: the engine's
// name, Fabric (a Fabric job runs the whole notebook) or Fabric Spark (a cell set to run there).
function dbgRemoteWhere(nb, cells) {
  if (nbOnEngine(nb)) return nbEngine(nb).label || "This engine";  // engines.js
  if (nbWhere(nb).fabric) return "A Fabric run";
  if ((cells || []).some((c) => isFabricStep(c))) return "Fabric Spark";  // fabricstep.js
  return null;
}

// Says plainly that a run elsewhere can't pause, when it was asked to debug or a cell in it has
// breakpoints. True when `debug` was asked for there, so the caller doesn't start anything; a run
// with breakpoints alone goes on, to the end.
function dbgRemoteNote(nb, cells, debug = false) {
  if (!dbgOn() || !nb) return false;
  const where = dbgRemoteWhere(nb, cells);
  if (!where) return false;
  if (where === "Fabric Spark") {  // only the step cell runs there; the cells above still debug here
    const steps = cells.filter((c) => isFabricStep(c));
    if (!debug && !steps.some((c) => dbgBreakpoints(c).length)) return false;
    toast("The last cell runs on Fabric Spark, which can't pause, so it runs to the end. Cells on this PC still stop at their breakpoints.");
    return debug && steps.length === cells.length;
  }
  if (!debug && !cells.some((c) => dbgBreakpoints(c).length)) return false;
  toast(debug ? `${where} can't pause, so it can't be debugged. Switch Engine to Python (this PC) to step through cells.`
    : `${where} can't pause, so breakpoints are ignored and the cells run to the end. Switch Engine to Python (this PC) to stop at them.`);
  return debug;
}

// The panel under a paused cell.
function dbgPausedPanel(cell) {
  const p = cell.paused;
  if (!p) return null;
  // What's typed is kept on the cell, so a redraw of the panel (an answer, the next pause) keeps it.
  const input = h("input", { class: "dbg-add", placeholder: "Evaluate in this frame, for example df.shape", "aria-label": "Expression to evaluate", maxlength: 500,
    value: cell.dbgDraft || "", "data-dbg": "expr", oninput: (e) => { cell.dbgDraft = e.target.value; } });
  const evaluate = async () => {
    const expr = input.value.trim();
    if (!expr) return;
    const res = await dbgCommand("eval", expr);
    if (res && (cell.dbgDraft || "").trim() === expr) {
      cell.dbgDraft = "";
      const box = document.querySelector(`[data-cell="${cell.id}"] .dbg-paused [data-dbg="expr"]`);
      if (box) box.value = "";
    }
    if (res?.pending) toast("Still evaluating. The result shows here when it finishes.");
  };
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") evaluate(); });
  const step = (label, action, key, ic) => btn(label, ic, () => dbgCommand(action), "tool outline small", { title: `${label} (${key})`, "data-dbg": action });
  const value = (r) => r.error ? h("span", { class: "warn" }, r.error) : h("span", { class: "dbg-value" }, r.value, r.type ? h("span", { class: "muted-text" }, `  ${r.type}${r.size ? ` · ${r.size}` : ""}`) : null);
  return h("div", { class: "output dbg-paused", role: "region", "aria-label": "Debugger" },
    h("div", { class: "dbg-hidden-title" }, h("b", {}, `Paused at line ${p.line}${p.reason === "breakpoint" ? " (breakpoint)" : ""}`), previewTag()),
    dbgEditedNote(dbgPausedLine(cell.dbgRan, cell.source, p.line), p.line),
    h("div", { class: "dbg-actions" },
      step("Continue", "continue", "F5", "play"), step("Step over", "next", "F10", null), step("Step into", "step", "F11", null),
      step("Step out", "out", "Shift+F11", null), btn("Stop", "stop", cancelRun, "tool outline small", { title: "Cancel the cell (keeps variables)", "data-dbg": "stop" })),
    p.stack?.length > 1 ? h("div", { class: "muted-text" }, "Call stack: ", h("code", {}, dbgStackText(p.stack))) : null,
    p.skipped?.length ? h("div", { class: "muted-text dbg-skipped" }, "Stepped over, not into: ", h("code", {}, p.skipped.join(", ")),
      ". Step into goes only into this cell's own code.") : null,
    h("div", { class: "dbg-frame" },
      h("div", { class: "dbg-head" }, p.stack?.length > 1 ? `Variables in ${p.stack[p.stack.length - 1].function}` : "Variables"),
      p.variables?.length ? h("table", { class: "vars" }, h("tbody", {}, p.variables.map((r) => h("tr", {},
        h("td", { class: "name" }, r.name), h("td", {}, r.type), h("td", {}, r.size), h("td", { class: "val" }, r.value)))))
        : h("div", { class: "muted-text" }, "None yet."),
      dbgVariablesNote(p),
      p.watches?.length ? h("div", { class: "dbg-head" }, "Watches") : null,
      ...(p.watches || []).map((w) => h("div", { class: "dbg-eval" }, h("code", {}, w.expr), value(w))),
      h("div", { class: "dbg-head" }, "Evaluate"),
      ...(p.evals || []).map((w) => h("div", { class: "dbg-eval" }, h("code", {}, w.expr), value(w))),
      h("div", { class: "dbg-addrow" }, input, btn("Evaluate", null, evaluate, "tool outline small", { "data-dbg": "eval" }))),
    h("div", { class: "muted-text dbg-note" }, "Evaluating runs notebook code in the paused frame, so keep it to reads. A cell under the debugger has no time limit. Only this cell's own code stops at breakpoints; libraries, functions from other cells, %run notebooks and threads the cell starts don't."));
}

// The cell was changed after it started: the pause, and its breakpoints, are in the code as it ran.
function dbgEditedNote(at, line) {
  if (!at.edited) return null;
  const where = at.line == null ? `Line ${line} as it ran${at.text ? ` (${at.text})` : ""} isn't in the cell now.`
    : at.line === line ? `It's paused at line ${line} of the code as it ran.`
      : `It's paused at line ${line} of the code as it ran, which is line ${at.line} now.`;
  return h("div", { class: "muted-text dbg-note dbg-edited", role: "status" },
    `You changed this cell after it started. ${where} Your edits and breakpoint changes take effect the next time the cell runs.`);
}

// Under the paused frame's variables: what is capped and how to see more.
function dbgVariablesNote(p) {
  const parts = [];
  if (p.variables_total > (p.variables?.length || 0)) {
    parts.push(`Showing the first ${p.variables.length} of ${p.variables_total} variables, by name. Evaluate a name to see one that isn't listed.`);
  }
  if ((p.variables || []).some((r) => /×/.test(r.size || ""))) {
    parts.push("Data frames show their size and first columns only. To see rows, evaluate head() on one, such as df.head().");
  }
  return parts.length ? h("div", { class: "muted-text dbg-note dbg-capped" }, parts.join(" ")) : null;
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
