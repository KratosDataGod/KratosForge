// The notebook's actions and its next step after a run (AF-4, docs/ux/action-first.md), declared once
// through actions.js: each is in the command bar, says why when it can't run, and the line under the
// ribbon after a run offers what the notebook's state supports, from evidence the app already holds.

const FABRIC_ONLY_ALL = "Fabric runs the whole notebook. Use Run all, or switch Runs on to This PC to run single cells.";

provideActionContext("notebook", () => ({ nb: state.notebook, busy: state.kernel === "busy" }));

const nbIdle = (ctx) => (!ctx.nb ? "Open a notebook first." : ctx.busy ? "A run is already going." : true);
const nbLocalIdle = (ctx) => (nbIdle(ctx) !== true ? nbIdle(ctx) : nbWhere(ctx.nb).fabric ? FABRIC_ONLY_ALL : true);

defineAction({ id: "notebook.run_all", label: "Run all", icon: "play", keywords: "notebook run every cell", contexts: ["notebook"],
  risk: "moderate", where: "either", primary: true, available: nbIdle, run: () => runAll(),
  // In Fabric, runInFabric asks the risk check itself (riskGate) before the job starts.
  ownRiskCheck: "runInFabric" });
defineAction({ id: "notebook.run_cell", label: "Run the selected cell", icon: "play", keywords: "notebook run cell",
  contexts: ["notebook"], risk: "moderate", where: "local", available: (ctx) => (nbLocalIdle(ctx) !== true ? nbLocalIdle(ctx)
    : selectedCell() ? true : "Select a cell first."), run: () => runCell(selectedCell()) });
defineAction({ id: "notebook.restart_run_all", label: "Restart the kernel and run all", icon: "restart",
  keywords: "notebook restart kernel run all", contexts: ["notebook"], risk: "moderate", where: "local", available: nbLocalIdle,
  run: async () => { await restartKernel(); return runAll(); } });
defineAction({ id: "notebook.validate", label: "Validate", icon: "ok", keywords: "notebook check validate warnings errors clean run",
  contexts: ["notebook"], risk: "low", where: "local", preview: "validate", available: (ctx) => (ctx.nb ? true : "Open a notebook first."),
  run: (ctx) => openValidate(ctx.nb) });
defineAction({ id: "notebook.save", label: "Save the notebook", icon: "save", keywords: "notebook save", contexts: ["notebook"],
  risk: "low", where: "local", available: (ctx) => (ctx.nb ? true : "Open a notebook first."), run: () => saveNotebook() });
defineAction({ id: "notebook.publish", label: "Publish to OneLake", icon: "publish", keywords: "notebook publish fabric workspace onelake",
  contexts: ["notebook"], risk: "moderate", where: "fabric", available: (ctx) => (ctx.nb ? true : "Open a notebook first."),
  run: () => publishNotebook() });
defineAction({ id: "notebook.run_in_fabric", label: "Run in Fabric", icon: "cloud", keywords: "notebook run fabric capacity job",
  contexts: ["notebook"], risk: "moderate", where: "fabric", preview: "fabricRun", spendsCapacity: true,
  ownRiskCheck: "runInFabric (riskGate)", available: nbIdle, run: () => runInFabric() });
defineAction({ id: "notebook.go_failed", label: "Go to the failed cell", icon: "error", contexts: ["notebook"], risk: "low", where: "local",
  available: (ctx) => (nbFailedCell(ctx.nb) ? true : "No cell failed."), run: (ctx) => nbShowCell(nbFailedCell(ctx.nb)) });

function nbFailedCell(nb) {
  return (nb?.cells || []).find((c) => c.status === "error") || null;
}

function nbShowCell(cell) {
  if (!cell) return;
  selectCell(cell);
  document.querySelector(`[data-cell="${cell.id}"]`)?.scrollIntoView({ block: "center" });
}

// Most pressing first (actions.js ranks by reason): a failed cell, Validate's findings, unsaved
// changes, then publishing a notebook whose every cell just ran.
defineRecommendation({ id: "notebook.failed", contexts: ["notebook"], reason: "failure", basis: "verified", action: "notebook.go_failed",
  when: (ctx) => { const c = nbFailedCell(ctx.nb); return c ? { n: ctx.nb.cells.indexOf(c) + 1 } : null; },
  why: (e) => `Cell ${e.n} failed.` });
defineRecommendation({ id: "notebook.findings", contexts: ["notebook"], reason: "validation", basis: "verified", action: "notebook.validate",
  label: (c) => (c.error ? "Review errors" : "Review warnings"),
  when: (ctx) => { const c = ctx.nb?.validate?.record?.counts || {}; return c.error || c.warning ? c : null; },
  why: (c) => `Validate found ${[c.error ? `${c.error} error${c.error > 1 ? "s" : ""}` : "", c.warning ? `${c.warning} warning${c.warning > 1 ? "s" : ""}` : ""]
    .filter(Boolean).join(" and ")}.` });
defineRecommendation({ id: "notebook.unsaved", contexts: ["notebook"], reason: "unfinished", basis: "verified", action: "notebook.save",
  when: (ctx) => (ctx.nb?.dirty ? true : null), why: () => "It has unsaved changes." });
defineRecommendation({ id: "notebook.ran_clean", contexts: ["notebook"], reason: "continue", basis: "verified", action: "notebook.publish",
  when: (ctx) => { const r = ctx.nb?.lastRun; return r?.whole && r.ran === r.of && !nbFailedCell(ctx.nb) ? r : null; },
  why: () => "Every cell ran." });

// The line under the ribbon after a run of several cells: what happened, and the next steps with a
// button each. Closing it hides it until the next run.
function nbNextLine(nb) {
  const r = nb?.lastRun;
  if (!r || nb.lastRunClosed === r) return null;
  // runCells runs on this PC only; a Fabric run reports in the status bar (nbFabricRunStatus).
  const head = r.ran === r.of ? `Ran ${r.of} cells on this PC in ${r.seconds.toFixed(1)} s.` : `Ran ${r.ran} of ${r.of} cells on this PC; the run stopped.`;
  const { items } = recommend({ view: "notebook", nb, busy: state.kernel === "busy" });
  // Only the headline is a live region, so the buttons aren't read out again on every redraw.
  return h("div", { class: "nb-next" },
    h("b", { role: "status" }, head),
    ...items.map((it) => h("span", { class: "nb-next-why", "data-recommendation": it.id }, it.why)),
    h("span", { class: "spacer" }),
    ...items.map((it) => btn(it.label, null, () => runAction(it.state.action.id), "tool outline small")),
    h("button", { class: "tool small nb-next-close", "aria-label": "Close", title: "Close",
      onclick: () => { nb.lastRunClosed = r; document.querySelector(".nb-next")?.remove(); } }, icon("dismiss", 14)));
}

// Draws the line again in place (a run finishing doesn't redraw the whole notebook).
function refreshNbNextLine(nb) {
  if (state.notebook !== nb || state.view !== "notebook") return;
  const line = nbNextLine(nb);
  const old = document.querySelector(".nb-next");
  if (old) { if (line) old.replaceWith(line); else old.remove(); }
  else if (line) document.querySelector(".ribbon")?.after(line);
}
