// Cell dependencies and stale outputs: the View tab's Dependencies switch shows under each code
// cell what it uses, reads and writes and which cells it depends on; cells whose output is out of
// date get a Stale tag; the Run tab's "Run stale cells" runs them again in order, asking first when
// any of them changes something outside the notebook (writes a table or file, calls the network).
// The engine reads the dependencies from the code (depgraph.py); this file decides what is stale,
// from what each cell's code was when it last ran in this kernel.

// ---------- pure helpers (app/tests/ui.test.cjs runs these) ----------

// Which cells are out of date. `cells`: [{cell_id, source, ran}] where ran = {source, seq, epoch}
// from the cell's last successful run (null if it hasn't run or its last run failed). `graph`:
// notebook_dependencies' answer. Returns {cell_id: {why, text}} for stale cells only. Conservative:
// a cell is stale when its code changed, its kernel restarted, a cell it depends on is stale or ran
// again after it, or (for cells reading can't follow: exec, %run, import *) any cell above changed.
function depsStale(cells, graph, epoch) {
  const byId = new Map(cells.map((c) => [c.cell_id, c]));
  const out = {};
  const seen = [];
  for (const g of graph?.cells || []) {
    const c = byId.get(g.cell_id);
    if (!c) continue;
    const ran = c.ran;
    seen.push(g);
    if (!ran) continue;
    if (ran.epoch !== epoch) {
      out[g.cell_id] = { why: "restart", text: "Stale: kernel restarted" };
      continue;
    }
    if (ran.source !== c.source) {
      out[g.cell_id] = { why: "code", text: "Stale: code changed" };
      continue;
    }
    const upstream = g.dynamic?.length ? seen.slice(0, -1).map((u) => ({ cell_id: u.cell_id, n: u.n })) : g.upstream || [];
    for (const u of upstream) {
      const uc = byId.get(u.cell_id);
      if (out[u.cell_id]) {
        out[g.cell_id] = { why: "upstream", text: `Stale: cell ${u.n} is out of date`, from: u.n };
        break;
      }
      if (uc?.ran && uc.ran.epoch === epoch && uc.ran.seq > ran.seq) {
        out[g.cell_id] = { why: "upstream", text: `Stale: cell ${u.n} ran again`, from: u.n };
        break;
      }
    }
  }
  return out;
}

// What "Run stale cells" runs: the stale cells in notebook order, the ones among them that change
// something outside the notebook, and the stale cells before the first of those (run without asking).
function depsPlan(stale, graph) {
  const run = (graph?.cells || []).filter((g) => stale[g.cell_id]);
  const effects = run.filter((g) => g.effects?.length).map((g) => ({ cell_id: g.cell_id, n: g.n, effects: g.effects.map((e) => e.text) }));
  const first = effects.length ? run.findIndex((g) => g.cell_id === effects[0].cell_id) : run.length;
  return { run: run.map((g) => g.cell_id), ns: run.map((g) => g.n), effects, before: run.slice(0, first).map((g) => g.cell_id) };
}

// "2", "2 and 3", "2, 3 and 4"
function depsList(ns) {
  return ns.length < 2 ? ns.join("") : `${ns.slice(0, -1).join(", ")} and ${ns[ns.length - 1]}`;
}

// The lines under a cell: what it defines, uses, reads, writes; edges say whether they were found in
// the code or declared in the cell's metadata.
function depsLines(g) {
  const lines = [];
  const vars = (g.upstream || []).filter((e) => e.kind === "variable");
  if (vars.length) lines.push({ text: "Uses ", refs: vars.map((e) => ({ label: `${e.via.join(", ")} from`, n: e.n })) });
  for (const e of (g.upstream || []).filter((x) => x.kind === "table" || x.kind === "file")) {
    lines.push({ text: `Reads ${e.kind} ${e.via.join(", ")}, written by`, refs: [{ label: "", n: e.n }] });
  }
  const declared = (g.upstream || []).filter((e) => e.source === "declared");
  if (declared.length) lines.push({ text: "Declared to depend on", refs: declared.map((e) => ({ label: "", n: e.n })) });
  const fed = new Set((g.upstream || []).filter((e) => e.kind !== "variable").flatMap((e) => e.via));
  const reads = (g.reads || []).filter((r) => !fed.has(r.name)).map((r) => r.name);
  if (reads.length) lines.push({ text: `Reads ${[...new Set(reads)].join(", ")}` });
  const writes = [...new Set((g.writes || []).map((w) => `${w.kind} ${w.name}`))];
  if (writes.length) lines.push({ text: `Writes ${writes.join(", ")}` });
  if (g.defines?.length) lines.push({ text: `Defines ${g.defines.slice(0, 8).join(", ")}${g.defines.length > 8 ? ` and ${g.defines.length - 8} more` : ""}` });
  if (g.downstream?.length) lines.push({ text: "Used by", refs: g.downstream.map((n) => ({ label: "", n })) });
  for (const u of g.unresolved || []) lines.push({ text: u.text, warn: true });
  for (const d of g.dynamic || []) lines.push({ text: `Line ${d.line}: ${d.text}, so it's treated as depending on every cell above`, warn: true });
  return lines;
}

// ---------- state ----------

let depsSeq = 0;      // order of successful runs
let depsEpoch = 0;    // bumped when the kernel restarts or stops: variables are gone
let depsTimer = null;

function depsOn() {
  return !!state.showDeps && previewShown();
}

function depsCells(nb) {
  return nb.cells.filter((c) => c.type === "code").map((c) => ({ cell_id: c.cell_id, source: c.source, ran: c.ran || null }));
}

// Called by notebook.js after a cell finishes.
function depsRan(cell, ok) {
  cell.ran = ok ? { source: cell.source, seq: ++depsSeq, epoch: depsEpoch } : null;
  depsChanged();
}

function depsRestarted() {
  depsEpoch += 1;
  dbgRestarted();  // debugging.js
  depsChanged();
}

// Something changed (an edit, a run, a move): refresh the graph if the code changed, then repaint.
function depsChanged(now = false) {
  if (!depsOn() || state.view !== "notebook") return;
  clearTimeout(depsTimer);
  depsTimer = setTimeout(depsRefresh, now ? 0 : 400);
}

async function depsRefresh(nb = state.notebook) {
  if (!nb) return null;
  const payload = nb.cells.map((c) => ({ type: c.type, source: c.source, cell_id: c.cell_id, meta: { depends_on: c.meta?.depends_on || [] } }));
  const key = JSON.stringify(payload);
  if (nb.deps?.key !== key) {
    try {
      const graph = await rpc("notebook_dependencies", { cells: payload });
      nb.deps = { key, graph, byId: new Map(graph.cells.map((g) => [g.cell_id, g])) };
    } catch (err) {
      nb.deps = { key, graph: null, byId: new Map(), problem: String(err) };
    }
  }
  if (state.notebook === nb) depsPaint(nb);
  return nb.deps;
}

function depsStaleNow(nb) {
  return nb.deps?.graph ? depsStale(depsCells(nb), nb.deps.graph, depsEpoch) : {};
}

// ---------- the notebook screen ----------

function depsToggle() {
  return preview("deps", btn("Dependencies", "flow", () => { state.showDeps = !state.showDeps; renderNotebook(); if (state.showDeps) depsChanged(true); },
    `tool${state.showDeps ? " toggled" : ""}`, { "aria-pressed": String(!!state.showDeps),
      title: "Show what each cell depends on, read from its code, and which outputs are out of date" }));
}

function depsRunButton(nb, attrs) {
  const n = Object.keys(depsStaleNow(nb)).length;
  return preview("deps", btn(depsOn() && nb.deps?.graph ? `Run stale cells (${n})` : "Run stale cells", "restart", () => runStale(nb), "tool",
    { ...attrs, "data-deps-run": true, title: "Run again the cells whose output is out of date, in order. Asks first before a cell that writes a table or file." }));
}

// The tag in a cell's head and the strip under its code; empty when the view is off.
function depsHead(cell) {
  const nb = state.notebook;
  if (!depsOn() || cell.type !== "code" || !nb) return h("span", { class: "deps-head" });
  const n = nb.cells.indexOf(cell) + 1;
  const stale = depsStaleNow(nb)[cell.cell_id];
  const g = nb.deps?.byId.get(cell.cell_id);
  return h("span", { class: "deps-head" }, h("span", { class: "tag deps-n" }, `Cell ${n}`),
    stale ? h("span", { class: "tag deps-stale", title: "The output may not match the code and data above it" }, stale.text) : null,
    g?.writes?.some((w) => w.kind === "table") ? h("span", { class: "tag deps-fx" }, "Writes a table") : null);
}

function depsStrip(cell) {
  const nb = state.notebook;
  if (!depsOn() || cell.type !== "code" || !nb) return h("div", { class: "deps-strip", hidden: true });
  if (nb.deps?.problem) return h("div", { class: "deps-strip" }, h("span", { class: "warn" }, `Couldn't read dependencies: ${nb.deps.problem}`));
  const g = nb.deps?.byId.get(cell.cell_id);
  if (!g) return h("div", { class: "deps-strip" }, h("span", {}, nb.deps ? "Nothing to show for this cell." : "Reading dependencies..."));
  const ref = (n) => h("button", { class: "deps-ref", title: `Go to cell ${n}`, onclick: () => depsGo(nb, n) }, String(n));
  const lines = depsLines(g);
  return h("div", { class: "deps-strip" }, lines.length ? lines.map((l) => h("span", { class: l.warn ? "warn" : null },
    l.text, ...(l.refs || []).flatMap((r, i) => [i ? ", " : " ", r.label ? `${r.label} ` : "", ref(r.n)])))
    : h("span", {}, "Uses nothing from other cells."));
}

function depsGo(nb, n) {
  const cell = nb.cells[n - 1];
  if (!cell) return;
  selectCell(cell);
  document.querySelector(`[data-cell="${cell.id}"]`)?.scrollIntoView({ block: "center", behavior: "smooth" });
}

// Repaint tags and strips in place, so editors keep focus.
function depsPaint(nb) {
  if (state.view !== "notebook" || state.notebook !== nb) return;
  for (const cell of nb.cells) {
    const el = document.querySelector(`[data-cell="${cell.id}"]`);
    if (!el || cell.type !== "code") continue;
    el.querySelector(".deps-head")?.replaceWith(depsHead(cell));
    el.querySelector(".deps-strip")?.replaceWith(depsStrip(cell));
  }
  const n = Object.keys(depsStaleNow(nb)).length;
  document.querySelectorAll("[data-deps-run]").forEach((b) => {
    const label = [...b.childNodes].find((x) => x.nodeType === 3);
    if (label) label.textContent = depsOn() && nb.deps?.graph ? `Run stale cells (${n})` : "Run stale cells";
  });
}

async function runStale(nb) {
  if (state.kernel === "busy") return;
  const deps = await depsRefresh(nb);
  if (!deps?.graph) {
    toast(`Couldn't read the notebook's dependencies${deps?.problem ? `: ${deps.problem}` : ""}.`, true);
    return;
  }
  const plan = depsPlan(depsStaleNow(nb), deps.graph);
  const cellsFor = (ids) => ids.map((id) => nb.cells.find((c) => c.cell_id === id)).filter(Boolean);
  if (!plan.run.length) {
    toast("No outputs are out of date. Cells that haven't run yet aren't counted; use Run all for those.");
    return;
  }
  if (!plan.effects.length) return runCells(cellsFor(plan.run));
  const fx = plan.effects.map((e) => h("li", {}, `Cell ${e.n} ${e.effects.join("; ")}.`));
  const before = plan.ns.slice(0, plan.before.length);
  showDialog(`Run ${plan.run.length} stale cell${plan.run.length === 1 ? "" : "s"}?`, h("div", { class: "dialog-text" },
    h("p", {}, `Cells ${depsList(plan.ns)} are out of date. Running them again also does this:`),
    h("ul", { class: "deps-fx-list" }, fx),
    h("p", { class: "muted-text" }, "Dependencies are read from the code. A cell that uses exec, %run or import * can depend on anything, so it's treated as out of date after any change above it.")), [
    btn(`Run ${depsList(plan.ns)}`, null, () => { closeDialog(); runCells(cellsFor(plan.run)); }, "tool primary"),
    before.length ? btn(`Run ${depsList(before)} only`, null, () => { closeDialog(); runCells(cellsFor(plan.before)); }, "tool outline") : null,
    btn("Cancel", null, closeDialog, "tool outline"),
  ].filter(Boolean));
}

registerFeature({ id: "deps" });
