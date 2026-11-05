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

// ---------- opening a bundle and replaying it ----------
// Create > Open replay bundle makes a notebook of the bundle's cells ("<notebook> replay"). Its
// banner replays it: the engine restarts the kernel's own process with the proof runtime bound
// (tables at the bundle's versions, writes to a sandbox), then the cells up to the failed one run
// as usual here, so their output, variables and watches are there to debug.

function replayBase64(buffer) {
  const bytes = new Uint8Array(buffer);
  let text = "";
  for (let i = 0; i < bytes.length; i += 0x8000) text += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(text);
}

async function replayOpenDialog() {
  const rows = await attempt(() => rpc("replay_bundles_list"), "Could not list replay bundles");
  if (!rows) return;
  const open = async (params) => {
    const nb = await attempt(() => rpc("replay_bundle_open", params), "Could not open the replay bundle");
    if (!nb) return;
    closeDialog();
    if (state.notebook?.dirty && !(await saveNotebook())) return;  // a failed save keeps the unsaved cells open (QA-002)
    loadIntoState(nb);
    go("notebook");
  };
  const file = h("input", { type: "file", accept: ".zip", "aria-label": "Choose a replay bundle file" });
  file.addEventListener("change", async () => {
    const f = file.files?.[0];
    if (f) await open({ content: replayBase64(await f.arrayBuffer()) });
  });
  const when = (iso) => dateTimeText(iso);
  showDialog("Open a replay bundle", h("div", { class: "replay-dialog" },
    h("p", { class: "muted small" }, "Opens the bundle's cells as a new notebook. Replaying it runs that code on this PC with your sign-in, so read the cells first when someone else sent you the bundle."),
    rows.length ? h("div", { class: "replay-list" }, rows.map((r) => h("div", { class: "replay-row" },
      h("div", { class: "replay-what" }, h("b", {}, r.notebook || r.file),
        h("span", { class: "muted small" }, [`Failed at cell ${r.failed_cell}`, when(r.saved_at), r.samples ? "with a data sample" : "", r.file].filter(Boolean).join(" · "))),
      btn("Open", null, () => open({ path: r.path }), "tool outline small", { "aria-label": `Open ${r.file}` }))))
      : h("p", { class: "muted small" }, "No replay bundles in your Downloads folder."),
    h("label", { class: "field" }, "Or choose a bundle file", file)),
  [btn("Cancel", null, closeDialog, "tool outline")]);
}

function replayView(nb) {
  if (!nb.replayView) nb.replayView = { check: null, checkedFor: undefined, use: {}, result: null, busy: false, problem: "" };
  return nb.replayView;
}

// The check depends on the attached lakehouse: fetched again when that changes.
function replayLoad(nb, force = false) {
  const v = replayView(nb);
  const lh = nb.lakehouse?.lakehouse_id || null;
  if (!force && v.checkedFor === lh) return;
  v.checkedFor = lh;
  rpc("replay_check", { name: nb.name, lakehouse_ref: nb.lakehouse || null })
    .then((c) => { v.check = c; v.problem = ""; })
    .catch((e) => { v.problem = friendlyError(e).text; })  // a plain sentence, never the exception name (#1442)
    .finally(() => { if (state.notebook === nb) replayPaint(nb); });
}

function replayBanner(nb) {
  if (!nb.replay || !replayOn()) return null;
  replayLoad(nb);
  return h("div", { class: "replay-banner", id: "replay-banner" }, ...replayBody(nb));
}

function replayPaint(nb) {
  document.getElementById("replay-banner")?.replaceChildren(...replayBody(nb));
}

function replayBody(nb) {
  const r = nb.replay;
  const v = replayView(nb);
  const c = v.check;
  const when = dateTimeText(r.saved_at);
  const out = [h("div", { class: "replay-title" }, h("b", {}, `Replay of ${r.notebook}, failed at cell ${r.failed_cell}${when ? ` on ${when}` : ""}`), previewTag())];
  if (v.problem) out.push(h("span", { class: "error-text" }, v.problem));
  if (!c) return [...out, h("span", { class: "muted small" }, "Checking the bundle's tables...")];
  const pinned = c.tables.filter((t) => t.readable).map((t) => `${t.table} v${t.version}`);
  out.push(h("span", {}, pinned.length ? `Tables are read at the versions in the bundle: ${pinned.join(", ")}. ` : "",
    "Writes go to a sandbox in the lakehouse's Files, never its tables, as in a proof. The code runs on this PC with your sign-in."));
  for (const t of c.tables.filter((x) => !x.readable)) {
    const pick = h("select", { "aria-label": `What to read for ${t.table}` },
      h("option", { value: "" }, "Choose..."),
      t.has_sample ? h("option", { value: "sample" }, "Use the bundle's sample") : null,
      t.latest != null ? h("option", { value: "latest" }, `Use the latest version (v${t.latest}), marked as not the same input`) : null);
    pick.value = v.use[t.table] || "";
    pick.addEventListener("change", () => { if (pick.value) v.use[t.table] = pick.value; else delete v.use[t.table]; replayPaint(nb); });
    out.push(h("div", { class: "replay-choice" }, h("span", {}, `${t.table}: ${t.why}.`), pick));
  }
  if (c.environment.length) {
    out.push(h("span", { class: "muted small" }, `Environment differs: ${c.environment.map((d) => `${d.what} ${d["This PC"] ?? "missing"} here, ${d.Bundle ?? "missing"} in the bundle`).join("; ")}.`));
  }
  c.notes.forEach((t) => out.push(h("span", { class: "muted small" }, t)));
  c.blockers.forEach((t) => out.push(h("span", { class: "error-text" }, t)));
  if (v.result) {
    out.push(h("div", { class: `replay-result ${v.result.verdict === "reproduced" ? "ok" : ""}` }, h("b", {}, v.result.text),
      ...(v.result.inputs || []).map((t) => h("span", { class: "muted small" }, t)),
      h("span", { class: "muted small" }, `Writes are in ${v.result.sandbox}.`)));
  }
  const unresolved = c.tables.some((t) => !t.readable && !v.use[t.table]);
  const busy = v.busy || state.kernel === "busy";
  out.push(h("div", { class: "replay-actions" },
    c.live ? btn("End replay", null, () => replayEnd(nb, false), "tool outline", { disabled: busy })
      : btn(`Replay to cell ${r.failed_cell}`, "play", () => replayRun(nb), "tool primary",
        { disabled: busy || !!c.blockers.length || unresolved, title: unresolved ? "Choose what to read for each table above first." : "" }),
    btn("Keep as a normal notebook", null, () => replayEnd(nb, true), "tool outline", { disabled: busy }),
    c.sandboxes?.length ? btn(`Delete replay outputs (${c.sandboxes.length})`, "delete", () => replayDeleteOutputs(nb), "tool outline",
      { disabled: busy || c.live, title: c.live ? "End the replay first." : "" }) : null));
  return out;
}

// What the replays wrote stays in the lakehouse's Files until deleted here.
async function replayDeleteOutputs(nb) {
  const boxes = replayView(nb).check?.sandboxes || [];
  const where = boxes.map((b) => `${b.folder}${b.lakehouse ? ` in ${b.lakehouse}` : ""}`).join(", ");
  if (!(await confirmDialog(`Delete what ${boxes.length === 1 ? "the replay" : `${boxes.length} replays`} wrote?`,
    `Deletes ${where}: the tables the replays wrote in the lakehouse's Files. The lakehouse's own tables aren't touched, and the notebook stays.`,
    "Delete", { danger: true }))) return;
  const out = await attempt(() => rpc("replay_delete_outputs", { name: nb.name }), "Could not delete the replay outputs");
  if (!out) return;
  toast(out.problems.length ? `Deleted ${out.deleted.length}. Couldn't delete: ${out.problems.join("; ")}` : `Deleted ${out.deleted.length} replay output folders.`,
    !!out.problems.length);
  replayLoad(nb, true);
}

async function replayRun(nb) {
  const v = replayView(nb);
  v.busy = true;
  v.result = null;
  replayPaint(nb);
  const started = await attempt(() => rpc("replay_start", { name: nb.name, lakehouse_ref: nb.lakehouse || null, use: v.use }),
    "Could not start the replay");
  if (!started) { v.busy = false; replayPaint(nb); return; }
  nb.cells.forEach((c) => { c.count = null; });
  depsRestarted();  // a fresh kernel process: variables are gone
  const upto = nb.cells.slice(0, nb.replay.failed_cell);
  upto.forEach((c) => { if (c.type === "code") { c.status = null; c.outputs = []; updateResults(c); } });
  let stoppedAt = 0, last = null;
  for (const [i, cell] of upto.entries()) {
    if (cell.type !== "code" || !cell.source.trim() || cell.meta?.frozen) continue;
    stoppedAt = i + 1;
    last = cell;
    if (!(await runCell(cell))) break;
  }
  const result = await attempt(() => rpc("replay_result", { name: nb.name, stopped_at: stoppedAt,
    status: last?.status === "ok" ? "ok" : "error", outputs: last?.outputs || [] }), "Could not compare the replay");
  v.busy = false;
  v.result = result || null;
  replayLoad(nb, true);
}

async function replayEnd(nb, keep) {
  const v = replayView(nb);
  const boxes = v.check?.sandboxes || [];
  // The banner (and its Delete replay outputs) goes away with Keep, so say what stays behind.
  if (keep && boxes.length && !(await confirmDialog("Keep the replay outputs?",
    `${boxes.length === 1 ? "The replay's output folder stays" : `${boxes.length} replay output folders stay`} in the lakehouse's Files. ` +
    "To delete them, cancel and use Delete replay outputs first.", "Keep as a normal notebook"))) return;
  const done = await attempt(() => rpc("replay_end", { name: nb.name, keep }), keep ? "Could not keep the notebook" : "Could not end the replay");
  if (!done) return;
  if (v.check?.live) {
    nb.cells.forEach((c) => { c.count = null; });
    depsRestarted();
  }
  if (keep) {
    nb.replay = null;
    renderNotebook();
    toast(`${nb.name} is now a normal notebook. Its cells read and write the lakehouse's tables when they run.`);
    return;
  }
  v.result = null;
  replayLoad(nb, true);
}

// The kernel restarted (Restart, Stop, a crash): a running replay ended with it.
function replayRestarted() {
  const nb = state.notebook;
  if (nb?.replay && nb.replayView) replayLoad(nb, true);
}
