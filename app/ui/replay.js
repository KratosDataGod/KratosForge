// Failure replay bundles (Prove stage 5): "Save replay bundle" on a cell that failed on this PC.
// The engine (replay.py) saves a zip to Downloads with the code of the cells up to the failed one,
// the inputs as the cells read them (each table's Delta version, each file's SHA-256), the
// environment and the error with secrets removed. Workspace and lakehouse ids, and a sample of the
// input data, go in only as the person chooses; policy can turn the sample off.

// ---------- pure helpers (app/tests/ui.test.cjs runs these) ----------

// What the engine needs of the notebook's cells: those up to the failed one, with how they ran
// (execution count) and only the failed cell's output.
function replayCells(cells, failed) {
  return cells.slice(0, failed + 1).map((c, i) => ({ type: c.type, source: c.source, meta: c.meta, count: c.count ?? null,
    ...(i === failed ? { outputs: c.outputs || [] } : {}) }));
}

// One line per input, as the dialog lists them.
function replayInputLines(inputs) {
  const tables = (inputs?.tables || []).map((t) => t.created ? `${t.table}: created by the notebook, so a replay starts without it`
    : t.version == null ? `${t.table}: version not known` : `${t.table} at Delta version ${t.version}`);
  const files = (inputs?.files || []).map((f) => f.sha256 ? `${f.path} (SHA-256 ${f.sha256.slice(0, 8)}…)`
    : `${f.path} (changed after it was read, so no SHA-256)`);
  return [...tables, ...files];
}

function replayEnvironmentLine(env) {
  const pkgs = Object.entries(env?.packages || {}).map(([k, v]) => `${k} ${v}`);
  const k = env?.kernel;
  const where = k ? (k.process ? "kernel in its own process" : "kernel inside the engine") : "";
  const lim = k?.limits || {};
  const limits = [lim.cell_seconds ? `${Math.round(lim.cell_seconds / 60)} min cell limit` : "",
    lim.memory_bytes ? `${Math.round(lim.memory_bytes / 2 ** 30)} GB memory limit` : ""];
  return [`Python ${env?.python || "?"}`, ...pkgs, where, ...limits].filter(Boolean).join(", ");
}

// ---------- the button and the dialog ----------

function replayOn() {
  return previewShown() && !!PREVIEW_FEATURES.replay;
}

// Under a cell that failed on this PC (not on Fabric, where the kernel's reads aren't known).
function replayOffer(cell) {
  const nb = state.notebook;
  if (!replayOn() || !nb || cell.type !== "code" || cell.status !== "error" || nbWhere(nb).fabric) return null;
  return h("div", { class: "replay-offer" },
    btn("Save replay bundle", "arrowDownload", () => replayDialog(nb, cell), "tool outline small"), previewTag());
}

function replayRow(title, detail, note, control = null) {
  return h("div", { class: "replay-row" }, control,
    h("div", { class: "replay-what" }, h("b", {}, title), detail ? h("span", {}, detail) : null,
      note ? h("span", { class: "muted small" }, note) : null));
}

async function replayDialog(nb, cell) {
  const failed = nb.cells.indexOf(cell);
  if (failed < 0) return;
  const params = { name: nb.name, cells: replayCells(nb.cells, failed), failed, lakehouse_ref: nb.lakehouse || null };
  const preview = await attempt(() => rpc("replay_bundle_preview", params), "Could not prepare the replay bundle");
  if (!preview) return;
  const b = preview.bundle;
  const ids = h("input", { type: "checkbox", checked: true, "aria-label": "Include workspace and lakehouse IDs" });
  const canSample = preview.samples_allowed && preview.sample_tables.length > 0;
  const sample = h("input", { type: "checkbox", disabled: !canSample, "aria-label": "Include a sample of the input data" });
  const inputs = replayInputLines(b.inputs);
  const err = b.error ? `${b.error.ename}: ${b.error.evalue}` : "No error output was found on this cell.";
  const body = h("div", { class: "replay-dialog" },
    h("p", { class: "muted small" }, "A file someone can open to run this notebook again, up to the failed cell, against the same table versions. No data is included unless you tick it."),
    replayRow(`Code of cells 1 to ${b.failed_cell}`, b.cells.some((c) => c.parameters) ? "and the parameter cell" : "", "Always included"),
    replayRow("Input versions", inputs.length ? h("span", { class: "mono small" }, inputs.join("; ")) : "No lakehouse table or file was read.",
      "Always included. Recorded as the cells read them."),
    replayRow("Environment", replayEnvironmentLine(b.environment), "Always included"),
    replayRow("The error, with secrets removed", h("span", { class: "mono small" }, err.length > 300 ? `${err.slice(0, 297)}...` : err), "Always included"),
    replayRow("Workspace and lakehouse IDs", "", "Untick to share outside your organization. Replay then asks which lakehouse to use.", ids),
    replayRow(`A sample of the input data (up to ${preview.sample_rows.toLocaleString()} rows per table)`, "",
      !preview.samples_allowed ? "Your organization has turned this off."
        : !preview.sample_tables.length ? "No table was read, so there is nothing to sample."
          : "Off by default. The bundle then holds your data: share it only with people allowed to see it.", sample),
    b.limitations.length ? h("div", { class: "replay-limits" }, h("b", {}, "What this bundle can't promise"),
      h("ul", {}, b.limitations.map((t) => h("li", {}, t)))) : null);
  const save = async () => {
    const out = await attempt(() => rpc("replay_bundle_save", { ...params, include_ids: ids.checked, include_sample: sample.checked }),
      "Could not save the replay bundle");
    if (!out) return;
    closeDialog();
    toast(`Saved ${out.path.split(/[\\/]/).pop()} to Downloads${out.samples.length ? `, with a sample of ${out.samples.length} table${out.samples.length === 1 ? "" : "s"}` : ""}.`);
  };
  showDialog("Save a replay bundle", body, [btn("Cancel", null, closeDialog, "tool outline"), btn("Save bundle", null, save, "tool primary")]);
}
