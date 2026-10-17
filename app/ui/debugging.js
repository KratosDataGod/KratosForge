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
    try { d.vars = await rpc("kernel_variables"); } catch { /* History starts at the next run */ }
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
    vars = await rpc("kernel_variables");
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
