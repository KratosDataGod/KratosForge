// Format code, as Fabric and Databricks notebooks have it: Format cell (Shift+Alt+F while editing, or
// the cell's menu) and Format notebook (Edit tab). The engine formats (code_format.py): Python in
// Black's style with ruff, %%sql cells with the SQL page's formatter. Nothing runs. Frozen cells, other
// languages and a cell Python can't read are left as they are; Ctrl+Z in a cell undoes its formatting.

async function formatCells(cells, { notebook = false } = {}) {
  const nb = state.notebook;
  if (!notebook && cells.length === 1 && cells[0].meta?.frozen) {
    toast("This cell is frozen. Unfreeze it on the Edit tab to change it.");
    return;
  }
  const todo = cells.filter((c) => c.type === "code" && !c.meta?.frozen);
  if (!todo.length) { toast("There's no code cell to format."); return; }
  const sent = todo.map((c) => c.source);
  const out = await attempt(() => rpc("notebook_format", { sources: sent }), "Could not format the code");
  if (!out || state.notebook !== nb) return;
  let changed = 0;
  const problems = [];
  todo.forEach((c, i) => {
    const r = out[i];
    if (!r) return;
    if (r.error) problems.push({ cell: c, error: r.error });
    // A cell typed in while the engine formatted keeps what was typed.
    else if (r.changed && c.source === sent[i]) { setSource(c, r.source); changed++; }
  });
  if (changed) markDirty();
  if (!notebook) {
    if (problems.length) toast(problems[0].error, true);
    else if (!changed) toast("The cell is already formatted.");
    return;
  }
  const done = changed ? `Formatted ${changed} ${changed === 1 ? "cell" : "cells"}.` : "Every code cell is already formatted.";
  if (!problems.length) { toast(done); return; }
  const first = problems[0];
  const where = `cell ${nb.cells.indexOf(first.cell) + 1}`;
  toast(`${done} ${problems.length === 1 ? "One cell was" : `${problems.length} cells were`} left as written: ${where}. ${first.error}`, true, {
    action: { label: "Show cell", run: () => {
      revealCell(first.cell);  // nbcells.js: open a folded section that hides it
      selectCell(first.cell);
      document.querySelector(`[data-cell="${first.cell.id}"]`)?.scrollIntoView({ block: "center" });
    } },
  });
}

const formatNotebook = () => formatCells(state.notebook.cells, { notebook: true });

registerShortcuts("Notebook", [
  { keys: ["Shift+Alt+F"], label: "While editing a code cell: format it (Python in Black's style, SQL in the SQL page's style)", when: () => state.view === "notebook" },
]);
